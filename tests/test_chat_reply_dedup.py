"""chat_reply_handler 单测 — 重点验证 2026-08-19 改造的三个点：
  1. 终态防御（acked/ignored/dead_letter 事件跳过）
  2. LLM 失败 → ack(ignored) + 发错误消息
  3. reply 失败 → 仍 ack=acked（防无限重投）
  4. 循环结构：一次 poll 只处理 batch 第一条（一条一条）
  5. poll_once 透传 status="pending" filter
"""
import importlib

import pytest


@pytest.fixture
def base_event():
    """构造一个最小的飞书入站 event dict（status=pending）。"""
    return {
        "id":          "evt_test_001",
        "sequence":    42,
        "accountId":   "P8",
        "conversation": {"id": "oc_test_chat", "type": "group"},
        "sender":       {"id": "ou_test_user", "type": "user"},
        "message":      {"text": "[job_id=JOB-001] 测试消息"},
        "delivery":     {"status": "pending", "attempts": 0},
    }


# ============================================================
# 1. 终态防御
# ============================================================
@pytest.mark.parametrize(
    "terminal_status",
    ["acked", "ignored", "dead_letter"],
)
def test_handler_skips_terminal_status(base_event, terminal_status):
    """acked/ignored/dead_letter 状态的事件 handler 必须直接 return，不能再调 LLM/ack/reply。"""
    base_event["delivery"]["status"] = terminal_status

    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo") as mock_llm, \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event") as mock_reply:
        chat_reply_handler(base_event)

    mock_llm.assert_not_called()
    mock_ack.assert_not_called()
    mock_reply.assert_not_called()


def test_handler_processes_pending_event(base_event):
    """status=pending 的事件正常处理：调 LLM、reply、ack。"""
    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo", return_value="P8 已处理"), \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event"):
        chat_reply_handler(base_event)

    mock_ack.assert_called_once()
    assert mock_ack.call_args.kwargs["status"] == "acked"


# ============================================================
# 1.5 appid 防御（多 app 场景）
# ============================================================
@pytest.fixture
def p8_event(base_event):
    """带 P8 app_id 的事件（metadata.appId 与 .env FEISHU_P8_APP_ID 一致）。"""
    base_event["metadata"] = {"appId": "cli_p8_test_app"}
    return base_event


@pytest.fixture
def non_p8_event(base_event):
    """非 P8 app 消息（metadata.appId 是另一个 app 的 id）。"""
    base_event["metadata"] = {"appId": "cli_other_app_xxx"}
    return base_event


@pytest.fixture
def no_appid_event(base_event):
    """无 appId 字段的事件（直接 POST /v1/inbound 测试 / 老路径），不应被阻拦。"""
    base_event["metadata"] = {}
    return base_event


def test_handler_skips_non_p8_app_message(non_p8_event, monkeypatch):
    """metadata.appId ≠ .env FEISHU_P8_APP_ID → handler 跳过（多 app 场景）。"""
    from A7.adapters import chat_reply as cr
    monkeypatch.setattr(cr, "_P8_APP_ID", "cli_p8_test_app")

    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo") as mock_llm, \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event") as mock_reply:
        chat_reply_handler(non_p8_event)

    mock_llm.assert_not_called()
    mock_ack.assert_not_called()
    mock_reply.assert_not_called()


def test_handler_processes_p8_app_message(p8_event, monkeypatch):
    """metadata.appId == .env FEISHU_P8_APP_ID → handler 正常处理。"""
    from A7.adapters import chat_reply as cr
    monkeypatch.setattr(cr, "_P8_APP_ID", "cli_p8_test_app")

    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo", return_value="P8 已处理"), \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event"):
        chat_reply_handler(p8_event)

    mock_ack.assert_called_once()


def test_handler_passes_when_no_appid_configured(no_appid_event, monkeypatch):
    """未配置 FEISHU_P8_APP_ID → 视为"接受所有 app"，不阻拦。"""
    from A7.adapters import chat_reply as cr
    monkeypatch.setattr(cr, "_P8_APP_ID", None)   # .env 缺失

    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo", return_value="P8 已处理"), \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event"):
        chat_reply_handler(no_appid_event)

    mock_ack.assert_called_once()   # 没拦截，正常处理


def test_handler_passes_when_event_has_no_appid(p8_event, monkeypatch):
    """event.metadata.appId 缺失（如直接 POST /v1/inbound 测试）→ 不阻拦。

    真实飞书 webhook 路径 feishu.js:228 一定会写 metadata.appId，
    所以这场景只在直接 HTTP 注入 / 老路径出现。处理 = 接受，
    避免破坏现有测试 / SMOKE 注入工具。
    """
    from A7.adapters import chat_reply as cr
    monkeypatch.setattr(cr, "_P8_APP_ID", "cli_p8_test_app")

    # 清掉 metadata.appId
    p8_event["metadata"] = {}   # 无 appId

    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo", return_value="P8 已处理"), \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event"):
        chat_reply_handler(p8_event)

    mock_ack.assert_called_once()


# ============================================================
# 2. LLM 失败 → ack(ignored) + 发错误消息
# ============================================================
def test_handler_llm_failure_acks_ignored_and_sends_error(base_event):
    """LLM 抛异常时：发兜底错误消息 + ack=ignored（不是 acked）。"""
    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo",
                side_effect=RuntimeError("LLM 调用超时")), \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack, \
         _patch("A7.adapters.chat_reply.reply_to_event") as mock_reply:
        chat_reply_handler(base_event)

    mock_reply.assert_called_once()
    ack_kwargs = mock_ack.call_args.kwargs
    assert ack_kwargs["status"] == "ignored", \
        f"LLM 失败应 ack=ignored，实际 ack={ack_kwargs['status']}"
    assert "error" in ack_kwargs.get("details", {})


def test_handler_llm_failure_sends_error_text(base_event):
    """LLM 失败时飞书收到的消息包含 event_id（用户可拿去找运维）。"""
    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo",
                side_effect=RuntimeError("失败")), \
         _patch("A7.adapters.chat_reply.ack_event"), \
         _patch("A7.adapters.chat_reply.reply_to_event") as mock_reply:
        chat_reply_handler(base_event)

    reply_text = (mock_reply.call_args.kwargs.get("text")
                  or mock_reply.call_args.args[1])
    assert base_event["id"] in reply_text
    assert "P8 Agent 调用失败" in reply_text


# ============================================================
# 3. reply 失败 → 仍 ack=acked（避免无限重投）
# ============================================================
# 设计说明：reply 失败时 ack 仍是 "acked"（不是"ignored"）。
#   - "acked" = 已确认处理过 → Gateway 把它从 pending 集合移除 → 不再被 poll 返回
#   - 关键点：避免"飞书偶发 API 抖动 → 同一条消息无限重发重试"
#   - "ignored" 留给 LLM 失败路径（明确表达"我们没处理这条"）
def test_handler_reply_failure_still_acks_to_prevent_replay(base_event):
    """reply_to_event 抛异常时 handler 仍要 ack=acked（防同一条消息无限重投）。"""
    from unittest.mock import patch as _patch
    from A7.adapters.chat_reply import chat_reply_handler

    with _patch("A7.adapters.chat_reply.disposition_demo", return_value="P8 已处理"), \
         _patch("A7.adapters.chat_reply.reply_to_event",
                side_effect=RuntimeError("飞书 API 失败")), \
         _patch("A7.adapters.chat_reply.ack_event") as mock_ack:
        chat_reply_handler(base_event)   # 不应抛出（reply 异常被 catch）

    mock_ack.assert_called_once()
    assert mock_ack.call_args.kwargs["status"] == "acked", \
        "reply 失败仍 ack=acked（防重投）—— 不是 ignored"


# ============================================================
# 4. 循环结构：mock 验证一条条处理（不批量）
# ============================================================
# chat_reply.run_chat_reply_loop 在函数体内
# `from feishu_gateway_cli.feishu_receiver import poll_once`，
# 所以 monkeypatch 必须改 feishu_gateway_cli 模块的属性（不是 chat_reply 本身）。
def test_run_loop_processes_one_at_a_time(monkeypatch):
    """验证 run_chat_reply_loop 一次 poll 后只调一次 handler（不并行、不批量）。

    模拟 Gateway 返回 3 条事件；第二次 poll 让循环退出（KeyboardInterrupt）。
    断言：handler 只被调 1 次，且只处理 batch 的第一条 evt_1。
    """
    import feishu_gateway_cli.feishu_receiver as _recv
    from A7.adapters import chat_reply

    events_batch = [
        {"id": f"evt_{i}", "sequence": i,
         "message": {"text": f"msg-{i}"},
         "conversation": {"id": "oc_test", "type": "group"},
         "sender": {"id": "ou_test", "type": "user"},
         "delivery": {"status": "pending", "attempts": 0}}
        for i in range(1, 4)
    ]

    poll_call_count = {"n": 0}

    def fake_poll_once(**kwargs):
        poll_call_count["n"] += 1
        if poll_call_count["n"] == 1:
            return events_batch   # 第一轮：返 3 条
        raise KeyboardInterrupt  # 第二轮：直接退出循环

    # 必须改 feishu_gateway_cli.feishu_receiver 模块属性
    # （chat_reply.run_chat_reply_loop 函数内 import 从这里取名字）
    monkeypatch.setattr(_recv, "poll_once", fake_poll_once)

    handler_call_count = {"n": 0, "event_ids": []}

    def fake_handler(ev):
        handler_call_count["n"] += 1
        handler_call_count["event_ids"].append(ev["id"])

    monkeypatch.setattr(chat_reply, "chat_reply_handler", fake_handler)

    # 屏蔽 sleep：chat_reply 内部 import `time as _time`，改 sys.modules['time'] 更彻底
    import time as _time_mod
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)

    chat_reply.run_chat_reply_loop(interval=0.01, initial_sequence=0)

    assert handler_call_count["n"] == 1, \
        f"期望只处理 1 条（一条一条），实际处理了 {handler_call_count['n']} 条"
    assert handler_call_count["event_ids"] == ["evt_1"], \
        f"期望只处理第一条 evt_1，实际={handler_call_count['event_ids']}"


# ============================================================
# 5. poll_once 是否带 status="pending" filter
# ============================================================
# 模块路径是 feishu_gateway_cli（不是 openclaw_channel_gateway_standalone）。
def test_poll_once_passes_status_pending():
    """验证 feishu_receiver.poll_once 调底层 poll_inbound_events 时带 status='pending'。"""
    import feishu_gateway_cli.feishu_receiver as feishu_receiver

    captured = {}

    def fake_poll_inbound_events(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"events": [], "latest_sequence": 0})()

    feishu_receiver.poll_inbound_events = fake_poll_inbound_events
    try:
        # initial_sequence=-1 走 bootstrap 路径 → 内部应带 status="pending"
        feishu_receiver.poll_once(initial_sequence=-1)
        assert captured.get("status") == "pending", \
            f"bootstrap 应带 status=pending，实际={captured.get('status')}"

        # explicit sequence → 拉取路径也应带 status="pending"
        captured.clear()
        feishu_receiver.poll_once(initial_sequence=0)
        assert captured.get("status") == "pending", \
            f"explicit sequence 也应带 status=pending，实际={captured.get('status')}"
    finally:
        # 恢复（避免污染后续测试）
        importlib.reload(feishu_receiver)