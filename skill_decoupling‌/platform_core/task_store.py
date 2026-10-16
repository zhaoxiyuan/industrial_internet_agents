"""协作任务专用持久化：任务事实、私有检查点和状态事件分别存储。

任务列建立索引用于领取与统计，扩展业务字段存放 JSON；不把消息历史或
密钥放进公开任务大表。数据库事务仅用于状态更新，不包含网络或模型调用。
"""
import json
import time
from contextlib import nullcontext

from .common import Problem, dumps, now

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class TaskStore:
    """提供带版本的原子状态转换及检查点，不负责 Agent 授权。"""

    def __init__(self, store):
        """建立新表与索引，保留原有注册、飞书和会话数据。"""
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS tasks_agent_status ON tasks(agent_id,status);
                CREATE TABLE IF NOT EXISTS task_checkpoints(task_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,payload TEXT NOT NULL);
            """)
        # 不假定崩溃前的 HTTP 动作未发生；旧执行明确中断，由管理员核对。
        for hold in store.list('execution_holds'):
            if hold.get('state') == 'executing':
                # A lost Docker runner receipt cannot prove process termination,
                # even after its intended deadline (kill/wait can fail).
                store.put('execution_holds', {**hold, 'state':'uncertain'}, hold['id'])
        for task in self.list():
            recovery = {}
            if task.get('executor_active') or task['status'] == 'running':
                # Old Python workers died with the platform. A Docker runner may
                # outlive it: its bounded execution is <=120s plus exit grace.
                recovery = {'executor_active':False, 'executor_recovery_until':time.time()+130}
            if task["status"] not in TERMINAL:
                self.update(task, status="interrupted", phase="平台重启，需核对外部动作",
                            error={"code": "PROCESS_RESTARTED", "message": "平台重启后未自动重试旧任务"}, finished=now(), **recovery)
            elif recovery:
                self.update(task, **recovery)

    def create(self, value, checkpoint, db=None):
        """任务与初始上下文在一个事务内建立，保证执行器不会读到半成品。"""
        value = {**value, "version": 1, "created": now(), "updated": now()}
        with (nullcontext(db) if db is not None else self.store.db()) as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", (value["id"], value["executor_agent_id"],
                        value["status"], 1, dumps(value)))
            db.execute("INSERT INTO task_checkpoints VALUES(?,?)", (value["id"], dumps(checkpoint)))
            self._event(db, value)
        return value

    def get(self, id):
        """读取公开任务记录，不附加私有模型配置和检查点。"""
        with self.store.db() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (id,)).fetchone()
        if not row:
            raise Problem("TASK_NOT_FOUND", "任务不存在", 404)
        return json.loads(row[0])

    def list(self):
        """本地版本返回按插入顺序的任务；前端和 API 层对结果设置上限。"""
        with self.store.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM tasks ORDER BY rowid")]

    def checkpoint(self, id):
        """Runtime 专用上下文读取，HTTP 管理接口不暴露此返回值。"""
        with self.store.db() as db:
            row = db.execute("SELECT payload FROM task_checkpoints WHERE task_id=?", (id,)).fetchone()
        if not row:
            raise Problem("CHECKPOINT_MISSING", "任务执行上下文缺失", 409)
        return json.loads(row[0])

    def update(self, previous, checkpoint=None, **changes):
        """比较版本后更新状态和上下文，取消与完成并发时不覆盖更新后的记录。"""
        if checkpoint is not None and 'context_tokens' in checkpoint:
            changes.update(context_tokens=checkpoint['context_tokens'], context_estimated=checkpoint.get('context_estimated',True))
        value = {**previous, **changes, "version": previous["version"] + 1, "updated": now()}
        with self.store.db() as db:
            count = db.execute("UPDATE tasks SET status=?,version=?,payload=? WHERE id=? AND version=?",
                               (value["status"], value["version"], dumps(value), value["id"], previous["version"])).rowcount
            if not count:
                raise Problem("TASK_CONFLICT", "任务已被其他执行更新，请重新读取", 409)
            if checkpoint is not None:
                db.execute("UPDATE task_checkpoints SET payload=? WHERE task_id=?", (dumps(checkpoint), value["id"]))
            self._event(db, value)
        if value['status'] in TERMINAL:
            # Also covers queued cancellation, scheduler failures and restart interruption.
            def redact(item):
                if isinstance(item,dict): return {k:('[redacted]' if any(s in k.lower() for s in ('token','secret','api_key','authorization')) else redact(v)) for k,v in item.items()}
                if isinstance(item,list): return [redact(v) for v in item]
                return item
            directory=self.store.data/'artifacts'/value['id'];directory.mkdir(parents=True,exist_ok=True)
            target=directory/'execution.json'
            if not target.exists():
                temporary=target.with_suffix('.tmp')
                temporary.write_text(dumps({'task':redact(value),'state_events':self.events(value['id'])}),encoding='utf-8')
                temporary.replace(target)
        return value

    def _event(self, db, value):
        """记录简短状态事件；不复制业务输入与会话内容以避免无限膨胀。"""
        db.execute("INSERT INTO task_events(task_id,payload) VALUES(?,?)", (value["id"], dumps({
            "status": value["status"], "phase": value.get("phase", ""), "version": value["version"], "time": now()})))

    def events(self, id):
        """前端详情显示最近一百次状态变化，保持返回体有界。"""
        with self.store.db() as db:
            rows = db.execute("SELECT payload FROM task_events WHERE task_id=? ORDER BY seq DESC LIMIT 100", (id,))
            return [json.loads(r[0]) for r in rows]
