"""
边缘智能作业监测系统 Agent 模块

P1-P10 各阶段的独立 Agent 实现。
支持两层 HumanInTheLoop:
1. Workflow 层: check_and_request_confirmation() 在阶段边界暂停
2. Agent 层: create_*_agent_with_hitl() 使用 HumanInTheLoopMiddleware
"""
from .p1_permit_agent import (
    create_permit_agent,
    create_permit_agent_with_hitl,
    run_permit_agent,
    run_permit_agent_with_hitl,
    is_agent_interrupted,
    get_agent_next_tools,
    clear_agent_registry,
    permit_demo,
    permit_submit,
    jsa_analyze,
    permit_generate_draft,
    permit_check,
)

from .p2_task_agent import (
    create_task_agent,
    create_task_agent_with_hitl,
    run_task_agent,
    task_demo,
    task_list,
    task_get,
    task_instance_create,
    task_subscribe,
)

from .p3_context_agent import (
    create_context_agent,
    create_context_agent_with_hitl,
    run_context_agent,
    context_demo,
    context_build,
    context_validate,
    context_history,
)

from .p4_binding_agent import (
    create_binding_agent,
    create_binding_agent_with_hitl,
    run_binding_agent,
    binding_demo,
    binding_match,
    binding_status,
    binding_confirm,
    binding_request_manual,
)

from .p5_verify_agent import (
    create_verify_agent,
    create_verify_agent_with_hitl,
    run_verify_agent,
    verify_demo,
    verify_checklist,
    verify_execute,
    verify_recommendation,
)

# p6_monitor_agent 承载 A5 前端 + A6 前端（由 p7_risk_agent.register_a6_routes 挂载），共享端口 5002
# 实际使用的 A5/A6 路由函数无需在此导出

# 2026-09-17 v2：P8 仅暴露 open_work_ticket + resend_current_card 两个 tool
# 旧蓝图版（update_job / hitl_decide / notify_feishu 等 6 个 tool）已废弃
from .p8_disposition_agent import (
    create_disposition_agent,
    run_disposition_agent,
    disposition_demo,
    open_work_ticket,
    resend_current_card,
    get_p8_checkpointer,
)

# 2026-09-17 v2：P9 无 tool，纯对话
# 旧蓝图版（create_closure_agent_with_hitl / run_closure_agent /
#   closure_status / closure_verify / closure_report / closure_close）已废弃
from .p9_closure_agent import (
    create_closure_agent,
    run_p9_closure_review,
    closure_demo,
)

# ============================================================
# 2026-08-20 临时注释：p10_archive_agent 重构 in-flight（文件被清空），
# 导致 `from agents.channel_gateway_client import ...` 这类**任何**
# `from agents.X` 都会强制加载 agents/__init__.py，连带触发 p10 import，
# 进而导致 chat_reply.py / web/server.py 启动失败。
#
# 临时措施：注释掉 P10 的 eager import；P10 CLI 命令暂时不可用，但 P1-P9
# 全部正常加载，chat_reply / web / Gradio 都能起。
#
# **TODO（P10 重构完成后取消注释）**：删除此注释块，恢复原 import。
# ============================================================
# from .p10_archive_agent import (
#     create_archive_agent,
#     create_archive_agent_with_hitl,
#     run_archive_agent,
#     archive_demo,
#     archive_task,
#     archive_cases,
#     archive_performance,
#     archive_suggestions,
# )

from .main_agent import (
    create_main_agent,
    run_main_agent,
    main_demo,
    start_workflow_tool,
    execute_stage_tool,
    get_status_tool,
    confirm_stage_tool,
    list_pending_tool,
    run_workflow,
    confirm_and_continue,
    get_workflow_state,
    list_pending_confirmations,
)

from .main_agent import (
    save_job_application,
    add_job_log,
    save_confirmation,
    get_job_status,
    ensure_job_dir,
    get_stage_result_path,
    read_json_file,
    write_json_file,
    STAGE_EXECUTORS,
)
from .hitl.human_in_the_loop import (
    HumanInTheLoop,
    HumanConfirmRequest,
    HumanConfirmResult,
    get_hitl_manager,
    create_confirm_request,
    submit_confirm_result,
)

__all__ = [
    # Main Agent
    "create_main_agent",
    "run_main_agent",
    "main_demo",
    "start_workflow_tool",
    "execute_stage_tool",
    "get_status_tool",
    "confirm_stage_tool",
    "list_pending_tool",
    "run_workflow",
    "confirm_and_continue",
    "get_workflow_state",
    "list_pending_confirmations",
    # P1: permit
    "create_permit_agent",
    "create_permit_agent_with_hitl",
    "run_permit_agent",
    "run_permit_agent_with_hitl",
    "permit_demo",
    # P2: task
    "create_task_agent",
    "create_task_agent_with_hitl",
    "run_task_agent",
    "task_demo",
    # P3: context
    "create_context_agent",
    "create_context_agent_with_hitl",
    "run_context_agent",
    "context_demo",
    # P4: binding
    "create_binding_agent",
    "create_binding_agent_with_hitl",
    "run_binding_agent",
    "binding_demo",
    # P5: verify
    "create_verify_agent",
    "create_verify_agent_with_hitl",
    "run_verify_agent",
    "verify_demo",
    # P6: monitor (通过 p6_monitor_agent 独立服务访问，含 A5 前端 + 共享端口 5002)
    # P7: risk  (p7_risk_agent 暴露 risk_analyze/risk_list 工具，A6 路由通过 register_a6_routes 挂载到 P6)
    # P8: disposition
    "create_disposition_agent",
    "run_disposition_agent",
    "disposition_demo",
    "open_work_ticket",
    "resend_current_card",
    # P9: closure (v2 无 tool；run_p9_closure_review 是 record_closure_review approved 调用的入口)
    "create_closure_agent",
    "run_p9_closure_review",
    "closure_demo",
    # P10: archive
    "create_archive_agent",
    "create_archive_agent_with_hitl",
    "run_archive_agent",
    "archive_demo",
    # Workflow
    "run_workflow",
    "confirm_and_continue",
    "get_workflow_state",
    "list_pending_confirmations",
    # Job persistence
    "save_job_application",
    "add_job_log",
    "save_confirmation",
    "get_job_status",
    "ensure_job_dir",
    "read_json_file",
    "write_json_file",
    "STAGE_EXECUTORS",
    # HumanInTheLoop
    "HumanInTheLoop",
    "HumanConfirmRequest",
    "HumanConfirmResult",
    "get_hitl_manager",
    "create_confirm_request",
    "submit_confirm_result",
]
