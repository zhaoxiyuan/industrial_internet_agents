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
python web/server.py
```

## 日志规范

**所有 Web API 端点必须在入口和出口处打印日志，便于排查错误。**

日志格式：
```
[HTTP方法] [端点路径] 进入: 请求参数
[HTTP方法] [端点路径] 响应: 响应数据
[HTTP方法] [端点路径] 异常: 错误信息
```

示例：
```
2026-08-06 10:30:00 [INFO] server: [POST] /api/workflow/confirm 进入: thread_id=xxx, stage=P1
2026-08-06 10:30:01 [INFO] server: [POST] /api/workflow/confirm 响应: {'status': 'waiting', ...}
```

关键原则：
- **入口日志**：记录请求参数（敏感信息如 api_key 需要脱敏）
- **出口日志**：记录响应状态和数据摘要
- **异常日志**：使用 `logger.exception()` 打印完整堆栈信息

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
├── docs/               # 文档目录
├── SDD.md              # 软件设计说明书
└── task.md             # 作业流程阶段定义
```

## 文档同步规则

**代码变更后，必须检查并同步更新 `docs/` 目录下的相关文档：**

- `docs/architecture.md` - 系统架构和技术说明
- `docs/agents/*.md` - 各 Agent 的说明文档
- `docs/index.md` - 执行链路和交互流程

**规则：文档说明必须与代码逻辑保持一致。代码变更后：**
1. 检查是否影响架构、接口、执行流程
2. 如有影响，立即更新相关文档
3. 不要留下"文档待更新"之类的 TODO

## 环境配置

`.env` 文件需要配置：

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.minimax.chat/v1
OPENAI_MODEL=MiniMax-M3
```

## Git 提交规则

**修改代码后，必须经过人工确认才可以提交代码。**

提交前必须：
1. 运行 `git status` 和 `git diff` 查看变更内容
2. 向用户展示变更摘要
3. 获得用户明确确认后，才能执行 `git commit`
4. 如果用户拒绝或要求修改，必须按要求调整后再提交
