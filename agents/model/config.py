"""
共享配置模块
"""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """LLM 配置"""
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    OPENAI_MODEL: str = ""
    MODEL_PROVIDER: str = ""

    class Config:
        env_file = Path(__file__).parent.parent.parent / ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
