"""
a/agents — P6-P9 子集(独立子项目入口)

本目录是项目相对独立子集 `a/` 的 agents 包,只承载 P6-P9 业务逻辑,
不包含 P1-P5 主流程、P10 归档(在原项目里存在;这里剥离)。

子模块:
- model/        LLM 封装(chat_model / config / langsmith)
- utils/        通用工具(extract_output / response_utils / system_prompt / logging)
- workflow/     持久化(file_utils / workflow_state / job_persistence / execution_status)
- p6_monitor_agent.py   P6 作业过程监测(含 A5 + A6 FastAPI 服务,端口 5002)
- p7_risk_agent.py      P7 风险研判(挂载到 P6 服务)
- p8_disposition_agent.py  P8 人机协同处置(7 个 tool)
- p9_agent.py           P9 审核 + 关闭文案(单文件双 agent)
- channel_gateway_client.py  Channel Gateway Python REST 客户端

启动示例(在 a/ 目录下):
    python -m agents.p6_monitor_agent              # P6+A6 FastAPI 服务(5002)
    python -m agents.p8_disposition_agent         # CLI 入口(可选)
    python P8P9/web_server.py                     # P8P9 独立 Web 服务
"""

# ============================================================
# 公开符号(lazy re-export 模式以避免 import 链过深导致启动慢)
# ============================================================

__all__ = [
    # P6
    "run_a5_monitoring",
    "map_a5_events_to_p6",
    "monitor_start",
    "monitor_events",
    # P7
    "create_risk_agent",
    "run_risk_agent",
    "trigger_a6_assessment",
    "trigger_p7_assessment_batch",
    "register_a6_routes",
    "risk_analyze",
    "risk_list",
    # P8
    "create_disposition_agent",
    "run_disposition_agent",
    "disposition_demo",
    "open_work_ticket",
    "resend_current_card",
    "get_p8_checkpointer",
    # P9
    "create_audit_agent",
    "run_p9_materials_audit",
    "audit_demo",
    "create_closure_agent",
    "run_p9_closure_review",
    "closure_demo",
    # Channel Gateway
    "send_message",
    "reply_to_event",
    "poll_inbound_events",
    "ack_event",
    "GatewayError",
    "SendMessageResult",
]


def __getattr__(name):
    """懒加载公共符号,避免 import 触发完整 LLM/A6/P8 中间件加载。"""
    if name in {"run_a5_monitoring", "map_a5_events_to_p6", "monitor_start", "monitor_events"}:
        from . import p6_monitor_agent
        return getattr(p6_monitor_agent, name)

    if name in {"create_risk_agent", "run_risk_agent",
               "trigger_a6_assessment", "trigger_p7_assessment_batch",
               "register_a6_routes", "risk_analyze", "risk_list"}:
        from . import p7_risk_agent
        return getattr(p7_risk_agent, name)

    if name in {"create_disposition_agent", "run_disposition_agent", "disposition_demo",
               "open_work_ticket", "resend_current_card", "get_p8_checkpointer"}:
        from . import p8_disposition_agent
        return getattr(p8_disposition_agent, name)

    if name in {"create_audit_agent", "run_p9_materials_audit", "audit_demo",
               "create_closure_agent", "run_p9_closure_review", "closure_demo"}:
        from . import p9_agent
        return getattr(p9_agent, name)

    if name in {"send_message", "reply_to_event", "poll_inbound_events",
               "ack_event", "GatewayError", "SendMessageResult"}:
        from . import channel_gateway_client
        return getattr(channel_gateway_client, name)

    raise AttributeError(f"module 'agents' has no attribute {name!r}")