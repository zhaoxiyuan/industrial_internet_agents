# P8P9/services/card_render.py — 卡片渲染 service（§5.6 + §6.3.3）
#
# 设计依据：
#   - §5.6 CardKit sequence 机制（update_card_entity 必须 sequence 严格递增）
#   - §6.3.3 send_all_open_closure_cards / send_event_card_for_web
#   - MEMORY.md 约束：Web 端写状态必须自动刷新飞书卡片（send_event_card_for_web）
#
# 公开 API：
#   - update_job_card(job_id, version, *, actor_open_id=None) -> dict
#   - send_all_open_closure_cards(job_id, *, actor, chat_id, group_name=None, account_id=None) -> dict
#   - send_event_card_for_web(job_id, risk_event_id, *, actor="web-refresh") -> dict
#
# 内部：
#   - _send_event_card(job_id, risk_event_id, *, actor) -> dict
#   - _throttle(): CardKit sequence 节流（0.2s）

from __future__ import annotations
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..models import CARD_SEND_THROTTLE_SECONDS
from ..state_machine import ClosureService, StateNotFound
from ..cards import build_job_card


logger = logging.getLogger("P8P9.services.card_render")


# ─── 飞书 Gateway 模块延迟注入（避免循环 import） ────────────────────────────

_FEISHU_AVAILABLE = False
try:
    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    _OCG = _PROJECT_ROOT / "openclaw-channel-gateway-standalone"
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    if str(_OCG) not in sys.path:
        sys.path.insert(0, str(_OCG))

    import feishu_gateway_cli.feishu_card as feishu_card  # type: ignore
    import feishu_gateway_cli.feishu_sender as feishu_sender  # type: ignore
    _FEISHU_AVAILABLE = True
except Exception as e:
    logger.warning(f"feishu_gateway_cli 不可用（mock 模式）：{e}")
    feishu_card = None  # type: ignore
    feishu_sender = None  # type: ignore


# ─── 入口：update_job_card ────────────────────────────────────────────────────

def _entry_url(job_id: str) -> str:
    """Web 详情页 URL（默认 http://127.0.0.1:8089 对齐 P8P9 web_server 默认端口）。

    TODO(待开发·前端整合)：按 docs/风险处置卡片交互设计.md §2.4.1 + §1047，
          详情入口应改为 `closure/entry/<link_id>` 短时 token 链接（不是 job_id 直接暴露），
          返回 HTML 详情页（含复核/审核 textarea + 附件上传 + 多 event 勾选）。
          当前是临时 JSON API 入口（用于本地调试 + 联调）；前端整合时统一风格与开发。
    """
    import os
    base = os.environ.get("P8P9_WEB_BASE_URL", "http://127.0.0.1:8089")
    return f"{base}/api/closure/jobs/{job_id}"


def _dl_link_factory(job_id: str) -> callable:
    """closed 态附件链接工厂：调 ClosureLinkService.create_attachment_link。"""
    from ..links import ClosureLinkService

    svc = ClosureLinkService()
    def factory(evidence_id: str) -> str:
        result = svc.create_attachment_link(
            job_id, evidence_id,
            actor={"open_id": "system-archive-bot"},
            ttl_minutes=24 * 60,
            max_consume_count=999,
        )
        return result["short_url"]
    return factory


def _throttle() -> None:
    """§5.6 CardKit sequence 机制：0.2s 节流。"""
    time.sleep(CARD_SEND_THROTTLE_SECONDS)


def _build_card_json(state: Dict[str, Any], version: int, *, actor_open_id: Optional[str] = None) -> Dict[str, Any]:
    """构造 Card 2.0 dict。"""
    job_id = state.get("job_id", "?")
    return build_job_card(
        state=state, version=version,
        entry_url=_entry_url(job_id),
        actor_open_id=actor_open_id,
        dl_link_factory=_dl_link_factory(job_id),
    )


# ─── 公开 API ────────────────────────────────────────────────────────────────

def update_job_card(
    job_id: str, version: int, *, actor_open_id: Optional[str] = None,
) -> Dict[str, Any]:
    """§5.6 + §6.3.3：刷新已有 card entity（按 card_binding.card_id）。

    card_binding is None → skip（no-op）。
    """
    _throttle()
    svc = ClosureService()
    try:
        state = svc.get_state(job_id)
    except StateNotFound:
        return {"status": "skipped", "reason": "state_not_found", "job_id": job_id}

    binding = state.get("card_binding") or {}
    if not binding.get("card_id") or not binding.get("alert_id"):
        return {"status": "skipped", "reason": "no_card_binding", "job_id": job_id}

    if not _FEISHU_AVAILABLE:
        return {
            "status": "mock",
            "reason": "feishu_gateway_unavailable",
            "job_id": job_id,
            "version": version,
        }

    card_id = binding["card_id"]
    alert_id = binding["alert_id"]
    new_card_json = _build_card_json(state, version, actor_open_id=actor_open_id)

    # 反查 sequence
    entry = feishu_card.lookup_card_id(alert_id) or {}
    sequence = int(entry.get("sequence") or 0) + 1

    try:
        feishu_card.update_card_entity(
            card_id, new_card_json,
            sequence=sequence,
            account_id=binding.get("account_id"),
        )
        # 落盘新 sequence
        feishu_card.register_card(
            alert_id, card_id,
            account_id=binding.get("account_id"),
            message_id=entry.get("message_id"),
            sequence=sequence,
            card_json=new_card_json,
        )
        return {
            "status": "updated",
            "job_id": job_id,
            "card_id": card_id,
            "sequence": sequence,
            "version": version,
            # 2026-09-17：返回 card_json 让 callback 响应带回新卡片数据，
            # 飞书 client 收到 callback 后用新卡片替换原 message（不会切回原状态）。
            "card_json": new_card_json,
        }
    except Exception as e:
        logger.exception(f"update_card_entity 失败：job_id={job_id} err={e}")
        return {"status": "error", "job_id": job_id, "error": str(e)}


def send_all_open_closure_cards(
    job_id: str, *, actor: str, chat_id: str,
    group_name: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """§6.3.3：初始化发送（或批量重发）。

    - 调 ClosureService.get_state
    - 对每个 event 调 _send_event_card（per-event 卡片）
    - 写 card_binding 到 state（首次）
    """
    svc = ClosureService()
    try:
        state = svc.get_state(job_id)
    except StateNotFound:
        return {"status": "error", "reason": "state_not_found", "job_id": job_id}

    results: list = []
    events = state.get("events", []) or []

    for event in events:
        rid = event.get("risk_event_id")
        if not rid:
            continue
        r = _send_event_card(job_id, rid, actor=actor, chat_id=chat_id,
                              group_name=group_name, account_id=account_id)
        results.append({"event_id": rid, **r})

    # 写 card_binding（取首个成功的 card_id）
    if not _FEISHU_AVAILABLE:
        return {
            "status": "mock",
            "job_id": job_id,
            "results": results,
            "actor": actor,
        }

    return {
        "status": "ok",
        "job_id": job_id,
        "results": results,
        "actor": actor,
    }


def send_event_card_for_web(
    job_id: str, risk_event_id: str, *, actor: str = "web-refresh",
) -> Dict[str, Any]:
    """§6.3.3（MEMORY.md 约束）：Web 端写状态后调，按 event 刷新。"""
    return _send_event_card(job_id, risk_event_id, actor=actor)


# ─── 私有：单 event 卡片发送 ────────────────────────────────────────────────

def _send_event_card(
    job_id: str, risk_event_id: str, *, actor: str,
    chat_id: Optional[str] = None,
    group_name: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """发送单 event 卡片。

    流程：
      1. ClosureService.get_state
      2. 构造 card_json（per-event：只展示这个 event）
      3. feishu_card.create_card_entity
      4. feishu_sender.send_to_group_card
      5. 落 register_card + 写 state.card_binding
    """
    _throttle()
    svc = ClosureService()
    try:
        state = svc.get_state(job_id)
    except StateNotFound:
        return {"status": "error", "reason": "state_not_found"}

    if not _FEISHU_AVAILABLE:
        return {"status": "mock", "actor": actor}

    # 构造 per-event state 视图
    event_view = _filter_state_for_event(state, risk_event_id)
    version = state.get("version", 0)
    card_json = _build_card_json(event_view, version)

    # alert_id = f"P8P9-{job_id}-{risk_event_id}"（保证唯一）
    alert_id = f"P8P9-{job_id}-{risk_event_id}"

    try:
        card_id = feishu_card.create_card_entity(card_json, account_id=account_id)
    except Exception as e:
        logger.exception(f"create_card_entity 失败：job_id={job_id} err={e}")
        return {"status": "error", "stage": "create_card_entity", "error": str(e)}

    # 落 register_card（account_id + message_id 持久化）
    try:
        feishu_card.register_card(
            alert_id, card_id,
            account_id=account_id, sequence=1, card_json=card_json,
        )
    except Exception as e:
        logger.warning(f"register_card 失败（非致命）：{e}")

    # send
    try:
        send_result = feishu_sender.send_to_group_card(
            card_json,
            chat_id=chat_id, group_name=group_name, account_id=account_id,
            alert_id=alert_id,
            idempotency_key=f"init:{job_id}:{risk_event_id}:{version}",
        )
    except Exception as e:
        logger.exception(f"send_to_group_card 失败：job_id={job_id} err={e}")
        return {"status": "error", "stage": "send_to_group_card", "error": str(e)}

    # 写 card_binding（首次；后续 update_job_card 接管）
    # 2026-09-17：删掉 cur["version"] = cur.get("version", 0) + 1。
    # 重发卡片是 UI 渲染动作，不是业务状态变更；不能 bump version，
    # 否则飞书按钮 embedded expected_version 立即过期 → VersionConflict。
    # state.version 应只在 set_job_status / set_event_status / patch_fields 时 bump。
    with svc._lock_for(job_id):
        cur = svc.get_state(job_id)
        cur["card_binding"] = {
            "card_id": card_id,
            "alert_id": alert_id,
            "chat_id": chat_id,
            "group_name": group_name,
            "account_id": account_id,
            "bound_at": datetime.now(timezone.utc).isoformat(),
            "bound_by": actor,
            "risk_event_id": risk_event_id,
        }
        cur["updated_at"] = datetime.now(timezone.utc).isoformat()
        svc._atomic_write(job_id, cur)

    return {
        "status": "sent",
        "alert_id": alert_id,
        "card_id": card_id,
        "send_result": getattr(send_result, "message_id", None) or str(send_result),
    }


def _filter_state_for_event(state: Dict[str, Any], risk_event_id: str) -> Dict[str, Any]:
    """per-event state 视图：仅展示目标 event + display_risk_level = event.risk_level。"""
    events = state.get("events", [])
    target = next((e for e in events if e.get("risk_event_id") == risk_event_id), None)
    if target is None:
        target = events[0] if events else {"risk_event_id": risk_event_id, "risk_level": 1}
    view = dict(state)
    view["events"] = [target]
    view["display_risk_level"] = int(target.get("risk_level", state.get("display_risk_level", 0)))
    return view


# ─── 注入钩子：让 business_actions 调本 service ──────────────────────────────

def _register_self() -> None:
    """import 时注册到 business_actions._card_renderer。"""
    try:
        from .. import business_actions
        business_actions.register_card_renderer(update_job_card)
    except Exception as e:
        logger.warning(f"register_card_renderer 失败：{e}")


_register_self()


__all__ = [
    "update_job_card",
    "send_all_open_closure_cards",
    "send_event_card_for_web",
]