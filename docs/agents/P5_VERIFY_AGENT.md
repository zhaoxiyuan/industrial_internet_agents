# P5: 作业前条件核验 Agent (p5_verify_agent)

## 概述

**作业前条件核验专家**，负责在作业开始前核验各项安全措施是否落实。

## 主要功能

核验项目包括：
1. 隔离措施（隔离阀、电源切断等）
2. 警戒标识（警戒带、警示牌等）
3. 消防器材（灭火器、消防栓等）
4. 气体检测（可燃气体、有毒气体、氧气含量）
5. 人员资质（证书有效期、作业授权）
6. PPE配备（呼吸器、防护服等）

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_verify_agent(message)` | 运行 P5 Agent |
| `verify_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `verify_checklist` | 用户请求生成清单 | 按作业类型生成核验检查清单 | ❌ 自动批准 |
| `verify_execute` | 用户执行核验 | 执行核验，返回核验结果 | ✅ 需要确认 |
| `verify_recommendation` | 用户请求开工建议 | 获取开工建议和决策 | ✅ 需要确认 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "verify_checklist": False,      # 查询清单自动批准
        "verify_execute": True,         # 执行核验需要确认
        "verify_recommendation": True,  # 开工建议需要确认
    }
)
```

## 核验结果枚举

| 结果 | 说明 |
|------|------|
| `PASS` | 符合 |
| `PENDING` | 待确认 |
| `FAIL` | 不符合 |
| `N/A` | 不适用 |

## 文件位置

`agents/p5_verify_agent.py`
