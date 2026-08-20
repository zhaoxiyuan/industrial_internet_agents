# 项目技术架构

## 1. 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 核心框架 | LangChain | 1.3.14+ Agent 框架 |
| Agent 编排 | LangGraph | 状态机工作流编排 |
| Web UI | 原生 HTML/CSS/JS | 无框架，轻量级 |
| LLM 接口 | MiniMax API | OpenAI 兼容接口 |
| Python | 3.x | 主要开发语言 |

**架构模式**: 基于 LangChain 1.x 的 `create_agent` 函数 + ReAct 模式

---

## 2. 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         前端 (Web)                               │
│                    web/index.html + app.js                      │
│                  原生 HTML/CSS/JS，无框架依赖                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓ HTTP REST API
┌─────────────────────────────────────────────────────────────────┐
│                       后端服务 (server.py)                       │
│                    HTTP Server (8080端口)                        │
│  /api/workflow/start  /api/workflow/confirm  /api/workflow/state │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      主调度 Agent (main_agent)                   │
│         start_workflow / execute_stage / confirm_stage          │
│                    run_workflow() 协调 P1-P10                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    P1-P10 阶段 Agent                            │
│  p1_permit  p2_task  p3_context  p4_binding  p5_verify          │
│  p6_monitor  p7_risk  p8_disposition  p9_closure  p10_archive   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    HumanInTheLoop (两层机制)                     │
│     Workflow 层: 阶段边界暂停 (main_agent.py)                    │
│     Agent 层: 工具调用前暂停 (HumanInTheLoopMiddleware)          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        LLM (MiniMax)                            │
│                   OpenAI 兼容接口调用                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Agent 层级架构

```
┌─────────────────────────────────────────────────────────────┐
│                      MAIN AGENT                              │
│                   (main_agent.py)                            │
│  tools: start_workflow / execute_stage / confirm_stage /     │
│         get_status / list_pending                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              P1-P10 阶段 Agent (独立 Agent)                  │
├───────────┬───────────┬───────────┬───────────┬────────────┤
│ P1 permit │ P2 task   │ P3 ctx    │ P4 bind   │ P5 verify  │
├───────────┼───────────┼───────────┼───────────┼────────────┤
│ P6 monitor│ P7 risk   │ P8 disp   │ P9 closure│ P10 archive│
└───────────┴───────────┴───────────┴───────────┴────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              各阶段 Agent 内部结构                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ create_xxx_agent()        → 基础 Agent              │   │
│  │ create_xxx_agent_with_hitl() → HITL Agent           │   │
│  └─────────────────────────────────────────────────────┘   │
│  工具: @tool 装饰器定义的函数 (如 permit_submit, jsa_analyze) │
│  中间件: HumanInTheLoopMiddleware (HITL模式下)              │
│  Checkpoint: MemorySaver (中断恢复)                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 执行链路架构

### 4.1 Web 请求链路

```
用户点击启动
     ↓
web/app.js: startWorkflow()
     ↓ POST /api/workflow/start
web/server.py: Handler.do_POST()
     ↓
main_agent.py: run_workflow(application, thread_id)
     ↓ 后台线程执行，不阻塞
立即返回 {job_id, status: "starting"}
     ↓
web/app.js: startPollingWorkflow(job_id)
     ↓ 每2秒轮询
web/app.js: pollWorkflowState(jobId)
     ↓ GET /api/workflow/state?thread_id=xxx
web/server.py: Handler.do_GET()
     ↓
main_agent.py: get_workflow_state(thread_id)
     ↓ 读取 data/jobs/{job_id}/*.json
返回状态 {current_stage, pending_confirmations, ...}
     ↓
如有 pending_confirmation → showHitlModal() 弹窗
     ↓
用户确认 → confirmDecision(decision)
     ↓ POST /api/workflow/confirm
main_agent.py: confirm_and_continue(thread_id, stage, decision)
     ↓
继续轮询或工作流完成
```

### 4.2 工作流 P1-P10 执行链路

```
用户提交作业申请
          ↓
┌─────────────────────────────────────────────────────────────┐
│ run_workflow(application, thread_id)                        │
│  1. save_job_application() → data/jobs/{job_id}/           │
│  2. 按顺序执行 STAGE_EXECUTORS[P1] → ... → [P10]           │
└─────────────────────────────────────────────────────────────┘
          ↓
    ┌─────────────────────────────────────────────────────┐
    │ P1: execute_p1() - HITL Agent 层中断恢复            │
    │  run_permit_agent_with_hitl()                       │
    │  → 工具调用前被 HITL Middleware 中断                │
    │  → pending: P1 Agent 工具调用确认                   │
    │  → 用户确认后: execute_p1(resume=True) 恢复执行    │
    │  → 保存 p1_result.json                              │
    │  → pending: 作业票缺失字段确认（阶段级）            │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P2: execute_p2()                                    │
    │  task_instance_create                               │
    │  → 保存 p2_result.json                              │
    │  → pending: 是否纳入智能监测                        │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P3: execute_p3()                                    │
    │  context_build                                      │
    │  → 保存 p3_result.json                              │
    │  → pending: 上下文缺失字段确认                      │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P4: execute_p4()                                    │
    │  binding_match                                      │
    │  → 保存 p4_result.json                              │
    │  → pending: 资源绑定确认                            │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P5: execute_p5()                                    │
    │  verify_execute + verify_recommendation             │
    │  → 保存 p5_result.json                              │
    │  → pending: 开工条件核验确认                        │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P6: execute_p6()                                    │
    │  run_a5_monitoring() → AsyncCollector + A5Agent     │
    │  → 保存 p6_result.json + a5_logs/                   │
    │  → 随机场景 A-E → 触发 A6 研判                       │
    └─────────────────────────────────────────────────────┘
          ↓
    ┌─────────────────────────────────────────────────────┐
    │ P7: execute_p7()                                    │
    │  risk_analyze (遍历事件)                             │
    │  → 保存 p7_result.json                              │
    │  → pending: 高风险事件确认                          │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P8: execute_p8()                                    │
    │  disposition_create (遍历风险事件)                   │
    │  → 保存 p8_result.json（向后兼容）                  │
    │  → 保存 P8/result.json（per-job 新约定，2026-08-20）│
    │  → middleware 自动归档 → per-job 双写 + dump        │
    │  → pending: 处置任务确认                            │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P9: execute_p9()                                    │
    │  closure_status + closure_verify + closure_report   │
    │  → 保存 p9_result.json                              │
    │  → pending: 关闭事件和作业确认                      │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认
    ┌─────────────────────────────────────────────────────┐
    │ P10: execute_p10()                                  │
    │  archive_task + archive_cases + archive_performance │
    │  → 保存 p10_result.json                             │
    │  → pending: 归档报告确认                            │
    └─────────────────────────────────────────────────────┘
          ↓ 用户确认 → 工作流完成
```

### 4.3 两层 HumanInTheLoop 机制

```
┌─────────────────────────────────────────────────────────────┐
│                    第一层: Workflow 层                       │
│  位置: main_agent.py 的 run_workflow()                      │
│  触发: 每个阶段执行完后检查 pending_confirmation            │
│  处理: 返回 status: "waiting"，前端弹窗等待用户确认         │
│  恢复: confirm_and_continue() 清除 pending 并继续执行       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    第二层: Agent 层                          │
│  位置: 各 Agent 的 create_xxx_agent_with_hitl()            │
│  触发: 工具调用前通过 HumanInTheLoopMiddleware 暂停         │
│  处理: 中断在 checkpointer，保存状态，恢复后可继续          │
│  配置: interrupt_on 字典控制哪些工具需要确认                │
│  恢复: confirm_and_continue() 调用 execute_p1(resume=True) │
│        → run_permit_agent_with_hitl(None, job_id)          │
│        → agent.invoke(None, config) 从中断点恢复            │
└─────────────────────────────────────────────────────────────┘
```

#### P1 Agent HITL 中断恢复实现

```python
# p1_permit_agent.py - Agent 注册表
_agent_registry: Dict[str, Any] = {}  # thread_id → Agent 实例

def is_agent_interrupted(thread_id: str) -> bool:
    """检查指定 thread_id 的 Agent 是否处于中断状态"""
    if thread_id not in _agent_registry:
        return False
    agent = _agent_registry[thread_id]
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    return bool(state and state.next)

# main_agent.py - execute_p1() 中使用
def execute_p1(job_id: str, resume: bool = False) -> dict:
    if is_agent_interrupted(job_id):
        # 从中断点恢复
        hitl_result = run_permit_agent_with_hitl(None, job_id)  # message=None
        ...
```

---

## 5. 文件持久化结构

```
data/
├── jobs/                              # 作业持久化根目录
│   └── {job_id}/                      # 每个作业单独目录
│       ├── application.json           # 用户填写的作业申请
│       ├── p1_result.json             # P1 阶段执行结果
│       ├── p2_result.json             # P2 阶段执行结果
│       ├── ...                        # ...
│       ├── p10_result.json            # P10 阶段执行结果
│       ├── logs.json                  # 执行日志数组
│       ├── confirmations.json         # 人工确认记录数组
│       └── permit.json                # 作业票信息
│
├── input/                             # 输入数据
│   ├── P1_data.json                   # P1 阶段测试数据
│   ├── P2_data.json                   # P2 阶段测试数据
│   ├── P3_data.json                   # P3 阶段测试数据
│   └── data.json                      # 主测试数据
│
├── output/                            # 输出报告
│   └── permit_TASK_*.json             # 作业票输出
│
└── system_prompt/                     # Agent 系统提示词
    ├── MAIN_AGENT_SYSTEM_PROMPT.md
    ├── P1_PERMIT_SYSTEM_PROMPT.md
    ├── P2_TASK_SYSTEM_PROMPT.md
    ├── P3_CONTEXT_SYSTEM_PROMPT.md
    ├── P4_BINDING_SYSTEM_PROMPT.md
    ├── P5_VERIFY_SYSTEM_PROMPT.md
    ├── P6_MONITOR_SYSTEM_PROMPT.md
    ├── P7_RISK_SYSTEM_PROMPT.md
    ├── P8_DISPOSITION_SYSTEM_PROMPT.md
    ├── P9_CLOSURE_SYSTEM_PROMPT.md
    └── P10_ARCHIVE_SYSTEM_PROMPT.md
```

---

## 6. API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/config` | GET | 获取 LLM 配置 |
| `/api/config` | POST | 保存 LLM 配置 |
| `/api/prompt/{stage}` | GET | 获取 Agent 系统提示词 |
| `/api/prompt/{stage}` | POST | 保存 Agent 系统提示词 |
| `/api/workflow/start` | POST | 启动工作流（异步） |
| `/api/workflow/confirm` | POST | 提交人工确认 |
| `/api/workflow/state` | GET | 查询工作流状态 |
| `/data/input/mock_job_content.json` | GET | 获取 Mock 数据 |

---

## 7. 关键模块说明

### agents/main_agent.py

主调度 Agent，包含：

| 函数/类 | 说明 |
|---------|------|
| `STAGE_EXECUTORS` | P1-P10 阶段执行器映射 {"P1": execute_p1, ...} |
| `execute_p1() ~ execute_p10()` | 各阶段执行函数 |
| `execute_p1(job_id, resume=False)` | P1 执行函数（支持 HITL 中断恢复） |
| `run_workflow(application, thread_id)` | 主工作流入口 |
| `confirm_and_continue(thread_id, stage, decision)` | 确认并继续（含 P1 HITL 恢复逻辑） |
| `get_workflow_state(thread_id)` | 获取工作流状态 |
| `save_job_application() / add_job_log() / save_confirmation()` | 持久化函数 |

### agents/p1_permit_agent.py ~ p10_archive_agent.py

各阶段独立 Agent，结构一致：

| 函数 | 说明 |
|------|------|
| `create_xxx_agent()` | 创建基础 Agent |
| `create_xxx_agent_with_hitl()` | 创建 HITL Agent |
| `run_xxx_agent(message)` | 运行 Agent |
| `xxx_demo(message, history)` | Gradio 兼容入口 |
| `tool1 / tool2 / ...` | @tool 装饰的工具函数 |

### web/server.py

HTTP 服务器：

| 函数 | 说明 |
|------|------|
| `load_env_config() / save_env_config()` | .env 配置读写 |
| `load_system_prompt(stage) / save_system_prompt(stage, content)` | 提示词读写 |
| `Handler.do_POST()` | 处理 POST 请求 |
| `Handler.do_GET()` | 处理 GET 请求 |

### web/app.js

前端交互：

| 函数 | 说明 |
|------|------|
| `startWorkflow()` | 启动工作流 |
| `startPollingWorkflow(jobId)` | 开始轮询（每2秒） |
| `pollWorkflowState(jobId)` | 轮询状态 |
| `confirmDecision(decision)` | 提交确认 |
| `showHitlModal(stage, data)` | 显示确认弹窗 |
| `renderWorkflowDiagram()` | 渲染工作流图 |

### agents/human_in_the_loop.py

HITL 管理器：

| 类/函数 | 说明 |
|---------|------|
| `HumanInTheLoop` | 确认管理器类 |
| `HumanConfirmRequest` | 确认请求数据结构 |
| `HumanConfirmResult` | 确认结果数据结构 |
| `get_hitl_manager()` | 获取全局管理器 |
| `STAGE_CONFIG` | 阶段配置（标题、描述、选项） |

---

## 8. 目录结构

```
industrial_internet_agents/
├── agents/                      # Agent 核心模块
│   ├── __init__.py             # 模块导出
│   ├── main_agent.py           # 主调度 Agent
│   ├── human_in_the_loop.py    # HITL 管理器
│   ├── p1_permit_agent.py      # P1 作业许可
│   ├── p2_task_agent.py        # P2 作业任务
│   ├── p3_context_agent.py     # P3 上下文理解
│   ├── p4_binding_agent.py     # P4 监测绑定
│   ├── p5_verify_agent.py      # P5 条件核验
│   ├── p6_monitor_agent.py     # P6 动态监测 + A5 Web 服务（共享端口 5002）
│   ├── p7_risk_agent.py        # P7 风险研判：A6 Agent + A6 路由 + LangChain 工具桩（挂载到 P6）
│   ├── p8_disposition_agent.py # P8 人机处置
│   ├── p9_closure_agent.py     # P9 闭环跟踪
│   ├── p10_archive_agent.py    # P10 归档复盘
│   └── utils.py                # 工具函数
│
├── model/                      # LLM 模型封装
│   └── chat_model.py          # MiniMax API 调用
│
├── utils/                      # 工具函数
│   └── agent_utils.py         # Agent 工具
│
├── web/                        # Web 前端
│   ├── index.html             # 主页面
│   ├── server.py              # HTTP 服务器
│   └── app.js                 # 前端交互逻辑
│
├── data/                       # 数据目录
│   ├── jobs/                  # 作业持久化
│   ├── input/                 # 输入数据
│   ├── output/                # 输出报告
│   └── system_prompt/         # 系统提示词
│
├── docs/                       # 文档
│   ├── architecture.md        # 技术架构（本文档）
│   ├── index.md               # Web 执行链路
│   └── agents/                # Agent 说明文档
│
├── system_prompt/              # Agent 系统提示词源文件
├── SDD.md                      # 软件设计说明书
├── CLAUDE.md                   # Claude Code 指导文件
└── requirements.txt            # Python 依赖
```

---

## 9. P8 per-job 路径规范（2026-08-20 新增）

P6/P7 已 per-job 化（`data/jobs/{job_id}/P6/`、`P7/`）；P8 在 2026-08-20 跟进，
保留**仓库级全局**长期记忆 + **per-job** 落地：

```
data/jobs/
├── _long_term/                          # 仓库级全局（行业追溯）
│   ├── p8_archive.index.json
│   └── p8_archive.json
└── {job_id}/                            # 单 job 数据
    ├── application.json                 # 申请
    ├── p7_result.json                   # P7 输出（主流程聚合）
    ├── p8_result.json                   # P8 输出快照（向后兼容保留 1 cycle）
    ├── P6/                              # P6 per-job 产物
    ├── P7/
    │   ├── a6_*.json                    # 每个 a6_event 单独文件
    │   └── ...
    └── P8/                              # P8 per-job 持久化（2026-08-20 新增）
        ├── working_memory.json          # 工作记忆 snapshot
        ├── archived.json                # per-job 归档副本（{pid → archived dict}）
        └── result.json                  # execute_p8 主流程结果
```

**写入时机**：

| 文件 | 写入入口 |
|------|---------|
| `_long_term/p8_archive{,.index}.json` | `A7.storage.p8_long_term.save_archived_job`（必写） |
| `{job_id}/P8/archived.json` | 同上 `save_archived_job(job_id=...)` 触发 per-job 双写 |
| `{job_id}/P8/working_memory.json` | `P8ArchiveMiddleware.after_model`（终态归档后）<br>+ `run_disposition_agent` invoke end flush |
| `{job_id}/P8/result.json` | `agents.main_agent.execute_p8` 结束 |
| `{job_id}/p8_result.json` | 同上（向后兼容；1 cycle 保留期） |

**Bot 模式说明**：chat_reply 解析消息正文 `[job_id=...]` 前缀；无前缀 → `job_id=None`
→ middleware 不触发 per-job 双写 + working_memory 不 dump（临时会话无需持久化）。

**详见**：[docs/P8_人机协同处置_文件组织与职责.md § 5.1.5](P8_人机协同处置_文件组织与职责.md#515p5.1.5)
及 [tests/test_a7_p8_per_job_persistence.py](../tests/test_a7_p8_per_job_persistence.py)（17 个测试）。
