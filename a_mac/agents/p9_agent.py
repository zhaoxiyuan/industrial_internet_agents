"""P9 智能体（v2 合并：审核 + 关闭文案）

2026-09-17 合并：
- 把 P9 的两个独立职责合并到同一个文件，但保持两个 agent 工厂 + 两个入口函数
  + 两个 system_prompt + 两套 logger 完全独立（不合并函数 / 不合并逻辑）
- 两个 agent 职责正交（详见各工厂 docstring）：
  - 审核 agent：run_p9_materials_audit → 纯文本审核意见(comment + confidence + evidence_check)写入
    state.review.p9_opinion（materials_in_audit 阶段；不推 job_status）
  - 关闭文案 agent：run_p9_closure_review → 纯文本关闭理由写入卡片 P9 段（approved → closed 终态）

★ 为什么合并到一个 .py 文件：
- 两个 agent 都属于 P9 阶段（v2.1 设计文档 §2.0.1「2 agent + 3 service」中 P9 闭环的核心组件）
- 两个 agent 都无 tool（纯对话 LLM）—— 共用 chat_model / extract_output / system_prompt loader
- agents/ 目录按"阶段"组织（p1_*.py / p2_*.py / ... / p10_*.py），两个 P9 文件违反单阶段单文件原则
- 调用方 4 处（audit_scheduler / business_actions / main_agent / cli）都引用 P9 阶段入口

★ 为什么不合并函数 / 工厂 / prompt：
- 审核 = 审核意见类（结构化 JSON + 反幻觉 + **不**推动状态机）；关闭 = 文案类（≤500 字纯文本）
- 触发时机不同：审核在 materials_in_audit 阶段，关闭在 approved → closed 终态
- 输出 schema 不同：审核必须严格 JSON（service 解析后写入 review.p9_opinion）；关闭只要纯文本
- 合并后 prompt 既要管审核意见又要管文案 → LLM 易混杂 comment 与文案字段 → JSON 解析失败

★ v2.1 设计修正（2026-09-17）：
- P9 审核 agent **没有决策权限**：只输出 comment（可包含「建议驳回 / 建议补充 / 建议人工复核」
  等意见，但不直接拒绝）
- P9 审核 agent **不推 job_status** —— job_status 保持 materials_in_audit，等待人工
  record_closure_review(decision="approved"|"rejected") 决策
- 决策完全由人工 record_closure_review 控制；P9 只提供参考意见
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from .model.chat_model import create_chat_model_with_logging, get_llm_params
from .utils.agent_utils import extract_output
from .utils.logging_handler import get_agent_config
from .utils.system_prompt import load_system_prompt


# ============================================================
# Logger：保留两个独立 logger 名（日志 grep 友好，不破坏现有日志检索）
# ============================================================
_audit_logger = logging.getLogger("p9_audit_agent_v2")
_closure_logger = logging.getLogger("p9_closure_agent_v2")

for _lg in (_audit_logger, _closure_logger):
    if not _lg.handlers:
        _handler = logging.StreamHandler()
        _handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        _lg.addHandler(_handler)
        _lg.setLevel(logging.INFO)


# ============================================================
# 工厂 1：P9 审核 agent（materials 审核意见，不推状态）
# ============================================================
def create_audit_agent():
    """P9 审核 agent（v2：无 tool；纯文本意见输出）。

    ★ 触发方式：P8P9/services/audit_scheduler._run_p9_audit_agent（daemon thread）同步 invoke。
    ★ 输入：作业快照 + materials.submissions 详情
    ★ 输出：JSON 审核意见 = {comment, confidence, evidence_check}（写入 review.p9_opinion）

    ★ v2.1 约束（2026-09-17）：
    - P9 audit **没有决策权限** —— comment 可包含「建议驳回 / 建议补充 / 建议人工复核」
      等意见，但不直接拒绝
    - P9 audit **不推 job_status** —— job_status 保持 materials_in_audit
    - 所有决策走人工 record_closure_review
    """
    llm = create_chat_model_with_logging("P9-Audit")
    # ★ 无 tool —— P9 audit 的核心约束（不能查 P7 / 不能改状态 / 不能归档）
    return create_agent(
        model=llm,
        tools=[],
        system_prompt=load_system_prompt("P9_AUDIT"),
    )


# ============================================================
# 工厂 2：P9 关闭文案 agent（纯文本关闭理由）
# ============================================================
def create_closure_agent():
    """P9 关闭文案 agent（v2：无 tool；纯文本输出）。

    ★ 触发方式：P8P9.business_actions.record_closure_review(decision="approved") 内部同步调用。
    ★ 输入：作业快照（events + materials + review history）
    ★ 输出：≤500 字关闭理由文本（写入 state.review.p9_opinion_text + 卡片 P9 段）
    """
    llm = create_chat_model_with_logging("P9")
    # ★ 无 tool —— P9 v2 的核心约束
    return create_agent(
        model=llm,
        tools=[],
        system_prompt=load_system_prompt("P9"),
    )


# ============================================================
# 入口 1：run_p9_materials_audit（审核）
# ============================================================
def run_p9_materials_audit(job_id: str) -> Dict[str, Any]:
    """同步审核 job 当前 materials.submissions，产出纯文本审核意见（不推 job_status）。

    调用方：P8P9.services.audit_scheduler._run_p9_audit_agent（daemon thread）
    失败语义：抛任何异常都被 caller 包 try/except（service 层兜底，job_status 不动）。

    Args:
        job_id: P8P9 作业 ID（必须是 17 位 ERP 工单号或合法 P8P9 job_id）

    Returns:
        Dict（写入 state.review.p9_opinion）:
            {
                "comment":        str (≤500 字；可写「建议驳回 / 建议补充 / 建议人工复核」等意见),
                "confidence":     float (0-1),
                "evidence_check": list[{evidence_id, ok: bool, reason: str}],
                "audited_at":     iso8601,
                "auditor":        "P9-AuditAgent",
                "is_mock":        False,
            }

    Raises:
        StateNotFound: job_id 不存在
        IllegalTransition: 当前 job_status != 'materials_in_audit'

    ★ v2.1 设计约束（2026-09-17 修正）：
        - P9 audit **没有拒绝权限**，只有写理由的权限
        - P9 audit **不推 job_status** —— job_status 保持 materials_in_audit
        - 所有决策走人工 record_closure_review（approved / rejected）
        - P9 comment 可以包含「建议驳回」等意见，但**不**直接改状态
    """
    from P8P9.state_machine import ClosureService, StateNotFound, IllegalTransition

    svc = ClosureService()
    try:
        state = svc.get_state(job_id)
    except StateNotFound:
        _audit_logger.warning(f"P9 audit: job_id={job_id} 不存在")
        raise

    if state.get("job_status") != "materials_in_audit":
        raise IllegalTransition(
            f"P9 audit 要求 job_status='materials_in_audit'，"
            f"实际={state.get('job_status')!r}"
        )

    # ── 1. 构造 P9 输入快照 ──
    snapshot = _build_audit_snapshot(state)

    # ── 2. 同步 invoke LLM ──
    user_input = (
        f"请审核 P8P9 作业 {job_id} 的最新处置材料，按 P9_AUDIT_SYSTEM_PROMPT 输出 JSON 审核意见：\n\n"
        f"作业快照：\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}"
    )

    agent = create_audit_agent()
    config = get_agent_config(
        thread_id=f"p9-audit-{job_id}",
        agent_name="P9-Audit",
        llm_params=get_llm_params(),
    )

    raw_text = ""
    try:
        result = agent.invoke({"messages": [HumanMessage(content=user_input)]}, config)
        raw_text = extract_output(result)
    except Exception as exc:
        _audit_logger.exception(f"P9 audit LLM 失败：job_id={job_id} err={exc}")
        raise

    _audit_logger.info(
        f"P9 audit LLM 原始输出：job_id={job_id} raw_len={len(raw_text or '')}"
    )

    # ── 3. 解析审核意见 JSON（纯文本意见，无 verdict） ──
    opinion = _parse_audit_output(raw_text, job_id)

    # ── 4. 落 review.p9_opinion（patch_fields 不动 job_status） ──
    now = datetime.now(timezone.utc).isoformat()
    actor = {"open_id": "P9-AuditAgent", "name": "P9 Materials Audit Agent"}

    review_obj = dict(state.get("review") or {})
    review_obj["p9_opinion"] = opinion
    review_obj["p9_audited_at"] = now
    review_obj["p9_auditor"] = "P9-AuditAgent"

    state = svc.patch_fields(
        job_id,
        actor=actor,
        expected_version=None,
        review=review_obj,
    )
    _audit_logger.info(
        f"P9 audit 写 p9_opinion：job_id={job_id} confidence={opinion['confidence']:.2f} "
        f"comment_len={len(opinion['comment'])}"
    )

    # ── 5. ★ 不推 job_status（v2.1 设计：P9 没有决策权限） ──
    _audit_logger.info(
        f"P9 audit 完成（不推状态）：job_id={job_id} "
        f"job_status 保持 materials_in_audit，等待人工 record_closure_review 决策"
    )

    # ── 6. 触发卡片刷新 ──
    try:
        from P8P9.services.card_render import update_job_card
        update_job_card(job_id, state.get("version", 0))
    except Exception as e:
        _audit_logger.warning(f"卡片刷新失败（非致命）：job_id={job_id} err={e}")

    return opinion


# ============================================================
# 入口 2：run_p9_closure_review（关闭文案）
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
        _closure_logger.warning(f"P9: job_id={job_id} 不存在；返回空文本")
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
        _closure_logger.exception(f"P9 invoke 失败：job_id={job_id} err={exc}")
        raise

    # 2026-09-17：剥离 MiniMax-M3 等 reasoning 模型的 <think>...</think> 块。
    # 不剥的话整个推理链会被写进 review.p9_opinion_text（污染字段、JSON 也会失败）。
    if text and "</think>" in text:
        end_think = text.rfind("</think>")
        text = text[end_think + len("</think>"):].strip()
        _closure_logger.info(
            f"P9 关闭理由剥离 thinking 块：job_id={job_id} clean_len={len(text or '')}"
        )

    _closure_logger.info(f"P9 关闭理由生成完成：job_id={job_id} len={len(text or '')}")
    return (text or "").strip()


# ============================================================
# 内部辅助
# ============================================================
def _build_audit_snapshot(state: dict) -> dict:
    """构造 P9 审核输入：events 详情 + materials.submissions 完整内容 + review 历史。"""
    materials = state.get("materials") or {}

    events_brief: List[Dict[str, Any]] = []
    for e in state.get("events", []) or []:
        events_brief.append({
            "risk_event_id": e.get("risk_event_id"),
            "risk_level": e.get("risk_level"),
            "risk_level_name": e.get("risk_level_name"),
            "event_type": e.get("event_type"),
            "risk_basis": (e.get("risk_basis") or "")[:300],
            "involved_persons_count": len(e.get("involved_persons") or []),
        })

    submissions_brief: List[Dict[str, Any]] = []
    for sub in materials.get("submissions", []):
        submissions_brief.append({
            "submitted_by": sub.get("submitted_by"),
            "submitted_at": sub.get("submitted_at"),
            "review_text": (sub.get("review_text") or "")[:500],
            "submissions": sub.get("submissions") or [],
            "event_ids": sub.get("event_ids") or [],
        })

    return {
        "job_id": state.get("job_id"),
        "job_status": state.get("job_status"),
        "display_risk_level": state.get("display_risk_level"),
        "events": events_brief,
        "events_count": len(events_brief),
        "materials": {
            "submissions": submissions_brief,
            "submissions_count": len(submissions_brief),
            "latest_review_text": (materials.get("latest_review_text") or "")[:500],
            "latest_at": materials.get("latest_at"),
        },
        "review": {
            "history_count": len((state.get("review") or {}).get("history", [])),
            "last_decision": (state.get("review") or {}).get("last_decision"),
            "last_comment": ((state.get("review") or {}).get("last_comment") or "")[:200],
        },
    }


def _build_p9_snapshot(state: dict) -> dict:
    """从 state 摘录 P9 关闭文案输入字段：
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


def _parse_audit_output(raw_text: str, job_id: str) -> Dict[str, Any]:
    """解析 LLM 输出的审核意见 JSON（纯文本意见，无 verdict）。

    v2.1 设计（2026-09-17 修正）：
    - P9 audit **没有拒绝权限**，只输出 comment（审核意见）+ confidence + evidence_check
    - comment 可包含「建议驳回 / 建议补充 / 建议人工复核」等意见，但**不**直接拒绝
    - 所有决策走人工 record_closure_review
    - LLM 输出 JSON 解析失败 → 兜底空 comment + confidence=0.5 + 「建议人工复核」意见

    兜底语义：LLM 不可靠时，给出保守意见让人工复核，不自动拒绝。
    """
    now = datetime.now(timezone.utc).isoformat()
    text = (raw_text or "").strip()

    # 尝试剥离 markdown code block（LLM 偶尔会包 ```json ... ```）
    if text.startswith("```"):
        # 找第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        else:
            text = ""
    elif text.startswith("<think>") or "<think>" in text[:50]:
        # 2026-09-17：MiniMax-M3 等 reasoning 模型会先输出 <think>...</think> 推理块，
        # 然后才是真正的 JSON。必须剥离 thinking 块否则 json.loads 失败。
        # 找最后一个 </think> 之后的位置；如没有则退回 markdown 剥离。
        end_think = text.rfind("</think>")
        if end_think >= 0:
            text = text[end_think + len("</think>"):].strip()
        # 剥离后再找 { 和 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]

    parsed: Dict[str, Any] = {}
    if text:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError) as e:
            _audit_logger.warning(
                f"P9 audit 输出不是合法 JSON：job_id={job_id} "
                f"raw={text[:200]!r} err={e} → 兜底：建议人工复核"
            )
            parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    # ── confidence ──
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= float(confidence) <= 1):
        confidence = 0.5
    else:
        confidence = float(confidence)

    # ── comment（审核意见；可包含「建议驳回」等建议，但不直接拒绝） ──
    comment = str(parsed.get("comment") or "").strip()
    if not comment:
        comment = (
            f"P9 审核输出 comment 缺失或非标准结构；建议人工复核。"
        )
    comment = comment[:1000]  # 限长兜底

    # ── evidence_check ──
    evidence_check = parsed.get("evidence_check") or []
    if not isinstance(evidence_check, list):
        evidence_check = []

    # evidence_check 每项尽量是 dict 且含 evidence_id
    norm_evidence: List[Dict[str, Any]] = []
    for ev in evidence_check:
        if isinstance(ev, dict) and ev.get("evidence_id"):
            norm_evidence.append({
                "evidence_id": str(ev["evidence_id"]),
                "ok": bool(ev.get("ok", False)),
                "reason": str(ev.get("reason") or "")[:200],
            })

    return {
        "comment": comment,
        "confidence": confidence,
        "evidence_check": norm_evidence,
        "audited_at": now,
        "auditor": "P9-AuditAgent",
        "is_mock": False,
    }


# ============================================================
# Gradio / chat_reply 兼容入口
# ============================================================
def audit_demo(message: str, history: list = None) -> str:
    """P9 审核 demo（Gradio / chat_reply 兼容入口）。

    调用方传 message（描述作业），audit 模拟一次审核，输出 JSON 审核意见。
    不暴露任何 tool；纯对话。
    """
    agent = create_audit_agent()
    config = get_agent_config(
        thread_id="default",
        agent_name="P9-Audit",
        llm_params=get_llm_params(),
    )
    wrapped = (
        f"请基于以下用户描述审核作业材料，按 P9_AUDIT_SYSTEM_PROMPT 输出 JSON 审核意见\n"
        f"（comment + confidence + evidence_check；不要 verdict，不要决策）：\n\n"
        f"{message}"
    )
    result = agent.invoke({"messages": [HumanMessage(content=wrapped)]}, config)
    return extract_output(result)


def closure_demo(message: str, history: list = None) -> str:
    """P9 关闭文案 demo（Gradio / chat_reply 兼容入口）。

    调用方传 message（描述作业摘要），P9 输出关闭理由文本。
    不暴露任何 tool；纯对话。
    """
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
    # ── 审核 agent（决策类） ──
    "create_audit_agent",
    "run_p9_materials_audit",
    "audit_demo",
    # ── 关闭文案 agent（生成类） ──
    "create_closure_agent",
    "run_p9_closure_review",
    "closure_demo",
]
