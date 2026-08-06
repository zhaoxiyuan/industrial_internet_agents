"""
主 Agent - P1-P10 调度管理中心
通过文件传递协调各阶段 Agent 执行
支持 HumanInTheLoop 中断恢复
"""
import os
import json
from datetime import datetime, timezone
from typing import TypedDict, Optional, Any, List
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from model.chat_model import create_chat_model
from utils.agent_utils import extract_output
from .utils import make_response, make_error, SCHEMA_VERSION

# 导入各阶段工具函数
from .p1_permit_agent import permit_submit, jsa_analyze, permit_generate_draft
from .p2_task_agent import task_instance_create
from .p3_context_agent import context_build
from .p4_binding_agent import binding_match
from .p5_verify_agent import verify_execute, verify_recommendation
from .p6_monitor_agent import monitor_start, monitor_events
from .p7_risk_agent import risk_analyze, risk_list
from .p8_disposition_agent import disposition_create
from .p9_closure_agent import closure_status, closure_verify, closure_report, closure_close
from .p10_archive_agent import archive_task, archive_cases, archive_performance, archive_suggestions


# ============================================================
# 文件路径管理
# ============================================================

def get_jobs_dir():
    """获取作业根目录"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "jobs"
    )


def get_job_dir(job_id: str) -> str:
    """获取指定作业的目录"""
    return os.path.join(get_jobs_dir(), job_id)


def ensure_job_dir(job_id: str) -> str:
    """确保作业目录存在"""
    job_dir = get_job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


def get_stage_result_path(job_id: str, stage: str) -> str:
    """获取指定阶段结果文件路径"""
    return os.path.join(get_job_dir(job_id), f"{stage}_result.json")


def read_json_file(filepath: str) -> dict:
    """读取 JSON 文件"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_json_file(filepath: str, data: dict) -> None:
    """写入 JSON 文件"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 持久化函数
# ============================================================

def save_job_application(job_id: str, application: dict) -> str:
    """保存作业申请"""
    job_dir = ensure_job_dir(job_id)
    filepath = os.path.join(job_dir, "application.json")
    write_json_file(filepath, {
        "job_id": job_id,
        "application": application,
        "saved_at": datetime.now(timezone.utc).isoformat()
    })
    return filepath


def add_job_log(job_id: str, log_entry: dict) -> str:
    """追加作业执行日志"""
    job_dir = ensure_job_dir(job_id)
    log_file = os.path.join(job_dir, "logs.json")

    logs = []
    if os.path.exists(log_file):
        try:
            logs = read_json_file(log_file)
            if isinstance(logs, dict):
                logs = [logs]
        except:
            logs = []

    log_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    logs.append(log_entry)

    write_json_file(log_file, logs)
    return log_file


def save_confirmation(job_id: str, stage: str, decision: str, notes: str = "") -> str:
    """保存确认记录"""
    job_dir = ensure_job_dir(job_id)
    confirm_file = os.path.join(job_dir, "confirmations.json")

    confirmations = []
    if os.path.exists(confirm_file):
        try:
            confirmations = read_json_file(confirm_file)
            if isinstance(confirmations, dict):
                confirmations = [confirmations]
        except:
            confirmations = []

    record = {
        "stage": stage,
        "decision": decision,
        "notes": notes,
        "confirmed_at": datetime.now(timezone.utc).isoformat()
    }
    confirmations.append(record)

    write_json_file(confirm_file, confirmations)
    return confirm_file


def get_job_status(job_id: str) -> dict:
    """获取作业状态"""
    job_dir = get_job_dir(job_id)
    if not os.path.exists(job_dir):
        return {"error": "Job not found"}

    status = {
        "job_id": job_id,
        "stages": {},
        "current_stage": None,
        "pending_confirmations": []
    }

    for stage in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"]:
        result_file = os.path.join(job_dir, f"{stage}_result.json")
        if os.path.exists(result_file):
            result_data = read_json_file(result_file)
            status["stages"][stage] = {
                "completed": result_data.get("completed", False),
                "has_pending": bool(result_data.get("pending_confirmation")),
            }
            if result_data.get("pending_confirmation"):
                status["pending_confirmations"].append({
                    "stage": stage.upper(),
                    "pending": result_data["pending_confirmation"]
                })

    for stage in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"]:
        result_file = os.path.join(job_dir, f"{stage}_result.json")
        if not os.path.exists(result_file):
            status["current_stage"] = stage.upper()
            break
    else:
        status["current_stage"] = "completed"

    return status


# ============================================================
# 阶段执行函数
# ============================================================

def execute_p1(job_id: str) -> dict:
    """执行 P1 阶段：作业预约、JSA分析与作业票"""
    app_file = os.path.join(get_job_dir(job_id), "application.json")
    application = read_json_file(app_file).get("application", {})

    if not application:
        return {"error": "No application found", "completed": False}

    result = {
        "job_id": job_id,
        "stage": "P1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 1. permit_submit
        app_str = json.dumps(application, ensure_ascii=False)
        submit_result = json.loads(permit_submit.invoke(app_str))

        if "error" in submit_result:
            result["error"] = submit_result["error"]
            return result

        res = submit_result.get("result", {})
        result["task_id"] = res.get("task_id", "")
        result["permit_draft_id"] = res.get("permit_draft_id", "")

        # 2. jsa_analyze
        jsa_result = json.loads(jsa_analyze.invoke(result["task_id"]))
        if "result" in jsa_result:
            result["jsa_result"] = jsa_result["result"]

        # 3. permit_generate_draft
        draft_result = json.loads(permit_generate_draft.invoke(result["task_id"]))
        if "result" in draft_result:
            result["permit_content"] = draft_result["result"].get("content", {})
            result["missing_fields"] = draft_result["result"].get("missing_fields", [])

        # 4. 保存作业票
        permit_data = {
            "task_id": result.get("task_id"),
            "permit_draft_id": result.get("permit_draft_id"),
            "application": application,
            "jsa_result": result.get("jsa_result"),
            "permit_content": result.get("permit_content"),
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        output_dir = os.path.join(get_job_dir(job_id), "permit.json")
        write_json_file(output_dir, permit_data)
        result["permit_file"] = output_dir

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        if result.get("missing_fields"):
            result["pending_confirmation"] = {
                "type": "missing_fields",
                "fields": result["missing_fields"],
                "message": "作业票存在缺失字段，需要人工确认"
            }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p1"), result)
    add_job_log(job_id, {
        "action": "execute_p1",
        "result": "success" if result["completed"] else "failed"
    })

    return result


def execute_p2(job_id: str) -> dict:
    """执行 P2 阶段：作业任务实例化"""
    p1_result = read_json_file(get_stage_result_path(job_id, "p1"))
    permit_draft_id = p1_result.get("permit_draft_id", "")

    result = {
        "job_id": job_id,
        "stage": "P2",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        task_result = json.loads(task_instance_create.invoke(permit_draft_id))

        if "result" in task_result:
            result["task_instance"] = task_result["result"]
            result["task_id"] = task_result["result"].get("task_id", "")

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "monitor_decide",
            "message": "是否将此作业纳入智能监测"
        }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p2"), result)
    add_job_log(job_id, {"action": "execute_p2", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p3(job_id: str) -> dict:
    """执行 P3 阶段：作业上下文理解"""
    p2_result = read_json_file(get_stage_result_path(job_id, "p2"))
    task_id = p2_result.get("task_id", "")

    result = {
        "job_id": job_id,
        "stage": "P3",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        context_result = json.loads(context_build.invoke(task_id))

        if "result" in context_result:
            result["context"] = context_result["result"].get("context", {})
            result["missing_fields"] = context_result["result"].get("missing_fields", [])

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        if result.get("missing_fields"):
            result["pending_confirmation"] = {
                "type": "context_missing",
                "fields": result["missing_fields"],
                "message": "上下文存在缺失字段，需要人工确认"
            }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p3"), result)
    add_job_log(job_id, {"action": "execute_p3", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p4(job_id: str) -> dict:
    """执行 P4 阶段：监测资源绑定"""
    p3_result = read_json_file(get_stage_result_path(job_id, "p3"))
    task_id = p3_result.get("task_id", "")

    result = {
        "job_id": job_id,
        "stage": "P4",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        binding_result = json.loads(binding_match.invoke(task_id))

        if "result" in binding_result:
            result["bindings"] = binding_result["result"].get("bindings", {})
            result["unmatched_resources"] = binding_result["result"].get("unmatched_resources", [])

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "binding_confirm",
            "message": "请确认监测资源绑定是否正确"
        }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p4"), result)
    add_job_log(job_id, {"action": "execute_p4", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p5(job_id: str) -> dict:
    """执行 P5 阶段：开工前条件核验"""
    p4_result = read_json_file(get_stage_result_path(job_id, "p4"))
    task_id = p4_result.get("task_id", "")

    result = {
        "job_id": job_id,
        "stage": "P5",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        verify_result = json.loads(verify_execute.invoke(task_id))
        if "result" in verify_result:
            result["verification_result"] = verify_result["result"]

        rec_result = json.loads(verify_recommendation.invoke(task_id))
        if "result" in rec_result:
            result["recommendation"] = rec_result["result"]

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "verification_approve",
            "decision": rec_result.get("result", {}).get("decision", ""),
            "message": "请确认开工条件核验结果"
        }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p5"), result)
    add_job_log(job_id, {"action": "execute_p5", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p6(job_id: str) -> dict:
    """执行 P6 阶段：作业过程动态监测"""
    p5_result = read_json_file(get_stage_result_path(job_id, "p5"))
    task_id = p5_result.get("task_id", "")

    result = {
        "job_id": job_id,
        "stage": "P6",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        monitor_result = json.loads(monitor_start.invoke(task_id))
        if "result" in monitor_result:
            result["session_id"] = monitor_result["result"].get("session_id", "")

        events_str = monitor_events.invoke(task_id)
        events = []
        for line in events_str.strip().split("\n"):
            if line:
                try:
                    events.append(json.loads(line))
                except:
                    pass

        result["candidate_events"] = events
        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p6"), result)
    add_job_log(job_id, {"action": "execute_p6", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p7(job_id: str) -> dict:
    """执行 P7 阶段：风险研判与分级"""
    p6_result = read_json_file(get_stage_result_path(job_id, "p6"))
    task_id = p6_result.get("task_id", "")
    events = p6_result.get("candidate_events", [])

    result = {
        "job_id": job_id,
        "stage": "P7",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        risk_events = []
        for event in events:
            event_id = event.get("event_id", "")
            if event_id:
                risk_result = json.loads(risk_analyze.invoke(event_id))
                if "result" in risk_result:
                    risk_events.append(risk_result["result"])

        if not risk_events:
            list_result = json.loads(risk_list.invoke(task_id))
            risk_events = list_result.get("result", {}).get("events", [])

        result["risk_events"] = risk_events
        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        high_risk = [e for e in risk_events if e.get("level") in ("HIGH", "CRITICAL")]
        if high_risk:
            result["pending_confirmation"] = {
                "type": "high_risk",
                "count": len(high_risk),
                "message": f"检测到 {len(high_risk)} 个高风险事件，需要人工确认"
            }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p7"), result)
    add_job_log(job_id, {"action": "execute_p7", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p8(job_id: str) -> dict:
    """执行 P8 阶段：人机协同处置"""
    p7_result = read_json_file(get_stage_result_path(job_id, "p7"))
    risk_events = p7_result.get("risk_events", [])

    result = {
        "job_id": job_id,
        "stage": "P8",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        disposition_tasks = []
        for event in risk_events:
            event_id = event.get("event_id", "")
            if event_id:
                disp_result = json.loads(disposition_create.invoke(event_id))
                if "result" in disp_result:
                    disposition_tasks.append(disp_result["result"])

        result["disposition_tasks"] = disposition_tasks
        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        if disposition_tasks:
            result["pending_confirmation"] = {
                "type": "disposition_confirm",
                "message": "请确认处置任务"
            }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p8"), result)
    add_job_log(job_id, {"action": "execute_p8", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p9(job_id: str) -> dict:
    """执行 P9 阶段：闭环跟踪与报告"""
    p8_result = read_json_file(get_stage_result_path(job_id, "p8"))
    task_id = p8_result.get("task_id", "")

    result = {
        "job_id": job_id,
        "stage": "P9",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        status_result = json.loads(closure_status.invoke(task_id))
        if "result" in status_result:
            result["closure_status"] = status_result["result"]

        verify_result = json.loads(closure_verify.invoke(task_id))
        if "result" in verify_result:
            result["verify_result"] = verify_result["result"]

        report_result = json.loads(closure_report.invoke(task_id))
        if "result" in report_result:
            result["report"] = report_result["result"]

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "closure_close",
            "message": "请确认是否关闭事件和作业"
        }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p9"), result)
    add_job_log(job_id, {"action": "execute_p9", "result": "success" if result["completed"] else "failed"})

    return result


def execute_p10(job_id: str) -> dict:
    """执行 P10 阶段：归档与复盘"""
    p9_result = read_json_file(get_stage_result_path(job_id, "p9"))
    task_id = p9_result.get("task_id", "")

    result = {
        "job_id": job_id,
        "stage": "P10",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        archive_result = json.loads(archive_task.invoke(task_id))
        if "result" in archive_result:
            result["archive_result"] = archive_result["result"]

        cases_result = json.loads(archive_cases.invoke(task_id))
        if "result" in cases_result:
            result["mined_cases"] = cases_result["result"]

        perf_result = json.loads(archive_performance.invoke(task_id))
        if "result" in perf_result:
            result["performance"] = perf_result["result"]

        suggestions_result = json.loads(archive_suggestions.invoke(task_id))
        if "result" in suggestions_result:
            result["suggestions"] = suggestions_result["result"]

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "report_confirm",
            "message": "请确认归档报告"
        }

    except Exception as e:
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p10"), result)
    add_job_log(job_id, {"action": "execute_p10", "result": "success" if result["completed"] else "failed"})

    return result


# 阶段执行映射
STAGE_EXECUTORS = {
    "P1": execute_p1,
    "P2": execute_p2,
    "P3": execute_p3,
    "P4": execute_p4,
    "P5": execute_p5,
    "P6": execute_p6,
    "P7": execute_p7,
    "P8": execute_p8,
    "P9": execute_p9,
    "P10": execute_p10,
}


# ============================================================
# 工具定义
# ============================================================

@tool(description="启动 P1-P10 工作流协调。传入 job_id，开始协调各阶段执行。")
def start_workflow_tool(job_id: str) -> str:
    """
    启动工作流协调。

    参数:
        job_id: 作业单编号
    返回:
        标准 JSON 响应
    """
    job_dir = get_job_dir(job_id)

    if not os.path.exists(job_dir):
        return json.dumps(make_error(
            code="JOB_NOT_FOUND",
            message=f"作业 {job_id} 不存在",
            recoverable=False
        ), ensure_ascii=False)

    app_file = os.path.join(job_dir, "application.json")
    if not os.path.exists(app_file):
        return json.dumps(make_error(
            code="NO_APPLICATION",
            message="作业申请文件不存在",
            recoverable=False
        ), ensure_ascii=False)

    add_job_log(job_id, {
        "action": "workflow_start",
        "message": f"开始协调作业 {job_id}"
    })

    response_data = {
        "job_id": job_id,
        "status": "started",
        "message": f"开始协调作业 {job_id}",
        "application_file": app_file,
        "instruction": "请依次调用 execute_stage 工具执行 P1-P10 各阶段"
    }

    return json.dumps(make_response("workflow started", response_data), ensure_ascii=False)


@tool(description="执行指定阶段。读取上一阶段结果，执行本阶段，保存结果文件。")
def execute_stage_tool(job_id: str, stage: str) -> str:
    """
    执行指定阶段。

    参数:
        job_id: 作业单编号
        stage: 阶段名称 (P1-P10)
    返回:
        标准 JSON 响应，包含执行结果文件路径
    """
    stage = stage.upper()

    if stage not in STAGE_EXECUTORS:
        return json.dumps(make_error(
            code="INVALID_STAGE",
            message=f"无效的阶段: {stage}",
            recoverable=False,
            details={"valid_stages": list(STAGE_EXECUTORS.keys())}
        ), ensure_ascii=False)

    stage_order = list(STAGE_EXECUTORS.keys())
    stage_idx = stage_order.index(stage)

    if stage_idx > 0:
        prev_stage = stage_order[stage_idx - 1]
        prev_result_file = get_stage_result_path(job_id, f"p{stage_idx}")
        if not os.path.exists(prev_result_file):
            prev_result = read_json_file(prev_result_file)
            if not prev_result.get("completed"):
                return json.dumps(make_error(
                    code="PREV_STAGE_INCOMPLETE",
                    message=f"前置阶段 {prev_stage} 未完成",
                    recoverable=True
                ), ensure_ascii=False)

    executor = STAGE_EXECUTORS[stage]
    result = executor(job_id)

    response_data = {
        "job_id": job_id,
        "stage": stage,
        "result_file": get_stage_result_path(job_id, f"p{stage_idx + 1}"),
        "completed": result.get("completed", False),
        "has_pending": bool(result.get("pending_confirmation")),
    }

    if result.get("pending_confirmation"):
        response_data["pending_confirmation"] = result["pending_confirmation"]
        response_data["message"] = result["pending_confirmation"].get("message", "需要人工确认")
    elif result.get("error"):
        response_data["error"] = result["error"]
        response_data["message"] = f"执行失败: {result['error']}"
    else:
        response_data["message"] = f"{stage} 执行完成"

    return json.dumps(make_response("stage executed", response_data), ensure_ascii=False)


@tool(description="查询作业状态。返回各阶段完成情况和待确认项。")
def get_status_tool(job_id: str) -> str:
    """查询作业状态"""
    status = get_job_status(job_id)

    if "error" in status:
        return json.dumps(make_error(
            code="JOB_NOT_FOUND",
            message=status["error"],
            recoverable=False
        ), ensure_ascii=False)

    return json.dumps(make_response("job status", status), ensure_ascii=False)


@tool(description="确认阶段完成，清除待确认状态，继续工作流。")
def confirm_stage_tool(job_id: str, stage: str, decision: str = "approve", notes: str = "") -> str:
    """
    确认阶段继续执行。

    参数:
        job_id: 作业单编号
        stage: 阶段名称 (P1-P10)
        decision: 决定 (approve/reject)
        notes: 备注
    """
    stage = stage.upper()
    stage_lower = stage.lower()

    result_file = get_stage_result_path(job_id, stage_lower)
    result = read_json_file(result_file)

    if not result:
        return json.dumps(make_error(
            code="STAGE_NOT_EXECUTED",
            message=f"阶段 {stage} 未执行",
            recoverable=False
        ), ensure_ascii=False)

    save_confirmation(job_id, stage, decision, notes)

    if "pending_confirmation" in result:
        del result["pending_confirmation"]
        write_json_file(result_file, result)

    add_job_log(job_id, {
        "action": "stage_confirm",
        "stage": stage,
        "decision": decision,
        "notes": notes
    })

    response_data = {
        "job_id": job_id,
        "stage": stage,
        "decision": decision,
        "message": f"{stage} 已确认，决策: {decision}",
        "next_instruction": f"可以继续执行 {stage} 或下一阶段"
    }

    return json.dumps(make_response("stage confirmed", response_data), ensure_ascii=False)


@tool(description="列出所有待确认的阶段。")
def list_pending_tool(job_id: str) -> str:
    """列出待确认阶段"""
    status = get_job_status(job_id)

    if "error" in status:
        return json.dumps(make_error(
            code="JOB_NOT_FOUND",
            message=status["error"],
            recoverable=False
        ), ensure_ascii=False)

    return json.dumps(make_response("pending confirmations", {
        "job_id": job_id,
        "pending_count": len(status.get("pending_confirmations", [])),
        "pending_list": status.get("pending_confirmations", []),
        "current_stage": status.get("current_stage")
    }), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

SYSTEM_PROMPT = """你是工业互联网边缘智能作业管理的主调度 Agent。

你通过文件传递协调 P1-P10 各阶段 Agent：
1. 作业申请保存在 data/jobs/{job_id}/application.json
2. 各阶段执行结果保存到 data/jobs/{job_id}/p{n}_result.json
3. 依次调度各阶段执行
4. 每个阶段执行后检查是否有待确认项

工作流阶段：P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10

当需要启动工作流时，调用 start_workflow_tool。
当需要执行某个阶段时，调用 execute_stage_tool。
当用户确认后，调用 confirm_stage_tool 清除待确认状态。
当需要查询状态时，调用 get_status_tool。
当需要列出待确认项时，调用 list_pending_tool。"""


def create_main_agent():
    """创建主 Agent"""
    llm = create_chat_model()
    tools = [
        start_workflow_tool,
        execute_stage_tool,
        get_status_tool,
        confirm_stage_tool,
        list_pending_tool,
    ]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_main_agent(message: str, thread_id: str = "default") -> str:
    """运行主 Agent"""
    agent = create_main_agent()
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, config)
    return extract_output(result)


def main_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_main_agent(message)


# ============================================================
# 工作流入口函数（供 server.py 调用）
# ============================================================

def run_workflow(application: dict, thread_id: str) -> dict:
    """运行工作流（按文件传递模式执行）"""
    job_id = thread_id

    save_job_application(job_id, application)

    add_job_log(job_id, {
        "action": "workflow_start",
        "message": f"开始执行作业 {job_id}"
    })

    for stage_name, executor in STAGE_EXECUTORS.items():
        add_job_log(job_id, {
            "action": f"execute_{stage_name.lower()}",
            "message": f"开始执行 {stage_name}"
        })

        result = executor(job_id)

        if result.get("pending_confirmation"):
            add_job_log(job_id, {
                "action": "workflow_pending",
                "stage": stage_name,
                "message": result["pending_confirmation"].get("message", "需要人工确认")
            })
            return {
                "job_id": job_id,
                "current_stage": stage_name,
                "pending_confirmations": [stage_name],
                "confirmed_stages": {},
                "status": "waiting"
            }

    add_job_log(job_id, {
        "action": "workflow_completed",
        "message": f"作业 {job_id} 执行完成"
    })

    return {
        "job_id": job_id,
        "current_stage": "completed",
        "pending_confirmations": [],
        "confirmed_stages": list(STAGE_EXECUTORS.keys()),
        "status": "completed"
    }


def confirm_and_continue(thread_id: str, stage: str, decision: str = "approve", notes: str = "") -> dict:
    """确认阶段并继续工作流"""
    job_id = thread_id

    result_file = get_stage_result_path(job_id, stage.lower())
    result = read_json_file(result_file)

    save_confirmation(job_id, stage, decision, notes)

    if "pending_confirmation" in result:
        del result["pending_confirmation"]
        write_json_file(result_file, result)

    add_job_log(job_id, {
        "action": "stage_confirm",
        "stage": stage,
        "decision": decision,
        "notes": notes
    })

    stage_order = list(STAGE_EXECUTORS.keys())
    current_idx = stage_order.index(stage.upper()) if stage.upper() in stage_order else 0

    for i in range(current_idx + 1, len(stage_order)):
        next_stage = stage_order[i]
        executor = STAGE_EXECUTORS[next_stage]

        add_job_log(job_id, {
            "action": f"execute_{next_stage.lower()}",
            "message": f"继续执行 {next_stage}"
        })

        next_result = executor(job_id)

        if next_result.get("pending_confirmation"):
            return {
                "job_id": job_id,
                "current_stage": next_stage,
                "pending_confirmations": [next_stage],
                "confirmed_stages": stage_order[:i+1],
                "status": "waiting"
            }

    return {
        "job_id": job_id,
        "current_stage": "completed",
        "pending_confirmations": [],
        "confirmed_stages": stage_order,
        "status": "completed"
    }


def get_workflow_state(thread_id: str) -> dict:
    """获取工作流状态"""
    return get_job_status(thread_id)


def list_pending_confirmations(thread_id: str) -> list:
    """列出待确认的阶段"""
    status = get_job_status(thread_id)
    return status.get("pending_confirmations", [])
