
from rich import print as rprint
from langchain_core.tools import tool
from langchain_core.prompts import PromptTemplate
from langchain.agents import AgentExecutor, create_react_agent

from model.chat_model import create_chat_model


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


# 创建 ReAct Agent 的提示模板
template = '''你是一个工业互联网技术专家。请使用以下工具回答用户问题。

工具列表:
{tools}

工具名称: [{tool_names}]

你必须严格按以下格式回答。如果需要使用工具：

Thought: 你应该思考如何回答这个问题
Action: 选择的工具名称 (应该是 {tool_names} 之一)
Action Input: 工具的输入

如果你已经获得足够的信息可以直接回答用户问题：

Thought: 现在我知道最终答案了
Final Answer: 最终答案

重要规则：
1. 当你输出 "Action:" 时，不要输出 "Final Answer:"
2. 当你输出 "Final Answer:" 时，不要输出任何 "Action:" 或 "Action Input:"
3. Final Answer 只能在最后一次回答时使用，之前必须使用工具

Begin!

Question: {input}
Thought:{agent_scratchpad}'''

prompt = PromptTemplate.from_template(template)

# 创建大模型对象
llm = create_chat_model()

# 创建 ReAct Agent
tools = [get_edge_ai, get_k8s_ai, get_kube_edge_ai, get_openYurt_ai]
agent = create_react_agent(llm, tools, prompt)

# 创建 AgentExecutor
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# 运行 Agent
resp = agent_executor.invoke({"input": "请介绍下边缘智能的技术框架"})
rprint(resp)