"""
Feishu outbox：reply_to_event ambiguous 失败时落盘 + 手动/worker 重发。

================================================================================
背景（2026-08-20）
================================================================================
飞书 webhook 偶尔返回 ``HTTP 502 UPSTREAM_NETWORK_ERROR``（``retryable=False`` /
``ambiguous=True`` / ``outbound_status=unknown``），表示飞书实际是否收到消息
**未知**。盲目重试可能重复发送，不重试则消息丢失。

Gateway 主动把 ambiguous 场景的 ``retryable`` 强制置 ``False``，语义是
"**可能已发送，盲目重试会重复**"。但 ``reply_to_event`` 支持 ``idempotency_key``，
**同 key gateway 自动去重**（``replayed:true``），所以可以在不丢消息的前提下重试。

本模块提供：
- :func:`enqueue_failed_reply`：chat_reply.py 在 ambiguous 失败时调用，落盘一条记录
- :func:`list_outbox`：列出当前待重发条目（仅 metadata）
- :func:`retry_outbox`：手动/worker 触发的重发接口
- :const:`OUTBOX_DIR`：落盘根目录（默认 ``data/runtime/feishu_outbox/``，
  已被 ``.gitignore`` 忽略）

================================================================================
数据格式（每文件一个事件，文件名 = event_id）
================================================================================
::
    {
      "event_id": "evt_xxx",
      "text": "P8 回复内容",
      "account_id": "P8",
      "idempotency_key": "reply-evt_xxx",   # 同 key gateway 去重
      "channel": "feishu",
      "error_code": "UPSTREAM_NETWORK_ERROR",
      "error_message": "Feishu send message failed ...",
      "intent_id": "out_xxx",                # 可选，gateway 返回
      "request_id": "92c736...",             # 可选
      "failed_at": "2026-08-20T09:21:21Z",
      "retry_count": 1,
      "last_retry_at": "2026-08-20T09:30:00Z",
      "last_retry_error": null
    }

================================================================================
Worker 接入（后续任务，留口子）
================================================================================
后续可通过 ``cron`` 或独立进程周期性调用 :func:`retry_outbox()`::

    from A7.adapters.feishu_outbox import retry_outbox
    result = retry_outbox()  # 重发所有
    logger.info("outbox 重发: %s", result)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.channel_gateway_client import GatewayError, reply_to_event

logger = logging.getLogger("feishu_outbox")

# 默认落盘目录；可用 FEISHU_OUTBOX_DIR 环境变量覆盖（测试场景）
OUTBOX_DIR = Path(os.getenv(
    "FEISHU_OUTBOX_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "data" / "runtime" / "feishu_outbox"),
))

_outbox_lock = threading.Lock()


def _safe_filename(event_id: str) -> str:
    """防 path traversal：仅保留 [A-Za-z0-9_.-]，替换其他字符为 _。限制 128 字符。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", event_id)[:128]


def enqueue_failed_reply(
    *,
    event_id: str,
    text: str,
    account_id: Optional[str],
    idempotency_key: str,
    channel: str = "feishu",
    error_code: str = "UNKNOWN",
    error_message: str = "",
    intent_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Path:
    """落盘一条 ambiguous 失败的回复；返回写入路径。

    若文件已存在（重试多次失败累积），保留原有 ``failed_at`` 与 ``retry_count`` 累加，
    更新错误字段；其余字段全部覆盖。

    Args:
        event_id: 内部事件 ID（``evt_xxx``）。
        text: P8 回复文本。
        account_id: gateway 账号 ID。
        idempotency_key: 与首次发送时保持一致，让 gateway 同 key 去重。
        channel: 默认 ``feishu``。
        error_code / error_message: GatewayError 字段透传。
        intent_id: Gateway 返回的 outbox intent ID（可选）。
        request_id: Gateway 返回的 X-Request-Id（可选）。

    Returns:
        写入文件的绝对路径。
    """
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    fname = _safe_filename(event_id) + ".json"
    fpath = OUTBOX_DIR / fname

    with _outbox_lock:
        if fpath.exists():
            try:
                existing = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            payload = {
                **existing,
                "event_id": event_id,
                "text": text,
                "account_id": account_id,
                "idempotency_key": idempotency_key,
                "channel": channel,
                "error_code": error_code,
                "error_message": error_message,
                "intent_id": intent_id or existing.get("intent_id"),
                "request_id": request_id or existing.get("request_id"),
                "failed_at": existing.get("failed_at") or datetime.now(timezone.utc).isoformat(),
                "retry_count": existing.get("retry_count", 0) + 1,
                "last_retry_at": None,
                "last_retry_error": None,
            }
        else:
            payload = {
                "event_id": event_id,
                "text": text,
                "account_id": account_id,
                "idempotency_key": idempotency_key,
                "channel": channel,
                "error_code": error_code,
                "error_message": error_message,
                "intent_id": intent_id,
                "request_id": request_id,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "retry_count": 1,
                "last_retry_at": None,
                "last_retry_error": None,
            }
        fpath.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "[feishu_outbox] enqueue: event_id=%s retry_count=%d path=%s",
            event_id, payload["retry_count"], fpath,
        )
    return fpath


def list_outbox() -> List[Dict[str, Any]]:
    """列出所有 outbox 条目（仅 metadata，不含 text 防止日志过大）。"""
    if not OUTBOX_DIR.exists():
        return []
    items = []
    for f in sorted(OUTBOX_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append({
                "path": str(f),
                "event_id": data.get("event_id"),
                "failed_at": data.get("failed_at"),
                "retry_count": data.get("retry_count", 0),
                "error_code": data.get("error_code"),
                "intent_id": data.get("intent_id"),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("outbox 文件 %s 解析失败: %s", f, exc)
    return items


def retry_outbox(
    event_id: Optional[str] = None,
    *,
    delete_on_success: bool = True,
) -> Dict[str, Any]:
    """手动/worker 重发 outbox。

    Args:
        event_id: 指定单个 event_id；``None`` = 全部。
        delete_on_success: 重发成功后是否删除文件（默认 True，避免累积）。

    Returns:
        ``{"attempted": int, "succeeded": int, "failed": int, "errors": [...]}``

    Note:
        - 重发仍使用原 ``idempotency_key``，gateway 同 key 会去重（``replayed:true``）
        - 失败时更新 ``last_retry_at`` / ``last_retry_error`` 元数据，**不删除文件**
        - 可由 worker / 人工触发；调用方应自行控制并发（避免同时多次重发）
    """
    if not OUTBOX_DIR.exists():
        return {"attempted": 0, "succeeded": 0, "failed": 0, "errors": []}

    attempted = succeeded = failed = 0
    errors: List[Dict[str, str]] = []

    if event_id is None:
        targets = sorted(OUTBOX_DIR.glob("*.json"))
    else:
        targets = [OUTBOX_DIR / (_safe_filename(event_id) + ".json")]

    for fpath in targets:
        if not fpath.exists():
            errors.append({"path": str(fpath), "error": "file not found"})
            continue
        attempted += 1
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"path": str(fpath), "error": f"read failed: {exc}"})
            continue

        try:
            reply_to_event(
                event_id=data["event_id"],
                text=data["text"],
                account_id=data.get("account_id"),
                channel=data.get("channel", "feishu"),
                idempotency_key=data["idempotency_key"],
            )
            # 成功 → 更新元数据
            data["last_retry_at"] = datetime.now(timezone.utc).isoformat()
            data["last_retry_error"] = None
            if delete_on_success:
                fpath.unlink()
                logger.info(
                    "[feishu_outbox] 重发成功并删除: event_id=%s path=%s",
                    data["event_id"], fpath,
                )
            else:
                fpath.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(
                    "[feishu_outbox] 重发成功保留: event_id=%s path=%s",
                    data["event_id"], fpath,
                )
            succeeded += 1
        except GatewayError as exc:
            failed += 1
            data["last_retry_at"] = datetime.now(timezone.utc).isoformat()
            data["last_retry_error"] = f"[{exc.code}] {exc.message}"
            # 累加重试计数（重发本身算一次 retry）
            data["retry_count"] = data.get("retry_count", 0) + 1
            fpath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            errors.append({
                "path": str(fpath),
                "event_id": data["event_id"],
                "error": f"[{exc.code}] {exc.message}",
            })
            logger.warning(
                "[feishu_outbox] 重发失败: event_id=%s err=[%s] %s",
                data["event_id"], exc.code, exc.message,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append({"path": str(fpath), "error": f"unexpected: {exc}"})
            logger.exception("[feishu_outbox] 重发异常: %s", exc)

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
    }


__all__ = [
    "OUTBOX_DIR",
    "enqueue_failed_reply",
    "list_outbox",
    "retry_outbox",
]