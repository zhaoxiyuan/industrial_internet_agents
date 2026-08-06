# P4: 监测资源绑定与数据关联 Agent (p4_binding_agent)

## 概述

**监测资源绑定专家**，负责将摄像头、传感器、定位设备与作业任务关联。

## 主要功能

1. 固定摄像头区域覆盖分析
2. 移动设备流动性动态调整
3. 传感器点位匹配（同区域、同介质）
4. 人员定位与工卡关联
5. 无法自动匹配时触发人工补充

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_binding_agent(message)` | 运行 P4 Agent |
| `binding_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `binding_match` | 用户请求匹配资源 | 自动匹配摄像头、传感器、定位 | ✅ 需要确认 |
| `binding_status` | 用户查看绑定状态 | 查看当前绑定详情 | ❌ 自动批准 |
| `binding_confirm` | 用户确认绑定 | 人工确认资源绑定 | ✅ 需要确认 |
| `binding_request_manual` | 无法自动匹配时 | 请求人工补充资源 | ✅ 需要确认 |

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "binding_match": True,            # 绑定匹配需要确认
        "binding_status": False,          # 查询状态自动批准
        "binding_confirm": True,          # 确认绑定需要确认
        "binding_request_manual": True,   # 请求人工绑定需要确认
    }
)
```

## 匹配策略

| 资源类型 | 策略 |
|----------|------|
| 固定摄像头 | 区域覆盖分析，选择最近且覆盖最好的N个 |
| 移动设备 | 根据作业流动性动态调整 |
| 传感器 | 选择同区域、同介质类型点位 |
| 人员定位 | 作业人员工卡与定位基站关联 |

## 文件位置

`agents/p4_binding_agent.py`
