from P8P9 import web_server
from P8P9.services import callback_router
from P8P9.services import card_render


def test_successful_callback_does_not_return_cardkit_json_as_callback_card(monkeypatch):
    # CardKit entity is updated by the business action; callback response must
    # not attempt a second update using CardKit's card_json wire format.
    monkeypatch.setattr(
        callback_router,
        "route_card_callback",
        lambda _payload: {"_card_json": {"schema": "2.0", "body": {"elements": []}}},
    )
    response = web_server.app.test_client().post(
        "/feishu/card/callback", json={"event": {"action": {"value": {}}}}
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "toast": {"type": "success", "content": "已记录您的处置"},
        "card": {"type": "raw", "data": {"schema": "2.0", "body": {"elements": []}}},
    }


def test_callback_queues_cardkit_update_without_sleep_or_network(monkeypatch):
    state = {"job_id": "JOB-TEST", "version": 3,
             "card_binding": {"card_id": "card_1", "alert_id": "alert_1"}}
    monkeypatch.setattr(card_render, "ClosureService", lambda: type(
        "Service", (), {"get_state": lambda self, _job_id: state})())
    monkeypatch.setattr(card_render, "_FEISHU_AVAILABLE", True)
    monkeypatch.setattr(card_render, "_build_card_json", lambda *_args, **_kwargs: {"schema": "2.0"})
    queued = []
    monkeypatch.setattr(card_render, "_queue_card_update", queued.append)
    monkeypatch.setattr(card_render, "_throttle", lambda: (_ for _ in ()).throw(AssertionError("slept")))
    monkeypatch.setattr(card_render.feishu_card, "update_card_entity",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network")))
    with card_render.card_callback_context():
        result = card_render.update_job_card("JOB-TEST", 3)
    assert result["status"] == "queued"
    assert result["card_json"] == {"schema": "2.0"}
    assert queued == ["JOB-TEST"]
