"""
P5: 作业前条件核验
Verify Agent - 核对隔离、警戒、消防、气体检测、人员资质和PPE
"""
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model.chat_model import create_chat_model_with_logging, get_llm_params
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.logging_handler import get_agent_config
from .utils import get_stage_logger

# 配置日志
import logging
logger = logging.getLogger("p5_verify_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# Agent 层级 Checkpointer - 用于 Agent 内部中断
# ============================================================
_verify_checkpointer = MemorySaver()


# ============================================================
# 工具定义
# ============================================================

@tool(description="生成作业前核验清单。当用户请求生成核验清单时触发。")
def verify_checklist(task_id: str) -> str:
    """
    按作业类型生成核验检查清单。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含核验清单
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
        "checklist_id": f"CL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "items": [
            {"item_id": "C-001", "description": "隔离措施落实", "category": "隔离", "required": True},
            {"item_id": "C-002", "description": "警戒标识设置", "category": "警戒", "required": True},
            {"item_id": "C-003", "description": "消防器材配备", "category": "消防", "required": True},
            {"item_id": "C-004", "description": "气体检测合格", "category": "气体检测", "required": True},
            {"item_id": "C-005", "description": "人员资质有效", "category": "人员资质", "required": True},
            {"item_id": "C-006", "description": "PPE配备齐全", "category": "PPE", "required": True}
        ]
    }

    return json.dumps(make_response("verify checklist", result), ensure_ascii=False)


@tool(description="执行作业前条件核验。当用户执行核验时触发。")
def verify_execute(task_id: str, checklist_id: Optional[str] = None) -> str:
    """
    执行作业前条件核验。

    参数:
        task_id: 任务唯一标识
        checklist_id: 核验清单ID（可选）
    返回:
        标准 JSON 响应，包含核验结果
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
        "checklist_id": checklist_id or f"CL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "checklist_results": [
            {
                "item": "隔离措施落实",
                "result": "PASS",
                "evidence": ["video_snippet_001"],
                "checked_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "item": "警戒标识设置",
                "result": "PASS",
                "evidence": [],
                "checked_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "item": "消防器材配备",
                "result": "PENDING",
                "evidence": [],
                "checked_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "item": "气体检测合格",
                "result": "FAIL",
                "evidence": ["sensor_data_GAS-001"],
                "checked_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "item": "人员资质有效",
                "result": "PASS",
                "evidence": ["cert_check_api"],
                "checked_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "item": "PPE配备齐全",
                "result": "PENDING",
                "evidence": [],
                "checked_at": datetime.now(timezone.utc).isoformat()
            }
        ],
        "pass_rate": "50%",
        "recommendation": "整改后开工"
    }

    return json.dumps(make_response("verify execute", result), ensure_ascii=False)


@tool(description="获取开工建议。当用户请求获取开工建议时触发。")
def verify_recommendation(task_id: str) -> str:
    """
    获取开工建议。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含开工建议
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
        "decision": "整改后开工",
        "reasons": [
            "气体检测不合格（GAS-001检测到可燃气体）",
            "消防器材配备待确认",
            "PPE配备待确认"
        ],
        "conditions": [
            "完成气体检测并合格",
            "确认消防器材可用",
            "确认PPE配备齐全"
        ]
    }

    return json.dumps(make_response("verify recommendation", result), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

def create_verify_agent():
    """创建 P5 作业前条件核验 Agent（基础版本，无 HITL）"""
    llm = create_chat_model_with_logging("P5")
    tools = [verify_checklist, verify_execute, verify_recommendation]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P5"))


def create_verify_agent_with_hitl():
    """创建 P5 作业前条件核验 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model_with_logging("P5")
    tools = [verify_checklist, verify_execute, verify_recommendation]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "verify_checklist": False,          # 查询清单自动批准
            "verify_execute": True,              # 执行核验需要确认
            "verify_recommendation": True,       # 开工建议需要确认
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P5"),
        middleware=[hitl_middleware],
        checkpointer=_verify_checkpointer,
    )


def run_verify_agent(message: str) -> str:
    """运行 P5 作业前条件核验 Agent"""
    agent = create_verify_agent()
    agent_config = get_agent_config("default", "P5", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def verify_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_verify_agent(message)


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(job_id: str) -> dict:
    """P5 阶段执行入口：开工前条件核验

    读取 P4 结果中的 task_id，执行条件核验
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger, add_job_log

    log = get_stage_logger("P5")
    log.log_enter(job_id)

    result = {
        "job_id": job_id,
        "stage": "P5",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 1. 读取前置阶段结果
        p4_result = read_json_file(get_stage_result_path(job_id, "p4"))
        task_id = p4_result.get("task_id", "")
        logger.info(f"[P5] task_id={task_id}")

        # 2. 调用本模块工具
        logger.info(f"[P5] 调用 verify_execute: task_id={task_id}")
        verify_result = json.loads(verify_execute.invoke(task_id))
        log.log_tool_call("verify_execute", {"task_id": task_id}, verify_result)
        if "result" in verify_result:
            result["verification_result"] = verify_result["result"]

        logger.info(f"[P5] 调用 verify_recommendation: task_id={task_id}")
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
