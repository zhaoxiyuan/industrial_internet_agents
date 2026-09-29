# P8P9/tests/test_cards.py — 6 态卡片 build_job_card 测试
#
# 覆盖：
#   - 7 个状态（initial + 6 transition） → build_job_card 输出合法 schema
#   - 标题色（closed → grey；else by display_risk_level）
#   - 危急前缀 + carmine
#   - waiting_human_review / ready_to_close 含 form 按钮
#   - acknowledged 退接按钮仅 accepted_by 本人 enable

from __future__ import annotations
import pytest

from P8P9.cards import build_job_card
from P8P9.tests.fixtures.sample_state import SAMPLE_STATES


ALL_STATUSES = ["open", "acknowledged", "rectifying", "materials_in_audit",
                "waiting_human_review", "ready_to_close", "closed"]


def _is_button(elem):
    return elem.get("tag") == "button"


def _find_buttons(elements):
    """递归收集所有 button（含 form_action 内的 button）。"""
    out = []
    for e in elements:
        if _is_button(e):
            out.append(e)
        # column_set → columns[*].elements[*]
        if e.get("tag") == "column_set":
            for col in e.get("columns", []):
                out.extend(_find_buttons(col.get("elements", [])))
    return out


def test_all_statuses_build_legal_card():
    for status in ALL_STATUSES:
        state = SAMPLE_STATES[status]
        card = build_job_card(state, version=state["version"], entry_url="http://x")
        assert card["schema"] == "2.0"
        assert card["config"]["update_multi"] is True
        assert "header" in card
        assert "title" in card["header"]
        assert card["body"]["elements"], f"{status} 卡片 body 为空"


def test_title_color_grey_when_closed():
    state = SAMPLE_STATES["closed"]
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    assert card["header"]["template"] == "grey"


def test_title_color_by_risk_level_when_not_closed():
    state = dict(SAMPLE_STATES["acknowledged"])  # display_risk_level=2
    state["display_risk_level"] = 4
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    assert card["header"]["template"] == "red"


def test_critical_level_uses_carmine():
    state = dict(SAMPLE_STATES["acknowledged"])
    state["display_risk_level"] = 5
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    assert card["header"]["template"] == "carmine"
    title = card["header"]["title"]["content"]
    assert "🚨" in title
    assert "危急" in title


def test_open_card_has_acknowledge_button():
    state = SAMPLE_STATES["open"]
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    buttons = _find_buttons(card["body"]["elements"])
    actions = []
    for b in buttons:
        import json
        try:
            v = json.loads(b.get("value", "{}"))
            if isinstance(v, dict):
                actions.append(v.get("action"))
        except Exception:
            pass
    assert "acknowledge_disposition" in actions


def test_acknowledged_card_relinquish_only_for_accepted_user():
    state = SAMPLE_STATES["acknowledged"]
    # accepted_by = ou_test_user_001

    # 1) actor 一致：relinquish 按钮正常
    card_ok = build_job_card(
        state, version=state["version"], entry_url="http://x",
        actor_open_id="ou_test_user_001",
    )
    buttons_ok = [button for form in card_ok["body"]["elements"]
                  if form.get("tag") == "form" for button in form["elements"]
                  if button.get("tag") == "button"]
    has_relinquish = any(
        b.get("value", {}).get("action") == "relinquish_job" and not b.get("disabled")
        for b in buttons_ok
    )
    assert has_relinquish, "accepted_by 一致时应有 relinquish_job 按钮"

    # 2) actor 不一致：relinquish 按钮被禁用（action=relinquish_job_disabled）
    card_bad = build_job_card(
        state, version=state["version"], entry_url="http://x",
        actor_open_id="ou_someone_else",
    )
    buttons_bad = [button for form in card_bad["body"]["elements"]
                   if form.get("tag") == "form" for button in form["elements"]
                   if button.get("tag") == "button"]
    has_disabled = any(
        b.get("value", {}).get("action") == "relinquish_job" and b.get("disabled")
        for b in buttons_bad
    )
    assert has_disabled, "actor 不一致时应显示 disabled 退接按钮"


def test_rectifying_card_restores_submit_and_relinquish_after_rejection():
    state = {
        **SAMPLE_STATES["rectifying"],
        "review": {"history": [{"decision": "rejected"}], "last_comment": "证据不足，请重新提交。"},
    }
    card = build_job_card(
        state, version=state["version"], entry_url="http://x",
        actor_open_id="ou_test_user_001",
        upload_url_factory=lambda job_id, target, actor: f"http://x/{job_id}/{target}?open_id={actor}",
    )
    elements = card["body"]["elements"]
    forms = [element for element in elements if element.get("tag") == "form"]
    actions = {
        button["value"]["action"]: button
        for form in forms for button in form["elements"]
        if button.get("tag") == "button"
    }
    assert set(actions) == {"submit_rectification_materials", "relinquish_job"}
    assert all(not button.get("disabled") for button in actions.values())
    assert all(button["value"]["expected_version"] == state["version"] for button in actions.values())
    assert any("证据不足，请重新提交" in element.get("content", "") for element in elements)
    assert any("上传附件" in str(element) for element in elements)

    other_user_card = build_job_card(
        state, version=state["version"], entry_url="http://x",
        actor_open_id="ou_someone_else",
    )
    other_forms = [element for element in other_user_card["body"]["elements"] if element.get("tag") == "form"]
    assert all(
        button.get("disabled")
        for form in other_forms for button in form["elements"]
        if button.get("tag") == "button"
    )


def test_waiting_human_review_has_form_buttons():
    state = SAMPLE_STATES["waiting_human_review"]
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    form_buttons = [b for b in card["body"]["elements"] if b.get("form_action")]
    actions = [
        (b.get("form_action") or {}).get("on_action", {}).get("action")
        for b in form_buttons
    ]
    assert "record_closure_review" in actions
    assert "escalate_risk" in actions
    assert "downgrade_risk" in actions


def test_ready_to_close_has_close_button():
    state = SAMPLE_STATES["ready_to_close"]
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    form_buttons = [b for b in card["body"]["elements"] if b.get("form_action")]
    actions = [
        (b.get("form_action") or {}).get("on_action", {}).get("action")
        for b in form_buttons
    ]
    assert "record_closure_review" in actions


def test_closed_card_has_attachment_links():
    """closed 态卡片含「查看附件」link_button。"""
    state = SAMPLE_STATES["closed"]
    state["events"] = [
        {
            "risk_event_id": "A6-CLOSED-001",
            "risk_level": 2,
            "risk_basis": "test basis",
            "event_status": "disposed",
            "evidence": {"evidence_id": "ev-1"},
        }
    ]
    calls = []

    def factory(evidence_id):
        calls.append(evidence_id)
        return f"/dl/fake-{evidence_id}"

    card = build_job_card(
        state, version=state["version"], entry_url="http://x",
        dl_link_factory=factory,
    )
    assert calls == ["ev-1"]
    # 找到 dl/fake-ev-1 的 link button
    found = False
    for elem in card["body"]["elements"]:
        if elem.get("tag") == "button" and elem.get("behaviors"):
            url = elem["behaviors"][0].get("default_url", "")
            if "/dl/fake-ev-1" in url:
                found = True
                break
    assert found


def test_unknown_status_raises():
    state = {"job_id": "X", "job_status": "BOGUS", "version": 0}
    with pytest.raises(ValueError):
        build_job_card(state, version=0, entry_url="http://x")


def test_value_must_be_json_string():
    """Card 2.0 要求 button.value 是 JSON 字符串（不是 dict）。"""
    import json
    state = SAMPLE_STATES["open"]
    card = build_job_card(state, version=state["version"], entry_url="http://x")
    for elem in card["body"]["elements"]:
        if elem.get("tag") == "button" and elem.get("value"):
            assert isinstance(elem["value"], str), "Card 2.0 要求 button.value 是字符串"
            json.loads(elem["value"])  # 必须能解析
