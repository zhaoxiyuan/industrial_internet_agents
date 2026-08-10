# P9: 闭环跟踪与报告 Agent (p9_closure_agent)

## 概述

**闭环跟踪与报告专家**，负责跟踪整改状态、复核处置结果并生成作业过程报告。

## 主要功能

1. 跟踪整改状态
2. 复核处置结果
3. 执行闭环完整性检查
4. 生成作业过程报告
5. 关闭事件和作业

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_closure_agent(message)` | 运行 P9 Agent |
| `closure_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `closure_status` | 用户跟踪闭环状态 | 跟踪当前闭环状态 | ❌ 自动批准 |
| `closure_verify` | 用户执行完整性检查 | 执行闭环完整性检查 | ✅ 需要确认 |
| `closure_report` | 用户生成报告 | 生成作业过程报告 | ✅ 需要确认 |
| `closure_close` | 用户关闭事件和作业 | 关闭事件和作业 | ✅ 需要确认 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "closure_status": False,   # 查询状态自动批准
        "closure_verify": True,     # 验证需要确认
        "closure_report": True,     # 生成报告需要确认
        "closure_close": True,      # 关闭作业需要确认
    }
)
```

## 完整性检查项

| 检查项 | 说明 |
|--------|------|
| 处置记录完整性 | 所有处置记录完整 |
| 证据材料齐全性 | 视频证据完整 |
| 复核签字有效性 | 复核签字有效 |
| 时间线连续性 | 时间线连续 |

## 报告内容

- 作业基本信息
- 核验结果汇总
- 监测事件时间线
- 风险处置记录
- 证据索引

## 文件位置

`agents/p9_closure_agent.py`
