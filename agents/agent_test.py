
"""Agent 测试脚本"""
from rich import print as rprint
from agents.edge_agent import chat


if __name__ == "__main__":
    resp = chat("请介绍下边缘智能的技术框架")
    rprint(resp)