"""LangGraph State schema（P8 专用）+ 终态判定工具。

定义 P8 Agent 在 LangGraph state 中的字段；与主设计文档 § 4.1 双层记忆模型对齐：

- messages           LangChain 标准字段；对话历史（含 tool calls / tool results）
- working_memory     当前 in-progress P8_job 列表（数组；可空；LLM 每次 invoke 都看到）
- long_term_memory   已归档 P8_job 字典（p8_job_id → archived dict；按需精确查询）

终态集合（_TERMINAL_STATUSES）= {completed, rejected, escalated, resumed}，
P8ArchiveMiddleware 在 P8Job.status 进入终态时自动从 working_memory 移到 long_term_memory
（详见 § 6.4 与 A7/storage/p8_long_term.py）。

注意：**P8 不存在后台守护进程 / 轮询 worker**——何时把新 P7 数据给 P8 Agent
完全取决于两个唯一入口（详见主文档 § 7）：(1) `execute_p8(p7_data=...)` 透传；
(2) 用户明确要求（LLM 调 `read_p7_events`）。因此 state 中无需 `seen_event_ids` 字段。
"""
from __future__ import annotations

from typing import Optional, TypedDict

# _TERMINAL_STATUSES 在 p8_models.P8JobStatus 中也定义了值；
# 这里以原始字符串集合形式独立维护，供 is_terminal_status() 使用（避免导入 pydantic enum 时的循环依赖）
_TERMINAL_STATUSES: set[str] = {
    "completed",
    "rejected",
    "escalated",
    "resumed",
}


class P8State(TypedDict, total=False):
    """LangGraph state schema（P8 专用）。

    字段说明：
        messages           LangChain 标准字段；
                            HumanMessage / AIMessage / ToolMessage 等序列；
                            LangGraph 内置支持
        working_memory     当前 in-progress P8_job 列表（数组，可空）；
                            LLM 每次 invoke 都能看到所有未归档 P8_job；
                            元素类型为 P8Job（dict 形式，TypedDict total=False 允许缺省）
        long_term_memory   已归档 P8_job 字典；
                            key = p8_job_id（字符串）；
                            value = archived P8Job dict（含 summary + archived_at 字段）；
                            按需精确查询，避免污染 LLM context

    说明：
        - TypedDict total=False 表示所有字段都是 Optional（缺省合法）；
          LangGraph 在 invoke 时按需合并。
        - 字段顺序无业务含义，仅按访问频率排序以便阅读。
        - **无 `seen_event_ids` 字段**：P8 不存在后台守护进程 / 轮询 worker；
          新 P7 数据进入 P8 仅两个入口（主流程透传 / 用户明确要求），无需去重。
    """

    # ===== LangChain 标准字段（必需） =====
    messages: list  # LangChain 标准；list[BaseMessage]；LangGraph 内置支持

    # ===== P8 业务字段 =====
    working_memory: list  # 当前 in-progress P8_job 列表；元素类型 P8Job（dict 形式）
    long_term_memory: dict  # 已归档 P8_job；key=p8_job_id → value=archived P8Job dict


def is_terminal_status(status: str) -> bool:
    """判断 status 是否为终态（P8ArchiveMiddleware 调用）。

    终态集合（§ 6.4）：{completed, rejected, escalated, resumed}。
    P8Job.status 进入终态时，P8ArchiveMiddleware 自动 working → long_term + 从 working_memory 移除。

    Args:
        status: P8Job.status 字符串（来自 P8JobStatus enum 的 .value）

    Returns:
        True → 终态（应触发归档）
        False → 非终态（继续在 working_memory 中）

    Examples:
        >>> is_terminal_status("completed")
        True
        >>> is_terminal_status("pending")
        False
        >>> is_terminal_status("waiting_decision")
        False
    """
    return status in _TERMINAL_STATUSES


def get_terminal_statuses() -> frozenset[str]:
    """返回不可变终态集合副本（供外部只读查询）。

    Returns:
        frozenset 形式的 _TERMINAL_STATUSES 副本；
        外部修改不影响内部状态
    """
    return frozenset(_TERMINAL_STATUSES)