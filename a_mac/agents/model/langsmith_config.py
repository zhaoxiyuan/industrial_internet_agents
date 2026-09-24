"""LangSmith 配置管理模块

LangSmith 0.10+ 集成方式
"""

import os
import json
from typing import Optional, Any, Dict, List
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class LangSmithCallbackHandler(BaseCallbackHandler):
    """
    LangSmith 回调处理器

    将 LangChain callback 事件转发到 LangSmith

    LangSmith 0.10+ 使用 trace 机制，不再有独立的 CallbackHandler
    """

    def __init__(
        self,
        project_name: str = "industrial-internet-agents",
        tags: Optional[List[str]] = None,
        **kwargs
    ):
        super().__init__()
        self.project_name = project_name
        self.tags = tags or []
        self._runs: List[Any] = []
        self._client = None

    def _get_client(self):
        """获取 LangSmith 客户端"""
        if self._client is None:
            from langsmith import Client
            self._client = Client()
        return self._client

    def on_llm_start(self, serialized: Dict, prompts: List[str], **kwargs) -> None:
        """LLM 调用开始"""
        client = self._get_client()
        run_config = {
            "name": serialized.get("name", "llm") if isinstance(serialized, dict) else "llm",
            "run_type": "llm",
            "project_name": self.project_name,
            "tags": self.tags,
            "inputs": {"prompts": prompts},
            "extra": kwargs,
        }
        run = client.create_run(**run_config)
        self._runs.append(run)

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        """LLM 调用结束"""
        if not self._runs:
            return

        client = self._get_client()
        run = self._runs.pop()

        generations = []
        try:
            for generation_list in response.generations:
                for gen in generation_list:
                    generations.append(getattr(gen, 'text', str(gen)))
        except Exception:
            generations = [str(response)]

        try:
            client.update_run(
                run.id,
                outputs={"generations": generations}
            )
        except Exception as e:
            # 静默处理更新失败
            pass

    def on_llm_error(self, error: Exception, **kwargs) -> None:
        """LLM 调用错误"""
        if not self._runs:
            return

        client = self._get_client()
        run = self._runs.pop()

        try:
            client.update_run(
                run.id,
                error={"error": str(error)}
            )
        except Exception:
            pass

    def on_tool_start(self, serialized: Dict, input_str: str, **kwargs) -> None:
        """工具调用开始"""
        client = self._get_client()
        tool_name = serialized.get("name", "tool") if isinstance(serialized, dict) else "tool"
        run_config = {
            "name": tool_name,
            "run_type": "tool",
            "project_name": self.project_name,
            "tags": self.tags,
            "inputs": {"input": input_str},
            "extra": kwargs,
        }
        run = client.create_run(**run_config)
        self._runs.append(run)

    def on_tool_end(self, output: str, **kwargs) -> None:
        """工具调用结束"""
        if not self._runs:
            return

        client = self._get_client()
        run = self._runs.pop()

        try:
            client.update_run(
                run.id,
                outputs={"output": output}
            )
        except Exception:
            pass

    def on_tool_error(self, error: Exception, **kwargs) -> None:
        """工具调用错误"""
        if not self._runs:
            return

        client = self._get_client()
        run = self._runs.pop()

        try:
            client.update_run(
                run.id,
                error={"error": str(error)}
            )
        except Exception:
            pass

    def on_agent_action(self, action: Any, **kwargs) -> None:
        """Agent 动作"""
        pass

    def on_agent_finish(self, finish: Any, **kwargs) -> None:
        """Agent 完成"""
        pass


class LangSmithConfig:
    """LangSmith 配置管理"""

    @staticmethod
    def is_enabled() -> bool:
        """检查是否启用 LangSmith 追踪"""
        api_key = os.getenv("LANGCHAIN_API_KEY")
        return bool(api_key and api_key.strip())

    @staticmethod
    def get_callback(
        project_name: str = "industrial-internet-agents",
        tags: Optional[list[str]] = None,
    ) -> Optional[LangSmithCallbackHandler]:
        """
        创建 LangSmith 回调处理器

        Args:
            project_name: LangSmith 项目名称
            tags: 追踪标签列表

        Returns:
            LangSmithCallbackHandler 实例，如果未启用则返回 None
        """
        if not LangSmithConfig.is_enabled():
            return None

        return LangSmithCallbackHandler(
            project_name=project_name,
            tags=tags,
        )