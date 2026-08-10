"""
P10: 归档与复盘
Archive Agent - 归档票证、视频证据、风险事件、处置记录和报告
"""
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model.chat_model import create_chat_model
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION


# ============================================================
# Agent 层级 Checkpointer - 用于 Agent 内部中断
# ============================================================
_archive_checkpointer = MemorySaver()


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个归档与复盘专家，负责归档全过程记录并挖掘误报、漏报、规则冲突案例。

归档内容：
- 作业票证（结构化 + 扫描件）
- 视频证据片段
- 风险事件记录
- 处置全记录
- 作业报告

案例挖掘类型：
- misdetection: 检测模型误报
- missed: 人工发现的风险事件
- rule_conflict: 同场景不同规则的冲突点

知识沉淀：
- 案例摘要Embedding → 向量数据库
- 规则冲突报告 → 规则管理系统
- 模型优化建议 → 模型训练平台

当用户归档任务数据时，调用 archive_task 工具。
当用户挖掘案例时，调用 archive_cases 工具。
当用户分析处置效果时，调用 archive_performance 工具。
当用户生成规则优化建议时，调用 archive_suggestions 工具。"""


# ============================================================
# 工具定义
# ============================================================

@tool(description="归档任务数据。当用户归档任务时触发。")
def archive_task(task_id: str) -> str:
    """
    归档任务数据。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含归档信息
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
        "archive_id": f"ARC-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "contents": [
            "作业票证",
            "视频证据片段",
            "风险事件记录",
            "处置全记录",
            "作业报告"
        ],
        "status": "archived"
    }

    return json.dumps(make_response("archive task", result), ensure_ascii=False)


@tool(description="挖掘误报、漏报、规则冲突案例。当用户挖掘案例时触发。")
def archive_cases(task_id: str, case_type: Optional[str] = None) -> str:
    """
    挖掘案例。

    参数:
        task_id: 任务唯一标识
        case_type: 案例类型（misdetection/missed/rule_conflict/all），可选
    返回:
        标准 JSON 响应，包含案例列表
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    all_cases = [
        {
            "case_id": f"CASE-{datetime.now().strftime('%Y%m%d')}-001",
            "type": "misdetection",
            "description": "CV模型误将管道反光识别为未佩戴安全帽",
            "evidence": ["clip_误报001.mp4"],
            "mined_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "case_id": f"CASE-{datetime.now().strftime('%Y%m%d')}-002",
            "type": "missed",
            "description": "人工发现作业区域温度异常但传感器未报警",
            "evidence": ["人工记录表"],
            "mined_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "case_id": f"CASE-{datetime.now().strftime('%Y%m%d')}-003",
            "type": "rule_conflict",
            "description": "GB标准和企标在受限空间作业时间限制上存在冲突",
            "evidence": ["GBXXXX-X", "企标-QHSE-003"],
            "mined_at": datetime.now(timezone.utc).isoformat()
        }
    ]

    if case_type and case_type != "all":
        all_cases = [c for c in all_cases if c["type"] == case_type]

    return json.dumps(make_response("archive cases", all_cases), ensure_ascii=False)


@tool(description="分析处置效果。当用户分析处置效果时触发。")
def archive_performance(task_id: str) -> str:
    """
    分析处置效果。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含性能指标
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
        "metrics": {
            "detection_rate": 0.92,
            "false_positive_rate": 0.08,
            "avg_response_time_seconds": 180.5,
            "closure_time_hours": 4.2
        }
    }

    return json.dumps(make_response("archive performance", result), ensure_ascii=False)


@tool(description="生成规则优化建议。当用户生成优化建议时触发。")
def archive_suggestions(task_id: str) -> str:
    """
    生成规则优化建议。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含优化建议列表
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
            "rule_id": "R-001",
            "type": "rule_modify",
            "description": "建议增加受限空间作业气体检测频次，从每30分钟一次改为每15分钟一次",
            "priority": "high"
        },
        {
            "rule_id": None,
            "type": "model_update",
            "description": "建议优化CV模型，减少管道反光误报",
            "priority": "medium"
        },
        {
            "rule_id": "R-002",
            "type": "rule_add",
            "description": "建议新增规则：高空作业前必须确认安全带挂点",
            "priority": "medium"
        }
    ]

    return json.dumps(make_response("archive suggestions", result), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

def create_archive_agent():
    """创建 P10 归档复盘 Agent（基础版本，无 HITL）"""
    llm = create_chat_model()
    tools = [archive_task, archive_cases, archive_performance, archive_suggestions]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P10"))


def create_archive_agent_with_hitl():
    """创建 P10 归档复盘 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model()
    tools = [archive_task, archive_cases, archive_performance, archive_suggestions]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "archive_task": True,                # 归档任务需要确认
            "archive_cases": False,              # 挖掘案例自动批准
            "archive_performance": False,        # 分析性能自动批准
            "archive_suggestions": True,         # 生成建议需要确认
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P10"),
        middleware=[hitl_middleware],
        checkpointer=_archive_checkpointer,
    )


def run_archive_agent(message: str) -> str:
    """运行 P10 归档复盘 Agent"""
    agent = create_archive_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def archive_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_archive_agent(message)
