"""
P5: 作业前条件核验
Verify Agent - 核对隔离、警戒、消防、气体检测、人员资质和PPE
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
SYSTEM_PROMPT = """你是一个作业前条件核验专家，负责在作业开始前核验各项安全措施是否落实。

核验结果枚举：
- PASS: 符合
- PENDING: 待确认
- FAIL: 不符合
- N/A: 不适用

核验项目包括：
1. 隔离措施（隔离阀、电源切断等）
2. 警戒标识（警戒带、警示牌等）
3. 消防器材（灭火器、消防栓等）
4. 气体检测（可燃气体、有毒气体、氧气含量）
5. 人员资质（证书有效期、作业授权）
6. PPE配备（呼吸器、防护服等）

当用户请求生成核验清单时，调用 verify_checklist 工具。
当用户执行核验时，调用 verify_execute 工具。
当用户获取开工建议时，调用 verify_recommendation 工具。"""


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
    """创建 P5 作业前条件核验 Agent"""
    llm = create_chat_model()
    tools = [verify_checklist, verify_execute, verify_recommendation]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_verify_agent(message: str) -> str:
    """运行 P5 作业前条件核验 Agent"""
    agent = create_verify_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def verify_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_verify_agent(message)
