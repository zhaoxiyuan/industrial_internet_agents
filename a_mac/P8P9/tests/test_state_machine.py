# P8P9/tests/test_state_machine.py — 状态机 service 单元测试
#
# 覆盖：
#   - initialize_job
#   - set_job_status LEGAL/非法转换
#   - 乐观锁 (expected_version 冲突)
#   - 原子写 (失败回滚)
#   - set_event_status LEGAL/非法
#   - _recompute_display_risk_level

from __future__ import annotations
import pytest

from P8P9.state_machine import (
    ClosureService, StateNotFound, IllegalTransition, VersionConflict,
    InputValidationError,
)
from P8P9.tests.fixtures.sample_events import event_by_level


def test_initialize_job(closure_service):
    svc = closure_service
    state = svc.initialize_job(
        "JOB-TEST-001", actor="test", events=[event_by_level(2)],
    )
    assert state["job_status"] == "open"
    assert state["version"] == 0
    assert state["display_risk_level"] == 2
    assert len(state["events"]) == 1
    assert state["events"][0]["risk_event_id"] == "A6-MOCK-002"
    assert state["events"][0]["event_status"] == "pending"


def test_initialize_job_idempotent(closure_service):
    svc = closure_service
    s1 = svc.initialize_job("JOB-TEST-IDEM", actor="test")
    s2 = svc.get_state("JOB-TEST-IDEM")
    assert s1["version"] == s2["version"]


def test_initialize_job_invalid_id(closure_service):
    svc = closure_service
    with pytest.raises(InputValidationError):
        svc.initialize_job("../escape", actor="test")


def test_set_job_status_legal_transition(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-TEST-002", actor="test")
    state = svc.set_job_status(
        "JOB-TEST-002", "acknowledged",
        actor={"open_id": "ou_x", "name": "X"},
        expected_version=0,
        accepted_by={"open_id": "ou_x", "name": "X", "accepted_at": "2026-09-16T10:00:00+00:00"},
    )
    assert state["job_status"] == "acknowledged"
    assert state["version"] == 1
    assert state["accepted_by"]["open_id"] == "ou_x"


def test_set_job_status_illegal_transition(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-TEST-003", actor="test")
    with pytest.raises(IllegalTransition):
        svc.set_job_status(
            "JOB-TEST-003", "closed",
            actor={"open_id": "ou_x"}, expected_version=0,
        )


def test_set_job_status_version_conflict(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-TEST-004", actor="test")
    with pytest.raises(VersionConflict):
        svc.set_job_status(
            "JOB-TEST-004", "acknowledged",
            actor={"open_id": "ou_x"}, expected_version=999,
        )


def test_set_job_status_state_not_found(closure_service):
    svc = closure_service
    with pytest.raises(StateNotFound):
        svc.set_job_status(
            "JOB-NONEXISTENT", "acknowledged",
            actor={"open_id": "ou_x"}, expected_version=0,
        )


def test_set_job_status_full_lifecycle(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-LIFE-001", actor="test")
    actor = {"open_id": "ou_t", "name": "Test"}

    # open → acknowledged
    s = svc.set_job_status("JOB-LIFE-001", "acknowledged", actor=actor, expected_version=0)
    assert s["job_status"] == "acknowledged"

    # acknowledged → rectifying
    s = svc.set_job_status("JOB-LIFE-001", "rectifying", actor=actor, expected_version=1)
    assert s["job_status"] == "rectifying"

    # rectifying → materials_in_audit
    s = svc.set_job_status("JOB-LIFE-001", "materials_in_audit", actor=actor, expected_version=2)
    assert s["job_status"] == "materials_in_audit"

    # materials_in_audit → waiting_human_review
    s = svc.set_job_status("JOB-LIFE-001", "waiting_human_review", actor=actor, expected_version=3)
    assert s["job_status"] == "waiting_human_review"

    # waiting_human_review → closed
    s = svc.set_job_status("JOB-LIFE-001", "closed", actor=actor, expected_version=4)
    assert s["job_status"] == "closed"
    assert s["version"] == 5


def test_set_job_status_relinquish_flow(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-RELIN-001", actor="test")
    actor = {"open_id": "ou_r", "name": "Relinquisher"}

    svc.set_job_status("JOB-RELIN-001", "acknowledged", actor=actor, expected_version=0,
                       accepted_by={"open_id": "ou_r", "name": "Relinquisher"})
    # acknowledged → open (退接)
    s = svc.set_job_status("JOB-RELIN-001", "open", actor=actor, expected_version=1)
    assert s["job_status"] == "open"


def test_set_job_status_history_recorded(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-HIST-001", actor="init-agent")
    actor = {"open_id": "ou_h", "name": "Hist"}
    svc.set_job_status("JOB-HIST-001", "acknowledged", actor=actor, expected_version=0)
    state = svc.get_state("JOB-HIST-001")
    history = state["job_status_history"]
    assert len(history) >= 2
    assert history[-1]["to"] == "acknowledged"
    assert history[-1]["by"] == "ou_h"


def test_atomic_write_rollback_on_failure(closure_service, monkeypatch):
    """模拟 _atomic_write 中途抛错：原文件保留。"""
    svc = closure_service
    svc.initialize_job("JOB-ATOMIC-001", actor="test")
    original = svc.get_state("JOB-ATOMIC-001")

    def boom(self, job_id, state):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(ClosureService, "_atomic_write", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        svc.set_job_status(
            "JOB-ATOMIC-001", "acknowledged",
            actor={"open_id": "ou_x"}, expected_version=0,
        )

    # 原文件内容不变
    after = svc.get_state("JOB-ATOMIC-001")
    assert after["job_status"] == "open"
    assert after["version"] == 0


def test_set_event_status_legal_transition(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-EVT-001", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_e", "name": "Ev"}
    # pending → disposing
    s = svc.set_event_status(
        "JOB-EVT-001", "A6-MOCK-002", "disposing",
        actor=actor, expected_version=0,
    )
    target = next(e for e in s["events"] if e["risk_event_id"] == "A6-MOCK-002")
    assert target["event_status"] == "disposing"


def test_set_event_status_illegal_transition(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-EVT-002", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_e", "name": "Ev"}
    # pending → disposed 非法
    with pytest.raises(IllegalTransition):
        svc.set_event_status(
            "JOB-EVT-002", "A6-MOCK-002", "disposed",
            actor=actor, expected_version=0,
        )


def test_set_event_status_event_not_found(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-EVT-003", actor="test")
    with pytest.raises(InputValidationError):
        svc.set_event_status(
            "JOB-EVT-003", "A6-MOCK-NONE", "disposing",
            actor={"open_id": "ou_e"}, expected_version=0,
        )


def test_recompute_display_risk_level(closure_service):
    svc = closure_service
    from P8P9.tests.fixtures.sample_events import SAMPLE_RISK_EVENTS
    svc.initialize_job(
        "JOB-RCL-001", actor="test",
        events=[SAMPLE_RISK_EVENTS[0], SAMPLE_RISK_EVENTS[3]],  # 1级 + 4级
    )
    state = svc.get_state("JOB-RCL-001")
    assert state["display_risk_level"] == 4


def test_get_state_not_found(closure_service):
    svc = closure_service
    with pytest.raises(StateNotFound):
        svc.get_state("JOB-DOES-NOT-EXIST")


def test_list_events_default_excludes_disposed(closure_service):
    svc = closure_service
    svc.initialize_job("JOB-LIST-001", actor="test", events=[event_by_level(1), event_by_level(2)])
    actor = {"open_id": "ou_l", "name": "L"}
    svc.set_event_status("JOB-LIST-001", "A6-MOCK-001", "disposing", actor=actor)
    svc.set_event_status("JOB-LIST-001", "A6-MOCK-001", "disposed", actor=actor)

    events_default = svc.list_events("JOB-LIST-001")
    assert len(events_default) == 1  # 只剩 002

    events_all = svc.list_events("JOB-LIST-001", include_closed=True)
    assert len(events_all) == 2