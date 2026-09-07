"""
构建并运行 P1-P10 StateGraph

构造方式：
    StateGraph(WorkflowState)
        .add_node(P1, fn).add_node(P2, fn)... .add_node(P10, fn)
        .add_node(__interrupt__, 暂停节点)
        .set_entry_point('P1')
        .add_conditional_edges('P1', _route_after('P1'), {P2: 'P2', __interrupt__: __interrupt__, __end__: __end__})
        ...
        .add_edge('__interrupt__', __end__)
        .add_edge('P10', __end__)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Callable

from langgraph.graph import END, START, StateGraph

from .nodes import INTERRUPT_NODE, build_all_nodes, set_broadcast_callback
from .state import STAGES, StageInfo, WorkflowState, make_initial_state

logger = logging.getLogger("agents_graph.graph")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# 条件路由
# ============================================================

def _next_stage_key(stage: str) -> Optional[str]:
    """返回下一阶段名；P10 后没有下一阶段，返回 None。"""
    idx = STAGES.index(stage)
    if idx + 1 >= len(STAGES):
        return None
    return STAGES[idx + 1]


def _make_router(current_stage: str):
    """生成 StageGraph 条件边使用的 router 函数。"""
    next_stage = _next_stage_key(current_stage)

    def _router(state: WorkflowState) -> str:
        stages = state.get("stages") or {}
        info: StageInfo = stages.get(current_stage) or {}
        status = info.get("status", "pending")
        workflow_status = state.get("workflow_status", "running")

        # 任何阶段失败 -> 结束
        if status == "failed" or workflow_status == "failed":
            return END

        # 等待确认 -> 暂停
        if status == "waiting" or workflow_status == "waiting":
            return INTERRUPT_NODE

        # 已完成 -> 去下一阶段或结束
        if status == "completed":
            if next_stage is None:
                return END
            return next_stage

        # 兜底：仍是 running（多轮 invoke 场景），按当前 current_stage 选择路由
        # 但通常 router 不会在 running 时被调用（节点返回前会更新 status）
        return next_stage or END

    _router.__name__ = f"router_after_{current_stage.lower()}"
    return _router


def build_workflow_graph():
    """构造并 compile StateGraph，返回 CompiledStateGraph。"""
    nodes = build_all_nodes()

    g = StateGraph(WorkflowState)

    for stage, fn in nodes.items():
        # 跳过我们会在内部暴露的 __interrupt__
        if stage == INTERRUPT_NODE:
            continue
        g.add_node(stage, fn)

    # 单独的伪中断节点
    g.add_node(INTERRUPT_NODE, nodes[INTERRUPT_NODE])

    g.add_edge(START, STAGES[0])

    # 串行条件边：每个阶段后根据状态分流
    path_map: Dict[str, str] = {END: END}
    for i, stage in enumerate(STAGES):
        next_stage = _next_stage_key(stage)
        allowed: Dict[str, str] = {}
        allowed[INTERRUPT_NODE] = INTERRUPT_NODE
        allowed[END] = END
        if next_stage is not None:
            allowed[next_stage] = next_stage
        # 仅保留可达项
        cleaned = {k: v for k, v in allowed.items() if k}
        g.add_conditional_edges(stage, _make_router(stage), cleaned)

    # 暂停节点结束
    g.add_edge(INTERRUPT_NODE, END)

    return g.compile()


# ============================================================
# 运行入口
# ============================================================

_DEFAULT_SIDECAR_DIR = Path("data/graph_states")


def run_workflow_graph(
    job_id: str,
    application: Optional[Dict[str, Any]] = None,
    graph: Optional[Any] = None,
    sidecar_dir: Optional[Path] = None,
) -> WorkflowState:
    """运行整张图并返回最终 WorkflowState。

    副作用：
        - 每节点完成后会调用 broadcast 回调（如果有）
        - 同时把 state 快照写入 sidecar_dir/{job_id}.json
          （供动态可视化 HTML 轮询使用）
    """
    compiled = graph or build_workflow_graph()
    sidecar_path: Optional[Path] = None
    if sidecar_dir is not None:
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = sidecar_dir / f"{job_id}.json"

    snapshot_holder: Dict[str, Any] = {"final": None}

    def _cb(jid: str, snapshot: Dict[str, Any]) -> None:
        if sidecar_path is not None:
            try:
                sidecar_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("写入 state sidecar 失败: %s", exc)
        snapshot_holder["final"] = snapshot

    prev_cb = _swap_callback(_cb)
    try:
        init = make_initial_state(job_id, application)
        final = compiled.invoke(init)
    finally:
        _swap_callback(prev_cb)

    snapshot_holder["final"] = final
    if sidecar_path is not None:
        try:
            sidecar_path.write_text(json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning("写入最终 state sidecar 失败: %s", exc)

    return final


def _swap_callback(new_cb: Optional[Callable[[str, Dict[str, Any]], None]]):
    """线程不安全，但脚本一次性执行即可。"""
    from . import nodes as _nodes

    old = _nodes._broadcast_callback
    _nodes._broadcast_callback = new_cb
    return old
