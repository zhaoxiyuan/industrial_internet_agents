"""
Channel Gateway 客户端
========================

封装 OpenClaw Channel Gateway Standalone（默认监听 127.0.0.1:8787）的
REST 接口，提供三个核心能力：

1. **主动发消息** :func:`send_message`
   包装 ``POST /v1/messages/send``，可指定通道、账号、接收方（conversation_id + receive_id_type）
   和文本内容。支持 ``Idempotency-Key`` 自动重放保护。

2. **回复入站事件** :func:`reply_to_event`
   包装 ``POST /v1/messages/reply``，根据入站 ``event_id`` 自动继承原通道/会话，
   也可显式覆盖 channel / accountId / conversation_id / reply_to_id。

3. **轮询收消息** :func:`poll_inbound_events` / :func:`iter_inbound_events`
   包装 ``GET /v1/events?after_sequence=...``，按单调递增的 ``sequence``
   拉取网关持久化的入站事件，天然支持断点续传。

事件处理完成后应调用 :func:`ack_event` 通知网关，避免重复投递。

------------------------------------------------------------------------
快速开始
------------------------------------------------------------------------

.. code-block:: python

    from agents.channel_gateway_client import (
        send_message,
        reply_to_event,
        poll_inbound_events,
        ack_event,
    )

    # 主动发一条飞书群消息
    result = send_message(
        channel="feishu",
        account_id="default",
        to={"conversation_id": "oc_xxx", "receive_id_type": "chat_id"},
        text="告警：A5 监测到 XX 风险",
        idempotency_key="job-20260813-001",
    )

    # 轮询入站事件并回复
    events, latest_seq = poll_inbound_events(after_sequence=0, limit=100)
    for event in events:
        text = event.get("message", {}).get("text", "")
        reply_to_event(event_id=event["id"], text=f"收到: {text}")
        ack_event(event_id=event["id"], status="acked")

------------------------------------------------------------------------
环境变量
------------------------------------------------------------------------

从 **项目根目录** ``.env`` 自动加载（与 A5/A6 一致）：

- ``GATEWAY_HOST``              网关地址，默认 ``http://127.0.0.1:8787``
- ``CG_API_KEY`` / ``CHANNEL_GATEWAY_API_KEY`` 网关 REST API Key
- ``CG_DEFAULT_CHANNEL``        主动发送 / 默认回复的通道名，默认 ``"feishu"``
- ``CG_DEFAULT_ACCOUNT_ID``     主动发送 / 默认回复的账号 ID，默认 ``"default"``。
    必须与 ``openclaw-channel-gateway-standalone/config/*.json`` 里 channels.<channel>.accounts
    注册的 key 一致；不一致会触发 ``CHANNEL_ACCOUNT_NOT_FOUND`` (HTTP 404)
- ``FEISHU_APP_ID``          可选，飞书 App ID（仅在直接调用飞书 API 时使用）
- ``FEISHU_APP_SECRET``      可选，飞书 App Secret
- ``FEISHU_DOMAIN``          可选，``feishu`` (中国站) / ``lark`` (国际版)

依赖：``pip install python-dotenv requests``
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Union

import requests
from dotenv import load_dotenv


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
logger = logging.getLogger("channel_gateway_client")
logger.setLevel(logging.INFO)


# ============================================================
# 1. 环境变量加载（与 A5/A6 一致，统一从项目根 .env 读取）
# ============================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)
else:
    # 没有 .env 也不抛错——调用方可以显式传入配置
    load_dotenv(override=False)


# ============================================================
# 2. 配置与数据类
# ============================================================

@dataclass
class GatewayConfig:
    """Channel Gateway 连接配置。

    Attributes:
        host: 网关地址，例如 ``http://127.0.0.1:8787``。
        api_key: REST API Key。网关除 ``/healthz``、``/readyz`` 与 ``/webhooks/*``
            外，所有 ``/v1/*`` 都需要 ``Authorization: Bearer <api_key>``。
        default_channel: 主动发送 / 默认回复使用的通道，例如 ``feishu`` / ``loopback``。
        default_account_id: 默认账号 ID，未指定时为 ``default``。
        timeout: HTTP 请求超时（秒）。
    """

    host: str = "http://127.0.0.1:8787"
    api_key: str = ""
    default_channel: str = "feishu"
    default_account_id: str = "default"
    timeout: float = 15.0

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """从环境变量构造配置。

        兼容两套 key 命名：
        - ``GATEWAY_HOST`` / ``CG_API_KEY``（项目根 ``.env``）
        - ``CHANNEL_GATEWAY_HOST`` / ``CHANNEL_GATEWAY_API_KEY``（网关启动脚本）
        """
        host = (
            os.getenv("GATEWAY_HOST")
            or os.getenv("CHANNEL_GATEWAY_HOST")
            or "http://127.0.0.1:8787"
        )
        api_key = (
            os.getenv("CG_API_KEY")
            or os.getenv("CHANNEL_GATEWAY_API_KEY")
            or ""
        )
        return cls(
            host=host.rstrip("/"),
            api_key=api_key,
            default_channel=os.getenv("CG_DEFAULT_CHANNEL", "feishu"),
            default_account_id=os.getenv("CG_DEFAULT_ACCOUNT_ID", "default"),
            timeout=float(os.getenv("CG_TIMEOUT", "15")),
        )


@dataclass
class SendMessageResult:
    """发送消息的解析结果。"""

    intent_id: Optional[str] = None
    status: str = "unknown"          # sent | sending | failed | unknown
    idempotency_key: Optional[str] = None
    receipt_id: Optional[str] = None
    platform_message_id: Optional[str] = None
    evidence: Optional[str] = None
    replayed: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PollResult:
    """轮询入站事件的解析结果。"""

    events: List[Dict[str, Any]] = field(default_factory=list)
    latest_sequence: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 3. Channel Gateway REST 客户端
# ============================================================

class ChannelGatewayClient:
    """Channel Gateway 同步 REST 客户端。

    设计原则：
    - 单一职责：仅负责 HTTP 调用与结果解析，不做业务路由；
    - 异常透明：网络错误抛出 ``requests.RequestException``，业务错误抛出 ``GatewayError``；
    - 线程安全：复用 ``requests.Session``，每次调用生成新 ``X-Request-Id``。
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.from_env()
        if not self.config.api_key:
            logger.warning(
                "CG_API_KEY 未配置。调用受保护接口（/v1/*）将返回 401。"
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "industrial-internet-agents/1.0 (channel-gateway-client)",
        })

    # ---------- 通用请求方法 ----------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        url = f"{self.config.host}{path}"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        headers["X-Request-Id"] = uuid.uuid4().hex
        if extra_headers:
            headers.update(extra_headers)

        # 2026-08-17：高频轮询路径（GET /v1/events）默认走 DEBUG，
        # 避免持续轮询时无新消息也刷屏。其它一次性接口（send/reply/ack）保持 INFO。
        is_poll_path = method == "GET" and path == "/v1/events"
        entry_log = logger.debug if is_poll_path else logger.info
        entry_log(
            "[%s] %s 进入: %s params=%s",
            method, path, url, _truncate(params),
        )
        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout or self.config.timeout,
            )
        except requests.RequestException as exc:
            logger.exception("[%s] %s 异常: %s", method, path, exc)
            raise

        # 业务错误统一抛 GatewayError
        if response.status_code >= 400:
            logger.error(
                "[%s] %s 响应: status=%s body=%s",
                method, path, response.status_code, _truncate(response.text),
            )
            raise GatewayError.from_response(response)

        try:
            data = response.json()
        except ValueError:
            # SSE/纯文本等场景下不应走到这里；这里做兜底
            data = {"raw": response.text}

        entry_log(
            "[%s] %s 响应: status=%s body=%s",
            method, path, response.status_code, _truncate(data),
        )
        return data if isinstance(data, dict) else {"data": data}

    # ---------- 健康检查 ----------

    def health(self) -> Dict[str, Any]:
        """``GET /healthz`` — 进程存活检查。"""
        url = f"{self.config.host}/healthz"
        try:
            r = self._session.get(url, timeout=self.config.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            logger.exception("health 检查失败: %s", exc)
            raise

    def ready(self) -> Dict[str, Any]:
        """``GET /readyz`` — 状态存储就绪检查。"""
        return self._request("GET", "/readyz")

    # ---------- 主动发消息 ----------

    def send_message(
        self,
        text: str = "",
        *,
        channel: Optional[str] = None,
        account_id: Optional[str] = None,
        to: Optional[Dict[str, Any]] = None,
        receive_id: Optional[str] = None,
        receive_id_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
        msg_type: str = "text",
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> SendMessageResult:
        """主动发送一条消息。

        文档：``POST /v1/messages/send``（详见 ``openapi/openapi.yaml``）

        Args:
            text: 消息文本内容（默认空；当 ``content`` 提供时可省）。
            channel: 目标通道，默认 ``GatewayConfig.default_channel``。
            account_id: 目标账号 ID，默认 ``default``。
            to: 完整 to 结构 ``{"conversation_id": "...", "receive_id_type": "..."}``，
                显式传入时优先级最高。
            receive_id / receive_id_type / conversation_id: ``to`` 的拆分写法。
                三个参数至少需要提供 ``conversation_id``（飞书 API 中即 receive_id）。
            msg_type: 消息类型，默认 ``"text"``。支持 ``"text"`` /
                ``"interactive"``（飞书卡片）。仅当网关后端支持该类型时生效。
            content: 自定义 content 字符串（如卡片的 JSON 字符串）；
                提供时网关应原样转发到飞书 Open API 的 ``content`` 字段。
                ``msg_type="interactive"`` 时必传 ``content``。
            metadata: 可选透传字段。
            idempotency_key: 幂等键，建议传业务唯一键；网关对相同键返回原结果。
                也可通过 ``Idempotency-Key`` HTTP 头传入。

        Returns:
            :class:`SendMessageResult`：发送意图与回执摘要。

        Raises:
            GatewayError: 网关返回 4xx/5xx 时。
            requests.RequestException: 网络错误时。
        """
        if not text and not content:
            raise ValueError("text 或 content 必须传其一")

        to_payload = dict(to) if to else {}
        if receive_id and "receive_id" not in to_payload:
            to_payload["receive_id"] = receive_id
        if receive_id_type and "receive_id_type" not in to_payload:
            to_payload["receive_id_type"] = receive_id_type
        if conversation_id and "conversation_id" not in to_payload:
            # 飞书 receive_id 既可能是 open_id 也可能是 chat_id
            to_payload["conversation_id"] = conversation_id
        if "receive_id_type" not in to_payload:
            to_payload["receive_id_type"] = "chat_id"

        body: Dict[str, Any] = {
            "channel": channel or self.config.default_channel,
            "account_id": account_id or self.config.default_account_id,
            "to": to_payload,
            "text": text,
            "msg_type": msg_type,
        }
        if content is not None:
            body["content"] = content
        if metadata:
            body["metadata"] = metadata

        extra_headers: Dict[str, str] = {}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key
            body["idempotency_key"] = idempotency_key

        data = self._request(
            "POST", "/v1/messages/send",
            json_body=body,
            extra_headers=extra_headers,
            timeout=timeout,
        )
        return self._parse_send_result(data)

    @staticmethod
    def _parse_send_result(data: Dict[str, Any]) -> SendMessageResult:
        intent = data.get("intent") or {}
        receipt = data.get("receipt") or {}
        return SendMessageResult(
            intent_id=intent.get("id"),
            status=intent.get("status", "unknown"),
            idempotency_key=intent.get("idempotencyKey") or intent.get("idempotency_key"),
            receipt_id=intent.get("receiptId") or intent.get("receipt_id") or receipt.get("id"),
            platform_message_id=receipt.get("platformMessageId") or receipt.get("platform_message_id"),
            evidence=receipt.get("evidence"),
            replayed=bool(data.get("replayed", False)),
            raw=data,
        )

    # ---------- 回复入站事件 ----------

    def reply_to_event(
        self,
        *,
        event_id: str,
        text: str,
        msg_type: Optional[str] = None,
        content: Optional[str] = None,
        channel: Optional[str] = None,
        account_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        receive_id_type: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> SendMessageResult:
        """回复一条入站事件。

        默认继承原事件的 channel / accountId / conversation.id /
        message.id（作为 replyToId）/ conversation.threadId，
        可通过参数显式覆盖。

        Args:
            event_id: 入站事件的 ``id``（``evt_xxx``）。
            text: 回复文本。
            msg_type: 消息类型（``"text"`` / ``"interactive"`` / ``"post"``）。
                2026-08-19：新增，让 chat_reply 适配层可发送飞书 interactive 卡片，
                解决 LLM 输出的 markdown 在 text 类型下被剥成纯文本的问题。
                仅当网关后端支持时生效。
            content: 自定义 content 字符串（如飞书卡片的 JSON 字符串）。
                ``msg_type="interactive"`` 时必传。
                网关应原样转发到飞书 Open API 的 ``content`` 字段。
            其余参数：覆盖默认值。
        """
        if not event_id:
            raise ValueError("event_id 不能为空")
        if not text:
            raise ValueError("text 不能为空")
        # 2026-08-19：msg_type + content 联动校验。
        # 飞书 interactive 卡片必须带 content（card JSON 字符串）；否则网关会回退到 text。
        if msg_type and msg_type != "text" and not content:
            raise ValueError(
                f"msg_type={msg_type} 时必须传 content（飞书卡片 JSON 字符串）"
            )

        body: Dict[str, Any] = {"event_id": event_id, "text": text}
        if msg_type:
            body["msg_type"] = msg_type
        if content is not None:
            body["content"] = content
        if channel:
            body["channel"] = channel
        if account_id:
            body["accountId"] = account_id
        if conversation_id:
            body["conversationId"] = conversation_id
        if receive_id_type:
            body["receiveIdType"] = receive_id_type
        if reply_to_id:
            body["replyToId"] = reply_to_id
        if thread_id:
            body["threadId"] = thread_id
        if metadata:
            body["metadata"] = metadata

        extra_headers: Dict[str, str] = {}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key
            body["idempotency_key"] = idempotency_key

        data = self._request(
            "POST", "/v1/messages/reply",
            json_body=body,
            extra_headers=extra_headers,
            timeout=timeout,
        )
        return self._parse_send_result(data)

    # ---------- 轮询收消息 ----------

    def poll_inbound_events(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> PollResult:
        """单次拉取入站事件（同步轮询）。

        Args:
            after_sequence: 只返回 ``sequence > after_sequence`` 的事件。首次调用传 0。
            limit: 1–1000，超过会被网关截断。
            status: 可选，按 ``delivery.status`` 过滤（pending / processing / acked / dead_letter）。
            channel: 可选，按通道过滤。
            session_key: 可选，按网关统一会话键过滤（``cg:v1:<channel>:<account>:...``）。

        Returns:
            :class:`PollResult`，含 ``events`` 与 ``latest_sequence``。
        """
        params: Dict[str, Any] = {
            "after_sequence": int(after_sequence),
            "limit": max(1, min(int(limit), 1000)),
        }
        if status:
            params["status"] = status
        if channel:
            params["channel"] = channel
        if session_key:
            params["session_key"] = session_key

        data = self._request("GET", "/v1/events", params=params, timeout=timeout)
        events = data.get("events") or []
        latest_seq = data.get("latestSequence", after_sequence)
        return PollResult(
            events=events,
            latest_sequence=int(latest_seq or after_sequence),
            raw=data,
        )

    def iter_inbound_events(
        self,
        *,
        initial_sequence: int = 0,
        poll_interval: float = 1.0,
        idle_sleep: float = 0.5,
        limit: int = 100,
        stop_on_empty: bool = False,
        max_iterations: Optional[int] = None,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """持续轮询入站事件的生成器。

        每次拉取后立刻 ``yield`` 事件（先 yield、再 sleep），便于上层直接消费。
        当 ``stop_on_empty=True`` 时，连续一次空轮询即退出。

        Args:
            initial_sequence: 起始 ``sequence``。
            poll_interval: 空闲时（无事件）的轮询间隔（秒）。
            idle_sleep: 收到事件后的退避间隔（秒），避免 busy loop。
            limit: 每次拉取上限。
            stop_on_empty: 空轮询一次即停止（用于单元测试 / 一次性消费）。
            max_iterations: 最大迭代次数，超过后停止（防止无限循环）。
            channel / session_key: 透传给 :meth:`poll_inbound_events`。
            on_event: 每条事件的回调；先于 ``yield`` 调用。
            on_error: 错误回调；默认打印到日志。
        """
        current = int(initial_sequence)
        iterations = 0
        while True:
            iterations += 1
            try:
                result = self.poll_inbound_events(
                    after_sequence=current,
                    limit=limit,
                    channel=channel,
                    session_key=session_key,
                )
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    on_error(exc)
                else:
                    logger.exception("轮询入站事件失败: %s", exc)
                if stop_on_empty:
                    return
                time.sleep(poll_interval)
                continue

            events = result.events
            if events:
                # latestSequence 是网关视角的最大序号，下一次轮询用 latestSequence
                current = max(result.latest_sequence, current)
                for event in events:
                    if on_event:
                        try:
                            on_event(event)
                        except Exception as exc:  # noqa: BLE001
                            logger.exception("on_event 回调异常: %s", exc)
                    yield event
                if stop_on_empty:
                    return
                time.sleep(idle_sleep)
            else:
                if stop_on_empty:
                    return
                time.sleep(poll_interval)

            if max_iterations is not None and iterations >= max_iterations:
                return

    def ack_event(
        self,
        *,
        event_id: str,
        status: str = "acked",
        details: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """ACK 一条入站事件，避免重复投递。

        Args:
            event_id: 入站事件 ID。
            status: ``acked`` 或 ``ignored``。
            details: 可选审计信息（consumer / run_id 等）。
        """
        if status not in ("acked", "ignored"):
            raise ValueError("status 仅支持 'acked' 或 'ignored'")
        body: Dict[str, Any] = {"status": status}
        if details:
            body["details"] = details
        return self._request(
            "POST", f"/v1/events/{event_id}/ack",
            json_body=body, timeout=timeout,
        )

    def retry_event(
        self,
        *,
        event_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """把事件重新置回 pending，立即允许处理。"""
        return self._request(
            "POST", f"/v1/events/{event_id}/retry", timeout=timeout,
        )

    # ---------- 出站意图 / 回执查询 ----------

    def list_outbound_intents(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """``GET /v1/outbound/intents``。"""
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return self._request("GET", "/v1/outbound/intents", params=params, timeout=timeout)

    def list_receipts(
        self,
        *,
        limit: int = 100,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """``GET /v1/receipts``。"""
        return self._request(
            "GET", "/v1/receipts",
            params={"limit": limit}, timeout=timeout,
        )

    def list_dead_letters(
        self,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """``GET /v1/dead-letters``。"""
        return self._request("GET", "/v1/dead-letters", timeout=timeout)

    def close(self) -> None:
        """关闭底层 ``requests.Session``。"""
        self._session.close()


# ============================================================
# 4. 异常类型
# ============================================================

class GatewayError(Exception):
    """Channel Gateway 业务错误。

    网关返回的错误体结构：
    ``{"error": {"code": "...", "message": "...", "retryable": bool, "ambiguous": bool, ...}}``
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        request_id: Optional[str] = None,
        retryable: bool = False,
        ambiguous: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(f"[{code}] {message} (status={status_code})")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.details = details or {}

    @classmethod
    def from_response(cls, response: requests.Response) -> "GatewayError":
        request_id = response.headers.get("X-Request-Id")
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        err = payload.get("error") or {}
        return cls(
            code=err.get("code") or f"HTTP_{response.status_code}",
            message=err.get("message") or response.text or "unknown error",
            status_code=response.status_code,
            request_id=err.get("request_id") or request_id,
            retryable=bool(err.get("retryable", False)),
            ambiguous=bool(err.get("ambiguous", False)),
            details=err.get("details") or {},
        )


# ============================================================
# 5. 模块级便捷方法（共享默认客户端）
# ============================================================

_default_client_lock = threading.Lock()
_default_client: Optional[ChannelGatewayClient] = None


def get_default_client() -> ChannelGatewayClient:
    """获取（或懒加载）默认客户端。

    默认客户端基于 ``GatewayConfig.from_env()``，整个进程共享一份。
    """
    global _default_client
    if _default_client is None:
        with _default_client_lock:
            if _default_client is None:
                _default_client = ChannelGatewayClient()
    return _default_client


def configure_default_client(config: GatewayConfig) -> ChannelGatewayClient:
    """替换默认客户端（用于测试或指定非默认网关）。"""
    global _default_client
    with _default_client_lock:
        if _default_client is not None:
            _default_client.close()
        _default_client = ChannelGatewayClient(config)
    return _default_client


def send_message(
    text: str = "",
    *,
    channel: Optional[str] = None,
    account_id: Optional[str] = None,
    to: Optional[Dict[str, Any]] = None,
    receive_id: Optional[str] = None,
    receive_id_type: Optional[str] = None,
    conversation_id: Optional[str] = None,
    msg_type: str = "text",
    content: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> SendMessageResult:
    """便捷封装：使用默认客户端主动发消息。

    与 :meth:`ChannelGatewayClient.send_message` 同签名；额外接受
    ``msg_type``（默认 ``"text"``；卡片场景传 ``"interactive"``）和
    ``content``（卡片场景传 ``json.dumps(card, ensure_ascii=False)``）。
    """
    return get_default_client().send_message(
        text=text,
        channel=channel,
        account_id=account_id,
        to=to,
        receive_id=receive_id,
        receive_id_type=receive_id_type,
        conversation_id=conversation_id,
        msg_type=msg_type,
        content=content,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )


def reply_to_event(
    event_id: str,
    text: str,
    *,
    msg_type: Optional[str] = None,
    content: Optional[str] = None,
    channel: Optional[str] = None,
    account_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    receive_id_type: Optional[str] = None,
    reply_to_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> SendMessageResult:
    """便捷封装：使用默认客户端回复入站事件。

    2026-08-19 修复：模块级便捷函数原来漏传 ``msg_type`` / ``content``，
    导致 chat_reply.py import 后调用报
    ``reply_to_event() got an unexpected keyword argument 'msg_type'``。
    补全这两个参数 + 透传给实例方法。
    """
    return get_default_client().reply_to_event(
        event_id=event_id,
        text=text,
        msg_type=msg_type,
        content=content,
        channel=channel,
        account_id=account_id,
        conversation_id=conversation_id,
        receive_id_type=receive_id_type,
        reply_to_id=reply_to_id,
        thread_id=thread_id,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )


def poll_inbound_events(
    *,
    after_sequence: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
) -> PollResult:
    """便捷封装：使用默认客户端轮询入站事件。"""
    return get_default_client().poll_inbound_events(
        after_sequence=after_sequence,
        limit=limit,
        status=status,
        channel=channel,
        session_key=session_key,
    )


def iter_inbound_events(
    *,
    initial_sequence: int = 0,
    poll_interval: float = 1.0,
    idle_sleep: float = 0.5,
    limit: int = 100,
    stop_on_empty: bool = False,
    max_iterations: Optional[int] = None,
    channel: Optional[str] = None,
    session_key: Optional[str] = None,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """便捷封装：使用默认客户端持续轮询入站事件。"""
    return get_default_client().iter_inbound_events(
        initial_sequence=initial_sequence,
        poll_interval=poll_interval,
        idle_sleep=idle_sleep,
        limit=limit,
        stop_on_empty=stop_on_empty,
        max_iterations=max_iterations,
        channel=channel,
        session_key=session_key,
        on_event=on_event,
        on_error=on_error,
    )


def ack_event(
    event_id: str,
    *,
    status: str = "acked",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """便捷封装：ACK 入站事件。"""
    return get_default_client().ack_event(
        event_id=event_id, status=status, details=details,
    )


# ============================================================
# 6. 内部辅助
# ============================================================

def _truncate(obj: Any, limit: int = 600) -> str:
    """把对象转成截断后的字符串，便于日志输出。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(obj)
    if len(s) > limit:
        return s[:limit] + f"...(+{len(s) - limit} chars)"
    return s


# ============================================================
# 8. CLI 入口（演示用）
# ============================================================

def _main() -> int:  # pragma: no cover - 手工调用入口
    """最小命令行入口：拉取一次事件并打印，便于快速验证。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Channel Gateway 客户端（演示）",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="发送一条测试消息（用 --conversation-id 传入目标 ID）",
    )
    parser.add_argument("--channel", default="feishu", help="通道，默认 feishu")
    parser.add_argument(
        "--conversation-id", default="oc_demo",
        help="目标会话 ID（飞书 chat_id / open_id 通用）",
    )
    parser.add_argument(
        "--receive-id-type", default="chat_id",
        help="receive_id_type，默认 chat_id",
    )
    parser.add_argument(
        "--text", default="hello from channel_gateway_client",
        help="要发送的文本",
    )
    parser.add_argument(
        "--poll", action="store_true",
        help="拉取一次入站事件并打印",
    )
    parser.add_argument(
        "--after-sequence", type=int, default=0,
        help="after_sequence，默认 0",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="每次拉取上限，默认 10",
    )
    args = parser.parse_args()

    client = get_default_client()
    print(f"[host] {client.config.host}")

    if args.send:
        result = client.send_message(
            text=args.text,
            channel=args.channel,
            conversation_id=args.conversation_id,
            receive_id_type=args.receive_id_type,
            idempotency_key=f"cli-{uuid.uuid4().hex[:8]}",
        )
        print("[send] result:", json.dumps({
            "intent_id": result.intent_id,
            "status": result.status,
            "idempotency_key": result.idempotency_key,
            "platform_message_id": result.platform_message_id,
            "replayed": result.replayed,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.poll:
        polled = client.poll_inbound_events(
            after_sequence=args.after_sequence,
            limit=args.limit,
        )
        print(
            f"[poll] events={len(polled.events)} "
            f"latestSequence={polled.latest_sequence}",
        )
        for event in polled.events:
            print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())


# ============================================================
# 7. 公开导出
# ============================================================

__all__ = [
    # 配置 / 数据类
    "GatewayConfig",
    "SendMessageResult",
    "PollResult",
    # 客户端
    "ChannelGatewayClient",
    # 异常
    "GatewayError",
    # 模块级便捷方法
    "get_default_client",
    "configure_default_client",
    "send_message",
    "reply_to_event",
    "poll_inbound_events",
    "iter_inbound_events",
    "ack_event",
]