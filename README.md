# 工业互联网边缘智能作业监测系统

基于 LangChain 1.x 和 ReAct 模式的边缘智能作业监测助手，支持 P1-P10 十阶段作业管理流程。

## 技术栈

- **LangChain 1.x**: 1.3.14+ (LLM 应用框架，ReAct Agent 编排)
- **LangGraph**: 边缘编排引擎（状态管理与检查点）
- **FastAPI / 原生 HTTP**: Web 服务层
- **WebSocket**: 实时日志推送
- **MiniMax API**: 大语言模型后端（OpenAI 兼容接口）
- **原生 HTML/JS**: 前端界面（无框架依赖）

## 项目结构

```
├── agents/                    # Agent 核心模块
│   ├── main_agent.py         # P1-P10 编排器（工作流入口）
│   ├── hitl/                 # Human-in-the-Loop 机制
│   │   └── human_in_the_loop.py
│   ├── p1_permit_agent.py    # P1: 作业票申请
│   ├── p2_task_agent.py      # P2: 任务实例化
│   ├── p3_context_agent.py   # P3: 上下文构建
│   ├── p4_binding_agent.py   # P4: 资源绑定
│   ├── p5_verify_agent.py    # P5: 作业前核查
│   ├── p6_monitor_agent.py   # P6: 过程监控（A5/A6 集成）
│   ├── p7_risk_agent.py      # P7: 风险分析
│   ├── p8_disposition_agent.py  # P8: 处置下发
│   ├── p9_closure_agent.py   # P9: 完工闭环
│   ├── p10_archive_agent.py  # P10: 归档总结
│   ├── model/                # LLM 模型封装
│   │   ├── config.py         # 环境配置（.env）
│   │   └── chat_model.py     # MiniMax API 封装
│   ├── utils/                # 工具函数
│   │   ├── response_utils.py # 响应格式化
│   │   ├── logging_handler.py# WebSocket 日志广播
│   │   └── system_prompt.py  # Prompt 加载
│   └── workflow/             # 工作流状态与持久化
│       ├── workflow_state.py # 状态追踪
│       └── job_persistence.py# 文件持久化
├── web/                      # Web 前端 + HTTP 服务
│   ├── server.py            # HTTP 服务器（8080 端口）
│   ├── app.js               # 前端交互逻辑
│   ├── index.html           # 主页面
│   ├── api/                 # REST API 路由
│   │   ├── config.py        # LLM 配置管理
│   │   └── workflow.py      # 工作流 API
│   └── ws/                  # WebSocket 服务
│       ├── manager.py       # 广播队列管理
│       └── servers.py       # WebSocket 服务线程
├── A5/                       # A5: 实时监控子系统
│   └── agent/
│       └── a5_agent.py      # A5 监控 Agent
├── A6/                       # A6: 风险评估子系统
│   └── agent/
│       └── a6_agent.py      # A6 风险评估 Agent
├── data/                     # 数据持久化目录
│   └── jobs/{job_id}/       # 作业数据（application.json, p1-p10_result.json 等）
├── docs/                     # 文档
│   ├── architecture.md      # 技术架构详述
│   ├── index.md             # 执行链路
│   └── spec/
│       ├── SDD.md           # 软件设计说明书
│       └── task.md          # P1-P10 阶段定义
└── .env                      # 环境配置
```

## 核心架构

```
┌──────────────────────────────────────────────────────────────┐
│                    前端 (web/index.html + app.js)             │
│                 原生 HTML/JS，无框架依赖                       │
└──────────────────────────────────────────────────────────────┘
                              │ HTTP / WebSocket
┌──────────────────────────────────────────────────────────────┐
│                 HTTP 服务层 (web/server.py:8080)              │
│         /api/workflow/start  /api/workflow/confirm           │
│         /api/workflow/state  /api/config  /api/prompt        │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│              主编排器 (agents/main_agent.py)                   │
│              run_workflow() 执行 P1-P10 串行流程               │
│              文件持久化: data/jobs/{job_id}/                  │
└──────────────────────────────────────────────────────────────┘
           │        │        │        │        │
      ┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
      │  P1   ││  P2   ││  P3   ││  P4   ││  P5   │
      │ Permit││ Task  ││Context││Binding││Verify │
      └────────┘└────────┘└────────┘└────────┘└────────┘
           │        │        │        │        │
      ┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐
      │  P6   ││  P7   ││  P8   ││  P9   ││  P10  │
      │Monitor││ Risk  ││Disposition│Closure││Archive│
      │(A5/A6)││ (stub)││        ││       ││       │
      └────────┘└────────┘└────────┘└────────┘└────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│              Human-in-the-Loop (HITL) 两层机制                │
│  Workflow 层: 阶段完成后检查 pending_confirmation 暂停等待     │
│  Agent 层: HumanInTheLoopMiddleware 在工具调用前中断          │
└──────────────────────────────────────────────────────────────┘
                              │
┌──────────────────────────────────────────────────────────────┐
│                   MiniMax LLM (OpenAI 兼容)                   │
└──────────────────────────────────────────────────────────────┘
```

## P1-P10 作业流程

| 阶段 | Agent | 说明 |
|------|-------|------|
| P1 | p1_permit_agent | 作业票申请：提交作业申请、JSA 分析、生成作业票草稿 |
| P2 | p2_task_agent | 任务实例化：从批准作业票创建监测任务 |
| P3 | p3_context_agent | 上下文构建：汇聚场景、人员、设备、环境信息 |
| P4 | p4_binding_agent | 资源绑定：绑定摄像头、传感器等监测资源 |
| P5 | p5_verify_agent | 作业前核查：执行核查清单，输出开工建议 |
| P6 | p6_monitor_agent | 过程监控：集成 A5 实时监控 + A6 风险评估 |
| P7 | p7_risk_agent | 风险分析：风险识别与分级（当前为存根） |
| P8 | p8_disposition_agent | 处置下发：创建确认、暂停、恢复等处置任务 |
| P9 | p9_closure_agent | 完工闭环：完工确认、报告生成 |
| P10 | p10_archive_agent | 归档总结：归档、案例挖掘、性能分析 |

## Human-in-the-Loop 机制

系统采用**两层 HITL 机制**：

1. **Workflow 层**：每个阶段执行完成后检查 `pending_confirmation`，暂停等待人工确认
2. **Agent 层**：P1 等阶段使用 `HumanInTheLoopMiddleware`，在工具调用前中断等待审批

```
用户提交申请
      ↓
run_workflow() 执行 P1
      ↓
P1 Agent 执行，第一次工具调用前中断
      ↓
返回 {pending_confirmation: {type: "hitl_tool_call"}}
      ↓
前端弹出确认对话框
      ↓
用户审批 → POST /api/workflow/confirm
      ↓
confirm_and_continue() 恢复执行
      ↓
P1 完成，继续 P2-P10
```

## 快速开始

### 1. 配置环境变量

创建 `.env` 文件：

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.minimax.chat/v1
OPENAI_MODEL=MiniMax-M3
OPENAI_TEMPERATURE=0.7
OPENAI_MAX_TOKENS=2000
```

### 2. 安装依赖

```bash
uv pip install -r requirements.txt
```

### 3. 启动 Web 服务

```bash
python web/server.py
```

访问 http://localhost:8080 打开作业监测界面。

### 4. 启动 A5/A6 监控服务（可选）

```bash
python agents/p6_monitor_agent.py
```

访问 http://localhost:5002 打开 A5/A6 监控面板。

## Web API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/workflow/start` | POST | 启动工作流，提交作业申请 |
| `/api/workflow/confirm` | POST | 提交人工确认（阶段、决策） |
| `/api/workflow/state` | GET | 查询工作流状态 |
| `/api/config` | GET/POST | 获取/设置 LLM 配置 |
| `/api/prompt/{stage}` | GET/POST | 获取/设置各阶段 System Prompt |
| `/api/test/llm` | POST | 测试 LLM 连接 |

## 数据持久化

作业数据存储在 `data/jobs/{job_id}/` 目录：

```
data/jobs/{job_id}/
├── application.json    # 用户提交的作业申请
├── permit.json         # P1 作业票数据
├── p1_result.json      # P1 执行结果
├── p2_result.json      # P2 执行结果
├── ...
├── p10_result.json     # P10 执行结果
├── logs.json           # 执行日志
├── confirmations.json  # 人工确认记录
└── a5_logs/           # A5 监控日志（P6）
```

## Agent 工具说明

### P1 工具

| 工具 | 说明 |
|------|------|
| `permit_submit` | 提交作业票申请 |
| `jsa_analyze` | JSA（作业安全分析） |
| `permit_generate_draft` | 生成作业票草稿 |
| `permit_check` | 查询作业票状态 |

### P2-P10 工具

- **P2**: `task_list`, `task_get`, `task_instance_create`, `task_subscribe`
- **P3**: `context_build`, `context_validate`, `context_history`
- **P4**: `binding_match`, `binding_status`, `binding_confirm`, `binding_request_manual`
- **P5**: `verify_checklist`, `verify_execute`, `verify_recommendation`
- **P6**: `monitor_start`, `monitor_events`（集成 A5/A6）
- **P7**: `risk_analyze`, `risk_list`（存根实现）
- **P8**: `disposition_create`, `disposition_confirm`, `disposition_status`, `disposition_list`
- **P9**: `closure_status`, `closure_verify`, `closure_report`, `closure_close`
- **P10**: `archive_task`, `archive_cases`, `archive_performance`, `archive_suggestions`