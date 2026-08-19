"""
共享配置模块
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """LLM 配置"""
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = ""
    MODEL_PROVIDER: str = ""
    TEMPERATURE: float = Field(default=0.7, validation_alias="OPENAI_TEMPERATURE")
    MAX_TOKENS: int = Field(default=8192, validation_alias="OPENAI_MAX_TOKENS")

    # ★ 2026-08-18：env 中常含其它子系统变量（feishu_user_map / gateway_host /
    #   cg_api_key / openai_provider 等），与本 Settings 无关；用 extra='ignore'
    #   静默丢弃，避免 Bot 模式调用 LLM 时被 pydantic v2 拒收。
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
