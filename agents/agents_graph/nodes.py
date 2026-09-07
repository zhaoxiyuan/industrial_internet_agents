"""
P1-P10 节点工厂

每个 pX_agent.execute_stage(job_id) -> dict 都会被包装成一个 LangGraph 节点：
    - 进入节点时把 stages[P_X].status 置为 running
    - 调用 execute_stage
    - 根据返回结果更新状态：
        pending_confirmation -> waiting（进入暂停）
        error 字段存在       -> failed
        其余                  -> completed
    - 触发 broadcast 回调（如果设置）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .state import STAGES, WorkflowState

logger = logging.getLogger("agents_graph.nodes")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# 中断（暂停）伪节点：进入后立即路由到 __end__，保留状态供人工恢复
INTERRUPT_NODE = "wait_human"

# 全局广播回调（由 graph.set_broadcast_callback 设置）
_broadcast_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None


def set_broadcast_callback(callback: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
    """设置节点状态广播回调；callback(job_id, state_snapshot) -> None。"""
    global _broadcast_callback
    _broadcast_callback = callback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _broadcast(job_id: str, snapshot: Dict[str, Any]) -> None:
    """内部广播，按需容错。"""
    cb = _broadcast_callback
    if not cb:
        return
    try:
        cb(job_id, snapshot)
    except Exception as exc:  # pragma: no cover
        logger.warning("broadcast 失败: %s", exc)


# ============================================================
# 节点工厂
# ============================================================

def _make_stage_node(stage: str, executor: Callable[..., Dict[str, Any]]):
    """生成单个 pX 节点的闭包。

    executor: 可调用 (job_id, **kwargs) -> dict
    """
    state_key = stage

    def _node(state: WorkflowState) -> Dict[str, Any]:
        job_id = state.get("job_id", "")
        stages: Dict[str, Any] = dict(state.get("stages") or {})
        stage_info = dict(stages.get(state_key) or {})
        stage_info["status"] = "running"
        stage_info["started_at"] = _now()
        stage_info["error"] = None
        stages[state_key] = stage_info

        logger.info("进入节点 %s: job_id=%s", stage, job_id)

        result: Dict[str, Any] = {}
        try:
            result = executor(job_id)
        except Exception as exc:  # pragma: no cover - 防御
            logger.exception("节点 %s 执行异常: %s", stage, exc)
            stage_info["status"] = "failed"
            stage_info["error"] = str(exc)
            stage_info["completed_at"] = _now()
            stages[state_key] = stage_info
            snapshot = _snapshot(job_id, stages, "failed", stage)
            _broadcast(job_id, snapshot)
            return {
                "stages": stages,
                "workflow_status": "failed",
                "current_stage": stage,
                "last_stage_result": {"error": str(exc)},
                "interrupt_reason": str(exc),
            }

        now = _now()
        pending = result.get("pending_confirmation")
        if pending:
            stage_info["status"] = "waiting"
            stage_info["pending_confirmation"] = pending
            stage_info["completed_at"] = now
            stages[state_key] = stage_info
            snapshot = _snapshot(job_id, stages, "waiting", stage)
            _broadcast(job_id, snapshot)
            return {
                "stages": stages,
                "workflow_status": "waiting",
                "current_stage": stage,
                "last_stage_result": result,
                "interrupt_reason": pending.get("message", "需要人工确认"),
            }

        if result.get("error") and not result.get("completed", True):
            stage_info["status"] = "failed"
            stage_info["error"] = result["error"]
            stage_info["completed_at"] = now
            stages[state_key] = stage_info
            snapshot = _snapshot(job_id, stages, "failed", stage)
            _broadcast(job_id, snapshot)
            return {
                "stages": stages,
                "workflow_status": "failed",
                "current_stage": stage,
                "last_stage_result": result,
                "interrupt_reason": result["error"],
            }

        stage_info["status"] = "completed"
        stage_info["completed_at"] = now
        # 仅保留关键字段，避免大字典污染 state
        stage_info["result"] = {
            k: result.get(k)
            for k in ("completed", "task_id", "permit_draft_id")
            if k in result
        }
        stages[state_key] = stage_info

        # 终态判定：若完成的是最后一个阶段，整体状态置为 completed
        is_last = stage == STAGES[-1]
        next_workflow_status = "completed" if is_last else "running"
        next_current_stage = stage  # 即便未到终态也保持当前是已完成的 stage

        # 路由到下一阶段（在条件边中处理）
        snapshot = _snapshot(job_id, stages, next_workflow_status, stage)
        _broadcast(job_id, snapshot)
        return {
            "stages": stages,
            "workflow_status": next_workflow_status,
            "current_stage": next_current_stage,
            "last_stage_result": result,
            "interrupt_reason": None,
        }

    _node.__name__ = f"node_{stage.lower()}"
    return _node


def _snapshot(job_id: str, stages: Dict[str, Any], workflow_status: str, current: str) -> Dict[str, Any]:
    """构造广播快照（精简版）。"""
    return {
        "job_id": job_id,
        "workflow_status": workflow_status,
        "current_stage": current,
        "stages": {
            stage: {
                "status": (info or {}).get("status", "pending"),
                "started_at": (info or {}).get("started_at"),
                "completed_at": (info or {}).get("completed_at"),
                "error": (info or {}).get("error"),
            }
            for stage, info in stages.items()
        },
    }


def _import_stage_executors() -> Dict[str, Callable[..., Dict[str, Any]]]:
    """延迟导入各 pX_agent.execute_stage，避免循环依赖。

    pX_agent 模块位于上级包（agents.pX_agent），故使用 `..` 相对导入。
    P8 实际上没有显式 execute_stage：从 main_agent 复用我们暴露的 p8_execute_stage。
    """
    from ..p1_permit_agent import execute_stage as p1
    from ..p2_task_agent import execute_stage as p2
    from ..p3_context_agent import execute_stage as p3
    from ..p4_binding_agent import execute_stage as p4
    from ..p5_verify_agent import execute_stage as p5
    from ..p6_monitor_agent import execute_stage as p6
    from ..p7_risk_agent import execute_stage as p7
    from ..main_agent import p8_execute_stage as p8
    from ..p9_closure_agent import execute_stage as p9
    from ..p10_archive_agent import execute_stage as p10

    return {
        "P1": p1,
        "P2": p2,
        "P3": p3,
        "P4": p4,
        "P5": p5,
        "P6": p6,
        "P7": p7,
        "P8": p8,
        "P9": p9,
        "P10": p10,
    }


def build_all_nodes() -> Dict[str, Callable[[WorkflowState], Dict[str, Any]]]:
    """构造 {stage: node} 字典。

    - P1 的 execute_stage 接受可选 resume 参数，节点层用默认 False 调用；
      复杂 HITL resume 由上层 main_agent.confirm_and_continue 负责。
    """
    executors = _import_stage_executors()
    nodes: Dict[str, Callable[[WorkflowState], Dict[str, Any]]] = {}

    for stage in STAGES:
        executor = executors[stage]
        nodes[stage] = _make_stage_node(stage, executor)

    # 伪中断节点：把状态置为 waiting 后立即返回，StateGraph 之后由条件边接到 __end__
    def _interrupt(state: WorkflowState) -> Dict[str, Any]:
        snapshot = _snapshot(
            state.get("job_id", ""),
            dict(state.get("stages") or {}),
            "waiting",
            state.get("current_stage", ""),
        )
        _broadcast(state.get("job_id", ""), snapshot)
        return {"workflow_status": "waiting"}

    nodes[INTERRUPT_NODE] = _interrupt
    return nodes