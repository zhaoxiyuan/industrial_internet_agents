# P10: 归档与复盘 Agent (p10_archive_agent)

## 概述

**归档与复盘专家**，负责归档全过程记录并挖掘误报、漏报、规则冲突案例。

## 主要功能

1. 归档作业票证、视频证据、风险事件、处置记录和报告
2. 挖掘误报、漏报、规则冲突案例
3. 分析处置效果
4. 生成规则优化建议

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_archive_agent(message)` | 运行 P10 Agent |
| `archive_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `archive_task` | 用户归档任务 | 归档任务数据 | ✅ 需要确认 |
| `archive_cases` | 用户挖掘案例 | 挖掘误报/漏报/规则冲突 | ❌ 自动批准 |
| `archive_performance` | 用户分析性能 | 分析处置效果 | ❌ 自动批准 |
| `archive_suggestions` | 用户生成建议 | 生成规则优化建议 | ✅ 需要确认 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "archive_task": True,           # 归档任务需要确认
        "archive_cases": False,         # 挖掘案例自动批准
        "archive_performance": False,   # 分析性能自动批准
        "archive_suggestions": True,    # 生成建议需要确认
    }
)
```

## 归档内容

- 作业票证（结构化 + 扫描件）
- 视频证据片段
- 风险事件记录
- 处置全记录
- 作业报告

## 案例挖掘类型

| 类型 | 说明 |
|------|------|
| `misdetection` | 检测模型误报 |
| `missed` | 人工发现的风险事件 |
| `rule_conflict` | 同场景不同规则的冲突点 |

## 知识沉淀

- 案例摘要Embedding → 向量数据库
- 规则冲突报告 → 规则管理系统
- 模型优化建议 → 模型训练平台

## 文件位置

`agents/p10_archive_agent.py`
