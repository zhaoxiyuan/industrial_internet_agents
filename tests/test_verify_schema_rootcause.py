"""验证根因：P8State.messages 没用 Annotated[list, add_messages]。

构造一个对比实验：
  A) 用 LangChain 默认 AgentState（messages 有 add_messages reducer）→ 不报 2013
  B) 用 P8State（messages 是 list，无 reducer）→ 报 2013

如果 A 通过且 B 失败，确认根因是 schema 定义而非 LangChain bug。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.mark.manual
def test_a_default_agent_state_no_2013():
    """A: 用 LangChain 默认 AgentState（带 add_messages reducer）跑相同流程 → 不应报 2013。"""
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import MemorySaver

    @tool
    def list_active_p8_jobs() -> str:
        """列出当前所有 P8_job。"""
        return '{"active_p8_jobs": [], "note": "empty"}'

    # 用 LangChain 默认 schema（带 add_messages reducer）
    agent = create_agent(
        model=os.environ["OPENAI_MODEL"] if False else None,  # 占位
        tools=[list_active_p8_jobs],
        system_prompt="你是助手。",
    )

    # 用项目实际的 chat_model
    from agents.model.chat_model import create_chat_model_with_logging
    agent = create_agent(
        model=create_chat_model_with_logging("P8"),
        tools=[list_active_p8_jobs],
        system_prompt="你是助手。",
        checkpointer=MemorySaver(),
    )

    thread_id = f"agent-a-{os.getpid()}"
    cfg = {"configurable": {"thread_id": thread_id}}

    print(f"\n[A] thread_id={thread_id}")
    r1 = agent.invoke({"messages": [HumanMessage(content="你好")]}, cfg)
    print(f"[A] r1: {r1['messages'][-1].content[:80]}")

    # 第二轮：触发工具
    try:
        r2 = agent.invoke(
            {"messages": [HumanMessage(content="调用 list_active_p8_jobs 工具")]},
            cfg,
        )
        print(f"[A] r2: {r2['messages'][-1].content[:80]}")
        print("[A] 没报错 —— add_messages reducer 正确累积 messages")
    except Exception as exc:
        if "2013" in str(exc):
            pytest.fail(f"[A] 假设错 —— 仍然报 2013: {str(exc)[:200]}")
        else:
            raise


@pytest.mark.manual
def test_b_p8_state_triggers_2013():
    """B: 用 P8State（messages 是裸 list，无 reducer）跑相同流程 → 应报 2013。"""
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver

    from A7.schema import P8State
    from agents.model.chat_model import create_chat_model_with_logging

    # 找 list_active_p8_jobs 工具（从 P8 agent 注册表取）
    from agents.p8_disposition_agent import list_active_p8_jobs

    # Monkeypatch MemorySaver 实例避免与 P8 agent 共享
    fresh_checkpointer = MemorySaver()

    agent = create_agent(
        model=create_chat_model_with_logging("P8"),
        tools=[list_active_p8_jobs],
        system_prompt="你是助手。",
        state_schema=P8State,
        checkpointer=fresh_checkpointer,
    )

    thread_id = f"agent-b-{os.getpid()}"
    cfg = {"configurable": {"thread_id": thread_id}}

    print(f"\n[B] thread_id={thread_id}")
    r1 = agent.invoke({"messages": [HumanMessage(content="你好")]}, cfg)
    print(f"[B] r1: {r1['messages'][-1].content[:80]}")

    # 第二轮：触发工具
    try:
        r2 = agent.invoke(
            {"messages": [HumanMessage(content="调用 list_active_p8_jobs 工具")]},
            cfg,
        )
        print(f"[B] r2 没报错: {r2['messages'][-1].content[:80]}")
        print("[B] 假设错 —— P8State 也能跑")
    except Exception as exc:
        if "2013" in str(exc):
            print(f"[B] ✅ 已复现 2013: {str(exc)[:200]}")
            pytest.fail(
                f"[B] 根因验证成功！P8State 触发 2013 而默认 AgentState 不触发。"
                f"修复方案：把 A7/schema/p8_state.py:62 的 `messages: list` 改成 "
                f"`messages: Annotated[list, add_messages]`（从 langgraph.graph.message import add_messages）。"
                f"\n\n错误: {str(exc)[:300]}"
            )
        else:
            raise


# ============================================================
def pytest_collection_modifyitems(config, items):
    expr = config.option.markexpr or ""
    if "manual" in expr:
        return
    skip_manual = pytest.mark.skip(reason="manual probe (run with -m manual)")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)