"""
A5 智能体 — 最小可用 LangChain 定义
====================================

本文件仅做两件事:
  1. 从 .env 读取模型 API 等配置(config.py)
  2. 创建一个 LangChain ReAct Agent 工厂函数 build_agent()

不绑定 Tool、不定义 RiskCalibrator、不写 dedup 状态机
(那些都在 src/ 下单独实现)。

usage:
    from 智能体配置.agent import build_agent
    agent = build_agent()
    resp = agent.invoke({"input": "P7 是否还在动火区?"})
"""
from __future__ import annotations

from typing import Any, List, Optional

from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from .config import create_llm


# ============================================================
# 极简 ReAct Prompt(占位 — 后续在 src/ 接入完整 Prompt)
# ============================================================

REACT_PROMPT = PromptTemplate.from_template("""你是 A5 作业过程监测智能体。下面是你观察到的现场数据,请基于这些信息决策。

可用工具:{tools}
工具名称列表:{tool_names}

严格按以下格式回答:

Question: {input}
Thought: <你的思考>
Action: {{"action": "工具名", "action_input": "工具入参 JSON 字符串"}}
Observation: <工具返回>
... (循环 Thought / Action / Observation 直到能下结论)
Thought: 我已经收集到足够证据,给出最终结论。
Final Answer: <最终结论,中文,简洁>

Question: {input}
Thought:{agent_scratchpad}""")


# ============================================================
# Agent 工厂
# ============================================================

def build_agent(
    *,
    llm: Optional[BaseChatModel] = None,
    tools: Optional[List[Any]] = None,
    verbose: bool = False,
    **kwargs: Any,
) -> AgentExecutor:
    """
    创建 A5 智能体的 LangChain AgentExecutor。

    Args:
        llm:     默认从 .env 读取(见 config.create_llm)
        tools:   LangChain Tool 列表,默认空
        verbose: 是否打印推理过程
        kwargs:  透传给 AgentExecutor 的额外参数

    Returns:
        LangChain AgentExecutor 实例
    """
    llm = llm or create_llm()
    tools = tools or []

    agent = create_react_agent(
        llm=llm,
        tools=tools,
        prompt=REACT_PROMPT,
    )
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=verbose,
        handle_parsing_errors=True,
        max_iterations=6,
        **kwargs,
    )


__all__ = ["build_agent", "REACT_PROMPT"]
