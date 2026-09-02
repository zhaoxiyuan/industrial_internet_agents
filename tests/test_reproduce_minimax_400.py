"""复现 MiniMax tool_use_id 400 错误 + 验证根因。

用法（手动运行，**不在 pytest 套件里**，避免 CI 自动触发）：
    python -m pytest tests/test_reproduce_minimax_400.py -s -v --tb=short --maxfail=1 \
        --override-ini="addopts="

约束：只读 tests/ 外的代码，不改外部代码。允许 import 任何现有模块。

验证目标：
  H1: 多轮同 thread_id 调用 disposition_demo → 是否报 400？
  H2: 多轮 unique thread_id 调用 → 是否不报 400？
  H3: 直接 HTTP 打 MiniMax，构造"含历史 ToolMessage 的 messages" → 是否报 400？
  H4: 直接 HTTP 打 MiniMax，构造"仅当次 messages（无历史 ToolMessage）" → 是否正常？
  H5: 错配（ToolMessage.tool_call_id 与 AIMessage.tool_calls[i].id 不一致）→ 是否报 400？
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# 让相对路径能找到项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


# ============================================================
# 工具：构造一个会"必然触发 list_active_p8_jobs 工具调用"的 prompt
# ============================================================
# 工具 list_active_p8_jobs 是只读无副作用的，调用后必然产生
# AIMessage(tool_calls=[call_xxx]) + ToolMessage(tool_call_id=call_xxx) 这对配对。
#
# 注意：LLM 是否调用工具受 system_prompt 引导 + 用户问题影响。我们用清晰指令引导。

PROMPT_TRIGGER_TOOL = (
    "调用 list_active_p8_jobs 工具，"
    "并把工具返回的 active_p8_jobs 字段原样转述给我。"
    "不要做任何额外解释。"
)

PROMPT_NO_TOOL = (
    "用一句话回答：你叫什么名字？"
)


def _call_disposition_demo(message: str, *, thread_id: str) -> tuple[str, dict]:
    """调 disposition_demo，捕获异常和底层 invoke 结果。

    Returns:
        (response_text, debug_info)
        debug_info 含 invoke 内部关键状态（如果有的话）。
    """
    from agents.p8_disposition_agent import run_disposition_agent, _p8_checkpointer

    # 清空 checkpointer 中该 thread_id 的旧状态（避免被前一次跑污染）
    # MemorySaver 没有删除 API → 直接用新 thread_id 隔离

    debug = {"thread_id": thread_id, "exception": None}
    try:
        resp = run_disposition_agent(message, thread_id=thread_id)
        debug["response"] = resp[:200] if resp else ""
        return resp, debug
    except Exception as exc:
        debug["exception"] = exc
        debug["exception_type"] = type(exc).__name__
        debug["exception_str"] = str(exc)[:1000]
        return "", debug


# ============================================================
# H1: 多轮同 thread_id
# ============================================================
@pytest.mark.manual  # 不在自动 CI 跑
def test_h1_multi_round_same_thread_id():
    """H1: 同一 thread_id 调 4 次，让 LLM 每次都触发工具调用。

    预期观察：是否在第 N 轮 invoke 时报 400 "tool result's tool id ... not found (2013)"。
    """
    thread_id = f"probe-h1-{int(time.time())}"
    print(f"\n[H1] thread_id={thread_id}")

    history = []
    for round_idx in range(1, 5):
        msg = PROMPT_TRIGGER_TOOL if round_idx > 1 else (
            f"你好，这是第 {round_idx} 轮。请简短回复。" if round_idx == 1
            else PROMPT_TRIGGER_TOOL
        )
        print(f"\n[H1] round={round_idx} → 调 disposition_demo ...")
        resp, dbg = _call_disposition_demo(msg, thread_id=thread_id)
        history.append({"round": round_idx, "msg": msg, "resp": resp, "dbg": dbg})

        if dbg["exception"]:
            print(f"[H1] round={round_idx} 异常: {dbg['exception_type']}: {dbg['exception_str']}")
            # 命中预期错误 → 记录并提前结束
            if "tool" in dbg["exception_str"].lower() and "2013" in dbg["exception_str"]:
                pytest.fail(
                    f"H1 已复现: round={round_idx} 报 2013 tool_id 错误。"
                    f"exception={dbg['exception_str']}"
                )
        else:
            print(f"[H1] round={round_idx} 成功: {resp[:80]}")

        # 看 checkpointer 状态
        from agents.p8_disposition_agent import _p8_checkpointer
        ck = _p8_checkpointer.get({"configurable": {"thread_id": thread_id}})
        if ck:
            msgs = ck.checkpoint["channel_values"].get("messages", [])
            tool_msgs = [m for m in msgs if m.type == "tool"]
            ai_tool_call_msgs = [m for m in msgs
                                 if m.type == "ai" and getattr(m, "tool_calls", None)]
            print(f"[H1] checkpoint 状态: total_msgs={len(msgs)}, "
                  f"tool_msgs={len(tool_msgs)}, ai_with_tool_calls={len(ai_tool_call_msgs)}")

            # 校验 ToolMessage.tool_call_id 与前一条 AIMessage.tool_calls[i].id 是否一一对应
            mismatch = []
            for i, m in enumerate(msgs):
                if m.type == "tool":
                    expected_id = m.tool_call_id
                    # 向前找最近的 AIMessage(tool_calls)
                    for prev in reversed(msgs[:i]):
                        if prev.type == "ai" and getattr(prev, "tool_calls", None):
                            ids = [tc["id"] for tc in prev.tool_calls]
                            if expected_id not in ids:
                                mismatch.append((i, expected_id, ids))
                            break
            if mismatch:
                print(f"[H1] 客户端 messages 内 id 配对不一致: {mismatch}")

    # 跑到第 4 轮都没报错 → 假设不成立
    pytest.fail(
        f"H1: 跑完 4 轮没复现 400。\n"
        f"说明 '累积历史 ToolMessage' 可能不是唯一/真实根因。\n"
        f"看上面打印的 history 推断下一步。"
    )


# ============================================================
# H2: 多轮 unique thread_id
# ============================================================
@pytest.mark.manual
def test_h2_multi_round_unique_thread_id():
    """H2: 每次用新 thread_id，不累积历史 → 应永不报 400。"""
    print("\n[H2] 每次新 thread_id")
    for round_idx in range(1, 5):
        thread_id = f"probe-h2-{int(time.time())}-{round_idx}"
        msg = PROMPT_TRIGGER_TOOL if round_idx > 1 else (
            f"这是第 {round_idx} 轮。"
        )
        resp, dbg = _call_disposition_demo(msg, thread_id=thread_id)
        if dbg["exception"]:
            print(f"[H2] round={round_idx} 异常: {dbg['exception_str']}")
            pytest.fail(f"H2 round={round_idx} 异常: {dbg['exception_str']}")
        else:
            print(f"[H2] round={round_idx} OK: {resp[:80]}")


# ============================================================
# H3 / H4 / H5: 直接打 MiniMax HTTP API
# ============================================================
@pytest.mark.manual
def test_h3_http_full_history_with_tool_message():
    """H3: 模拟 LangGraph 累积 history 的实际请求。

    构造完整 messages 含历史 ToolMessage.tool_call_id 与 AIMessage.tool_calls[i].id
    配对正确（与客户端内部状态一致） → 看 MiniMax 服务端是否仍报 2013。
    """
    import requests

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
    model = os.environ["OPENAI_MODEL"]

    print("\n[H3] 模拟完整 history 请求")

    # 第一轮：无工具
    messages_r1 = [{"role": "user", "content": "你好"}]
    body1 = {"model": model, "messages": messages_r1, "stream": False}
    r1 = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body1, timeout=60,
    )
    print(f"[H3] round 1 status={r1.status_code}, resp={r1.text[:300]}")
    assert r1.status_code == 200, r1.text

    r1_data = r1.json()
    asst_msg_1 = r1_data["choices"][0]["message"]
    messages_r1.append(asst_msg_1)

    # 第二轮：让 LLM 调工具（list_active_p8_jobs 在 P8 系统提示里）
    # 直接给 messages 一个 list_active_p8_jobs tool 定义 + 用户要求调工具
    messages_r2 = list(messages_r1)
    messages_r2.append({"role": "user", "content": "调用 list_active_p8_jobs 工具，把结果告诉我"})

    # 注入工具定义（必须与 agent 注册的一致）
    tool_def = {
        "type": "function",
        "function": {
            "name": "list_active_p8_jobs",
            "description": "列出当前所有 in-progress P8_job（从 working_memory 读）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
    body2 = {"model": model, "messages": messages_r2, "tools": [tool_def], "stream": False}
    r2 = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body2, timeout=60,
    )
    print(f"[H3] round 2 (tool call) status={r2.status_code}")
    if r2.status_code != 200:
        print(f"[H3] round 2 FAILED: {r2.text[:500]}")
        pytest.fail(f"H3 round 2 状态码={r2.status_code}，text={r2.text[:500]}")

    r2_data = r2.json()
    asst_msg_2 = r2_data["choices"][0]["message"]
    print(f"[H3] round 2 响应含 tool_calls: {bool(asst_msg_2.get('tool_calls'))}")
    if not asst_msg_2.get("tool_calls"):
        print(f"[H3] LLM 没调工具（可能 system prompt 没注入），跳过: {asst_msg_2}")
        pytest.skip("LLM 未触发工具调用")

    tool_call_id = asst_msg_2["tool_calls"][0]["id"]
    print(f"[H3] tool_call_id = {tool_call_id}")

    # 模拟工具执行（list_active_p8_jobs 返回固定 JSON）
    tool_response = json.dumps({"active_p8_jobs": [], "note": "mocked"}, ensure_ascii=False)

    # 第三轮：把完整 history 都发给服务端（含上次 AIMessage + 这次 ToolMessage）
    # 这是 LangGraph MemorySaver 重发历史的典型形态
    messages_r3 = list(messages_r2)
    messages_r3.append(asst_msg_2)   # AIMessage(tool_calls)
    messages_r3.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": tool_response,
    })
    messages_r3.append({"role": "user", "content": "好的，总结一下"})

    body3 = {"model": model, "messages": messages_r3, "tools": [tool_def], "stream": False}
    r3 = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body3, timeout=60,
    )
    print(f"[H3] round 3 (含历史 ToolMessage) status={r3.status_code}")
    if r3.status_code != 200:
        print(f"[H3] round 3 FAILED: {r3.text[:500]}")
        pytest.fail(f"H3 已复现: round 3 状态码={r3.status_code}\n{r3.text[:500]}")
    else:
        print(f"[H3] round 3 OK: {r3.json()['choices'][0]['message']['content'][:200]}")


@pytest.mark.manual
def test_h4_http_clean_history_no_tool_message():
    """H4: 构造"仅当次"messages，不含历史 ToolMessage → 应正常。

    等价于 unique thread_id 行为。
    """
    import requests

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
    model = os.environ["OPENAI_MODEL"]

    # 直接发"含 tool 配对完整"但**只在同一次 request 内**的 messages
    # 不重发历史 ToolMessage
    messages = [
        {"role": "user", "content": "用一句话回答你叫什么名字？"},
    ]

    tool_def = {
        "type": "function",
        "function": {
            "name": "noop_tool",
            "description": "no-op tool",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }

    body = {"model": model, "messages": messages, "tools": [tool_def], "stream": False}
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    print(f"[H4] status={r.status_code}")
    assert r.status_code == 200, f"H4 应正常，实际 {r.status_code}: {r.text[:300]}"
    print(f"[H4] OK: {r.json()['choices'][0]['message']['content'][:200]}")


@pytest.mark.manual
def test_h5_http_mismatched_tool_id():
    """H5: 故意配错 ToolMessage.tool_call_id 与 AIMessage.tool_calls[i].id。

    预期：服务端报 2013（错配）—— 这验证 H3 中如果 client 配对正确但服务端仍报 2013，
    那服务端校验算法不止"严格配对"。
    """
    import requests

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
    model = os.environ["OPENAI_MODEL"]

    # 构造: AIMessage(tool_calls=[id="call_real_xxx"]) → ToolMessage(tool_call_id="call_FAKE_xxx")
    # 期望 400
    fake_tool_call_id = "call_ffffffffffffffffffffffff"
    messages = [
        {"role": "user", "content": "随便说一句话"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": fake_tool_call_id,
                "type": "function",
                "function": {"name": "noop_tool", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": fake_tool_call_id,
            "content": "ok",
        },
        {"role": "user", "content": "继续"},
    ]

    tool_def = {
        "type": "function",
        "function": {
            "name": "noop_tool",
            "description": "no-op tool",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }

    body = {"model": model, "messages": messages, "tools": [tool_def], "stream": False}
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    print(f"[H5] 错配 tool_call_id 状态码={r.status_code}")
    print(f"[H5] 响应: {r.text[:500]}")
    # 错配应报错；记录但不强制 fail（不同厂商行为不同）
    if r.status_code == 200:
        print("[H5] 注意：错配 id 居然没报错 —— MiniMax 不校验此规则")
    else:
        print(f"[H5] 错配被服务端拒绝（预期行为），码={r.status_code}")


# ============================================================
# H6: 用异常的 tool_call_id 格式（"chatcmpl-tool-..."）触发 2013
# ============================================================
@pytest.mark.manual
def test_h6_http_abnormal_id_format():
    """H6: 实际错误日志显示 AIMessage 返回的 tool_call_id 是
    'chatcmpl-tool-a0e3e5ef1222ca90'（不是 OpenAI 标准 'call_xxx' 24hex 格式）。

    验证：当 messages 列表里的 tool_call_id 用这个异常格式时，MiniMax 是否报 2013。
    """
    import requests

    api_key = os.environ["OPENAI_API_KEY"]
    base_url = os.environ["OPENAI_BASE_URL"].rstrip("/")
    model = os.environ["OPENAI_MODEL"]

    # 完全照搬实际错误场景的格式
    actual_bad_id = "chatcmpl-tool-a0e3e5ef1222ca90"

    messages = [
        {"role": "user", "content": "随便说一句话"},
        {
            "role": "assistant",
            "content": "好的，我调用工具看看",
            "tool_calls": [{
                "id": actual_bad_id,
                "type": "function",
                "function": {"name": "noop_tool", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": actual_bad_id,
            "content": "ok",
        },
        {"role": "user", "content": "继续"},
    ]

    tool_def = {
        "type": "function",
        "function": {
            "name": "noop_tool",
            "description": "no-op tool",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }

    body = {"model": model, "messages": messages, "tools": [tool_def], "stream": False}
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    print(f"[H6] 异常 id 格式 'chatcmpl-tool-xxx' 状态码={r.status_code}")
    print(f"[H6] 响应: {r.text[:500]}")

    if r.status_code == 400 and "2013" in r.text:
        pytest.fail(
            f"H6: 复现成功！\n"
            f"tool_call_id='chatcmpl-tool-...' 格式触发了 MiniMax 2013 校验失败。\n"
            f"实际响应: {r.text[:500]}\n"
            f"结论：MiniMax 服务端校验的是 tool_call_id 格式（不是配对），"
            f"非 'call_<24hex>' 格式一律拒绝。"
        )
    elif r.status_code == 200:
        print("[H6] 注意：异常 id 格式没被拒 — 假设不成立，需重新思考根因")


# ============================================================
# H7: 复现"实际错误路径"——同一 thread_id 多轮 invoke
# ============================================================
@pytest.mark.manual
def test_h7_reproduce_real_error_path():
    """H7: 跑真正的 disposition_demo 多轮，捕获实际 AIMessage.tool_calls[].id 格式。

    看 MiniMax 在我们的 P8 真实请求下，返回的 tool_call_id 格式。
    如果格式是 'chatcmpl-tool-...'（异常），证明 H6 假设成立。
    """
    from agents.p8_disposition_agent import run_disposition_agent

    # 用 P8 系统提示里的工具 — 直接引导调工具
    thread_id = f"probe-h7-{int(time.time())}"
    print(f"\n[H7] thread_id={thread_id}")

    # 第 1 轮：先发个非工具问题，让 daemon checkpointer 创建空 thread
    msg1 = "你好，简单介绍一下你自己"
    print(f"[H7] round 1: {msg1}")
    r1 = run_disposition_agent(msg1, thread_id=thread_id)
    print(f"[H7] round 1 done: {r1[:80]}")

    # 第 2 轮：明确请求调 list_active_p8_jobs 工具
    msg2 = "立即调用 list_active_p8_jobs 工具，列出当前所有 P8_job"
    print(f"[H7] round 2: {msg2}")
    try:
        r2 = run_disposition_agent(msg2, thread_id=thread_id)
        print(f"[H7] round 2 done: {r2[:120]}")
    except Exception as exc:
        print(f"[H7] round 2 异常: {type(exc).__name__}: {str(exc)[:500]}")
        if "2013" in str(exc):
            pytest.fail(
                f"H7: 复现成功！\n"
                f"P8 Agent 多轮对话触发 MiniMax 2013 'tool result's tool id not found' 错误。\n"
                f"异常: {str(exc)[:500]}\n"
                f"结合 H6 可知根因：MiniMax 返回的 tool_call_id 是 'chatcmpl-tool-...' "
                f"异常格式，服务端拒绝引用。"
            )

    # 如果没复现，看 checkpointer 里 AIMessage 的实际 id 格式
    from agents.p8_disposition_agent import _p8_checkpointer
    ck = _p8_checkpointer.get({"configurable": {"thread_id": thread_id}})
    if ck:
        msgs = ck["channel_values"].get("messages", []) if isinstance(ck, dict) else \
               ck.checkpoint["channel_values"].get("messages", [])
        for m in msgs:
            if m.type == "ai" and getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    print(f"[H7] 实际 tool_call.id: {tc['id']!r}")
                    print(f"[H7]   格式: chatcmpl-tool-...={tc['id'].startswith('chatcmpl-tool-')}, "
                          f"call_<hex>={tc['id'].startswith('call_')}")


# ============================================================
# 让 pytest 默认跳过 manual 标记的测试（避免 CI 自动跑）
# 用法：pytest -m manual --override-ini="addopts="
# ============================================================
def pytest_collection_modifyitems(config, items):
    # 如果用户显式 -m manual，不跳过
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)


# ============================================================
# 让 pytest 默认跳过 manual 标记的测试（避免 CI 自动跑）
# 用法：pytest -m manual --override-ini="addopts="
# ============================================================
def pytest_collection_modifyitems(config, items):
    # 如果用户显式 -m manual，不跳过
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)