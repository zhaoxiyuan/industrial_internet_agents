"""
P4: 监测资源绑定与数据关联
Binding Agent - 匹配固定/移动摄像头、传感器、定位数据
支持 HumanInTheLoop - Agent 层级中断
"""
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model.chat_model import create_chat_model_with_logging, get_llm_params
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.logging_handler import get_agent_config


# ============================================================
# Agent 层级 Checkpointer - 用于 Agent 内部中断
# ============================================================
_binding_checkpointer = MemorySaver()


# ============================================================
# 系统提示词
# ============================================================
SYSTEM_PROMPT = """你是一个监测资源绑定专家，负责将摄像头、传感器、定位设备与作业任务关联。

匹配策略：
- 固定摄像头：区域覆盖分析，选择最近且覆盖最好的N个
- 移动设备：根据作业流动性动态调整
- 传感器：选择同区域、同介质类型点位
- 人员定位：作业人员工卡与定位基站关联

当用户请求匹配资源时，调用 binding_match 工具。
当用户查看绑定状态时，调用 binding_status 工具。
当用户确认绑定时，调用 binding_confirm 工具。
当用户请求人工补充资源时，调用 binding_request_manual 工具。

无法自动匹配时触发人工补充流程。"""


# ============================================================
# 工具定义
# ============================================================

@tool(description="自动匹配监测资源（摄像头、传感器、定位）。当用户请求匹配监测资源时触发。")
def binding_match(task_id: str) -> str:
    """
    自动匹配监测资源。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含绑定关系
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
        "bindings": {
            "fixed_cameras": [
                {"camera_id": "CAM-001", "coverage": "95%", "location": "R-101入口"},
                {"camera_id": "CAM-002", "coverage": "88%", "location": "R-101中部"}
            ],
            "mobile_devices": [],
            "sensors": [
                {"sensor_id": "GAS-001", "type": "combustible_gas", "location": "R-101内部"},
                {"sensor_id": "TEMP-001", "type": "temperature", "location": "R-101内部"}
            ],
            "personnel_locations": [
                {"badge_id": "P-101", "zone": "R-101"}
            ]
        },
        "unmatched_resources": [],
        "status": "auto_matched",
        "requires_manual_confirm": True
    }

    return json.dumps(make_response("binding match", result), ensure_ascii=False)


@tool(description="查看资源绑定状态。当用户查看绑定状态时触发。")
def binding_status(task_id: str) -> str:
    """
    查看资源绑定状态。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含绑定详情
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="BINDING_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "task_id": task_id,
        "bindings": {
            "fixed_cameras": [
                {"camera_id": "CAM-001", "coverage": "95%", "location": "R-101入口"}
            ],
            "mobile_devices": [],
            "sensors": [
                {"sensor_id": "GAS-001", "type": "combustible_gas", "location": "R-101内部"}
            ],
            "personnel_locations": [
                {"badge_id": "P-101", "zone": "R-101"}
            ]
        },
        "status": "confirmed",
        "confirmed_at": datetime.now(timezone.utc).isoformat()
    }

    return json.dumps(make_response("binding status", result), ensure_ascii=False)


@tool(description="人工确认资源绑定。当用户确认绑定时触发。")
def binding_confirm(task_id: str) -> str:
    """
    人工确认资源绑定。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含确认信息
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="BINDING_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "task_id": task_id,
        "confirmed": True,
        "confirmed_by": "operator",
        "confirmed_at": datetime.now(timezone.utc).isoformat()
    }

    return json.dumps(make_response("binding confirm", result), ensure_ascii=False)


@tool(description="请求人工补充资源。当无法自动匹配时触发。")
def binding_request_manual(task_id: str, resource_type: str) -> str:
    """
    请求人工补充资源。

    参数:
        task_id: 任务唯一标识
        resource_type: 资源类型（camera/sensor/location）
    返回:
        标准 JSON 响应，包含请求信息
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    if resource_type not in ("camera", "sensor", "location"):
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"无效的 resource_type: {resource_type}",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "task_id": task_id,
        "request_id": f"REQ-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "resource_type": resource_type,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat()
    }

    return json.dumps(make_response("binding request-manual", result), ensure_ascii=False)


# ============================================================
# Agent 工厂 (HITL Enabled)
# ============================================================

def create_binding_agent():
    """创建 P4 监测资源绑定 Agent（基础版本，无 HITL）"""
    llm = create_chat_model_with_logging("P4")
    tools = [binding_match, binding_status, binding_confirm, binding_request_manual]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P4"))


def create_binding_agent_with_hitl():
    """创建 P4 监测资源绑定 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认
    """
    llm = create_chat_model_with_logging("P4")
    tools = [binding_match, binding_status, binding_confirm, binding_request_manual]

    # 创建 HITL Middleware
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "binding_match": True,              # 绑定匹配需要确认
            "binding_status": False,            # 查询状态自动批准
            "binding_confirm": True,             # 确认绑定需要确认
            "binding_request_manual": True,      # 请求人工绑定需要确认
        }
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P4"),
        middleware=[hitl_middleware],
        checkpointer=_binding_checkpointer,
    )
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P4"))


def run_binding_agent(message: str) -> str:
    """运行 P4 监测资源绑定 Agent"""
    agent = create_binding_agent()
    agent_config = get_agent_config("default", "P4", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def binding_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_binding_agent(message)
