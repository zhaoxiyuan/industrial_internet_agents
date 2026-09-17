# P8P9/services/audit_scheduler.py — P9 审核调度 service
#
# 设计依据：
#   - §2.0.1「2 agent + 3 service」：P9 审核 agent + 1 个 service（audit_scheduler）
#   - §6.3.3 异步调度封装
#
# ─────────────────────────────────────────────────────────────────────────────
# P9 审核 agent 接入契约（2026-09-17 已实现）：
#
#   **P9 智能体**负责审核 submit_rectification_materials 上传的处置材料。
#   整条链路如下：
#
#   1. 用户在 acknowledged/rectifying 卡片里点「提交/补充材料」
#      → business_actions.submit_rectification_materials
#      → job_status: acknowledged → rectifying → materials_in_audit
#      → _trigger_audit(job_id) 触发本 service
#
#   2. P9 智能体异步拿到 materials.submissions 最新一条，
#      调 LLM 审核 review_text + evidence_ids + submissions，
#      产出 review.p9_opinion = {
#          verdict:        "pass" | "reject" | "need_supplement",
#          confidence:     float (0-1),
#          comment:        str (≤500 字),
#          evidence_check: list[{evidence_id, ok, reason}],
#          audited_at:     iso8601,
#          auditor:        "P9-AuditAgent",
#          is_mock:        False,
#      }
#
#   3. P9 智能体把 p9_opinion 写入 state.review（patch_fields 不动 job_status），
#      再按 verdict 调 set_job_status 推 job_status：
#        - verdict == "pass"            → ready_to_close（人工终审 record_closure_review）
#        - verdict == "reject"          → waiting_human_review（人工驳回）
#        - verdict == "need_supplement" → waiting_human_review（人工要求补充）
#      （注意：状态机不允许 materials_in_audit → rectifying 一步直达；
#       只能走 waiting_human_review 再由 record_closure_review 决定 closed/rectifying）
#
#   4. update_job_card 刷新卡片：
#      - ready_to_close 卡片显示「P9 审核通过，等待人工终审」+「确认关闭 / 驳回」按钮
#      - waiting_human_review 卡片显示 P9 审核意见 + 待人工复核
#
#   5. 人工走 record_closure_review(decision="approved"|"rejected")：
#        - approved → closed（触发归档）—— 此时再同步调 p9_closure_agent 写 p9_opinion_text
#        - rejected → rectifying + 清 materials（用户可改后再提）
#
#   **失败兜底**：
#     - LLM invoke 抛异常 → fallback 到 _run_p9_audit_fallback_placeholder：
#       仅写 review.p9_opinion（标注 is_mock=true + verdict="pending_real_agent"），
#       job_status 保持 materials_in_audit 不动（避免假阳性 close job）。
#     - verdict JSON 解析失败 → 由 p9_audit_agent._parse_audit_output 兜底 need_supplement。
#
# 公开 API：
#   - agent_audit_job(job_id: str, *, intent: str = "materials_audit") -> dict
#   - _run_p9_audit_agent(job_id: str, intent: str) -> dict
#   - _run_p9_audit_fallback_placeholder(job_id: str, intent: str) -> dict
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..state_machine import ClosureService
from .. import business_actions


logger = logging.getLogger("P8P9.services.audit_scheduler")


# ─── 公开 API ────────────────────────────────────────────────────────────────

def agent_audit_job(job_id: str, *, intent: str = "materials_audit") -> Dict[str, Any]:
    """§6.3.3：service 包装层。

    同步返回 {"status": "scheduled"}；异步后台跑 P9 真审核 agent。
    失败时由 _run_p9_audit_fallback_placeholder 兜底（job_status 不动）。
    """
    thread = threading.Thread(
        target=_run_p9_audit_agent, args=(job_id, intent),
        daemon=True, name=f"P9-Audit-{job_id}",
    )
    thread.start()
    return {"status": "scheduled", "job_id": job_id, "intent": intent}


def _run_p9_audit_agent(job_id: str, intent: str) -> Dict[str, Any]:
    """P9 真审核 agent：调 agents.p9_audit_agent.run_p9_materials_audit。

    失败语义：LLM 抛异常 → fallback 到 _run_p9_audit_fallback_placeholder；
    避免 audit 流程崩溃 + 假阳性 close job。
    """
    try:
        from agents.p9_audit_agent import run_p9_materials_audit
    except ImportError as e:
        logger.warning(
            f"[P9 audit] agents.p9_audit_agent 不可用：err={e}；fallback 占位"
        )
        return _run_p9_audit_fallback_placeholder(job_id, intent)

    try:
        opinion = run_p9_materials_audit(job_id)
        logger.info(
            f"[P9 audit] 完成：job_id={job_id} "
            f"verdict={opinion.get('verdict')!r} "
            f"confidence={opinion.get('confidence', 0.0):.2f}"
        )
        return {
            "status": "completed",
            "job_id": job_id,
            "verdict": opinion.get("verdict"),
            "is_mock": False,
        }
    except Exception as e:
        logger.exception(
            f"[P9 audit] LLM/状态机异常：job_id={job_id} err={e}；fallback 占位"
        )
        return _run_p9_audit_fallback_placeholder(job_id, intent)


def _run_p9_audit_fallback_placeholder(job_id: str, intent: str) -> Dict[str, Any]:
    """LLM 失败兜底：仅写 review.p9_opinion（标注 is_mock=true），**不**推动 job_status。

    避免假阳性 close job（与 v2.1 mock 行为一致）：
      - job_status 保持 materials_in_audit 不动
      - p9_opinion.verdict = "pending_real_agent"（明确标记「待真 agent」）
      - p9_opinion.is_mock = True
      - 触发 update_job_card 让卡片显示「P9 审核待人工介入」标记
    """
    try:
        time.sleep(0.1)  # 模拟异步调度延迟
        svc = ClosureService()
        state = svc.get_state(job_id)

        # 防御：materials_in_audit 之外的态被误推到本函数（理论上不应该发生）
        if state.get("job_status") != "materials_in_audit":
            logger.warning(
                f"[P9 fallback] 跳过：job_id={job_id} 当前 job_status="
                f"{state.get('job_status')!r}（仅在该态下写 p9_opinion）"
            )
            return {"status": "skipped", "job_id": job_id,
                    "reason": "not_in_materials_in_audit"}

        now = datetime.now(timezone.utc).isoformat()
        review = dict(state.get("review") or {})
        review["p9_opinion"] = {
            "verdict": "pending_real_agent",  # 不是 pass/reject；明确标记「待真 agent」
            "confidence": 0.0,
            "comment": (
                f"[fallback] P9 真审核 agent 调用失败（LLM 异常 / import 失败 / 状态机异常）；"
                f"fallback 占位写入。job_status 保持 materials_in_audit 不动；"
                f"接入稳定后可重新提交材料触发重审。"
            ),
            "evidence_check": [],
            "audited_at": now,
            "auditor": "P9-Fallback-Bot",
            "is_mock": True,
        }
        review["p9_audited_at"] = now
        review["p9_auditor"] = "P9-Fallback-Bot"

        # patch_fields：不校验 LEGAL 转换、不写 job_status_history
        # （仍走 lock + atomic_write + version++，保持并发安全）
        new_state = svc.patch_fields(
            job_id,
            actor={"open_id": "P9-Fallback-Bot", "name": "P9 Fallback (placeholder)"},
            expected_version=None,
            review=review,
        )
        # 触发卡片刷新（job_status 未变，所以卡片仍是 materials_in_audit 态；
        # 渲染时会把 review.p9_opinion.is_mock 标记展示出来）
        try:
            from . import card_render
            card_render.update_job_card(job_id, new_state.get("version", 0))
        except Exception as e:
            logger.warning(f"卡片刷新失败（非致命）：{e}")
        logger.warning(
            f"[P9 fallback] 占位写入 p9_opinion（is_mock=true），job_id={job_id} "
            f"job_status 保持 materials_in_audit 未变"
        )
        return {"status": "fallback_placeholder", "job_id": job_id,
                "job_status": "materials_in_audit", "is_mock": True}
    except Exception as e:
        logger.exception(f"[P9 fallback] 占位写入失败：job_id={job_id} err={e}")
        return {"status": "error", "job_id": job_id, "error": str(e)}


# ─── 注入钩子 ────────────────────────────────────────────────────────────────

def _register_self() -> None:
    """import 时注册到 business_actions._audit_scheduler。"""
    try:
        business_actions.register_audit_scheduler(agent_audit_job)
    except Exception as e:
        logger.warning(f"register_audit_scheduler 失败：{e}")


_register_self()


__all__ = ["agent_audit_job"]