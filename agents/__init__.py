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

from .p6_monitor_agent import (
    create_monitor_agent,
    create_monitor_agent_with_hitl,
    run_monitor_agent,
    monitor_demo,
    monitor_start,
    monitor_stop,
    monitor_status,
    monitor_events,
)

from .p7_risk_agent import (
    create_risk_agent,
    create_risk_agent_with_hitl,
    run_risk_agent,
    risk_demo,
    risk_analyze,
    risk_grade,
    risk_cases,
    risk_list,
)

from .p8_disposition_agent import (
    create_disposition_agent,
    create_disposition_agent_with_hitl,
    run_disposition_agent,
    disposition_demo,
    disposition_create,
    disposition_confirm,
    disposition_status,
    disposition_list,
)

from .p9_closure_agent import (
    create_closure_agent,
    create_closure_agent_with_hitl,
    run_closure_agent,
    closure_demo,
    closure_status,
    closure_verify,
    closure_report,
    closure_close,
)

from .p10_archive_agent import (
    create_archive_agent,
    create_archive_agent_with_hitl,
    run_archive_agent,
    archive_demo,
    archive_task,
    archive_cases,
    archive_performance,
    archive_suggestions,
)

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
    # P6: monitor
    "create_monitor_agent",
    "create_monitor_agent_with_hitl",
    "run_monitor_agent",
    "monitor_demo",
    # P7: risk
    "create_risk_agent",
    "create_risk_agent_with_hitl",
    "run_risk_agent",
    "risk_demo",
    # P8: disposition
    "create_disposition_agent",
    "create_disposition_agent_with_hitl",
    "run_disposition_agent",
    "disposition_demo",
    # P9: closure
    "create_closure_agent",
    "create_closure_agent_with_hitl",
    "run_closure_agent",
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
