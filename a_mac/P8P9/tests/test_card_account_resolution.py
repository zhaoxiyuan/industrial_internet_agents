from pathlib import Path

from feishu_gateway_cli import feishu_card


def test_card_index_uses_project_data_directory():
    project = Path(feishu_card.__file__).resolve().parent.parent
    assert feishu_card._CARD_INDEX == project / "data" / "feishu_card_index.json"


def test_single_account_is_selected_without_hardcoded_name(monkeypatch):
    monkeypatch.delenv("FEISHU_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("FEISHU_EXAMPLE_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_EXAMPLE_APP_SECRET", "test_secret")
    assert feishu_card.resolve_card_account_id() == "EXAMPLE"


def test_explicit_account_wins(monkeypatch):
    monkeypatch.setenv("FEISHU_ACCOUNT_ID", "another")
    assert feishu_card.resolve_card_account_id("chosen") == "chosen"
