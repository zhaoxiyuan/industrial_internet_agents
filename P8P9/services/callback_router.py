# P8P9/services/callback_router.py — 飞书 callback 路由 service
#
# 设计依据：
#   - §6.3.3 飞书 callback 路由（按钮 → business_actions）
#   - §2.0.1 service 边界（不 import langchain；纯 if-else 表查询）
#
# 公开 API：
#   - route_card_callback(event: dict) -> dict
#   - create_attachment_download_link(evidence_id: str, **kwargs) -> dict
#
# 输入 payload 结构（飞书 card.action.trigger）：
#   {
#     "header": {"event_id": "...", "event_type": "card.action.trigger"},
#     "event": {
#       "action": {
#         "value": "<JSON string>"（Card 2.0 强制 string，dict 不行）
#         # 或 form_value（带 textarea 时）
#       },
#       "operator": {"open_id": "ou_xxx", "user_name": "张三"},
#       "context": {"open_chat_id": "oc_xxx", "open_message_id": "om_xxx"},
#     },
#   }
#
# 路由表：
#   - acknowledge_disposition / submit_rectification_materials / relinquish_job
#   - escalate_risk / downgrade_risk / record_closure_review
#   - download_attachment

from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

# 让 callback_router 独立可运行（不被 web_server / card_render 链式 import 时）
# 也确保能读 FEISHU_USER_MAP。项目根的 .env 含 FEISHU_USER_MAP 配置。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"
if _DOTENV_AVAILABLE and _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

from .. import business_actions
from ..links import ClosureLinkService
# 2026-09-17：触发 audit_scheduler 模块级 _register_self() 把
# agent_audit_job 注入到 business_actions._audit_scheduler。
# 否则 audit_scheduler 从未被 import，_audit_scheduler 永远是 None，
# submit_rectification_materials 末尾的 _trigger_audit(job_id) 静默 return。
from . import audit_scheduler  # noqa: F401  仅触发 _register_self()
from ..state_machine import (
    ClosureService, StateNotFound, IllegalTransition,
    VersionConflict, BusinessConsistencyViolation,
    CardinalityExceeded, InputValidationError,
)


logger = logging.getLogger("P8P9.services.callback_router")


# ─── FEISHU_USER_MAP 反查（兜底 operator.user_name 缺失） ──────────────────

def _load_feishu_user_map() -> Dict[str, Dict[str, str]]:
    """读 .env 的 FEISHU_USER_MAP（{open_id: {role, name}}）。

    飞书 card.action.trigger 的 operator 字段经常缺 user_name（只含
    open_id/tenant_key/union_id）；callback 时按 open_id 反查 USER_MAP
    拿到中文姓名 + 角色（如「李宗睿（作业负责人）」）。
    """
    raw = os.environ.get("FEISHU_USER_MAP", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"FEISHU_USER_MAP 解析失败：{e}")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _lookup_user_name(open_id: Optional[str]) -> Optional[Dict[str, str]]:
    """按 open_id 反查 USER_MAP；返回 {name, role} 或 None。"""
    if not open_id:
        return None
    um = _load_feishu_user_map()
    info = um.get(open_id)
    if not isinstance(info, dict):
        return None
    name = (info.get("name") or "").strip()
    role = (info.get("role") or "").strip()
    if not name and not role:
        return None
    return {"name": name, "role": role}


def _format_user_display(open_id: Optional[str]) -> str:
    """「李宗睿（作业负责人）」格式；缺角色退化为「李宗睿」；都缺返回 open_id。"""
    info = _lookup_user_name(open_id)
    if not info:
        return open_id or "unknown"
    name = info["name"]
    role = info["role"]
    if name and role:
        return f"{name}（{role}）"
    return name or role or (open_id or "unknown")


# ─── 异常类 ──────────────────────────────────────────────────────────────────

class InvalidAction(Exception):
    """callback action 字段缺失或不在路由表内。"""


class InvalidOperator(Exception):
    """operator.open_id 缺失或非法。"""


class CallbackError(Exception):
    """callback 处理失败（含业务校验错误）。"""


# ─── 路由表 ──────────────────────────────────────────────────────────────────

# 路由函数签名前缀统一为 (job_id, actor, **kwargs) -> dict
# form_value 解析后注入到 kwargs
# 注意：download_attachment 是 _handle_download_attachment 的代理；在模块末尾追加

ROUTE_TABLE: Dict[str, Callable[..., Dict[str, Any]]] = {
    "acknowledge_disposition": business_actions.acknowledge_disposition,
    "submit_rectification_materials": business_actions.submit_rectification_materials,
    "relinquish_job": business_actions.relinquish_job,
    "escalate_risk": business_actions.escalate_risk,
    "downgrade_risk": business_actions.downgrade_risk,
    "record_closure_review": business_actions.record_closure_review,
}


# ─── 主入口：route_card_callback ──────────────────────────────────────────────

def route_card_callback(event: Dict[str, Any]) -> Dict[str, Any]:
    """§6.3.3：解析飞书 callback payload → 路由表 → 业务动作入口。

    Returns:
        业务动作返回值（一般是新 state）
        或 _handle_download_attachment 返回的 oss_url dict

    Raises:
        InvalidAction: action 缺失 / 不在路由表
        InvalidOperator: operator.open_id 缺失
        CallbackError: 路由失败（含业务校验异常）
    """
    parsed = _parse_payload(event)
    action = parsed["action"]
    operator = parsed["operator"]
    value = parsed["value"]
    form_value = parsed["form_value"]

    if action not in ROUTE_TABLE:
        raise InvalidAction(f"未知 action={action!r}")

    if not operator.get("open_id"):
        raise InvalidOperator(f"operator.open_id 缺失：action={action!r}")

    open_id = operator["open_id"]
    # 2026-09-17：operator.user_name 缺失时按 open_id 反查 FEISHU_USER_MAP
    # 拿到「李宗睿（作业负责人）」格式的姓名，避免卡片显示空接取人。
    user_name = operator.get("user_name") or operator.get("name") or ""
    user_role = ""
    if not user_name:
        derived = _lookup_user_name(open_id)
        if derived:
            user_name = derived.get("name") or ""
            user_role = derived.get("role") or ""
    # 仅当 user_name 不是已 format（含括号）时才补 role
    display_name = user_name
    if user_role and "（" not in user_name and "(" not in user_name:
        display_name = f"{user_name}（{user_role}）"
    actor = {
        "open_id": open_id,
        "name": display_name,
    }
    job_id = value.get("job_id") or form_value.get("job_id")
    if not job_id:
        raise InvalidAction(f"action={action!r} 缺 job_id")

    expected_version = value.get("expected_version")
    if expected_version is None:
        expected_version = form_value.get("expected_version")

    handler = ROUTE_TABLE[action]

    # download_attachment 单独处理（不走 business_actions）
    if action == "download_attachment":
        evidence_id = value.get("evidence_id") or form_value.get("evidence_id")
        if not evidence_id:
            raise InvalidAction("download_attachment 缺 evidence_id")
        return _handle_download_attachment(
            job_id=job_id, evidence_id=evidence_id, actor=actor,
        )

    # 业务动作统一调度
    kwargs: Dict[str, Any] = {
        "actor": actor,
        "expected_version": expected_version,
    }
    # 把 form_value 字段合并（reason / comment / event_id / new_level / evidence_ids）
    for k, v in form_value.items():
        if k not in ("job_id", "expected_version"):
            kwargs[k] = v

    # 不同动作的额外必填字段校验
    if action == "acknowledge_disposition":
        pass
    elif action == "submit_rectification_materials":
        kwargs.setdefault("review_text", "")
        kwargs.setdefault("submissions", [])
        kwargs.setdefault("event_ids", None)
    elif action == "relinquish_job":
        kwargs.setdefault("reason", "")
    elif action == "escalate_risk":
        kwargs.setdefault("event_id", value.get("event_id") or form_value.get("event_id"))
        kwargs.setdefault("new_level", int(value.get("new_level", 0)) if value.get("new_level") else None)
        kwargs.setdefault("reason", "")
        kwargs.setdefault("evidence_ids", [])
    elif action == "downgrade_risk":
        kwargs.setdefault("event_id", value.get("event_id") or form_value.get("event_id"))
        kwargs.setdefault("new_level", int(value.get("new_level", 0)) if value.get("new_level") else None)
        kwargs.setdefault("reason", "")
        kwargs.setdefault("evidence_ids", [])
    elif action == "record_closure_review":
        kwargs.setdefault("decision", value.get("decision") or form_value.get("decision", ""))
        kwargs.setdefault("comment", "")

    try:
        return handler(job_id, **kwargs)
    except (IllegalTransition, VersionConflict, BusinessConsistencyViolation,
            CardinalityExceeded, InputValidationError) as e:
        # 业务校验异常 → 包装为 CallbackError，gateway 转成 toast
        logger.warning(f"callback 业务校验失败：action={action} job_id={job_id} err={e}")
        raise CallbackError(str(e)) from e
    except StateNotFound as e:
        raise CallbackError(f"job_id={job_id} 不存在") from e
    except Exception as e:
        logger.exception(f"callback 路由失败：action={action} job_id={job_id}")
        raise CallbackError(f"内部错误: {e}") from e


# ─── 辅助：payload 解析 ──────────────────────────────────────────────────────

def _normalize_flat_to_nested(event: Dict[str, Any]) -> Dict[str, Any]:
    """2026-09-17 修复：把飞书真实平铺格式 callback 转成内部统一嵌套格式。

    飞书 card.action.trigger 实际发来的是**平铺格式**（见
    data/card_callbacks.jsonl 历史样本）：event_type / action / operator_open_id
    / operator_name / open_chat_id / job_id / alert_id 等都在顶层。

    P8P9 内部统一按**嵌套格式**处理（event.event.action.value 等）。
    已有 event 嵌套时直接 return（agent 自己调的格式不动）。

    平铺→嵌套映射：
        平铺字段            → 嵌套路径
        event_type          → event.type
        action / button_text → event.action.name + event.action.value.action
        operator_open_id     → event.operator.open_id
        operator_name        → event.operator.user_name
        open_chat_id         → event.context.open_chat_id
        message_id           → event.context.open_message_id
        job_id               → event.action.value.job_id
        alert_id             → event.action.value.alert_id
        expected_version     → event.action.value.expected_version
        decision             → event.action.value.decision
        event_id             → event.action.value.event_id
        new_level            → event.action.value.new_level
        reason/comment/review_text/submissions/event_ids → event.action.form_value
    """
    if "event" in event:
        return event  # 已是嵌套格式（agent 路径），不动
    action_name = event.get("action") or event.get("button_text") or ""
    value_dict = {
        "action": action_name,
        "job_id": event.get("job_id", ""),
        "alert_id": event.get("alert_id"),
        "expected_version": event.get("expected_version"),
        "decision": event.get("decision"),
        "event_id": event.get("event_id"),
        "new_level": event.get("new_level"),
    }
    form_value: Dict[str, Any] = {}
    for k in ("reason", "comment", "review_text", "submissions", "event_ids",
              "evidence_id", "evidence_ids"):
        v = event.get(k)
        if v not in (None, "", [], {}):
            form_value[k] = v
    return {
        "event": {
            "type": event.get("event_type", "card.action.trigger"),
            "action": {
                "name": action_name,
                "value": json.dumps(value_dict, ensure_ascii=False),
                "form_value": form_value,
                "tag": "button",
            },
            "operator": {
                "open_id": event.get("operator_open_id", ""),
                "user_name": event.get("operator_name", ""),
            },
            "context": {
                "open_chat_id": event.get("open_chat_id", ""),
                "open_message_id": event.get("message_id", ""),
            },
        }
    }


def _parse_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """从飞书 callback event 中提取 action / value / form_value / operator。

    兼容三种 value 形式：
      - 单层 JSON string: '{"action": "...", ...}'
      - 双层 JSON string: '"{\\"action\\": \\"...\\", ...}"'（飞书有时包一层）
      - dict（已解析）

    兼容两种 payload 形式：
      - 嵌套格式：{event: {action: {...}, operator: {...}, ...}}（agent 自己调）
      - 平铺格式：{event_type, action, operator_open_id, ...}（飞书真实发来）
        → 顶部 _normalize_flat_to_nested 自动转嵌套

    2026-09-17 兼容 Card 2.0 form_submit 模式：
      - event.action.tag = "input"（form 容器内 input 元素）
      - event.action.input_value = 用户输入的文本
      - event.action.value = behaviors.value（含 action/job_id/expected_version）
      - event.action.name = 按钮名（业务 action 名）
    """
    # 2026-09-17：先把平铺格式（飞书真实）转嵌套；嵌套格式不动
    event = _normalize_flat_to_nested(event)
    ev_event = event.get("event") or {}
    raw_action = ev_event.get("action") or {}
    operator = ev_event.get("operator") or {}

    raw_value = raw_action.get("value")
    form_value = raw_action.get("form_value") or {}

    # 解析 raw_value
    value: Dict[str, Any] = {}
    if isinstance(raw_value, dict):
        value = raw_value
    elif isinstance(raw_value, str):
        value = _safe_json_loads(raw_value) or {}
        # 双层 JSON 兜底
        if isinstance(value, str):
            value = _safe_json_loads(value) or {}

    # 旧模式：button.value = JSON string 含 action 名 → value["action"]
    action = value.get("action") or raw_action.get("action") or ""

    # 新模式（form_submit）：button.name = 业务 action 名；input_value 是用户填的内容
    if not action:
        action = raw_action.get("name") or ""

    # form_value 也是 dict 即可（飞书已自动解析）
    if not isinstance(form_value, dict):
        form_value = {}

    # form_submit 模式下：input_value 当 form_value[input_name] 用
    if not form_value and raw_action.get("tag") == "input":
        # 尝试找 input 字段名（从 event.context 或最近的 button name 推断；
        # 简单做法：把所有 input 注入到 kwargs 让业务动作函数自己识别）
        input_value = raw_action.get("input_value")
        if input_value is not None:
            # 用 input 元素的 name 字段（来自 button.behaviors 推断暂不可行，
            # 业务动作按字段名自取：relinquish_job 取 reason，submit 取 review_text）。
            # 通用兜底：把 input_value 同时注入 reason + review_text 让业务函数自取。
            form_value = {"reason": input_value, "review_text": input_value,
                         "input_value": input_value}

    return {
        "action": action,
        "value": value,
        "form_value": form_value,
        "operator": operator,
    }


def _safe_json_loads(s: str) -> Any:
    """宽容 JSON 解析；失败返回 None。"""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


# ─── download_attachment 处理 ────────────────────────────────────────────────

def _handle_download_attachment(
    *, job_id: str, evidence_id: str, actor: Dict[str, Any],
) -> Dict[str, Any]:
    """不直接 generate oss URL；返回 create 链接（短 URL），由 web /dl/<token> 路由消费。"""
    link_svc = ClosureLinkService()
    return link_svc.create_attachment_link(
        job_id, evidence_id,
        actor=actor, ttl_minutes=24 * 60, max_consume_count=999,
    )


def create_attachment_download_link(evidence_id: str, **kwargs: Any) -> Dict[str, Any]:
    """外部入口：包装 _handle_download_attachment。"""
    return _handle_download_attachment(evidence_id=evidence_id, **kwargs)


# 把 _handle_download_attachment 追加到路由表（避免前向引用）
ROUTE_TABLE["download_attachment"] = _handle_download_attachment


__all__ = [
    "route_card_callback",
    "create_attachment_download_link",
    "InvalidAction",
    "InvalidOperator",
    "CallbackError",
]