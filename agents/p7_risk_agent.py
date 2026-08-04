"""
P7: 风险研判与分级
Risk Agent - 融合上下文、模型结果、规则和历史事件，去重并判级
"""
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from model.chat_model import create_chat_model
from utils.agent_utils import extract_output
from .utils import make_response, make_error, SCHEMA_VERSION


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个风险研判与分级专家，负责综合研判候选风险事件并确定风险等级。

风险等级定义：
- LOW: 低风险（绿色）
- MEDIUM: 中风险（黄色）
- HIGH: 高风险（橙色）
- CRITICAL: 重大风险（红色）

研判流程：
1. 多源证据融合（视频 + 传感器 + 定位 + 规则）
2. 企业风险规则库匹配
3. Embedding 相似度搜索相似历史事件
4. 规则 + 模型 + 历史综合评分

当用户综合研判候选事件时，调用 risk_analyze 工具。
当用户计算风险等级时，调用 risk_grade 工具。
当用户查询相似案例时，调用 risk_cases 工具。
当用户列出任务所有风险事件时，调用 risk_list 工具。"""


# ============================================================
# 工具定义
# ============================================================

@tool(description="综合研判候选事件。当用户请求分析风险时触发。")
def risk_analyze(candidate_event_id: str) -> str:
    """
    综合研判候选风险事件。

    参数:
        candidate_event_id: 候选事件ID
    返回:
        标准 JSON 响应，包含风险事件详情
    """
    import json
    from datetime import datetime, timezone

    if not candidate_event_id:
        return json.dumps(make_error(
            code="RISK_EVENT_NOT_FOUND",
            message="candidate_event_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "event_id": candidate_event_id,
        "description": "人员进入受限空间未佩戴呼吸器",
        "facts": [
            "视频画面显示人员A进入R-101",
            "未检测到呼吸器佩戴"
        ],
        "evidence": [
            {"type": "video", "timestamp": datetime.now(timezone.utc).isoformat(),
             "clip": "clip_001.mp4"}
        ],
        "rule_bases": ["GBXXXX-X 5.2.3", "企业安规-受限空间篇 3.1"],
        "confidence": 0.95,
        "level": "HIGH",
        "suggestion": "立即整改"
    }

    return json.dumps(make_response("risk analyze", result), ensure_ascii=False)


@tool(description="计算风险等级。当用户请求计算风险等级时触发。")
def risk_grade(risk_event_id: str) -> str:
    """
    计算风险等级。

    参数:
        risk_event_id: 风险事件ID
    返回:
        标准 JSON 响应，包含等级评分
    """
    import json
    from datetime import datetime, timezone

    if not risk_event_id:
        return json.dumps(make_error(
            code="RISK_EVENT_NOT_FOUND",
            message="risk_event_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "event_id": risk_event_id,
        "level": "HIGH",
        "score": 0.85,
        "factors": [
            {"factor": "证据充分性", "weight": 0.3, "contribution": 0.9},
            {"factor": "规则匹配度", "weight": 0.3, "contribution": 0.95},
            {"factor": "历史相似度", "weight": 0.2, "contribution": 0.8},
            {"factor": "置信度", "weight": 0.2, "contribution": 0.95}
        ],
        "requires_human_confirm": True
    }

    return json.dumps(make_response("risk grade", result), ensure_ascii=False)


@tool(description="查询相似历史案例。当用户查询相似案例时触发。")
def risk_cases(risk_event_id: str, limit: Optional[int] = 5) -> str:
    """
    查询相似历史案例。

    参数:
        risk_event_id: 风险事件ID
        limit: 返回数量限制（可选，默认5）
    返回:
        标准 JSON 响应，包含相似案例列表
    """
    import json
    from datetime import datetime, timezone

    if not risk_event_id:
        return json.dumps(make_error(
            code="RISK_EVENT_NOT_FOUND",
            message="risk_event_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = [
        {
            "case_id": f"CASE-{datetime.now().strftime('%Y%m%d')}-001",
            "description": "2026-07-15 炼油厂区01 受限空间作业人员未佩戴呼吸器",
            "similarity": 0.92,
            "resolution": "现场整改完成",
            "occurred_at": "2026-07-15T14:30:00Z"
        },
        {
            "case_id": f"CASE-{datetime.now().strftime('%Y%m%d')}-002",
            "description": "2026-06-20 炼油厂区02 受限空间作业气体检测不合格",
            "similarity": 0.78,
            "resolution": "暂停作业，完成通风后重新检测",
            "occurred_at": "2026-06-20T10:15:00Z"
        }
    ]

    if limit:
        result = result[:limit]

    return json.dumps(make_response("risk cases", result), ensure_ascii=False)


@tool(description="列出任务所有风险事件。当用户列出风险事件时触发。")
def risk_list(task_id: str) -> str:
    """
    列出任务所有风险事件。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含风险事件列表
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
        "events": [
            {
                "event_id": f"RE-{datetime.now().strftime('%Y%m%d')}-001",
                "description": "人员进入受限空间未佩戴呼吸器",
                "level": "HIGH",
                "status": "open",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ],
        "total": 1
    }

    return json.dumps(make_response("risk list", result), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

def create_risk_agent():
    """创建 P7 风险研判 Agent"""
    llm = create_chat_model()
    tools = [risk_analyze, risk_grade, risk_cases, risk_list]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_risk_agent(message: str) -> str:
    """运行 P7 风险研判 Agent"""
    agent = create_risk_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def risk_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_risk_agent(message)
