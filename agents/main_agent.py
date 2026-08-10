"""
主 Agent - P1-P10 调度管理中心
通过文件传递协调各阶段 Agent 执行
支持 HumanInTheLoop 中断恢复
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import TypedDict, Optional, Any, List
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from .model.chat_model import create_chat_model
from .utils import extract_output, get_stage_logger, push_websocket_log

# 导入 Workflow 模块
from .workflow import (
    get_job_dir, ensure_job_dir, get_stage_result_path,
    read_json_file, write_json_file,
    init_workflow_status, update_workflow_status, get_workflow_status,
    ALL_STAGES,
    save_job_application, add_job_log, save_confirmation, get_job_status,
)
# 导入工具响应和 Prompt 加载器
from .utils import make_response, make_error, SCHEMA_VERSION, load_system_prompt

# 配置日志
logger = logging.getLogger("main_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# 导入各阶段工具函数
from .p1_permit_agent import permit_submit, jsa_analyze, permit_generate_draft, run_permit_agent_with_hitl, is_agent_interrupted, get_agent_next_tools
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
# 阶段执行函数
# ============================================================

def execute_p1(job_id: str, resume: bool = False) -> dict:
    """执行 P1 阶段：作业预约、JSA分析与作业票

    支持 HumanInTheLoop 中断恢复：
    - 首次执行时调用 HITL Agent，工具调用前会暂停等待确认
    - 确认后再次调用 with resume=True 从中断点恢复执行
    """
    log = get_stage_logger("P1")
    log.log_enter(job_id, {"resume": resume})
    result_file = get_stage_result_path(job_id, "p1")
    existing_result = read_json_file(result_file)

    # 当 resume=True 时，强制从 checkpoint 恢复执行，不依赖 is_agent_interrupted 的检查
    if resume or is_agent_interrupted(job_id):
        log.log_hitl_interrupt(job_id, get_agent_next_tools(job_id))
        # 从中断点恢复执行
        hitl_result = run_permit_agent_with_hitl(None, job_id, resume=True)
        if not hitl_result:
            logger.error(f"[execute_p1] hitl_result 为空: job_id={job_id}, resume={resume}")
            return {"job_id": job_id, "stage": "P1", "completed": False, "error": "恢复执行失败：hitl_result 为空"}
        if hitl_result["interrupted"]:
            # 仍然中断，等待下次确认
            next_tools = hitl_result.get("next", [])
            result = {
                "job_id": job_id,
                "stage": "P1",
                "completed": False,
                "pending_confirmation": {
                    "type": "hitl_recover",
                    "message": "P1 Agent 工具调用等待确认",
                    "next_tools": next_tools,
                }
            }
            log.log_exit(job_id, result)
            return result
        # 恢复后执行完成，解析结果
        result_text = hitl_result.get("result", "{}")
        try:
            result_data = json.loads(result_text)
        except:
            result_data = {"result": result_text}
        result = _process_p1_result(job_id, result_data, existing_result)
        log.log_exit(job_id, result)
        return result

    # 首次执行或正常流程
    app_file = get_job_dir(job_id) + "/application.json"
    application = read_json_file(app_file).get("application", {})

    if not application:
        logger.warning(f"[P1] !!! 作业申请为空: job_id={job_id}")
        result = {"error": "No application found", "completed": False}
        log.log_exit(job_id, result)
        return result

    result = {
        "job_id": job_id,
        "stage": "P1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 构建发送给 Agent 的消息
        app_str = json.dumps(application, ensure_ascii=False)
        message = f"""请处理以下作业申请：

作业申请内容：{app_str}

请依次执行：
1. 调用 permit_submit 工具提交作业申请
2. 调用 jsa_analyze 工具进行JSA分析
3. 调用 permit_generate_draft 工具生成作业票草稿"""

        # 使用 HITL Agent 执行
        logger.info(f"[P1] 调用 run_permit_agent_with_hitl: job_id={job_id}")
        push_websocket_log(job_id, "INFO", "AGENT", f"[P1] 开始执行作业许可流程")
        hitl_result = run_permit_agent_with_hitl(message, job_id)

        if hitl_result["interrupted"]:
            # 被 HITL 中断，等待人工确认
            next_tools = hitl_result.get("next", [])
            log.log_hitl_interrupt(job_id, next_tools)
            add_job_log(job_id, {
                "action": "execute_p1_hitl_interrupt",
                "next_tools": next_tools
            })
            result["pending_confirmation"] = {
                "type": "hitl_tool_call",
                "message": "P1 Agent 工具调用需要人工确认",
                "next_tools": next_tools,
            }
            write_json_file(result_file, result)
            log.log_exit(job_id, result)
            return result

        # 执行完成，解析结果
        result_text = hitl_result.get("result", "{}")
        try:
            result_data = json.loads(result_text)
        except:
            result_data = {"result": result_text}

        result = _process_p1_result(job_id, result_data, result)
        log.log_exit(job_id, result)
        return result

    except Exception as e:
        log.log_error(job_id, e)
        result["error"] = str(e)
        write_json_file(result_file, result)
        log.log_exit(job_id, result)
        return result


def _process_p1_result(job_id: str, result_data: dict, existing_result: dict) -> dict:
    """处理 P1 执行结果，提取并保存作业票数据"""
    result = existing_result.copy() if existing_result else {}
    result["job_id"] = job_id
    result["stage"] = "P1"
    result["completed"] = True
    result["completed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        # 尝试从结果中提取数据
        submit_data = result_data.get("result", result_data)

        if isinstance(submit_data, dict):
            result["task_id"] = submit_data.get("task_id", "")
            result["permit_draft_id"] = submit_data.get("permit_draft_id", "")
            result["jsa_result"] = submit_data.get("jsa_result", {})
            result["permit_content"] = submit_data.get("permit_content", {})
            result["missing_fields"] = submit_data.get("missing_fields", [])

        # 保存作业票
        app_file = get_job_dir(job_id) + "/application.json"
        application = read_json_file(app_file).get("application", {})

        permit_data = {
            "task_id": result.get("task_id"),
            "permit_draft_id": result.get("permit_draft_id"),
            "application": application,
            "jsa_result": result.get("jsa_result"),
            "permit_content": result.get("permit_content"),
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        output_path = get_job_dir(job_id) + "/permit.json"
        write_json_file(output_path, permit_data)
        result["permit_file"] = output_path

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
    log = get_stage_logger("P2")
    log.log_enter(job_id)
    p1_result = read_json_file(get_stage_result_path(job_id, "p1"))
    permit_draft_id = p1_result.get("permit_draft_id", "")
    logger.info(f"[P2] permit_draft_id={permit_draft_id}")

    result = {
        "job_id": job_id,
        "stage": "P2",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P2] 调用 task_instance_create.invoke: permit_draft_id={permit_draft_id}")
        task_result = json.loads(task_instance_create.invoke(permit_draft_id))
        log.log_tool_call("task_instance_create", {"permit_draft_id": permit_draft_id}, task_result)

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
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p2"), result)
    add_job_log(job_id, {"action": "execute_p2", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p3(job_id: str) -> dict:
    """执行 P3 阶段：作业上下文理解"""
    log = get_stage_logger("P3")
    log.log_enter(job_id)
    p2_result = read_json_file(get_stage_result_path(job_id, "p2"))
    task_id = p2_result.get("task_id", "")
    logger.info(f"[P3] task_id={task_id}")

    result = {
        "job_id": job_id,
        "stage": "P3",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P3] 调用 context_build.invoke: task_id={task_id}")
        context_result = json.loads(context_build.invoke(task_id))
        log.log_tool_call("context_build", {"task_id": task_id}, context_result)

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
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p3"), result)
    add_job_log(job_id, {"action": "execute_p3", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p4(job_id: str) -> dict:
    """执行 P4 阶段：监测资源绑定"""
    log = get_stage_logger("P4")
    log.log_enter(job_id)
    p3_result = read_json_file(get_stage_result_path(job_id, "p3"))
    task_id = p3_result.get("task_id", "")
    logger.info(f"[P4] task_id={task_id}")

    result = {
        "job_id": job_id,
        "stage": "P4",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P4] 调用 binding_match.invoke: task_id={task_id}")
        binding_result = json.loads(binding_match.invoke(task_id))
        log.log_tool_call("binding_match", {"task_id": task_id}, binding_result)

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
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p4"), result)
    add_job_log(job_id, {"action": "execute_p4", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p5(job_id: str) -> dict:
    """执行 P5 阶段：开工前条件核验"""
    log = get_stage_logger("P5")
    log.log_enter(job_id)
    p4_result = read_json_file(get_stage_result_path(job_id, "p4"))
    task_id = p4_result.get("task_id", "")
    logger.info(f"[P5] task_id={task_id}")

    result = {
        "job_id": job_id,
        "stage": "P5",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P5] 调用 verify_execute.invoke: task_id={task_id}")
        verify_result = json.loads(verify_execute.invoke(task_id))
        log.log_tool_call("verify_execute", {"task_id": task_id}, verify_result)
        if "result" in verify_result:
            result["verification_result"] = verify_result["result"]

        logger.info(f"[P5] 调用 verify_recommendation.invoke: task_id={task_id}")
        rec_result = json.loads(verify_recommendation.invoke(task_id))
        log.log_tool_call("verify_recommendation", {"task_id": task_id}, rec_result)
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
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p5"), result)
    add_job_log(job_id, {"action": "execute_p5", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p6(job_id: str) -> dict:
    """执行 P6 阶段：作业过程动态监测"""
    log = get_stage_logger("P6")
    log.log_enter(job_id)
    p5_result = read_json_file(get_stage_result_path(job_id, "p5"))
    task_id = p5_result.get("task_id", "")
    logger.info(f"[P6] task_id={task_id}")

    result = {
        "job_id": job_id,
        "stage": "P6",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P6] 调用 monitor_start.invoke: task_id={task_id}")
        monitor_result = json.loads(monitor_start.invoke(task_id))
        log.log_tool_call("monitor_start", {"task_id": task_id}, monitor_result)
        if "result" in monitor_result:
            result["session_id"] = monitor_result["result"].get("session_id", "")

        logger.info(f"[P6] 调用 monitor_events.invoke: task_id={task_id}")
        events_str = monitor_events.invoke(task_id)
        events = []
        for line in events_str.strip().split("\n"):
            if line:
                try:
                    events.append(json.loads(line))
                except:
                    pass
        log.log_tool_call("monitor_events", {"task_id": task_id}, {"events_count": len(events)})

        result["candidate_events"] = events
        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p6"), result)
    add_job_log(job_id, {"action": "execute_p6", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p7(job_id: str) -> dict:
    """执行 P7 阶段：风险研判与分级"""
    log = get_stage_logger("P7")
    log.log_enter(job_id)
    p6_result = read_json_file(get_stage_result_path(job_id, "p6"))
    task_id = p6_result.get("task_id", "")
    events = p6_result.get("candidate_events", [])
    logger.info(f"[P7] task_id={task_id}, events_count={len(events)}")

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
                logger.info(f"[P7] 调用 risk_analyze.invoke: event_id={event_id}")
                risk_result = json.loads(risk_analyze.invoke(event_id))
                log.log_tool_call("risk_analyze", {"event_id": event_id}, risk_result)
                if "result" in risk_result:
                    risk_events.append(risk_result["result"])

        if not risk_events:
            logger.info(f"[P7] 无风险事件，调用 risk_list.invoke")
            list_result = json.loads(risk_list.invoke(task_id))
            log.log_tool_call("risk_list", {"task_id": task_id}, list_result)
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
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p7"), result)
    add_job_log(job_id, {"action": "execute_p7", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p8(job_id: str) -> dict:
    """执行 P8 阶段：人机协同处置"""
    log = get_stage_logger("P8")
    log.log_enter(job_id)
    p7_result = read_json_file(get_stage_result_path(job_id, "p7"))
    risk_events = p7_result.get("risk_events", [])
    logger.info(f"[P8] risk_events_count={len(risk_events)}")

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
                logger.info(f"[P8] 调用 disposition_create.invoke: event_id={event_id}")
                disp_result = json.loads(disposition_create.invoke(event_id))
                log.log_tool_call("disposition_create", {"event_id": event_id}, disp_result)
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
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p8"), result)
    add_job_log(job_id, {"action": "execute_p8", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p9(job_id: str) -> dict:
    """执行 P9 阶段：闭环跟踪与报告"""
    log = get_stage_logger("P9")
    log.log_enter(job_id)
    p8_result = read_json_file(get_stage_result_path(job_id, "p8"))
    task_id = p8_result.get("task_id", "")
    logger.info(f"[P9] task_id={task_id}")

    result = {
        "job_id": job_id,
        "stage": "P9",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P9] 调用 closure_status.invoke: task_id={task_id}")
        status_result = json.loads(closure_status.invoke(task_id))
        log.log_tool_call("closure_status", {"task_id": task_id}, status_result)
        if "result" in status_result:
            result["closure_status"] = status_result["result"]

        logger.info(f"[P9] 调用 closure_verify.invoke: task_id={task_id}")
        verify_result = json.loads(closure_verify.invoke(task_id))
        log.log_tool_call("closure_verify", {"task_id": task_id}, verify_result)
        if "result" in verify_result:
            result["verify_result"] = verify_result["result"]

        logger.info(f"[P9] 调用 closure_report.invoke: task_id={task_id}")
        report_result = json.loads(closure_report.invoke(task_id))
        log.log_tool_call("closure_report", {"task_id": task_id}, report_result)
        if "result" in report_result:
            result["report"] = report_result["result"]

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "closure_close",
            "message": "请确认是否关闭事件和作业"
        }

    except Exception as e:
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p9"), result)
    add_job_log(job_id, {"action": "execute_p9", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

    return result


def execute_p10(job_id: str) -> dict:
    """执行 P10 阶段：归档与复盘"""
    log = get_stage_logger("P10")
    log.log_enter(job_id)
    p9_result = read_json_file(get_stage_result_path(job_id, "p9"))
    task_id = p9_result.get("task_id", "")
    logger.info(f"[P10] task_id={task_id}")

    result = {
        "job_id": job_id,
        "stage": "P10",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        logger.info(f"[P10] 调用 archive_task.invoke: task_id={task_id}")
        archive_result = json.loads(archive_task.invoke(task_id))
        log.log_tool_call("archive_task", {"task_id": task_id}, archive_result)
        if "result" in archive_result:
            result["archive_result"] = archive_result["result"]

        logger.info(f"[P10] 调用 archive_cases.invoke: task_id={task_id}")
        cases_result = json.loads(archive_cases.invoke(task_id))
        log.log_tool_call("archive_cases", {"task_id": task_id}, cases_result)
        if "result" in cases_result:
            result["mined_cases"] = cases_result["result"]

        logger.info(f"[P10] 调用 archive_performance.invoke: task_id={task_id}")
        perf_result = json.loads(archive_performance.invoke(task_id))
        log.log_tool_call("archive_performance", {"task_id": task_id}, perf_result)
        if "result" in perf_result:
            result["performance"] = perf_result["result"]

        logger.info(f"[P10] 调用 archive_suggestions.invoke: task_id={task_id}")
        suggestions_result = json.loads(archive_suggestions.invoke(task_id))
        log.log_tool_call("archive_suggestions", {"task_id": task_id}, suggestions_result)
        if "result" in suggestions_result:
            result["suggestions"] = suggestions_result["result"]

        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        result["pending_confirmation"] = {
            "type": "report_confirm",
            "message": "请确认归档报告"
        }

    except Exception as e:
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p10"), result)
    add_job_log(job_id, {"action": "execute_p10", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)

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
    logger.info(f"[start_workflow_tool] >>> 工具入口: job_id={job_id}")
    job_dir = get_job_dir(job_id)

    if not os.path.exists(job_dir):
        logger.warning(f"[start_workflow_tool] !!! 作业目录不存在: job_id={job_id}")
        return json.dumps(make_error(
            code="JOB_NOT_FOUND",
            message=f"作业 {job_id} 不存在",
            recoverable=False
        ), ensure_ascii=False)

    app_file = os.path.join(job_dir, "application.json")
    if not os.path.exists(app_file):
        logger.warning(f"[start_workflow_tool] !!! 作业申请文件不存在: job_id={job_id}")
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

    logger.info(f"[start_workflow_tool] <<< 工具出口: job_id={job_id}, status=started")
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
    import os
    logger.info(f"[execute_stage_tool] >>> 工具入口: job_id={job_id}, stage={stage}")
    stage = stage.upper()

    if stage not in STAGE_EXECUTORS:
        logger.warning(f"[execute_stage_tool] !!! 无效的阶段: {stage}")
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
                logger.warning(f"[execute_stage_tool] !!! 前置阶段未完成: {prev_stage}")
                return json.dumps(make_error(
                    code="PREV_STAGE_INCOMPLETE",
                    message=f"前置阶段 {prev_stage} 未完成",
                    recoverable=True
                ), ensure_ascii=False)

    logger.info(f"[execute_stage_tool] 执行阶段: {stage}")
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

    logger.info(f"[execute_stage_tool] <<< 工具出口: stage={stage}, completed={response_data['completed']}, has_pending={response_data['has_pending']}")
    return json.dumps(make_response("stage executed", response_data), ensure_ascii=False)


@tool(description="查询作业状态。返回各阶段完成情况和待确认项。")
def get_status_tool(job_id: str) -> str:
    """查询作业状态"""
    logger.info(f"[get_status_tool] >>> 工具入口: job_id={job_id}")
    status = get_job_status(job_id)

    if "error" in status:
        logger.warning(f"[get_status_tool] !!! 作业不存在: job_id={job_id}")
        return json.dumps(make_error(
            code="JOB_NOT_FOUND",
            message=status["error"],
            recoverable=False
        ), ensure_ascii=False)

    logger.info(f"[get_status_tool] <<< 工具出口: job_id={job_id}, current_stage={status.get('current_stage')}")
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
    logger.info(f"[confirm_stage_tool] >>> 工具入口: job_id={job_id}, stage={stage}, decision={decision}")
    stage = stage.upper()
    stage_lower = stage.lower()

    result_file = get_stage_result_path(job_id, stage_lower)
    result = read_json_file(result_file)

    if not result:
        logger.warning(f"[confirm_stage_tool] !!! 阶段未执行: stage={stage}")
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

    logger.info(f"[confirm_stage_tool] <<< 工具出口: stage={stage}, decision={decision}")
    return json.dumps(make_response("stage confirmed", response_data), ensure_ascii=False)


@tool(description="列出所有待确认的阶段。")
def list_pending_tool(job_id: str) -> str:
    """列出待确认阶段"""
    logger.info(f"[list_pending_tool] >>> 工具入口: job_id={job_id}")
    status = get_job_status(job_id)

    if "error" in status:
        logger.warning(f"[list_pending_tool] !!! 作业不存在: job_id={job_id}")
        return json.dumps(make_error(
            code="JOB_NOT_FOUND",
            message=status["error"],
            recoverable=False
        ), ensure_ascii=False)

    pending_count = len(status.get("pending_confirmations", []))
    logger.info(f"[list_pending_tool] <<< 工具出口: job_id={job_id}, pending_count={pending_count}")
    return json.dumps(make_response("pending confirmations", {
        "job_id": job_id,
        "pending_count": pending_count,
        "pending_list": status.get("pending_confirmations", []),
        "current_stage": status.get("current_stage")
    }), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

def create_main_agent():
    """创建主 Agent"""
    logger.info("[create_main_agent] 创建主 Agent")
    llm = create_chat_model()
    tools = [
        start_workflow_tool,
        execute_stage_tool,
        get_status_tool,
        confirm_stage_tool,
        list_pending_tool,
    ]
    system_prompt = load_system_prompt("MAIN")
    return create_agent(model=llm, tools=tools, system_prompt=system_prompt)


def run_main_agent(message: str, thread_id: str = "default") -> str:
    """运行主 Agent"""
    logger.info(f"[run_main_agent] >>> Agent 入口: thread_id={thread_id}, message={message[:100]}...")
    agent = create_main_agent()
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, config)
    output = extract_output(result)
    logger.info(f"[run_main_agent] <<< Agent 出口: output={output[:100] if output else 'None'}...")
    return output


def main_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_main_agent(message)


# ============================================================
# 工作流入口函数（供 server.py 调用）
# ============================================================

def run_workflow(application: dict, thread_id: str) -> dict:
    """运行工作流（按文件传递模式执行）"""
    import os
    job_id = thread_id
    logger.info(f"[run_workflow] >>> 工作流入口: job_id={job_id}")

    save_job_application(job_id, application)

    # 初始化工作流状态文件
    init_workflow_status(job_id)
    update_workflow_status(job_id, {
        "main_agent": {"status": "running", "current_stage": ""},
        "P1_status": "pending", "P2_status": "pending", "P3_status": "pending",
        "P4_status": "pending", "P5_status": "pending", "P6_status": "pending",
        "P7_status": "pending", "P8_status": "pending", "P9_status": "pending",
        "P10_status": "pending"
    })
    _broadcast_state(job_id)

    add_job_log(job_id, {
        "action": "workflow_start",
        "message": f"开始执行作业 {job_id}"
    })

    for stage_name, executor in STAGE_EXECUTORS.items():
        logger.info(f"[run_workflow] 执行阶段: {stage_name}")
        add_job_log(job_id, {
            "action": f"execute_{stage_name.lower()}",
            "message": f"开始执行 {stage_name}"
        })

        # 更新主Agent当前阶段
        update_workflow_status(job_id, {
            "main_agent": {"status": "running", "current_stage": stage_name},
            f"{stage_name}_status": "running"
        })
        _broadcast_state(job_id)

        result = executor(job_id)

        # 更新阶段状态
        if result.get("pending_confirmation"):
            update_workflow_status(job_id, {
                f"{stage_name}_status": "waiting",
                "main_agent": {"status": "waiting", "pending_confirmations": [stage_name]}
            })
            _broadcast_state(job_id)
            logger.info(f"[run_workflow] 阶段等待确认: stage={stage_name}")
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
        else:
            update_workflow_status(job_id, {f"{stage_name}_status": "completed"})
            _broadcast_state(job_id)

    logger.info(f"[run_workflow] <<< 工作流完成: job_id={job_id}")
    update_workflow_status(job_id, {
        "main_agent": {"status": "completed", "current_stage": "completed", "pending_confirmations": []}
    })
    _broadcast_state(job_id)
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


def confirm_and_continue(thread_id: str, stage: str, decision: str = "approve", notes: str = "", async_execute: bool = False) -> dict:
    """确认阶段并继续工作流

    对于 P1 阶段的 HITL 中断，确认后会调用 execute_p1(resume=True) 恢复执行

    Args:
        thread_id: 作业ID
        stage: 阶段名称 (P1-P10)
        decision: 决定 (approve/reject)
        notes: 备注
        async_execute: 是否异步执行（True=点击确认后立即返回，后台执行）
    """
    import os
    logger.info(f"[confirm_and_continue] >>> 确认入口: thread_id={thread_id}, stage={stage}, decision={decision}, async={async_execute}")
    if not thread_id:
        raise ValueError("thread_id 不能为空")
    if not stage:
        raise ValueError("stage 不能为空")

    job_id = thread_id

    result_file = get_stage_result_path(job_id, stage.lower())
    result = read_json_file(result_file)

    save_confirmation(job_id, stage, decision, notes)

    # 更新工作流状态：清除该阶段的 waiting 状态
    update_workflow_status(job_id, {f"{stage.upper()}_status": "completed"})
    _broadcast_state(job_id)

    # 清除 pending_confirmation 标记
    if result and "pending_confirmation" in result:
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

    # 异步执行模式：立即返回，后台继续执行
    if async_execute:
        logger.info(f"[confirm_and_continue] 异步模式，立即返回")
        # 启动后台线程执行
        thread = threading.Thread(
            target=_confirm_and_continue_async,
            args=(job_id, stage, decision, notes, current_idx, stage_order)
        )
        thread.daemon = True
        thread.start()
        return {
            "job_id": job_id,
            "current_stage": stage.upper(),
            "pending_confirmations": [],
            "confirmed_stages": [stage.upper()],
            "status": "executing",
            "message": f"{stage} 已确认，异步执行中"
        }

    # 同步执行模式：等待执行完成

    # P1 阶段特殊处理：HITL 中断恢复
    if stage.upper() == "P1" and is_agent_interrupted(job_id):
        logger.info(f"[confirm_and_continue] P1 HITL 恢复执行")
        add_job_log(job_id, {
            "action": "p1_hitl_resume",
            "message": "P1 阶段 HITL 中断恢复执行"
        })
        p1_result = execute_p1(job_id, resume=True)

        if p1_result.get("pending_confirmation"):
            logger.info(f"[confirm_and_continue] P1 恢复后仍等待确认")
            return {
                "job_id": job_id,
                "current_stage": "P1",
                "pending_confirmations": ["P1"],
                "confirmed_stages": [],
                "status": "waiting"
            }

        # P1 恢复执行后完成，继续后续阶段
        current_idx = 0  # P1 已完成，从 P2 继续

    for i in range(current_idx + 1, len(stage_order)):
        next_stage = stage_order[i]
        executor = STAGE_EXECUTORS[next_stage]

        logger.info(f"[confirm_and_continue] 继续执行下一阶段: {next_stage}")
        add_job_log(job_id, {
            "action": f"execute_{next_stage.lower()}",
            "message": f"继续执行 {next_stage}"
        })

        # 更新下一阶段状态为 running
        update_workflow_status(job_id, {
            "main_agent": {"status": "running", "current_stage": next_stage},
            f"{next_stage}_status": "running"
        })
        _broadcast_state(job_id)

        next_result = executor(job_id)

        if next_result.get("pending_confirmation"):
            update_workflow_status(job_id, {
                f"{next_stage}_status": "waiting",
                "main_agent": {"status": "waiting", "pending_confirmations": [next_stage]}
            })
            _broadcast_state(job_id)
            logger.info(f"[confirm_and_continue] 下一阶段等待确认: {next_stage}")
            return {
                "job_id": job_id,
                "current_stage": next_stage,
                "pending_confirmations": [next_stage],
                "confirmed_stages": stage_order[:i],  # i 之前的是真正完成的，i 之后的是未执行的
                "status": "waiting"
            }
        else:
            update_workflow_status(job_id, {f"{next_stage}_status": "completed"})
            _broadcast_state(job_id)

    logger.info(f"[confirm_and_continue] <<< 工作流全部完成")
    update_workflow_status(job_id, {
        "main_agent": {"status": "completed", "current_stage": "completed", "pending_confirmations": []}
    })
    _broadcast_state(job_id)
    return {
        "job_id": job_id,
        "current_stage": "completed",
        "pending_confirmations": [],
        "confirmed_stages": stage_order,
        "status": "completed"
    }


def _confirm_and_continue_async(job_id: str, stage: str, decision: str, notes: str, current_idx: int, stage_order: list):
    """后台执行工作流（供异步模式调用）

    注意：这是后台线程执行，不能直接返回结果到前端，只能通过 WebSocket 推送状态更新
    """
    try:
        logger.info(f"[_confirm_and_continue_async] >>> 后台执行开始: job_id={job_id}, stage={stage}")

        # P1 阶段特殊处理：HITL 中断恢复
        if stage.upper() == "P1" and is_agent_interrupted(job_id):
            logger.info(f"[_confirm_and_continue_async] P1 HITL 恢复执行")
            add_job_log(job_id, {
                "action": "p1_hitl_resume",
                "message": "P1 阶段 HITL 中断恢复执行"
            })
            p1_result = execute_p1(job_id, resume=True)

            if p1_result.get("pending_confirmation"):
                logger.info(f"[_confirm_and_continue_async] P1 恢复后仍等待确认")
                update_workflow_status(job_id, {
                    "main_agent": {"status": "waiting", "current_stage": "P1", "pending_confirmations": ["P1"]},
                    "P1_status": "waiting"
                })
                _broadcast_state(job_id)
                return

            # P1 恢复执行后完成，继续后续阶段
            current_idx = 0  # P1 已完成，从 P2 继续

        for i in range(current_idx + 1, len(stage_order)):
            next_stage = stage_order[i]
            executor = STAGE_EXECUTORS[next_stage]

            logger.info(f"[_confirm_and_continue_async] 继续执行下一阶段: {next_stage}")
            add_job_log(job_id, {
                "action": f"execute_{next_stage.lower()}",
                "message": f"继续执行 {next_stage}"
            })

            # 更新下一阶段状态为 running
            update_workflow_status(job_id, {
                "main_agent": {"status": "running", "current_stage": next_stage},
                f"{next_stage}_status": "running"
            })
            _broadcast_state(job_id)

            next_result = executor(job_id)

            if next_result.get("pending_confirmation"):
                update_workflow_status(job_id, {
                    f"{next_stage}_status": "waiting",
                    "main_agent": {"status": "waiting", "pending_confirmations": [next_stage]}
                })
                _broadcast_state(job_id)
                logger.info(f"[_confirm_and_continue_async] 下一阶段等待确认: {next_stage}")
                return
            else:
                update_workflow_status(job_id, {f"{next_stage}_status": "completed"})
                _broadcast_state(job_id)

        logger.info(f"[_confirm_and_continue_async] <<< 工作流全部完成")
        update_workflow_status(job_id, {
            "main_agent": {"status": "completed", "current_stage": "completed", "pending_confirmations": []}
        })
        _broadcast_state(job_id)

    except Exception as e:
        logger.exception(f"[_confirm_and_continue_async] 后台执行异常: job_id={job_id}, error={e}")
        update_workflow_status(job_id, {
            "main_agent": {"status": "error", "current_stage": stage, "pending_confirmations": []}
        })
        _broadcast_state(job_id)


def get_workflow_state(thread_id: str) -> dict:
    """获取工作流状态（兼容旧接口，同时返回新旧格式）"""
    # 尝试从新的统一状态文件获取
    status = get_workflow_status(thread_id)
    if status:
        # 转换为主流程兼容格式
        job_status = get_job_status(thread_id)
        agents_status = status.get("agents", {})
        confirmed = [f"P{i}" for i in range(1, 11) if agents_status.get(f"P{i}", {}).get("status") == "completed"]
        pending_stages = []
        for stage in ALL_STAGES:
            if agents_status.get(stage, {}).get("status") == "waiting":
                pending_stages.append(stage)
        return {
            "status": status.get("main_agent", {}).get("status", "unknown"),
            "current_stage": status.get("main_agent", {}).get("current_stage", ""),
            "pending_confirmations": [{"stage": p, "pending": {}} for p in pending_stages],
            "confirmed_stages": confirmed,
            "thread_id": thread_id,
            "agents": agents_status
        }
    return get_job_status(thread_id)


def list_pending_confirmations(thread_id: str) -> list:
    """列出待确认的阶段"""
    status = get_workflow_status(thread_id)
    if status:
        pending = []
        for stage in ALL_STAGES:
            if status.get("agents", {}).get(stage, {}).get("status") == "waiting":
                pending.append({"stage": stage, "pending": {}})
        return pending
    job_status = get_job_status(thread_id)
    return job_status.get("pending_confirmations", [])


# 全局广播回调（由 server.py 设置）
_broadcast_callback = None


def set_broadcast_callback(callback):
    """设置状态广播回调函数（供 server.py 调用）"""
    global _broadcast_callback
    _broadcast_callback = callback


def _broadcast_state(job_id: str):
    """内部广播函数，推送状态到 WebSocket"""
    if _broadcast_callback:
        try:
            _broadcast_callback(job_id)
        except Exception as e:
            logger.warning(f"广播状态失败: job_id={job_id}, error={e}")
