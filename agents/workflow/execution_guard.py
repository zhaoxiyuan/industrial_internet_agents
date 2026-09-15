"""单进程内的工单执行占用管理。"""
import threading
from typing import Any, Dict, Optional

from .file_utils import get_job_lock


_active_executions: Dict[str, Dict[str, Any]] = {}
_registry_guard = threading.Lock()


def claim_job_execution(job_id: str, owner: str) -> Optional[object]:
    """占用工单执行权；已被占用时返回 None。"""
    key = str(job_id)
    with get_job_lock(key):
        with _registry_guard:
            if key in _active_executions:
                return None
            token = object()
            _active_executions[key] = {"token": token, "owner": owner}
            return token


def release_job_execution(job_id: str, token: object) -> bool:
    """仅允许持有当前 token 的线程释放，避免旧线程误释放新任务。"""
    key = str(job_id)
    with get_job_lock(key):
        with _registry_guard:
            current = _active_executions.get(key)
            if not current or current.get("token") is not token:
                return False
            _active_executions.pop(key, None)
            return True


def get_job_execution_owner(job_id: str) -> Optional[str]:
    with get_job_lock(job_id):
        with _registry_guard:
            current = _active_executions.get(str(job_id))
            return current.get("owner") if current else None


def clear_execution_registry() -> None:
    """测试辅助：清空当前进程的执行占用。"""
    with _registry_guard:
        _active_executions.clear()


__all__ = [
    "claim_job_execution",
    "release_job_execution",
    "get_job_execution_owner",
    "clear_execution_registry",
]
