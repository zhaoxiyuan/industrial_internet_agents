# P7: 风险研判与分级 Agent (p7_risk_agent)

## 概述

**风险研判与分级专家**，负责综合研判候选风险事件并确定风险等级。

## 主要功能

1. 多源证据融合（视频 + 传感器 + 定位 + 规则）
2. 企业风险规则库匹配
3. Embedding 相似度搜索相似历史事件
4. 规则 + 模型 + 历史综合评分

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_risk_agent(message)` | 运行 P7 Agent |
| `risk_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `risk_analyze` | 用户请求分析风险 | 综合研判候选事件 | ✅ 需要确认 |
| `risk_grade` | 用户请求计算等级 | 计算风险等级评分 | ✅ 需要确认 |
| `risk_cases` | 用户查询相似案例 | 查询相似历史案例 | ❌ 自动批准 |
| `risk_list` | 用户列出风险事件 | 列出任务所有风险事件 | ❌ 自动批准 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "risk_analyze": True,   # 风险分析需要确认
        "risk_grade": True,     # 风险评级需要确认
        "risk_cases": False,     # 查询案例自动批准
        "risk_list": False,     # 查询列表自动批准
    }
)
```

## 风险等级定义

| 等级 | 说明 |
|------|------|
| `LOW` | 低风险（绿色） |
| `MEDIUM` | 中风险（黄色） |
| `HIGH` | 高风险（橙色） |
| `CRITICAL` | 重大风险（红色） |

## 研判流程

```
候选事件
    ↓
多源证据融合（视频 + 传感器 + 定位 + 规则）
    ↓
规则库匹配
    ↓
相似案例搜索（Embedding）
    ↓
综合评分
    ↓
风险等级
```

## 文件位置

`agents/p7_risk_agent.py`
