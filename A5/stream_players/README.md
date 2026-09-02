# 流式播放 — 设计说明

> **定位**:模拟 CV / 传感器 / 定位 3 个数据源 + 集中采集器
> **文件**:`mock_cv_player.py`, `mock_sensor_stream.py`, `mock_positioning_stream.py`, `async_collector.py`
> **状态**:已完成

---

## 一、4 个文件

| 文件 | 类型 | 频率 | 输出格式 |
|---|---|---|---|
| `mock_cv_player.py` | 异步生成器 | 25 FPS | 每帧 3 条检测日志(3 人) |
| `mock_sensor_stream.py` | 异步生成器 | 1 Hz | 每秒 1 条读数(3 传感器) |
| `mock_positioning_stream.py` | 异步生成器 | 1 Hz | 每秒 1 条位置(3 人) |
| `async_collector.py` | 协调器 | — | 3 流并行 + 落盘 + 防未来 + 查询接口 |

---

## 二、AsyncCollector 核心 API

```python
from 流式播放.async_collector import AsyncCollector

collector = AsyncCollector(scenario="B", log_dir="logs/run")

# 生命周期
await collector.start()
# ... 主循环 ...
await collector.stop()

# 落盘接口(主循环每秒调)
await collector.flush_second(sec)

# 查询接口(智能体用)
collector.query_raw_logs(start_wall, end_wall, source_type, person_id)
collector.get_snapshot(wall_time)

# 防未来
collector.set_agent_now(wall_time)
```

---

## 三、落盘规则

- **每次新建文件,永不覆盖**:`{type}_{ts}_T{sec:02d}.json`
- **4 类文件**:cv / sensor / position / snapshot
- **每场景 120 个文件**(4 类 × 30 秒)

---

## 四、wall_time 约定

```
start_wall = collector 启动时刻的现实时间
scenario_sec = (wall_time - start_wall)  # 1:1 实时

工具方法:
  _wall_to_sec(wall_time_str) → 场景秒数(float)
  _sec_to_wall(sec)            → 现实时间(ISO)
```

---

## 五、防未来机制

```python
collector.set_agent_now("2026-08-04T14:32:12.000")

# ✅ OK
collector.query_raw_logs("14:32:00", "14:32:12", ...)

# ❌ 拒绝
collector.query_raw_logs("14:32:12", "14:32:13", ...)
# 返回 [{"error": "end_wall > agent_now"}]
```
