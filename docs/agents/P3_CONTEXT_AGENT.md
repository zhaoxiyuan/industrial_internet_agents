# P3: 作业上下文理解与标准化 Agent (p3_context_agent)

## 概述

**作业上下文理解专家**，负责构建标准化的作业上下文包。

## 主要功能

聚合作业类型、区域、设备、介质、人员、资质、风险、措施、时间、关联作业、数据源等 **11 个维度**信息。

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_context_agent(message)` | 运行 P3 Agent |
| `context_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `context_build` | 用户请求构建上下文 | 聚合11维信息生成标准上下文包 | ✅ 需要确认 |
| `context_validate` | 用户请求验证上下文 | 验证上下文完整性 | ✅ 需要确认 |
| `context_history` | 用户请求查看历史 | 获取上下文变更历史 | ❌ 自动批准 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "context_build": True,       # 构建上下文需要确认
        "context_validate": True,    # 验证需要确认
        "context_history": False,    # 查询历史自动批准
    }
)
```

## 标准上下文包 11 维

| 维度 | 说明 |
|------|------|
| 作业对象 | 作业内容、类型 |
| 区域 | 作业所在区域 |
| 设备 | 相关设备列表 |
| 介质 | 作业涉及介质 |
| 人员 | 作业人员信息 |
| 资质 | 人员资质证书 |
| 风险 | 识别出的风险 |
| 措施 | 对应风险措施 |
| 时间 | 计划开始结束时间 |
| 关联作业 | 关联的其他作业 |
| 数据源 | 数据来源系统 |

## 数据溯源

每项数据记录：
- 来源系统
- 获取时间
- 有效期

## 文件位置

`agents/p3_context_agent.py`
