"""P7 Risk Agent - 空壳，保留 LangChain 工具桩供 main_agent.py 调用"""
import json
from langchain_core.tools import tool


@tool
def risk_analyze(event_id: str) -> str:
    """风险分析工具。供 main_agent P7 阶段调用。"""
    return json.dumps({"status": "ok", "result": {"event_id": event_id, "risk_level": 0}})


@tool
def risk_list(task_id: str) -> str:
    """风险列表工具。供 main_agent P7 阶段调用。"""
    return json.dumps({"status": "ok", "result": {"events": []}})


# 兼容导出
create_risk_agent = None
create_risk_agent_with_hitl = None
run_risk_agent = None
risk_demo = None
risk_grade = None
risk_cases = None
