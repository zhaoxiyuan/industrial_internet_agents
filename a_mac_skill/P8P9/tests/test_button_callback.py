# P8P9/tests/test_button_callback.py — 真实按钮回调 → 状态机变更集成测试
#
# 模拟 7 个飞书按钮回调 → state_machine 真实走完 6 态：
#   1. acknowledge_disposition     open → acknowledged
#   2. web「开始整改」             acknowledged → rectifying  (svc.set_job_status)
#   3. submit_rectification_materials  rectifying → materials_in_audit
#   4. P9 审核 mock                 materials_in_audit → ready_to_close  (audit_scheduler)
#   5. record_closure_review(approved)  ready_to_close → closed  (含归档铁律)
#
# 验证：
#   - 每次 callback 后 state.json 实际被改（不是 mock 数据）
#   - 终态 closed + archived_to_lt=true
#   - 状态机历史完整
#   - 卡片渲染钩子被调用（card_render.update_job_card mock）

from __future__ import annotations
import json
import time
import pytest

from P8P9.services.callback_router import (
    route_card_callback,
    InvalidAction, InvalidOperator, CallbackError,
)
from P8P9.tests.fixtures.sample_events import event_by_level


# ─── payload 工具 ────────────────────────────────────────────────────────────

def _wrap(action_value: dict, *, operator_open_id="ou_test_user", operator_name="测试员", form_value=None):
    return {
        "header": {"event_id": "evt_btn", "event_type": "card.action.trigger"},
        "event": {
            "action": {
                "value": json.dumps(action_value, ensure_ascii=False),
                "form_value": form_value or {},
            },
            "operator": {"open_id": operator_open_id, "user_name": operator_name},
        },
    }


def _wait_for_state(svc, job_id, predicate, *, timeout=2.0, interval=0.05):
    """异步任务（audit_scheduler）写完才返回。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = svc.get_state(job_id)
        if predicate(last):
            return last
        time.sleep(interval)
    raise AssertionError(f"等待状态条件超时：last={last}")


# ─── 完整链路：6 态走通 ──────────────────────────────────────────────────────

def test_full_lifecycle_via_button_callbacks(
    mock_card_renderer, mock_audit_scheduler, closure_service,
):
    """open → acknowledged → rectifying → materials_in_audit → ready_to_close → closed"""
    svc = closure_service
    actor = {"open_id": "ou_test_user", "name": "测试员"}
    job_id = "JOB-BTN-LIFECYCLE"

    # 初始：1 个 event，risk_level=2
    svc.initialize_job(job_id, actor="seed", events=[event_by_level(2)])
    assert svc.get_state(job_id)["job_status"] == "open"

    # ① 按钮回调：acknowledge_disposition（open → acknowledged）
    payload1 = _wrap({
        "action": "acknowledge_disposition",
        "job_id": job_id,
        "expected_version": 0,
    })
    r1 = route_card_callback(payload1)
    assert r1["job_status"] == "acknowledged"
    assert r1["accepted_by"]["open_id"] == "ou_test_user"
    state = svc.get_state(job_id)
    assert state["job_status"] == "acknowledged"
    assert state["version"] == 1
    assert state["accepted_by"]["open_id"] == "ou_test_user"

    # ② Web 端「开始整改」（acknowledged → rectifying）；这一步走 state_machine（飞书按钮不直接支持）
    # 模拟 web_server 端调 svc.set_job_status
    state = svc.get_state(job_id)
    state = svc.set_job_status(
        job_id, "rectifying",
        actor=actor, expected_version=state["version"],
    )
    assert state["job_status"] == "rectifying"

    # ③ 按钮回调：submit_rectification_materials（rectifying → materials_in_audit）
    # 用 mock_audit_scheduler 接管（mock 不起 daemon thread）
    payload3 = _wrap(
        {
            "action": "submit_rectification_materials",
            "job_id": job_id,
            "expected_version": state["version"],
        },
        form_value={
            "review_text": "已补充消防器材照片；现场通风正常；可继续作业。",
            "submissions": [{"kind": "photo", "url": "oss://x"}],
        },
    )
    r3 = route_card_callback(payload3)
    assert r3["job_status"] == "materials_in_audit"

    # ④ P9 审核 mock：materials_in_audit → ready_to_close（mock_audit_scheduler fixture 已接管）
    # mock_audit_scheduler 没起后台线程，需手动触发（模拟真实飞书场景下的 _trigger_audit 调度）
    from P8P9.services import audit_scheduler as _audit_mod
    # 用 register 的 fake（来自 conftest 的 register_audit_scheduler）
    # 直接调 P9 路径：业务动作 submit_materials 已经调过 _trigger_audit（被 mock 接管）；
    # 现在我们要让 P9 真实地把状态推到 ready_to_close。
    # 在 conftest 里 mock_audit_scheduler 返回 {"status": "mocked"}，不会真改状态。
    # 所以这里我们要么：
    #   a) 真的跑 audit_scheduler 的 daemon thread（需要重新走业务）
    #   b) 直接调 svc.set_job_status("ready_to_close") 模拟 P9 完成
    # 这里选 (b)：mock audit_scheduler 已经接管了 _trigger_audit 钩子，
    # 测试 fixture 不阻塞，我们手动推状态。
    cur = svc.get_state(job_id)
    state = svc.set_job_status(
        job_id, "ready_to_close",
        actor={"open_id": "P9-Mock-Bot", "name": "P9 Mock"},
        expected_version=cur["version"],
    )
    assert state["job_status"] == "ready_to_close"

    # ⑤ 按钮回调：record_closure_review(approved) → closed + 归档
    payload5 = _wrap(
        {
            "action": "record_closure_review",
            "decision": "approved",
            "job_id": job_id,
            "expected_version": state["version"],
        },
        form_value={"comment": "终审通过；现场已恢复；可关闭归档。"},
    )
    r5 = route_card_callback(payload5)
    assert r5["job_status"] == "closed"
    assert r5["archived_to_lt"] is True

    # ─── 终态断言 ────────────────────────────────────────────────────────────
    final = svc.get_state(job_id)
    assert final["job_status"] == "closed"
    assert final["archived_to_lt"] is True
    assert final["archived_at"] is not None
    assert final["closed_at"] is not None
    assert final["closed_by"] == "ou_test_user"

    # ─── 状态机历史完整 ──────────────────────────────────────────────────────
    history = final["job_status_history"]
    transitions = [(h.get("from"), h.get("to")) for h in history]
    assert (None, "open") in transitions
    assert ("open", "acknowledged") in transitions
    assert ("acknowledged", "rectifying") in transitions
    assert ("rectifying", "materials_in_audit") in transitions
    assert ("materials_in_audit", "ready_to_close") in transitions
    assert ("ready_to_close", "closed") in transitions

    # ─── 卡片渲染钩子被调用（ack / submit / record-review 三个 callback 触发；中间 svc.set_job_status 不触发） ──
    assert len(mock_card_renderer) >= 3

    # ─── 归档文件存在 ────────────────────────────────────────────────────────
    lt_dir = svc.base_dir / "_long_term"
    assert (lt_dir / f"{job_id}.json").exists()


# ─── 单步断言：每个 callback 真实改 state.json ────────────────────────────────

def test_button_acknowledge_changes_state_persisted(closure_service, tmp_jobs_dir):
    """按钮回调后立刻读 disk，job_status 已变。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-PERSIST", actor="seed", events=[event_by_level(1)])
    payload = _wrap({
        "action": "acknowledge_disposition",
        "job_id": "JOB-BTN-PERSIST",
        "expected_version": 0,
    })
    route_card_callback(payload)

    # 直接读盘（不走 svc.get_state，验证持久化）
    import json
    from pathlib import Path
    raw = (tmp_jobs_dir / "JOB-BTN-PERSIST" / "closure_state.json").read_text(encoding="utf-8")
    state = json.loads(raw)
    assert state["job_status"] == "acknowledged"
    assert state["version"] == 1
    assert state["accepted_by"]["open_id"] == "ou_test_user"


# ─── 业务规则：「驳回」自动翻面 ──────────────────────────────────────────────

def test_button_record_review_with_banhui_word_flips(closure_service, tmp_jobs_dir):
    """record_closure_review 注释含「驳回」+ decision=approved → 自动翻面为 rejected。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-BANHUI", actor="seed", events=[event_by_level(2)])
    actor = {"open_id": "ou_test_user", "name": "测试员"}
    from P8P9 import business_actions
    business_actions.acknowledge_disposition("JOB-BTN-BANHUI", actor=actor, expected_version=0)
    svc.set_job_status(
        "JOB-BTN-BANHUI", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-BTN-BANHUI")["version"],
    )
    business_actions.submit_rectification_materials(
        "JOB-BTN-BANHUI",
        review_text="已补充材料。请审核。",
        submissions=[], actor=actor,
        expected_version=svc.get_state("JOB-BTN-BANHUI")["version"],
    )
    svc.set_job_status(
        "JOB-BTN-BANHUI", "waiting_human_review",
        actor=actor, expected_version=svc.get_state("JOB-BTN-BANHUI")["version"],
    )

    payload = _wrap(
        {
            "action": "record_closure_review",
            "decision": "approved",
            "job_id": "JOB-BTN-BANHUI",
            "expected_version": svc.get_state("JOB-BTN-BANHUI")["version"],
        },
        form_value={"comment": "驳回：材料不完整，请重新提交。"},
    )
    result = route_card_callback(payload)
    # 翻面后 rejected → waiting_human_review → rectifying
    assert result["job_status"] == "rectifying"
    assert result["review"]["last_decision"] == "rejected"


# ─── 业务一致性：非接取人不能 submit ────────────────────────────────────────

def test_button_submit_by_non_accepted_user_blocked(closure_service):
    """接取人=ou_a；其它人不能 submit（业务一致性校验）。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-BUSI", actor="seed", events=[event_by_level(2)])
    from P8P9 import business_actions
    business_actions.acknowledge_disposition(
        "JOB-BTN-BUSI", actor={"open_id": "ou_a", "name": "A"}, expected_version=0,
    )
    svc.set_job_status(
        "JOB-BTN-BUSI", "rectifying",
        actor={"open_id": "ou_a", "name": "A"},
        expected_version=svc.get_state("JOB-BTN-BUSI")["version"],
    )

    # 用 ou_b 触发 submit → CallbackError
    payload = {
        "header": {"event_id": "evt_x", "event_type": "card.action.trigger"},
        "event": {
            "action": {
                "value": json.dumps({
                    "action": "submit_rectification_materials",
                    "job_id": "JOB-BTN-BUSI",
                    "expected_version": svc.get_state("JOB-BTN-BUSI")["version"],
                }),
                "form_value": {"review_text": "材料已提交"},
            },
            "operator": {"open_id": "ou_b", "user_name": "B"},
        },
    }
    with pytest.raises(CallbackError):
        route_card_callback(payload)


# ─── 退接按钮走完整链路 ──────────────────────────────────────────────────────

def test_button_relinquish_flow_back_to_open(closure_service):
    """按钮：relinquish_job → acknowledged/rectifying 回 open。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-RELIN", actor="seed", events=[event_by_level(2)])
    actor = {"open_id": "ou_test_user", "name": "测试员"}
    from P8P9 import business_actions
    business_actions.acknowledge_disposition("JOB-BTN-RELIN", actor=actor, expected_version=0)

    payload = _wrap({
        "action": "relinquish_job",
        "job_id": "JOB-BTN-RELIN",
        "expected_version": svc.get_state("JOB-BTN-RELIN")["version"],
    }, form_value={"reason": "误接此作业，应由他人处置。"})
    result = route_card_callback(payload)
    assert result["job_status"] == "open"
    assert result["relinquish_count"]["ou_test_user"] == 1
    # accepted_by 保留（§4.6）
    assert result["accepted_by"]["open_id"] == "ou_test_user"


# ─── download_attachment 按钮走 link service ─────────────────────────────────

def test_button_download_attachment_returns_token(closure_service):
    """按钮：download_attachment → 返回短链 token。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-DL", actor="seed", events=[event_by_level(2)])
    payload = _wrap({
        "action": "download_attachment",
        "job_id": "JOB-BTN-DL",
        "evidence_id": "ev-real-001",
    })
    result = route_card_callback(payload)
    assert "token" in result
    assert result["short_url"].startswith("/dl/")
    assert len(result["token"]) >= 8


# ─── 异常路径：非法 action → InvalidAction（不写 state） ─────────────────────

def test_button_invalid_action_does_not_mutate_state(closure_service, tmp_jobs_dir):
    """非法 action 抛 InvalidAction，state.json 不变。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-INV", actor="seed", events=[event_by_level(2)])
    import json
    from pathlib import Path
    raw_before = (tmp_jobs_dir / "JOB-BTN-INV" / "closure_state.json").read_text(encoding="utf-8")

    payload = _wrap({
        "action": "made_up_action",
        "job_id": "JOB-BTN-INV",
        "expected_version": 0,
    })
    with pytest.raises(InvalidAction):
        route_card_callback(payload)

    raw_after = (tmp_jobs_dir / "JOB-BTN-INV" / "closure_state.json").read_text(encoding="utf-8")
    assert raw_before == raw_after  # state 未变


def test_button_missing_operator_does_not_mutate_state(closure_service, tmp_jobs_dir):
    """operator.open_id 缺失 → InvalidOperator，state.json 不变。"""
    svc = closure_service
    svc.initialize_job("JOB-BTN-NOOP", actor="seed", events=[event_by_level(2)])
    import json
    from pathlib import Path
    raw_before = (tmp_jobs_dir / "JOB-BTN-NOOP" / "closure_state.json").read_text(encoding="utf-8")

    payload = _wrap({
        "action": "acknowledge_disposition",
        "job_id": "JOB-BTN-NOOP",
        "expected_version": 0,
    })
    payload["event"]["operator"] = {}  # 清掉 open_id
    with pytest.raises(InvalidOperator):
        route_card_callback(payload)

    raw_after = (tmp_jobs_dir / "JOB-BTN-NOOP" / "closure_state.json").read_text(encoding="utf-8")
    assert raw_before == raw_after