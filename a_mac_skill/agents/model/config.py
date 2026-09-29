"""
共享配置模块
"""
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field
from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings


ROOT_ENV_FILE = Path(__file__).parent.parent.parent / ".env"
MODEL_ENV_KEYS = {
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "MODEL_PROVIDER",
    "OPENAI_TEMPERATURE", "OPENAI_MAX_TOKENS", "A5_LLM_API_KEY",
    "A5_LLM_BASE_URL", "A5_LLM_MODEL", "A5_LLM_PROTOCOL",
    "A5_LLM_TEMPERATURE", "A5_LLM_MAX_TOKENS",
}


class Settings(BaseSettings):
    """LLM 配置"""
    model_config = ConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = ""
    MODEL_PROVIDER: str = ""
    TEMPERATURE: float = Field(default=0.7, validation_alias="OPENAI_TEMPERATURE")
    MAX_TOKENS: int = Field(default=8192, validation_alias="OPENAI_MAX_TOKENS")

    # 根目录 .env 中由配置页维护的主模型配置。旧 OPENAI_* 仅在非空时优先。
    A5_LLM_API_KEY: str = ""
    A5_LLM_BASE_URL: str = ""
    A5_LLM_MODEL: str = ""
    A5_LLM_PROTOCOL: str = ""
    A5_LLM_TEMPERATURE: float | None = None
    A5_LLM_MAX_TOKENS: int | None = None

    @model_validator(mode="after")
    def resolve_main_model(self) -> "Settings":
        self.OPENAI_API_KEY = self.OPENAI_API_KEY or self.A5_LLM_API_KEY
        self.OPENAI_BASE_URL = self.OPENAI_BASE_URL or self.A5_LLM_BASE_URL
        self.OPENAI_MODEL = self.OPENAI_MODEL or self.A5_LLM_MODEL
        self.MODEL_PROVIDER = self.MODEL_PROVIDER or self.A5_LLM_PROTOCOL
        if "TEMPERATURE" not in self.model_fields_set and self.A5_LLM_TEMPERATURE is not None:
            self.TEMPERATURE = self.A5_LLM_TEMPERATURE
        if "MAX_TOKENS" not in self.model_fields_set and self.A5_LLM_MAX_TOKENS is not None:
            self.MAX_TOKENS = self.A5_LLM_MAX_TOKENS
        return self


def get_settings() -> Settings:
    """每次从根 .env 获取激活配置；文件值优先于容器启动时的环境快照。"""
    current = dotenv_values(ROOT_ENV_FILE) if ROOT_ENV_FILE.exists() else {}
    return Settings(**{key: value for key, value in current.items()
                       if key in MODEL_ENV_KEYS and value is not None})


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
