# 工业互联网边缘智能 Agent

基于 LangChain 1.x 和 ReAct 模式的边缘智能技术问答助手。

## 技术栈

- **LangChain**: 1.3.14 (LLM 应用框架)
- **LangGraph**: 边缘编排引擎
- **Gradio**: 6.22.0 (Web UI)
- **MiniMax API**: 大语言模型后端

## 项目结构

```
├── agents/              # Agent 核心模块
│   └── edge_agent_demo.py   # 边缘智能 Agent 实现
├── model/              # LLM 模型封装
│   └── chat_model.py        # ChatModel 配置
├── utils/              # 工具模块
│   └── agent_utils.py       # Agent 辅助函数
├── web/                # Web 前端
│   └── web_demo.py          # Gradio 聊天界面
└── .env                # 环境配置
```

## 快速开始

### 1. 配置环境变量

创建 `.env` 文件：

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.minimax.chat/v1
OPENAI_MODEL=MiniMax-M3
```

### 2. 安装依赖

```bash
uv pip install -r requirements.txt
```

### 3. 启动 Web 服务

```bash
python web/web_demo.py
```

## Agent 工具

| 工具 | 描述 |
|------|------|
| `get_edge_ai` | 查询边缘智能相关知识 |
| `get_k8s_ai` | 查询 K8s 相关信息 |
| `get_kube_edge_ai` | 查询 KubeEdge 相关信息 |
| `get_openYurt_ai` | 查询 OpenYurt 相关信息 |

## 技术架构

```
User → Gradio ChatInterface → create_agent (LangGraph)
                                   ↓
                              MiniMax LLM
                                   ↓
                            ToolNode (tools)
                                   ↓
                              边缘智能知识库
```