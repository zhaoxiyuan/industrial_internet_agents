# P8: 人机协同处置 Agent (p8_disposition_agent)

## 概述

**人机协同处置专家**，负责创建和跟踪处置任务，按角色与权限推送责任人。

## 主要功能

1. 创建处置任务
2. 按风险等级推送（消息/短信/电话）
3. 形成整改、暂停、复核或升级建议
4. 跟踪处置状态

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_disposition_agent(message)` | 运行 P8 Agent |
| `disposition_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `disposition_create` | 用户创建处置任务 | 创建处置任务 | ✅ 需要确认 |
| `disposition_confirm` | 用户确认处置 | 人工确认处置任务 | ✅ 需要确认 |
| `disposition_status` | 用户查看状态 | 查看处置状态 | ❌ 自动批准 |
| `disposition_list` | 用户列出任务 | 列出所有处置任务 | ❌ 自动批准 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "disposition_create": True,    # 创建处置任务需要确认
        "disposition_confirm": True,    # 确认处置需要确认
        "disposition_status": False,     # 查询状态自动批准
        "disposition_list": False,      # 查询列表自动批准
    }
)
```

## 推送策略

| 风险等级 | 推送方式 | 处理时限 |
|----------|----------|----------|
| `LOW` | 消息推送 | 24h |
| `MEDIUM` | 短信+消息 | 4h |
| `HIGH` | 电话+短信+消息 | 1h |
| `CRITICAL` | 电话直达 | 实时 |

## 操作类型

| 操作 | 说明 |
|------|------|
| `approve` | 批准 |
| `reject` | 否决 |
| `escalate` | 升级 |
| `rectify` | 整改 |
| `pause` | 暂停作业 |
| `resume` | 恢复作业 |

## 人工控制点

- 高风险事件：必须人工确认
- 写入操作：必须人工确认
- 暂停作业：必须人工确认
- 恢复作业：必须人工确认

## 文件位置

`agents/p8_disposition_agent.py`
