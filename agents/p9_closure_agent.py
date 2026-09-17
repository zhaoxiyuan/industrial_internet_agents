"""P9 智能体（v2：纯文本关闭理由生成）

2026-09-17 重构：
- **无 tool** —— P9 仅产出纯文本关闭理由，写入 state.review.p9_opinion_text
- **触发场景**：record_closure_review(decision="approved") 关闭审核时，业务动作
  自动同步调 run_p9_closure_review(job_id)，把 P9 输出文本写入卡片 P9 段
- **不允许**：P9 不暴露任何 tool（不能写状态机 / 不能查 P7 / 不能归档）。
  唯一职责 = 输入作业快照 → 输出 ≤500 字关闭理由
- 旧蓝图版（closure_status / closure_verify / closure_report / closure_close）已废弃
- 保留 closure_demo 签名 → Gradio / chat_reply 兼容入口（输入任意作业摘要）
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from .model.chat_model import create_chat_model_with_logging, get_llm_params
from .utils.agent_utils import extract_output
from .utils.logging_handler import get_agent_config
from .utils.system_prompt import load_system_prompt


logger = logging.getLogger("p9_closure_agent_v2")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# Agent 工厂（无 tool 纯对话）
# ============================================================
def create_closure_agent():
    """P9 智能体（v2：无 tool；纯文本输出）。

    ★ 触发方式：business_actions.record_closure_review(approved) 内部同步调用。
    ★ 输入：作业快照（events + materials + review history）
    ★ 输出：≤500 字关闭理由文本（写入 state.review.p9_opinion_text + 卡片 P9 段）
    """
    llm = create_chat_model_with_logging("P9")
    # ★ 无 tool —— 这是 P9 v2 的核心约束
    return create_agent(
        model=llm,
        tools=[],
        system_prompt=load_system_prompt("P9"),
    )


# ============================================================
# 同步入口：run_p9_closure_review
# ============================================================
def run_p9_closure_review(job_id: str) -> str:
    """同步调 P9 产出关闭理由文本。

    调用方：P8P9.business_actions.record_closure_review(decision="approved")
    失败语义：抛任何异常都会被 caller 包 try/except，不阻断关闭流程。

    Args:
        job_id: P8P9 作业 ID

    Returns:
        str: P9 输出的纯文本（≤500 字）。写入 state.review.p9_opinion_text。
    """
    from P8P9.state_machine import ClosureService, StateNotFound

    # ── 1. 读 state 摘录（P9 输入） ──
    svc = ClosureService()
    try:
        state = svc.get_state(job_id)
    except StateNotFound:
        logger.warning(f"P9: job_id={job_id} 不存在；返回空文本")
        return ""

    snapshot = _build_p9_snapshot(state)

    # ── 2. 构造 P9 输入消息 ──
    message = (
        f"请对 P8P9 作业 {job_id} 生成关闭理由（≤500 字），"
        f"作为卡片 P9 段文字写入飞书卡片供作业人员查阅。\n\n"
        f"作业快照：\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}"
    )

    # ── 3. 同步 invoke P9 agent ──
    agent = create_closure_agent()
    config = get_agent_config(
        thread_id=f"p9-{job_id}",
        agent_name="P9",
        llm_params=get_llm_params(),
    )
    try:
        result = agent.invoke({"messages": [HumanMessage(content=message)]}, config)
        text = extract_output(result)
    except Exception as exc:
        logger.exception(f"P9 invoke 失败：job_id={job_id} err={exc}")
        raise

    logger.info(f"P9 关闭理由生成完成：job_id={job_id} len={len(text or '')}")
    return (text or "").strip()


def _build_p9_snapshot(state: dict) -> dict:
    """从 state 摘录 P9 输入字段：
    - events: 风险事件摘要（去 PII）
    - materials: 处置材料（review_text + submissions 计数）
    - review: 审核历史（决策 + comment 摘要）
    - 卡片上下文：job_status / display_risk_level / closed_at / closed_by
    """
    materials = state.get("materials") or {}
    review = state.get("review") or {}

    events_brief = []
    for e in state.get("events", []) or []:
        events_brief.append({
            "risk_event_id": e.get("risk_event_id"),
            "risk_level": e.get("risk_level"),
            "risk_level_name": e.get("risk_level_name"),
            "event_type": e.get("event_type"),
            "risk_basis": (e.get("risk_basis") or "")[:200],
            "involved_persons_count": len(e.get("involved_persons") or []),
        })

    review_history_brief = []
    for h in review.get("history", []) or []:
        review_history_brief.append({
            "decision": h.get("decision"),
            "comment": (h.get("comment") or "")[:150],
            "at": h.get("at"),
        })

    return {
        "job_id": state.get("job_id"),
        "job_status": state.get("job_status"),
        "display_risk_level": state.get("display_risk_level"),
        "created_at": state.get("created_at"),
        "closed_at": state.get("closed_at"),
        "events": events_brief,
        "events_count": len(events_brief),
        "materials": {
            "submissions_count": len(materials.get("submissions", [])),
            "latest_review_text": (materials.get("latest_review_text") or "")[:300],
            "latest_at": materials.get("latest_at"),
        },
        "review": {
            "history_count": len(review_history_brief),
            "history": review_history_brief,
            "last_decision": review.get("last_decision"),
            "last_comment": (review.get("last_comment") or "")[:200],
        },
    }


# ============================================================
# Gradio / chat_reply 兼容入口
# ============================================================
def closure_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface / chat_reply 兼容入口。

    调用方传 message（描述作业摘要），P9 输出关闭理由文本。
    不暴露任何 tool；纯对话。
    """
    # closure_demo 期望 message 是用户消息；
    # 这里包装成 P9 标准化输入
    wrapped = (
        f"请基于以下用户描述生成 ≤500 字关闭理由文本：\n\n{message}"
    )
    agent = create_closure_agent()
    config = get_agent_config(
        thread_id="default",
        agent_name="P9",
        llm_params=get_llm_params(),
    )
    result = agent.invoke({"messages": [HumanMessage(content=wrapped)]}, config)
    return extract_output(result)


__all__ = [
    "create_closure_agent",
    "run_p9_closure_review",
    "closure_demo",
]