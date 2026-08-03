"""
MiniMax LLM 封装
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from langchain.chat_models import init_chat_model


class Settings(BaseSettings):
    """LLM 配置"""
    OPENAI_API_KEY: str
    OPENAI_BASE_URL: str
    OPENAI_MODEL: str
    MODEL_PROVIDER: str

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"


def create_chat_model():
    settings = Settings()
    llm = init_chat_model(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model_provider=settings.MODEL_PROVIDER
    )
    return llm
