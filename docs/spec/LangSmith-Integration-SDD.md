# LangSmith 集成技术方案

## 边缘智能作业监测系统 - 观测性增强

---

## 1. 引言

### 1.1 目的

本文档描述在边缘智能作业监测系统中集成 LangSmith 观测平台的技术方案，用于实现：
- Agent 调用链完整追踪
- LLM 输入输出日志持久化
- 工具调用性能分析
- 多 Agent 协作链路可视化

### 1.2 范围

本方案涵盖以下集成内容：
- LangSmith 客户端安装与配置
- LLM 回调处理器改造
- 多 Agent 会话关联（通过 thread_id 和 job_id）
- 追踪数据过滤与脱敏
- 本地验证与 LangSmith 控制台联调

### 1.3 定义、缩略语

| 术语 | 定义 |
|------|------|
| LangSmith | LangChain 官方观测平台，提供追踪、评估和基准测试 |
| Callback | LangChain 回调机制，用于拦截 LLM/Agent/Tool 事件 |
| Trace | 一次 LLM 调用或 Agent 执行链路记录 |
| Run | Trace 中的单个执行单元（LLM Run、Tool Run 等） |

### 1.4 参考资料

- [LangSmith Documentation](https://docs.smith.langchain.com/)
- `agents/model/chat_model.py` - 现有 LLM 封装
- `agents/utils/logging_handler.py` - 现有日志回调
- `requirements.txt` - 当前依赖版本

---

## 2. 总体描述

### 2.1 背景

当前系统使用 LangChain 1.3.14 构建 P1-P10 工作流 Agent，已实现：
- 自定义 `LLMLoggingCallback` 记录 LLM 调用
- 自定义 `AgentLoggingCallback` 记录 Agent 执行
- WebSocket 日志推送至前端

**存在的观测性缺失：**
| 已有能力 | 缺失能力 |
|---------|---------|
| 实时日志 | 调用链持久化（无法回溯历史） |
| 单 Agent 日志 | 跨 Agent 关联分析 |
| 基础性能记录 | Token 用量统计与成本分析 |
| 结构化日志 | LangSmith 官方支持与 UI 可视化 |

### 2.2 集成架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     LangSmith Cloud                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Traces  │  │ Sessions │  │ Datasets │  │ Evals    │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────────────────────┘
                              ↑
                     LangSmithCallback
                              ↑
┌─────────────────────────────────────────────────────────────────┐
│                   边缘智能作业监测系统                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Callback Handler                        │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │  │
│  │  │ LLMLogging  │  │AgentLogging │  │ LangSmithCallback   │ │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              ↓                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    chat_model.py                          │  │
│  │              create_chat_model_with_logging()             │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 数据流设计

```
用户请求 → job_id: "JOB-20260817-001"
                ↓
    ┌───────────────────────────┐
    │  LangChain Agent Executor  │
    │  (携带 metadata: job_id,   │
    │   agent_name, thread_id)   │
    └───────────────────────────┘
                ↓
    ┌───────────────────────────┐
    │    on_llm_start hook      │ → 记录 prompt 到 LangSmith
    └───────────────────────────┘
                ↓
    ┌───────────────────────────┐
    │    on_llm_end hook        │ → 记录 response 到 LangSmith
    └───────────────────────────┘
                ↓
    ┌───────────────────────────┐
    │    on_tool_start hook     │ → 记录 tool_call 到 LangSmith
    └───────────────────────────┘
                ↓
    LangSmith Dashboard 可视化：
    - 会话列表（按 job_id 过滤）
    - Agent 调用时间线
    - LLM 输入/输出对比
    - Tool 调用链与耗时
```

---

## 3. 功能详细设计

### 3.1 环境配置模块

**模块**: `agents/model/langsmith_config.py`（新增）

**功能列表**:

| 功能 | 说明 |
|------|------|
| `is_langsmith_enabled()` | 检查 LangSmith 是否启用 |
| `get_langsmith_callback()` | 创建配置好的 LangSmith 回调处理器 |
| `get_tracing_metadata()` | 构造追踪元数据（job_id, agent_name 等） |

**接口设计**:

```python
from langsmith import LangSmithCallback
from typing import Optional

class LangSmithConfig:
    """LangSmith 配置管理"""

    @staticmethod
    def is_enabled() -> bool:
        """检查是否启用 LangSmith 追踪"""
        api_key = os.getenv("LANGCHAIN_API_KEY")
        return bool(api_key and api_key.strip())

    @staticmethod
    def get_callback(
        project_name: str = "industrial-internet-agents",
        tags: Optional[list[str]] = None,
    ) -> LangSmithCallback:
        """
        创建 LangSmith 回调处理器

        Args:
            project_name: LangSmith 项目名称
            tags: 追踪标签列表

        Returns:
            LangSmithCallback 实例或 None
        """
        if not LangSmithConfig.is_enabled():
            return None

        return LangSmithCallback(
            project_name=project_name,
            tags=tags or [],
            metadata_provider=lambda: {
                "environment": os.getenv("ENV", "development"),
            },
        )
```

**环境变量**:

| 变量名 | 必填 | 说明 | 示例 |
|-------|------|------|------|
| `LANGCHAIN_API_KEY` | 是 | LangSmith API Key | `ls.xxx` |
| `LANGCHAIN_TRACING_SESSIONS` | 否 | 默认会话名 | `industrial-internet-agents` |
| `LANGCHAIN_PROJECT` | 否 | 项目名称 | `industrial-internet-agents-prod` |
| `LANGCHAIN_ENDPOINT` | 否 | API 端点 | `https://api.smith.langchain.com` |

### 3.2 LLM 回调集成

**修改模块**: `agents/model/chat_model.py`

**功能变化**:

| 变更类型 | 说明 |
|---------|------|
| 修改 | `create_chat_model_with_logging` 增加 LangSmith 回调 |
| 修改 | 传递 `job_id` 和 `agent_name` 到 LangSmith metadata |

**接口设计**:

```python
def create_chat_model_with_logging(
    agent_name: str = "LLM",
    job_id: str = "*",
) -> BaseChatModel:
    """
    创建带日志回调的 LLM 模型

    Args:
        agent_name: Agent 名称（用于日志标识）
        job_id: 作业 ID（用于 LangSmith 会话关联）

    Returns:
        配置好的 ChatModel 实例
    """
    callbacks = []

    # 1. 添加现有日志回调
    callbacks.append(LLMLoggingCallback(agent_name, job_id))

    # 2. 添加 LangSmith 回调（仅当配置了 API key 时）
    langsmith_cb = LangSmithConfig.get_callback(
        tags=[agent_name, f"job:{job_id}"],
    )
    if langsmith_cb:
        callbacks.append(langsmith_cb)

    return create_chat_model(callbacks=callbacks)
```

**Metadata 传递机制**:

LangSmith 通过 `metadata` 参数传递上下文信息：

```python
# 在 agent.invoke() 时通过 config 传递
agent_config = {
    "metadata": {
        "job_id": job_id,
        "agent_name": agent_name,
        "thread_id": thread_id,
    }
}
result = agent.invoke(input, agent_config)
```

### 3.3 追踪过滤器（数据脱敏）

**新增模块**: `agents/utils/langsmith_filters.py`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `filter_sensitive_content()` | 过滤敏感内容（API Key、Token 等） |
| `mask_pii()` | 脱敏个人信息 |
| `truncate_long_content()` | 截断过长内容避免存储浪费 |

**接口设计**:

```python
from langchain_core.messages import BaseMessage
from typing import Any

class LangSmithFilter:
    """LangSmith 追踪数据过滤器"""

    # 敏感字段模式
    SENSITIVE_PATTERNS = [
        (r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]+', '[API_KEY]'),
        (r'token["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]+', '[TOKEN]'),
        (r'password["\']?\s*[:=]\s*["\']?[^\s"\']+', '[PASSWORD]'),
    ]

    @classmethod
    def filter_message(cls, message: BaseMessage) -> BaseMessage:
        """过滤消息中的敏感信息"""
        content = message.content
        for pattern, replacement in cls.SENSITIVE_PATTERNS:
            import re
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
        return message

    @classmethod
    def truncate(cls, text: str, max_length: int = 10000) -> str:
        """截断过长文本"""
        if len(text) <= max_length:
            return text
        return text[:max_length] + f"\n... [truncated {len(text) - max_length} chars]"
```

### 3.4 追踪会话管理

**新增模块**: `agents/utils/langsmith_session.py`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `create_session()` | 创建追踪会话 |
| `get_session_url()` | 获取 LangSmith 会话链接 |
| `session_to_dict()` | 导出会话数据 |

**接口设计**:

```python
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class TracingSession:
    """追踪会话信息"""
    job_id: str
    thread_id: str
    agent_name: str
    started_at: datetime
    langsmith_run_id: Optional[str] = None

    @property
    def dashboard_url(self) -> Optional[str]:
        """生成 LangSmith Dashboard 链接"""
        if not self.langsmith_run_id:
            return None
        project = os.getenv("LANGCHAIN_PROJECT", "industrial-internet-agents")
        return f"https://smith.langchain.com/projects/{project}/runs/{self.langsmith_run_id}"

    def to_dict(self) -> dict:
        """导出会话信息用于日志记录"""
        return {
            "job_id": self.job_id,
            "thread_id": self.thread_id,
            "agent_name": self.agent_name,
            "started_at": self.started_at.isoformat(),
            "langsmith_run_id": self.langsmith_run_id,
            "dashboard_url": self.dashboard_url,
        }
```

---

## 4. 接口设计

### 4.1 新增接口

| 接口 | 文件 | 说明 |
|------|------|------|
| `LangSmithConfig.is_enabled()` | `model/langsmith_config.py` | 检查是否启用 |
| `LangSmithConfig.get_callback()` | `model/langsmith_config.py` | 获取回调处理器 |
| `LangSmithFilter.filter_message()` | `utils/langsmith_filters.py` | 过滤敏感信息 |
| `LangSmithFilter.truncate()` | `utils/langsmith_filters.py` | 截断过长内容 |
| `TracingSession.dashboard_url` | `utils/langsmith_session.py` | 获取追踪链接 |
| `TracingSession.to_dict()` | `utils/langsmith_session.py` | 导出会话数据 |

### 4.2 修改接口

| 接口 | 文件 | 变更说明 |
|------|------|---------|
| `create_chat_model_with_logging()` | `model/chat_model.py` | 增加 LangSmith 回调参数 |
| `create_main_agent()` | `main_agent.py` | 传递 tracing metadata |
| `run_main_agent()` | `main_agent.py` | 记录 langsmith_run_id 到会话 |

---

## 5. 实现步骤

### 5.1 依赖安装

```bash
# 安装 langsmith 包
uv pip install langsmith
```

### 5.2 环境配置

在 `.env` 文件中添加：

```env
# LangSmith 配置
LANGCHAIN_API_KEY=ls.your_api_key_here
LANGCHAIN_TRACING_SESSIONS=industrial-internet-agents
LANGCHAIN_PROJECT=industrial-internet-agents-dev
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

### 5.3 文件变更清单

| 序号 | 操作 | 文件路径 |
|------|------|---------|
| 1 | 新增 | `agents/model/langsmith_config.py` |
| 2 | 新增 | `agents/utils/langsmith_filters.py` |
| 3 | 新增 | `agents/utils/langsmith_session.py` |
| 4 | 修改 | `agents/model/chat_model.py` |
| 5 | 修改 | `requirements.txt` |
| 6 | 修改 | `.env.example` |

### 5.4 代码实现

#### 5.4.1 `agents/model/langsmith_config.py`

```python
"""LangSmith 配置管理模块"""
import os
from typing import Optional
from langsmith import LangSmithCallback


class LangSmithConfig:
    """LangSmith 配置管理"""

    @staticmethod
    def is_enabled() -> bool:
        """检查是否启用 LangSmith 追踪"""
        api_key = os.getenv("LANGCHAIN_API_KEY")
        return bool(api_key and api_key.strip())

    @staticmethod
    def get_callback(
        project_name: str = "industrial-internet-agents",
        tags: Optional[list[str]] = None,
    ) -> Optional[LangSmithCallback]:
        """
        创建 LangSmith 回调处理器

        Args:
            project_name: LangSmith 项目名称
            tags: 追踪标签列表

        Returns:
            LangSmithCallback 实例，如果未启用则返回 None
        """
        if not LangSmithConfig.is_enabled():
            return None

        return LangSmithCallback(
            project_name=project_name,
            tags=tags or [],
            metadata_provider=lambda: {
                "environment": os.getenv("ENV", "development"),
            },
        )
```

#### 5.4.2 `agents/model/chat_model.py` 修改

```python
# 添加导入
from agents.model.langsmith_config import LangSmithConfig

def create_chat_model_with_logging(
    agent_name: str = "LLM",
    job_id: str = "*",
) -> BaseChatModel:
    """创建带日志回调的 LLM 模型"""
    callbacks = []

    # 添加现有日志回调
    callbacks.append(LLMLoggingCallback(agent_name, job_id))

    # 添加 LangSmith 回调（仅当配置了 API key 时）
    langsmith_cb = LangSmithConfig.get_callback(
        tags=[agent_name, f"job:{job_id}"],
    )
    if langsmith_cb:
        callbacks.append(langsmith_cb)

    return create_chat_model(callbacks=callbacks)
```

---

## 6. 验证方法

### 6.1 本地验证清单

| 序号 | 验证项 | 预期结果 |
|------|--------|---------|
| 1 | `python -c "from langsmith import LangSmithCallback; print('OK')"` | 输出 `OK` |
| 2 | `LangSmithConfig.is_enabled()` 无 API Key 时 | 返回 `False` |
| 3 | `LangSmithConfig.is_enabled()` 配置 API Key 后 | 返回 `True` |
| 4 | `create_chat_model_with_logging()` 不报异常 | 正常返回模型 |
| 5 | 发起一次 Agent 调用 | LangSmith Dashboard 可见 trace |

### 6.2 联调验证步骤

1. **获取 API Key**
   - 访问 [LangSmith](https://smith.langchain.com/)
   - 创建账户并生成 API Key

2. **配置环境变量**
   ```bash
   export LANGCHAIN_API_KEY=ls.your_key
   export LANGCHAIN_PROJECT=industrial-internet-agents-test
   ```

3. **运行测试**
   ```bash
   python -c "
   from agents.model.chat_model import create_chat_model_with_logging
   model = create_chat_model_with_logging('TEST', 'TEST-JOB-001')
   result = model.invoke([('user', 'Hello, say hi in 5 words')])
   print(result.content)
   "
   ```

4. **检查 LangSmith Dashboard**
   - 访问 `https://smith.langchain.com/`
   - 在对应项目中查看新创建的 trace
   - 验证 metadata 中的 `job_id` 和 `agent_name`

### 6.3 追踪数据验证点

| 验证内容 | 检查位置 | 预期 |
|---------|---------|------|
| LLM 调用 | LangSmith Run 列表 | 显示 prompt 和 response |
| Tool 调用 | LangSmith Trace 详情 | 显示 tool_name、input、output |
| 会话关联 | Run metadata | 包含 job_id、thread_id |
| 敏感信息过滤 | Trace 内容 | API Key 已脱敏 |

---

## 7. 附录

### 7.1 LangSmith 免费额度

| 功能 | 免费额度 |
|------|---------|
| 追踪条数 | 每月 10,000 条 |
| 存储时间 | 30 天 |
| 项目数 | 3 个 |
| 团队成员 | 1 人 |

### 7.2 备选方案：自托管追踪

如不使用 LangSmith 云服务，可使用 LangChain 内置的 `LangChainTracer`：

```python
from langchain_core.callbacks import LangChainTracer

tracer = LangChainTracer(
    project_name="industrial-internet-agents",
    example_id=job_id,
)
```

### 7.3 后续扩展

- **评估（Evaluation）**: 配置自动化 LLM 输出评估
- **数据集（Datasets）**: 导入历史对话用于回归测试
- **基准测试（Benchmarks）**: 对比不同模型/提示词的效果