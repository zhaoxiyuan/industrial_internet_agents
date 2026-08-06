# P6: 作业过程动态监测 Agent (p6_monitor_agent)

## 概述

**作业过程动态监测专家**，负责实时监测作业过程中的风险事件。

## 主要功能

1. 持续获取视频、传感器、定位数据
2. 识别违章及条件变化
3. 时序聚合避免单帧误报
4. 跨镜头追踪同一目标

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_monitor_agent(message)` | 运行 P6 Agent |
| `monitor_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `monitor_start` | 用户启动监测 | 启动监测会话（幂等） | ✅ 需要确认 |
| `monitor_stop` | 用户停止监测 | 停止监测会话 | ✅ 需要确认 |
| `monitor_status` | 用户查看状态 | 查看监测状态 | ❌ 自动批准 |
| `monitor_events` | 用户获取风险事件 | 获取候选风险事件（JSON Lines） | ❌ 自动批准 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "monitor_start": True,    # 启动监测需要确认
        "monitor_stop": True,     # 停止监测需要确认
        "monitor_status": False,   # 查询状态自动批准
        "monitor_events": False,   # 查询事件自动批准
    }
)
```

## 采样策略

| 状态 | 采样频率 |
|------|----------|
| 正常状态 | 1帧/秒 |
| 异常检测 | 5帧/秒 + 全量存储 |
| 告警触发 | 连续10帧异常才上报 |

## 时序聚合

- **单帧误报过滤**：同位置、同类型事件需连续N帧确认
- **跨镜头追踪**：同一目标多摄像头追踪

## 幂等性

`monitor_start` 是幂等的，重复调用不会重启会话。

## 文件位置

`agents/p6_monitor_agent.py`
