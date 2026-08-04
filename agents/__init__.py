"""
边缘智能作业监测系统 Agent 模块

P1-P10 各阶段的独立 Agent 实现。
"""
from .p1_permit_agent import (
    create_permit_agent,
    run_permit_agent,
    permit_demo,
    permit_submit,
    jsa_analyze,
    permit_generate_draft,
    permit_check,
)

from .p2_task_agent import (
    create_task_agent,
    run_task_agent,
    task_demo,
    task_list,
    task_get,
    task_instance_create,
    task_subscribe,
)

from .p3_context_agent import (
    create_context_agent,
    run_context_agent,
    context_demo,
    context_build,
    context_validate,
    context_history,
)

from .p4_binding_agent import (
    create_binding_agent,
    run_binding_agent,
    binding_demo,
    binding_match,
    binding_status,
    binding_confirm,
    binding_request_manual,
)

from .p5_verify_agent import (
    create_verify_agent,
    run_verify_agent,
    verify_demo,
    verify_checklist,
    verify_execute,
    verify_recommendation,
)

from .p6_monitor_agent import (
    create_monitor_agent,
    run_monitor_agent,
    monitor_demo,
    monitor_start,
    monitor_stop,
    monitor_status,
    monitor_events,
)

from .p7_risk_agent import (
    create_risk_agent,
    run_risk_agent,
    risk_demo,
    risk_analyze,
    risk_grade,
    risk_cases,
    risk_list,
)

from .p8_disposition_agent import (
    create_disposition_agent,
    run_disposition_agent,
    disposition_demo,
    disposition_create,
    disposition_confirm,
    disposition_status,
    disposition_list,
)

from .p9_closure_agent import (
    create_closure_agent,
    run_closure_agent,
    closure_demo,
    closure_status,
    closure_verify,
    closure_report,
    closure_close,
)

from .p10_archive_agent import (
    create_archive_agent,
    run_archive_agent,
    archive_demo,
    archive_task,
    archive_cases,
    archive_performance,
    archive_suggestions,
)

from .workflow import run_workflow, run_workflow_stream

__all__ = [
    # P1: permit
    "create_permit_agent",
    "run_permit_agent",
    "permit_demo",
    # P2: task
    "create_task_agent",
    "run_task_agent",
    "task_demo",
    # P3: context
    "create_context_agent",
    "run_context_agent",
    "context_demo",
    # P4: binding
    "create_binding_agent",
    "run_binding_agent",
    "binding_demo",
    # P5: verify
    "create_verify_agent",
    "run_verify_agent",
    "verify_demo",
    # P6: monitor
    "create_monitor_agent",
    "run_monitor_agent",
    "monitor_demo",
    # P7: risk
    "create_risk_agent",
    "run_risk_agent",
    "risk_demo",
    # P8: disposition
    "create_disposition_agent",
    "run_disposition_agent",
    "disposition_demo",
    # P9: closure
    "create_closure_agent",
    "run_closure_agent",
    "closure_demo",
    # P10: archive
    "create_archive_agent",
    "run_archive_agent",
    "archive_demo",
    # Workflow
    "run_workflow",
    "run_workflow_stream",
]
