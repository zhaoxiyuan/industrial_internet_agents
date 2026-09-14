"""
共享配置模块
"""
from pathlib import Path

from pydantic import Field
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """LLM 配置"""
    model_config = ConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = ""
    MODEL_PROVIDER: str = ""
    TEMPERATURE: float = Field(default=0.7, validation_alias="OPENAI_TEMPERATURE")
    MAX_TOKENS: int = Field(default=8192, validation_alias="OPENAI_MAX_TOKENS")


def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


def get_llm_params() -> dict:
    """获取当前 LLM 配置参数（用于日志记录）"""
    settings = get_settings()
    return {
        "model": settings.OPENAI_MODEL,
        "api_key": settings.OPENAI_API_KEY[:10] + "..." if settings.OPENAI_API_KEY else "",
        "base_url": settings.OPENAI_BASE_URL,
        "temperature": settings.TEMPERATURE,
        "max_tokens": settings.MAX_TOKENS,
        "model_provider": settings.MODEL_PROVIDER,
    }
