"""
P8: 人机协同处置
Disposition Agent - 按角色与权限推送责任人，形成整改、暂停、复核或升级建议
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
logger = logging.getLogger("p8_disposition_agent")
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
_disposition_checkpointer = MemorySaver()


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个人机协同处置专家，负责创建和跟踪处置任务。

推送策略：
- LOW: 消息推送，24h内处理
- MEDIUM: 短信+消息，4h内处理
- HIGH: 电话+短信+消息，1h内处理
- CRITICAL: 电话直达，实时处理

人工控制点：
- 高风险事件：必须人工确认
- 写入操作：必须人工确认
- 暂停作业：必须人工确认
- 恢复作业：必须人工确认

操作类型：approve（批准）/ reject（否决）/ escalate（升级）/ rectify（整改）/ pause（暂停）/ resume（恢复）

当用户创建处置任务时，调用 disposition_create 工具。
当用户确认处置时，调用 disposition_confirm 工具。
当用户查看处置状态时，调用 disposition_status 工具。
当用户列出处置任务时，调用 disposition_list 工具。"""


# ============================================================
# 工具定义
# ============================================================

# 模拟处置任务存储
_MOCK_DISPOSITIONS = {}


@tool(description="创建处置任务。当用户创建处置任务时触发。")
def disposition_create(risk_event_id: str) -> str:
    """
    创建处置任务。

    参数:
        risk_event_id: 风险事件ID
    返回:
        标准 JSON 响应，包含处置任务信息
    """
    import json
    from datetime import datetime, timezone

    if not risk_event_id:
        return json.dumps(make_error(
            code="RISK_EVENT_NOT_FOUND",
            message="risk_event_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    disposition_task_id = f"DT-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 根据风险等级确定推送策略
    push_strategy = "phone+sms+message"
    due_hours = 1

    result = {
        "disposition_task_id": disposition_task_id,
        "risk_event_id": risk_event_id,
        "assignee": "属地责任人-张三",
        "action_type": "rectify",
        "status": "pending",
        "due_at": datetime.now(timezone.utc).isoformat(),
        "push_strategy": push_strategy
    }

    _MOCK_DISPOSITIONS[disposition_task_id] = result

    return json.dumps(make_response("disposition create", result), ensure_ascii=False)


@tool(description="人工确认处置任务。当用户确认处置时触发。")
def disposition_confirm(disposition_task_id: str, action: str) -> str:
    """
    人工确认处置任务。

    参数:
        disposition_task_id: 处置任务ID
        action: 操作类型（approve/reject/escalate/rectify/pause/resume）
    返回:
        标准 JSON 响应，包含确认信息
    """
    import json
    from datetime import datetime, timezone

    if not disposition_task_id:
        return json.dumps(make_error(
            code="DISPOSITION_NOT_FOUND",
            message="disposition_task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    valid_actions = ("approve", "reject", "escalate", "rectify", "pause", "resume")
    if action not in valid_actions:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"无效的 action: {action}",
            recoverable=False
        ), ensure_ascii=False)

    # 高风险操作需要人工确认
    if action in ("pause", "resume"):
        return json.dumps(make_error(
            code="DISPOSITION_HUMAN_CONFIRM_REQUIRED",
            message=f"Action {action} requires human confirmation",
            recoverable=True,
            action="请确认是否执行此操作"
        ), ensure_ascii=False)

    result = {
        "disposition_task_id": disposition_task_id,
        "action": action,
        "confirmed_by": "operator",
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "notes": None
    }

    # 更新状态
    if disposition_task_id in _MOCK_DISPOSITIONS:
        _MOCK_DISPOSITIONS[disposition_task_id]["status"] = "confirmed"

    return json.dumps(make_response("disposition confirm", result), ensure_ascii=False)


@tool(description="查看处置状态。当用户查看处置状态时触发。")
def disposition_status(disposition_task_id: str) -> str:
    """
    查看处置状态。

    参数:
        disposition_task_id: 处置任务ID
    返回:
        标准 JSON 响应，包含处置状态
    """
    import json
    from datetime import datetime, timezone

    if not disposition_task_id:
        return json.dumps(make_error(
            code="DISPOSITION_NOT_FOUND",
            message="disposition_task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # 模拟数据
    result = {
        "disposition_task_id": disposition_task_id,
        "status": "pending",
        "assignee": "属地责任人-张三",
        "action_type": "rectify",
        "due_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None
    }

    if disposition_task_id in _MOCK_DISPOSITIONS:
        result.update(_MOCK_DISPOSITIONS[disposition_task_id])

    return json.dumps(make_response("disposition status", result), ensure_ascii=False)


@tool(description="列出任务所有处置任务。当用户列出处置任务时触发。")
def disposition_list(task_id: str) -> str:
    """
    列出任务所有处置任务。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含处置任务列表
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = [
        {
            "disposition_task_id": f"DT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "risk_event_id": f"RE-{datetime.now().strftime('%Y%m%d')}-001",
            "level": "HIGH",
            "status": "pending",
            "assignee": "属地责任人-张三",
            "due_at": datetime.now(timezone.utc).isoformat()
        }
    ]

    return json.dumps(make_response("disposition list", result), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

def create_disposition_agent():
    """创建 P8 人机协同处置 Agent（基础版本，无 HITL）"""
    llm = create_chat_model_with_logging("P8")
    tools = [disposition_create, disposition_confirm, disposition_status, disposition_list]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P8"))


def create_disposition_agent_with_hitl():
    """创建 P8 人机协同处置 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model_with_logging("P8")
    tools = [disposition_create, disposition_confirm, disposition_status, disposition_list]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "disposition_create": True,         # 创建处置任务需要确认
            "disposition_confirm": True,        # 确认处置需要确认
            "disposition_status": False,        # 查询状态自动批准
            "disposition_list": False,          # 查询列表自动批准
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P8"),
        middleware=[hitl_middleware],
        checkpointer=_disposition_checkpointer,
    )


def run_disposition_agent(message: str) -> str:
    """运行 P8 人机协同处置 Agent"""
    agent = create_disposition_agent()
    agent_config = get_agent_config("default", "P8", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def disposition_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_disposition_agent(message)


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(job_id: str) -> dict:
    """P8 阶段执行入口：人机协同处置

    读取 P7 结果中的 risk_events，创建处置任务
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger, add_job_log

    log = get_stage_logger("P8")
    log.log_enter(job_id)

    result = {
        "job_id": job_id,
        "stage": "P8",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 1. 读取前置阶段结果
        p7_result = read_json_file(get_stage_result_path(job_id, "p7"))
        risk_events = p7_result.get("risk_events", [])
        logger.info(f"[P8] risk_events_count={len(risk_events)}")

        # 2. 遍历风险事件创建处置任务
        disposition_tasks = []
        for event in risk_events:
            event_id = event.get("event_id", "")
            if event_id:
                logger.info(f"[P8] 调用 disposition_create: event_id={event_id}")
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
