"""
LangChain 日志回调处理器
用于记录 LLM 调用和工具调用的输入输出
"""
import json
import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

# 配置日志
logger = logging.getLogger("agent_logging")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# 日志 WebSocket 推送队列（由 server.py 设置）
_logs_broadcast_queue: Optional[queue.Queue] = None
_queue_lock = threading.Lock()


def _format_messages(messages: List[BaseMessage]) -> List[Dict]:
    """格式化消息列表为可 JSON 序列化的字典"""
    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            result.append({"type": "HumanMessage", "content": msg.content})
        elif isinstance(msg, AIMessage):
            result.append({"type": "AIMessage", "content": msg.content})
        elif isinstance(msg, SystemMessage):
            result.append({"type": "SystemMessage", "content": msg.content})
        else:
            result.append({"type": msg.__class__.__name__, "content": getattr(msg, 'content', str(msg))})
    return result


def _truncate_content(content: Any, max_length: int = 500) -> Any:
    """截断内容以避免日志过长"""
    if isinstance(content, str):
        return content[:max_length] + "..." if len(content) > max_length else content
    elif isinstance(content, list):
        return [_truncate_content(item, max_length) for item in content[:10]]
    elif isinstance(content, dict):
        return {k: _truncate_content(v, max_length) for k, v in list(content.items())[:20]}
    return content


def _safe_serialize(data: Any) -> str:
    """安全地序列化数据为 JSON 字符串"""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return str(data)


class AgentLoggingCallback(BaseCallbackHandler):
    """
    Agent 执行日志回调处理器

    记录内容：
    1. LLM 输入消息（User/System/AI Messages）
    2. LLM 输出响应
    3. 工具调用（输入参数、输出结果）
    4. Agent 入口/出口
    """

    def __init__(self, agent_name: str = "Agent"):
        super().__init__()
        self.agent_name = agent_name
        self._indent = 0

    def _log(self, level: int, msg: str, data: Dict = None):
        """内部日志方法"""
        prefix = "  " * self._indent
        if data:
            logger.log(level, f"{prefix}[{self.agent_name}] {msg}")
            logger.log(level, f"{prefix}  Data: {_safe_serialize(_truncate_content(data))}")
        else:
            logger.log(level, f"{prefix}[{self.agent_name}] {msg}")

    def on_agent_start(self, serialized: Dict, inputs: Dict, **kwargs) -> None:
        """Agent 入口"""
        self._indent = 0
        messages = inputs.get("messages", [])
        self._log(logging.INFO, ">>> Agent 入口", {"messages": _format_messages(messages)})
        self._indent = 1

    def on_agent_end(self, output: Any, **kwargs) -> None:
        """Agent 出口"""
        self._indent = 0
        self._log(logging.INFO, "<<< Agent 出口", {"output": _truncate_content(str(output), 1000)})

    def on_agent_action(self, action: Any, **kwargs) -> None:
        """Agent 内部动作（如工具调用）"""
        if hasattr(action, 'tool'):
            self._log(logging.INFO, f">>> Agent 动作: {action.tool}", {"tool_input": action.tool_input})

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs
    ) -> None:
        """LLM 调用入口"""
        self._log(logging.INFO, ">>> LLM 调用开始", {"prompts": _truncate_content(prompts, 300)})

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        """LLM 调用结束"""
        try:
            outputs = []
            for generations in response.generations:
                for gen in generations:
                    if isinstance(gen, ChatGeneration):
                        outputs.append({
                            "text": _truncate_content(gen.text, 500),
                            "message": _truncate_content(str(gen.message), 500) if hasattr(gen, 'message') else None
                        })
                    else:
                        outputs.append({"text": _truncate_content(gen.text, 500)})
            self._log(logging.INFO, "<<< LLM 调用结束", {"outputs": outputs})
        except Exception as e:
            self._log(logging.INFO, f"<<< LLM 调用结束 (解析失败: {e})")

    def on_llm_error(self, error: Exception, **kwargs) -> None:
        """LLM 调用错误"""
        self._log(logging.ERROR, f"!!! LLM 调用错误: {str(error)}")

    def on_tool_start(
        self, serialized: Dict[str, Any], input_str: str, **kwargs
    ) -> None:
        """工具调用入口"""
        tool_name = serialized.get("name", serialized.get("description", "unknown"))
        try:
            tool_input = json.loads(input_str) if input_str else {}
        except:
            tool_input = {"raw_input": input_str}
        self._log(logging.INFO, f">>> 工具调用开始: {tool_name}", {"input": tool_input})

    def on_tool_end(self, output: str, **kwargs) -> None:
        """工具调用结束"""
        tool_name = kwargs.get("name", "unknown")
        try:
            output_data = json.loads(output)
            self._log(logging.INFO, f"<<< 工具调用结束: {tool_name}", {"output": _truncate_content(output_data, 500)})
        except:
            self._log(logging.INFO, f"<<< 工具调用结束: {tool_name}", {"output": _truncate_content(output, 300)})

    def on_tool_error(self, error: Exception, **kwargs) -> None:
        """工具调用错误"""
        tool_name = kwargs.get("name", "unknown")
        self._log(logging.ERROR, f"!!! 工具调用错误: {tool_name} - {str(error)}")

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs) -> None:
        """Chain 入口"""
        chain_name = serialized.get("name", serialized.get("id", ["unknown"])[-1])
        self._log(logging.INFO, f">>> Chain 开始: {chain_name}", {"inputs": _truncate_content(inputs, 300)})

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs) -> None:
        """Chain 出口"""
        chain_name = kwargs.get("name", "unknown")
        self._log(logging.INFO, f"<<< Chain 结束: {chain_name}", {"outputs": _truncate_content(outputs, 500)})

    def on_chain_error(self, error: Exception, **kwargs) -> None:
        """Chain 错误"""
        chain_name = kwargs.get("name", "unknown")
        self._log(logging.ERROR, f"!!! Chain 错误: {chain_name} - {str(error)}")


class StageLoggingCallback:
    """
    阶段执行日志记录器
    用于记录 execute_pX 函数的入口/出口和关键操作
    """

    def __init__(self, stage_name: str):
        self.stage_name = stage_name
        self.start_time = None
        self._job_id = None  # 缓存 job_id

    def log_enter(self, job_id: str, extra_data: Dict = None):
        """记录阶段入口"""
        self._job_id = job_id
        self.start_time = datetime.now(timezone.utc)
        data = {"job_id": job_id, "timestamp": self.start_time.isoformat()}
        if extra_data:
            data.update(extra_data)

        # 控制台日志
        logger.info(f"[{self.stage_name}] >>> 阶段入口: {_safe_serialize(data)}")
        # WebSocket 日志
        push_websocket_log(job_id, "INFO", self.stage_name, f">>> 阶段入口", data)

    def log_exit(self, job_id: str, result: Dict, extra_data: Dict = None):
        """记录阶段出口"""
        elapsed = ""
        if self.start_time:
            elapsed = f"{(datetime.now(timezone.utc) - self.start_time).total_seconds():.2f}s"
        data = {"job_id": job_id, "elapsed": elapsed}
        if extra_data:
            data.update(extra_data)
        summary = {
            "completed": result.get("completed", False),
            "has_pending": bool(result.get("pending_confirmation")),
            "has_error": bool(result.get("error"))
        }

        # 控制台日志
        logger.info(f"[{self.stage_name}] <<< 阶段出口: {_safe_serialize(data)}")
        logger.info(f"[{self.stage_name}]     结果摘要: {_safe_serialize(summary)}")
        # WebSocket 日志
        push_websocket_log(job_id, "INFO", self.stage_name, f"<<< 阶段出口 (耗时: {elapsed})", summary)

    def log_tool_call(self, tool_name: str, input_data: Any, output_data: Any = None):
        """记录工具调用"""
        # 控制台日志
        logger.info(f"[{self.stage_name}]     工具调用: {tool_name}")
        logger.info(f"[{self.stage_name}]         输入: {_safe_serialize(_truncate_content(input_data, 300))}")
        if output_data:
            logger.info(f"[{self.stage_name}]         输出: {_safe_serialize(_truncate_content(output_data, 500))}")
        # WebSocket 日志
        if self._job_id:
            push_websocket_log(self._job_id, "INFO", "TOOL", f"工具调用: {tool_name}", {
                "input": _truncate_content(input_data, 300),
                "output": _truncate_content(output_data, 500) if output_data else None
            })

    def log_hitl_interrupt(self, job_id: str, next_tools: List[str]):
        """记录 HITL 中断"""
        # 控制台日志
        logger.info(f"[{self.stage_name}]     !!! HITL 中断: next_tools={next_tools}")
        # WebSocket 日志
        push_websocket_log(job_id, "WARNING", self.stage_name, f"!!! HITL 中断", {"next_tools": next_tools})

    def log_error(self, job_id: str, error: Exception):
        """记录错误"""
        # 控制台日志
        logger.error(f"[{self.stage_name}]     !!! 阶段错误 [{job_id}]: {str(error)}")
        # WebSocket 日志
        push_websocket_log(job_id, "ERROR", self.stage_name, f"!!! 阶段错误: {str(error)}")


# 全局日志开关
AGENT_LOGGING_ENABLED = True


def set_logs_broadcast_queue(q: queue.Queue):
    """设置日志广播队列（由 server.py 调用）"""
    global _logs_broadcast_queue
    with _queue_lock:
        _logs_broadcast_queue = q


def get_logs_broadcast_queue() -> Optional[queue.Queue]:
    """获取日志广播队列"""
    with _queue_lock:
        return _logs_broadcast_queue


def push_websocket_log(job_id: str, level: str, source: str, message: str, data: dict = None):
    """推送日志到 WebSocket 通道

    Args:
        job_id: 作业ID
        level: 日志级别 (INFO, WARNING, ERROR, DEBUG)
        source: 日志来源 (P1, P2, ..., MAIN, WORKFLOW, TOOL, LLM, AGENT)
        message: 日志消息
        data: 附加数据
    """
    q = get_logs_broadcast_queue()
    if q is None:
        return  # 队列未设置，跳过

    log_entry = {
        "type": "workflow_log",
        "job_id": job_id,
        "level": level,
        "source": source,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "data": data or {}
    }
    try:
        q.put_nowait(log_entry)
    except queue.Full:
        pass  # 队列满，跳过


def get_logging_callback(agent_name: str = "Agent") -> Optional[AgentLoggingCallback]:
    """获取日志回调处理器"""
    if AGENT_LOGGING_ENABLED:
        return AgentLoggingCallback(agent_name)
    return None


def get_stage_logger(stage_name: str) -> StageLoggingCallback:
    """获取阶段日志记录器"""
    return StageLoggingCallback(stage_name)
