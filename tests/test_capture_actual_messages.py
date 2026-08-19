"""拦截 LLM 调用，捕获 LangGraph 第二轮实际发给 MiniMax 的 messages。

目的：明确看到 2013 触发时，messages 列表的真实内容（AIMessage 有没有？SystemMessage 有没有？）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.mark.manual
def test_capture_real_messages_sent_to_llm():
    """在 langchain_openai ChatOpenAI 上 monkeypatch _generate，捕获每次调用的完整 messages。"""
    from langchain_openai import ChatOpenAI
    from agents.p8_disposition_agent import run_disposition_agent

    captured_calls = []

    original_generate = ChatOpenAI._generate
    original_agenerate = ChatOpenAI._agenerate if hasattr(ChatOpenAI, "_agenerate") else None

    def patched_generate(self, messages, stop=None, **kwargs):
        # 捕获当前 messages 的精简版（只保留 type / tool_call_id / tool_calls / content 前 50）
        captured = []
        for m in messages:
            if hasattr(m, "type"):
                if m.type == "tool":
                    captured.append({
                        "type": "tool",
                        "name": getattr(m, "name", None),
                        "tool_call_id": getattr(m, "tool_call_id", None),
                        "content_preview": str(getattr(m, "content", ""))[:50],
                    })
                elif m.type == "ai":
                    captured.append({
                        "type": "ai",
                        "tool_calls_count": len(getattr(m, "tool_calls", []) or []),
                        "tool_call_ids": [tc.get("id") for tc in (getattr(m, "tool_calls", []) or [])],
                        "content_preview": str(getattr(m, "content", ""))[:50],
                    })
                else:
                    captured.append({
                        "type": m.type,
                        "content_preview": str(getattr(m, "content", ""))[:50],
                    })
        captured_calls.append(captured)
        return original_generate(self, messages, stop=stop, **kwargs)

    ChatOpenAI._generate = patched_generate
    try:
        # round 1
        from agents.p8_disposition_agent import _p8_checkpointer
        thread_id = f"capture-{os.getpid()}-{id(captured_calls)}"
        print(f"\n[CAPTURE] thread_id={thread_id}")

        print("\n[CAPTURE] round 1: 你好")
        r1 = run_disposition_agent("你好，简单介绍一下你自己", thread_id=thread_id)
        print(f"[CAPTURE] r1: {r1[:80]}")

        print("\n[CAPTURE] round 2: 调 list_active_p8_jobs")
        try:
            r2 = run_disposition_agent(
                "立即调用 list_active_p8_jobs 工具，列出所有 P8_job",
                thread_id=thread_id,
            )
            print(f"[CAPTURE] r2: {r2[:80]}")
        except Exception as exc:
            print(f"[CAPTURE] r2 异常: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        ChatOpenAI._generate = original_generate

    # 打印捕获的所有 LLM 调用
    print(f"\n[CAPTURE] 共捕获 {len(captured_calls)} 次 LLM 调用")
    for call_idx, msgs in enumerate(captured_calls):
        print(f"\n[CAPTURE] === LLM 调用 #{call_idx + 1}: messages 共 {len(msgs)} 条 ===")
        for i, m in enumerate(msgs):
            extra = ""
            if m["type"] == "ai" and m.get("tool_call_ids"):
                extra = f" tool_ids={m['tool_call_ids']}"
            elif m["type"] == "tool":
                extra = f" tool_call_id={m['tool_call_id']!r}"
            print(f"  [{i}] {m['type']:<10} content={m['content_preview']!r}{extra}")

    # 关键诊断：round 2 后（即第 3+ 次 LLM 调用），messages 里
    # - 有没有 AIMessage(tool_calls=[id=xxx]) 在 ToolMessage(tool_call_id=xxx) 之前？
    has_pair = False
    for call_idx, msgs in enumerate(captured_calls):
        # 找 ToolMessage 前面最近的 AIMessage
        for i, m in enumerate(msgs):
            if m["type"] == "tool":
                tool_call_id = m["tool_call_id"]
                # 向前找最近的 ai
                for j in range(i - 1, -1, -1):
                    if msgs[j]["type"] == "ai":
                        ai_ids = msgs[j].get("tool_call_ids") or []
                        if tool_call_id in ai_ids:
                            has_pair = True
                        break
    print(f"\n[CAPTURE] ToolMessage 前存在配对 AIMessage? {has_pair}")


# ============================================================
def pytest_collection_modifyitems(config, items):
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)