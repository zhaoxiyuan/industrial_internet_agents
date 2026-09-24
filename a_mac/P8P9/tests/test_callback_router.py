# P8P9/tests/test_callback_router.py — 飞书 callback 路由测试

from __future__ import annotations
import json
import pytest

from P8P9.services.callback_router import (
    route_card_callback,
    InvalidAction, InvalidOperator, CallbackError,
)
from P8P9.tests.fixtures.sample_events import event_by_level


def _wrap(action_value: dict, *, operator_open_id="ou_test_user", form_value=None):
    """构造飞书 card.action.trigger payload。"""
    return {
        "header": {"event_id": "evt_x", "event_type": "card.action.trigger"},
        "event": {
            "action": {
                "value": json.dumps(action_value, ensure_ascii=False),
                "form_value": form_value or {},
            },
            "operator": {"open_id": operator_open_id, "user_name": "测试员"},
        },
    }


# ─── acknowledge_disposition ─────────────────────────────────────────────────

def test_route_acknowledge_disposition(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-RT-ACK", actor="test", events=[event_by_level(2)])
    payload = _wrap({
        "action": "acknowledge_disposition",
        "job_id": "JOB-RT-ACK",
        "expected_version": 0,
    })
    result = route_card_callback(payload)
    assert result["job_status"] == "acknowledged"


def test_route_unknown_action(mock_card_renderer, closure_service):
    payload = _wrap({
        "action": "completely_made_up",
        "job_id": "JOB-RT-X",
    })
    with pytest.raises(InvalidAction):
        route_card_callback(payload)


def test_route_missing_operator(mock_card_renderer, closure_service):
    payload = _wrap({
        "action": "acknowledge_disposition",
        "job_id": "JOB-RT-NOOP",
    })
    payload["event"]["operator"] = {}  # 清掉 open_id
    with pytest.raises(InvalidOperator):
        route_card_callback(payload)


def test_route_missing_job_id(mock_card_renderer, closure_service):
    payload = _wrap({
        "action": "acknowledge_disposition",
    })
    with pytest.raises(InvalidAction):
        route_card_callback(payload)


# ─── submit_rectification_materials（带 form_value） ─────────────────────────

def test_route_submit_materials_with_form(mock_card_renderer, mock_audit_scheduler, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-RT-SUB", actor="test", events=[event_by_level(2)])
    actor = {"open_id": "ou_test_user", "name": "测试员"}
    # 先 acknowledge 拿 accepted_by
    from P8P9 import business_actions
    business_actions.acknowledge_disposition("JOB-RT-SUB", actor=actor, expected_version=0)
    # 进入 rectifying
    svc.set_job_status(
        "JOB-RT-SUB", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-RT-SUB")["version"],
    )
    payload = _wrap(
        {
            "action": "submit_rectification_materials",
            "job_id": "JOB-RT-SUB",
            "expected_version": svc.get_state("JOB-RT-SUB")["version"],
        },
        form_value={
            "review_text": "已补充消防器材；现场通风良好；可继续作业。",
            "submissions": [{"kind": "photo"}],
        },
    )
    result = route_card_callback(payload)
    assert result["job_status"] == "materials_in_audit"


# ─── download_attachment ─────────────────────────────────────────────────────

def test_route_download_attachment(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-RT-DL", actor="test", events=[event_by_level(2)])
    payload = _wrap({
        "action": "download_attachment",
        "job_id": "JOB-RT-DL",
        "evidence_id": "ev-123",
    })
    result = route_card_callback(payload)
    assert "token" in result
    assert result["short_url"].startswith("/dl/")


# ─── 双层 JSON value 兼容 ────────────────────────────────────────────────────

def test_route_double_layer_json_value(mock_card_renderer, closure_service):
    """飞书有时把 value 包两次 JSON：value='"{...}"'。"""
    svc = closure_service
    svc.initialize_job("JOB-RT-DBL", actor="test", events=[event_by_level(2)])
    inner = json.dumps({"action": "acknowledge_disposition", "job_id": "JOB-RT-DBL", "expected_version": 0})
    outer_value = json.dumps(inner)  # 双层
    payload = {
        "header": {"event_id": "evt_x", "event_type": "card.action.trigger"},
        "event": {
            "action": {"value": outer_value, "form_value": {}},
            "operator": {"open_id": "ou_test_user"},
        },
    }
    result = route_card_callback(payload)
    assert result["job_status"] == "acknowledged"


# ─── 业务异常包装为 CallbackError ────────────────────────────────────────────

def test_route_business_error_wrapped(mock_card_renderer, closure_service):
    """submit_materials 业务异常 → CallbackError。"""
    svc = closure_service
    svc.initialize_job("JOB-RT-ERR", actor="test", events=[event_by_level(2)])
    # 不 acknowledge 直接 submit → IllegalTransition
    payload = _wrap({
        "action": "submit_rectification_materials",
        "job_id": "JOB-RT-ERR",
        "expected_version": 0,
    }, form_value={"review_text": "无效提交测试文本足够长"})
    with pytest.raises(CallbackError):
        route_card_callback(payload)


# ─── record_closure_review ───────────────────────────────────────────────────

def test_route_record_review_approved(mock_card_renderer, closure_service):
    svc = closure_service
    svc.initialize_job("JOB-RT-CLOSE", actor="test", events=[event_by_level(3)])
    actor = {"open_id": "ou_test_user", "name": "测试员"}
    from P8P9 import business_actions
    business_actions.acknowledge_disposition("JOB-RT-CLOSE", actor=actor, expected_version=0)
    svc.set_job_status(
        "JOB-RT-CLOSE", "rectifying",
        actor=actor, expected_version=svc.get_state("JOB-RT-CLOSE")["version"],
    )
    business_actions.submit_rectification_materials(
        "JOB-RT-CLOSE",
        review_text="已提交整改证据。请审核。",
        submissions=[], actor=actor, expected_version=svc.get_state("JOB-RT-CLOSE")["version"],
    )
    svc.set_job_status(
        "JOB-RT-CLOSE", "waiting_human_review",
        actor=actor, expected_version=svc.get_state("JOB-RT-CLOSE")["version"],
    )
    payload = _wrap({
        "action": "record_closure_review",
        "decision": "approved",
        "job_id": "JOB-RT-CLOSE",
        "expected_version": svc.get_state("JOB-RT-CLOSE")["version"],
    }, form_value={"comment": "终审通过；可关闭归档。"})
    result = route_card_callback(payload)
    assert result["job_status"] == "closed"
    assert result["archived_to_lt"] is True