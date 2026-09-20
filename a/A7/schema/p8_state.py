"""LangGraph State schema（P8 专用）+ 终态判定工具 + working_memory reducer。

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

working_memory 的 reducer 语义（`_working_memory_reducer`）：
- 工具通过 `Command(update={"working_memory": [new_job, ...]})` 增量写入
- reducer 按 `p8_job_id` **upsert**（同 pid 替换；新增追加）
- 哨兵 dict `{"__delete__": "<pid>"}` 触发删除（被 P8ArchiveMiddleware 用于终态移除）
- 同一 list 中同一 pid 多次出现时，**最后一条**为最终值（delete 之后又出现则重新入队）
- reducer 始终返回**新 list**（不修改 existing 的原始引用，避免 LangGraph 状态污染）
"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages

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
    # 2026-08-19 修复 MiniMax 2013 错误：
    # 必须用 add_messages reducer（与 LangChain 默认 AgentState 一致），否则 LangGraph
    # 把 messages 当成普通 state 字段做"整体替换"，导致跨 invoke 时 AIMessage(tool_calls)
    # 被 ToolMessage 覆盖、丢失配对 → MiniMax 服务端校验 ToolMessage.tool_call_id 时
    # 在 messages 列表内找不到对应 AIMessage.tool_calls → 报 2013 bad_request。
    # 详见 tests/test_verify_schema_rootcause.py 的 A/B 对照实验。
    messages: Annotated[list, add_messages]  # LangChain 标准；list[BaseMessage]；用 add_messages 累积跨 invoke 历史

    # ===== P8 业务字段 =====
    # working_memory 用自定义 reducer（见 _working_memory_reducer）——按 p8_job_id 增/删/改，
    # 而非简单覆盖。LLM 工具通过 Command(update={"working_memory": [...]}) 增量写入。
    working_memory: Annotated[list[dict], _working_memory_reducer]
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


# ============================================================
# working_memory reducer（LangGraph Annotated 字段 reducer）
# ============================================================
# 备注：以下函数在 P8State 的字段注解中通过名字引用（PEP 563 forward ref 风格）；
# Python 仅在调用 typing.get_type_hints() 时真正 evaluate，因此**先后顺序无关**。
# 这里把它放在 P8State 定义之后仅为阅读流畅（先讲字段，再讲 reducer 逻辑）。

# Delete 哨兵：含此键的 dict 视作删除指令，不视作正常 P8_job。
# 命名空间前缀 `__p8__` 避免与正常 P8_job 字段冲突（LLM 不会主动构造）。
_DELETE_SENTINEL_KEY = "__p8__delete__"


def _working_memory_reducer(
    existing: Optional[list[dict]],
    new: Optional[list[dict]],
) -> list[dict]:
    """working_memory 的 LangGraph state reducer。

    语义：
        - **upsert**：若 new 中的元素含 `p8_job_id` 字段，在 existing 中找同 pid 元素并替换；
          找不到则追加。
        - **delete 哨兵**：若 new 元素为 `{"__p8__delete__": "<pid>"}`（或 `{"__delete__": "<pid>"}`），
          从结果中移除所有 `p8_job_id == pid` 的元素。
        - **空 existing**：返回 `new` 的归一化结果（过滤 None）。
        - **空 new**：返回 existing 的浅拷贝（不修改原引用）。
        - **同 pid 多次出现**：以**最后一次出现**为最终值；若最后一次为 delete 哨兵，
          则该 pid 元素被删除；如果后续又出现正常 dict（罕见），仍按最后一次判定为删除。
        - 哨兵 dict 自身**不会**出现在最终结果中（避免污染 working_memory）。

    Args:
        existing: 现有 working_memory 列表（LangGraph 传入；可能为 None）
        new: 工具通过 Command(update={"working_memory": [...]}) 写入的新值（可能为 None）

    Returns:
        新 list（永不复用 existing 引用；LangGraph 状态比较基于 __eq__，新 list 一定 ≠ 旧 list）

    Examples:
        >>> # 1. 空 → 加一条
        >>> _working_memory_reducer(None, [{"p8_job_id": "P8J-1", "status": "pending"}])
        [{'p8_job_id': 'P8J-1', 'status': 'pending'}]

        >>> # 2. 已有 → upsert（同 pid 替换）
        >>> _working_memory_reducer(
        ...     [{"p8_job_id": "P8J-1", "status": "pending"}],
        ...     [{"p8_job_id": "P8J-1", "status": "notified"}],
        ... )
        [{'p8_job_id': 'P8J-1', 'status': 'notified'}]

        >>> # 3. 哨兵删除
        >>> _working_memory_reducer(
        ...     [{"p8_job_id": "P8J-1", "status": "completed"}, {"p8_job_id": "P8J-2", "status": "pending"}],
        ...     [{"__p8__delete__": "P8J-1"}],
        ... )
        [{'p8_job_id': 'P8J-2', 'status': 'pending'}]

        >>> # 4. 新增 + 删除同一批
        >>> _working_memory_reducer(
        ...     [{"p8_job_id": "P8J-1", "status": "completed"}],
        ...     [{"p8_job_id": "P8J-2", "status": "pending"}, {"__p8__delete__": "P8J-1"}],
        ... )
        [{'p8_job_id': 'P8J-2', 'status': 'pending'}]

        >>> # 5. None 入参
        >>> _working_memory_reducer(None, None)
        []
    """
    # 1. 空集合归一化
    base: list[dict] = list(existing) if existing else []
    if new is None:
        return base

    # 2. 按 new 顺序处理：每条可能是 normal dict / delete sentinel / 其它
    #    用 index 跟踪（而非 dict），保持 reducer 确定性 + 允许同 pid 多次出现
    by_pid_index: dict[str, int] = {}  # pid → 在 result 中的位置
    result: list[dict] = []

    # 先把 existing 里的放进 by_pid_index
    for idx, job in enumerate(base):
        if not isinstance(job, dict):
            # 防御：非法元素静默丢弃（不应出现在正常路径）
            continue
        pid = job.get("p8_job_id")
        if isinstance(pid, str) and pid:
            result.append(job)
            by_pid_index[pid] = idx
        else:
            # 无 pid 的元素按"无主 dict"保留（不影响后续 upsert）
            result.append(job)

    # 3. 处理 new
    for item in new:
        if not isinstance(item, dict):
            # 非法元素静默丢弃
            continue

        # 哨兵检测：__p8__delete__（优先）兼容旧的 __delete__
        sentinel_pid = item.get(_DELETE_SENTINEL_KEY) or item.get("__delete__")
        if isinstance(sentinel_pid, str) and sentinel_pid:
            # 删除 existing 中所有同 pid 元素
            if sentinel_pid in by_pid_index:
                del_idx = by_pid_index[sentinel_pid]
                # 用 pop 重建索引（位置变化）
                result.pop(del_idx)
                # 重新计算索引（O(n) 一次完成；n 通常很小）
                by_pid_index = {
                    job.get("p8_job_id"): i
                    for i, job in enumerate(result)
                    if isinstance(job, dict) and isinstance(job.get("p8_job_id"), str)
                }
            continue  # 哨兵不进入 result

        # 普通 P8_job → upsert
        pid = item.get("p8_job_id")
        if isinstance(pid, str) and pid:
            if pid in by_pid_index:
                # 替换（先删再加，保持 result 顺序）
                del_idx = by_pid_index[pid]
                result[del_idx] = item
            else:
                # 追加
                by_pid_index[pid] = len(result)
                result.append(item)
        else:
            # 无 pid 的元素直接追加（不影响索引）
            result.append(item)

    return result