# Agent 文档索引

## Agent 说明书

| Agent | 文件 | 说明 |
|-------|------|------|
| [主调度 Agent](MAIN_AGENT.md) | `main_agent.py` | P1-P10 调度管理中心 |
| [P1 作业许可](P1_PERMIT_AGENT.md) | `p1_permit_agent.py` | 作业预约、JSA分析与作业票 |
| [P2 作业任务](P2_TASK_AGENT.md) | `p2_task_agent.py` | 作业任务获取与实例化 |
| [P3 上下文理解](P3_CONTEXT_AGENT.md) | `p3_context_agent.py` | 作业上下文理解与标准化 |
| [P4 监测绑定](P4_BINDING_AGENT.md) | `p4_binding_agent.py` | 监测资源绑定与数据关联 |
| [P5 条件核验](P5_VERIFY_AGENT.md) | `p5_verify_agent.py` | 作业前条件核验 |
| [P6 动态监测](P6_MONITOR_AGENT.md) | `p6_monitor_agent.py` | 作业过程动态监测 |
| [P7 风险研判](P7_RISK_AGENT.md) | `p7_risk_agent.py` | 风险研判与分级 |
| [P8 人机处置](P8_DISPOSITION_AGENT.md) | `p8_disposition_agent.py` | 人机协同处置 |
| [P9 闭环跟踪](P9_CLOSURE_AGENT.md) | `p9_closure_agent.py` | 闭环跟踪与报告 |
| [P10 归档复盘](P10_ARCHIVE_AGENT.md) | `p10_archive_agent.py` | 归档与复盘 |
| [HumanInTheLoop](HUMAN_IN_THE_LOOP.md) | `human_in_the_loop.py` | 人工确认管理 |

## 通用文档模板

每个 Agent 说明书包含：

1. **概述** - Agent 职责说明
2. **主要功能** - 核心功能列表
3. **入口函数** - `run_*_agent()`, `*_demo()`
4. **工具定义** - 所有 @tool 装饰器定义的工具
5. **HITL 支持** - HumanInTheLoopMiddleware 配置
6. **执行链路** - 工具调用顺序
7. **内部状态** - State 定义

## HITL 标记说明

| 标记 | 说明 |
|------|------|
| ✅ 需要确认 | 工具调用前会暂停等待人工确认 |
| ❌ 自动批准 | 工具自动执行，无需确认 |

## 架构图

```
┌─────────────────────────────────────────────────────────┐
│                     主调度 Agent                         │
│                  (main_agent.py)                         │
│  start_workflow / get_status / confirm_stage / ...      │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│                    Workflow 引擎                         │
│                  (main_agent.py)                         │
│     P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10 │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│                   各阶段 Agent                           │
│  p1_permit_agent / p2_task_agent / ... / p10_archive   │
│         每个 Agent 有自己的工具集和 HITL 配置            │
└─────────────────────────────────────────────────────────┘
```
