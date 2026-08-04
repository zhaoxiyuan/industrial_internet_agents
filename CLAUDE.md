# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

工业互联网边缘智能 Agent - 基于 LangChain 1.x 和 ReAct 模式的边缘智能技术问答助手。

## 常用命令

```bash
# 安装依赖
uv pip install -r requirements.txt

# 升级依赖到最新版本
uv pip install -r requirements.txt --upgrade

# 更新 lock 文件
uv lock

# 启动 Web 服务
python web/web_demo.py
```

## 技术栈

- **LangChain**: 1.3.14+ (核心框架)
- **LangGraph**: Agent 编排引擎
- **Gradio**: 6.x (Web UI)
- **LLM**: MiniMax API (通过 OpenAI 兼容接口)

## 架构说明

```
User → Gradio ChatInterface → create_agent (LangGraph)
                                   ↓
                              MiniMax LLM
                                   ↓
                            ToolNode (tools)
                                   ↓
                              边缘智能知识库
```

### Agent 实现模式 (LangChain 1.x)

使用 `langchain.agents.create_agent` 创建 Agent，特点：

1. **输入格式**: `agent.invoke({"messages": [HumanMessage(content=message)]})`
   - 注意：是 `messages` 不是 `input`

2. **系统提示词**: 直接传字符串，由 `create_agent` 内部转换为 SystemMessage

3. **输出提取**: Agent 返回 `{"messages": [...]}`，需用 `extract_output()` 获取最后一条 AI 消息

### 工具定义模式

使用 `@tool` 装饰器定义工具：

```python
from langchain_core.tools import tool

@tool(description="工具描述，当用户提及xxx时触发")
def get_xxx():
    """工具函数说明"""
    return "返回内容"
```

### Gradio ChatInterface 适配

Gradio 6.x 的 `ChatInterface` 传递 `(message, history)` 两个参数：

```python
def agent_demo(message: str, history: list) -> str:
    # 处理消息并返回字符串
    return result
```

`placeholder` 参数需通过 `textbox=gr.Textbox(placeholder="...")` 配置。

## 目录结构

```
├── agents/              # Agent 核心模块
├── model/              # LLM 模型封装
├── utils/              # 工具函数
├── web/                # Gradio Web 前端
├── SDD.md              # 软件设计说明书
└── task.md             # 作业流程阶段定义
```

## 环境配置

`.env` 文件需要配置：

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.minimax.chat/v1
OPENAI_MODEL=MiniMax-M3
```
