"""
执行状态管理
记录每个阶段的执行次数、状态和历史，支持失败恢复
"""
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .file_utils import get_job_dir, read_json_file, write_json_file


# 错误类型定义
ERROR_TYPES = {
    "temporary": [
        "timeout",
        "rate_limit",
        "rate limit",
        "network_error",
        "network error",
        "5xx_error",
        "connection_refused",
        "LLM rate limit",
        "overloaded",
        "temporarily unavailable",
        "temporarily",
        "retry",
    ],
    "business": [
        "invalid_parameter",
        "invalid parameter",
        "data_format_error",
        "data format error",
        "permission_denied",
        "permission denied",
        "not_found",
        "not found",
        "validation_error",
        "validation error",
    ],
    "config": [
        "model_not_found",
        "api_key_invalid",
        "missing_config",
        "Unable to infer model",
        "CONFIG_ENV_MISSING",
    ],
}

# 阶段配置
STAGE_CONFIG = {
    "P1": {"critical": True, "max_attempts": 3, "retry_on_temporary": True},
    "P2": {"critical": True, "max_attempts": 3, "retry_on_temporary": True},
    "P3": {"critical": True, "max_attempts": 3, "retry_on_temporary": True},
    "P4": {"critical": True, "max_attempts": 2, "retry_on_temporary": True},
    "P5": {"critical": True, "max_attempts": 2, "retry_on_temporary": True},
    "P6": {"critical": True, "max_attempts": 2, "retry_on_temporary": True},
    "P7": {"critical": True, "max_attempts": 2, "retry_on_temporary": True},
    "P8": {"critical": True, "max_attempts": 2, "retry_on_temporary": True},
    "P9": {"critical": False, "max_attempts": 2, "retry_on_temporary": True},
    "P10": {"critical": False, "max_attempts": 2, "retry_on_temporary": True},
}

ALL_STAGES = list(STAGE_CONFIG.keys())


def classify_error(error_message: str) -> str:
    """根据错误信息分类错误类型"""
    if not error_message:
        return "unknown"

    error_lower = error_message.lower()

    for error_type, keywords in ERROR_TYPES.items():
        for keyword in keywords:
            if keyword.lower() in error_lower:
                return error_type

    return "unknown"


def get_execution_status_path(job_id: str) -> str:
    """获取执行状态文件路径"""
    return get_job_dir(job_id) + "/execution_status.json"


def init_execution_status(job_id: str) -> Dict[str, Any]:
    """初始化执行状态"""
    status = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_stage": None,
        "stages": {}
    }

    for stage in ALL_STAGES:
        status["stages"][stage] = {
            "status": "pending",
            "attempts": 0,
            "last_attempt_at": None,
            "completed_at": None,
            "last_error": None,
            "last_error_type": None,
            "history": []
        }

    status_file = get_execution_status_path(job_id)
    write_json_file(status_file, status)
    return status


def get_execution_status(job_id: str) -> Dict[str, Any]:
    """获取执行状态"""
    status_file = get_execution_status_path(job_id)
    status = read_json_file(status_file)

    if not status:
        status = init_execution_status(job_id)

    return status


def update_stage_status(
    job_id: str,
    stage: str,
    status: str,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
    new_attempt: bool = True,
) -> Dict[str, Any]:
    """更新阶段状态

    Args:
        job_id: 作业ID
        stage: 阶段名称 (P1-P10)
        status: 状态 (running/waiting/completed/failed)
        error: 错误信息（失败时）
        duration_ms: 执行耗时（毫秒）
        new_attempt: running 时是否开始一次新的阶段执行；HITL 继续执行时为 False

    Returns:
        更新后的执行状态
    """
    execution_status = get_execution_status(job_id)
    stage_info = execution_status["stages"].get(stage)

    if not stage_info:
        return execution_status

    now = datetime.now(timezone.utc).isoformat()
    # 更新阶段状态
    stage_info["status"] = status
    if status == "running":
        # 每次真正调用 executor 前即持久化次数；进程中途退出也不会丢失本次尝试。
        if new_attempt or stage_info["attempts"] == 0:
            stage_info["attempts"] += 1
        stage_info["last_attempt_at"] = now
        # 一旦真正重新进入 executor，就不再属于上一次服务异常中断。
        stage_info.pop("interrupted", None)
        stage_info.pop("interrupted_at", None)

    if status == "completed":
        stage_info["completed_at"] = now
        stage_info["last_error"] = None
        stage_info["last_error_type"] = None
    elif status == "failed":
        stage_info["last_error"] = error
        stage_info["last_error_type"] = classify_error(error) if error else None

    # completed / failed / waiting 都是一次 executor 调用的结果。
    if status in ["completed", "failed", "waiting"]:
        history_entry = {
            "attempt": stage_info["attempts"],
            "status": status,
            "started_at": stage_info["last_attempt_at"],
            "completed_at": now,
            "duration_ms": duration_ms,
            "error": error,
            "error_type": classify_error(error) if error else None
        }
        # 人工确认会把同一次尝试由 waiting 推进到 completed，不应重复计为两次。
        history = stage_info["history"]
        if (
            status in {"waiting", "completed", "failed"}
            and history
            and history[-1].get("attempt") == stage_info["attempts"]
            and history[-1].get("status") == "waiting"
        ):
            history[-1].update(history_entry)
        else:
            history.append(history_entry)

    # 更新当前阶段
    if status == "running":
        execution_status["current_stage"] = stage
    elif status in ["completed", "failed"]:
        if stage == "P10" and status == "completed":
            execution_status["current_stage"] = "completed"

    execution_status["updated_at"] = now

    status_file = get_execution_status_path(job_id)
    write_json_file(status_file, execution_status)

    return execution_status


def mark_stage_interrupted(job_id: str, stage: str, error: str) -> Dict[str, Any]:
    """把服务退出时遗留的 running 阶段转换为可人工恢复的失败状态。"""
    execution_status = get_execution_status(job_id)
    stage_info = execution_status["stages"].get(stage)
    if not stage_info:
        return execution_status

    now = datetime.now(timezone.utc).isoformat()
    stage_info["status"] = "failed"
    stage_info["last_error"] = error
    stage_info["last_error_type"] = "interrupted"
    stage_info["interrupted"] = True
    stage_info["interrupted_at"] = now
    history_entry = {
        "attempt": stage_info.get("attempts", 0),
        "status": "failed",
        "started_at": stage_info.get("last_attempt_at"),
        "completed_at": now,
        "duration_ms": None,
        "error": error,
        "error_type": "interrupted",
    }
    history = stage_info.setdefault("history", [])
    if (
        history
        and history[-1].get("attempt") == history_entry["attempt"]
        and history[-1].get("status") in {"running", "waiting"}
    ):
        history[-1].update(history_entry)
    else:
        history.append(history_entry)

    execution_status["current_stage"] = stage
    execution_status["updated_at"] = now
    write_json_file(get_execution_status_path(job_id), execution_status)
    return execution_status


def can_retry_stage(job_id: str, stage: str) -> bool:
    """检查失败阶段是否还允许人工重试。

    错误类型只决定是否自动重试；配置或业务错误修复后仍应允许人工恢复。
    """
    execution_status = get_execution_status(job_id)
    stage_info = execution_status["stages"].get(stage)
    config = STAGE_CONFIG.get(stage, {})

    if not stage_info:
        return False

    # 服务异常退出不应把工单永久锁死；即使此前已到次数上限，也放行一次恢复。
    if stage_info.get("interrupted"):
        return stage_info.get("status") == "failed"

    # 检查是否已达到最大重试次数
    max_attempts = config.get("max_attempts", 3)
    if stage_info["attempts"] >= max_attempts:
        return False

    return stage_info.get("status") == "failed"


def get_retry_delay(attempt: int) -> int:
    """获取重试延迟（毫秒），指数退避"""
    return min(1000 * (2 ** (attempt - 1)), 4000)  # 1s, 2s, 4s


def get_failed_stage(job_id: str) -> Optional[str]:
    """获取第一个失败的阶段"""
    execution_status = get_execution_status(job_id)

    for stage in ALL_STAGES:
        stage_info = execution_status["stages"].get(stage, {})
        if stage_info.get("status") == "failed":
            return stage

    return None


def get_stage_execution_info(job_id: str, stage: str) -> Dict[str, Any]:
    """获取阶段执行信息"""
    execution_status = get_execution_status(job_id)
    return execution_status["stages"].get(stage, {})


def finalize_execution_status(job_id: str) -> Dict[str, Any]:
    """标记阶段序列已经走到末尾，同时保留各阶段的失败状态。"""
    execution_status = get_execution_status(job_id)
    execution_status["current_stage"] = "completed"
    execution_status["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_file(get_execution_status_path(job_id), execution_status)
    return execution_status


def is_stage_critical(stage: str) -> bool:
    """检查阶段是否是关键阶段（失败需要中断）"""
    config = STAGE_CONFIG.get(stage, {})
    return config.get("critical", True)


# 导出
__all__ = [
    "STAGE_CONFIG",
    "ALL_STAGES",
    "ERROR_TYPES",
    "classify_error",
    "init_execution_status",
    "get_execution_status",
    "update_stage_status",
    "mark_stage_interrupted",
    "can_retry_stage",
    "get_retry_delay",
    "get_failed_stage",
    "get_stage_execution_info",
    "finalize_execution_status",
    "is_stage_critical",
]
