# 主调度 Agent (main_agent)

## 概述

**主调度中心**，负责协调 P1-P10 完整作业流程，是整个边缘智能作业监测系统的核心入口。

## 主要功能

- 接收作业申请，启动 P1-P10 完整工作流
- 实时查询工作流状态
- 处理人工确认请求
- 管理作业生命周期：启动 → 执行 → 监控 → 闭环 → 归档

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_main_agent(message)` | 运行主 Agent，返回字符串结果 |
| `run_main_agent_with_hitl(message, thread_id)` | 运行主 Agent（支持 HITL 中断），返回 dict |
| `main_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 |
|------|----------|------|
| `start_workflow_tool` | 用户提交作业申请 | 启动 P1-P10 工作流 |
| `get_status_tool` | 用户询问任务进度 | 查询工作流当前状态 |
| `confirm_stage_tool` | 用户确认阶段操作 | 确认某阶段，使工作流继续 |
| `list_pending_tool` | 用户询问待确认项 | 列出所有待确认阶段 |
| `get_stage_config_tool` | 用户了解阶段配置 | 获取阶段详细配置和选项 |
| `cancel_workflow_tool` | 用户取消工作流 | 取消正在运行的工作流 |

## HITL 支持

```python
# 无 HITL
agent = create_main_agent()

# 支持 HITL（工具调用前暂停等待确认）
agent = create_main_agent_with_hitl()

# HITL 中断哪些工具
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "start_workflow_tool": True,
        "confirm_stage_tool": True,
        "cancel_workflow_tool": True,
    }
)
```

## 执行链路

```
用户消息
    ↓
LLM 理解意图
    ↓
调用对应工具
    ↓
工具执行 main_agent.py 中的函数
    ↓
P1 → P2 → P3 → ... → P10
    ↓
每阶段可能暂停等待人工确认
```

## 持久化

- `save_job_application()` - 保存作业申请
- `save_job_state()` - 保存工作流状态快照
- `add_job_log()` - 追加执行日志
- `save_confirmation()` - 保存确认记录
- `get_job_history()` - 获取作业完整历史
- `list_all_jobs()` - 列出所有作业

## 异常处理机制

工作流执行过程中，各阶段子 Agent 可能抛出异常。MainAgent 提供完整的异常捕获和上报机制：

### 异常处理流程

```
阶段执行 → 正常完成 → pending_confirmation? → 等待确认 / 进入下一阶段
         ↓
      抛出异常 → 更新 error 状态 → 广播状态 → 记录日志 → 中断工作流
```

### 异常处理行为

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `logger.exception()` | 打印完整堆栈日志 |
| 2 | `update_workflow_status()` | 更新 `{stage}_status="error"`, `main_agent.status="error"` |
| 3 | `_broadcast_state()` | 广播状态到前端 WebSocket |
| 4 | `add_job_log()` | 记录 `action="stage_error"` 日志 |
| 5 | `return {...status:"error"}` | 中断工作流，返回错误信息 |

### 错误响应格式

```python
{
    "job_id": "xxx",
    "current_stage": "P3",           # 异常发生的阶段
    "status": "error",
    "error": "具体的异常信息",
    "confirmed_stages": ["P1", "P2"]  # 异常前已完成的阶段
}
```

### 涉及的函数

- `run_workflow()` - 启动工作流时的异常处理
- `confirm_and_continue()` - 确认后继续执行时的异常处理
- `_confirm_and_continue_async()` - 异步模式下的异常处理（后台线程）

## 工作流状态

```python
MainAgentState = {
    "application": dict,           # 作业申请
    "task_id": str,               # 任务ID
    "permit_draft_id": str,       # 作业票草稿ID
    "jsa_result": dict,           # JSA分析结果
    "current_stage": str,         # 当前阶段 P1-P10
    "confirmed_stages": dict,     # 已确认的阶段
    "pending_confirmations": list, # 待确认阶段列表
    "status": str,                # running | waiting | completed | error
    # ... P2-P10 各阶段状态
}
```

### 阶段状态流转

```
pending → running → waiting(需确认) → completed
                     ↓
                   error(异常中断)
```

## 文件位置

`agents/main_agent.py`
