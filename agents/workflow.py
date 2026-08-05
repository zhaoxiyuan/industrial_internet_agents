"""
P1-P10 顺序编排工作流
使用 LangGraph StateGraph 实现 P1→P2→...→P10 流水线
支持 HumanInTheLoop 确认机制
"""
from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage

from .p1_permit_agent import permit_submit, permit_generate_draft, jsa_analyze
from .p2_task_agent import task_instance_create
from .p3_context_agent import context_build
from .p4_binding_agent import binding_match, binding_confirm
from .p5_verify_agent import verify_execute, verify_recommendation
from .p6_monitor_agent import monitor_start, monitor_events
from .p7_risk_agent import risk_analyze, risk_list
from .p8_disposition_agent import disposition_create, disposition_confirm
from .p9_closure_agent import closure_status, closure_verify, closure_report, closure_close
from .p10_archive_agent import archive_task, archive_cases, archive_performance, archive_suggestions
from .human_in_the_loop import get_hitl_manager


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
    p1_current_step: str             # P1当前步骤 (permit_submit/jsa_analyze/generate_draft/save_permit/completed)
    permit_submit_result: dict        # permit_submit 结果
    permit_draft_content: dict        # 作业票草稿内容
    permit_draft_missing_fields: list # 缺失字段
    permit_file_path: str             # 保存的作业票文件路径

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
    confirmed_stages: dict           # 已确认的阶段 {"P1": True, "P5": True, ...}
    pending_confirmations: list       # 等待确认的阶段列表 ["P1", "P5", ...]


# ============================================================
# 确认状态管理
# ============================================================

def is_stage_confirmed(state: WorkflowState, stage: str) -> bool:
    """检查阶段是否已确认"""
    return state.get("confirmed_stages", {}).get(stage, False)


def confirm_stage(state: WorkflowState, stage: str) -> None:
    """标记阶段已确认"""
    if "confirmed_stages" not in state:
        state["confirmed_stages"] = {}
    state["confirmed_stages"][stage] = True


def check_and_request_confirmation(state: WorkflowState, stage: str, action: str) -> Any:
    """检查是否需要确认，如果需要则记录（不中断）

    对于P1的子步骤，使用复合key如"P1_permit_submit"
    P1的最终审批使用"P1_approve_permit"
    返回: None（继续执行）
    """
    # 构建确认key
    if stage == "P1":
        if action == "approve_permit":
            confirm_key = "P1_approve_permit"
        else:
            confirm_key = f"P1_{action}"
    else:
        confirm_key = stage

    # 如果已确认，继续
    if is_stage_confirmed(state, confirm_key):
        return None

    # 尚未确认，添加到待确认列表
    if "pending_confirmations" not in state:
        state["pending_confirmations"] = []
    if confirm_key not in state["pending_confirmations"]:
        state["pending_confirmations"].append(confirm_key)
    state["error"] = f"[PENDING_CONFIRMATION] {confirm_key}: {action}"
    return None


def is_stage_pending(state: WorkflowState, stage: str) -> bool:
    """检查阶段是否处于待确认状态（包括子步骤）"""
    pending = state.get("pending_confirmations", [])
    # 直接匹配
    if stage in pending:
        return True
    # 检查P1的子步骤
    if stage == "P1":
        for p in pending:
            if p.startswith("P1_"):
                return True
    return False


# ============================================================
# 各阶段节点函数
# ============================================================

def p1_permit(state: WorkflowState) -> Any:
    """P1: 作业预约、JSA分析与作业票

    分步骤执行，每步都需要人工确认：
    1. permit_submit - 提交作业申请
    2. jsa_analyze - JSA分析
    3. permit_generate_draft - 生成作业票草稿
    4. 保存作业票到文件
    """
    import json
    import os
    import time

    application = state.get("application", {})
    if not application:
        state["error"] = "P1: application is required"
        state["current_stage"] = "P1"
        return state

    current_step = state.get("p1_current_step", "permit_submit")

    # ===== 步骤1: 提交作业申请 =====
    if current_step == "permit_submit":
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
        state["permit_submit_result"] = res
        state["p1_current_step"] = "jsa_analyze"
        state["current_stage"] = "P1"

        # 需要人工确认
        result = check_and_request_confirmation(state, "P1", "permit_submit")
        return result if result else state

    # ===== 步骤2: JSA分析 =====
    elif current_step == "jsa_analyze":
        task_id = state.get("task_id")
        if not task_id:
            state["error"] = "P1: task_id is required for JSA analysis"
            state["current_stage"] = "P1"
            return state

        jsa_str = jsa_analyze.invoke(task_id)
        jsa_result = json.loads(jsa_str)

        if "error" in jsa_result:
            state["error"] = f"P1 jsa_analyze failed: {jsa_result['error']}"
            state["current_stage"] = "P1"
            return state

        state["jsa_result"] = jsa_result.get("result", {})
        state["p1_current_step"] = "generate_draft"
        state["current_stage"] = "P1"

        # 需要人工确认
        result = check_and_request_confirmation(state, "P1", "jsa_analyze")
        return result if result else state

    # ===== 步骤3: 生成作业票草稿 =====
    elif current_step == "generate_draft":
        task_id = state.get("task_id")
        if not task_id:
            state["error"] = "P1: task_id is required for draft generation"
            state["current_stage"] = "P1"
            return state

        draft_str = permit_generate_draft.invoke(task_id)
        draft_result = json.loads(draft_str)

        if "error" in draft_result:
            state["error"] = f"P1 permit_generate_draft failed: {draft_result['error']}"
            state["current_stage"] = "P1"
            return state

        draft_data = draft_result.get("result", {})
        state["permit_draft_id"] = draft_data.get("permit_draft_id", state.get("permit_draft_id"))
        state["permit_draft_content"] = draft_data.get("content", {})
        state["permit_draft_missing_fields"] = draft_data.get("missing_fields", [])
        state["p1_current_step"] = "save_permit"
        state["current_stage"] = "P1"

        # 需要人工确认
        result = check_and_request_confirmation(state, "P1", "generate_draft")
        return result if result else state

    # ===== 步骤4: 保存作业票到文件 =====
    elif current_step == "save_permit":
        # 构建完整作业票信息
        permit_data = {
            "task_id": state.get("task_id", ""),
            "permit_draft_id": state.get("permit_draft_id", ""),
            "application": state.get("application", {}),
            "jsa_result": state.get("jsa_result", {}),
            "permit_content": state.get("permit_draft_content", {}),
            "missing_fields": state.get("permit_draft_missing_fields", []),
            "confirmed_stages": list(state.get("confirmed_stages", {}).keys()),
        }

        # 保存到 data/output 目录
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "output"
        )
        os.makedirs(output_dir, exist_ok=True)

        # 生成文件名: permit_{task_id}_{timestamp}.json
        timestamp = int(time.time())
        task_id_safe = state.get("task_id", "unknown").replace("-", "_")
        filename = f"permit_{task_id_safe}_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(permit_data, f, ensure_ascii=False, indent=2)

        state["permit_file_path"] = filepath
        state["p1_current_step"] = "completed"
        state["current_stage"] = "P1"

        # P1 全部完成，需要最终确认
        result = check_and_request_confirmation(state, "P1", "approve_permit")
        return result if result else state

    # ===== 完成 =====
    state["current_stage"] = "P1"
    return state


def p2_task(state: WorkflowState) -> Any:
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

    # P2: 需要人工确认是否纳入监测
    result = check_and_request_confirmation(state, "P2", "monitor_decide")
    return result if result else state


def p3_context(state: WorkflowState) -> Any:
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

    context_data = result.get("result", {})
    state["work_context"] = context_data.get("context", {})
    state["current_stage"] = "P3"

    # P3: 检测信息缺失，需要人工确认
    missing_fields = context_data.get("missing_fields", [])
    if missing_fields:
        state["error"] = f"P3: 信息缺失确认，缺失字段: {missing_fields}"
        result = check_and_request_confirmation(state, "P3", "context_confirm")
        if result:
            return result

    return state


def p4_binding(state: WorkflowState) -> Any:
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

    # P4: 需要人工确认资源绑定
    result = check_and_request_confirmation(state, "P4", "binding_confirm")
    return result if result else state


def p5_verify(state: WorkflowState) -> Any:
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
    else:
        state["verification_approved"] = True

    state["current_stage"] = "P5"

    # P5: 需要人工确认
    result = check_and_request_confirmation(state, "P5", "approve_verification")
    return result if result else state


def p6_monitor(state: WorkflowState) -> WorkflowState:
    """P6: 作业过程动态监测（不需要人工确认）"""
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


def p7_risk(state: WorkflowState) -> Any:
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

    # P7: 高风险事件需要人工确认
    high_risk_events = [e for e in risk_events if e.get("level") in ("HIGH", "CRITICAL")]
    if high_risk_events:
        state["error"] = f"P7: 检测到 {len(high_risk_events)} 个高风险事件，需要人工确认"
        result = check_and_request_confirmation(state, "P7", "risk_confirm")
        if result:
            return result

    return state


def p8_disposition(state: WorkflowState) -> Any:
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

    # P8: 需要人工确认
    if disposition_tasks:
        result = check_and_request_confirmation(state, "P8", "disposition_confirm")
        if result:
            return result

    return state


def p9_closure(state: WorkflowState) -> Any:
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
        state["error"] = f"P9: 闭环完整性检查未通过，阻塞项: {blocked_by}"

    state["closure_approved"] = is_complete
    state["current_stage"] = "P9"

    # P9: 需要人工确认
    result = check_and_request_confirmation(state, "P9", "approve_closure")
    return result if result else state


def p10_archive(state: WorkflowState) -> Any:
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

    # P10: 报告需要人工确认
    result = check_and_request_confirmation(state, "P10", "report_confirm")
    return result if result else state


# ============================================================
# 路由函数 - 检查是否需要人工确认后结束
# ============================================================

def route_to_next_or_end(state: WorkflowState) -> str:
    """通用路由：如果有待确认阶段则结束，否则继续到下一阶段"""
    pending = state.get("pending_confirmations", [])
    if pending:
        # 有待确认阶段，工作流结束等待用户确认
        return END
    return "continue"


def route_after_p1(state: WorkflowState) -> str:
    """P1 之后路由

    根据当前步骤和待确认状态决定路由：
    - 如果有待确认的子步骤，暂停等待确认
    - 如果没有待确认且当前步骤是最终步骤（completed），移动到 P2
    - 如果没有待确认且还有未完成的步骤，继续执行下一个子步骤
    """
    pending = state.get("pending_confirmations", [])
    current_step = state.get("p1_current_step", "")

    if pending:
        # 有待确认，暂停等待用户确认
        return END

    # 没有待确认
    if current_step == "completed":
        # 所有步骤完成，移动到 P2
        return "P2"

    # 还有未完成的步骤，继续执行下一个子步骤
    return "P1"


def route_after_p2(state: WorkflowState) -> str:
    """P2 之后路由"""
    if "P2" in state.get("pending_confirmations", []):
        return END
    return "P3"


def route_after_p3(state: WorkflowState) -> str:
    """P3 之后路由"""
    if "P3" in state.get("pending_confirmations", []):
        return END
    return "P4"


def route_after_p4(state: WorkflowState) -> str:
    """P4 之后路由"""
    if "P4" in state.get("pending_confirmations", []):
        return END
    return "P5"


def route_after_p5(state: WorkflowState) -> str:
    """P5 之后路由"""
    if "P5" in state.get("pending_confirmations", []):
        return END
    return "P6"


def route_after_p6(state: WorkflowState) -> str:
    """P6 之后路由"""
    if "P6" in state.get("pending_confirmations", []):
        return END
    return "P7"


def route_after_p7(state: WorkflowState) -> str:
    """P7 之后路由"""
    if "P7" in state.get("pending_confirmations", []):
        return END
    return "P8"


def route_after_p8(state: WorkflowState) -> str:
    """P8 之后路由"""
    if "P8" in state.get("pending_confirmations", []):
        return END
    return "P9"


def route_after_p9(state: WorkflowState) -> str:
    """P9 之后路由"""
    if "P9" in state.get("pending_confirmations", []):
        return END
    return "P10"


def route_after_p10(state: WorkflowState) -> str:
    """P10 之后路由"""
    if "P10" in state.get("pending_confirmations", []):
        return END
    return END


# ============================================================
# 构建工作流图
# ============================================================

def build_workflow() -> StateGraph:
    """构建 P1-P10 顺序编排工作流（支持人工确认中断）"""
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

    # 使用条件边 - 当有待确认时结束工作流，等待用户确认后重新运行
    workflow.add_conditional_edges("P1", route_after_p1)
    workflow.add_conditional_edges("P2", route_after_p2)
    workflow.add_conditional_edges("P3", route_after_p3)
    workflow.add_conditional_edges("P4", route_after_p4)
    workflow.add_conditional_edges("P5", route_after_p5)
    workflow.add_edge("P6", "P7")
    workflow.add_conditional_edges("P7", route_after_p7)
    workflow.add_conditional_edges("P8", route_after_p8)
    workflow.add_conditional_edges("P9", route_after_p9)
    workflow.add_conditional_edges("P10", route_after_p10)

    # 设置入口
    workflow.set_entry_point("P1")

    return workflow


# ============================================================
# 工作流编译和运行
# ============================================================

_workflow_graph = None
_checkpointer = MemorySaver()


def get_workflow():
    """获取编译后的工作流（单例）"""
    global _workflow_graph
    if _workflow_graph is None:
        builder = build_workflow()
        _workflow_graph = builder.compile(checkpointer=_checkpointer)
    return _workflow_graph


def run_workflow(application: dict, thread_id: str = "default") -> dict:
    """
    运行 P1-P10 完整工作流（支持 checkpointing）

    参数:
        application: 作业申请字典
        thread_id: 线程ID（用于 checkpoint）
    返回:
        最终状态字典
    """
    config = {"configurable": {"thread_id": thread_id}}

    graph = get_workflow()

    # 检查是否有暂停点可以恢复
    try:
        existing = graph.get_state(config)
        if existing and existing.next:
            # 有暂停点，恢复执行
            result = graph.invoke(None, config)
        else:
            # 没有暂停点，重新开始
            initial_state: WorkflowState = {
                "application": application,
                "current_stage": "P1",
                "confirmed_stages": {},
                "pending_confirmations": [],
            }
            result = graph.invoke(initial_state, config)
    except Exception:
        # 首次运行
        initial_state: WorkflowState = {
            "application": application,
            "current_stage": "P1",
            "confirmed_stages": {},
            "pending_confirmations": [],
        }
        result = graph.invoke(initial_state, config)

    return result


def confirm_and_continue(thread_id: str, stage: str, decision: str = "approve") -> dict:
    """
    确认阶段并继续工作流

    参数:
        thread_id: 线程ID
        stage: 阶段名称（如 P1, P1_permit_submit, P2 等）
        decision: 决定（approve/reject）
    返回:
        更新后的状态
    """
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_workflow()

    # 获取当前状态
    current_state = graph.get_state(config)
    if not current_state:
        return {"error": "No workflow state found"}

    # 获取状态值
    state_values = dict(current_state.values)

    # 更新 confirmed_stages
    confirmed_stages = dict(state_values.get("confirmed_stages", {}))
    confirmed_stages[stage] = True

    # 更新 pending_confirmations
    pending_confirmations = list(state_values.get("pending_confirmations", []))
    if stage in pending_confirmations:
        pending_confirmations.remove(stage)

    update = {
        "confirmed_stages": confirmed_stages,
        "pending_confirmations": pending_confirmations
    }

    # 如果 reject，可能需要设置 error
    if decision == "reject":
        update["error"] = f"{stage} rejected by user"

    # 更新状态并等待完成
    graph.update_state(config, update)

    # 继续执行 - 使用 None 让 LangGraph 从 checkpointer 恢复
    # 注意：这里可能导致状态不一致问题
    result = graph.invoke(None, config)
    return result


def get_workflow_state(thread_id: str = "default") -> dict:
    """获取工作流当前状态"""
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_workflow()
    try:
        state = graph.get_state(config)
        return dict(state) if state else {}
    except Exception as e:
        return {"error": str(e)}


def list_pending_confirmations(thread_id: str = "default") -> list:
    """列出待确认的阶段"""
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_workflow()
    try:
        state = graph.get_state(config)
        if state:
            pending_list = state.values.get("pending_confirmations", [])
            task_id = state.values.get("task_id", "")
            return [{"stage": p, "task_id": task_id} for p in pending_list]
    except Exception:
        pass
    return []
