# P8P9/business_actions.py — 业务动作 service（§6.3）
#
# 7 个业务动作 + 1 个归档铁律（_archive_with_retry）。
#
# 设计依据：
#   - §6.3.1 接取/提交/驳回/通过
#   - §6.3.2 退接（业务一致性 + 退接次数）
#   - §2.5.2 升级（new_level > current + reason ≥ 10）
#   - §2.5.3 降级（new_level < current + reason ≥ 20 + ≥ 1 evidence）
#   - §6.3.1 review comment ≥ 10 字且不含「驳回」
#   - §8.2 归档内容；§8.3 归档铁律（指数退避 + 持续重试）
#   - §2.0.1 service 边界（不 import langchain）
#
# 入口：仅 callback_router / web_server（不允许 agent 直接调）。
# 所有动作内部：先校验业务一致性 → 调 ClosureService.set_job_status →
# 触发 services.card_render.update_job_card → 返回新 state。
#
# 严禁：@tool 装饰器（不是 LangChain tool）；import langchain；全局可变状态。

from __future__ import annotations
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .state_machine import (
    ClosureService,
    BusinessConsistencyViolation,
    CardinalityExceeded,
    InputValidationError,
    IllegalTransition,
    VersionConflict,
)
from .models import (
    REVIEW_COMMENT_MIN,
    RELINQUISH_REASON_MIN,
    ESCALATE_REASON_MIN,
    ESCALATE_MAX_DELTA,
    DOWNGRADE_REASON_MIN,
    DOWNGRADE_EVIDENCE_MIN,
    MAX_RELINQUISH_COUNT,
    ARCHIVE_FINAL_MARKER,
    ARCHIVE_MAX_RETRY_BACKOFF,
)


logger = logging.getLogger("P8P9.business_actions")


# ─── 回调钩子（card_render 延迟注入） ────────────────────────────────────────

# 避免循环 import：business_actions → services.card_render → business_actions
# 由 services.card_render 在 import time 通过 register_card_renderer 注册。
_card_renderer: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None
_audit_scheduler: Optional[Callable[[str], Dict[str, Any]]] = None


def register_card_renderer(
    renderer: Callable[[str, Dict[str, Any]], Dict[str, Any]]
) -> None:
    """services/card_render.py 在 import 时调，注入 update_job_card 钩子。"""
    global _card_renderer
    _card_renderer = renderer


def register_audit_scheduler(
    scheduler: Callable[[str], Dict[str, Any]]
) -> None:
    """services/audit_scheduler.py 在 import 时调，注入 agent_audit_job 钩子。"""
    global _audit_scheduler
    _audit_scheduler = scheduler


def _trigger_card_update(job_id: str, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """调 card_render.update_job_card；card_binding is None 时 no-op。

    2026-09-17：把 update_job_card 返回的 card_json 注入到 state，
    让飞书 callback 响应能带回新卡片数据，飞书 client 用新卡片替换原 message
    （避免 form_submit 后"切换又切换回去"问题）。

    2026-09-17 fix：card_renderer 签名是 update_job_card(job_id, version)，
    必须传 int version，不能传 state dict——否则 _build_acknowledged 里的
    submit_value["expected_version"] = state（含 _card_json 引用 cj），
    导致 json.dumps(cj) 时报 Circular reference detected。
    """
    if _card_renderer is None:
        # 未注入（单元测试场景）
        return None
    version = state.get("version", 0) if isinstance(state, dict) else 0
    try:
        result = _card_renderer(job_id, version)
    except Exception as e:
        logger.warning(f"card_render 触发失败: job_id={job_id} err={e}")
        return None
    # 把 card_json 注入 state（callers 直接 return state，web_server 看到）
    if isinstance(result, dict) and result.get("card_json"):
        state["_card_json"] = result["card_json"]
    return result


def _trigger_audit(job_id: str) -> None:
    if _audit_scheduler is None:
        return
    try:
        _audit_scheduler(job_id)
    except Exception as e:
        logger.warning(f"audit_scheduler 触发失败: job_id={job_id} err={e}")


# ─── 工具：业务一致性 + 字段校验 ─────────────────────────────────────────────

def _ensure_actor(actor: Dict[str, Any]) -> Dict[str, Any]:
    """actor 必须是 dict 且至少含 open_id。"""
    if not isinstance(actor, dict):
        raise InputValidationError("actor 必须是 dict")
    if "open_id" not in actor:
        raise InputValidationError("actor 必须含 open_id 字段")
    return actor


def _ensure_accepted_by(state: Dict[str, Any], actor: Dict[str, Any]) -> None:
    """§7.2.1：动作发起人必须 = accepted_by.open_id。"""
    accepted = state.get("accepted_by") or {}
    if not accepted:
        raise BusinessConsistencyViolation(
            "job 尚未接取（accepted_by 为空）；无法执行" \
            f" open_id={actor.get('open_id')!r} 的处置动作"
        )
    if accepted.get("open_id") != actor.get("open_id"):
        raise BusinessConsistencyViolation(
            f"业务一致性违反：当前接取人={accepted.get('open_id')!r}" \
            f" ≠ 本次操作人={actor.get('open_id')!r}"
        )


def _validate_text_length(value: Optional[str], min_len: int, field_name: str) -> str:
    """校验最小长度。空 / None / 空白过短都抛 InputValidationError。"""
    if value is None or not isinstance(value, str):
        raise InputValidationError(f"{field_name} 必须是字符串")
    stripped = value.strip()
    if len(stripped) < min_len:
        raise InputValidationError(
            f"{field_name} 至少 {min_len} 字（当前 {len(stripped)}）"
        )
    return stripped


def _banned_word_check(value: str, *, forbidden: List[str], field_name: str) -> None:
    """§7.1 防误触：comment 不能含禁用词。"""
    for word in forbidden:
        if word in value:
            raise InputValidationError(
                f"{field_name} 含禁用词 {word!r}；请改写后重新提交"
            )


# ─── 1. acknowledge_disposition（§6.3.1） ────────────────────────────────────

def acknowledge_disposition(
    job_id: str, *, actor: Dict[str, Any], expected_version: int,
) -> Dict[str, Any]:
    """§6.3.1：open → acknowledged。抢锁 + 写 accepted_by。

    校验：
      - actor.open_id 存在
      - 当前态 = open
      - expected_version 一致
    """
    actor = _ensure_actor(actor)
    svc = ClosureService()
    state = svc.get_state(job_id)
    if state.get("job_status") != "open":
        raise IllegalTransition(
            f"acknowledge_disposition 要求 job_status='open'，"
            f"实际={state.get('job_status')!r}"
        )

    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "accepted_by": {
            "open_id": actor["open_id"],
            "name": actor.get("name", ""),
            "accepted_at": now,
        }
    }
    state = svc.set_job_status(
        job_id, "acknowledged",
        actor=actor, expected_version=expected_version, **fields,
    )
    _trigger_card_update(job_id, state)
    return state


# ─── 2. submit_rectification_materials（§6.3.1 + §7.2.1） ──────────────────────

def submit_rectification_materials(
    job_id: str, *,
    review_text: str,
    submissions: List[Dict[str, Any]],
    actor: Dict[str, Any],
    expected_version: int,
    event_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """§6.3.1 + §7.2.1：业务一致性校验 actor == accepted_by。
    review_text 非空（§4.2 不强制下限）。

    Accepted job_status: acknowledged / rectifying / waiting_human_review (驳回后重交)。
    转 materials_in_audit。

    2026-09-17 fix：acknowledged → materials_in_audit 不是 LEGAL_JOB_TRANSITIONS
    一步直达，必须先 acknowledged → rectifying 再 rectifying → materials_in_audit。
    用一次 svc._lock_for 包住两段 transition 保证原子（第二次 expected_version=None
    跳过乐观锁，因为锁内连续 transition 无竞争）。
    """
    actor = _ensure_actor(actor)
    svc = ClosureService()
    state = svc.get_state(job_id)
    _ensure_accepted_by(state, actor)

    current = state.get("job_status")
    if current not in ("acknowledged", "rectifying", "waiting_human_review"):
        raise IllegalTransition(
            f"submit_rectification_materials 仅在 acknowledged/rectifying/"
            f"waiting_human_review 态允许；实际={current!r}"
        )

    # review_text 必须非空（doc §4.2：不强制长度下限，但绝不可空）
    if not review_text or not review_text.strip():
        raise InputValidationError("review_text 不能为空")

    if event_ids is None:
        event_ids = [e["risk_event_id"] for e in state.get("events", []) if e.get("risk_event_id")]

    now = datetime.now(timezone.utc).isoformat()
    materials = state.get("materials") or {"submissions": []}
    submissions_list = list(materials.get("submissions", []))
    submissions_list.append({
        "review_text": review_text.strip(),
        "submissions": submissions,
        "event_ids": event_ids,
        "submitted_by": actor["open_id"],
        "submitted_at": now,
    })
    fields = {
        "materials": {
            "submissions": submissions_list,
            "latest_review_text": review_text.strip(),
            "latest_at": now,
        }
    }

    # 两阶段 transition（一次 lock 内）
    with svc._lock_for(job_id):
        # 第一段：acknowledged → rectifying（如需要）
        if current == "acknowledged":
            state = svc.set_job_status(
                job_id, "rectifying",
                actor=actor, expected_version=expected_version,
            )
        # 第二段：rectifying → materials_in_audit（带 materials）
        # expected_version=None 跳过乐观锁：已经在 _lock_for 内，连续 transition 无竞争
        state = svc.set_job_status(
            job_id, "materials_in_audit",
            actor=actor, expected_version=None, **fields,
        )
    _trigger_card_update(job_id, state)
    # 异步触发 P9 审核（mock 实现）
    _trigger_audit(job_id)
    return state


# ─── 3. relinquish_job（§4.6 + §7.2.2） ──────────────────────────────────────

def relinquish_job(
    job_id: str, *, reason: str,
    actor: Dict[str, Any], expected_version: int,
) -> Dict[str, Any]:
    """§4.6 + §7.2.2：校验 actor == accepted_by + 退接次数 < MAX + reason ≥ 10。

    acknowledged/rectifying → open；accepted_by 保留；relinquish_count[open_id] += 1。
    """
    actor = _ensure_actor(actor)
    reason_clean = _validate_text_length(reason, RELINQUISH_REASON_MIN, "reason")

    svc = ClosureService()
    state = svc.get_state(job_id)
    _ensure_accepted_by(state, actor)

    current = state.get("job_status")
    if current not in ("acknowledged", "rectifying"):
        raise IllegalTransition(
            f"relinquish_job 仅在 acknowledged/rectifying 态允许；实际={current!r}"
        )

    # 退接次数校验（§7.2.2）
    rel_count_map = state.get("relinquish_count") or {}
    current_count = rel_count_map.get(actor["open_id"], 0)
    if current_count >= MAX_RELINQUISH_COUNT:
        raise CardinalityExceeded(
            f"open_id={actor['open_id']!r} 已退接 {current_count} 次，"
            f"达到上限 {MAX_RELINQUISH_COUNT}（§7.2.2）"
        )

    now = datetime.now(timezone.utc).isoformat()
    rel_count_map[actor["open_id"]] = current_count + 1
    fields = {
        "relinquish_count": rel_count_map,
        "last_relinquish": {
            "by": actor["open_id"],
            "reason": reason_clean,
            "at": now,
        },
        # accepted_by 保留（§4.6）；清 materials
        "materials": {"submissions": []},
    }
    state = svc.set_job_status(
        job_id, "open",
        actor=actor, expected_version=expected_version, **fields,
    )
    _trigger_card_update(job_id, state)
    return state


# ─── 4. escalate_risk（§2.5.2） ──────────────────────────────────────────────

def escalate_risk(
    job_id: str, *,
    event_id: str, new_level: int, reason: str,
    evidence_ids: Optional[List[str]],
    actor: Dict[str, Any], expected_version: int,
) -> Dict[str, Any]:
    """§2.5.2：new_level > current + reason ≥ 10 字；写 risk_changes + 改 risk_level。

    new_level == 5 → 异步发安全管理部门群通知（本版只 log）。
    """
    actor = _ensure_actor(actor)
    reason_clean = _validate_text_length(reason, ESCALATE_REASON_MIN, "reason")

    if not isinstance(new_level, int) or not (1 <= new_level <= 5):
        raise InputValidationError(f"new_level 必须是 1-5 的整数；实际={new_level!r}")

    svc = ClosureService()
    state = svc.get_state(job_id)
    event = _find_event_for_action(state, event_id)

    old_level = int(event.get("risk_level", 0))
    if new_level <= old_level:
        raise InputValidationError(
            f"升级要求 new_level > current={old_level}；实际 new_level={new_level}"
        )
    if new_level - old_level > ESCALATE_MAX_DELTA:
        raise InputValidationError(
            f"单次升级最多 {ESCALATE_MAX_DELTA} 级（{old_level} → {new_level} 超出）"
        )

    now = datetime.now(timezone.utc).isoformat()
    risk_changes = list(state.get("risk_changes") or [])
    change_id = f"RC-{len(risk_changes)+1:04d}"
    risk_changes.append({
        "change_id": change_id,
        "event_id": event_id,
        "action": "escalate",
        "from_level": old_level,
        "to_level": new_level,
        "reason": reason_clean,
        "evidence_ids": evidence_ids or [],
        "by": actor["open_id"],
        "at": now,
        "status": "applied",
    })

    # 走原子写 + version++；不走 set_job_status（job_status 不变；open → open 非法）
    with svc._lock_for(job_id):
        cur = svc.get_state(job_id)
        if expected_version is not None and cur.get("version") != expected_version:
            from .state_machine import VersionConflict as _VC
            raise _VC(
                f"job_id={job_id} expected_version={expected_version} "
                f"!= actual={cur.get('version')}"
            )
        # 找到当前 event（cur 重读后引用可能不同）
        target_event = next(
            (e for e in cur.get("events", []) if e.get("risk_event_id") == event_id),
            None,
        )
        if target_event is None:
            raise InputValidationError(f"risk_event_id={event_id!r} 不存在")
        target_event["risk_level"] = new_level
        target_event["updated_at"] = now
        cur["risk_changes"] = risk_changes
        cur["version"] = cur.get("version", 0) + 1
        cur["updated_at"] = now
        svc._recompute_display_risk_level_inplace(cur)
        svc._atomic_write(job_id, cur)
        state = cur

    # 危急（5 级）→ 异步通知（本版只 log）
    if new_level == 5:
        logger.warning(
            f"[CRITICAL ESCALATION] job_id={job_id} event_id={event_id} "
            f"由 {old_level} 级 → 5 级；触发安全管理部门群通知（mock）"
        )

    _trigger_card_update(job_id, state)
    return state


# ─── 5. downgrade_risk（§2.5.3） ────────────────────────────────────────────

def downgrade_risk(
    job_id: str, *,
    event_id: str, new_level: int, reason: str,
    evidence_ids: Optional[List[str]],
    actor: Dict[str, Any], expected_version: int,
) -> Dict[str, Any]:
    """§2.5.3：new_level < current + reason ≥ 20 字 + ≥ 1 evidence。

    不直接改 level，写 risk_changes[action: downgrade_pending] + 返回 change_request_id。
    本计划 P9 LLM agent 不在范围，所以 _post_process_downgrade_review 同步 mock 返回 approved。

    返回 state + change_request_id。
    """
    actor = _ensure_actor(actor)
    reason_clean = _validate_text_length(reason, DOWNGRADE_REASON_MIN, "reason")

    if not isinstance(new_level, int) or not (1 <= new_level <= 5):
        raise InputValidationError(f"new_level 必须是 1-5 的整数；实际={new_level!r}")

    if not evidence_ids or not isinstance(evidence_ids, list) or len(evidence_ids) < DOWNGRADE_EVIDENCE_MIN:
        raise InputValidationError(
            f"evidence_ids 至少 {DOWNGRADE_EVIDENCE_MIN} 个；实际={evidence_ids!r}"
        )

    svc = ClosureService()
    state = svc.get_state(job_id)
    event = _find_event_for_action(state, event_id)

    old_level = int(event.get("risk_level", 0))
    if new_level >= old_level:
        raise InputValidationError(
            f"降级要求 new_level < current={old_level}；实际 new_level={new_level}"
        )

    now = datetime.now(timezone.utc).isoformat()
    risk_changes = list(state.get("risk_changes") or [])
    change_id = f"RC-{len(risk_changes)+1:04d}"
    risk_changes.append({
        "change_id": change_id,
        "event_id": event_id,
        "action": "downgrade",
        "from_level": old_level,
        "to_level": new_level,
        "reason": reason_clean,
        "evidence_ids": evidence_ids,
        "by": actor["open_id"],
        "at": now,
        "status": "pending_review",  # 等 P9 审核；本计划 mock 立即通过
    })

    fields = {"risk_changes": risk_changes}
    # 走原子写 + version++（job_status 不变）
    with svc._lock_for(job_id):
        cur = svc.get_state(job_id)
        if expected_version is not None and cur.get("version") != expected_version:
            from .state_machine import VersionConflict as _VC
            raise _VC(
                f"job_id={job_id} expected_version={expected_version} "
                f"!= actual={cur.get('version')}"
            )
        cur["risk_changes"] = risk_changes
        cur["version"] = cur.get("version", 0) + 1
        cur["updated_at"] = datetime.now(timezone.utc).isoformat()
        svc._recompute_display_risk_level_inplace(cur)
        svc._atomic_write(job_id, cur)
        state = cur

    # 同步 mock P9 审核（生产环境由 services/audit_scheduler 异步调度）
    # 注意：P9 mock 也走原子写，可能 version 已被它 +1；下面取最新
    after_post = svc.get_state(job_id)
    _post_process_downgrade_review(job_id, change_id, base_state=after_post)

    final_state = svc.get_state(job_id)
    _trigger_card_update(job_id, final_state)
    return {"state": final_state, "change_request_id": change_id}


def _post_process_downgrade_review(job_id: str, change_id: str, *, base_state: Dict[str, Any] = None) -> None:
    """Mock P9 审核：把 risk_changes[change_id].status 改为 applied，并把 event.risk_level 降下来。"""
    svc = ClosureService()
    # 用 base_state 避免 race；写时再读最新
    state = base_state or svc.get_state(job_id)
    found = False
    for rc in state.get("risk_changes", []):
        if rc.get("change_id") == change_id and rc.get("status") == "pending_review":
            rc["status"] = "applied"
            rc["reviewed_by"] = "P9-Audit-Mock"
            rc["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            target_event_id = rc.get("event_id")
            for event in state.get("events", []):
                if event.get("risk_event_id") == target_event_id:
                    event["risk_level"] = rc["to_level"]
                    event["updated_at"] = datetime.now(timezone.utc).isoformat()
                    break
            found = True
            break
    if not found:
        return
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["version"] = state.get("version", 0) + 1
    svc._atomic_write(job_id, state)


# ─── 6. record_closure_review（§6.3.1 + §7.1） ───────────────────────────────

def record_closure_review(
    job_id: str, *,
    decision: str, comment: str,
    actor: Dict[str, Any], expected_version: int,
) -> Dict[str, Any]:
    """§6.3.1 + §7.1：decision ∈ {approved, rejected} + comment ≥ 10 字 + 不含「驳回」（含则翻面）。

    approved → closed（触发 _archive_with_retry）。
    rejected → waiting_human_review → rectifying + 清 materials。
    """
    actor = _ensure_actor(actor)
    comment_clean = _validate_text_length(comment, REVIEW_COMMENT_MIN, "comment")

    # §7.1 防误触
    if "驳回" in comment_clean:
        if decision == "approved":
            decision = "rejected"
            logger.info(
                f"job_id={job_id}: comment 含「驳回」自动翻面为 rejected；"
                f"actor={actor.get('open_id')}"
            )

    if decision not in ("approved", "rejected"):
        raise InputValidationError(f"decision 必须是 approved/rejected；实际={decision!r}")

    svc = ClosureService()
    state = svc.get_state(job_id)
    current = state.get("job_status")
    if current not in ("waiting_human_review", "ready_to_close"):
        raise IllegalTransition(
            f"record_closure_review 仅在 waiting_human_review/ready_to_close 态允许；"
            f"实际={current!r}"
        )

    now = datetime.now(timezone.utc).isoformat()
    review_obj = state.get("review") or {}
    review_obj["history"] = list(review_obj.get("history", []))
    review_obj["history"].append({
        "decision": decision,
        "comment": comment_clean,
        "by": actor["open_id"],
        "at": now,
    })
    review_obj["last_decision"] = decision
    review_obj["last_comment"] = comment_clean
    review_obj["last_at"] = now

    fields = {"review": review_obj}

    if decision == "approved":
        fields["closed_at"] = now
        fields["closed_by"] = actor["open_id"]
        fields["job_closure_status"] = "closed"
        state = svc.set_job_status(
            job_id, "closed",
            actor=actor, expected_version=expected_version, **fields,
        )
        # 2026-09-17：approved 时自动同步调 P9 智能体生成关闭理由，
        # 写入 state.review.p9_opinion_text + 卡片 P9 段。
        # 失败兜底：不阻断关闭流程（卡片仍 closed；p9_opinion_text 留 None）。
        try:
            from agents.p9_closure_agent import run_p9_closure_review
            p9_text = run_p9_closure_review(job_id)
            if p9_text:
                # 用 patch_fields 写入 p9_opinion_text（不触发状态机转换，
                # 仅扩展 review 子字段；不影响 job_status / version 递增规则之外的语义）
                state = svc.patch_fields(
                    job_id,
                    actor={"open_id": "P9-Agent", "name": "P9 Closure Agent"},
                    expected_version=None,
                    **{
                        "review": {
                            **((state.get("review") or {})),
                            "p9_opinion_text": p9_text,
                            "p9_generated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    },
                )
                logger.info(
                    f"P9 关闭理由已写入：job_id={job_id} len={len(p9_text)}"
                )
            else:
                logger.warning(f"P9 关闭理由为空：job_id={job_id}")
        except Exception as e:
            logger.warning(f"P9 关闭理由生成失败（不阻断关闭）：job_id={job_id} err={e}")
        # 触发归档铁律
        try:
            _archive_with_retry(job_id)
        except Exception as e:
            logger.error(f"归档失败（首次）：job_id={job_id} err={e}")
            # 不抛错；后台重试会接
        # 重新读最新 state（archived_to_lt 已被 archive_job_to_long_term_memory 写 true）
        try:
            state = svc.get_state(job_id)
        except Exception:
            pass
        _trigger_card_update(job_id, state)
        return state
    else:
        # rejected → waiting_human_review → rectifying
        # 状态机 §3.3：waiting_human_review → {closed, rectifying}
        fields["materials"] = {"submissions": []}
        state = svc.set_job_status(
            job_id, "rectifying",
            actor=actor, expected_version=expected_version, **fields,
        )
        _trigger_card_update(job_id, state)
        return state


# ─── 7. archive_job_to_long_term_memory + _archive_with_retry（§8.2 / §8.3） ──

class ArchiveError(Exception):
    """归档失败（多次重试后仍失败）。"""


def archive_job_to_long_term_memory(job_id: str) -> Dict[str, Any]:
    """§8.2：收集全部状态内容写 data/p8p9_jobs/_long_term/{job_id}.json。
    失败抛 ArchiveError。
    """
    svc = ClosureService()
    state = svc.get_state(job_id)

    lt_dir = svc.base_dir / "_long_term"
    lt_dir.mkdir(parents=True, exist_ok=True)
    target = lt_dir / f"{job_id}.json"

    # 收集 §8.2 全部内容
    payload = {
        "schema_version": "1.1",
        "job_id": job_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "closure_state": state,
        # 索引层一行描述（p8_long_term.py 同款）
        "index_entry": _build_index_entry(state),
    }

    # 原子写
    tmp = None
    try:
        fd, tmp = _mkstemp_in(lt_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp, target)
    except Exception:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise ArchiveError(f"归档失败: job_id={job_id}")

    # 更新 state.archived_to_lt = True
    state["archived_to_lt"] = True
    state["archived_at"] = payload["archived_at"]
    state["archive_attempts"] = (state.get("archive_attempts") or 0) + 1
    svc._atomic_write(job_id, state)

    return {"target": str(target), "archived_at": payload["archived_at"]}


def _mkstemp_in(directory: Path):
    import tempfile
    return tempfile.mkstemp(prefix=".long_term_", suffix=".json.tmp", dir=str(directory))


def _archive_with_retry(job_id: str) -> None:
    """§8.3 铁律：archived_to_lt 终态必须 = true。

    退避策略：60s → 300s → 900s → 第 3 次失败后 1h 持续重试。
    本函数**同步执行**全部退避（耗时极长），调用方应决定是否 daemon thread。
    """
    backoff_schedule = [60, 300, 900]
    attempt = 0
    while True:
        attempt += 1
        try:
            archive_job_to_long_term_memory(job_id)
            logger.info(f"归档成功：job_id={job_id} attempt={attempt}")
            return
        except ArchiveError as e:
            if attempt <= len(backoff_schedule):
                delay = backoff_schedule[attempt - 1]
                logger.warning(
                    f"归档失败（第 {attempt} 次）：job_id={job_id} err={e}；"
                    f"{delay}s 后重试"
                )
                time.sleep(delay)
            else:
                # 第 3 次失败后 → 1h 持续重试（铁律）
                logger.error(
                    f"归档持续失败（attempt={attempt}）：job_id={job_id}；"
                    f"按 §8.3 铁律，{ARCHIVE_MAX_RETRY_BACKOFF}s 后继续重试"
                )
                time.sleep(ARCHIVE_MAX_RETRY_BACKOFF)


def _build_index_entry(state: Dict[str, Any]) -> str:
    """一行描述：'[max_level] risk_basis前30字；decision by decider @ archived_at'。"""
    max_level = state.get("display_risk_level") or 0
    events = state.get("events", [])
    basis = (events[0].get("risk_basis", "") if events else "").replace("\n", " ")[:30]
    review = state.get("review") or {}
    decision = review.get("last_decision", "pending")
    decider = (review.get("history", [{}])[-1].get("by", "") if review.get("history") else "")
    archived_at = (state.get("archived_at") or "")[:19]
    return f"[{max_level}] {basis}；{decision} by {decider} @ {archived_at}"


# ─── 私有辅助 ───────────────────────────────────────────────────────────────

def _find_event_for_action(state: Dict[str, Any], event_id: str) -> Dict[str, Any]:
    """升级 / 降级用的 event 查找；找不到抛 InputValidationError。"""
    for e in state.get("events", []):
        if e.get("risk_event_id") == event_id:
            return e
    raise InputValidationError(f"risk_event_id={event_id!r} 不存在")


__all__ = [
    "register_card_renderer",
    "register_audit_scheduler",
    "acknowledge_disposition",
    "submit_rectification_materials",
    "relinquish_job",
    "escalate_risk",
    "downgrade_risk",
    "record_closure_review",
    "archive_job_to_long_term_memory",
    "ArchiveError",
]