# 主事件循环 — 实现说明(待确认)

> **定位**:固定流程(AsyncCollector),不涉及智能体决策
> **编制日期**:2026-08-04

---

## 一、这是什么?

对照 README 架构图:

```
═══════════════════════════════════════════════════════

   ┌──────────┐    ┌──────────┐    ┌──────────┐
   │ CV 流    │    │ 传感器流 │    │ 定位流   │
   │(25 FPS)  │    │ (1 Hz)   │    │ (1 Hz)   │
   └────┬─────┘    └────┬─────┘    └────┬─────┘
        │               │               │
        └───────┬───────┴───────┬───────┘
                ↓               ↓
        ┌──────────────────────────┐
        │  固定流程(AsyncCollector) │  ← ★ 这次要做的
        └────────────┬─────────────┘
                     ↓
              日志/快照(智能体输入)

═══════════════════════════════════════════════════════
```

**这次不做的事**:
- ❌ 智能体决策(调 VL / 去重 / 校准)
- ❌ VL 模型
- ❌ 候选事件输出

**这次做的事**:
- ✅ 启动 3 个流
- ✅ 每 1 秒打印世界状态
- ✅ 落盘 3 个 JSON 文件
- ✅ 模拟完成后生成统计摘要

---

## 二、主循环的节奏

```
场景时间 ──────────────────────────────→ 30s

CV 流:   ██████████████████████████████  (750 帧 × 3 人 = 2250 条)
传感器流:●  ●  ●  ●  ●  ...  ●  ●     (30 条)
定位流:  ▲  ▲  ▲  ▲  ▲  ...  ▲  ▲     (30 条)

主循环:  ┊───┊───┊───┊───┊─── ... ┊──┊  (30 个周期,每 1 秒)
        tick tick tick tick tick   tick tick
```

**主循环每 1 秒做一次**:

```
for 第 N 秒:
    1. 等 1 秒(等待新一批 CV 日志到位)
    2. 从 Collector 内存中取这一秒的快照
    3. 打印摘要(多少条 CV / 传感器状态 / 危险区内人数)
    4. 攒到内存(供后续智能体用)
    
30 秒后:
    5. 落盘全部数据(cv.json / sensor.json / position.json)
    6. 打印统计摘要
```

---

## 三、输入与输出

### 输入

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--scenario` | 场景 A/B/C/D/E | B |
| `--speed` | 速度倍率 | 1.0 |
| `--log-dir` | 日志输出目录 | 自动生成 |
| `--no-save` | 仅打印不落盘 | False |

### 输出(每个场景)

**终端打印**(每秒 1 行):
```
[T= 1s] CV=75条 气体=normal   危险区内=3人
[T= 2s] CV=75条 气体=normal   危险区内=3人
...
[T=30s] 完成:总CV=2250条,传感器=30条,定位=30条
```

**文件落盘**(3 个文件):
```
logs/scenario_B_speed_1.0_20260804_143000/
├── cv_20260804_143000.json
├── sensor_20260804_143000.json
└── position_20260804_143000.json
```

---

## 四、伪代码

```python
async def main_loop(scenario, speed, log_dir):
    # ===== 1. 创建 3 个流 =====
    cv_stream = MockCVPlayer(scenario, speed)
    sensor_stream = MockSensorStream(scenario, speed)
    pos_stream = MockPositioningStream(scenario, speed)
    
    # ===== 2. 创建 Collector(负责缓存 + 落盘)=====
    collector = AsyncCollector(
        cv_stream, sensor_stream, pos_stream,
        log_dir=log_dir
    )
    
    # ===== 3. 启动(3 个消费协程开始跑)=====
    await collector.start()
    print("[已启动]")
    
    # ===== 4. 主循环(30 个周期,每周期 1 秒)=====
    for sec in range(30):
        await asyncio.sleep(1.0 / speed)     # 等现实 1/speed 秒
        snapshot = collector.get_snapshot(sec) # 取这一秒的数据
        
        # 打印摘要(不涉及决策)
        print_summary(sec, snapshot)
    
    # ===== 5. 收尾 =====
    await collector.stop()  # 落盘数据
    print_final_stats(collector)
```

---

## 五、已有的东西(不需要重写)

| 组件 | 文件 | 状态 |
|---|---|---|
| MockCVPlayer | `流式播放/mock_cv_player.py` | ✅ |
| MockSensorStream | `流式播放/mock_sensor_stream.py` | ✅ |
| MockPositioningStream | `流式播放/mock_positioning_stream.py` | ✅ |
| AsyncCollector | `流式播放/async_collector.py` | ✅ |

**主循环只是把这些"搭起来"**。

---

## 六、新增文件

```
主循环/
├── README.md          ← 本文档
└── main_loop.py       ← 主循环实现入口(待写)
```

---

## 七、5 个场景的预期输出

### 场景 A(正常)
```
[T= 1s] CV=75条 气体=normal   危险区内=3人
...
[T=30s] CV=75条 气体=normal   危险区内=3人
统计:0 次数据异常(全场景正常)
```

### 场景 B(摘头盔)
```
[T= 8s] CV=75条 气体=normal   危险区内=3人  ← P7 开始摘头盔
...
[T=30s] CV=75条 气体=normal   危险区内=3人
统计:P7 在 8-13s + 22-25s 无头盔
```

### 场景 C(气体上升)
```
[T=13s] CV=75条 气体=warning  危险区内=3人  ← 开始上升
[T=18s] CV=75条 气体=alarm    危险区内=3人  ← 达报警线
[T=22s] CV=75条 气体=normal   危险区内=3人  ← 回落
```

### 场景 D(监护人离岗)
```
[T= 8s] CV=75条 气体=normal   危险区内=2人  ← P11 离开
[T=15s] CV=75条 气体=normal   危险区内=2人  ← P11 在休息室
```

### 场景 E(多重违规)
```
[T= 8s] CV=75条 气体=normal   危险区内=3人  ← P7 摘头盔
[T=12s] CV=75条 气体=normal   危险区内=2人  ← P11 离岗
[T=20s] CV=75条 气体=alarm    危险区内=2人  ← 气体 alarm
```

---

## 八、确认后开工

```
┌──────────────────────────────────────┐
│  main_loop.py 只是"启动器":          │
│  - 不需要决策                        │
│  - 不需要 VL                         │
│  - 只是"搭积木"                      │
│  - 文件非常短(约 60 行)              │
└──────────────────────────────────────┘
```

确认上述设计后,我写 `main_loop.py`。预计需要 **5 分钟**。