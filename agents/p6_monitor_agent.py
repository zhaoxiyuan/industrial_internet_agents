"""
P6: 作业过程动态监测
Monitor Agent - 持续获取视频、传感器、定位数据，识别违章及条件变化
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
SYSTEM_PROMPT = """你是一个作业过程动态监测专家，负责实时监测作业过程中的风险事件。

采样策略：
- 正常状态：1帧/秒采样
- 异常检测：5帧/秒 + 全量存储
- 告警触发：连续10帧异常才上报

时序聚合：
- 单帧误报过滤：同位置、同类型事件需连续N帧确认
- 跨镜头追踪：同一目标多摄像头追踪

当用户启动监测时，调用 monitor_start 工具。
当用户停止监测时，调用 monitor_stop 工具。
当用户查看监测状态时，调用 monitor_status 工具。
当用户获取候选风险事件时，调用 monitor_events 工具。

monitor start 是幂等的：重复调用不重启会话。"""


# ============================================================
# 工具定义
# ============================================================

# 模拟监测会话存储
_MOCK_SESSIONS = {}


@tool(description="启动监测会话（幂等）。当用户启动监测时触发。")
def monitor_start(task_id: str) -> str:
    """
    启动监测会话（幂等操作）。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 session_id
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # 幂等检查
    existing = _MOCK_SESSIONS.get(task_id)
    if existing:
        result = {
            "task_id": task_id,
            "session_id": existing["session_id"],
            "status": "running",
            "started_at": existing["started_at"],
            "idempotent": True
        }
        return json.dumps(make_response("monitor start", result), ensure_ascii=False)

    # 创建新会话
    session_id = f"SESSION-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    started_at = datetime.now(timezone.utc).isoformat()

    _MOCK_SESSIONS[task_id] = {
        "session_id": session_id,
        "started_at": started_at,
        "status": "running"
    }

    result = {
        "task_id": task_id,
        "session_id": session_id,
        "status": "running",
        "started_at": started_at,
        "idempotent": False
    }

    return json.dumps(make_response("monitor start", result), ensure_ascii=False)


@tool(description="停止监测会话。当用户停止监测时触发。")
def monitor_stop(task_id: str) -> str:
    """
    停止监测会话。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含停止信息
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    session = _MOCK_SESSIONS.get(task_id)
    if not session:
        return json.dumps(make_error(
            code="MONITOR_SESSION_NOT_FOUND",
            message=f"Monitoring session for task {task_id} not found",
            recoverable=False
        ), ensure_ascii=False)

    # 停止会话
    del _MOCK_SESSIONS[task_id]

    result = {
        "task_id": task_id,
        "status": "stopped",
        "events_captured": 0,
        "duration_seconds": 3600
    }

    return json.dumps(make_response("monitor stop", result), ensure_ascii=False)


@tool(description="查看监测状态。当用户查看监测状态时触发。")
def monitor_status(task_id: str) -> str:
    """
    查看监测状态。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含监测状态
    """
    import json
    from datetime import datetime, timezone

    if not task_id:
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    session = _MOCK_SESSIONS.get(task_id)

    if session:
        result = {
            "task_id": task_id,
            "session_id": session["session_id"],
            "status": "running",
            "sampling_rate": "1帧/秒",
            "model_selected": ["CV-高空作业模型", "VL-受限空间模型"],
            "events_count": 0,
            "started_at": session["started_at"]
        }
    else:
        result = {
            "task_id": task_id,
            "session_id": None,
            "status": "idle",
            "sampling_rate": None,
            "model_selected": [],
            "events_count": 0,
            "started_at": None
        }

    return json.dumps(make_response("monitor status", result), ensure_ascii=False)


@tool(description="获取候选风险事件。当用户获取风险事件时触发。")
def monitor_events(task_id: str, since: Optional[str] = None) -> str:
    """
    获取候选风险事件。

    参数:
        task_id: 任务唯一标识
        since: ISO8601 时间戳，仅返回此时间后的事件（可选）
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

    # 模拟候选事件
    events = [
        {
            "event_type": "candidate_event",
            "event_id": f"CE-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "task_id": task_id,
            "description": "检测到人员未佩戴呼吸器",
            "confidence": 0.85,
            "evidence": [
                {"type": "video", "timestamp": datetime.now(timezone.utc).isoformat(),
                 "clip": "clip_001.mp4"}
            ],
            "severity": "HIGH",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    ]

    # 输出 JSON Lines
    output = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    return output


# ============================================================
# Agent 工厂
# ============================================================

def create_monitor_agent():
    """创建 P6 动态监测 Agent"""
    llm = create_chat_model()
    tools = [monitor_start, monitor_stop, monitor_status, monitor_events]
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_monitor_agent(message: str) -> str:
    """运行 P6 动态监测 Agent"""
    agent = create_monitor_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def monitor_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_monitor_agent(message)
