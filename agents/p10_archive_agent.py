"""P10：归档与复盘阶段的工作流入口。"""

import json
from datetime import datetime, timezone


def execute_stage(job_id: str) -> dict:
    """归档 P9 结果并生成基础复盘摘要。"""
    from .workflow import get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger, add_job_log

    log = get_stage_logger("P10")
    log.log_enter(job_id)
    result = {
        "job_id": job_id,
        "stage": "P10",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }
    try:
        p9_result = read_json_file(get_stage_result_path(job_id, "p9"))
        task_id = p9_result.get("task_id") or job_id
        result.update({
            "task_id": task_id,
            "archive_result": {
                "task_id": task_id,
                "archive_id": f"ARC-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "contents": ["作业票证", "视频证据片段", "风险事件记录", "处置全记录", "作业报告"],
                "status": "archived",
            },
            "completed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pending_confirmation": {
                "type": "report_confirm",
                "message": "请确认归档报告",
            },
        })
    except Exception as exc:
        log.log_error(job_id, exc)
        result["error"] = str(exc)

    write_json_file(get_stage_result_path(job_id, "p10"), result)
    add_job_log(job_id, {
        "action": "execute_p10",
        "result": "success" if result["completed"] else "failed",
    })
    log.log_exit(job_id, result)
    return result
