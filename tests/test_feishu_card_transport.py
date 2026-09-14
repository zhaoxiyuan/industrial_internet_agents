"""P8 飞书 Card 2.0 发送与原位更新协议回归测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATEWAY_ROOT = PROJECT_ROOT / "openclaw-channel-gateway-standalone"
for path in (PROJECT_ROOT, GATEWAY_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_send_card_uses_cardkit_reference_and_registers_handle():
    from feishu_gateway_cli import feishu_sender as sender

    card = {
        "schema": "2.0",
        "body": {"elements": [{"tag": "markdown", "content": "告警正文"}]},
    }
    send_result = SimpleNamespace(
        status="sent",
        intent_id="intent-1",
        platform_message_id="om-1",
        replayed=False,
    )

    with mock.patch.object(
        sender.feishu_card_api, "create_card_entity", return_value="7355372766134157313"
    ) as create_card, mock.patch.object(
        sender.feishu_card_api, "lookup_card_id", return_value=None
    ), mock.patch.object(
        sender, "send_message", return_value=send_result
    ) as send_message, mock.patch.object(
        sender.feishu_card_api, "register_card"
    ) as register_card:
        result = sender.send_to_group_card(
            card,
            chat_id="oc-test",
            account_id="P8",
            alert_id="P8J-test",
            idempotency_key="p8-idem-1",
        )

    assert result is send_result
    create_card.assert_called_once_with(
        card, account_id="P8"
    )
    sent = send_message.call_args.kwargs
    assert sent["msg_type"] == "interactive"
    assert json.loads(sent["content"]) == {
        "type": "card",
        "data": {"card_id": "7355372766134157313"},
    }
    assert register_card.call_count == 2
    register_card.assert_any_call(
        "P8J-test",
        "7355372766134157313",
        account_id="P8",
        sequence=0,
        card_json=card,
    )
    register_card.assert_any_call(
        "P8J-test",
        "7355372766134157313",
        account_id="P8",
        message_id="om-1",
        sequence=0,
        card_json=card,
    )


def test_update_card_entity_uses_nested_card_payload():
    from feishu_gateway_cli import feishu_card

    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {"code": 0, "msg": "success", "data": {}}
    card = {"schema": "2.0", "body": {"elements": []}}

    with mock.patch.object(
        feishu_card, "_resolve_account_credentials", return_value=("app", "secret", "feishu")
    ), mock.patch.object(
        feishu_card, "_get_tenant_access_token", return_value="token"
    ), mock.patch.object(
        feishu_card._cg_session, "put", return_value=response
    ) as put:
        feishu_card.update_card_entity(
            "card-1", card, sequence=2, op_uuid="update-1", account_id="P8"
        )

    body = put.call_args.kwargs["json"]
    assert body["card"]["type"] == "card_json"
    assert json.loads(body["card"]["data"]) == card
    assert body["sequence"] == 2
    assert body["uuid"] == "update-1"
    assert "type" not in body
    assert "data" not in body


def test_create_card_entity_uses_cardkit_v1_payload():
    from feishu_gateway_cli import feishu_card

    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {
        "code": 0,
        "msg": "success",
        "data": {"card_id": "7355372766134157313"},
    }
    card = {"schema": "2.0", "body": {"elements": []}}

    with mock.patch.object(
        feishu_card, "_resolve_account_credentials", return_value=("app", "secret", "feishu")
    ), mock.patch.object(
        feishu_card, "_get_tenant_access_token", return_value="token"
    ), mock.patch.object(
        feishu_card._cg_session, "post", return_value=response
    ) as post:
        card_id = feishu_card.create_card_entity(card, account_id="P8")

    assert card_id == "7355372766134157313"
    assert post.call_args.args[0].endswith("/open-apis/cardkit/v1/cards")
    assert post.call_args.kwargs["json"] == {
        "type": "card_json",
        "data": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
    }


def test_update_card_in_place_prefers_cardkit_and_advances_sequence():
    from feishu_gateway_cli import feishu_card

    entry = {
        "card_id": "card-1",
        "message_id": "om-stored",
        "account_id": "P8",
        "sequence": 4,
    }
    processed = {"schema": "2.0", "body": {"elements": []}}

    with mock.patch.object(
        feishu_card, "lookup_card_id", return_value=entry
    ), mock.patch.object(
        feishu_card, "update_card_entity"
    ) as update, mock.patch.object(
        feishu_card, "register_card"
    ) as register, mock.patch.object(feishu_card.time, "sleep"):
        feishu_card._update_card_in_place(
            alert_id="P8J-test", message_id="om-callback", processed_card=processed
        )

    update.assert_called_once_with(
        "card-1", processed, sequence=5, account_id="P8"
    )
    register.assert_called_once_with(
        "P8J-test",
        "card-1",
        account_id="P8",
        message_id="om-callback",
        sequence=5,
    )


def test_update_card_in_place_patches_legacy_inline_message():
    from feishu_gateway_cli import feishu_card

    processed = {"schema": "2.0", "body": {"elements": []}}
    with mock.patch.object(
        feishu_card, "lookup_card_id", return_value=None
    ), mock.patch.object(feishu_card, "_patch_feishu_message") as patch_message:
        feishu_card._update_card_in_place(
            alert_id="legacy-alert",
            message_id="om-legacy",
            processed_card=processed,
        )

    patch_message.assert_called_once_with(
        "om-legacy", processed, account_id=None
    )


def test_callback_dispatches_p8_action_then_in_place_update():
    from feishu_gateway_cli import feishu_card

    payload = {
        "header": {"event_id": "evt-1", "event_type": "card.action.trigger"},
        "event": {
            "action": {
                "value": json.dumps(
                    {"action": "handle", "alert_id": "P8J-test", "job_id": "JOB-1"}
                ),
                "text": {"content": "立即处理"},
            },
            "operator": {"open_id": "ou-1", "user_name": "测试员"},
            "context": {"open_chat_id": "oc-1", "open_message_id": "om-1"},
        },
    }

    with mock.patch.object(
        feishu_card, "_check_already_processed", return_value=None
    ), mock.patch.object(feishu_card, "_write_audit"), mock.patch.object(
        feishu_card, "_handle_card_action_with_llm_async"
    ) as dispatch:
        response = feishu_card.process_card_callback(payload)

    assert response["toast"]["type"] == "success"
    args = dispatch.call_args.kwargs
    assert args["alert_id"] == "P8J-test"
    assert args["job_id"] == "JOB-1"
    assert args["message_id"] == "om-1"
    assert args["processed_card"]["schema"] == "2.0"
    assert args["processed_card"]["header"]["template"] == "green"


def test_follow_up_card_preserves_content_and_adds_terminal_and_link_buttons(monkeypatch):
    from feishu_gateway_cli import feishu_card

    monkeypatch.setenv("P8_DETAIL_BASE_URL", "https://example.ngrok.app")
    original = {
        "schema": "2.0",
        "header": {"template": "red", "title": {"tag": "plain_text", "content": "告警"}},
        "body": {"elements": [
            {"tag": "markdown", "content": "原始处置信息"},
            {"tag": "column_set", "columns": []},
        ]},
    }
    card = feishu_card._build_follow_up_card(
        original_card=original, alert_id="P8J/1", job_id="JOB 1"
    )

    assert card["body"]["elements"][0]["content"] == "原始处置信息"
    raw = json.dumps(card, ensure_ascii=False)
    for action in ("approve", "reject", "escalate", "resume"):
        assert f'\\"action\\":\\"{action}\\"' in raw
    link = card["body"]["elements"][-1]["actions"][0]
    assert "value" not in link
    assert link["behaviors"] == [{
        "type": "open_url",
        "default_url": "https://example.ngrok.app/p8_detail.html?job_id=JOB%201&p8_job_id=P8J%2F1",
    }]


def test_only_terminal_audit_record_locks_further_decisions(tmp_path, monkeypatch):
    from feishu_gateway_cli import feishu_card

    audit = tmp_path / "callbacks.jsonl"
    audit.write_text(
        json.dumps({"alert_id": "P8J-1", "action": "handle"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(feishu_card, "_AUDIT_LOG", audit)
    assert feishu_card._check_already_processed("P8J-1") is None

    with audit.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"alert_id": "P8J-1", "action": "reject"}) + "\n")
    assert feishu_card._check_already_processed("P8J-1")["action"] == "reject"
