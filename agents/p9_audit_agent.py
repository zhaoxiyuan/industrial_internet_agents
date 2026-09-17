"""P9 审核智能体（v2：materials 审核 + verdict 推动）

2026-09-17 实现：
- 替换 P8P9/services/audit_scheduler.py 的 _run_p9_audit_mock 占位
- 触发场景：submit_rectification_materials → materials_in_audit → audit_scheduler
- 输入：state 摘录（events + materials.submissions 详情 + review 历史）
- 输出：verdict 字典（pass / reject / need_supplement + confidence + comment + evidence_check）
- 推动状态：
  - pass            → ready_to_close（人工终审 record_closure_review）
  - reject          → waiting_human_review（人工驳回）
  - need_supplement → waiting_human_review（人工要求补充）

★ 与 p9_closure_agent.run_p9_closure_review 的区别：
  - run_p9_closure_review：纯文本生成，写入 review.p9_opinion_text（用于卡片 P9 段）
  - run_p9_materials_audit：verdict 审核，推动 job_status（materials_in_audit → ready_to_close / waiting_human_review）
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


logger = logging.getLogger("p9_audit_agent_v2")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# Agent 工厂（无 tool；纯 LLM）
# ============================================================
def create_audit_agent():
    """P9 审核 agent（v2：无 tool；verdict 输出）。

    ★ 触发方式：audit_scheduler._run_p9_audit_agent（daemon thread）同步 invoke。
    ★ 输入：作业快照 + materials.submissions 详情
    ★ 输出：verdict JSON 字典
    """
    llm = create_chat_model_with_logging("P9-Audit")
    # ★ 无 tool —— 这是 P9 audit 的核心约束（不能查 P7 / 不能改状态 / 不能归档）
    return create_agent(
        model=llm,
        tools=[],
        system_prompt=load_system_prompt("P9_AUDIT"),
    )


# ============================================================
# 同步入口：run_p9_materials_audit
# ============================================================
def run_p9_materials_audit(job_id: str) -> Dict[str, Any]:
    """同步审核 job 当前 materials.submissions，产出 verdict + 推动 job_status。

    调用方：P8P9.services.audit_scheduler._run_p9_audit_agent（daemon thread）
    失败语义：抛任何异常都被 caller 包 try/except（service 层兜底，job_status 不动）。

    Args:
        job_id: P8P9 作业 ID（必须是 17 位 ERP 工单号或合法 P8P9 job_id）

    Returns:
        Dict（写入 state.review.p9_opinion）:
            {
                "verdict":        "pass" | "reject" | "need_supplement",
                "confidence":     float (0-1),
                "comment":        str (≤500 字),
                "evidence_check": list[{evidence_id, ok: bool, reason: str}],
                "audited_at":     iso8601,
                "auditor":        "P9-AuditAgent",
                "is_mock":        False,
            }

    Raises:
        StateNotFound: job_id 不存在
        IllegalTransition: 当前 job_status != 'materials_in_audit'
    """
    from P8P9.state_machine import ClosureService, StateNotFound, IllegalTransition

    svc = ClosureService()
    try:
        state = svc.get_state(job_id)
    except StateNotFound:
        logger.warning(f"P9 audit: job_id={job_id} 不存在")
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
        f"请审核 P8P9 作业 {job_id} 的最新处置材料，按 P9_AUDIT_SYSTEM_PROMPT 输出严格 JSON verdict：\n\n"
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
        logger.exception(f"P9 audit LLM 失败：job_id={job_id} err={exc}")
        raise

    logger.info(
        f"P9 audit LLM 原始输出：job_id={job_id} raw_len={len(raw_text or '')}"
    )

    # ── 3. 解析 verdict JSON ──
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
    logger.info(
        f"P9 audit 写 p9_opinion：job_id={job_id} verdict={opinion['verdict']!r} "
        f"confidence={opinion['confidence']:.2f}"
    )

    # ── 5. 推 job_status（按 verdict） ──
    new_status = _verdict_to_status(opinion["verdict"])
    if new_status != state.get("job_status"):
        try:
            state = svc.set_job_status(
                job_id, new_status,
                actor=actor, expected_version=None,
            )
            logger.info(
                f"P9 audit 推状态：job_id={job_id} "
                f"{opinion['verdict']!r} → job_status={new_status!r}"
            )
        except IllegalTransition as e:
            logger.warning(
                f"P9 audit 推状态失败（非致命）：job_id={job_id} "
                f"from {state.get('job_status')!r} to {new_status!r} err={e}"
            )

    # ── 6. 触发卡片刷新 ──
    try:
        from P8P9.services.card_render import update_job_card
        update_job_card(job_id, state.get("version", 0))
    except Exception as e:
        logger.warning(f"卡片刷新失败（非致命）：job_id={job_id} err={e}")

    return opinion


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


def _parse_audit_output(raw_text: str, job_id: str) -> Dict[str, Any]:
    """解析 LLM 输出的 verdict JSON。失败兜底 → need_supplement（保守）。

    兜底语义：LLM 不可靠时，宁可让人工复核，不要 pass。
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

    parsed: Dict[str, Any] = {}
    if text:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"P9 audit 输出不是合法 JSON：job_id={job_id} "
                f"raw={text[:200]!r} err={e} → 兜底 need_supplement"
            )
            parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    # ── verdict ──
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "reject", "need_supplement"):
        if verdict:
            logger.warning(
                f"P9 audit verdict 无法识别：job_id={job_id} "
                f"verdict={verdict!r} → 兜底 need_supplement"
            )
        verdict = "need_supplement"

    # ── confidence ──
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= float(confidence) <= 1):
        confidence = 0.5
    else:
        confidence = float(confidence)

    # ── comment ──
    comment = str(parsed.get("comment") or "").strip()
    if not comment:
        comment = (
            f"审核输出 comment 缺失或非标准结构（verdict={verdict!r}）；"
            f"建议人工复核。"
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
        "verdict": verdict,
        "confidence": confidence,
        "comment": comment,
        "evidence_check": norm_evidence,
        "audited_at": now,
        "auditor": "P9-AuditAgent",
        "is_mock": False,
    }


def _verdict_to_status(verdict: str) -> str:
    """verdict → job_status 映射（受 LEGAL_JOB_TRANSITIONS 约束）。

    状态机（models.py LEGAL_JOB_TRANSITIONS["materials_in_audit"]）只允许：
      - materials_in_audit → ready_to_close
      - materials_in_audit → waiting_human_review
    不允许直接 materials_in_audit → rectifying（必须经过 waiting_human_review）。

    映射：
      pass            → ready_to_close（材料齐全，进入人工终审）
      reject          → waiting_human_review（人工驳回 record_closure_review）
      need_supplement → waiting_human_review（人工要求补充 record_closure_review）
    """
    if verdict == "pass":
        return "ready_to_close"
    # reject / need_supplement 都进 waiting_human_review
    return "waiting_human_review"


# ============================================================
# Gradio / chat_reply 兼容入口（如有需要；当前未使用）
# ============================================================
def audit_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface / chat_reply 兼容入口（如有需要）。

    调用方传 message（描述作业），audit 模拟一次审核，输出 verdict JSON 字符串。
    不暴露任何 tool；纯对话。
    """
    agent = create_audit_agent()
    config = get_agent_config(
        thread_id="default",
        agent_name="P9-Audit",
        llm_params=get_llm_params(),
    )
    wrapped = (
        f"请基于以下用户描述审核作业材料，按 P9_AUDIT_SYSTEM_PROMPT 输出 JSON verdict：\n\n"
        f"{message}"
    )
    result = agent.invoke({"messages": [HumanMessage(content=wrapped)]}, config)
    return extract_output(result)


__all__ = [
    "create_audit_agent",
    "run_p9_materials_audit",
    "audit_demo",
]