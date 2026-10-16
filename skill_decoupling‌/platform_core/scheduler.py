"""单环境轻量调度：领取、依赖、并发上限、等待唤醒与关闭。

扫描线程不执行网络请求；工作线程只运行可执行片段。父任务 waiting 后释放
线程，因此子任务能够获得执行机会。本版不包含 Redis 或分布式节点管理。
"""
import threading
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .common import Problem, now
from .task_store import TERMINAL


class Scheduler:
    """有限执行池搭配持久状态，所有领取使用数据库版本条件更新。"""

    def __init__(self, service, workers=4):
        """等待 HTTP 入口配置完成才启动，避免 CLI 调用空地址。"""
        self.service = service
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent-runtime")
        self.active = {}
        self.cleanup_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='lease-cleanup')
        self.stopping = threading.Event()
        self.thread = None
        self.last_error = None
        self.cleanup_check=0

    def start(self):
        """一个平台进程只运行一个调度线程，不重复启动。"""
        if self.thread:
            return
        self.thread = threading.Thread(target=self.loop, name="task-scheduler", daemon=True)
        self.thread.start()

    def loop(self):
        """低频唤醒检查依赖；SQLite 只保存状态，不依赖模型轮询。"""
        while not self.stopping.wait(.1):
            try:
                self.tick()
                self.last_error = None
            except (Problem, OSError, sqlite3.Error):
                # 暂时写锁或文件异常等下一轮重新读取，不隐式重复外部动作。
                self.last_error = {"code": "SCHEDULER_RETRY", "message": "调度暂时失败，正在重新读取任务状态"}
                continue
            except Exception:
                # 调度异常可见且不中断 HTTP；不公开内部堆栈或私有任务上下文。
                self.last_error = {"code": "SCHEDULER_ERROR", "message": "调度发生内部错误，请核对任务与服务日志"}
                continue

    def tick(self):
        """先回收工作片段，再选取满足依赖和并发限制的任务。"""
        repo = self.service.tasks.repo
        for id, future in list(self.active.items()):
            if future.done():
                if future.exception():
                    row = repo.get(id)
                    if row["status"] not in TERMINAL:
                        repo.update(row, status="failed", phase="执行器异常", finished=now(),
                                    error={"code": "RUNTIME_ERROR", "message": "执行器发生内部错误"})
                if repo.get(id)['status'] in TERMINAL:
                    self.cleanup_pool.submit(self.service.skill_tools.finish_run,id,'execution_ended')
                # Terminal status alone does not release execution ownership.
                repo.update(repo.get(id), executor_active=False)
                del self.active[id]
        # Mark deadlines independently of blocked model/network calls. Fencing happens
        # first; cleanup may then run without allowing the old execution to commit.
        for task_id in list(self.active):
            row=repo.get(task_id)
            if row['status'] not in TERMINAL and row.get('deadline') and datetime.fromisoformat(now())>=datetime.fromisoformat(row['deadline']):
                from .workflow import fence_run
                fence_run(self.service,row,'task_timeout')
                repo.update(row,status='failed',phase='执行超时，领取回收中',finished=now(),error={'code':'TASK_TIMEOUT','message':'任务超过执行期限'})
                self.cleanup_pool.submit(self.service.skill_tools.finish_run,task_id,'task_timeout')
        if time.monotonic()-self.cleanup_check>5:
            self.cleanup_check=time.monotonic()
            runs={c['run_id'] for c in self.service.store.list('skill_claims') if c['status']=='processing'}
            for run_id in runs:
                try:ended=repo.get(run_id)
                except Problem:continue
                if ended['status'] in TERMINAL:
                    from .workflow import fence_run
                    if ended['status']!='succeeded':fence_run(self.service,ended,'terminal_recovery')
                    self.cleanup_pool.submit(self.service.skill_tools.finish_run,run_id,'terminal_recovery')
        self.service.schedules.tick()
        rows = repo.list()
        by_id = {r["id"]: r for r in rows}
        for row in rows:
            if row["id"] in self.active or row["status"] not in ("queued", "waiting"):
                continue
            if row.get("deadline") and datetime.fromisoformat(now()) >= datetime.fromisoformat(row["deadline"]):
                self.service.tasks.cancel(row["id"])
                current = repo.get(row["id"])
                repo.update(current, status="failed", phase="任务期限已到", finished=now(),
                            error={"code": "TASK_TIMEOUT", "message": "等待或执行超过任务期限"})
                continue
            if row["status"] == "waiting":
                waits = repo.checkpoint(row["id"])["wait_ids"]
                if not all(by_id.get(id, {}).get("status") in TERMINAL for id in waits):
                    continue
            else:
                deps = [by_id[id] for id in row["depends_on"]]
                if any(d["status"] in TERMINAL and d["status"] != "succeeded" for d in deps):
                    repo.update(row, status="failed", phase="前置任务未成功", finished=now(),
                                error={"code": "DEPENDENCY_FAILED", "message": "前置任务失败，未执行本任务"})
                    continue
                if not all(d["status"] == "succeeded" for d in deps):
                    continue
            if len(self.active) >= self.workers:
                break
            agent = self.service.store.get("agents", row["executor_agent_id"])
            running = sum(repo.get(id)["executor_agent_id"] == agent["id"] for id in self.active)
            if running >= agent.get("concurrency", 2):
                continue
            try:
                if row.get("caller_agent_id"):
                    self.service.tasks.authorize(row["caller_agent_id"], agent["id"], "submit")
                self.service.schedules.execution_guard(row)
                updates = {"status": "running", "phase": "执行中", 'executor_active':True}
                if not row.get("started"):
                    updates.update(started=now(), attempt=1, deadline=(datetime.now(timezone.utc) + timedelta(seconds=row["timeout"])).isoformat())
                repo.update(row, **updates)
            except Problem as error:
                if error.code != "TASK_CONFLICT":
                    repo.update(repo.get(row["id"]), status="failed", phase="执行授权已失效", error=error.as_dict(), finished=now())
                continue
            try:
                self.active[row["id"]] = self.pool.submit(self.service.runtime.advance, row["id"])
            except RuntimeError:
                repo.update(repo.get(row['id']), status='failed', executor_active=False,
                            phase='执行池未接受任务', finished=now(),
                            error={'code':'EXECUTOR_UNAVAILABLE','message':'执行池未接受任务，本次执行已失败'})

    def close(self):
        """停止领取并等待有界在途调用结束；剩余任务下次启动标记 interrupted。"""
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.cleanup_pool.shutdown(wait=True)
        self.pool.shutdown(wait=True, cancel_futures=True)
