---
name: ai-ready
description: This skill should be used when the user asks to "show me the project knowledge base", "index modules", "find agents or tools", "look up how something works", "understand the codebase structure", "find a specific file or function", "see what tools are available", or needs to navigate/modify/verify code in this industrial_internet_agents project. Also use when the user wants to "modify", "edit", "change", "add", "update", "fix", or "implement" any agent, tool, workflow, or module in this project, or asks about where to make changes. Use when user mentions "P1-P10", "agent", "workflow", "HITL", "tools", "P1 Permit", "P8 Disposition", or any stage name, or wants to explore the project architecture.
---

# AI-Ready: 项目知识库

工业互联网边缘智能 Agent 项目的知识库，提供渐进式模块索引、工具查找和文件路由。

## 使用方式

```
/ai-ready              # 显示模块总览
/ai-ready agents       # 查看所有 Agent 详情
/ai-ready tools        # 查看所有工具清单
/ai-ready <agent>      # 查看特定 Agent (p1-p10, main)
/ai-ready tool <name>  # 查看特定工具详情
/ai-ready path <file>  # 查找文件路径
/ai-ready workflow     # 查看工作流机制
/ai-ready hitl         # 查看 HITL 中断机制
/ai-ready verify <mod> # 验证模块语法/导入
```

## 快速索引

| 模块 | 路径 | 说明 |
|------|------|------|
| [agents](#agents-模块) | `agents/` | P1-P10 Agent 实现 |
| [web](#web-模块) | `web/` | Gradio Web 前端 |
| [docs](#docs-文档) | `docs/` | 架构文档 |
| [A5/A6/A7](#子系统) | `A5/`, `A6/`, `A7/` | 边缘智能子系统 |

---

## Agents 模块

### 目录结构
```
agents/
├── __init__.py              # 模块导出
├── main_agent.py            # P1-P10 工作流协调器
├── p1_permit_agent.py       # P1: 作业预约、JSA、作业票
├── p2_task_agent.py         # P2: 任务实例化
├── p3_context_agent.py      # P3: 上下文构建
├── p4_binding_agent.py      # P4: 资源绑定
├── p5_verify_agent.py       # P5: 作业前验证
├── p6_monitor_agent.py      # P6: 实时监控 (FastAPI)
├── p7_risk_agent.py         # P7: 风险评估 (A6)
├── p8_disposition_agent.py  # P8: 人机协同处置
├── p9_closure_agent.py      # P9: 闭环跟踪
├── p10_archive_agent.py     # P10: 归档与案例挖掘
├── cli/                     # CLI 入口
├── hitl/                    # HumanInTheLoop 机制
├── model/                   # LLM 模型封装
├── system_prompt/           # System Prompt 文件
├── utils/                   # 工具函数
└── workflow/                # 工作流状态持久化
```

### Agent 工厂函数速查

| Agent | 基础版本 | HITL 版本 | 入口文件 |
|-------|----------|-----------|----------|
| Main | `create_main_agent()` | - | `main_agent.py` |
| P1 | `create_permit_agent()` | `create_permit_agent_with_hitl()` | `p1_permit_agent.py` |
| P2 | `create_task_agent()` | `create_task_agent_with_hitl()` | `p2_task_agent.py` |
| P3 | `create_context_agent()` | `create_context_agent_with_hitl()` | `p3_context_agent.py` |
| P4 | `create_binding_agent()` | `create_binding_agent_with_hitl()` | `p4_binding_agent.py` |
| P5 | `create_verify_agent()` | `create_verify_agent_with_hitl()` | `p5_verify_agent.py` |
| P8 | `create_disposition_agent()` | `create_disposition_agent_with_hitl()` | `p8_disposition_agent.py` |
| P9 | `create_closure_agent()` | `create_closure_agent_with_hitl()` | `p9_closure_agent.py` |
| P10 | `create_archive_agent()` | `create_archive_agent_with_hitl()` | `p10_archive_agent.py` |

### P1-P10 工具清单（快速参考）

**P1 Permit** (`p1_permit_agent.py`): `permit_submit`, `jsa_analyze`, `permit_generate_draft`, `permit_check`
**P2 Task** (`p2_task_agent.py`): `task_list`, `task_get`, `task_instance_create`, `task_subscribe`
**P3 Context** (`p3_context_agent.py`): `context_build`, `context_validate`, `context_history`
**P4 Binding** (`p4_binding_agent.py`): `binding_match`, `binding_status`, `binding_confirm`, `binding_request_manual`
**P5 Verify** (`p5_verify_agent.py`): `verify_checklist`, `verify_execute`, `verify_recommendation`
**P6 Monitor** (`p6_monitor_agent.py`): `monitor_start`, `monitor_events`
**P7 Risk** (`p7_risk_agent.py`): `risk_analyze`, `risk_list`
**P8 Disposition** (`p8_disposition_agent.py`): `disposition_create`, `disposition_confirm`, `disposition_status`, `disposition_list`, `recall_jobs`
**P9 Closure** (`p9_closure_agent.py`): `closure_status`, `closure_verify`, `closure_report`, `closure_close`
**P10 Archive** (`p10_archive_agent.py`): `archive_task`, `archive_cases`, `archive_performance`, `archive_suggestions`
**Main** (`main_agent.py`): `start_workflow_tool`, `execute_stage_tool`, `get_status_tool`, `confirm_stage_tool`, `list_pending_tool`

---

## Web 模块

```
web/
├── server.py           # HTTP 服务器 (port 8080)
├── app.js              # 前端 JavaScript
├── index.html          # 主页面
├── api/
│   ├── config.py       # LLM/提示词配置
│   └── workflow.py     # 工作流 REST API
├── hitl_app.py         # HITL 可视化 Dashboard
└── web_demo.py         # Gradio ChatInterface Demo
```

### 关键端口
| 端口 | 服务 |
|------|------|
| 8080 | HTTP Server + REST API |
| 8081 | WebSocket 状态广播 |
| 8082 | WebSocket 日志广播 |
| 5002 | FastAPI (P6 监控 + A6 路由) |

---

## 数据目录

```
data/jobs/{job_id}/
├── application.json         # 用户作业申请
├── p1_result.json ~ p10_result.json  # 各阶段结果
├── permit.json              # 作业票数据
├── logs.json                # 执行日志
└── confirmation/            # 人工确认记录
```

---

## 子系统

| 子系统 | 路径 | 说明 |
|--------|------|------|
| A5 | `A5/` | 实时监控（摄像头/CV/传感器） |
| A6 | `A6/` | 风险评估 Agent |
| A7 | `A7/` | 长期记忆 + 飞书通知 |

---

## 工作流机制

### P1-P10 执行顺序
```
P1(许可) → P2(任务) → P3(上下文) → P4(绑定) → P5(验证)
    → P6(监控) → P7(风险) → P8(处置) → P9(闭环) → P10(归档)
```

### 文件传递协调
- 阶段结果: `data/jobs/{job_id}/p{n}_result.json`
- 作业申请: `data/jobs/{job_id}/application.json`
- 作业票: `data/jobs/{job_id}/permit.json`

### 两层 HITL
1. **Workflow 层** (`main_agent.py`): `check_and_request_confirmation()` 阶段边界暂停
2. **Agent 层** (`HumanInTheLoopMiddleware`): 工具调用前中断

---

## 工具响应格式

所有工具返回标准 JSON:
```json
{
    "schema_version": "1.0",
    "command": "tool_name",
    "timestamp": "ISO8601",
    "result": {...},
    "errors": []
}
```

---

## 验证命令

```bash
# 语法检查
python -m py_compile agents/p1_permit_agent.py

# 导入检查
python -c "from agents import create_permit_agent; print('OK')"

# 启动 Web 服务
python web/server.py

# 启动 P6 监控服务
python agents/p6_monitor_agent.py --port 5002
```

---

## 详细参考

- **Agent 详情**: 阅读 `references/agents.md`
- **工具签名**: 阅读 `references/tools.md`
- **工作流/HITL**: 阅读 `references/workflow.md`
- **文档索引**: 阅读 `references/docs.md`
