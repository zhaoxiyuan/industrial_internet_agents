"""
P2: 作业任务获取与实例化
Task Agent - 处理任务列表、详情、订阅和实例创建
"""
from typing import Optional, List
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from model.chat_model import create_chat_model
from utils.agent_utils import extract_output
from .utils import make_response, make_error, SCHEMA_VERSION


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个作业任务管理专家，负责处理作业任务的获取、列表查询和实例创建。

你需要：
1. 按时间、区域、状态筛选和列出作业任务
2. 获取任务详情
3. 创建唯一任务实例，确保幂等性
4. 订阅任务变更事件

Task ID 格式: {作业类型}_{区域代码}_{时间戳}_{序号}

当用户列出任务时，调用 task_list 工具。
当用户获取任务详情时，调用 task_get 工具。
当用户创建任务实例时，调用 task_instance_create 工具。
当用户订阅任务变更时，调用 task_subscribe 工具。"""


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
            "work_type": "受限空间作业",
            "region": "炼油厂区01",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "task_id": "TASK-HIGH-02-20260804-002",
            "permit_id": "PD-20260804002",
            "work_type": "高空作业",
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
        "work_type": "受限空间作业",
        "region": "炼油厂区01",
        "status": "pending",
        "context": {
            "work_type": "受限空间作业",
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
# Agent 工厂
# ============================================================

def create_task_agent():
    """创建 P2 作业任务 Agent"""
    llm = create_chat_model()
    tools = [task_list, task_get, task_instance_create, task_subscribe]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_task_agent(message: str) -> str:
    """运行 P2 作业任务 Agent"""
    agent = create_task_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def task_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_task_agent(message)
