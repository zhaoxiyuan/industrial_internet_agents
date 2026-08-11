# P6: 作业过程动态监测 Agent (基于 A5)

## 概述

**基于 A5 的作业过程动态监测**，完全替换原有 monitor_start/stop/status/events 工具，使用 A5 的 AsyncCollector + A5Agent 进行真实监测。

## 架构

```
execute_p6(job_id)
  ├── 1. 随机选择场景 A-E
  ├── 2. AsyncCollector 生成 30 秒 snapshot 数据
  │     └── 每秒生成: cv_*.json / sensor_*.json / position_*.json / snapshot_*.json
  ├── 3. A5Agent 轮询处理 snapshot 文件
  │     ├── processing_batch_*.json 标记处理中
  │     ├── raw_event_*.json 处理成功
  │     └── error_*.json 处理失败
  ├── 4. 有事件时触发 A6 研判
  └── 5. 汇总 raw_event_*.json 为 candidate_events
```

## 场景定义

| 场景 | 违规类型 | 说明 |
|------|----------|------|
| A | 无 | 正常作业基线 |
| B | 头盔缺失 | 工人 P7 摘头盔（单项 PPE） |
| C | 气体上升 | 可燃气体浓度升高（环境） |
| D | 监护人离岗 | 监护人 P11 离开岗位 |
| E | 多人违规 | P7+P8+P11 同时多种违规 |

## 日志文件结构

```
data/jobs/{job_id}/a5_logs/
├── cv_*.json                  # CV 检测原始数据
├── sensor_*.json              # 传感器数据
├── position_*.json            # 定位数据
├── snapshot_*.json            # 每秒聚合快照
├── processing_batch_*.json    # Agent 处理中标记
├── raw_event_*.json           # 处理完成（有事件）
└── error_*.json               # 处理失败
```

## 状态机

```
snapshot_*.json → processing_batch_*.json → raw_event_*.json (成功)
                                     ↓
                                  error_*.json (失败)
```

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_a5_monitoring(job_id, work_permit, duration_sec)` | 运行 A5 完整监测流程（异步） |
| `map_a5_events_to_p6(raw_events, job_id)` | 将 A5 事件映射为 P6 格式 |
| `get_a5_log_dir(job_id)` | 获取 A5 日志目录路径 |

## 输出格式

`p6_result.json` 中的 `candidate_events` 字段：

```json
{
  "event_type": "candidate_event",
  "event_id": "A5-P7-...",
  "description": "P7 在 ... 期间护目镜持续缺失...",
  "confidence": 0.9,
  "evidence": [{"source": "A5", "type": "PPE缺失", "person": {...}}],
  "severity": "PENDING_A6",
  "timestamp": "...",
  "type": "PPE缺失",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"}
}
```

**注意：** A5 不判定 severity 字段，设为 `"PENDING_A6"`，由 A6 研判后补充。

## A6 研判触发

当 A5 产生告警事件时，调用：
```
POST http://localhost:5002/api/a6/process/{event_id}
Body: {"events": [event_data]}
```

## 文件位置

`agents/p6_monitor_agent.py`
