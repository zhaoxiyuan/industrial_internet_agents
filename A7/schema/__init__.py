"""A7.schema — P8 数据模型 + LangGraph State schema。

子模块：
- p8_models : Pydantic 数据模型（P8JobStatus / RiskLevel / Channel / ActionType 枚举 + P8Job / P8JobUpdate / PushMessage 模型）
- p8_state  : LangGraph State schema（P8State TypedDict）+ 终态判定（_TERMINAL_STATUSES / is_terminal_status）

设计要点：
- 所有 Pydantic 字段都加 description + 字段后注释，便于 LLM 工具 schema 生成
- 所有类前面有 docstring 说明类目的，便于 IDE 悬浮提示
- 终态集合（completed/rejected/escalated/resumed）在 p8_state 中以原始 set 维护，
  避免 p8_models 与 p8_state 的循环依赖
"""
from .p8_models import (
    P8Job,
    P8JobStatus,
    P8JobUpdate,
    ActionType,
    Channel,
    PushMessage,
    RiskLevel,
)
from .p8_state import (
    P8State,
    _TERMINAL_STATUSES,
    get_terminal_statuses,
    is_terminal_status,
)

__all__ = [
    # Enums
    "P8JobStatus",
    "RiskLevel",
    "Channel",
    "ActionType",
    # Pydantic 模型
    "P8Job",
    "P8JobUpdate",
    "PushMessage",
    # State 相关
    "P8State",
    "_TERMINAL_STATUSES",
    "get_terminal_statuses",
    "is_terminal_status",
]