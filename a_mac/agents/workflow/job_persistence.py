"""
Job 持久化操作
作业申请、日志、确认记录的保存和读取
"""
import os
from datetime import datetime, timezone
from typing import Dict, Any

from .file_utils import (
    get_job_dir, ensure_job_dir, get_stage_result_path,
    read_json_file, write_json_file
)


def save_job_application(job_id: str, application: dict) -> str:
    """保存作业申请"""
    job_dir = ensure_job_dir(job_id)
    filepath = os.path.join(job_dir, "application.json")
    write_json_file(filepath, {
        "job_id": job_id,
        "application": application,
        "saved_at": datetime.now(timezone.utc).isoformat()
    })
    return filepath


def add_job_log(job_id: str, log_entry: dict) -> str:
    """追加作业执行日志"""
    job_dir = ensure_job_dir(job_id)
    log_file = os.path.join(job_dir, "logs.json")

    logs = []
    if os.path.exists(log_file):
        try:
            logs = read_json_file(log_file)
            if isinstance(logs, dict):
                logs = [logs]
        except:
            logs = []

    log_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    logs.append(log_entry)

    write_json_file(log_file, logs)
    return log_file


def save_confirmation(job_id: str, stage: str, decision: str, notes: str = "") -> str:
    """保存确认记录"""
    job_dir = ensure_job_dir(job_id)
    confirm_file = os.path.join(job_dir, "confirmations.json")

    confirmations = []
    if os.path.exists(confirm_file):
        try:
            confirmations = read_json_file(confirm_file)
            if isinstance(confirmations, dict):
                confirmations = [confirmations]
        except:
            confirmations = []

    record = {
        "stage": stage,
        "decision": decision,
        "notes": notes,
        "confirmed_at": datetime.now(timezone.utc).isoformat()
    }
    confirmations.append(record)

    write_json_file(confirm_file, confirmations)
    return confirm_file


def get_job_status(job_id: str) -> dict:
    """获取作业状态"""
    job_dir = get_job_dir(job_id)
    if not os.path.exists(job_dir):
        return {"error": "Job not found"}

    status = {
        "job_id": job_id,
        "stages": {},
        "current_stage": None,
        "pending_confirmations": []
    }

    for stage in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"]:
        result_file = os.path.join(job_dir, f"{stage}_result.json")
        if os.path.exists(result_file):
            result_data = read_json_file(result_file)
            status["stages"][stage] = {
                "completed": result_data.get("completed", False),
                "has_pending": bool(result_data.get("pending_confirmation")),
            }
            if result_data.get("pending_confirmation"):
                status["pending_confirmations"].append({
                    "stage": stage.upper(),
                    "pending": result_data["pending_confirmation"]
                })

    for stage in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"]:
        result_file = os.path.join(job_dir, f"{stage}_result.json")
        if not os.path.exists(result_file):
            status["current_stage"] = stage.upper()
            break
    else:
        status["current_stage"] = "completed"

    return status
