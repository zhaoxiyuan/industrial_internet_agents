"""
MiniMax LLM 封装
"""

import logging
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from agents.model.config import get_settings

logger = logging.getLogger("llm_callback")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class LLMLoggingCallback(BaseCallbackHandler):
    """LLM 调用日志回调 - 记录每次 LLM 的输入输出"""

    def __init__(self, agent_name: str = "LLM", job_id: str = "*"):
        super().__init__()
        self.agent_name = agent_name
        self.job_id = job_id

    def _push_ws(self, level: str, message: str, data: dict = None):
        """推送日志到 WebSocket"""
        try:
            from agents.utils.logging_handler import push_websocket_log
            push_websocket_log(self.job_id, level, "LLM", message, data)
        except Exception:
            pass  # WebSocket 未连接时静默忽略

    def on_chat_model_start(self, serialized, messages, **kwargs):
        """LLM 调用开始 - 记录完整消息"""
        logger.info(f"[{self.agent_name}] >>> LLM 调用开始")
        # 推送配置参数
        settings = get_settings()
        llm_config = {
            "model": settings.OPENAI_MODEL,
            "base_url": settings.OPENAI_BASE_URL,
            "temperature": settings.TEMPERATURE,
            "max_tokens": settings.MAX_TOKENS,
        }
        self._push_ws("INFO", f">>> LLM 调用开始", {"llm_config": llm_config})
        # 推送每条消息
        for i, msg_list in enumerate(messages):
            for msg in msg_list:
                content = getattr(msg, 'content', str(msg))
                logger.info(f"[{self.agent_name}]   消息 {i}: {content[:500]}...")
                self._push_ws("INFO", f"LLM 输入消息 {i}", {"content": content[:2000]})

    def on_llm_end(self, response, **kwargs):
        """LLM 调用结束 - 记录完整响应"""
        try:
            for generations in response.generations:
                for gen in generations:
                    text = getattr(gen, 'text', str(gen))
                    logger.info(f"[{self.agent_name}] <<< LLM 响应: {text[:500]}...")
                    self._push_ws("INFO", f"<<< LLM 响应", {"response": text[:2000]})
        except Exception as e:
            logger.warning(f"[{self.agent_name}] LLM 响应解析失败: {e}")
            self._push_ws("WARNING", f"LLM 响应解析失败: {e}")

    def on_llm_error(self, error, **kwargs):
        """LLM 调用错误"""
        logger.error(f"[{self.agent_name}] !!! LLM 调用错误: {error}")
        self._push_ws("ERROR", f"LLM 调用错误: {error}")


def create_chat_model(callbacks=None):
    """创建 LLM 模型

    Args:
        callbacks: 可选的回调处理器列表
    """
    settings = get_settings()
    llm = init_chat_model(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model_provider=settings.MODEL_PROVIDER,
        temperature=settings.TEMPERATURE,
        max_tokens=settings.MAX_TOKENS,
        callbacks=callbacks,
    )
    return llm


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


def create_chat_model_with_logging(agent_name: str = "LLM", job_id: str = "*"):
    """创建带日志回调的 LLM 模型

    Args:
        agent_name: Agent 名称
        job_id: 作业ID，用于 WebSocket 推送
    """
    callback = LLMLoggingCallback(agent_name, job_id)
    return create_chat_model(callbacks=[callback])
