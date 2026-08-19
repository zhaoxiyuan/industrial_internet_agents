"""
主 Agent - P1-P10 调度管理中心
通过文件传递协调各阶段 Agent 执行
支持 HumanInTheLoop 中断恢复
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import TypedDict, Optional, Any, List
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from .model.chat_model import create_chat_model_with_logging
from .model.config import get_llm_params
from .utils import extract_output, get_stage_logger, push_websocket_log
from .utils.logging_handler import get_agent_config

# 导入 Workflow 模块
from .workflow import (
    get_job_dir, ensure_job_dir, get_stage_result_path,
    read_json_file, write_json_file,
    init_workflow_status, update_workflow_status, get_workflow_status,
    ALL_STAGES,
    save_job_application, add_job_log, save_confirmation, get_job_status,
)
# 导入工具响应和 Prompt 加载器
from .utils import make_response, make_error, load_system_prompt

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

# 导入各子Agent的阶段执行入口
from .p1_permit_agent import execute_stage as p1_execute_stage, is_agent_interrupted
from .p2_task_agent import execute_stage as p2_execute_stage
from .p3_context_agent import execute_stage as p3_execute_stage
from .p4_binding_agent import execute_stage as p4_execute_stage
from .p5_verify_agent import execute_stage as p5_execute_stage
from .p6_monitor_agent import execute_stage as p6_execute_stage
from .p7_risk_agent import execute_stage as p7_execute_stage
from .p8_disposition_agent import execute_stage as p8_execute_stage
from .p9_closure_agent import execute_stage as p9_execute_stage
from .p10_archive_agent import execute_stage as p10_execute_stage

# P1 特殊处理：保留 HITL 逻辑
execute_p1 = p1_execute_stage

# 阶段执行映射（从子Agent导入）
STAGE_EXECUTORS = {
    "P1": p1_execute_stage,
    "P2": p2_execute_stage,
    "P3": p3_execute_stage,
    "P4": p4_execute_stage,
    "P5": p5_execute_stage,
    "P6": p6_execute_stage,
    "P7": p7_execute_stage,
    "P8": p8_execute_stage,
    "P9": p9_execute_stage,
    "P10": p10_execute_stage,
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
    llm = create_chat_model_with_logging("MAIN")
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
    agent_config = get_agent_config(thread_id, "MAIN", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
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

        try:
            result = executor(job_id)
        except Exception as e:
            logger.exception(f"[run_workflow] 阶段 {stage_name} 执行异常: job_id={job_id}")
            update_workflow_status(job_id, {
                f"{stage_name}_status": "error",
                "main_agent": {"status": "error", "current_stage": stage_name, "error": str(e)}
            })
            _broadcast_state(job_id)
            add_job_log(job_id, {
                "action": "stage_error",
                "stage": stage_name,
                "error": str(e),
                "message": f"{stage_name} 执行异常: {str(e)}"
            })
            return {
                "job_id": job_id,
                "current_stage": stage_name,
                "status": "error",
                "error": str(e),
                "confirmed_stages": list(STAGE_EXECUTORS.keys())[:list(STAGE_EXECUTORS.keys()).index(stage_name)]
            }

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
        try:
            p1_result = execute_p1(job_id, resume=True)
        except Exception as e:
            logger.exception(f"[confirm_and_continue] P1 恢复执行异常: job_id={job_id}")
            update_workflow_status(job_id, {
                "P1_status": "error",
                "main_agent": {"status": "error", "current_stage": "P1", "error": str(e)}
            })
            _broadcast_state(job_id)
            add_job_log(job_id, {
                "action": "stage_error",
                "stage": "P1",
                "error": str(e),
                "message": f"P1 恢复执行异常: {str(e)}"
            })
            return {
                "job_id": job_id,
                "current_stage": "P1",
                "status": "error",
                "error": str(e),
                "confirmed_stages": [stage.upper()]
            }

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

        try:
            next_result = executor(job_id)
        except Exception as e:
            logger.exception(f"[confirm_and_continue] 阶段 {next_stage} 执行异常: job_id={job_id}")
            update_workflow_status(job_id, {
                f"{next_stage}_status": "error",
                "main_agent": {"status": "error", "current_stage": next_stage, "error": str(e)}
            })
            _broadcast_state(job_id)
            add_job_log(job_id, {
                "action": "stage_error",
                "stage": next_stage,
                "error": str(e),
                "message": f"{next_stage} 执行异常: {str(e)}"
            })
            return {
                "job_id": job_id,
                "current_stage": next_stage,
                "status": "error",
                "error": str(e),
                "confirmed_stages": stage_order[:i]
            }

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
            try:
                p1_result = execute_p1(job_id, resume=True)
            except Exception as e:
                logger.exception(f"[_confirm_and_continue_async] P1 恢复执行异常: job_id={job_id}")
                update_workflow_status(job_id, {
                    "P1_status": "error",
                    "main_agent": {"status": "error", "current_stage": "P1", "error": str(e)}
                })
                _broadcast_state(job_id)
                add_job_log(job_id, {
                    "action": "stage_error",
                    "stage": "P1",
                    "error": str(e),
                    "message": f"P1 恢复执行异常: {str(e)}"
                })
                return

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

            try:
                next_result = executor(job_id)
            except Exception as e:
                logger.exception(f"[_confirm_and_continue_async] 阶段 {next_stage} 执行异常: job_id={job_id}")
                update_workflow_status(job_id, {
                    f"{next_stage}_status": "error",
                    "main_agent": {"status": "error", "current_stage": next_stage, "error": str(e)}
                })
                _broadcast_state(job_id)
                add_job_log(job_id, {
                    "action": "stage_error",
                    "stage": next_stage,
                    "error": str(e),
                    "message": f"{next_stage} 执行异常: {str(e)}"
                })
                return

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
