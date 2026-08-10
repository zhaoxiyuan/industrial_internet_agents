# P2: 作业任务获取与实例化 Agent (p2_task_agent)

## 概述

**作业任务管理专家**，负责处理作业任务的获取、列表查询和实例创建。

## 主要功能

1. 按时间、区域、状态筛选和列出作业任务
2. 获取任务详情
3. 创建唯一任务实例，确保幂等性
4. 订阅任务变更事件

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_task_agent(message)` | 运行 P2 Agent |
| `task_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `task_list` | 用户列出任务 | 支持按区域和状态筛选 | ❌ 自动批准 |
| `task_get` | 用户获取任务详情 | 获取任务详细信息 | ❌ 自动批准 |
| `task_instance_create` | 用户创建任务实例 | 幂等操作 | ✅ 需要确认 |
| `task_subscribe` | 用户订阅任务变更 | 订阅事件流 | ❌ 自动批准 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "task_list": False,             # 查询自动批准
        "task_get": False,              # 查询自动批准
        "task_instance_create": True,    # 创建需要确认
        "task_subscribe": False,         # 订阅自动批准
    }
)
```

## Task ID 格式

```
{作业类型}_{区域代码}_{时间戳}_{序号}
例如: TASK-WELD-01-20260804-001
```

## 幂等性

`task_instance_create` 是幂等操作：
- 相同 `permit_id` 重复调用返回已创建的实例
- 返回 `idempotent: True`

## 文件位置

`agents/p2_task_agent.py`
