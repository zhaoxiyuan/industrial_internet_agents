"""P8 模型配置应以根目录已激活的 A5 主模型为兜底。"""

from agents.model import config as model_config
from agents.model.config import Settings


def test_a5_main_model_fills_empty_legacy_fields(monkeypatch):
    for key in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "MODEL_PROVIDER",
        "OPENAI_TEMPERATURE", "OPENAI_MAX_TOKENS", "A5_LLM_API_KEY",
        "A5_LLM_BASE_URL", "A5_LLM_MODEL", "A5_LLM_PROTOCOL",
        "A5_LLM_TEMPERATURE", "A5_LLM_MAX_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(
        _env_file=None,
        OPENAI_MODEL="",
        A5_LLM_MODEL="MiniMax-M3",
        A5_LLM_PROTOCOL="openai",
        A5_LLM_BASE_URL="https://example.test/v1",
        A5_LLM_API_KEY="test-key",
        A5_LLM_TEMPERATURE=0.2,
        A5_LLM_MAX_TOKENS=1234,
    )
    assert settings.OPENAI_MODEL == "MiniMax-M3"
    assert settings.MODEL_PROVIDER == "openai"
    assert settings.OPENAI_BASE_URL == "https://example.test/v1"
    assert settings.OPENAI_API_KEY == "test-key"
    assert settings.TEMPERATURE == 0.2
    assert settings.MAX_TOKENS == 1234


def test_nonempty_legacy_model_keeps_precedence(monkeypatch):
    for key in ("OPENAI_MODEL", "A5_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None, OPENAI_MODEL="explicit", A5_LLM_MODEL="fallback")
    assert settings.OPENAI_MODEL == "explicit"


def test_get_settings_uses_current_root_env_over_old_container_snapshot(monkeypatch):
    current = {"A5_LLM_MODEL": "new-model", "A5_LLM_PROTOCOL": "openai"}
    monkeypatch.setattr(model_config, "dotenv_values", lambda _path: current)
    monkeypatch.setenv("A5_LLM_MODEL", "old-model")
    assert model_config.get_settings().OPENAI_MODEL == "new-model"
    current["A5_LLM_MODEL"] = "newer-model"
    assert model_config.get_settings().OPENAI_MODEL == "newer-model"
