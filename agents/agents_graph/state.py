"""
工作流共享状态模型
P1-P10 StateGraph 的 TypedDict 状态定义
"""
from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict


class StageInfo(TypedDict, total=False):
    """单个阶段的运行信息（写入 state['stages'][stage]）。"""

    status: str                # pending | running | completed | waiting | failed
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    pending_confirmation: Optional[Dict[str, Any]]
    result: Optional[Dict[str, Any]]


class WorkflowState(TypedDict, total=False):
    """P1-P10 StateGraph 的共享状态。

    所有节点读写此字典；通过 LangGraph reducer 自动合并。
    """

    job_id: str
    application: Dict[str, Any]
    workflow_status: str       # running | waiting | completed | failed
    current_stage: str
    interrupt_reason: Optional[str]
    last_stage_result: Optional[Dict[str, Any]]
    stages: Dict[str, StageInfo]


# P1-P10 顺序阶段，节点命名约定与图遍历均使用此列表
STAGES = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]


def make_initial_state(job_id: str, application: Dict[str, Any] | None = None) -> WorkflowState:
    """构造工作流初始状态。"""
    return {
        "job_id": job_id,
        "application": application or {},
        "workflow_status": "running",
        "current_stage": STAGES[0],
        "interrupt_reason": None,
        "last_stage_result": None,
        "stages": {stage: StageInfo(status="pending") for stage in STAGES},
    }
