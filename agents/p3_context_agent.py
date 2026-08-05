"""
P3: 作业上下文理解与标准化
Context Agent - 聚合作业类型、区域、设备、介质等11维信息
支持 HumanInTheLoop - Agent 层级中断
"""
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

from model.chat_model import create_chat_model
from utils.agent_utils import extract_output
from .utils import make_response, make_error, SCHEMA_VERSION


# ============================================================
# Agent 层级 Checkpointer - 用于 Agent 内部中断
# ============================================================
_context_checkpointer = MemorySaver()


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个作业上下文理解专家，负责构建标准化的作业上下文包。

标准上下文包包含 11 个维度：
作业对象 — 区域 — 设备 — 介质 — 人员 — 资质 — 风险 — 措施 — 时间 — 关联作业 — 数据源

你需要：
1. 调用 context_build 聚合任务相关信息
2. 调用 context_validate 验证上下文完整性
3. 调用 context_history 获取上下文变更历史
4. 上下文不完整时提示人工补充

数据溯源：每项数据记录来源系统、获取时间、有效期
缺失确认：上下文不完整时暂停流程，等待人工补充"""


# ============================================================
# 工具定义
# ============================================================

@tool(description="构建标准作业上下文包。当用户请求构建上下文时触发。")
def context_build(task_id: str) -> str:
    """
    构建标准作业上下文包（11维）。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含完整的上下文包
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
        "context": {
            "work_type": "受限空间作业",
            "region": "炼油厂区01",
            "equipment": ["反应器R-101", "管道P-205"],
            "medium": "原油",
            "personnel": [
                {
                    "name": "张三",
                    "badge_id": "P-101",
                    "qualifications": ["受限空间作业证", "易燃易爆作业证"]
                }
            ],
            "qualifications": ["受限空间作业证", "易燃易爆作业证"],
            "risks": [
                {"id": "R-001", "description": "有毒有害气体", "severity": "高"},
                {"id": "R-002", "description": "高温烫伤", "severity": "中"}
            ],
            "measures": [
                {"id": "M-001", "description": "气体检测", "status": "已落实"},
                {"id": "M-002", "description": "强制通风", "status": "已落实"},
                {"id": "M-003", "description": "佩戴呼吸器", "status": "待确认"}
            ],
            "time_range": {
                "start": "2026-08-04T09:00:00Z",
                "end": "2026-08-04T17:00:00Z"
            },
            "related_tasks": [],
            "data_sources": {
                "work_permit": "permit-system-v2",
                "personnel": "hr-system-v3",
                "equipment": "equipment-db-v1"
            },
            "valid_until": "2026-08-04T17:00:00Z"
        },
        "completeness": "95%",
        "missing_fields": ["related_tasks"]
    }

    return json.dumps(make_response("context build", result), ensure_ascii=False)


@tool(description="验证上下文完整性。当用户请求验证上下文时触发。")
def context_validate(task_id: str) -> str:
    """
    验证上下文完整性。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含验证结果
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
        "valid": True,
        "completeness": "95%",
        "missing_fields": ["related_tasks"],
        "requires_manual_confirmation": True
    }

    return json.dumps(make_response("context validate", result), ensure_ascii=False)


@tool(description="获取上下文变更历史。当用户请求查看上下文历史时触发。")
def context_history(task_id: str) -> str:
    """
    获取上下文变更历史。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含历史版本列表
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    now = datetime.now(timezone.utc).isoformat()
    result = [
        {
            "version": 1,
            "context": {"work_type": "受限空间作业", "region": "炼油厂区01"},
            "changed_at": now,
            "changed_by": "system"
        },
        {
            "version": 2,
            "context": {"work_type": "受限空间作业", "region": "炼油厂区01",
                      "equipment": ["反应器R-101"]},
            "changed_at": now,
            "changed_by": "system"
        }
    ]

    return json.dumps(make_response("context history", result), ensure_ascii=False)


# ============================================================
# Agent 工厂 (HITL Enabled)
# ============================================================

def create_context_agent():
    """创建 P3 上下文理解 Agent（基础版本，无 HITL）"""
    llm = create_chat_model()
    tools = [context_build, context_validate, context_history]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def create_context_agent_with_hitl():
    """创建 P3 上下文理解 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model()
    tools = [context_build, context_validate, context_history]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "context_build": True,       # 构建上下文需要确认
            "context_validate": True,     # 验证需要确认
            "context_history": False,     # 查询历史自动批准
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[hitl_middleware],
        checkpointer=_context_checkpointer,
    )


def run_context_agent(message: str) -> str:
    """运行 P3 上下文理解 Agent"""
    agent = create_context_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def context_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_context_agent(message)
