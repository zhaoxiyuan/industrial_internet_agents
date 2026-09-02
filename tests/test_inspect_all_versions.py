"""彻底检查 MemorySaver 里所有 checkpoint versions 的 state.messages 内容。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.mark.manual
def test_inspect_all_checkpoint_versions():
    """列出 MemorySaver 里同一 thread_id 的所有 checkpoint versions，看 messages 演进。"""
    from agents.p8_disposition_agent import run_disposition_agent, _p8_checkpointer

    thread_id = f"allver-{os.getpid()}"
    cfg = {"configurable": {"thread_id": thread_id}}

    # Round 1
    r1 = run_disposition_agent("你好", thread_id=thread_id)
    print(f"\n[ALLVER] r1 done: {r1[:60]}")

    # 列出所有 checkpoint versions
    print("\n[ALLVER] === MemorySaver 所有 versions ===")
    try:
        versions = list(_p8_checkpointer.list(cfg))
    except Exception as e:
        print(f"[ALLVER] list() 失败: {e}")
        versions = []

    print(f"[ALLVER] versions count: {len(versions)}")

    for v_idx, ck_meta in enumerate(versions):
        # ck_meta 通常含 {'configurable': {'thread_id', 'checkpoint_id', 'checkpoint_ns'}, ...}
        print(f"\n[ALLVER] --- version {v_idx}: {ck_meta} ---")

        # 拿这个 version 的完整 checkpoint
        try:
            ck_full = _p8_checkpointer.get(ck_meta)
        except Exception as e:
            print(f"[ALLVER] get() 失败: {e}")
            continue

        # 解析 messages
        if isinstance(ck_full, dict):
            channel_values = ck_full.get("channel_values", {})
            checkpoint_meta = ck_full.get("metadata", {})
            msgs = channel_values.get("messages", [])
            ver_id = channel_values.get("langgraph_checkpoint_version", "?")
        else:
            channel_values = ck_full.checkpoint.get("channel_values", {})
            checkpoint_meta = ck_full.metadata
            msgs = channel_values.get("messages", [])
            ver_id = channel_values.get("langgraph_checkpoint_version", "?")

        step = checkpoint_meta.get("step", "?") if isinstance(checkpoint_meta, dict) else "?"
        print(f"[ALLVER] step={step}, messages={len(msgs)}")
        for i, m in enumerate(msgs):
            t = type(m).__name__
            extra = ""
            if t == "AIMessage":
                tcs = getattr(m, "tool_calls", None) or []
                extra = f" tool_calls_count={len(tcs)} ids={[tc.get('id') for tc in tcs]}"
            elif t == "ToolMessage":
                extra = f" tool_call_id={getattr(m, 'tool_call_id', None)!r}"
            content = str(getattr(m, "content", ""))[:60]
            print(f"  [{i}] {t:<12} content={content!r}{extra}")

    # Round 2（看 add_messages 是否正确累积）
    print("\n[ALLVER] === Round 2 触发 ===")
    try:
        r2 = run_disposition_agent(
            "立即调用 list_active_p8_jobs 工具，列出所有 P8_job",
            thread_id=thread_id,
        )
        print(f"[ALLVER] r2: {r2[:80]}")
    except Exception as exc:
        print(f"[ALLVER] r2 异常: {type(exc).__name__}: {str(exc)[:200]}")

    print("\n[ALLVER] === Round 2 后所有 versions ===")
    try:
        versions = list(_p8_checkpointer.list(cfg))
    except Exception as e:
        print(f"[ALLVER] list() 失败: {e}")
        versions = []
    print(f"[ALLVER] versions count: {len(versions)}")
    for v_idx, ck_meta in enumerate(versions):
        try:
            ck_full = _p8_checkpointer.get(ck_meta)
        except Exception as e:
            continue
        if isinstance(ck_full, dict):
            channel_values = ck_full.get("channel_values", {})
            checkpoint_meta = ck_full.get("metadata", {})
            msgs = channel_values.get("messages", [])
        else:
            channel_values = ck_full.checkpoint.get("channel_values", {})
            checkpoint_meta = ck_full.metadata
            msgs = channel_values.get("messages", [])
        step = checkpoint_meta.get("step", "?") if isinstance(checkpoint_meta, dict) else "?"
        print(f"\n[ALLVER] --- version {v_idx} step={step}, messages={len(msgs)} ---")
        for i, m in enumerate(msgs):
            t = type(m).__name__
            extra = ""
            if t == "AIMessage":
                tcs = getattr(m, "tool_calls", None) or []
                extra = f" tool_calls_count={len(tcs)} ids={[tc.get('id') for tc in tcs]}"
            elif t == "ToolMessage":
                extra = f" tool_call_id={getattr(m, 'tool_call_id', None)!r}"
            content = str(getattr(m, "content", ""))[:60]
            print(f"  [{i}] {t:<12} content={content!r}{extra}")


# ============================================================
def pytest_collection_modifyitems(config, items):
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)