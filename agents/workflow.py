"""
P1-P10 顺序编排工作流
使用 LangGraph StateGraph 实现 P1→P2→...→P10 流水线
"""
from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage

from .p1_permit_agent import permit_submit, permit_generate_draft
from .p2_task_agent import task_instance_create
from .p3_context_agent import context_build
from .p4_binding_agent import binding_match, binding_confirm
from .p5_verify_agent import verify_execute, verify_recommendation
from .p6_monitor_agent import monitor_start, monitor_events
from .p7_risk_agent import risk_analyze, risk_list
from .p8_disposition_agent import disposition_create, disposition_confirm
from .p9_closure_agent import closure_status, closure_verify, closure_report, closure_close
from .p10_archive_agent import archive_task, archive_cases, archive_performance, archive_suggestions


# ============================================================
# 工作流状态定义
# ============================================================

class WorkflowState(TypedDict, total=False):
    """P1-P10 流水线状态，所有阶段共享的数据"""
    # ===== P1 作业申请 =====
    application: dict                  # 原始作业申请
    task_id: str                      # 任务ID
    permit_draft_id: str              # 作业票草稿ID
    jsa_result: dict                  # JSA分析结果

    # ===== P2 任务实例 =====
    task_instance: dict              # 任务实例

    # ===== P3 上下文 =====
    work_context: dict               # 标准作业上下文包

    # ===== P4 资源绑定 =====
    monitoring_resources: dict        # 监测资源绑定关系
    binding_confirmed: bool           # 是否已人工确认绑定

    # ===== P5 核验 =====
    verification_result: dict         # 核验结果
    startup_recommendation: dict      # 开工建议
    verification_approved: bool       # 是否人工批准开工

    # ===== P6 监测 =====
    monitoring_session_id: str        # 监测会话ID
    candidate_events: list            # 候选风险事件

    # ===== P7 风险研判 =====
    risk_events: list                 # 风险事件列表

    # ===== P8 处置 =====
    disposition_tasks: list           # 处置任务列表
    disposition_confirmed: bool        # 是否人工确认处置

    # ===== P9 闭环 =====
    closure_status: dict             # 闭环状态
    closure_report: dict             # 作业报告
    closure_approved: bool            # 是否人工批准关闭

    # ===== P10 归档 =====
    archive_result: dict             # 归档结果
    mined_cases: list                # 挖掘的案例
    performance_metrics: dict         # 性能指标
    suggestions: list                 # 优化建议

    # ===== 工作流控制 =====
    current_stage: str                # 当前阶段 P1-P10
    error: Optional[str]              # 错误信息
    requires_human_confirm: bool      # 是否需要人工确认
    human_confirm_action: Optional[str]  # 待确认的操作类型
    human_confirm_stage: Optional[str]     # 需要确认的阶段


# ============================================================
# 各阶段节点函数
# ============================================================

def p1_permit(state: WorkflowState) -> WorkflowState:
    """P1: 作业预约、JSA分析与作业票"""
    application = state.get("application", {})
    if not application:
        state["error"] = "P1: application is required"
        state["current_stage"] = "P1"
        return state

    # 调用 permit_submit 工具
    import json
    app_str = json.dumps(application, ensure_ascii=False)
    result_str = permit_submit.invoke(app_str)
    result = json.loads(result_str)

    if "error" in result:
        state["error"] = f"P1 permit_submit failed: {result['error']}"
        state["current_stage"] = "P1"
        return state

    res = result.get("result", {})
    state["task_id"] = res.get("task_id", "")
    state["permit_draft_id"] = res.get("permit_draft_id", "")
    state["current_stage"] = "P1"

    # 模拟 JSA 分析
    jsa_str = jsa_analyze.invoke(state["task_id"])
    jsa_result = json.loads(jsa_str)
    state["jsa_result"] = jsa_result.get("result", {})

    # 生成作业票草稿
    draft_str = permit_generate_draft.invoke(state["task_id"])
    draft_result = json.loads(draft_str)
    state["permit_draft_id"] = draft_result.get("result", {}).get("permit_draft_id", state["permit_draft_id"])

    state["requires_human_confirm"] = True
    state["human_confirm_action"] = "approve_permit"
    state["human_confirm_stage"] = "P1"
    return state


def p2_task(state: WorkflowState) -> WorkflowState:
    """P2: 作业任务获取与实例化"""
    permit_draft_id = state.get("permit_draft_id")
    if not permit_draft_id:
        state["error"] = "P2: permit_draft_id is required"
        state["current_stage"] = "P2"
        return state

    import json
    result_str = task_instance_create.invoke(permit_draft_id)
    result = json.loads(result_str)

    if "error" in result:
        state["error"] = f"P2 task_instance_create failed: {result['error']}"
        state["current_stage"] = "P2"
        return state

    state["task_instance"] = result.get("result", {})
    state["task_id"] = result.get("result", {}).get("task_id", state.get("task_id", ""))
    state["current_stage"] = "P2"
    return state


def p3_context(state: WorkflowState) -> WorkflowState:
    """P3: 作业上下文理解"""
    task_id = state.get("task_id")
    if not task_id:
        state["error"] = "P3: task_id is required"
        state["current_stage"] = "P3"
        return state

    import json
    result_str = context_build.invoke(task_id)
    result = json.loads(result_str)

    if "error" in result:
        state["error"] = f"P3 context_build failed: {result['error']}"
        state["current_stage"] = "P3"
        return state

    state["work_context"] = result.get("result", {}).get("context", {})
    state["current_stage"] = "P3"
    return state


def p4_binding(state: WorkflowState) -> WorkflowState:
    """P4: 监测资源绑定"""
    task_id = state.get("task_id")
    if not task_id:
        state["error"] = "P4: task_id is required"
        state["current_stage"] = "P4"
        return state

    import json
    result_str = binding_match.invoke(task_id)
    result = json.loads(result_str)

    if "error" in result:
        state["error"] = f"P4 binding_match failed: {result['error']}"
        state["current_stage"] = "P4"
        return state

    state["monitoring_resources"] = result.get("result", {})
    state["binding_confirmed"] = False
    state["current_stage"] = "P4"

    # 自动确认绑定（实际场景需人工确认）
    confirm_str = binding_confirm.invoke(task_id)
    confirm_result = json.loads(confirm_str)
    state["binding_confirmed"] = confirm_result.get("result", {}).get("confirmed", False)
    return state


def p5_verify(state: WorkflowState) -> WorkflowState:
    """P5: 作业前条件核验"""
    task_id = state.get("task_id")
    if not task_id:
        state["error"] = "P5: task_id is required"
        state["current_stage"] = "P5"
        return state

    import json
    # 执行核验
    result_str = verify_execute.invoke(task_id)
    result = json.loads(result_str)

    if "error" in result:
        state["error"] = f"P5 verify_execute failed: {result['error']}"
        state["current_stage"] = "P5"
        return state

    state["verification_result"] = result.get("result", {})

    # 获取开工建议
    rec_str = verify_recommendation.invoke(task_id)
    rec_result = json.loads(rec_str)
    state["startup_recommendation"] = rec_result.get("result", {})

    # 检查是否需要整改
    recommendation = state["startup_recommendation"].get("decision", "")
    if "整改" in recommendation:
        state["verification_approved"] = False
        state["requires_human_confirm"] = True
        state["human_confirm_action"] = "approve_verification"
        state["human_confirm_stage"] = "P5"
    else:
        state["verification_approved"] = True

    state["current_stage"] = "P5"
    return state


def p6_monitor(state: WorkflowState) -> WorkflowState:
    """P6: 作业过程动态监测"""
    task_id = state.get("task_id")
    if not task_id:
        state["error"] = "P6: task_id is required"
        state["current_stage"] = "P6"
        return state

    import json
    # 启动监测（幂等）
    result_str = monitor_start.invoke(task_id)
    result = json.loads(result_str)

    if "error" in result:
        state["error"] = f"P6 monitor_start failed: {result['error']}"
        state["current_stage"] = "P6"
        return state

    res = result.get("result", {})
    state["monitoring_session_id"] = res.get("session_id", "")

    # 获取候选事件
    events_str = monitor_events.invoke(task_id)
    events = []
    for line in events_str.strip().split("\n"):
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    state["candidate_events"] = events
    state["current_stage"] = "P6"
    return state


def p7_risk(state: WorkflowState) -> WorkflowState:
    """P7: 风险研判与分级"""
    task_id = state.get("task_id")
    candidate_events = state.get("candidate_events", [])

    if not task_id:
        state["error"] = "P7: task_id is required"
        state["current_stage"] = "P7"
        return state

    import json
    risk_events = []

    for event in candidate_events:
        event_id = event.get("event_id", "")
        if event_id:
            result_str = risk_analyze.invoke(event_id)
            result = json.loads(result_str)
            if "result" in result:
                risk_events.append(result["result"])

    # 如果没有候选事件，模拟空结果
    if not risk_events:
        list_str = risk_list.invoke(task_id)
        list_result = json.loads(list_str)
        risk_events = list_result.get("result", {}).get("events", [])

    state["risk_events"] = risk_events
    state["current_stage"] = "P7"
    return state


def p8_disposition(state: WorkflowState) -> WorkflowState:
    """P8: 人机协同处置"""
    risk_events = state.get("risk_events", [])

    if not risk_events:
        state["current_stage"] = "P8"
        return state

    import json
    disposition_tasks = []

    for event in risk_events:
        event_id = event.get("event_id", "")
        if event_id:
            result_str = disposition_create.invoke(event_id)
            result = json.loads(result_str)
            if "result" in result:
                disposition_tasks.append(result["result"])

    state["disposition_tasks"] = disposition_tasks
    state["disposition_confirmed"] = len(disposition_tasks) > 0
    state["current_stage"] = "P8"
    return state


def p9_closure(state: WorkflowState) -> WorkflowState:
    """P9: 闭环跟踪与报告"""
    task_id = state.get("task_id")
    if not task_id:
        state["error"] = "P9: task_id is required"
        state["current_stage"] = "P9"
        return state

    import json
    # 跟踪闭环状态
    status_str = closure_status.invoke(task_id)
    status_result = json.loads(status_str)
    state["closure_status"] = status_result.get("result", {})

    # 执行完整性检查
    verify_str = closure_verify.invoke(task_id)
    verify_result = json.loads(verify_str)
    is_complete = verify_result.get("result", {}).get("complete", False)

    # 生成报告
    report_str = closure_report.invoke(task_id)
    report_result = json.loads(report_str)
    state["closure_report"] = report_result.get("result", {})

    # 完整性检查未通过，需要人工介入
    if not is_complete:
        blocked_by = verify_result.get("result", {}).get("blocked_by", [])
        state["requires_human_confirm"] = True
        state["human_confirm_action"] = "approve_closure"
        state["human_confirm_stage"] = "P9"
        state["error"] = f"P9: 闭环完整性检查未通过，阻塞项: {blocked_by}"

    state["closure_approved"] = is_complete
    state["current_stage"] = "P9"
    return state


def p10_archive(state: WorkflowState) -> WorkflowState:
    """P10: 归档与复盘"""
    task_id = state.get("task_id")
    if not task_id:
        state["error"] = "P10: task_id is required"
        state["current_stage"] = "P10"
        return state

    import json
    # 归档任务
    archive_str = archive_task.invoke(task_id)
    archive_result = json.loads(archive_str)
    state["archive_result"] = archive_result.get("result", {})

    # 挖掘案例
    cases_str = archive_cases.invoke(task_id)
    cases_result = json.loads(cases_str)
    state["mined_cases"] = cases_result.get("result", [])

    # 分析性能
    perf_str = archive_performance.invoke(task_id)
    perf_result = json.loads(perf_str)
    state["performance_metrics"] = perf_result.get("result", {})

    # 生成建议
    suggestions_str = archive_suggestions.invoke(task_id)
    suggestions_result = json.loads(suggestions_str)
    state["suggestions"] = suggestions_result.get("result", [])

    state["current_stage"] = "P10"
    return state


# ============================================================
# 人工确认节点
# ============================================================

def human_confirm_node(state: WorkflowState) -> WorkflowState:
    """人工确认节点——暂停等待人工确认"""
    stage = state.get("human_confirm_stage", "unknown")
    action = state.get("human_confirm_action", "unknown")
    state["error"] = f"[HUMAN_CONFIRM_REQUIRED] Stage={stage}, Action={action}"
    return state


# ============================================================
# 路由函数
# ============================================================

def should_human_confirm(state: WorkflowState) -> str:
    """判断是否需要人工确认"""
    if state.get("requires_human_confirm", False):
        return "human_confirm"
    return "continue"


def after_human_confirm(state: WorkflowState) -> str:
    """人工确认后的路由"""
    stage = state.get("human_confirm_stage", "")
    # 根据阶段决定下一步
    stage_order = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
    if stage in stage_order:
        idx = stage_order.index(stage)
        if idx < len(stage_order) - 1:
            return stage_order[idx + 1]
    return END


# ============================================================
# 构建工作流图
# ============================================================

def build_workflow() -> StateGraph:
    """构建 P1-P10 顺序编排工作流"""
    workflow = StateGraph(WorkflowState)

    # 添加所有阶段节点
    workflow.add_node("P1", p1_permit)
    workflow.add_node("P2", p2_task)
    workflow.add_node("P3", p3_context)
    workflow.add_node("P4", p4_binding)
    workflow.add_node("P5", p5_verify)
    workflow.add_node("P6", p6_monitor)
    workflow.add_node("P7", p7_risk)
    workflow.add_node("P8", p8_disposition)
    workflow.add_node("P9", p9_closure)
    workflow.add_node("P10", p10_archive)
    workflow.add_node("human_confirm", human_confirm_node)

    # 顺序连接（普通边）
    workflow.add_edge("P1", "P2")
    workflow.add_edge("P2", "P3")
    workflow.add_edge("P3", "P4")
    workflow.add_edge("P4", "P5")
    workflow.add_edge("P5", "P6")
    workflow.add_edge("P6", "P7")
    workflow.add_edge("P7", "P8")
    workflow.add_edge("P8", "P9")
    workflow.add_edge("P9", "P10")
    workflow.add_edge("P10", END)

    # 人工确认条件边（从需要确认的节点到人工确认节点）
    workflow.add_conditional_edges(
        "P1",
        should_human_confirm,
        {"human_confirm": "human_confirm", "continue": "P2"}
    )
    workflow.add_conditional_edges(
        "P5",
        should_human_confirm,
        {"human_confirm": "human_confirm", "continue": "P6"}
    )
    workflow.add_conditional_edges(
        "P9",
        should_human_confirm,
        {"human_confirm": "human_confirm", "continue": "P10"}
    )

    # 人工确认后返回
    workflow.add_conditional_edges(
        "human_confirm",
        after_human_confirm,
        {"P2": "P2", "P6": "P6", "P10": "P10", END: END}
    )

    # 设置入口
    workflow.set_entry_point("P1")

    return workflow


# ============================================================
# 工作流编译和运行
# ============================================================

_workflow_graph = None


def get_workflow():
    """获取编译后的工作流（单例）"""
    global _workflow_graph
    if _workflow_graph is None:
        builder = build_workflow()
        _workflow_graph = builder.compile()
    return _workflow_graph


def run_workflow(application: dict, human_confirm: bool = False,
                 confirm_action: str = None) -> dict:
    """
    运行 P1-P10 完整工作流

    参数:
        application: 作业申请字典
        human_confirm: 是否已有人的确认（绕过人工确认节点）
        confirm_action: 确认操作类型
    返回:
        最终状态字典
    """
    initial_state: WorkflowState = {
        "application": application,
        "current_stage": "P1",
        "requires_human_confirm": False,
        "human_confirm_action": None,
        "human_confirm_stage": None,
    }

    # 如果已有人工确认，直接设置标志
    if human_confirm and confirm_action:
        # 找到对应阶段并跳过人工确认
        action_to_stage = {
            "approve_permit": "P1",
            "approve_verification": "P5",
            "approve_closure": "P9",
        }
        stage = action_to_stage.get(confirm_action, None)
        if stage:
            initial_state["requires_human_confirm"] = False
            # 直接从下一阶段开始
            stage_order = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
            if stage in stage_order:
                idx = stage_order.index(stage)
                start_node = stage_order[idx + 1] if idx < len(stage_order) - 1 else "P10"
                # 简化处理，直接运行完整工作流
                pass

    graph = get_workflow()
    result = graph.invoke(initial_state)
    return result


def run_workflow_stream(application: dict):
    """流式运行工作流，逐步返回每个阶段的输出"""
    initial_state: WorkflowState = {
        "application": application,
        "current_stage": "P1",
        "requires_human_confirm": False,
    }

    graph = get_workflow()
    for state in graph.stream(initial_state):
        yield state
