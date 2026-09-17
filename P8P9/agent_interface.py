# P8P9/agent_interface.py — agent 只读 / 初始化接口
#
# ⚠️⚠️⚠️ 关键安全护栏 ⚠️⚠️⚠️
#
# 本模块**只暴露**给 agent 3 个接口：
#   - initialize_job_for_agent(job_id, *, actor) -> dict
#   - bind_card_for_agent(job_id, *, chat_id, actor) -> dict
#   - get_state_for_agent(job_id) -> dict
#
# **严禁**：
#   - import business_actions（任何写状态入口）
#   - import state_machine.ClosureService.set_job_status / set_event_status
#   - import services.callback_router.route_card_callback
#   - 添加任何 *_for_agent 写接口（set_job_status_for_agent / record_review_for_agent 等）
#
# 所有状态变更必须经：
#   - 飞书按钮 → services/callback_router.route_card_callback → business_actions
#   - Web 端提交 → web_server API → business_actions
#
# 这条护栏由 tests/test_agent_interface.py 静态 + 动态校验。

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from .state_machine import ClosureService, StateNotFound


logger = logging.getLogger("P8P9.agent_interface")


# ─── 接口 1：initialize_job_for_agent ────────────────────────────────────────

def initialize_job_for_agent(
    job_id: str, *, actor: str, events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """agent 调：创建 job + 同步 P7 events（mock 数据）。

    Args:
        job_id: 业务方传入的作业 ID
        actor: 标识（通常是 "P8-DispositionAgent" / "scheduler" / "admin"）
        events: P7 同步过来的风险事件列表（mock 数据从 fixtures 读）

    Returns:
        {
            "job_id": str,
            "version": int,
            "job_status": "open",
            "events": [{"risk_event_id", "risk_level", "event_status", ...}],
        }

    Raises:
        InputValidationError: job_id 含非法字符 / events 字段不全
        StateNotFound: 已经初始化过（idempotent：返回已存在的 state）
    """
    svc = ClosureService()
    try:
        existing = svc.get_state(job_id)
        logger.info(f"job_id={job_id} 已初始化；返回现有 state")
        return _state_to_agent_view(existing)
    except StateNotFound:
        pass

    state = svc.initialize_job(job_id, actor=actor, events=events or [])
    return _state_to_agent_view(state)


# ─── 接口 2：bind_card_for_agent ──────────────────────────────────────────────

def bind_card_for_agent(
    job_id: str, *, chat_id: str, actor: Dict[str, Any],
    group_name: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """agent 调：创建 placeholder CardKit 实体（不直接 send）。

    真实发送由 services/card_render.send_all_open_closure_cards 触发。

    Args:
        job_id: 作业 ID
        chat_id: 飞书群 ID (oc_xxx)
        actor: agent 标识 dict
        group_name: 可选，群名
        account_id: 可选，飞书 account

    Returns:
        {
            "job_id": str,
            "chat_id": str,
            "group_name": Optional[str],
            "account_id": Optional[str],
            "bound_by": str,
            "bound_at": ISO8601,
            "status": "bound" | "exists",
        }
    """
    svc = ClosureService()
    state = svc.get_state(job_id)  # StateNotFound if not exists
    binding = state.get("card_binding") or {}

    if binding.get("chat_id") == chat_id:
        return {
            "job_id": job_id,
            "chat_id": chat_id,
            "group_name": binding.get("group_name") or group_name,
            "account_id": binding.get("account_id") or account_id,
            "bound_by": binding.get("bound_by", ""),
            "bound_at": binding.get("bound_at", ""),
            "status": "exists",
        }

    # 仅绑关系，不发卡片（service 层后续按需发）
    new_binding = {
        "card_id": None,  # 真实 create_card_entity 由 card_render 完成
        "alert_id": None,
        "chat_id": chat_id,
        "group_name": group_name,
        "account_id": account_id,
        "risk_event_id": None,
    }

    # 走 svc.bind_card（白名单：仅写 card_binding 字段；不能写其它业务字段）
    new_state = svc.bind_card(
        job_id,
        actor=actor if isinstance(actor, dict) else {"open_id": str(actor)},
        binding=new_binding,
        expected_version=None,  # agent 初始化写不校验乐观锁
    )

    return {
        "job_id": new_state["job_id"],
        "chat_id": new_state["chat_id"],
        "group_name": new_state.get("group_name"),
        "account_id": new_state.get("account_id"),
        "bound_by": new_state["bound_by"],
        "bound_at": new_state["bound_at"],
        "status": new_state["status"],
        "version": new_state["version"],
    }


# ─── 接口 3：get_state_for_agent ──────────────────────────────────────────────

def get_state_for_agent(job_id: str) -> Dict[str, Any]:
    """agent 调：只读 state（脱敏 open_id 等 PII）。

    返回字段：
        job_id / version / job_status / display_risk_level
        events（脱敏：open_id → 'ou_***'；保留 risk_event_id / risk_level）
        accepted_by（脱敏）
        materials（保留 review_text + submissions 计数）
        review（保留 last_decision / last_comment，不含 by 字段）
        job_status_history（保留 to / at，by 字段脱敏）
        risk_changes（保留 action / reason 摘要）
        archived_to_lt / closed_at / created_at / updated_at
    """
    svc = ClosureService()
    state = svc.get_state(job_id)
    return _state_to_agent_view(state)


# ─── 内部：state → agent 视图 ────────────────────────────────────────────────

def _state_to_agent_view(state: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏：open_id / user_name 字段；保留业务字段。"""
    def mask(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if not value:
            return value
        # ou_xxx → ou_***
        if value.startswith("ou_"):
            return "ou_***"
        # 名字：李明 → 李*
        if len(value) > 1:
            return value[0] + "*" * (len(value) - 1)
        return "***"

    # events 脱敏
    events = []
    for e in state.get("events", []) or []:
        events.append({
            "risk_event_id": e.get("risk_event_id"),
            "risk_level": e.get("risk_level"),
            "risk_level_name": e.get("risk_level_name"),
            "event_type": e.get("event_type"),
            "event_status": e.get("event_status"),
            "first_seen": e.get("first_seen"),
            "last_seen": e.get("last_seen"),
            "involved_persons": [mask(p) for p in (e.get("involved_persons") or [])],
            "risk_basis": e.get("risk_basis"),
            "suggestions": e.get("suggestions"),
            "created_at": e.get("created_at"),
            "updated_at": e.get("updated_at"),
        })

    # accepted_by 脱敏
    ab = state.get("accepted_by") or {}
    accepted_by_view = None
    if ab:
        accepted_by_view = {
            "open_id": mask(ab.get("open_id")),
            "name": mask(ab.get("name")),
            "accepted_at": ab.get("accepted_at"),
        }

    # job_status_history 脱敏
    history = []
    for h in state.get("job_status_history", []) or []:
        history.append({
            "from": h.get("from"),
            "to": h.get("to"),
            "by": mask(h.get("by")),
            "by_name": mask(h.get("by_name")),
            "at": h.get("at"),
        })

    # review 脱敏
    review = state.get("review") or {}
    review_view = {
        "last_decision": review.get("last_decision"),
        "last_comment": review.get("last_comment"),
        "last_at": review.get("last_at"),
        "history": [
            {"decision": h.get("decision"), "comment": h.get("comment"), "at": h.get("at")}
            for h in (review.get("history") or [])
        ],
    }
    if "p9_opinion" in review:
        op = review["p9_opinion"]
        review_view["p9_opinion"] = {
            "verdict": op.get("verdict"),
            "confidence": op.get("confidence"),
            "comment": op.get("comment"),
            "audited_at": op.get("audited_at"),
            "auditor": mask(op.get("auditor")),
        }

    # risk_changes 摘要
    rc_view = []
    for rc in state.get("risk_changes", []) or []:
        rc_view.append({
            "change_id": rc.get("change_id"),
            "action": rc.get("action"),
            "from_level": rc.get("from_level"),
            "to_level": rc.get("to_level"),
            "status": rc.get("status"),
            "reason_summary": (rc.get("reason") or "")[:80],
            "at": rc.get("at"),
        })

    return {
        "job_id": state.get("job_id"),
        "version": state.get("version"),
        "job_status": state.get("job_status"),
        "display_risk_level": state.get("display_risk_level"),
        "accepted_by": accepted_by_view,
        "materials_summary": {
            "submission_count": len((state.get("materials") or {}).get("submissions") or []),
            "latest_review_text": (state.get("materials") or {}).get("latest_review_text"),
            "latest_at": (state.get("materials") or {}).get("latest_at"),
        },
        "review": review_view,
        "risk_changes": rc_view,
        "job_status_history": history,
        "events": events,
        "card_binding": state.get("card_binding"),  # 不脱敏（chat_id 非 PII）
        "archived_to_lt": state.get("archived_to_lt"),
        "closed_at": state.get("closed_at"),
        "closed_by": mask(state.get("closed_by")),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "relinquish_count": state.get("relinquish_count"),
    }


__all__ = [
    "initialize_job_for_agent",
    "bind_card_for_agent",
    "get_state_for_agent",
]