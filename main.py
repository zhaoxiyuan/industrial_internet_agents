"""
演示如何使用 LLM 模块
"""
from model.chat_model import create_chat_model
from rich import print as rprint


def main():
    llm = create_chat_model()
    # 调用示例
    response = llm.invoke("你好，请介绍一下你自己")
    rprint(response)

if __name__ == "__main__":
    main()
