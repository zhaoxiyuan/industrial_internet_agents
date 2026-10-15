"""
MiniMax LLM 封装
"""

import logging
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from .config import get_llm_params

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
        llm_config = get_llm_params()
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


def create_chat_model(callbacks=None, temperature=None, reasoning_effort=None):
    """创建 LLM 模型

    Args:
        callbacks: 可选的回调处理器列表
        temperature: 覆盖全局温度。**注意：服务端处于思考模式时该参数不生效**（不报错、也不起作用）。
            DeepSeek 的 `thinking` 默认开启，所以这个值目前只对已关闭思考的调用有实际意义。
        reasoning_effort: 思考强度 `low` / `high` / `max`；None 表示用服务默认（实测为 `high`）。
            真实判定任务里思考会消耗数千 token（占输出 90% 以上、单次 30~130 秒），
            换成 `low` 后平均约 28 秒、判定逐条一致（详见 docs/update/update_2026-10-13.md）。
    """
    from .config import get_settings
    settings = get_settings()
    extra = {}
    if reasoning_effort:
        # reasoning_effort 作为顶层参数直传会被 LangChain 丢弃（实测 reasoning 纹丝不动），
        # 必须放进 extra_body 才能到达服务端。
        extra["extra_body"] = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
        }
    llm = init_chat_model(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model_provider=settings.MODEL_PROVIDER,
        temperature=settings.TEMPERATURE if temperature is None else temperature,
        max_tokens=settings.MAX_TOKENS,
        callbacks=callbacks,
        **extra,
    )
    return llm


def create_chat_model_with_logging(agent_name: str = "LLM", job_id: str = "*", temperature=None,
                                   reasoning_effort=None):
    """创建带日志回调的 LLM 模型

    Args:
        agent_name: Agent 名称
        job_id: 作业ID，用于 WebSocket 推送和 LangSmith 关联
        temperature: 覆盖全局温度（思考模式下不生效，见 create_chat_model）
        reasoning_effort: 思考强度 low/high/max；None 表示用服务默认
    """
    from agents.model.langsmith_config import LangSmithConfig

    callbacks = []

    # 添加现有日志回调
    callbacks.append(LLMLoggingCallback(agent_name, job_id))

    # 添加 LangSmith 回调（仅当配置了 API key 时）
    langsmith_cb = LangSmithConfig.get_callback(
        tags=[agent_name, f"job:{job_id}"],
    )
    if langsmith_cb:
        callbacks.append(langsmith_cb)

    return create_chat_model(callbacks=callbacks, temperature=temperature,
                             reasoning_effort=reasoning_effort)
