"""
P9: 闭环跟踪与报告
Closure Agent - 跟踪整改状态，复核处置结果，汇总全过程记录
"""
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from .model.chat_model import create_chat_model_with_logging, get_llm_params
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.logging_handler import get_agent_config
from .utils import get_stage_logger
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

# 配置日志
import logging
logger = logging.getLogger("p9_closure_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个闭环跟踪与报告专家，负责跟踪整改状态、复核处置结果并生成作业过程报告。

完整性检查项：
- 处置记录完整性
- 证据材料齐全性
- 复核签字有效性
- 时间线连续性

报告内容：
- 作业基本信息
- 核验结果汇总
- 监测事件时间线
- 风险处置记录
- 证据索引

当用户跟踪闭环状态时，调用 closure_status 工具。
当用户执行完整性检查时，调用 closure_verify 工具。
当用户生成作业报告时，调用 closure_report 工具。
当用户关闭事件和作业时，调用 closure_close 工具。

closure close 需要人工确认。"""


# ============================================================
# 工具定义
# ============================================================

@tool(description="跟踪闭环状态。当用户跟踪闭环状态时触发。")
def closure_status(task_id: str) -> str:
    """
    跟踪闭环状态。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含闭环状态
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "task_id": task_id,
        "closure_status": "in_progress",
        "steps": {
            "task_received": True,
            "rectification_completed": True,
            "feedback_submitted": False,
            "review_confirmed": False
        }
    }

    return json.dumps(make_response("closure status", result), ensure_ascii=False)


@tool(description="执行闭环完整性检查。当用户执行完整性检查时触发。")
def closure_verify(task_id: str) -> str:
    """
    执行闭环完整性检查。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含检查结果
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "task_id": task_id,
        "complete": False,
        "checks": [
            {"name": "处置记录完整性", "passed": True, "details": "所有处置记录完整"},
            {"name": "证据材料齐全性", "passed": True, "details": "视频证据完整"},
            {"name": "复核签字有效性", "passed": False, "details": "缺少复核签字"},
            {"name": "时间线连续性", "passed": True, "details": "时间线连续"}
        ],
        "blocked_by": ["复核签字"]
    }

    return json.dumps(make_response("closure verify", result), ensure_ascii=False)


@tool(description="生成作业过程报告。当用户生成报告时触发。")
def closure_report(task_id: str, format: Optional[str] = "markdown") -> str:
    """
    生成作业过程报告。

    参数:
        task_id: 任务唯一标识
        format: 报告格式（json/markdown/pdf），默认 markdown
    返回:
        标准 JSON 响应，包含报告内容
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    if format == "json":
        content = {
            "task_id": task_id,
            "basic_info": {
                "job_content": "受限空间作业",
                "region": "炼油厂区01",
                "start_time": "2026-08-04T09:00:00Z",
                "end_time": "2026-08-04T17:00:00Z"
            },
            "verification_summary": {"total": 6, "passed": 3, "pending": 2, "failed": 1},
            "events": [],
            "dispositions": []
        }
    else:
        content = f"""# 作业过程报告

## 基本信息
- 任务ID: {task_id}
- 作业类型: 受限空间作业
- 区域: 炼油厂区01
- 开始时间: 2026-08-04T09:00:00Z
- 结束时间: 2026-08-04T17:00:00Z

## 核验结果汇总
- 总计: 6项
- 通过: 3项
- 待确认: 2项
- 不符合: 1项

## 风险事件
无

## 处置记录
无
"""

    result = {
        "task_id": task_id,
        "report_id": f"REP-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "format": format,
        "content": content,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    return json.dumps(make_response("closure report", result), ensure_ascii=False)


@tool(description="关闭事件和作业（需人工确认）。当用户关闭事件和作业时触发。")
def closure_close(task_id: str) -> str:
    """
    关闭事件和作业（需人工确认）。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含关闭信息
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "task_id": task_id,
        "closed": False,
        "closed_by": None,
        "closed_at": None,
        "requires_human_confirm": True
    }

    return json.dumps(make_response("closure close", result), ensure_ascii=False)


# ============================================================
# Agent 层级 Checkpointer - 用于 Agent 内部中断
# ============================================================
_closure_checkpointer = MemorySaver()


# ============================================================
# Agent 工厂
# ============================================================

def create_closure_agent():
    """创建 P9 闭环跟踪 Agent（基础版本，无 HITL）"""
    llm = create_chat_model_with_logging("P9")
    tools = [closure_status, closure_verify, closure_report, closure_close]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P9"))


def create_closure_agent_with_hitl():
    """创建 P9 闭环跟踪 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model_with_logging("P9")
    tools = [closure_status, closure_verify, closure_report, closure_close]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "closure_status": False,         # 查询状态自动批准
            "closure_verify": True,          # 验证需要确认
            "closure_report": True,          # 生成报告需要确认
            "closure_close": True,           # 关闭作业需要确认
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P9"),
        middleware=[hitl_middleware],
        checkpointer=_closure_checkpointer,
    )


def run_closure_agent(message: str) -> str:
    """运行 P9 闭环跟踪 Agent"""
    agent = create_closure_agent()
    agent_config = get_agent_config("default", "P9", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def closure_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_closure_agent(message)


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(job_id: str) -> dict:
    """P9 阶段执行入口：闭环跟踪与报告

    读取 P8 结果中的 task_id，执行闭环跟踪
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger, add_job_log

    log = get_stage_logger("P9")
    log.log_enter(job_id)

    result = {
        "job_id": job_id,
        "stage": "P9",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 1. 读取前置阶段结果
        p8_result = read_json_file(get_stage_result_path(job_id, "p8"))
        task_id = p8_result.get("task_id", "")
        logger.info(f"[P9] task_id={task_id}")

        # 2. 调用本模块工具
        logger.info(f"[P9] 调用 closure_status: task_id={task_id}")
        status_result = json.loads(closure_status.invoke(task_id))
        log.log_tool_call("closure_status", {"task_id": task_id}, status_result)
        if "result" in status_result:
            result["closure_status"] = status_result["result"]

        logger.info(f"[P9] 调用 closure_verify: task_id={task_id}")
        verify_result = json.loads(closure_verify.invoke(task_id))
        log.log_tool_call("closure_verify", {"task_id": task_id}, verify_result)
        if "result" in verify_result:
            result["verify_result"] = verify_result["result"]

        logger.info(f"[P9] 调用 closure_report: task_id={task_id}")
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
