"""检查 LangGraph state["messages"] 在 invoke 边界的真实内容。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.mark.manual
def test_inspect_state_messages_at_boundaries():
    """在 invoke 边界检查 state.messages 真实内容。"""
    from agents.p8_disposition_agent import run_disposition_agent, _p8_checkpointer

    thread_id = f"inspect-{os.getpid()}"
    cfg = {"configurable": {"thread_id": thread_id}}

    def dump_state(label):
        ck = _p8_checkpointer.get(cfg)
        if not ck:
            print(f"[INSPECT] {label}: NO CHECKPOINT")
            return
        # 不同版本可能返回 dict 或 Checkpoint 对象
        if isinstance(ck, dict):
            msgs = ck.get("channel_values", {}).get("messages", [])
        else:
            msgs = ck.checkpoint.get("channel_values", {}).get("messages", [])
        print(f"\n[INSPECT] {label}: state.messages 共 {len(msgs)} 条")
        for i, m in enumerate(msgs):
            t = type(m).__name__
            extra = ""
            if t == "AIMessage":
                tcs = getattr(m, "tool_calls", None) or []
                extra = f" tool_calls_count={len(tcs)} ids={[tc.get('id') for tc in tcs]}"
            elif t == "ToolMessage":
                extra = f" tool_call_id={getattr(m, 'tool_call_id', None)!r}"
            content = str(getattr(m, "content", ""))[:50]
            print(f"  [{i}] {t:<12} content={content!r}{extra}")

    # === Round 1 ===
    print(f"\n========== Round 1 ==========")
    dump_state("before round 1")
    r1 = run_disposition_agent("你好", thread_id=thread_id)
    print(f"r1: {r1[:80]}")
    dump_state("after round 1")

    # === Round 2 ===
    print(f"\n========== Round 2 ==========")
    dump_state("before round 2")
    try:
        r2 = run_disposition_agent(
            "立即调用 list_active_p8_jobs 工具，列出所有 P8_job",
            thread_id=thread_id,
        )
        print(f"r2: {r2[:80]}")
    except Exception as exc:
        print(f"r2 异常: {type(exc).__name__}: {str(exc)[:200]}")
    dump_state("after round 2")


# ============================================================
def pytest_collection_modifyitems(config, items):
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)