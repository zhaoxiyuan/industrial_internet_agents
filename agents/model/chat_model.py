"""
MiniMax LLM 封装
"""

from langchain.chat_models import init_chat_model
from agents.model.config import get_settings


def create_chat_model():
    settings = get_settings()
    llm = init_chat_model(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model_provider=settings.MODEL_PROVIDER,
        temperature=settings.TEMPERATURE,
        max_tokens=settings.MAX_TOKENS
    )
    return llm
