"""
文件路径管理和 JSON I/O 工具
"""
import os
import json
import tempfile
import threading
import weakref
from typing import Any


# 锁只在使用期间保留强引用；历史 Mock 工单删除或长期不用后可自动回收。
_job_locks = weakref.WeakValueDictionary()
_job_locks_guard = threading.Lock()


def get_job_lock(job_id: str) -> threading.RLock:
    """按 job_id 延迟创建进程内可重入锁。"""
    key = str(job_id)
    with _job_locks_guard:
        lock = _job_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _job_locks[key] = lock
        return lock


def get_agents_dir():
    """获取 agents 目录"""
    return os.path.dirname(os.path.abspath(__file__))


def get_jobs_dir():
    """获取作业根目录"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "jobs"
    )


def get_job_dir(job_id: str) -> str:
    """获取指定作业的目录"""
    return os.path.join(get_jobs_dir(), job_id)


def ensure_job_dir(job_id: str) -> str:
    """确保作业目录存在"""
    job_dir = get_job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


def get_stage_result_path(job_id: str, stage: str) -> str:
    """获取指定阶段结果文件路径"""
    return os.path.join(get_job_dir(job_id), f"{stage}_result.json")


def read_json_file(filepath: str) -> Any:
    """读取 JSON 文件"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_json_file(filepath: str, data: Any) -> None:
    """在同目录写临时文件后原子替换，读取方不会看到半截 JSON。"""
    directory = os.path.dirname(filepath)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(filepath)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        try:
            mode = os.stat(filepath).st_mode & 0o777
        except FileNotFoundError:
            mode = 0o644
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_path, filepath)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def get_workflow_status_path(job_id: str) -> str:
    """获取工作流状态文件路径"""
    return os.path.join(get_job_dir(job_id), "workflow_status.json")
