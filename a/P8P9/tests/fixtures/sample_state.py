# P8P9/tests/fixtures/sample_state.py — 7 个 job_status 的 mock state
#
# 给每个 job_status 一份「真实可写入 closure_state.json」的结构。
# 调用者直接传 job_id + state dict 给 ClosureService._atomic_write。

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict

from .sample_events import event_by_level


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_state(
    job_id: str,
    job_status: str,
    *,
    events: list = None,
    display_risk_level: int = None,
    accepted_by: Dict[str, Any] = None,
    materials: Dict[str, Any] = None,
    review: Dict[str, Any] = None,
    risk_changes: list = None,
    archived_to_lt: bool = False,
    closed_at: str = None,
    closed_by: str = None,
) -> Dict[str, Any]:
    """构造一个指定 job_status 的 state dict（不写盘）。"""
    events = events if events is not None else [event_by_level(1)]
    if display_risk_level is None:
        display_risk_level = max(int(e.get("risk_level", 0)) for e in events)
    now = _now()
    return {
        "schema_version": "1.1",
        "job_id": job_id,
        "version": 1,
        "job_status": job_status,
        "display_risk_level": display_risk_level,
        "card_binding": None,
        "accepted_by": accepted_by,
        "materials": materials or {"submissions": []},
        "review": review or {},
        "risk_changes": risk_changes or [],
        "p7_reassessments": [],
        "relinquish_count": {},
        "job_status_history": [
            {"to": job_status, "by": "test", "at": now, "from": None},
        ],
        "job_closure_status": "pending",
        "events": events,
        "confirmations": [],
        "report": {},
        "closure_blockers": [],
        "created_at": now,
        "updated_at": now,
        "archived_to_lt": archived_to_lt,
        "archived_at": now if archived_to_lt else None,
        "archive_attempts": 0,
        "links": {},
        "closed_at": closed_at,
        "closed_by": closed_by,
    }


# ─── 7 个 job_status 的预设 state ────────────────────────────────────────────

SAMPLE_STATES: Dict[str, Dict[str, Any]] = {
    "open": make_state(
        "JOB-MOCK-OPEN",
        "open",
        events=[event_by_level(1)],
    ),
    "acknowledged": make_state(
        "JOB-MOCK-ACK",
        "acknowledged",
        events=[event_by_level(2)],
        accepted_by={
            "open_id": "ou_test_user_001",
            "name": "测试员A",
            "accepted_at": _now(),
        },
    ),
    "rectifying": make_state(
        "JOB-MOCK-RECT",
        "rectifying",
        events=[event_by_level(2)],
        accepted_by={
            "open_id": "ou_test_user_001",
            "name": "测试员A",
            "accepted_at": _now(),
        },
        materials={"submissions": []},
    ),
    "materials_in_audit": make_state(
        "JOB-MOCK-AUDIT",
        "materials_in_audit",
        events=[event_by_level(3)],
        accepted_by={
            "open_id": "ou_test_user_001",
            "name": "测试员A",
            "accepted_at": _now(),
        },
        materials={
            "submissions": [
                {
                    "review_text": "现场已清理可燃气体源，通风良好，可继续作业。",
                    "submitted_by": "ou_test_user_001",
                    "submitted_at": _now(),
                }
            ],
            "latest_review_text": "现场已清理可燃气体源，通风良好，可继续作业。",
            "latest_at": _now(),
        },
    ),
    "waiting_human_review": make_state(
        "JOB-MOCK-REVIEW",
        "waiting_human_review",
        events=[event_by_level(4)],
        accepted_by={
            "open_id": "ou_test_user_001",
            "name": "测试员A",
            "accepted_at": _now(),
        },
        materials={
            "submissions": [
                {
                    "review_text": "已驱离未授权人员；启动应急响应。",
                    "submitted_by": "ou_test_user_001",
                    "submitted_at": _now(),
                }
            ],
            "latest_review_text": "已驱离未授权人员；启动应急响应。",
            "latest_at": _now(),
        },
        review={
            "p9_opinion": {
                "verdict": "pass",
                "confidence": 0.92,
                "comment": "[mock] materials_audit 审核通过；建议进入 ready_to_close。",
                "audited_at": _now(),
                "auditor": "P9-Mock-Bot",
            }
        },
    ),
    "ready_to_close": make_state(
        "JOB-MOCK-CLOSE",
        "ready_to_close",
        events=[event_by_level(3)],
        accepted_by={
            "open_id": "ou_test_user_001",
            "name": "测试员A",
            "accepted_at": _now(),
        },
        materials={
            "submissions": [
                {
                    "review_text": "全部事件已处置；可关闭告警。",
                    "submitted_by": "ou_test_user_001",
                    "submitted_at": _now(),
                }
            ],
            "latest_review_text": "全部事件已处置；可关闭告警。",
            "latest_at": _now(),
        },
        review={
            "p9_opinion": {
                "verdict": "pass",
                "confidence": 0.95,
                "comment": "[mock] 通过；建议关闭。",
                "audited_at": _now(),
                "auditor": "P9-Mock-Bot",
            }
        },
    ),
    "closed": make_state(
        "JOB-MOCK-CLOSED",
        "closed",
        events=[event_by_level(3)],
        accepted_by={
            "open_id": "ou_test_user_001",
            "name": "测试员A",
            "accepted_at": _now(),
        },
        materials={
            "submissions": [
                {
                    "review_text": "全部事件已处置完毕；终审通过。",
                    "submitted_by": "ou_test_user_001",
                    "submitted_at": _now(),
                }
            ],
            "latest_review_text": "全部事件已处置完毕；终审通过。",
            "latest_at": _now(),
        },
        review={
            "last_decision": "approved",
            "last_comment": "终审通过；归档完成。",
            "last_at": _now(),
            "history": [
                {
                    "decision": "approved",
                    "comment": "终审通过；归档完成。",
                    "by": "ou_test_user_002",
                    "at": _now(),
                }
            ],
        },
        archived_to_lt=True,
        closed_at=_now(),
        closed_by="ou_test_user_002",
    ),
}


def get_sample(job_status: str) -> Dict[str, Any]:
    """按 job_status 取一份 mock state。"""
    if job_status not in SAMPLE_STATES:
        raise ValueError(f"未知 job_status={job_status!r}; 可选: {list(SAMPLE_STATES.keys())}")
    import copy
    return copy.deepcopy(SAMPLE_STATES[job_status])


__all__ = ["SAMPLE_STATES", "make_state", "get_sample"]