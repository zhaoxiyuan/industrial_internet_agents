"""
P1 Sequential ReAct Executor
使用 LLM 进行推理，但保证工具串行执行（避免并行导致日志乱序）
"""
import json
import logging
from typing import List, Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

logger = logging.getLogger("p1_react")


class P1ReActExecutor:
    """
    P1 顺序 ReAct 执行器

    核心特点：
    1. 每次 LLM 调用只允许执行一个工具（通过只处理第一个 tool_call）
    2. 工具执行后立即记录日志，保证串行展示
    3. 支持 HITL 中断（通过 interrupt_after 工具列表）
    """

    def __init__(
        self,
        job_id: str,
        system_prompt: str,
        llm=None,
        interrupt_after: List[str] = None,
    ):
        self.job_id = job_id
        self.system_prompt = system_prompt
        self.interrupt_after = interrupt_after or []

        # 延迟导入避免循环依赖
        from agents.model.chat_model import create_chat_model_with_logging
        self.llm = llm or create_chat_model_with_logging("P1-ReAct", job_id)

        # 消息历史
        self.messages: List = []

        # 工具注册表（延迟初始化）
        self._tools: Optional[Dict[str, Any]] = None

        # 当前活跃的工具调用（用于 HITL）
        self.pending_tool_call: Optional[Dict] = None

        # 执行统计
        self.step_count = 0

    def _get_tools(self) -> Dict[str, Any]:
        """延迟导入 P1 工具函数"""
        if self._tools is None:
            # 延迟导入避免循环依赖
            from agents.p1_permit_agent import (
                permit_submit, jsa_analyze, permit_generate_draft, permit_check
            )
            tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
            self._tools = {t.name: t for t in tools}
        return self._tools

    def _broadcast_substep(self, tool_name: str, status: str):
        """广播步骤状态"""
        # 延迟导入避免循环依赖
        from agents.p1_permit_agent import (
            _broadcast_substep, _p1_items_with_status, _p1_mark_completed,
            P1_STEP_ITEMS, P1_STEP_TOTAL
        )
        step_idx = next(
            (i + 1 for i, t in enumerate(P1_STEP_ITEMS) if t["tool"] == tool_name),
            0
        )
        _broadcast_substep(
            self.job_id, "P1", step_idx, P1_STEP_TOTAL,
            next((t["label"] for t in P1_STEP_ITEMS if t["tool"] == tool_name), tool_name),
            tool_name, status,
            _p1_items_with_status(tool_name, status),
        )

    def _should_interrupt(self, tool_name: str) -> bool:
        """检查是否应该在工具执行前中断等待确认"""
        return tool_name in self.interrupt_after

    def _execute_single_tool(self, tool_name: str, tool_input: Any) -> str:
        """执行单个工具并返回结果"""
        from agents.utils.logging_handler import push_websocket_log

        # 广播 running 状态
        self._broadcast_substep(tool_name, "running")

        push_websocket_log(
            self.job_id, "INFO", "TOOL",
            f">>> {tool_name} 工具入口",
            {"input": str(tool_input)[:200]}
        )
        logger.info(f"[P1-ReAct] >>> 工具调用: {tool_name}, input={str(tool_input)[:100]}...")

        # 执行工具
        tools = self._get_tools()
        tool = tools.get(tool_name)
        if not tool:
            result = json.dumps({"error": f"未知工具: {tool_name}"})
        else:
            try:
                result = tool.invoke(tool_input)
            except Exception as e:
                logger.error(f"[P1-ReAct] 工具执行失败: {tool_name}, error={e}")
                result = json.dumps({"error": str(e)})

        # 标记完成并广播 completed 状态
        from agents.p1_permit_agent import _p1_mark_completed
        _p1_mark_completed(tool_name)
        self._broadcast_substep(tool_name, "completed")

        push_websocket_log(
            self.job_id, "INFO", "TOOL",
            f"<<< {tool_name} 工具出口",
            {"result": str(result)[:200]}
        )
        logger.info(f"[P1-ReAct] <<< 工具执行完成: {tool_name}")

        return result

    def _log_llm_input(self, messages: List):
        """记录 LLM 输入"""
        from agents.utils.logging_handler import push_websocket_log
        for i, msg in enumerate(messages):
            content = getattr(msg, 'content', str(msg))[:500]
            logger.info(f"[P1-ReAct] LLM 输入消息 {i}: {content[:100]}...")
            push_websocket_log(
                self.job_id, "INFO", "LLM",
                f"LLM 输入消息 {i}",
                {"content": content[:200]}
            )

    def _log_llm_output(self, response):
        """记录 LLM 输出"""
        from agents.utils.logging_handler import push_websocket_log
        try:
            text = response.content[:500] if hasattr(response, 'content') else str(response)[:500]
            logger.info(f"[P1-ReAct] LLM 输出: {text[:100]}...")
            push_websocket_log(
                self.job_id, "INFO", "LLM",
                "LLM 输出",
                {"content": text[:200]}
            )
        except Exception as e:
            logger.warning(f"[P1-ReAct] LLM 输出解析失败: {e}")

    def run(self, initial_message: str, max_iterations: int = 10) -> Dict[str, Any]:
        """
        执行 ReAct 循环

        Args:
            initial_message: 用户输入消息
            max_iterations: 最大迭代次数（防止无限循环）

        Returns:
            {"result": str, "interrupted": bool, "next": list}
        """
        from agents.p1_permit_agent import (
            _p1_set_active_job, _p1_clear_active_job
        )
        from agents.utils.logging_handler import push_websocket_log
        from agents.model.chat_model import create_chat_model_with_logging

        # 设置活跃 job_id
        _p1_set_active_job(self.job_id)

        # 初始化消息
        self.messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=initial_message),
        ]

        push_websocket_log(
            self.job_id, "INFO", "AGENT",
            ">>> P1 ReAct Agent 入口",
            {"message": initial_message[:100]}
        )
        logger.info(f"[P1-ReAct] >>> Agent 入口: message={initial_message[:100]}...")

        try:
            for self.step_count in range(1, max_iterations + 1):
                # ----- LLM 调用 -----
                self._log_llm_input(self.messages)

                # 绑定工具（每次 LLM 调用可能触发工具）
                llm_with_tools = self.llm.bind_tools(
                    self._get_tools().values(),
                    tool_choice="auto",
                )
                response = llm_with_tools.invoke(self.messages)

                self._log_llm_output(response)

                # 添加 AI 响应到消息历史
                self.messages.append(response)

                # ----- 检查 LLM 是否调用了工具 -----
                if not hasattr(response, 'tool_calls') or not response.tool_calls:
                    # LLM 返回最终回答，没有更多工具调用
                    final_answer = response.content if hasattr(response, 'content') else str(response)
                    logger.info(f"[P1-ReAct] <<< Agent 完成: {final_answer[:100]}...")
                    push_websocket_log(
                        self.job_id, "INFO", "AGENT",
                        "<<< Agent 完成",
                        {"answer": final_answer[:200]}
                    )
                    return {
                        "result": final_answer,
                        "interrupted": False,
                        "next": []
                    }

                # ----- 处理工具调用（每次只处理第一个）-----
                tool_call = response.tool_calls[0]
                tool_name = tool_call.get("name") or tool_call.get("function", {}).get("name", "")
                tool_input = tool_call.get("args") or tool_call.get("function", {}).get("arguments", {})

                # 解析 JSON 字符串参数
                if isinstance(tool_input, str):
                    try:
                        tool_input = json.loads(tool_input)
                    except json.JSONDecodeError:
                        pass

                logger.info(f"[P1-ReAct] Step {self.step_count}: 调用工具 {tool_name}")

                # 检查是否需要 HITL 中断
                if self._should_interrupt(tool_name):
                    self.pending_tool_call = {
                        "tool": tool_name,
                        "input": tool_input,
                        "message_index": len(self.messages) - 1,
                    }
                    push_websocket_log(
                        self.job_id, "WARNING", "AGENT",
                        f"!!! HITL 中断等待确认",
                        {"tool": tool_name}
                    )
                    return {
                        "result": None,
                        "interrupted": True,
                        "next": [tool_name]
                    }

                # ----- 执行工具 -----
                tool_result = self._execute_single_tool(tool_name, tool_input)

                # ----- 将工具结果添加为 ToolMessage -----
                tool_msg = ToolMessage(
                    content=tool_result,
                    tool_call_id=tool_call.get("id", ""),
                    name=tool_name,
                )
                self.messages.append(tool_msg)

            # 达到最大迭代次数
            logger.warning(f"[P1-ReAct] 达到最大迭代次数: {max_iterations}")
            return {
                "result": f"达到最大迭代次数 {max_iterations}",
                "interrupted": False,
                "next": []
            }

        finally:
            _p1_clear_active_job()


def run_p1_react_agent(message: str, job_id: str, resume: bool = False) -> Dict[str, Any]:
    """
    运行 P1 ReAct Agent（串行工具执行 + LLM 推理）

    Args:
        message: 用户消息
        job_id: 作业ID
        resume: 是否从 HITL 中断恢复（暂未实现）

    Returns:
        {"result": str, "interrupted": bool, "next": list}
    """
    from agents.utils.system_prompt import load_system_prompt

    # 加载系统提示词
    system_prompt = load_system_prompt("P1")

    # 创建执行器
    executor = P1ReActExecutor(
        job_id=job_id,
        system_prompt=system_prompt,
        # HITL 模式：所有工具调用前都中断（当前暂不启用）
        interrupt_after=[],
    )

    # 运行
    return executor.run(message)
