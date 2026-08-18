"""飞书交互式 Card 按钮回调 API v2（2026-08-17）。

================================================================================
职责
================================================================================

接收飞书侧 POST 过来的 Card 按钮点击事件，把按钮的 ``action`` + ``alert_id``
解析后记录到本地审计日志（JSONL），并按用户选择路由到下游处置（P8 工作流）。

V2 新增（相对于 v1）：
    1. **幂等**——同一个 alert_id 只能被处理一次；第二次点击返回 toast 警告，
       不会重复写日志，也不会再替换卡片。
    2. **Card 替换**——首次点击后，把群里所有人看到的卡替换为「已处置」状态
       （header 改绿色 + 显示处理人 + 处理结果 + 时间戳 + 移除按钮）。
    3. **字段兜底**——button_text 缺失时按 action 反查中文标签；
       operator_name 缺失时按 open_id 反查 .env 的 FEISHU_USER_MAP。

飞书侧回调 payload 形态（参考飞书开放平台 Card 交互事件文档）：
    {
      "schema": "2.0",
      "header": {
        "event_id": "...",
        "event_type": "card.action.trigger",
        "app_id": "...",
        "tenant_key": "...",
        "create_time": "..."
      },
      "event": {
        "operator": {"open_id": "ou_xxx", "user_name": "张三"},
        "action": {
          "value": {"action": "ack", "alert_id": "gas_20260817_001"},
          "tag": "button",
          "text": {"tag": "plain_text", "content": "已知悉"},
          "type": "default"
        },
        "context": {
          "open_message_id": "om_xxx",
          "open_chat_id": "oc_xxx"
        }
      }
    }

================================================================================
约束（CLAUDE.md / 安全）
================================================================================

    - 入参校验严格：缺 event / action.value.action → 400
    - api_key 字段（如果飞书带 X-Lark-Request-Token）走脱敏日志
    - 写审计日志时不在 logger 里 dump 整个 payload（含用户姓名）
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("server")

# 审计日志路径（绝对路径；项目根 data/card_callbacks.jsonl）
_ROOT = Path(__file__).resolve().parent.parent.parent
_AUDIT_LOG = _ROOT / "data" / "card_callbacks.jsonl"
_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)

# 线程安全写（飞书可能在短时间 POST 多条）
_audit_lock = threading.Lock()

# action → 中文标签兜底（button_text 缺失时使用）
_ACTION_LABELS: Dict[str, str] = {
    "handle": "立即处理",
    "ack": "已知悉",
    "false_alarm": "误报",
    "confirm": "确认",
    "execute": "执行",
    "cancel": "取消",
    "reject": "驳回",
    "delete": "删除",
}


# ============================================================
# 内部辅助
# ============================================================

def _lookup_user_name(open_id: Optional[str]) -> Optional[str]:
    """按 open_id 从 .env FEISHU_USER_MAP 反查中文名。

    FEISHU_USER_MAP 形态：``{"ou_xxx": {"role": "...", "name": "李宗睿"}}``。
    """
    if not open_id:
        return None
    raw = os.environ.get("FEISHU_USER_MAP", "").strip()
    if not raw:
        return None
    try:
        user_map = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(user_map, dict):
        return None
    entry = user_map.get(open_id)
    if isinstance(entry, dict):
        name = (entry.get("name") or "").strip()
        return name or None
    return None


def _action_label(action: Optional[str]) -> str:
    """action → 中文显示标签。未知 action 原样返回。"""
    if not action:
        return ""
    return _ACTION_LABELS.get(action.strip().lower(), action.strip())


def _check_already_processed(alert_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """检查 alert_id 是否已被处置过。

    按 audit log 倒序扫描（最新在前），找到该 alert_id 的最早一条记录。
    同一 alert_id 在不同时间被多人点击时，取**最早一次**作为"获胜者"。

    Args:
        alert_id: 业务告警 ID；None 或空字符串直接返回 None。

    Returns:
        已处置记录 dict（含 action / button_text / operator_name / received_at）；
        没找到返回 None。
    """
    if not alert_id:
        return None
    if not _AUDIT_LOG.exists():
        return None
    try:
        with _AUDIT_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    # 倒序扫（最新在前），找最早匹配
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("alert_id") == alert_id:
            return record
    return None


def _write_audit(record: Dict[str, Any]) -> None:
    """追加一条 JSONL 审计记录（线程安全）。"""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _audit_lock:
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _extract_action(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从飞书 Card 回调 payload 中抽取业务字段。

    V2 增强：
        - ``button_text`` 缺失时，按 action 反查中文标签兜底；
        - ``operator_name`` 缺失时，按 open_id 反查 FEISHU_USER_MAP 兜底。

    Returns:
        dict 含 action / alert_id / button_text / operator_open_id / operator_name
        / open_chat_id / message_id；
        缺失关键字段（action）返回 None。
    """
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    action_obj = event.get("action")
    if not isinstance(action_obj, dict):
        return None
    value = action_obj.get("value")
    if not isinstance(value, dict):
        return None
    action = value.get("action")
    if not isinstance(action, str) or not action.strip():
        return None
    action = action.strip()

    operator = event.get("operator") or {}
    open_id = (operator.get("open_id") or "").strip() or None

    # button_text 兜底：先看 action.text.content，没有则按 action 查中文标签
    raw_button_text = (action_obj.get("text") or {}).get("content", "").strip()
    button_text = raw_button_text or _action_label(action)

    # operator_name 兜底：先看 operator.user_name，没有则按 open_id 反查 USER_MAP
    raw_user_name = (operator.get("user_name") or "").strip()
    operator_name = raw_user_name or _lookup_user_name(open_id)

    return {
        "action": action,
        "alert_id": (value.get("alert_id") or "").strip() or None,
        "button_text": button_text or None,
        "operator_open_id": open_id,
        "operator_name": operator_name or None,
        "open_chat_id": ((event.get("context") or {}).get("open_chat_id") or "").strip() or None,
        "message_id": ((event.get("context") or {}).get("open_message_id") or "").strip() or None,
    }


def _build_processed_card(
    alert_id: Optional[str],
    action: str,
    button_text: str,
    operator_name: Optional[str],
    processed_at: str,
) -> Dict[str, Any]:
    """生成「已处置」替换卡（群里所有人看到的卡片会被替换成这个）。

    飞书 Card 规范：
        - header.template: 颜色（green=已处置 / grey=已关闭 / red=告警）
        - header.title: 标题
        - elements: 正文 + 分割线 + 提示
        - 不放按钮：群内不能再点击

    Args:
        alert_id: 业务告警 ID（可能为 None）。
        action: 回调 action 值（handle / ack / false_alarm 等）。
        button_text: 按钮显示文字（兜底用 _action_label）。
        operator_name: 处理人姓名（None 时显示"未知"）。
        processed_at: ISO 时间字符串。
    """
    label = button_text or _action_label(action) or action
    operator_display = operator_name or "未知"
    alert_display = alert_id or "未知告警"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",  # 绿色=已处置
            "title": {
                "tag": "plain_text",
                "content": f"已处置 · {label}",
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**告警 ID**：`{alert_display}`\n"
                        f"**处理结果**：{label}\n"
                        f"**处理人**：{operator_display}\n"
                        f"**处理时间**：{processed_at}"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "✅ 本告警已处置完毕，群内卡片已锁定。"
                        "如需重新发起处置，请联系值班管理员。"
                    ),
                },
            },
        ],
    }


# ============================================================
# HTTP 入口
# ============================================================

def handle_card_callback(handler, data: Dict[str, Any]) -> None:
    """POST /api/feishu/card-callback：飞书 Card 按钮点击回调（v2）。

    V2 业务行为：
        1. 飞书 url_verification 挑战——回显 challenge（飞书后台配置回调 URL 时的握手）
        2. 解析 payload（_extract_action v2，带 button_text / operator_name 兜底）
        3. **幂等检查**——同一 alert_id 已处置过 → 返回 toast 警告，不写日志、不替换卡
        4. 首次处置：写审计日志 + 返回 ``{toast, card}``（群里所有人看到「已处置」卡）
    """
    logger.info("[POST] /api/feishu/card-callback 进入: schema=%s type=%s",
                data.get("schema"), data.get("type"))

    # 飞书 URL 验证挑战（开发者后台首次配置回调 URL 时会发一次）
    if data.get("type") == "url_verification":
        challenge = data.get("challenge", "")
        logger.info(
            "[POST] /api/feishu/card-callback url_verification 挑战: challenge=%s",
            challenge[:32],
        )
        handler.send_json({"challenge": challenge})
        return

    info = _extract_action(data)
    if info is None:
        logger.warning(
            "[POST] /api/feishu/card-callback 参数错误: event 或 action.value 缺失"
        )
        handler.send_json({
            "status": "error",
            "error": "missing event.action.value.action",
        }, status=400)
        return

    # 幂等检查：同一 alert_id 已被处置过 → 拒绝 + 不替换卡
    alert_id = info["alert_id"]
    existing = _check_already_processed(alert_id)
    if existing is not None:
        logger.info(
            "[POST] /api/feishu/card-callback 幂等拦截: alert_id=%s "
            "首次处置 action=%s by=%s at=%s",
            alert_id,
            existing.get("action"),
            existing.get("operator_name"),
            existing.get("received_at"),
        )
        handler.send_json({
            "toast": {
                "type": "warning",
                "content": (
                    f"该告警已被 {existing.get('operator_name') or '他人'} "
                    f"于 {existing.get('received_at') or '之前'} 处置（{_action_label(existing.get('action')) or existing.get('action')}）"
                ),
            },
        })
        return

    # 首次处置：写审计日志 + 生成替换卡
    received_at = datetime.now().isoformat(timespec="seconds")
    record = {
        "received_at": received_at,
        "event_id": (data.get("header") or {}).get("event_id"),
        "event_type": (data.get("header") or {}).get("event_type"),
        **info,
    }
    _write_audit(record)

    # 脱敏日志：只在 logger 里记 action + alert_id + operator_open_id
    logger.info(
        "[POST] /api/feishu/card-callback 响应: action=%s alert_id=%s "
        "operator_open_id=%s button_text=%s",
        info["action"], info["alert_id"], info["operator_open_id"], info["button_text"],
    )

    # 构造回复：toast（仅点击者看到）+ card（群里所有人看到「已处置」卡）
    processed_card = _build_processed_card(
        alert_id=info["alert_id"],
        action=info["action"],
        button_text=info["button_text"] or "",
        operator_name=info["operator_name"],
        processed_at=received_at,
    )
    handler.send_json({
        "toast": {
            "type": "success",
            "content": f"已记录您的处置（{info['button_text'] or _action_label(info['action'])}）",
        },
        "card": processed_card,
    })


def handle_card_callback_list(handler) -> None:
    """GET /api/feishu/card-callbacks：列出最近 N 条审计记录（默认 50 条）。

    调试用；返回全部字段（包括 operator_name / open_id）便于排查。
    limit 可通过 ?limit=N 传入（默认 50，上限 500）。
    """
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query)
    try:
        limit = int(qs.get("limit", ["50"])[0])
    except (ValueError, TypeError):
        limit = 50
    limit = max(1, min(500, limit))

    records: list = []
    if _AUDIT_LOG.exists():
        with _AUDIT_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-limit:][::-1]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    logger.info(
        "[GET] /api/feishu/card-callbacks 响应: count=%d limit=%d",
        len(records), limit,
    )
    handler.send_json({"status": "ok", "count": len(records), "records": records})