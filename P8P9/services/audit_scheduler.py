# P8P9/services/audit_scheduler.py — P9 审核调度 service
#
# 设计依据：
#   - §2.0.1「2 agent + 3 service」：P9 审核 agent + 1 个 service（audit_scheduler）
#   - §6.3.3 异步调度封装
#
# 本计划无 P9 LLM agent，所以后台线程仅 mock 实现。
# 接口设计留给后续接 `agents.p9_audit_agent`。
#
# ─────────────────────────────────────────────────────────────────────────────
# TODO(P9) — P9 审核 agent 接入契约（2026-09-17 暂留）：
#
#   **P9 智能体**负责审核 submit_rectification_materials 上传的处置材料。
#   整条链路预期如下：
#
#   1. 用户在 acknowledged/rectifying 卡片里点「提交/补充材料」
#      → business_actions.submit_rectification_materials
#      → job_status: acknowledged → rectifying → materials_in_audit
#      → _trigger_audit(job_id) 触发本 service
#
#   2. P9 智能体异步拿到 materials.submissions 最新一条，
#      调 LLM / 工具校验 review_text + evidence_ids + submissions，
#      产出 review.p9_opinion = {
#          verdict:        "pass" | "reject" | "need_supplement",
#          confidence:     float (0-1),
#          comment:        str,
#          evidence_check: list[{evidence_id, ok, reason}],
#          audited_at:     iso8601,
#          auditor:        "P9-AuditAgent",
#      }
#
#   3. P9 智能体把 p9_opinion 写入 state.review（不直接动 job_status），
#      再调 update_job_card 把审核结论刷到飞书卡片上：
#        - verdict == "pass"           → 卡片展示「P9 审核通过，等待人工终审」+ 卡片进入 ready_to_close 态（出现「确认关闭」按钮）
#        - verdict == "reject"         → 卡片退回 rectifying 态（job_status 推回 rectifying + 清空 latest_materials；用户可改后再提）
#        - verdict == "need_supplement"→ 卡片退回 rectifying 态（保留 materials，P9 在卡片上展示补充意见）
#
#   4. 卡片进入 ready_to_close / 退回 rectifying 后：
#        - ready_to_close 卡片**没有业务变更按钮**（不能由业务按钮直接 close），
#          必须由人工（监督员）走 record_closure_review(decision="approved"|"rejected")
#          → approved → closed（触发归档）/ rejected → waiting_human_review
#        - 退回 rectifying 后用户可继续编辑材料再提交
#
#   **约束**：
#     - 当前 mock 实现（_run_p9_audit_mock）**不允许**直接把 job_status 推到
#       ready_to_close 或 closed（避免在没有真 P9 agent 的情况下假阳性 close job）。
#       仅写 review.p9_opinion（标注 [mock]），job_status 保持 materials_in_audit
#       不动；卡片刷新后用户看到的是「P9 待接入」标记。
#     - 真 P9 agent 接入时，替换 _run_p9_audit_mock 为
#       `_run_p9_audit_agent(job_id, intent) -> Dict`，按上面的契约实现；
#       agent_audit_job 的 daemon thread 包装层不动。
#
# 公开 API：
#   - agent_audit_job(job_id: str, *, intent: str = "materials_audit") -> dict
#   - _run_p9_audit_mock(job_id: str, intent: str) -> dict
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

    同步返回 {"status": "scheduled"}；异步后台跑审核（mock）。
    生产实现：调 `agents.p9_audit_agent.run_audit(...)`。
    """
    thread = threading.Thread(
        target=_run_p9_audit_mock, args=(job_id, intent),
        daemon=True, name=f"P9-Mock-{job_id}",
    )
    thread.start()
    return {"status": "scheduled", "job_id": job_id, "intent": intent}


def _run_p9_audit_mock(job_id: str, intent: str) -> Dict[str, Any]:
    """Mock P9 审核：仅写 review.p9_opinion（标注 [mock]），**不**推动 job_status。

    约束（2026-09-17 由用户明确要求）：
      - 当前没有真 P9 agent；mock **不允许**直接把 job_status 推到
        ready_to_close / closed（避免假阳性 close job）。
      - job_status 保持 materials_in_audit 不动；卡片上展示 [mock] 标记
        让用户/测试知道「P9 待接入」。
      - 真 P9 agent 接入时按文件顶部 TODO(P9) 契约实现 _run_p9_audit_agent，
        本函数仅作为占位。
    """
    try:
        time.sleep(0.1)  # 模拟异步调度延迟
        svc = ClosureService()
        state = svc.get_state(job_id)

        # 防御：materials_in_audit 之外的态被误推到本函数（理论上不应该发生）
        # 如果发生，记录 warning 并直接返回（不写 p9_opinion）
        if state.get("job_status") != "materials_in_audit":
            logger.warning(
                f"[P9 mock] 跳过：job_id={job_id} 当前 job_status="
                f"{state.get('job_status')!r}（仅在该态下写 p9_opinion）"
            )
            return {"status": "skipped", "job_id": job_id,
                    "reason": "not_in_materials_in_audit"}

        now = datetime.now(timezone.utc).isoformat()
        review = state.get("review") or {}
        review["p9_opinion"] = {
            "verdict": "pending_real_agent",  # 不是 pass/reject；明确标记「待真 agent」
            "confidence": 0.0,
            "comment": (
                f"[mock] P9 智能体未接入。当前仅占位，job_status 保持 "
                f"materials_in_audit 不动；接入后由真 agent 按 TODO(P9) 契约 "
                f"产出 verdict + 推动 job_status。"
            ),
            "evidence_check": [],
            "audited_at": now,
            "auditor": "P9-Mock-Bot",
            "is_mock": True,
        }
        # patch_fields：不校验 LEGAL 转换、不写 job_status_history
        # （仍走 lock + atomic_write + version++，保持并发安全）
        new_state = svc.patch_fields(
            job_id,
            actor={"open_id": "P9-Mock-Bot", "name": "P9 Mock Auditor (placeholder)"},
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
        logger.info(
            f"[P9 mock] 占位写入 p9_opinion（is_mock=true），job_id={job_id} "
            f"job_status 保持 materials_in_audit 未变"
        )
        return {"status": "mock_placeholder", "job_id": job_id,
                "job_status": "materials_in_audit"}
    except Exception as e:
        logger.exception(f"P9 mock 占位写入失败：job_id={job_id} err={e}")
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