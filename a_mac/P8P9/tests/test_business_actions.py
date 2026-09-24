# P8P9/tests/test_business_actions.py — 业务动作测试
#
# 覆盖：
#   - acknowledge_disposition
#   - submit_rectification_materials
#   - relinquish_job
#   - escalate_risk
#   - downgrade_risk
#   - record_closure_review
#   - archive_job_to_long_term_memory

from __future__ import annotations
import pytest

from P8P9 import business_actions
from P8P9.state_machine import (
    ClosureService, BusinessConsistencyViolation,
    CardinalityExceeded, InputValidationError, IllegalTransition,
)
from P8P9.tests.fixtures.sample_events import event_by_level


# ─── acknowledge_disposition ─────────────────────────────────────────────────

def test_acknowledge_disposition(mock_card_renderer, mock_audit_scheduler, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-ACK-001", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_a", "name": "Alice"}
    state = business_actions.acknowledge_disposition(
        "JOB-ACK-001", actor=actor, expected_version=0,
    )
    assert state["job_status"] == "acknowledged"
    assert state["accepted_by"]["open_id"] == "ou_a"
    # 卡片刷新触发
    assert any(c["job_id"] == "JOB-ACK-001" for c in mock_card_renderer)


def test_acknowledge_disposition_illegal_when_not_open(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-ACK-002", actor="test")
    actor = {"open_id": "ou_a", "name": "A"}
    business_actions.acknowledge_disposition(
        "JOB-ACK-002", actor=actor, expected_version=0,
    )
    # 第二次 acknowledge 应抛 IllegalTransition（open → acknowledged 后已非 open）
    with pytest.raises(IllegalTransition):
        business_actions.acknowledge_disposition(
            "JOB-ACK-002", actor=actor, expected_version=1,
        )


# ─── submit_rectification_materials ──────────────────────────────────────────

def test_submit_materials_same_actor(mock_card_renderer, mock_audit_scheduler, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-SUB-001", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_s", "name": "S"}
    business_actions.acknowledge_disposition(
        "JOB-SUB-001", actor=actor, expected_version=0,
    )
    # acknowledged → rectifying
    svc.set_job_status(
        "JOB-SUB-001", "rectifying",
        actor=actor, expected_version=1,
    )
    state = business_actions.submit_rectification_materials(
        "JOB-SUB-001",
        review_text="现场已整改完成；已补充消防器材；请审核。",
        submissions=[{"kind": "photo", "filename": "fix-1.png"}],
        actor=actor, expected_version=2,
    )
    assert state["job_status"] == "materials_in_audit"
    assert state["materials"]["submissions"][0]["review_text"] == "现场已整改完成；已补充消防器材；请审核。"


def test_submit_materials_different_actor_violation(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-SUB-002", actor="test", events=[event_by_level(2)])
    business_actions.acknowledge_disposition(
        "JOB-SUB-002", actor={"open_id": "ou_x", "name": "X"}, expected_version=0,
    )
    svc.set_job_status(
        "JOB-SUB-002", "rectifying",
        actor={"open_id": "ou_x", "name": "X"}, expected_version=1,
    )
    # 别人来提交 → BusinessConsistencyViolation
    with pytest.raises(BusinessConsistencyViolation):
        business_actions.submit_rectification_materials(
            "JOB-SUB-002",
            review_text="我是另一个人想提交材料。这是一条测试文本。",
            submissions=[],
            actor={"open_id": "ou_y", "name": "Y"}, expected_version=2,
        )


def test_submit_materials_empty_review_text_rejected(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-SUB-003", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_s", "name": "S"}
    business_actions.acknowledge_disposition(
        "JOB-SUB-003", actor=actor, expected_version=0,
    )
    svc.set_job_status(
        "JOB-SUB-003", "rectifying",
        actor=actor, expected_version=1,
    )
    with pytest.raises(InputValidationError):
        business_actions.submit_rectification_materials(
            "JOB-SUB-003",
            review_text="",
            submissions=[],
            actor=actor, expected_version=2,
        )


# ─── relinquish_job ──────────────────────────────────────────────────────────

def test_relinquish_job_min_reason_length(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-REL-001", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_r", "name": "R"}
    business_actions.acknowledge_disposition(
        "JOB-REL-001", actor=actor, expected_version=0,
    )
    with pytest.raises(InputValidationError):
        business_actions.relinquish_job(
            "JOB-REL-001", reason="太短",
            actor=actor, expected_version=1,
        )


def test_relinquish_job_max_count(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-REL-002", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_r", "name": "R"}
    business_actions.acknowledge_disposition(
        "JOB-REL-002", actor=actor, expected_version=0,
    )
    # 第一次退接
    s = business_actions.relinquish_job(
        "JOB-REL-002", reason="第一次退接测试足够长",
        actor=actor, expected_version=1,
    )
    assert s["job_status"] == "open"
    # 重新接取 + 第二次退接
    business_actions.acknowledge_disposition(
        "JOB-REL-002", actor=actor, expected_version=s["version"],
    )
    s = business_actions.relinquish_job(
        "JOB-REL-002", reason="第二次退接测试足够长",
        actor=actor, expected_version=s["version"] + 1,
    )
    assert s["relinquish_count"]["ou_r"] == 2
    # 第三次退接 → CardinalityExceeded
    business_actions.acknowledge_disposition(
        "JOB-REL-002", actor=actor, expected_version=s["version"],
    )
    with pytest.raises(CardinalityExceeded):
        business_actions.relinquish_job(
            "JOB-REL-002", reason="第三次退接测试足够长",
            actor=actor, expected_version=s["version"] + 1,
        )


# ─── escalate_risk ───────────────────────────────────────────────────────────

def test_escalate_risk_basic(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-ESC-001", actor="test", events=[event_by_level(1)])
    actor = {"open_id": "ou_e", "name": "E"}
    state = business_actions.escalate_risk(
        "JOB-ESC-001",
        event_id="A6-MOCK-001",
        new_level=3,
        reason="现场情况恶化；需提升风险等级",
        evidence_ids=["ev-1", "ev-2"],
        actor=actor, expected_version=0,
    )
    event = next(e for e in state["events"] if e["risk_event_id"] == "A6-MOCK-001")
    assert event["risk_level"] == 3
    assert len(state["risk_changes"]) == 1
    assert state["risk_changes"][0]["action"] == "escalate"


def test_escalate_risk_must_increase(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-ESC-002", actor="test", events=[event_by_level(3)])
    with pytest.raises(InputValidationError):
        business_actions.escalate_risk(
            "JOB-ESC-002",
            event_id="A6-MOCK-003",
            new_level=2,
            reason="尝试降低等级触发升级校验；该理由超过十字",
            evidence_ids=[],
            actor={"open_id": "ou_e"}, expected_version=0,
        )


def test_escalate_risk_short_reason_rejected(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-ESC-003", actor="test", events=[event_by_level(1)])
    with pytest.raises(InputValidationError):
        business_actions.escalate_risk(
            "JOB-ESC-003",
            event_id="A6-MOCK-001",
            new_level=2,
            reason="太短",
            evidence_ids=[],
            actor={"open_id": "ou_e"}, expected_version=0,
        )


# ─── downgrade_risk ──────────────────────────────────────────────────────────

def test_downgrade_risk_basic(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-DOWN-001", actor="test", events=[event_by_level(4)])
    actor = {"open_id": "ou_d", "name": "D"}
    result = business_actions.downgrade_risk(
        "JOB-DOWN-001",
        event_id="A6-MOCK-004",
        new_level=2,
        reason="经现场核实，原风险等级判断偏高；已采取额外防护措施",
        evidence_ids=["ev-photo-1"],
        actor=actor, expected_version=0,
    )
    state = result["state"]
    # P9 mock 同步通过 → level 已降
    event = next(e for e in state["events"] if e["risk_event_id"] == "A6-MOCK-004")
    assert event["risk_level"] == 2
    assert result["change_request_id"].startswith("RC-")


def test_downgrade_risk_short_reason_rejected(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-DOWN-002", actor="test", events=[event_by_level(4)])
    with pytest.raises(InputValidationError):
        business_actions.downgrade_risk(
            "JOB-DOWN-002",
            event_id="A6-MOCK-004",
            new_level=2,
            reason="理由太短",  # < 20
            evidence_ids=["ev-photo-1"],
            actor={"open_id": "ou_d"}, expected_version=0,
        )


def test_downgrade_risk_no_evidence_rejected(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-DOWN-003", actor="test", events=[event_by_level(4)])
    with pytest.raises(InputValidationError):
        business_actions.downgrade_risk(
            "JOB-DOWN-003",
            event_id="A6-MOCK-004",
            new_level=2,
            reason="理由足够长超过二十字谢谢",
            evidence_ids=[],  # 空
            actor={"open_id": "ou_d"}, expected_version=0,
        )


# ─── record_closure_review ───────────────────────────────────────────────────

def test_record_review_approve_closes_job(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-REV-001", actor="test", events=[event_by_level(3)])
    actor = {"open_id": "ou_rv", "name": "R"}
    business_actions.acknowledge_disposition("JOB-REV-001", actor=actor, expected_version=0)
    svc.set_job_status(
        "JOB-REV-001", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-REV-001")["version"],
    )
    business_actions.submit_rectification_materials(
        "JOB-REV-001",
        review_text="已提交整改证据。请审核。",
        submissions=[], actor=actor, expected_version=svc.get_state("JOB-REV-001")["version"],
    )
    # 手动推到 waiting_human_review（绕过 P9 mock 的 ready_to_close）
    svc.set_job_status(
        "JOB-REV-001", "waiting_human_review",
        actor=actor, expected_version=svc.get_state("JOB-REV-001")["version"],
    )
    state = business_actions.record_closure_review(
        "JOB-REV-001",
        decision="approved",
        comment="终审通过；可关闭归档。",
        actor=actor, expected_version=svc.get_state("JOB-REV-001")["version"],
    )
    assert state["job_status"] == "closed"
    assert state["closed_by"] == "ou_rv"
    # 归档铁律已跑（state.archived_to_lt=True）
    assert state["archived_to_lt"] is True


def test_record_review_reject_goes_to_rectifying(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-REV-002", actor="test", events=[event_by_level(3)])
    actor = {"open_id": "ou_rv", "name": "R"}
    business_actions.acknowledge_disposition("JOB-REV-002", actor=actor, expected_version=0)
    svc.set_job_status(
        "JOB-REV-002", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-REV-002")["version"],
    )
    business_actions.submit_rectification_materials(
        "JOB-REV-002",
        review_text="首次提交整改证据。",
        submissions=[], actor=actor, expected_version=svc.get_state("JOB-REV-002")["version"],
    )
    svc.set_job_status(
        "JOB-REV-002", "waiting_human_review",
        actor=actor, expected_version=svc.get_state("JOB-REV-002")["version"],
    )
    state = business_actions.record_closure_review(
        "JOB-REV-002",
        decision="rejected",
        comment="材料不完整，需补充；请重新准备后再次提交。",
        actor=actor, expected_version=svc.get_state("JOB-REV-002")["version"],
    )
    assert state["job_status"] == "rectifying"
    assert state["materials"]["submissions"] == []


def test_record_review_approve_with_bohui_word_flips(mock_card_renderer, closure_service):
    """§7.1：approved 但含「驳回」自动翻面为 rejected。"""
    svc = closure_service
    svc.initialize_job("JOB-REV-003", actor="test", events=[event_by_level(3)])
    actor = {"open_id": "ou_rv", "name": "R"}
    business_actions.acknowledge_disposition("JOB-REV-003", actor=actor, expected_version=0)
    svc.set_job_status(
        "JOB-REV-003", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-REV-003")["version"],
    )
    business_actions.submit_rectification_materials(
        "JOB-REV-003",
        review_text="首次提交整改证据。",
        submissions=[], actor=actor, expected_version=svc.get_state("JOB-REV-003")["version"],
    )
    svc.set_job_status(
        "JOB-REV-003", "waiting_human_review",
        actor=actor, expected_version=svc.get_state("JOB-REV-003")["version"],
    )
    state = business_actions.record_closure_review(
        "JOB-REV-003",
        decision="approved",
        comment="驳回：材料不完整，需补充更多证据后再提交",
        actor=actor, expected_version=svc.get_state("JOB-REV-003")["version"],
    )
    # 自动翻面 → rejected → rectifying
    assert state["job_status"] == "rectifying"


def test_record_review_short_comment_rejected(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-REV-004", actor="test", events=[event_by_level(3)])
    actor = {"open_id": "ou_rv", "name": "R"}
    business_actions.acknowledge_disposition("JOB-REV-004", actor=actor, expected_version=0)
    svc.set_job_status(
        "JOB-REV-004", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-REV-004")["version"],
    )
    business_actions.submit_rectification_materials(
        "JOB-REV-004",
        review_text="首次提交整改证据。",
        submissions=[], actor=actor, expected_version=svc.get_state("JOB-REV-004")["version"],
    )
    svc.set_job_status(
        "JOB-REV-004", "waiting_human_review",
        actor=actor, expected_version=svc.get_state("JOB-REV-004")["version"],
    )
    with pytest.raises(InputValidationError):
        business_actions.record_closure_review(
            "JOB-REV-004",
            decision="approved",
            comment="太短",
            actor=actor, expected_version=svc.get_state("JOB-REV-004")["version"],
        )