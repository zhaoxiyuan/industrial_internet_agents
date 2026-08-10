"""
文件路径管理和 JSON I/O 工具
"""
import os
import json
from typing import Optional


def get_agents_dir():
    """获取 agents 目录"""
    return os.path.dirname(os.path.abspath(__file__))


def get_jobs_dir():
    """获取作业根目录"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
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


def read_json_file(filepath: str) -> dict:
    """读取 JSON 文件"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_json_file(filepath: str, data: dict) -> None:
    """写入 JSON 文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_workflow_status_path(job_id: str) -> str:
    """获取工作流状态文件路径"""
    return os.path.join(get_job_dir(job_id), "workflow_status.json")
