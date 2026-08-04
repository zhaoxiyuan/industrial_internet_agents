"""
P1: 作业预约、JSA分析与作业票
Permit Agent - 处理作业申请、JSA分析和作业票生成
"""
from typing import Any
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from model.chat_model import create_chat_model
from utils.agent_utils import extract_output
from .utils import make_response, make_error, SCHEMA_VERSION


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个工业作业许可管理专家，擅长处理作业预约、JSA（作业安全分析）和作业票生成。

你需要：
1. 分析作业申请，提取作业类型、区域、设备、人员和时间信息
2. 调用JSA分析工具识别危害因素和对应措施
3. 检查票证必填字段、风险措施完整性、人员资质冲突
4. 仅生成草稿，不自动审批

当用户提交作业申请时，调用 permit_submit 工具。
当用户请求JSA分析时，调用 jsa_analyze 工具。
当用户请求生成作业票草稿时，调用 permit_generate_draft 工具。
当用户查询作业票状态时，调用 permit_check 工具。"""


# ============================================================
# 工具定义
# ============================================================

@tool(description="提交作业申请，返回作业票草稿。当用户提供作业申请信息时触发。")
def permit_submit(application: str) -> str:
    """
    提交作业申请，返回作业票草稿。

    参数:
        application: 作业申请 JSON 字符串，包含 work_type, region, equipment,
                    personnel, planned_start, planned_end 等字段
    返回:
        标准 JSON 响应，包含 task_id, permit_draft_id, status, missing_fields
    """
    import json
    from datetime import datetime, timezone

    try:
        data = json.loads(application)
    except json.JSONDecodeError:
        return json.dumps(make_error(
            code="PERMIT_INVALID",
            message="无效的 JSON 格式",
            recoverable=False
        ), ensure_ascii=False)

    # 提取基本信息
    work_type = data.get("work_type", "")
    region = data.get("region", "")
    equipment = data.get("equipment", [])
    personnel = data.get("personnel", [])

    # 生成 task_id
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_id = f"TASK-{work_type[:4].upper()}-{region[:2]}-{timestamp}"

    # 生成 permit_draft_id
    permit_draft_id = f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # 检查缺失字段
    missing_fields = []
    if not work_type:
        missing_fields.append("work_type")
    if not region:
        missing_fields.append("region")
    if not equipment:
        missing_fields.append("equipment")
    if not personnel:
        missing_fields.append("personnel")

    # 检查人员资质
    for p in personnel:
        quals = p.get("qualifications", [])
        if not quals:
            missing_fields.append(f"personnel_{p.get('name', 'unknown')}_qualifications")

    result = {
        "task_id": task_id,
        "permit_draft_id": permit_draft_id,
        "status": "draft",
        "missing_fields": missing_fields,
        "jsa_complete": len(missing_fields) == 0,
    }

    return json.dumps(make_response("permit submit", result), ensure_ascii=False)


@tool(description="分析JSA，识别危害因素和对应措施。当用户请求分析JSA时触发。")
def jsa_analyze(task_id: str) -> str:
    """
    分析 JSA（作业安全分析），识别危害因素和对应措施。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 hazards 分析结果
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # 模拟 JSA 分析结果
    result = {
        "task_id": task_id,
        "hazards": [
            {
                "id": "H-001",
                "description": "受限空间内存在有毒有害气体",
                "severity": "高",
                "measures": ["气体检测", "强制通风", "佩戴呼吸器"]
            },
            {
                "id": "H-002",
                "description": "高温设备烫伤风险",
                "severity": "中",
                "measures": ["设备降温", "佩戴防护手套", "设置警戒区域"]
            },
            {
                "id": "H-003",
                "description": "人员误入风险区域",
                "severity": "中",
                "measures": ["设置警戒标识", "专人监护", "门禁管理"]
            }
        ],
        "completeness_score": 0.85,
        "missing_items": ["建议补充应急救援预案"]
    }

    return json.dumps(make_response("permit analyze-jsa", result), ensure_ascii=False)


@tool(description="生成作业票草稿，包含缺失项提示。当用户请求生成作业票草稿时触发。")
def permit_generate_draft(task_id: str) -> str:
    """
    生成作业票草稿。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 permit_draft_id, content, missing_fields
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
        "permit_draft_id": f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "content": {
            "task_id": task_id,
            "work_type": "受限空间作业",
            "region": "炼油厂区01",
            "equipment": ["反应器R-101", "管道P-205"],
            "medium": "原油",
            "personnel": [{"name": "张三", "badge_id": "P-101"}],
            "hazards": ["有毒有害气体", "高温烫伤", "人员误入"],
            "measures": ["气体检测", "通风", "警戒标识"]
        },
        "missing_fields": ["personnel_qualifications", "emergency_plan"],
        "requires_approval": True
    }

    return json.dumps(make_response("permit generate-draft", result), ensure_ascii=False)


@tool(description="查询作业票状态。当用户查询作业票状态时触发。")
def permit_check(permit_id: str) -> str:
    """
    查询作业票状态。

    参数:
        permit_id: 作业票ID
    返回:
        标准 JSON 响应，包含 permit_id, status, task_id 等
    """
    import json
    from datetime import datetime, timezone

    if not permit_id:
        return json.dumps(make_error(
            code="PERMIT_NOT_FOUND",
            message="permit_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "permit_id": permit_id,
        "status": "draft",
        "task_id": f"TASK-{permit_id.replace('PD-', '')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": None,
        "approved_at": None
    }

    return json.dumps(make_response("permit check", result), ensure_ascii=False)


# ============================================================
# Agent 工厂
# ============================================================

def create_permit_agent():
    """创建 P1 作业许可 Agent"""
    llm = create_chat_model()
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_permit_agent(message: str) -> str:
    """运行 P1 作业许可 Agent"""
    agent = create_permit_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def permit_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_permit_agent(message)
