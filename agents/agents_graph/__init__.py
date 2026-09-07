"""
agents_graph - 基于 LangGraph StateGraph 的 P1-P10 工作流编排与可视化

本包将各 pX_agent 的 execute_stage 入口串接为统一的 StateGraph：

- P1 -> P2 -> P3 -> ... -> P10
- 每个节点的条件边根据节点结果决定：
    completed -> 进入下一阶段
    waiting  -> 路由到 __interrupt__ 暂停（等待人工确认）
    failed   -> 直接结束（路由到 __end__）

可视化：
    静态：导出 Mermaid (.mmd) 文本 + 自包含 HTML（自动运行 Mermaid CDN）
    动态：自包含 HTML（WebSocket / 轮询，支持节点级状态高亮）

入口：
    from agents.agents_graph import build_workflow_graph, render_static_html
"""
from .state import WorkflowState, StageInfo, STAGES
from .nodes import build_all_nodes, INTERRUPT_NODE
from .graph import build_workflow_graph, run_workflow_graph
from .render import (
    render_mermaid,
    render_ascii,
    render_static_html,
    render_dynamic_html,
    write_state_sidecar,
    set_broadcast_callback,
)

__all__ = [
    "WorkflowState",
    "StageInfo",
    "STAGES",
    "INTERRUPT_NODE",
    "build_all_nodes",
    "build_workflow_graph",
    "run_workflow_graph",
    "render_mermaid",
    "render_ascii",
    "render_static_html",
    "render_dynamic_html",
    "write_state_sidecar",
    "set_broadcast_callback",
]
