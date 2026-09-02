"""
Workflow 状态管理
工作流状态文件的读写和更新
"""
from datetime import datetime, timezone
from typing import Dict, Any

from .file_utils import get_workflow_status_path, read_json_file, write_json_file, ensure_job_dir
from agents.utils.response_utils import SCHEMA_VERSION

# 所有阶段列表
ALL_STAGES = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]


def init_workflow_status(job_id: str) -> dict:
    """初始化工作流状态文件"""
    ensure_job_dir(job_id)
    status = {
        "job_id": job_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "main_agent": {
            "status": "pending",  # pending, running, waiting, completed
            "current_stage": "",
            "pending_confirmations": []
        },
        "agents": {
            stage: {"status": "pending", "updated_at": None}
            for stage in ALL_STAGES
        }
    }
    status_file = get_workflow_status_path(job_id)
    write_json_file(status_file, status)
    return status


def update_workflow_status(job_id: str, updates: dict) -> dict:
    """更新工作流状态文件

    Args:
        job_id: 作业ID
        updates: 更新内容，支持:
            - main_agent: 主Agent状态更新
            - agents.{P1-P10}: 各Agent状态更新
            - {agent}_status: 快捷方式，直接更新某个Agent状态

    Returns:
        更新后的完整状态
    """
    status_file = get_workflow_status_path(job_id)
    status = read_json_file(status_file)

    if not status:
        status = init_workflow_status(job_id)

    now = datetime.now(timezone.utc).isoformat()
    status["updated_at"] = now

    # 处理快捷方式（如 P1_status → agents.P1.status）
    for stage in ALL_STAGES:
        status_key = f"{stage}_status"
        if status_key in updates:
            updates.setdefault("agents", {})
            updates["agents"][stage] = {
                "status": updates.pop(status_key),
                "updated_at": now
            }

    # 更新主Agent状态
    if "main_agent" in updates:
        for k, v in updates["main_agent"].items():
            if k == "pending_confirmations":
                status["main_agent"]["pending_confirmations"] = v
            else:
                status["main_agent"][k] = v

    # 更新各Agent状态
    if "agents" in updates:
        for agent_id, agent_update in updates["agents"].items():
            if agent_id in status["agents"]:
                if isinstance(agent_update, dict):
                    for k, v in agent_update.items():
                        status["agents"][agent_id][k] = v
                else:
                    status["agents"][agent_id]["status"] = agent_update
                status["agents"][agent_id]["updated_at"] = now

    write_json_file(status_file, status)
    return status


def get_workflow_status(job_id: str) -> dict:
    """获取工作流状态"""
    status_file = get_workflow_status_path(job_id)
    return read_json_file(status_file)
