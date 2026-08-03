"""
Gradio Web 前端 - 与边缘智能 Agent 交互
"""
import gradio as gr
from agents.edge_agent import chat


def main():
    demo = gr.ChatInterface(
        fn=chat,
        title="边缘智能技术专家",
        description="基于 LangChain ReAct Agent 的边缘智能技术问答助手",
        placeholder="请输入您的问题，例如：请介绍下边缘智能的技术框架",
    )
    demo.launch()


if __name__ == "__main__":
    main()