# P8P9/services/audit_scheduler.py — P9 审核调度 service
#
# 设计依据：
#   - §2.0.1「2 agent + 3 service」：P9 审核 agent + 1 个 service（audit_scheduler）
#   - §6.3.3 异步调度封装
#
# ─────────────────────────────────────────────────────────────────────────────
# P9 审核 agent 接入契约（v2.1，2026-09-17 修正）：
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
#          comment:        str (≤1000 字；可包含「建议驳回 / 建议补充 / 建议人工复核」意见),
#          confidence:     float (0-1),
#          evidence_check: list[{evidence_id, ok, reason}],
#          audited_at:     iso8601,
#          auditor:        "P9-AuditAgent",
#          is_mock:        False,
#      }
#
#   3. ★ v2.1 修正：P9 智能体把 p9_opinion 写入 state.review（patch_fields 不动 job_status）
#      job_status **保持 materials_in_audit 不动**，不推 ready_to_close / waiting_human_review
#      P9 没有决策权限，comment 中可包含建议但**不**直接拒绝
#
#   4. update_job_card 刷新卡片：job_status 未变，卡片仍是 materials_in_audit 态；
#      渲染时会把 review.p9_opinion.comment 摘要 + confidence 显示在 P9 段
#
#   5. 人工走 record_closure_review(decision="approved"|"rejected")：
#        - approved → closed（触发归档）—— 此时再同步调 p9_closure_agent 写 p9_opinion_text
#        - rejected → rectifying + 清 materials（用户可改后再提）
#      P9 写的 comment 仅作为人工决策的参考，**不**强制采纳
#
#   **失败兜底**：
#     - LLM invoke 抛异常 → fallback 到 _run_p9_audit_fallback_placeholder：
#       仅写 review.p9_opinion（标注 is_mock=true + comment 含「P9 审核待人工介入」提示），
#       job_status 保持 materials_in_audit 不动（避免假阳性 close job）。
#     - JSON 解析失败 → 由 p9_agent._parse_audit_output 兜底 confidence=0.5 +
#       「P9 审核输出 comment 缺失或非标准结构；建议人工复核」
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
    """P9 真审核 agent：调 agents.p9_agent.run_p9_materials_audit。

    v2.1：run_p9_materials_audit 只输出意见（comment + confidence + evidence_check），
    不推 job_status。失败语义：LLM 抛异常 → fallback 到 _run_p9_audit_fallback_placeholder；
    避免 audit 流程崩溃 + 假阳性 close job。
    """
    try:
        from agents.p9_agent import run_p9_materials_audit
    except ImportError as e:
        logger.warning(
            f"[P9 audit] agents.p9_agent 不可用：err={e}；fallback 占位"
        )
        return _run_p9_audit_fallback_placeholder(job_id, intent)

    try:
        opinion = run_p9_materials_audit(job_id)
        logger.info(
            f"[P9 audit] 完成（仅写意见，不推状态）：job_id={job_id} "
            f"confidence={opinion.get('confidence', 0.0):.2f} "
            f"comment_len={len(opinion.get('comment') or '')} "
            f"job_status 保持 materials_in_audit"
        )
        return {
            "status": "completed",
            "job_id": job_id,
            "is_mock": False,
            "confidence": opinion.get("confidence", 0.0),
            "comment_len": len(opinion.get("comment") or ""),
        }
    except Exception as e:
        logger.exception(
            f"[P9 audit] LLM/状态机异常：job_id={job_id} err={e}；fallback 占位"
        )
        return _run_p9_audit_fallback_placeholder(job_id, intent)


def _run_p9_audit_fallback_placeholder(job_id: str, intent: str) -> Dict[str, Any]:
    """LLM 失败兜底：仅写 review.p9_opinion（标注 is_mock=true），**不**推动 job_status。

    v2.1（2026-09-17）修正：
      - p9_opinion 结构：comment + confidence + evidence_check（**没有** verdict 字段）
      - job_status 保持 materials_in_audit 不动
      - p9_opinion.is_mock = True
      - p9_opinion.comment 含「P9 审核待人工介入；接入稳定后可重新提交材料触发重审」提示
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
            # v2.1：无 verdict 字段；comment 表达「待人工介入」
            "comment": (
                f"[fallback] P9 真审核 agent 调用失败（LLM 异常 / import 失败 / 状态机异常）；"
                f"fallback 占位写入。建议人工复核；接入稳定后可重新提交材料触发重审。"
            ),
            "confidence": 0.0,
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
        # 渲染时会把 review.p9_opinion.is_mock + comment 标记展示出来）
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