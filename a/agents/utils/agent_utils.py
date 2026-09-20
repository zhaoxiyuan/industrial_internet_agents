"""Agent 工具模块"""


def extract_output(result: dict) -> str:
    """从 agent 返回结果中提取最终回复内容"""
    messages = result["messages"]
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content:
            return msg.content
    return ""