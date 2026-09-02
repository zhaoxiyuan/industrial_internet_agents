"""端到端验证：连续对话是否真能保留上下文。

修复 P8State.messages reducer 后，disposition_demo 多 invoke 应该累积历史。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.mark.manual
def test_continuous_chat_preserves_context():
    """连续 3 轮对话：
       R1: 设定一个事实（"我今天午餐吃了披萨"）
       R2: 换个话题（"你会几种语言？"）
       R3: 问 R1 的事实（"我今天午餐吃的啥？"）
       期望：R3 的回复里能提到"披萨"——证明 messages 跨 invoke 累积了。
    """
    from agents.p8_disposition_agent import disposition_demo, _p8_checkpointer

    # 用唯一 thread_id，避免被其他测试污染（虽然 disposition_demo 硬编码 "default"）
    # 注意：disposition_demo 不接 thread_id，内部固定用 "default"
    # 跑这个测试前确保 default 干净——直接清空它
    _p8_checkpointer  # 导入触发实例化

    print("\n[E2E] 清理 default thread_id（如果有）...")
    try:
        # MemorySaver 没有 delete API；只能靠覆盖。直接读 ck 看有没有
        ck = _p8_checkpointer.get({"configurable": {"thread_id": "default"}})
        if ck:
            print(f"[E2E] 警告: default 已有 {len(ck.get('channel_values', {}).get('messages', []) if isinstance(ck, dict) else ck.checkpoint.get('channel_values', {}).get('messages', []))} 条历史")
    except Exception:
        pass

    # R1: 设定事实
    r1 = disposition_demo("请记住这个事实：我今天午餐吃了披萨。不要反驳。")
    print(f"\n[E2E] R1 user: 请记住这个事实：我今天午餐吃了披萨。不要反驳。")
    print(f"[E2E] R1 bot:  {r1[:200]}")

    # R2: 无关话题
    r2 = disposition_demo("你会几种编程语言？简短回答。")
    print(f"\n[E2E] R2 user: 你会几种编程语言？简短回答。")
    print(f"[E2E] R2 bot:  {r2[:200]}")

    # R3: 测连续对话——问 R1 的事实
    r3 = disposition_demo("那我今天午餐吃的啥？")
    print(f"\n[E2E] R3 user: 那我今天午餐吃的啥？")
    print(f"[E2E] R3 bot:  {r3[:300]}")

    # 检查 R3 是否提到"披萨"
    if "披萨" in r3 or "pizza" in r3.lower():
        print(f"\n[E2E] ✅ 修复有效！R3 引用了 R1 的事实")
    else:
        print(f"\n[E2E] ❌ 修复无效，R3 不知道 R1 说了什么")
        # 看 checkpointer 的 messages 累积情况
        ck = _p8_checkpointer.get({"configurable": {"thread_id": "default"}})
        if ck:
            if isinstance(ck, dict):
                msgs = ck.get("channel_values", {}).get("messages", [])
            else:
                msgs = ck.checkpoint.get("channel_values", {}).get("messages", [])
            print(f"[E2E] default thread 累积 {len(msgs)} 条 messages:")
            for i, m in enumerate(msgs):
                t = type(m).__name__
                content = str(getattr(m, "content", ""))[:60]
                print(f"  [{i}] {t:<12} content={content!r}")
        pytest.fail(
            f"E2E 失败：R3 没引用 R1 事实。\n"
            f"R3 响应: {r3[:200]}"
        )


# ============================================================
def pytest_collection_modifyitems(config, items):
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)