"""后台操作队列。

HTTP 仅受理操作并返回 operationId；耗时的 MCP 连接探测和 Agent
对话在工作线程执行。持久记录与线程 Future 分开，页面可随时查询进度。
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from .common import Problem, now


class Operations:
    """管理后台任务和资源锁，防止同一实例被同时配置、探测或重复发送。"""

    def __init__(self, store):
        """恢复历史状态；进程重启后旧任务无法继续，因此明确记为 interrupted。"""
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="platform-operation")
        self.lock = threading.RLock()
        for row in store.list("operations"):
            if row["status"] in ("queued", "running"):
                store.put("operations", {**row, "status": "interrupted", "error": {
                    "code": "PROCESS_RESTARTED", "message": "平台重启，旧操作结果需重新核对"}}, row["id"])

    def submit(self, kind, resource, work):
        """同一资源重复操作返回冲突，成功受理后立即返回而不等待 work 完成。"""
        with self.lock:
            for row in self.store.list("operations"):
                if row["resource"] == resource and row["status"] in ("queued", "running"):
                    raise Problem("OPERATION_BUSY", "该对象已有执行中的操作", 409, {"operation_id": row["id"]})
            row = self.store.put("operations", {"kind": kind, "resource": resource, "status": "queued", "phase": "等待执行"})
            self.executor.submit(self._run, row, work)
            return row

    def _run(self, row, work):
        """只保存可公开的业务错误；意外异常不向网页暴露凭据或堆栈。"""
        self.store.put("operations", {**row, "status": "running", "phase": "执行中"}, row["id"])
        try:
            result = work()
            final = {"status": "succeeded", "phase": "完成", "result": result}
        except Problem as e:
            final = {"status": "failed", "phase": "失败", "error": e.as_dict()}
        except Exception:
            final = {"status": "failed", "phase": "失败", "error": {"code": "INTERNAL_ERROR", "message": "后台操作发生内部错误"}}
        self.store.put("operations", {**row, **final, "finished": now()}, row["id"])

    def close(self):
        """退出时结束队列；在执行任务受网络超时限制后等待其正常释放资源。"""
        self.executor.shutdown(wait=True, cancel_futures=True)
