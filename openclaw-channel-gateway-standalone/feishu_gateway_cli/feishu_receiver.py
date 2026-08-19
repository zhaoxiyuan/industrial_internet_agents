"""feishu_gateway_cli — P8 飞书通道适配器（精简版：只收，不拼）。

================================================================================
职责（极简版）
================================================================================

只做一件事：**从 Channel Gateway 拉取新的入站事件**。

不负责：
    - 消息解析 / 自然语言理解                 → 由调用方完成
    - 自动回复 / 回复模板                     → 由调用方完成
    - 业务决策（要不要通知某人 / 升级度）     → 由调用方完成
    - ACK 状态管理（默认不 ACK，留给调用方）  → 由调用方决定

本模块只暴露以下公开 API：

================================================================================
公开函数
================================================================================

    format_event(event, *, user_map, group_map) -> str
        把一条网关入站事件格式化为单行可读字符串：
            <时间>  <人名> (<单聊|群聊:群名>) | <消息内容>
        user_map / group_map 一般来自 _load_user_map() / _load_group_map()。

    poll_once(*, initial_sequence, account_id, group_name, person_name, channel) -> List[dict]
        单次拉取匹配过滤条件的事件，返回事件列表（无事件返回 []）。
        过滤参数：
            - account_id：按 event.accountId 字段 client-side 过滤
            - group_name + person_name：群聊过滤（要求群名匹配 + 发消息人名匹配）
            - person_name alone：单聊过滤（要求发消息人名匹配 + conv 是单聊）

    poll_and_print(*, interval, initial_sequence, account_id, group_name, person_name,
                   channel, do_print, show_raw, do_ack, exit_after, once, on_event) -> int
        持续轮询网关并把每条新事件：
            - 调用 on_event(event) 回调（如需）
            - 若 do_print=True 则打印 "有新消息啦"（+ 可选原始 JSON）
        on_event 返回 False 时停止轮询（供测试 / 一次性消费）。

    mark_event_status(event_id, *, status, details) -> dict
        修改一条入站事件的处理状态（封装 Gateway POST /v1/events/{id}/ack）。

================================================================================
CLI 命令（在任何项目终端运行）
================================================================================

下面命令是**单行命令**（bash / PowerShell / CMD / Git Bash 通用）：

    # 默认：持续轮询，每条新事件打印 "有新消息啦"（Ctrl+C 停止）
    python -m feishu_gateway_cli.feishu_receiver poll

    # 自定义轮询间隔（秒；默认 1.0）
    python -m feishu_gateway_cli.feishu_receiver poll --interval 2.0

    # 从指定 sequence 开始（断点续传；默认 -1=自动跳过历史，只接新事件）
    python -m feishu_gateway_cli.feishu_receiver poll --initial-sequence 12

    # 显式重放历史（从 sequence=0 开始拉所有事件，配合 --once / --exit-after 抽样）
    python -m feishu_gateway_cli.feishu_receiver poll --initial-sequence 0 --once --exit-after 5

    # 只拉一次就退出：无新消息返回空；有新消息打印 "有新消息啦" + 完整 event JSON
    python -m feishu_gateway_cli.feishu_receiver poll --once

    # 显式重放历史并按 account_id 过滤
    python -m feishu_gateway_cli.feishu_receiver poll --initial-sequence 0 --once --account-id default

    # 群聊过滤：群聊名称 + 人名（两条件都满足）
    python -m feishu_gateway_cli.feishu_receiver poll --once --group-name "应急响应群" --person-name "张三"

    # 单聊过滤：人名（单独使用）
    python -m feishu_gateway_cli.feishu_receiver poll --once --person-name "李四"

    # 收到 N 条事件后退出（常用于测试 / 抽样）
    python -m feishu_gateway_cli.feishu_receiver poll --exit-after 5

    # 静默模式：轮询但不打印到控制台（适合后台守护）
    python -m feishu_gateway_cli.feishu_receiver poll --no-print

    # 同时打印原始 event JSON（调试用；持续模式每事件后追加一次）
    python -m feishu_gateway_cli.feishu_receiver poll --show-raw

    # 收到事件后自动 ACK（默认不 ACK，避免误删未处理消息）
    python -m feishu_gateway_cli.feishu_receiver poll --ack

    # 主动修改一条事件的状态（ACK 或 ignore；封装 /v1/events/{id}/ack）
    python -m feishu_gateway_cli.feishu_receiver mark --event-id evt_d654e4f3-13d5-4291-84af-b70f7d798bdb
    python -m feishu_gateway_cli.feishu_receiver mark --event-id evt_xxx --status ignored --details '{"reason":"duplicate"}'

`--once` 模式输出示例：

    $ python -m feishu_gateway_cli.feishu_receiver poll --once --person-name "张三"
    有新消息啦
    [
      {
        "platformEventId": "546edf1d8e5c0f477a5eb581049dc785",
        "channel": "feishu",
        "accountId": "default",
        ...
      }
    ]

    $ python -m feishu_gateway_cli.feishu_receiver poll --once
    无新消息

持续模式输出示例：

    有新消息啦
    有新消息啦
    有新消息啦
    ^C  (Ctrl+C 停止)

退出码：
    0  正常退出（--once 完成 / 收到 --exit-after 条件 / Ctrl+C）
    1  参数错误
    2  其它未捕获异常（连不上网关等）

================================================================================
依赖
================================================================================

    agents.channel_gateway_client.poll_inbound_events / ack_event
        HTTP 调用底层
    os.environ["FEISHU_USER_MAP"]   open_id → {name, ...}  反查姓名（JSON 字符串）
    os.environ["FEISHU_GROUP_MAP"]  chat_id → {name, ...}  反查群名（JSON 字符串）

================================================================================
日志规范
================================================================================

入口 / 出口 / 异常按 [p8-notify-receiver] 进入 / 响应 / 异常 格式打印。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from agents.channel_gateway_client import (
    GatewayError,
    ack_event,
    poll_inbound_events,
)

# 2026-08-18：feishu_card 业务逻辑下沉（process_card_callback），
# receiver re-export 便于外部统一 import。
# V5（2026-08-18 升级）：process_card_callback 改为返回 {toast} + 异步调 cardkit 更新卡片，
# 严格按 docs/飞书卡片教程/ 官方路径。
from .feishu_card import process_card_callback as _process_card_callback


# ============================================================
# 常量
# ============================================================

FEISHU_USER_MAP_KEY: str = "FEISHU_USER_MAP"
FEISHU_GROUP_MAP_KEY: str = "FEISHU_GROUP_MAP"
FEISHU_CHANNEL: str = "feishu"

# 默认轮询间隔（秒）。可被 CLI --interval 覆盖。
DEFAULT_POLL_INTERVAL: float = 1.0
# 收到事件后的退避间隔（秒），避免 busy loop。
IDLE_SLEEP: float = 0.5
# 每次拉取上限。网关上界 1000；这里取一个保守值。
POLL_LIMIT: int = 100
# 哨兵值：传给 poll_and_print / CLI --initial-sequence 时，
# 表示"自动从当前最新 sequence 开始，跳过历史"。
INITIAL_SEQUENCE_AUTO: int = -1


# ============================================================
# 日志
# ============================================================
# 统一用 basicConfig 配 root logger（幂等；多次调用不会重复加 handler）。
# 子 logger 只设 level；所有日志 propagate 到 root 统一输出，避免重复打印。

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("feishu_receiver")
logger.setLevel(logging.INFO)


# ============================================================
# 0. .env 加载（与 channel_gateway_client 一致，从项目根 .env 读取）
# ============================================================
# 与 agents.channel_gateway_client 保持一致的策略：项目根 .env 在模块加载时
# 一次性加载到 os.environ。这样无论调用方是 CLI 还是库调用，FEISHU_USER_MAP /
# FEISHU_GROUP_MAP 都能直接读到。

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)
else:
    load_dotenv(override=False)


# ============================================================
# 1. USER_MAP / GROUP_MAP 读取（私有）
# ============================================================
# 与 feishu_gateway_cli.feishu_sender 的格式完全一致：
#   USER_MAP  = {open_id:  {chat_id, role, name}}
#   GROUP_MAP = {chat_id:  {name, description}}
# 这里只读 name / name，不读其它列。

def _load_user_map() -> Dict[str, Dict[str, str]]:
    """从 .env 读 FEISHU_USER_MAP，返回 {open_id: {name, ...}, ...}。

    解析失败 → 空 dict（不打错误；调用方会按"找不到"处理）。
    """
    raw = os.environ.get(FEISHU_USER_MAP_KEY, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[p8-notify-receiver] FEISHU_USER_MAP 解析失败: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("[p8-notify-receiver] FEISHU_USER_MAP 顶层必须是 JSON object")
        return {}
    cleaned: Dict[str, Dict[str, str]] = {}
    for open_id, info in parsed.items():
        if not isinstance(info, dict):
            continue
        cleaned[str(open_id)] = {
            "name": str(info.get("name", "") or "").strip(),
        }
    return cleaned


def _load_group_map() -> Dict[str, Dict[str, str]]:
    """从 .env 读 FEISHU_GROUP_MAP，返回 {chat_id: {name, ...}, ...}。

    name 为空的记录视为无效丢弃。
    """
    raw = os.environ.get(FEISHU_GROUP_MAP_KEY, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[p8-notify-receiver] FEISHU_GROUP_MAP 解析失败: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("[p8-notify-receiver] FEISHU_GROUP_MAP 顶层必须是 JSON object")
        return {}
    cleaned: Dict[str, Dict[str, str]] = {}
    for chat_id, info in parsed.items():
        if not isinstance(info, dict):
            continue
        name = str(info.get("name", "") or "").strip()
        if not name:
            continue
        cleaned[str(chat_id).strip()] = {"name": name}
    return cleaned


# ============================================================
# 2. 事件字段提取（私有）
# ============================================================

def _extract_text(event: Dict[str, Any]) -> str:
    """从 event 抽取消息文本。优先 message.text，回退 raw.event.message.content 解析。"""
    msg = event.get("message") or {}
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        return text
    # 回退：从 raw.event.message.content 解析 {"text": "..."}
    raw = event.get("raw") or {}
    inner = raw.get("event") or {}
    inner_msg = inner.get("message") or {}
    content = inner_msg.get("content")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            t = parsed.get("text")
            if isinstance(t, str):
                return t
        except json.JSONDecodeError:
            return content
    return ""


def _extract_time(event: Dict[str, Any]) -> str:
    """从 event 抽取消息时间，返回 'YYYY-MM-DD HH:MM:SS' 形式。

    优先 occurredAt（ISO 8601）；缺失则回回 receivedAt。
    """
    raw_iso = event.get("occurredAt") or event.get("receivedAt") or ""
    if not isinstance(raw_iso, str):
        return ""
    # 取前 19 个字符 "YYYY-MM-DDTHH:MM:SS" 并把 T 换成空格
    return raw_iso[:19].replace("T", " ")


def _extract_sender_name(sender_id: str, user_map: Dict[str, Dict[str, str]]) -> str:
    """按 open_id 反查 USER_MAP.name；找不到 → 返回原 open_id。"""
    info = user_map.get(sender_id) or {}
    name = info.get("name") or ""
    return name or sender_id


def _extract_conversation_label(
    conv_id: str,
    conv_type: str,
    group_map: Dict[str, Dict[str, str]],
) -> str:
    """根据 conversation.type 返回 "(单聊)" 或 "(群聊:<群名>)"。

    conv_type 取值约定（来自飞书 gateway）：
        - "direct" / "p2p" → 单聊
        - "group" / "chat" → 群聊
    群聊时按 conv_id 在 GROUP_MAP 反查群名；找不到 → 用原 conv_id。
    """
    is_group = conv_type in ("group", "chat")
    if not is_group:
        return "(单聊)"
    info = group_map.get(conv_id) or {}
    name = info.get("name") or ""
    return f"(群聊:{name or conv_id})"


def _extract_conversation_type(event: Dict[str, Any]) -> str:
    """从 event 抽取 conversation.type；缺失则从 raw.event.message.chat_type 回退。"""
    conv = event.get("conversation") or {}
    ct = conv.get("type")
    if isinstance(ct, str) and ct:
        return ct
    raw = event.get("raw") or {}
    inner = raw.get("event") or {}
    inner_msg = inner.get("message") or {}
    return str(inner_msg.get("chat_type") or "")


def _extract_account_id(event: Dict[str, Any]) -> str:
    """从 event 抽取 accountId（飞书网关事件字段）。缺失返回空字符串。"""
    return str(event.get("accountId") or "")


# ============================================================
# 2.5 事件过滤（私有）
# ============================================================

def _matches_filter(
    event: Dict[str, Any],
    *,
    account_id: Optional[str],
    group_name: Optional[str],
    person_name: Optional[str],
    user_map: Dict[str, Dict[str, str]],
    group_map: Dict[str, Dict[str, str]],
) -> bool:
    """检查 event 是否匹配过滤条件。

    Args:
        event: 网关入站事件 dict。
        account_id: 可选；非 None 时要求 event.accountId 等于此值。
        group_name: 可选；与 person_name 一起表示"群聊过滤"。
        person_name: 可选；单独表示"单聊过滤"，与 group_name 一起表示"群聊过滤"。
        user_map: {open_id: {name}}；反查发消息人名。
        group_map: {chat_id: {name}}；反查群聊名称。

    Returns:
        True 表示事件通过过滤；False 表示被过滤掉。

    过滤规则：
        1. account_id：client-side 按 event.accountId 字段精确匹配（gateway /v1/events
           不直接传 account_id 过滤参数）。
        2. group_name + person_name：要求 conv 是群 + GROUP_MAP 中 chat_id 对应
           name == group_name + USER_MAP 中 open_id 对应 name == person_name。
        3. person_name alone：要求 conv 是单聊 + USER_MAP 中 open_id 对应 name == person_name。
        4. 都没传：全部放行。
    """
    # 1. account_id 过滤
    if account_id is not None:
        if _extract_account_id(event) != account_id:
            return False

    # 2/3. 会话过滤
    if group_name is None and person_name is None:
        return True  # 无会话过滤

    conv = event.get("conversation") or {}
    conv_id = str(conv.get("id") or "")
    conv_type = _extract_conversation_type(event)
    is_group = conv_type in ("group", "chat")

    sender = event.get("sender") or {}
    sender_id = str(sender.get("id") or "")
    sender_name = _extract_sender_name(sender_id, user_map)

    if group_name is not None:
        # 群聊过滤：要求 conv 是群 + 群名匹配 + 人名匹配
        if not is_group:
            return False
        group_info = group_map.get(conv_id) or {}
        actual_group_name = group_info.get("name") or ""
        if actual_group_name != group_name:
            return False
        if sender_name != person_name:
            return False
        return True

    # 单聊过滤（person_name alone）
    if is_group:
        return False
    return sender_name == person_name


# ============================================================
# 3. 公开 API
# ============================================================

def format_event(
    event: Dict[str, Any],
    *,
    user_map: Optional[Dict[str, Dict[str, str]]] = None,
    group_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """把一条网关入站事件格式化为单行可读字符串：

        <时间>  <人名> (<单聊|群聊:群名>) | <消息内容>

    Args:
        event: 网关入站事件（dict）；通常是 poll_inbound_events().events 的元素。
        user_map: 可选，{open_id: {name}}；缺省时临时从 .env 读。
        group_map: 可选，{chat_id: {name}}；缺省时临时从 .env 读。

    Returns:
        单行字符串。若任何字段缺失，对应位置用空字符串占位。
    """
    if user_map is None:
        user_map = _load_user_map()
    if group_map is None:
        group_map = _load_group_map()

    sender = event.get("sender") or {}
    sender_id = str(sender.get("id") or "")
    conv = event.get("conversation") or {}
    conv_id = str(conv.get("id") or "")
    conv_type = _extract_conversation_type(event)

    ts = _extract_time(event)
    name = _extract_sender_name(sender_id, user_map)
    conv_label = _extract_conversation_label(conv_id, conv_type, group_map)
    text = _extract_text(event)

    return f"{ts}  {name} {conv_label} | {text}"


# ============================================================
# 2.6 Card 按钮回调（公开 API，2026-08-18 新增）
# ============================================================
# Gateway 当前架构下 card.action.trigger 不入事件流（feishu.js 直接代理给业务端），
# 但 feishu_card 把核心业务逻辑下沉为 process_card_callback 后，
# 这里 re-export 便于上层统一 import；format_card_callback 提供可读单行格式化。

def process_card_callback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """receiver 层 re-export :func:`feishu_gateway_cli.feishu_card.process_card_callback`。

    V5 业务流程（严格按 docs/飞书卡片教程/ 官方路径）：
        1. ``url_verification`` 挑战 → ``{"challenge": "..."}``
        2. 入参非法（缺 event/action.value）→ ``{"status": "error", "error": "..."}``
        3. 幂等命中（同 alert_id 已处置）→ ``{"toast": {"type": "warning", "content": "..."}}``
        4. 首次成功：写审计日志 + 同步返回 ``{"toast": {"type": "success", "content": "..."}}``
           + daemon 线程异步调 PUT cardkit 全量更新卡片（飞书 callback 2s 内必须返回）。

    本函数是"识别卡片点击 HTTP 请求"的统一入口；卡片替换由 feishu_card 模块内部
    异步调用 cardkit 完成（不在 callback 响应里实时返回 card 字段——
    飞书 callback 协议不支持实时替换，且 callback 期间错误码 200810 不可流式更新）。

    Args:
        payload: 飞书 card.action.trigger 回调 payload dict。

    Returns:
        回给飞书的完整响应 dict（可能含 challenge / error / toast）。
    """
    return _process_card_callback(payload)


def format_card_callback(
    payload: Dict[str, Any],
    *,
    user_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """把飞书 card.action.trigger payload 格式化为可读单行字符串：

        <时间>  <人名> 按了"<按钮文字>"按钮（action=<action>, alert_id=<alert_id>）

    用于：
        - 调试场景下人工查看 card.click payload 的可读视图
        - 未来 Gateway 改造让 card 事件入事件流后，poll_and_print 拿到事件时打印
        - CLI --show-raw 模式下的辅助输出

    Args:
        payload: 飞书 card.action.trigger 回调 payload dict。
        user_map: 可选，{open_id: {name}}；缺省时临时从 .env 读。

    Returns:
        单行字符串。字段缺失时对应位置用空串或占位符。
    """
    if user_map is None:
        user_map = _load_user_map()

    # 时间：header.create_time 是毫秒时间戳
    header = payload.get("header") or {}
    create_ms = header.get("create_time")
    ts = ""
    if create_ms is not None:
        try:
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(
                int(create_ms) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, TypeError):
            pass

    # 操作人姓名（按 USER_MAP 反查兜底）
    event = payload.get("event") or {}
    operator = event.get("operator") or {}
    open_id = (operator.get("open_id") or "").strip()
    raw_user_name = (operator.get("user_name") or "").strip()
    user_name = raw_user_name or (user_map.get(open_id) or {}).get("name", "")
    name = user_name or open_id or "匿名"

    # 按钮
    action_obj = event.get("action") or {}
    value = action_obj.get("value") or {}
    action = (value.get("action") or "").strip()
    alert_id = (value.get("alert_id") or "").strip()
    raw_button_text = (action_obj.get("text") or {}).get("content", "").strip()
    button_text = raw_button_text or action

    return (
        f"{ts}  {name} 按了\"{button_text}\"按钮"
        f"（action={action}, alert_id={alert_id}）"
    )


def mark_event_status(
    event_id: str,
    *,
    status: str = "acked",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """修改一条入站事件的处理状态（封装 Gateway POST /v1/events/{id}/ack）。

    本函数是 feishu_receiver 模块**对外暴露**的"修改事件状态"接口，
    把底层 :func:`agents.channel_gateway_client.ack_event` 包成业务级命名，
    并附加入参校验 + 日志规范。

    Args:
        event_id: 事件 ID（即 :func:`poll_inbound_events` 返回 events[i]["id"]；
                  网关侧也叫 ``evt_xxx``）。
        status: 目标 delivery.status，可选：
            - ``"acked"``   (默认) 处理成功，事件进入"已确认"态，不再被 pending 拉取返回
            - ``"ignored"`` 主动忽略（语义同 acked，但表达"已知晓但选择不处理"）
        details: 透传给网关的审计/上下文 dict（consumer 名、reason、run_id 等），
            网关会写到 ``event.delivery.outcome`` 字段。None 表示不传。

    Returns:
        网关 ACK 响应 dict（解构后的事件最新状态，含 ``delivery.status``）。

    Raises:
        ValueError: event_id 为空，或 status 不在 ``("acked", "ignored")`` 之中。
        GatewayError: 网关业务错误（4xx / 5xx）。
        requests.RequestException: 网络错误。

    Examples:
        >>> from feishu_gateway_cli.feishu_receiver import mark_event_status
        >>> mark_event_status("evt_d654e4f3-13d5-4291-84af-b70f7d798bdb")
        {'event': {...}, 'status': 'acked', ...}

        >>> # 主动忽略并写入审计
        >>> mark_event_status(
        ...     "evt_d654e4f3-...",
        ...     status="ignored",
        ...     details={"consumer": "feishu_receiver", "reason": "duplicate"},
        ... )

    Note:
        配合 :func:`poll_inbound_events` 使用时，建议每次拉取都传
        ``status="pending"``；ACK 后事件会从 pending 集合移除，跨进程零丢失。
    """
    eid = (event_id or "").strip()
    if not eid:
        raise ValueError("event_id 不能为空")
    if status not in ("acked", "ignored"):
        raise ValueError(
            f"status 仅支持 'acked' / 'ignored'，收到 {status!r}"
        )

    log_tag = f"event_id={eid} status={status}"
    logger.info(
        "[p8-notify-receiver] mark_event_status 进入: %s details=%s",
        log_tag, bool(details),
    )
    try:
        result = ack_event(event_id=eid, status=status, details=details)
    except Exception as exc:
        logger.exception(
            "[p8-notify-receiver] mark_event_status 异常: %s err=%s",
            log_tag, exc,
        )
        raise

    logger.info(
        "[p8-notify-receiver] mark_event_status 响应: %s delivery_status=%s",
        log_tag,
        (result.get("event") or {}).get("delivery", {}).get("status", "?"),
    )
    return result


def poll_once(
    *,
    initial_sequence: int = 0,
    account_id: Optional[str] = None,
    group_name: Optional[str] = None,
    person_name: Optional[str] = None,
    channel: str = FEISHU_CHANNEL,
) -> List[Dict[str, Any]]:
    """单次拉取匹配过滤条件的事件。

    本函数是 feishu_receiver 模块**对外暴露**的"单次拉取事件"接口，
    与 :func:`poll_inbound_events`（gateway 客户端）相比，本函数封装了：
    - sequence 自动跳过历史模式（``initial_sequence=-1``）
    - account_id client-side 过滤（gateway /v1/events 不直接支持）
    - group_name + person_name / person_name alone 的群聊 / 单聊 client-side 过滤
      （基于 .env 的 FEISHU_USER_MAP / FEISHU_GROUP_MAP）

    Args:
        initial_sequence: 起始 sequence；``-1`` 时先拉一批 events 取得最大 sequence
            作为起点（跳过历史）；``0`` 或正整数表示从该 sequence 开始拉（断点续传 / 重放）。
        account_id: 可选；非 None 时按 event.accountId client-side 过滤。
        group_name: 可选；与 ``person_name`` 一起表示"群聊过滤"。
        person_name: 可选；单独表示"单聊过滤"；与 ``group_name`` 一起表示"群聊过滤"。
        channel: 按通道过滤，默认 ``"feishu"``。

    Returns:
        匹配过滤条件的事件列表（按 gateway 返回顺序）。无事件返回 ``[]``。

    Raises:
        ValueError: ``group_name`` 传了但 ``person_name`` 没传，或任一参数为空白字符串。

    Examples:
        >>> from feishu_gateway_cli.feishu_receiver import poll_once
        >>> events = poll_once()
        >>> events = poll_once(account_id="default")
        >>> events = poll_once(group_name="应急响应群", person_name="张三")
        >>> events = poll_once(person_name="李四")
    """
    # 校验过滤参数
    if group_name is not None:
        if not group_name.strip():
            raise ValueError("group_name 不能为空字符串")
        if person_name is None:
            raise ValueError(
                "group_name 必须与 person_name 一起使用（群聊过滤需要群名 + 人名）"
            )
    if person_name is not None and not person_name.strip():
        raise ValueError("person_name 不能为空字符串")

    # 2026-08-17：每次轮询循环都打的"进入"日志降到 DEBUG，
    # 避免持续轮询时刷屏（默认 INFO 看不到；排查时开 DEBUG）。
    logger.debug(
        "[p8-notify-receiver] poll_once 进入: initial_sequence=%d "
        "account_id=%s group_name=%s person_name=%s channel=%s",
        initial_sequence, account_id, group_name, person_name, channel,
    )

    # 哨兵：initial_sequence == -1 → 先拉一批拿到最大 sequence 作为起点
    if int(initial_sequence) == INITIAL_SEQUENCE_AUTO:
        try:
            bootstrap = poll_inbound_events(
                after_sequence=0,
                limit=POLL_LIMIT,
                channel=channel,
            )
            seqs = [
                int(ev.get("sequence", 0))
                for ev in (bootstrap.events or [])
            ]
            current = max(seqs) if seqs else 0
            logger.info(
                "[p8-notify-receiver] poll_once 自动模式：跳到最新 sequence=%d "
                "（跳过 %d 条历史）",
                current, len(seqs),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[p8-notify-receiver] poll_once 启动 bootstrap 失败: %s", exc,
            )
            return []
    else:
        current = int(initial_sequence)

    # 拉一批事件
    try:
        polled = poll_inbound_events(
            after_sequence=current,
            limit=POLL_LIMIT,
            channel=channel,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[p8-notify-receiver] poll_once 拉取异常: %s", exc,
        )
        return []

    if not polled.events:
        # 2026-08-17：无新消息时不打印（避免持续轮询时日志噪音）。
        return []

    user_map = _load_user_map()
    group_map = _load_group_map()

    matched: List[Dict[str, Any]] = []
    for ev in polled.events:
        if _matches_filter(
            ev,
            account_id=account_id,
            group_name=group_name,
            person_name=person_name,
            user_map=user_map,
            group_map=group_map,
        ):
            matched.append(ev)

    logger.info(
        "[p8-notify-receiver] poll_once 响应: %d/%d 条事件匹配过滤",
        len(matched), len(polled.events),
    )
    return matched


def poll_and_print(
    *,
    interval: float = DEFAULT_POLL_INTERVAL,
    initial_sequence: int = 0,
    account_id: Optional[str] = None,
    group_name: Optional[str] = None,
    person_name: Optional[str] = None,
    channel: str = FEISHU_CHANNEL,
    do_print: bool = True,
    show_raw: bool = False,
    do_ack: bool = False,
    exit_after: Optional[int] = None,
    once: bool = False,
    on_event: Optional[Callable[[Dict[str, Any], str], Optional[bool]]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> int:
    """持续轮询网关并把每条新事件打印输出。

    默认每条事件打印一行 ``有新消息啦``（+ 可选的 show_raw JSON 块）；
    通过 :func:`format_event` 可以拿到可读字符串，但本函数不再默认打印它。

    Args:
        interval: 空闲时（无新事件）的轮询间隔（秒）。
        initial_sequence: 起始 sequence；传 :data:`INITIAL_SEQUENCE_AUTO`（-1）时
            自动跳过历史（先做一次空拉取拿到最新 sequence，再从那里开始）。
            显式传 0 / 正整数则从该 sequence 开始拉（断点续传 / 重放历史）。
        account_id: 可选；按 event.accountId client-side 过滤（gateway 不直接支持）。
        group_name: 可选；与 person_name 一起使用（群聊过滤）。
        person_name: 可选；单独使用（单聊过滤），与 group_name 一起（群聊过滤）。
        channel: 按通道过滤；默认 "feishu"。
        do_print: 是否打印到 stdout（默认 True → 打印 "有新消息啦"）。
        show_raw: 是否在每事件后追加一次完整 event JSON。
        do_ack: 处理后是否自动 ACK（避免重复投递）。
        exit_after: 收到 N 条事件后退出；None 表示不限。
        once: 只拉一次就退出（用于脚本串行）。
        on_event: 回调 (event, formatted_str) -> Optional[bool]；
                  返回 False 时停止轮询。None 表示无回调。
                  其中 ``formatted_str`` 是固定字符串 ``"有新消息啦"``（兼容旧调用）。

    Returns:
        收到的有效事件条数。

    Raises:
        ValueError: interval <= 0；或 group_name 传了但 person_name 没传。
    """
    if interval <= 0:
        raise ValueError("interval 必须 > 0")
    if group_name is not None and person_name is None:
        raise ValueError(
            "group_name 必须与 person_name 一起使用（群聊过滤需要群名 + 人名）"
        )

    logger.info(
        "[p8-notify-receiver] poll_and_print 进入: interval=%.2f "
        "initial_sequence=%d account_id=%s group_name=%s person_name=%s "
        "channel=%s do_print=%s show_raw=%s do_ack=%s exit_after=%s once=%s",
        interval, initial_sequence, account_id, group_name, person_name,
        channel, do_print, show_raw, do_ack, exit_after, once,
    )

    user_map = _load_user_map()
    group_map = _load_group_map()

    # 哨兵：--initial-sequence == -1 → 自动从"当前最新 sequence"开始，跳过历史。
    # 实现：拉一批 events（limit=POLL_LIMIT），从结果里取最大 sequence 作为起点。
    # 不依赖 channel_gateway_client 解析的 latest_sequence 字段名（网关返回
    # nextSequence / latestSequence 命名不一致；直接从 events[*].sequence 取
    # max 更稳健）。
    if int(initial_sequence) == INITIAL_SEQUENCE_AUTO:
        try:
            bootstrap = poll_inbound_events(
                after_sequence=0,
                limit=POLL_LIMIT,
                channel=channel,
            )
            seqs = [
                int(ev.get("sequence", 0))
                for ev in (bootstrap.events or [])
            ]
            current = max(seqs) if seqs else 0
            logger.info(
                "[p8-notify-receiver] 自动模式：跳到最新 sequence=%d "
                "（跳过 %d 条历史）",
                current, len(seqs),
            )
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)
            else:
                logger.exception(
                    "[p8-notify-receiver] 启动 bootstrap 失败: %s", exc,
                )
            return 0
    else:
        current = int(initial_sequence)
    received = 0
    notification_text = "有新消息啦"

    try:
        while True:
            try:
                events = poll_once(
                    initial_sequence=current,
                    account_id=account_id,
                    group_name=group_name,
                    person_name=person_name,
                    channel=channel,
                )
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    on_error(exc)
                else:
                    logger.exception(
                        "[p8-notify-receiver] poll_and_print 异常: %s", exc,
                    )
                if once:
                    return received
                time.sleep(interval)
                continue

            if events:
                # 推进 sequence 游标（用本批最大 sequence +1，下次只拉更新）
                current = max(
                    int(ev.get("sequence", 0)) for ev in events
                )
                for event in events:
                    if do_print:
                        print(notification_text)
                        if show_raw:
                            try:
                                print(json.dumps(
                                    event, ensure_ascii=False, indent=2,
                                ))
                            except (TypeError, ValueError):
                                print(repr(event))
                    received += 1

                    if do_ack:
                        eid = str(event.get("id") or "")
                        if eid:
                            try:
                                mark_event_status(
                                    event_id=eid,
                                    status="acked",
                                    details={"consumer": "feishu_receiver"},
                                )
                            except GatewayError as exc:
                                logger.warning(
                                    "[p8-notify-receiver] ACK 失败 "
                                    "event_id=%s err=%s", eid, exc,
                                )

                    if on_event is not None:
                        try:
                            keep_going = on_event(event, notification_text)
                        except Exception as exc:  # noqa: BLE001
                            logger.exception(
                                "[p8-notify-receiver] on_event 回调异常: %s",
                                exc,
                            )
                            keep_going = True
                        if keep_going is False:
                            logger.info(
                                "[p8-notify-receiver] on_event 返回 False，停止轮询 "
                                "received=%d",
                                received,
                            )
                            logger.info(
                                "[p8-notify-receiver] poll_and_print 响应: "
                                "received=%d（on_event 中止）",
                                received,
                            )
                            return received

                    if exit_after is not None and received >= exit_after:
                        logger.info(
                            "[p8-notify-receiver] poll_and_print 响应: "
                            "received=%d（达到 --exit-after 上限）",
                            received,
                        )
                        return received

                if once:
                    logger.info(
                        "[p8-notify-receiver] poll_and_print 响应: "
                        "received=%d（--once 完成）",
                        received,
                    )
                    return received
                time.sleep(IDLE_SLEEP)
            else:
                if once:
                    logger.info(
                        "[p8-notify-receiver] poll_and_print 响应: "
                        "received=%d（--once 完成，空拉取）",
                        received,
                    )
                    return received
                time.sleep(interval)
    except KeyboardInterrupt:
        logger.info(
            "[p8-notify-receiver] poll_and_print 响应: received=%d（Ctrl+C）",
            received,
        )
        return received


# ============================================================
# 4. CLI
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feishu_receiver",
        description=(
            "P8 飞书通道适配器（精简版）—— 只收，不拼。"
            "持续轮询 Channel Gateway 并按可读格式打印新事件；"
            "或主动修改事件处理状态。"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ── poll：持续轮询 + 打印 ──────────────────────────────
    p_poll = sub.add_parser(
        "poll",
        help="持续轮询 Channel Gateway 并按可读格式打印新事件",
    )
    p_poll.add_argument(
        "--interval", type=float, default=DEFAULT_POLL_INTERVAL,
        help=f"空闲时轮询间隔（秒），默认 {DEFAULT_POLL_INTERVAL}",
    )
    p_poll.add_argument(
        "--initial-sequence", type=int, default=INITIAL_SEQUENCE_AUTO,
        help=(
            "起始 sequence；默认 -1（自动跳过历史，只接收新事件）。"
            "显式传 0 或具体数字会从该 sequence 开始拉（用于断点续传 / 重放历史）。"
        ),
    )
    p_poll.add_argument(
        "--account-id", default=None,
        help=(
            "按 account_id 过滤（client-side；gateway /v1/events 不直接支持 account_id 参数）。"
            "默认不过滤。"
        ),
    )
    p_poll.add_argument(
        "--channel", default=FEISHU_CHANNEL,
        help=f"按通道过滤，默认 {FEISHU_CHANNEL}",
    )
    p_poll.add_argument(
        "--group-name", default=None,
        help=(
            "群聊名称过滤；必须与 --person-name 一起使用（互斥于单聊过滤）。"
            "从 .env 的 FEISHU_GROUP_MAP 反查群名。"
        ),
    )
    p_poll.add_argument(
        "--person-name", default=None,
        help=(
            "发消息人名过滤：单独使用为单聊过滤，与 --group-name 一起为群聊过滤。"
            "从 .env 的 FEISHU_USER_MAP 反查人名。"
        ),
    )
    p_poll.add_argument(
        "--once", action="store_true",
        help=(
            "只拉取一次就退出（不持续轮询）。"
            "无新消息返回空 / 打印 '无新消息'；"
            "有新消息打印 '有新消息啦' + 完整 event JSON（数组形式）。"
        ),
    )
    p_poll.add_argument(
        "--exit-after", type=int, default=None,
        help="收到 N 条事件后退出（用于测试 / 抽样）",
    )
    p_poll.add_argument(
        "--no-print", action="store_true",
        help="静默模式：轮询但不打印到 stdout（适合后台守护）",
    )
    p_poll.add_argument(
        "--show-raw", action="store_true",
        help="每条事件后追加打印完整 event JSON（调试用；持续模式）",
    )
    p_poll.add_argument(
        "--ack", action="store_true",
        help="收到事件后自动 ACK（默认不 ACK，避免误删未处理消息）",
    )
    p_poll.set_defaults(func=_cli_poll)

    # ── mark：主动修改事件状态 ──────────────────────────────
    p_mark = sub.add_parser(
        "mark",
        help="主动修改一条入站事件的处理状态（acked / ignored）",
        description=(
            "封装 Gateway POST /v1/events/{id}/ack。"
            "常用于脚本里手工补 ACK、或批量 ack 某些已知事件。"
        ),
    )
    p_mark.add_argument(
        "--event-id", required=True,
        help="事件 ID（即 poll_inbound_events 返回 events[i]['id']）",
    )
    p_mark.add_argument(
        "--status", default="acked", choices=["acked", "ignored"],
        help="目标状态：acked (默认) / ignored",
    )
    p_mark.add_argument(
        "--details", default=None,
        help='审计 dict 的 JSON 字符串，例如 \'{"consumer":"feishu_receiver","reason":"duplicate"}\'',
    )
    p_mark.set_defaults(func=_cli_mark)

    return parser


def _cli_poll(args: argparse.Namespace) -> int:
    # 校验过滤参数
    if args.group_name is not None and args.person_name is None:
        print(
            "[FAIL] 参数错误: --group-name 必须与 --person-name 一起使用（群聊过滤需要群名 + 人名）",
            file=sys.stderr,
        )
        return 1

    # ── --once 模式：单次拉取，返回完整 event JSON ──
    if args.once:
        try:
            events = poll_once(
                initial_sequence=args.initial_sequence,
                account_id=args.account_id,
                group_name=args.group_name,
                person_name=args.person_name,
                channel=args.channel,
            )
        except ValueError as exc:
            print(f"[FAIL] 参数错误: {exc}", file=sys.stderr)
            return 1
        except GatewayError as exc:
            print(
                f"[FAIL] Gateway 业务错误 [{exc.code}]: {exc.message}",
                file=sys.stderr,
            )
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        if not events:
            if not args.no_print:
                print("无新消息")
            return 0

        if not args.no_print:
            print("有新消息啦")
            try:
                print(json.dumps(events, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                print(repr(events))
        return 0

    # ── 持续轮询模式 ──
    try:
        poll_and_print(
            interval=args.interval,
            initial_sequence=args.initial_sequence,
            account_id=args.account_id,
            group_name=args.group_name,
            person_name=args.person_name,
            channel=args.channel,
            do_print=not args.no_print,
            show_raw=args.show_raw,
            do_ack=args.ack,
            exit_after=args.exit_after,
            once=False,
        )
    except ValueError as exc:
        print(f"[FAIL] 参数错误: {exc}", file=sys.stderr)
        return 1
    except GatewayError as exc:
        print(
            f"[FAIL] Gateway 业务错误 [{exc.code}]: {exc.message}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


def _cli_mark(args: argparse.Namespace) -> int:
    details: Optional[Dict[str, Any]] = None
    if args.details:
        try:
            parsed = json.loads(args.details)
        except json.JSONDecodeError as exc:
            print(f"[FAIL] --details 不是合法 JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(parsed, dict):
            print("[FAIL] --details 顶层必须是 JSON object", file=sys.stderr)
            return 1
        details = parsed
    try:
        result = mark_event_status(
            event_id=args.event_id,
            status=args.status,
            details=details,
        )
    except ValueError as exc:
        print(f"[FAIL] 参数错误: {exc}", file=sys.stderr)
        return 1
    except GatewayError as exc:
        print(
            f"[FAIL] Gateway 业务错误 [{exc.code}]: {exc.message}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    delivery = (result.get("event") or {}).get("delivery") or {}
    print(
        f"[OK] event_id={args.event_id} status={args.status} "
        f"delivery.status={delivery.get('status', '?')} "
        f"completedAt={delivery.get('completedAt', '?')}"
    )
    return 0


def main(argv: Optional[list] = None) -> int:
    """CLI 入口。

    Usage:
        python -m feishu_gateway_cli.feishu_receiver poll [--interval SEC] ...
        python -m feishu_gateway_cli.feishu_receiver mark --event-id evt_xxx [--status ...] [--details ...]
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 让 Ctrl+C 在 Windows 上也能优雅退出（无 unhandled stack trace）
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (AttributeError, ValueError):
        pass  # 非主线程或 Windows 特殊场景忽略

    return args.func(args)


# ============================================================
# 5. 导出
# ============================================================

__all__ = [
    # 常量
    "FEISHU_USER_MAP_KEY",
    "FEISHU_GROUP_MAP_KEY",
    "FEISHU_CHANNEL",
    "DEFAULT_POLL_INTERVAL",
    "INITIAL_SEQUENCE_AUTO",
    # 公开 API
    "format_event",
    "format_card_callback",
    "poll_once",
    "poll_and_print",
    "mark_event_status",
    "process_card_callback",
    # CLI
    "main",
]


if __name__ == "__main__":
    sys.exit(main())