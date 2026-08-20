"""
chat_reply.py reply_to_event 重试机制测试（2026-08-20）。

设计：
- mock reply_to_event 不真正调用 gateway（monkeypatch）
- tenacity 的 sleep 通过 ``time.sleep`` monkeypatch 避免测试被拖慢
- 沿用 tests/test_chat_reply_dedup.py 的 mock_disposition 模式

覆盖：
1. safe 错误：重试 3 次后成功
2. safe 错误：重试耗尽 → ACK（不落盘）
3. ambiguous 错误：1 次调用 + 落盘 outbox + 同 idempotency_key
4. permanent 错误：1 次调用 + ACK（不落盘）
5. idempotency_key 始终 = "reply-{event_id}"
6. outbox retry_outbox() 成功 → 删除
7. outbox retry_outbox() 失败 → 保留 + 更新 retry_count/last_retry_error
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from A7.adapters import chat_reply
from A7.adapters.chat_reply import (
    chat_reply_handler,
    _classify_gateway_error,
    _reply_with_safe_retry,
)
from A7.adapters import feishu_outbox
from agents.channel_gateway_client import GatewayError


# ============================================================
# Fixtures & Helpers
# ============================================================

def _make_gateway_error(*, code="UPSTREAM_NETWORK_ERROR", status_code=502,
                        retryable=False, ambiguous=False, message="test",
                        **details):
    """构造一个 GatewayError 用于测试"""
    return GatewayError(
        code=code, message=message, status_code=status_code,
        retryable=retryable, ambiguous=ambiguous, details=details,
    )


def _event(event_id="evt_test_001", text="@_user_1 hi", chat_id="oc_test"):
    """构造一个入站事件 dict（chat_reply_handler 接受的格式）"""
    return {
        "id": event_id,
        "accountId": "P8",
        "channel": "feishu",
        "conversation": {"id": chat_id, "type": "group"},
        "message": {"id": f"om_{event_id}", "type": "text", "text": text},
        "sender": {"id": "ou_test_user", "type": "user"},
        "metadata": {"appId": "cli_aac27c4dfcf9dbea"},  # 通过 appid 防御
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """patch time.sleep → tenacity 内部也用 time.sleep，patch 后不会真睡"""
    import time
    monkeypatch.setattr("time.sleep", lambda _: None)
    yield


@pytest.fixture
def mock_dependencies(monkeypatch):
    """mock disposition_demo / reply_to_event / ack_event"""
    monkeypatch.setattr(chat_reply, "disposition_demo", lambda *a, **kw: "P8 reply text")
    mock_reply = MagicMock()
    mock_ack = MagicMock()
    monkeypatch.setattr(chat_reply, "reply_to_event", mock_reply)
    monkeypatch.setattr(chat_reply, "ack_event", mock_ack)
    return mock_reply, mock_ack


@pytest.fixture
def tmp_outbox(monkeypatch, tmp_path):
    """把 OUTBOX_DIR 重定向到 tmp_path，避免污染 data/runtime/feishu_outbox"""
    outbox_dir = tmp_path / "feishu_outbox"
    monkeypatch.setattr(feishu_outbox, "OUTBOX_DIR", outbox_dir)
    return outbox_dir


# ============================================================
# 单元测试：三档决策
# ============================================================

class TestClassifyGatewayError:
    def test_safe(self):
        exc = _make_gateway_error(retryable=True, ambiguous=False)
        assert _classify_gateway_error(exc) == "safe"

    def test_ambiguous_takes_precedence_over_retryable(self):
        # Gateway 在 ambiguous 场景会同时设 retryable=False
        exc = _make_gateway_error(retryable=False, ambiguous=True)
        assert _classify_gateway_error(exc) == "ambiguous"

    def test_ambiguous_with_retryable_true_still_ambiguous(self):
        # 防御性：哪怕 retryable=True，ambiguous=True 时应归 ambiguous
        exc = _make_gateway_error(retryable=True, ambiguous=True)
        assert _classify_gateway_error(exc) == "ambiguous"

    def test_permanent(self):
        exc = _make_gateway_error(retryable=False, ambiguous=False)
        assert _classify_gateway_error(exc) == "permanent"


# ============================================================
# 集成测试：chat_reply_handler 三档行为
# ============================================================

class TestChatReplyHandler:
    def test_safe_error_retries_3_times_then_succeeds(self, mock_dependencies):
        """safe 错误 → 第 3 次成功 → 不落盘 + ACK"""
        mock_reply, mock_ack = mock_dependencies
        mock_reply.side_effect = [
            _make_gateway_error(retryable=True, ambiguous=False, message="net-1"),
            _make_gateway_error(retryable=True, ambiguous=False, message="net-2"),
            {"intent_id": "out_ok"},  # 第三次成功
        ]
        chat_reply_handler(_event("evt_safe_001"))
        assert mock_reply.call_count == 3
        assert mock_ack.call_args.kwargs["status"] == "acked"

    def test_safe_error_exhausts_retries_then_acks_no_outbox(
        self, mock_dependencies, tmp_outbox
    ):
        """safe 错误 → 3 次全失败 → ACK 不落盘（safe 临时性网络，留 outbox 无意义）"""
        mock_reply, mock_ack = mock_dependencies
        mock_reply.side_effect = _make_gateway_error(
            retryable=True, ambiguous=False, message="net-down",
        )
        chat_reply_handler(_event("evt_safe_fail"))
        assert mock_reply.call_count == 3
        assert mock_ack.call_args.kwargs["status"] == "acked"
        # 不应落盘
        assert not tmp_outbox.exists() or not list(tmp_outbox.glob("*.json"))

    def test_ambiguous_error_writes_to_outbox_with_idempotency_key(
        self, mock_dependencies, tmp_outbox
    ):
        """ambiguous → 1 次调用 + 落盘 outbox + 同 idempotency_key"""
        mock_reply, mock_ack = mock_dependencies
        mock_reply.side_effect = _make_gateway_error(
            code="UPSTREAM_NETWORK_ERROR",
            retryable=False, ambiguous=True,
            intent_id="out_ambig_001",
        )
        chat_reply_handler(_event("evt_amb_001"))

        # ambiguous 不重试
        assert mock_reply.call_count == 1
        # 仍 ACK（避免无限重投）
        assert mock_ack.call_args.kwargs["status"] == "acked"

        # 落盘验证
        outbox_files = list(tmp_outbox.glob("*.json"))
        assert len(outbox_files) == 1
        payload = json.loads(outbox_files[0].read_text(encoding="utf-8"))
        assert payload["event_id"] == "evt_amb_001"
        assert payload["idempotency_key"] == "reply-evt_amb_001"
        assert payload["intent_id"] == "out_ambig_001"
        assert payload["error_code"] == "UPSTREAM_NETWORK_ERROR"
        assert payload["account_id"] == "P8"
        assert payload["channel"] == "feishu"
        assert payload["retry_count"] == 1
        assert payload["failed_at"] is not None

    def test_permanent_error_acks_without_retry(self, mock_dependencies, tmp_outbox):
        """permanent → 1 次调用 + ACK 不落盘"""
        mock_reply, mock_ack = mock_dependencies
        mock_reply.side_effect = _make_gateway_error(
            code="VALIDATION_ERROR", retryable=False, ambiguous=False,
            message="to.conversationId must be non-empty",
        )
        chat_reply_handler(_event("evt_perm_001"))
        assert mock_reply.call_count == 1
        assert mock_ack.call_args.kwargs["status"] == "acked"
        assert not tmp_outbox.exists() or not list(tmp_outbox.glob("*.json"))

    def test_idempotency_key_passed(self, mock_dependencies):
        """idempotency_key 始终 = reply-{event_id}（无论成功/失败）"""
        mock_reply, _ = mock_dependencies
        mock_reply.return_value = {"intent_id": "out_ok"}
        chat_reply_handler(_event("evt_idem_001"))
        assert mock_reply.call_args.kwargs["idempotency_key"] == "reply-evt_idem_001"

    def test_runtime_error_does_not_break(self, mock_dependencies):
        """RuntimeError（非 GatewayError）走 except Exception 分支 → 仍 ACK（不破坏现有测试）"""
        mock_reply, mock_ack = mock_dependencies
        mock_reply.side_effect = RuntimeError("非网关错误，如 LLM 解析失败")
        # 不应抛异常
        chat_reply_handler(_event("evt_rt_001"))
        assert mock_reply.call_count == 1
        assert mock_ack.call_args.kwargs["status"] == "acked"

    def test_safe_filename_used_for_outbox(self, mock_dependencies, tmp_outbox):
        """event_id 含特殊字符 → 文件名被 sanitize（防 path traversal）"""
        mock_reply, _ = mock_dependencies
        mock_reply.side_effect = _make_gateway_error(
            retryable=False, ambiguous=True,
        )
        chat_reply_handler(_event("evt/with/slashes_001"))
        outbox_files = list(tmp_outbox.glob("*.json"))
        assert len(outbox_files) == 1
        # 斜杠已被替换为 _，无目录创建
        assert "/" not in outbox_files[0].name


# ============================================================
# Outbox 单元测试
# ============================================================

class TestFeishuOutbox:
    def test_enqueue_creates_file(self, tmp_outbox):
        """首次 enqueue 创建文件"""
        path = feishu_outbox.enqueue_failed_reply(
            event_id="evt_x_001", text="hello", account_id="P8",
            idempotency_key="reply-evt_x_001", error_code="UPSTREAM_NETWORK_ERROR",
            intent_id="out_1",
        )
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["event_id"] == "evt_x_001"
        assert payload["retry_count"] == 1

    def test_enqueue_repeated_increments_retry_count(self, tmp_outbox):
        """重复 enqueue 累加 retry_count"""
        feishu_outbox.enqueue_failed_reply(
            event_id="evt_x_002", text="t", account_id="P8",
            idempotency_key="reply-evt_x_002",
        )
        feishu_outbox.enqueue_failed_reply(
            event_id="evt_x_002", text="t", account_id="P8",
            idempotency_key="reply-evt_x_002",
        )
        path = next(tmp_outbox.glob("evt_x_002*.json"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["retry_count"] == 2
        # 保留原始 failed_at
        assert payload["failed_at"] is not None

    def test_retry_outbox_succeeds_and_deletes(self, tmp_outbox):
        """重发成功 → 删除文件 + 计数器 +1"""
        feishu_outbox.enqueue_failed_reply(
            event_id="evt_retry_001", text="P8 reply", account_id="P8",
            idempotency_key="reply-evt_retry_001",
            error_code="UPSTREAM_NETWORK_ERROR", intent_id="out_x",
        )
        with patch.object(
            feishu_outbox, "reply_to_event", return_value={"intent_id": "out_y"}
        ) as mock_reply:
            result = feishu_outbox.retry_outbox()

        assert result["succeeded"] == 1
        assert result["failed"] == 0
        assert result["attempted"] == 1
        assert mock_reply.call_args.kwargs["idempotency_key"] == "reply-evt_retry_001"
        # 文件已删
        assert not list(tmp_outbox.glob("*.json"))

    def test_retry_outbox_failure_preserves_file(self, tmp_outbox):
        """重发失败 → 保留文件 + retry_count +1 + last_retry_error 更新"""
        feishu_outbox.enqueue_failed_reply(
            event_id="evt_retry_002", text="t", account_id="P8",
            idempotency_key="reply-evt_retry_002",
            error_code="UPSTREAM_NETWORK_ERROR",
        )
        with patch.object(
            feishu_outbox, "reply_to_event",
            side_effect=_make_gateway_error(
                retryable=False, ambiguous=True,
                code="UPSTREAM_NETWORK_ERROR", message="still ambiguous",
            ),
        ):
            result = feishu_outbox.retry_outbox()

        assert result["succeeded"] == 0
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        # 文件保留 + retry_count = 2（初次 + 重试）
        files = list(tmp_outbox.glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["retry_count"] == 2
        assert "[UPSTREAM_NETWORK_ERROR]" in payload["last_retry_error"]
        assert payload["last_retry_at"] is not None

    def test_retry_outbox_single_event(self, tmp_outbox):
        """retry_outbox(event_id="xxx") 只重发指定那条"""
        for i in range(3):
            feishu_outbox.enqueue_failed_reply(
                event_id=f"evt_multi_{i:03d}", text="t", account_id="P8",
                idempotency_key=f"reply-evt_multi_{i:03d}",
            )
        with patch.object(
            feishu_outbox, "reply_to_event", return_value={"intent_id": "out_y"}
        ) as mock_reply:
            result = feishu_outbox.retry_outbox("evt_multi_001")

        assert result["attempted"] == 1
        assert result["succeeded"] == 1
        # 其他两条还在
        remaining = list(tmp_outbox.glob("*.json"))
        assert len(remaining) == 2

    def test_retry_outbox_delete_on_success_false(self, tmp_outbox):
        """delete_on_success=False 时重发成功保留文件"""
        feishu_outbox.enqueue_failed_reply(
            event_id="evt_keep_001", text="t", account_id="P8",
            idempotency_key="reply-evt_keep_001",
        )
        with patch.object(
            feishu_outbox, "reply_to_event", return_value={"intent_id": "out_y"}
        ):
            result = feishu_outbox.retry_outbox(delete_on_success=False)

        assert result["succeeded"] == 1
        # 文件保留
        assert len(list(tmp_outbox.glob("*.json"))) == 1

    def test_list_outbox(self, tmp_outbox):
        """list_outbox 返回 metadata，不含 text"""
        feishu_outbox.enqueue_failed_reply(
            event_id="evt_list_001", text="hello world", account_id="P8",
            idempotency_key="reply-evt_list_001",
            error_code="X", intent_id="out_z",
        )
        items = feishu_outbox.list_outbox()
        assert len(items) == 1
        item = items[0]
        assert item["event_id"] == "evt_list_001"
        assert item["error_code"] == "X"
        assert item["intent_id"] == "out_z"
        # 不应含 text 字段（防日志过大）
        assert "text" not in item