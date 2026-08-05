"""
边缘智能 Agent 核心模块
"""
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent

from model.chat_model import create_chat_model
from utils.agent_utils import extract_output
from rich import print as rprint


@tool(description="查询边缘智能相关知识,当用户提及到边缘计算时触发")
def get_edge_ai():
    return "K8s、kubeEdge、OpenYurt"


@tool(description="查询边缘计算K8S相关知识,当用户提及到边缘计算时触发")
def get_k8s_ai():
    return "K8s是一个云原生编排框架。"


@tool(description="查询边缘计算KubeEdge相关知识,当用户提及到边缘计算时触发")
def get_kube_edge_ai():
    return "KubeEdge是一个边缘计算框架。"


@tool(description="查询边缘计算OpenYurt相关知识,当用户提及到边缘计算时触发")
def get_openYurt_ai():
    return "OpenYurt是一个边缘计算框架。"


# 创建 Agent 的系统提示词
SYSTEM_PROMPT = """你是一个工业互联网技术专家。当用户提问时，你需要：
1. 分析用户问题是否需要使用工具
2. 如果需要使用工具，请调用合适的工具获取信息
3. 根据工具返回的结果回答用户问题

工具会返回相关信息，你可以直接使用这些信息回答问题。"""

prompt = SYSTEM_PROMPT


def agent_demo(message: str, history: list) -> str:
    """与 Agent 对话 (Gradio ChatInterface 格式)"""
    llm = create_chat_model()
    tools = [get_edge_ai, get_k8s_ai, get_kube_edge_ai, get_openYurt_ai]
    # 必传参数 模型、tools、系统提示词
    agent = create_agent(model=llm, tools=tools, system_prompt=prompt)
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


if __name__ == "__main__":
    resp = agent_demo("请介绍下边缘智能的技术框架")
    rprint(resp)
    last_msg = extract_output(resp)
    print(f'最后返回的消息:{last_msg}')
