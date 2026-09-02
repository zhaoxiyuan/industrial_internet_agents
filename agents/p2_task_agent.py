"""
P2: 作业任务获取与实例化
Task Agent - 处理任务列表、详情、订阅和实例创建
支持 HumanInTheLoop - Agent 层级中断
"""
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model.chat_model import create_chat_model_with_logging
from .model.config import get_llm_params
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.logging_handler import get_agent_config
from .utils.system_prompt import load_system_prompt

# 配置日志
import logging
logger = logging.getLogger("p2_task_agent")
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
_task_checkpointer = MemorySaver()


# ============================================================
# 工具定义
# ============================================================

# 模拟任务存储
_MOCK_TASKS = {}


@tool(description="列出作业任务，支持按区域和状态筛选。当用户请求列出任务时触发。")
def task_list(region: Optional[str] = None, status: Optional[str] = None) -> str:
    """
    列出作业任务。

    参数:
        region: 区域代码筛选（可选）
        status: 状态筛选（pending/running/closed，可选）
    返回:
        标准 JSON 响应，包含任务列表
    """
    import json
    from datetime import datetime, timezone

    if status and status not in ("pending", "running", "closed"):
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"无效的 status 值: {status}",
            recoverable=False
        ), ensure_ascii=False)

    # 模拟任务列表
    mock_tasks = [
        {
            "task_id": "TASK-WELD-01-20260804-001",
            "permit_id": "PD-20260804001",
            "job_content": "受限空间作业",
            "region": "炼油厂区01",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "task_id": "TASK-HIGH-02-20260804-002",
            "permit_id": "PD-20260804002",
            "job_content": "高空作业",
            "region": "炼油厂区02",
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    ]

    # 过滤
    filtered = mock_tasks
    if region:
        filtered = [t for t in filtered if region in t["region"]]
    if status:
        filtered = [t for t in filtered if t["status"] == status]

    result = {
        "tasks": filtered,
        "total": len(filtered),
        "filtered_by": {"region": region, "status": status}
    }

    return json.dumps(make_response("task list", result), ensure_ascii=False)


@tool(description="获取任务详情。当用户请求查看任务详情时触发。")
def task_get(task_id: str) -> str:
    """
    获取任务详情。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含任务详情
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # 模拟任务详情
    result = {
        "task_id": task_id,
        "permit_id": f"PD-{task_id.split('-')[-1]}",
        "job_content": "受限空间作业",
        "region": "炼油厂区01",
        "status": "pending",
        "context": {
            "job_content": "受限空间作业",
            "region": "炼油厂区01",
            "equipment": ["反应器R-101"],
            "medium": "原油"
        },
        "resources": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    return json.dumps(make_response("task get", result), ensure_ascii=False)


@tool(description="创建任务实例，幂等操作。当用户请求创建任务实例时触发。")
def task_instance_create(permit_id: str) -> str:
    """
    创建任务实例（幂等操作）。

    参数:
        permit_id: 作业票ID
    返回:
        标准 JSON 响应，包含 task_id, status, idempotent 标志
    """
    import json
    from datetime import datetime, timezone

    if not permit_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="permit_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # 检查是否已存在（幂等性）
    existing = _MOCK_TASKS.get(permit_id)
    if existing:
        result = {
            "task_id": existing["task_id"],
            "permit_id": permit_id,
            "status": existing["status"],
            "created_at": existing["created_at"],
            "idempotent": True
        }
        errors = [make_error(
            code="TASK_DUPLICATE",
            message=f"Task already exists for permit {permit_id}",
            recoverable=True
        )]
        return json.dumps({
            "schema_version": SCHEMA_VERSION,
            "command": "task instance-create",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result": result,
            "errors": errors
        }, ensure_ascii=False)

    # 创建新实例
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_id = f"TASK-{permit_id.replace('PD-', '')}-{timestamp}"
    created_at = datetime.now(timezone.utc).isoformat()

    _MOCK_TASKS[permit_id] = {
        "task_id": task_id,
        "permit_id": permit_id,
        "status": "pending",
        "created_at": created_at
    }

    result = {
        "task_id": task_id,
        "permit_id": permit_id,
        "status": "pending",
        "created_at": created_at,
        "idempotent": False
    }

    return json.dumps(make_response("task instance-create", result), ensure_ascii=False)


@tool(description="订阅任务变更事件。当用户请求订阅任务变更时触发。")
def task_subscribe(task_id: str) -> str:
    """
    订阅任务变更事件。

    参数:
        task_id: 任务唯一标识
    返回:
        JSON Lines 格式的事件流
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # 模拟订阅事件流
    events = [
        {
            "event_type": "created",
            "task_id": task_id,
            "data": {"status": "pending"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    ]

    # 输出 JSON Lines
    output = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    return output


# ============================================================
# Agent 工厂 (HITL Enabled)
# ============================================================

def create_task_agent():
    """创建 P2 作业任务 Agent（基础版本，无 HITL）"""
    llm = create_chat_model_with_logging("P2")
    tools = [task_list, task_get, task_instance_create, task_subscribe]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P2"))


def create_task_agent_with_hitl():
    """创建 P2 作业任务 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model_with_logging("P2")
    tools = [task_list, task_get, task_instance_create, task_subscribe]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "task_list": False,              # 查询自动批准
            "task_get": False,               # 查询自动批准
            "task_instance_create": True,     # 创建需要确认
            "task_subscribe": False,          # 订阅自动批准
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P2"),
        middleware=[hitl_middleware],
        checkpointer=_task_checkpointer,
    )


def run_task_agent(message: str) -> str:
    """运行 P2 作业任务 Agent"""
    agent = create_task_agent()
    agent_config = get_agent_config("default", "P2", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def task_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_task_agent(message)


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(job_id: str) -> dict:
    """P2 阶段执行入口：作业任务实例化

    读取 P1 结果中的 permit_draft_id，创建任务实例
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger, add_job_log

    log = get_stage_logger("P2")
    log.log_enter(job_id)

    result = {
        "job_id": job_id,
        "stage": "P2",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 1. 读取前置阶段结果
        p1_result = read_json_file(get_stage_result_path(job_id, "p1"))
        permit_draft_id = p1_result.get("permit_draft_id", "")
        logger.info(f"[P2] permit_draft_id={permit_draft_id}")

        # 2. 调用本模块工具
        logger.info(f"[P2] 调用 task_instance_create: permit_draft_id={permit_draft_id}")
        task_result = json.loads(task_instance_create.invoke(permit_draft_id))
        log.log_tool_call("task_instance_create", {"permit_draft_id": permit_draft_id}, task_result)

        # 3. 提取结果
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
