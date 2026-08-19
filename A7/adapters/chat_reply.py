"""A7/adapters/chat_reply — 飞书侧 → P8 Agent → 飞书侧的回复适配器。

================================================================================
循环伪代码（2026-08-19 修订版）
================================================================================

本模块**只实现单事件处理 chat_reply_handler(event)**；持续轮询由 daemon
调用方负责。完整闭环长这样：

    ┌─────────────────────────────────────────────────────────────────────┐
    │                          daemon 主循环（pull 模式）                  │
    ├─────────────────────────────────────────────────────────────────────┤
    │  while True:                                                        │
    │      try:                                                           │
    │          events = feishu_receiver.poll_once(      # ← pull 入口    │
    │              initial_sequence=current,            #   HTTP GET      │
    │              account_id=account_id,               #   /v1/events    │
    │              channel=channel,                     #   ?status=      │
    │          )                                       #   pending       │
    │      except Exception:                                             │
    │          log; sleep(interval); continue          # 轮询异常不退出  │
    │                                                                     │
    │      if not events:                                                 │
    │          sleep(interval); continue              # 无新消息 → sleep │
    │                                                                     │
    │      ev = events[0]                              # ← 一条一条      │
    │      try:                                       #   不并行、不批量 │
    │          chat_reply_handler(ev)                  #   ← 核心处理    │
    │      except Exception:                                             │
    │          log                                    # handler 异常不退出│
    │                                                                     │
    │      current = max(current, ev.sequence)         # 推进 sequence   │
    │      sleep(interval)                             # 处理完 → sleep  │
    │  # 收到 Ctrl+C → KeyboardInterrupt → 退出                          │
    └─────────────────────────────────────────────────────────────────────┘

chat_reply_handler(event) 单事件处理（按调用顺序）：

    1. 终态防御（双保险）：
       event.delivery.status ∈ {acked, ignored, dead_letter} → return
       （正常路径下 Gateway 已用 status="pending" filter 过滤；此处兜底）

    2. appid 防御（多 app 场景下只处理发给 P8 的消息）：
       event.metadata.appId 与 .env FEISHU_P8_APP_ID 比对 → 不匹配 return
       （多 daemon 各自处理自己 app 的消息；缺失 / 未配置 → 不阻拦）

    3. 校验 event_id 缺失 → log error + return（不 ack，Gateway 兜底）

    4. 解析 [job_id=...] 标记（Bot 模式可空）：
       parse_job_id_marker(text) → (job_id_or_None, user_message)

    5. 构造 HumanMessage：
       build_human_message(job_id, user_message)
       - 有 job_id → "[job_id=XXX] {msg}"
       - 无 job_id → 仅 "{msg}"（Bot 模式）

    6. 调 P8 Agent（disposition_demo）：
       disposition_demo(human_message, history=None)
       → run_disposition_agent(message) → agent.invoke(...)

    7. 回写飞书（reply_to_event）：
       reply_to_event(event_id, text=response, account_id=...)
       （reply 抛异常时仍 ack=acked —— 防同一条消息无限重投）

    8. ACK Gateway（ack_event）：
       正常路径 → ack_event(event_id, status="acked")
       LLM 失败 → ack_event(event_id, status="ignored",
                            details={"reason":"llm_error","error":...})
       + 同时 reply_to_event 发兜底错误消息（含 event_id 方便运维排查）

================================================================================
机制说明：为什么是 pull 而不是 push
================================================================================

当前实现采用 **pull 模式（轮询拉取）**，而非 push 模式（Gateway 主动回调）：

    - pull 模式（当前）：
        daemon 启动 → while True → poll_once → 处理 → sleep → poll_once
        本质是 daemon 主动调 Gateway HTTP /v1/events?status=pending 拉取
        入队事件，按 1.0s 间隔轮询。
        优势：daemon 单进程、无状态、故障自愈（重启从 latest sequence 续）；
        部署简单（只需保证 daemon 进程在跑）。
        Gateway 侧另提供 /v1/events/stream (SSE) 流式端点
        （server.js:195）作为未来 push 升级路径，但 chat_reply 当前未采用。

    - push 模式（未采用）：
        Gateway 在事件入队时主动 POST 到业务端 callbackUrl（config 里可配
        delivery.callbackUrl，本项目目前配 null）。daemon 退化为 HTTP 服务
        接收回调。优势：实时性更好；劣势：需管 webhook 幂等 / 重试 /
        超时，部署复杂度上升。

================================================================================
职责边界
================================================================================

本模块**不**实现以下职责：

    - 持续轮询 / daemon 进程管理   → agents.p8_service_manager / 手动启停
    - 入站事件拉取 / 过滤 / ACK     → feishu_gateway_cli.feishu_receiver
    - 消息拼装 / Card 渲染 / 按钮回调 → feishu_gateway_cli.feishu_sender / feishu_card
    - LLM 内部推理 / 工具调用 / 工作记忆 → agents.p8_disposition_agent

================================================================================
公开 API
================================================================================

    chat_reply_handler(event: Dict[str, Any]) -> None
        处理单条飞书入站事件：终态防御 → appid 防御 → 解析 → 调 LLM → 回写 → ACK。
        异常被捕获并以用户可读的错误消息回写到飞书（不抛到调用方）。

    parse_job_id_marker(text: str) -> Tuple[Optional[str], str]
        从消息正文解析 "[job_id=XXX]" 标记；返回 (job_id_or_None, 清理后正文)。
        job_id 解析失败时返回 (None, 原文本)——Bot 模式下不视为错误。

    build_human_message(job_id: Optional[str], user_message: str) -> str
        按 §7.5 构造完整 HumanMessage 内容：
            - job_id 给定时："[job_id={job_id}] {user_message}"
            - job_id 为空时：仅返回 user_message（不带前缀）

================================================================================
日志规范
================================================================================

入口 / 出口 / 异常按 [chat_reply] 进入 / 响应 / 异常 格式打印。
"""
from __future__ import annotations

import logging
import os
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from agents.channel_gateway_client import (
    GatewayError,
    ack_event,
    reply_to_event,
)
from agents.p8_disposition_agent import disposition_demo

# 2026-08-19：FEISHU_USER_MAP 反查 key（.env 的 JSON 字符串变量名）。
# 数据形态：``{"ou_xxx": {"role": "...", "name": "李宗睿"}}``。
FEISHU_USER_MAP_KEY: str = "FEISHU_USER_MAP"


# ============================================================
# 常量
# ============================================================

# [job_id=XXX] 标记的正则（XXX 不含 ']'，允许 ASCII / 中文 / 数字 / 下划线 / 横线）
_JOB_ID_PATTERN = re.compile(r"^\s*\[job_id=([^\]]+)\]\s*")

# LLM 调用失败时回写给用户的兜底消息（避免暴露内部堆栈）
_LLM_ERROR_FALLBACK = (
    "P8 Agent 调用失败，请稍后重试或联系管理员。"
    "（本次错误已记录到日志，event_id={event_id}）"
)

# 2026-08-19：appid 防御（多 app 场景下只处理发给 P8 的消息）。
#   - Gateway feishu.js:228 把飞书 header.app_id 存到 event.metadata.appId
#   - .env 里 FEISHU_P8_APP_ID 是当前 P8 应用 bot 的 app_id（与
#     openclaw-channel-gateway-standalone/config/config.feishu.local.json 的
#     channels.feishu.accounts.P8.appId 一致）
#   - 缺失 / 不匹配 → 跳过（多 daemon 各自处理自己 app 的消息时，这条消息
#     应当由对应 app 的 daemon 处理，而不是这个 P8 daemon）
# .env 缺失时不报错（视为 "未配置 = 接受所有 app"，向后兼容过渡版）
_P8_APP_ID = os.environ.get("FEISHU_P8_APP_ID", "").strip() or None

# 标识本模块日志的 prefix（避免与 feishu_gateway_cli 混淆）
LOG_TAG = "chat_reply"


# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("A7.chat_reply")
logger.setLevel(logging.INFO)


# ============================================================
# 1. 文本解析（私有 / 公开）
# ============================================================

def parse_job_id_marker(text: str) -> Tuple[Optional[str], str]:
    """从消息正文解析 ``[job_id=XXX]`` 标记。

    只在**消息开头**匹配（带前导空白容忍）。后续出现的 ``[job_id=...]`` 视为
    普通文本（避免误吞用户内嵌的同名字符串）。

    Args:
        text: 飞书消息正文。

    Returns:
        ``(job_id, 清理后正文)``：
            - 解析成功：``(job_id, 原文本去掉前缀 + 首位空白后的剩余部分)``
            - 解析失败：``(None, 原文本)``

    Examples:
        >>> parse_job_id_marker("[job_id=JOB-20260813-001] 为 A6-... 创建处置任务")
        ('JOB-20260813-001', '为 A6-... 创建处置任务')

        >>> parse_job_id_marker("  [job_id=abc] hello")
        ('abc', 'hello')

        >>> parse_job_id_marker("hello world")
        (None, 'hello world')
    """
    raw = (text or "").strip()
    if not raw:
        return None, text or ""

    match = _JOB_ID_PATTERN.match(raw)
    if not match:
        return None, text or ""

    job_id = match.group(1).strip()
    rest = raw[match.end():].lstrip()
    return (job_id or None), rest


def build_human_message(job_id: Optional[str], user_message: str) -> str:
    """按 §7.5 构造完整 HumanMessage 内容。

    Args:
        job_id: 主流程作业 ID；为 None 时不带前缀。
        user_message: 用户原始消息正文（已剥离 [job_id=...] 前缀）。

    Returns:
        完整消息文本。
        - job_id 给定：``"[job_id={job_id}] {user_message}"``
        - job_id 为空：仅 ``user_message``
    """
    clean_user_msg = (user_message or "").strip()
    if job_id and clean_user_msg:
        return f"[job_id={job_id}] {clean_user_msg}"
    if job_id:
        return f"[job_id={job_id}]"
    return clean_user_msg


# ============================================================
# 2. event 字段提取（私有）
# ============================================================

def _extract_text_from_event(event: Dict[str, Any]) -> str:
    """从 event 抽取消息文本。

    优先读取 ``event.message.text``；空缺时回退到 ``event.raw.event.message.content``
    JSON 解析（飞书 webhook 原生 payload 形态）。

    Args:
        event: feishu_receiver.poll_once 返回的入站事件 dict。

    Returns:
        消息文本；缺失返回空字符串。
    """
    msg = event.get("message") or {}
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        return text

    raw = event.get("raw") or {}
    inner = raw.get("event") or {}
    inner_msg = inner.get("message") or {}
    content = inner_msg.get("content")
    if isinstance(content, str):
        try:
            import json as _json
            parsed = _json.loads(content)
            t = parsed.get("text")
            if isinstance(t, str):
                return t
        except (ValueError, TypeError):
            return content
    return ""


def _extract_event_id(event: Dict[str, Any]) -> str:
    """从 event 抽取事件 ID；缺失返回空字符串。"""
    return str(event.get("id") or "")


def _extract_chat_id(event: Dict[str, Any]) -> str:
    """从 event 抽取 chat_id；缺失返回空字符串。"""
    conv = event.get("conversation") or {}
    return str(conv.get("id") or "")


def _extract_account_id(event: Dict[str, Any]) -> str:
    """从 event 抽取 accountId；缺失返回空字符串。"""
    return str(event.get("accountId") or "")


def _extract_sender_open_id(event: Dict[str, Any]) -> Optional[str]:
    """从 event 抽取 sender.open_id（飞书用户 open_id；ou_xxx 格式）。

    飞书事件归一化（Gateway normalize）后必有 ``event.sender.id`` 字段
    （参考 openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_receiver.py
    L399-400）。缺失返回 None —— 调用方按"未知身份"流程处理。

    Args:
        event: chat_reply_handler 入参（feishu_receiver.poll_once 返回值）。

    Returns:
        open_id 字符串（如 ``"ou_511d109b8ac87f972af2d5e67e2c8270"``），
        缺失/为空返回 None。
    """
    sender = event.get("sender") or {}
    open_id = sender.get("id")
    if isinstance(open_id, str) and open_id.strip():
        return open_id.strip()
    if isinstance(open_id, str):
        return None
    return None


def _lookup_sender_info(open_id: Optional[str]) -> Dict[str, str]:
    """按 open_id 从 .env FEISHU_USER_MAP 反查身份（role + name）。

    FEISHU_USER_MAP 形态：``{"ou_xxx": {"role": "...", "name": "李宗睿"}}``。

    **始终返回 dict**（绝不返回 None）—— 调用方可以无脑访问字段，
    简化上游逻辑。命中映射 → 含真实 role/name；未命中 → 含 ``"未识别用户"``
    占位 + ``note`` 字段说明。

    Args:
        open_id: 飞书用户 open_id（来自 ``_extract_sender_open_id``）。

    Returns:
        dict 至少有四个字段：
          - ``role``（str）：中文角色名（命中映射）或"未识别用户"
          - ``name``（str）：中文姓名（命中映射）或"未知"
          - ``open_id``（str）：原 open_id（**始终保留**，便于 LLM 日志追溯）
          - 未命中时额外有 ``note`` 字段说明原因
    """
    base = {
        "role": "未识别用户",
        "name": "未知",
        "open_id": open_id or "",
    }
    if not open_id:
        base["note"] = "event.sender.id 缺失（非飞书事件或网关未归一化）"
        return base

    raw = os.environ.get(FEISHU_USER_MAP_KEY, "").strip()
    if not raw:
        base["note"] = f"{FEISHU_USER_MAP_KEY} 未在 .env 配置"
        return base

    try:
        user_map = json.loads(raw)
    except json.JSONDecodeError:
        base["note"] = f"{FEISHU_USER_MAP_KEY} JSON 解析失败"
        return base

    if not isinstance(user_map, dict):
        base["note"] = f"{FEISHU_USER_MAP_KEY} 顶层不是 JSON object"
        return base

    entry = user_map.get(open_id)
    if not isinstance(entry, dict):
        base["note"] = "open_id 不在 .env FEISHU_USER_MAP 中"
        return base

    role = str(entry.get("role") or "").strip() or "未识别用户"
    name = str(entry.get("name") or "").strip() or "未知"
    base["role"] = role
    base["name"] = name
    base.pop("note", None)  # 命中映射 → 移除 note 字段
    return base


# ============================================================
# 3. 核心回调
# ============================================================

def chat_reply_handler(event: Dict[str, Any]) -> None:
    """处理单条飞书入站事件（§7.5 时序）。

    完整链路：

        1. 校验 event["id"] 存在
        2. 抽取 message.text，解析 [job_id=...] 标记
        3. 构造 HumanMessage 内容 → 调 disposition_demo(message, history)
        4. reply_to_event(event_id, llm_response) 把回复写到原会话
        5. ack_event(event_id, status="acked")

    异常处理：
        - GatewayError（飞书回复 / ACK 失败）→ logger.error 后继续
          （不抛；不阻塞其它事件处理）
        - 其它异常 → logger.exception，回写兜底错误消息给用户

    Args:
        event: feishu_receiver.poll_once 返回的入站事件 dict。
            至少需含 ``id`` / ``message.text``；accountId / conversation.id
            用于 reply_to_event 的回写与身份透传。

    Returns:
        None。所有结果通过飞书回写 + 日志输出；不抛异常给调用方。

    Note:
        - 本函数**不**实现持续轮询；由调用方（脚本 / 服务 / 测试）循环调用。
        - thread_id 命名按 §7.2.1：``f"feishu-{chat_id}"``，留待 P8 重写后接入
          agent.invoke(config={"configurable": {"thread_id": ...}})。
    """
    event_id = _extract_event_id(event)
    chat_id = _extract_chat_id(event)
    account_id = _extract_account_id(event) or None
    raw_text = _extract_text_from_event(event)

    # 2026-08-19：终态防御（双保险；正常路径下 status="pending" filter 已过滤）
    delivery = event.get("delivery") or {}
    delivery_status = delivery.get("status")
    if delivery_status in ("acked", "ignored", "dead_letter"):
        logger.info(
            "[%s] chat_reply_handler 跳过终态 event_id=%s status=%s",
            LOG_TAG, event_id, delivery_status,
        )
        return

    # 2026-08-19：appid 防御（多 app 场景下只处理发给 P8 的消息）。
    #   - 多 daemon 部署时：A daemon 处理 A app，B daemon 处理 B app
    #   - .env 未配置 FEISHU_P8_APP_ID 时跳过校验（向后兼容过渡版）
    #   - 跳过时**不调 ack**（保持 status=pending；让匹配 app 的其他 daemon 能拉到）
    #     → 多 daemon 路由场景下，每 tick 都可能拉到非本 daemon 的消息，
    #       所以这行日志降为 DEBUG（默认不刷屏；排查时开 DEBUG 可见）
    metadata = event.get("metadata") or {}
    event_app_id = metadata.get("appId") or metadata.get("app_id")
    if _P8_APP_ID and event_app_id and event_app_id != _P8_APP_ID:
        logger.debug(
            "[%s] chat_reply_handler 跳过非 P8 app 消息 event_id=%s "
            "event_app_id=%s expected=%s",
            LOG_TAG, event_id, event_app_id, _P8_APP_ID,
        )
        return

    logger.info(
        "[%s] chat_reply_handler 进入: event_id=%s chat_id=%s account_id=%s text_len=%d",
        LOG_TAG, event_id or "<missing>", chat_id or "<missing>",
        account_id or "<default>", len(raw_text or ""),
    )

    # ===== 校验：event_id 缺失 =====
    if not event_id:
        logger.error(
            "[%s] chat_reply_handler 异常: event['id'] 缺失，无法回写 / ACK。event=%s",
            LOG_TAG, _truncate(event),
        )
        return

    # ===== 解析 [job_id=...]（Bot 模式：可选；解析失败不视为错误）=====
    job_id, user_message = parse_job_id_marker(raw_text)
    if job_id:
        logger.info(
            "[%s] chat_reply_handler 绑定 job_id: event_id=%s job_id=%s",
            LOG_TAG, event_id, job_id,
        )
    else:
        logger.info(
            "[%s] chat_reply_handler Bot 模式（无 [job_id=...] 前缀）: event_id=%s",
            LOG_TAG, event_id,
        )

    # ===== 构造 HumanMessage 内容（按 §7.5）=====
    human_message = build_human_message(job_id, user_message)

    # 2026-08-19：构造 user_ctx（发消息人身份），注入 LLM system_prompt。
    # 始终返回 dict（即使未识别也含占位 + note）—— 避免上游 None 分支。
    sender_open_id = _extract_sender_open_id(event)
    user_ctx = _lookup_sender_info(sender_open_id)
    logger.info(
        "[%s] chat_reply_handler user_ctx: event_id=%s open_id=%s role=%s name=%s identified=%s",
        LOG_TAG, event_id, user_ctx.get("open_id") or "<missing>",
        user_ctx["role"], user_ctx["name"],
        "note" not in user_ctx,
    )
    if not human_message:
        logger.warning(
            "[%s] chat_reply_handler 用户消息为空: event_id=%s job_id=%s",
            LOG_TAG, event_id, job_id,
        )
        try:
            reply_to_event(
                event_id=event_id,
                text="消息正文为空，请补充内容后重发。",
                account_id=account_id,
            )
            ack_event(event_id=event_id, status="acked")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[%s] chat_reply_handler 回写异常: event_id=%s err=%s",
                LOG_TAG, event_id, exc,
            )
        return

    # ===== 调 LLM（disposition_demo = P8 独立对话模式统一入口）=====
    try:
        llm_response = disposition_demo(human_message, history=None, user_ctx=user_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[%s] chat_reply_handler LLM 调用异常: event_id=%s job_id=%s err=%s",
            LOG_TAG, event_id, job_id, exc,
        )
        # 失败路径（2026-08-19 改造）：发兜底错误消息 + ack=ignored
        # 不重试：避免同一条坏消息反复触发
        try:
            fallback_text = _LLM_ERROR_FALLBACK.format(event_id=event_id)
            reply_to_event(
                event_id=event_id,
                text=fallback_text,
                account_id=account_id,
            )
        except Exception as inner_exc:  # noqa: BLE001
            logger.exception(
                "[%s] chat_reply_handler 兜底回写失败: event_id=%s err=%s",
                LOG_TAG, event_id, inner_exc,
            )
        try:
            ack_event(
                event_id=event_id,
                status="ignored",
                details={
                    "reason": "llm_error",
                    "error": str(exc)[:200],
                },
            )
        except Exception as inner_exc:  # noqa: BLE001
            logger.exception(
                "[%s] chat_reply_handler ack=ignored 失败: event_id=%s err=%s",
                LOG_TAG, event_id, inner_exc,
            )
        return

    # ===== LLM 输出为空 → 兜底提示 =====
    response_text = (llm_response or "").strip()
    if not response_text:
        logger.warning(
            "[%s] chat_reply_handler LLM 输出为空: event_id=%s job_id=%s",
            LOG_TAG, event_id, job_id,
        )
        response_text = "P8 Agent 已处理（无文本回复）。"

    # ===== 回写到原飞书会话 =====
    try:
        reply_to_event(
            event_id=event_id,
            text=response_text,
            account_id=account_id,
        )
    except GatewayError as exc:
        logger.error(
            "[%s] chat_reply_handler reply_to_event 失败: event_id=%s err=%s",
            LOG_TAG, event_id, exc,
        )
        # 回写失败时仍 ACK（避免无限重投；用户可手动重发）
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[%s] chat_reply_handler reply_to_event 异常: event_id=%s err=%s",
            LOG_TAG, event_id, exc,
        )

    # ===== ACK（标记事件已处理）=====
    try:
        ack_event(event_id=event_id, status="acked")
    except GatewayError as exc:
        logger.error(
            "[%s] chat_reply_handler ack_event 失败: event_id=%s err=%s",
            LOG_TAG, event_id, exc,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[%s] chat_reply_handler ack_event 异常: event_id=%s err=%s",
            LOG_TAG, event_id, exc,
        )

    logger.info(
        "[%s] chat_reply_handler 响应: event_id=%s job_id=%s chat_id=%s "
        "response_len=%d",
        LOG_TAG, event_id, job_id, chat_id or "<missing>", len(response_text),
    )


# ============================================================
# 4. 持续轮询包装（可选便捷入口）
# ============================================================

def run_chat_reply_loop(
    *,
    interval: float = 1.0,
    account_id: Optional[str] = None,
    channel: str = "feishu",
    initial_sequence: int = -1,
) -> None:
    """持续轮询 + chat_reply_handler 调度（便捷入口；非生产 daemon）。

    本函数是单进程阻塞循环，**适合开发调试 / 临时人工测试**。生产场景建议
    部署为独立服务（自行实现 supervisor / 健康检查 / 优雅退出）。

    Args:
        interval: 空闲时轮询间隔（秒）。
        account_id: 可选；按 Gateway accountId 过滤（client-side）。
        channel: 网关通道名，默认 ``"feishu"``。
        initial_sequence: -1 表示自动从最新 sequence 开始（跳过历史）。

    Note:
        调用前需确保：
            1. Channel Gateway 在 127.0.0.1:8787 运行（``start_gateway start`）
            2. 项目根 .env 已配置 CG_API_KEY / FEISHU_APP_ID 等
            3. P8 disposition_demo 可正常调起（agents.p8_disposition_agent 路径正确）
    """
    import time as _time

    from feishu_gateway_cli.feishu_receiver import poll_once

    logger.info(
        "[%s] run_chat_reply_loop 启动: interval=%.2f account_id=%s channel=%s "
        "initial_sequence=%d walltime=%s",
        LOG_TAG, interval, account_id or "<default>", channel,
        initial_sequence, datetime.now(timezone.utc).isoformat(),
    )

    current = int(initial_sequence)
    if current == -1:
        # 自动跳过历史：先拉一批拿到最大 sequence 作为起点
        bootstrap = poll_once(
            initial_sequence=0,
            account_id=account_id,
            channel=channel,
        )
        if bootstrap:
            current = max(
                int(ev.get("sequence", 0)) for ev in bootstrap
            )
            logger.info(
                "[%s] run_chat_reply_loop 自动模式：跳到最新 sequence=%d",
                LOG_TAG, current,
            )
        else:
            current = 0

    try:
        while True:
            try:
                events = poll_once(
                    initial_sequence=current,
                    account_id=account_id,
                    channel=channel,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[%s] run_chat_reply_loop 轮询异常: %s", LOG_TAG, exc,
                )
                _time.sleep(interval)
                continue

            if not events:
                _time.sleep(interval)
                continue

            # 2026-08-19：一条一条处理（不并行、不批量）
            # poll_once 返回的 events 数组（最多 POLL_LIMIT=100）只取第一条；
            # 剩余 batch 留到下次循环自然推进，规避"一次拉到 N 条连续压垮 LLM / 飞书"。
            ev = events[0]
            try:
                chat_reply_handler(ev)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[%s] chat_reply_handler 未捕获异常: event_id=%s err=%s",
                    LOG_TAG, ev.get("id"), exc,
                )

            current = max(current, int(ev.get("sequence", 0)))
            _time.sleep(interval)  # 每条处理完后 sleep，避免连续压垮下游
    except KeyboardInterrupt:
        logger.info("[%s] run_chat_reply_loop 收到 KeyboardInterrupt，退出", LOG_TAG)


# ============================================================
# 5. 内部辅助
# ============================================================

def _truncate(obj: Any, limit: int = 600) -> str:
    """把对象转成截断后的字符串，便于日志输出。"""
    try:
        import json as _json
        s = _json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(obj)
    if len(s) > limit:
        return s[:limit] + f"...(+{len(s) - limit} chars)"
    return s


# ============================================================
# 6. CLI（演示用；本模块无 CLI 主路径）
# ============================================================

def _cli_run(args: Any) -> int:
    """``python -m A7.adapters.chat_reply run`` 持续轮询入口。"""
    run_chat_reply_loop(
        interval=args.interval,
        account_id=args.account_id,
        channel=args.channel,
        initial_sequence=args.initial_sequence,
    )
    return 0


def _build_parser() -> Any:
    """CLI 解析器。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="chat_reply",
        description=(
            "A7/adapters/chat_reply —— 飞书侧 → P8 Agent → 飞书侧的回复适配器。"
            "持续轮询 Channel Gateway 入站事件，调 disposition_demo 处理，"
            "把回复回写到原会话。"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run",
        help="持续轮询并把每条新事件送入 chat_reply_handler（Ctrl+C 停止）",
    )
    p_run.add_argument(
        "--interval", type=float, default=1.0,
        help="空闲时轮询间隔（秒），默认 1.0",
    )
    p_run.add_argument(
        "--account-id", default=None,
        help="按 accountId 过滤（client-side；不传则不过滤）",
    )
    p_run.add_argument(
        "--channel", default="feishu",
        help="网关通道名，默认 feishu",
    )
    p_run.add_argument(
        "--initial-sequence", type=int, default=-1,
        help=(
                "起始 sequence；默认 -1（自动跳过历史，只接新事件）。"
                "显式传 0 / 正整数则从该 sequence 开始拉（断点续传 / 重放）。"
            ),
    )
    p_run.set_defaults(func=_cli_run)

    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI 入口（演示用）。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


# ============================================================
# 7. 导出
# ============================================================

__all__ = [
    # 公开 API
    "parse_job_id_marker",
    "build_human_message",
    "chat_reply_handler",
    "run_chat_reply_loop",
    # CLI
    "main",
]


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())