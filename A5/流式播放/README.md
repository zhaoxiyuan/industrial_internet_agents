# 流式播放 — 实现逻辑说明

> **文档版本**:v0.1(待确认)
> **编制日期**:2026-08-04
> **作用**:在不动手写代码前,把 3 个流(CV/Sensor/Positioning)的设计讲清楚

---

## 一、目标与定位

### 1.1 我们要做什么

把 `demo/mock_data/` 里的**静态剧本数据**,变成**按时间节奏持续往外吐的异步生成器**。

| 工具 | 输入 | 输出 | 频率 |
|---|---|---|---|
| **MockCVPlayer** | 场景 A-E 的 CVEvent 时间表 | 每帧 1 条检测日志(3 人各 1 条) | **25 FPS** |
| **MockSensorStream** | 场景 A-E 的 SensorSchedule | 每秒 1 个 dict(含 3 个传感器读数) | **1 Hz** |
| **MockPositioningStream** | 场景 A-E 的 PositionSchedule | 每秒 1 个 dict(含 3 人位置) | **1 Hz** |

### 1.2 三个流的关系

```
┌─────────────────────────────────────────────────────────────┐
│                    异步并发运行                                │
│                                                               │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│   │MockCVPlayer  │  │MockSensorStr │  │MockPosition  │       │
│   │  (25 FPS)    │  │   (1 Hz)     │  │   (1 Hz)     │       │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│          │                 │                 │               │
│          ▼                 ▼                 ▼               │
│      [CV 帧 0]         [传感器 0]         [位置 0]            │
│      [CV 帧 1]         ...                ...               │
│      ...                                                     │
│      [CV 帧 749]       [传感器 29]        [位置 29]           │
│                                                               │
│          │                 │                 │               │
│          └────────────┬────┴────────────────┘               │
│                       ▼                                      │
│                  智能体每 1 秒消费一次                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、核心机制一:异步生成器

### 2.1 为什么用 `async def ... yield`

```python
async def stream(self):
    """异步生成器"""
    for i in range(N):
        await asyncio.sleep(0.04)   # 异步等待,不阻塞其他任务
        yield data                  # 每次 next() 返回一条
```

**3 个关键特性**:

| 特性 | 作用 |
|---|---|
| `async def` | 函数是协程,可以被 `await` / `asyncio.create_task()` |
| `yield` | 每次只产出**一条数据**,调用方一次拿一条 |
| `await asyncio.sleep()` | 让出 CPU,允许其他协程运行(并发) |

### 2.2 调用方式

```python
# 智能体启动流(后台跑)
cv_task = asyncio.create_task(cv_player.stream())

# 智能体主循环每 1 秒从缓冲区取数
while running:
    await asyncio.sleep(1.0)
    logs = cv_buffer.get_recent(window_sec=1.0)
    candidates = decision_engine.tick(logs, ...)

# 智能体结束时取消流
cv_task.cancel()
```

---

## 三、核心机制二:时间控制

### 3.1 速度倍率原理

```python
class MockCVPlayer:
    def __init__(self, scenario, speed=1.0):
        self.speed = speed   # 1.0 = 实时,5.0 = 5 倍速
    
    async def stream(self):
        scenario_start_wall = time.time()    # 现实起点
        
        for frame_no in range(750):           # 30s × 25fps
            # 场景内时间(秒),从 0 开始
            scenario_time = frame_no / 25.0
            
            # 这帧数据在"现实世界"应该出现的时刻
            target_wall_time = scenario_start_wall + scenario_time / self.speed
            
            # 距离"该出现的时刻"还要等多久
            sleep_sec = target_wall_time - time.time()
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)
            
            yield self._make_detection(scenario_time)
```

### 3.2 速度档位对照

| 档位 | 场景时间 | 现实时间 | 单帧间隔 | 总耗时 |
|---|---|---|---|---|
| **1x 实时** | 30s | 30s | 40ms | 30 秒 |
| **5x 加速** | 30s | 6s | 8ms | 6 秒 |
| **10x 加速** | 30s | 3s | 4ms | 3 秒 |

### 3.3 时间戳方案

每条数据带**两个时间戳**:

```python
yield {
    "scenario_time": f"{scenario_time:.3f}s",     # 场景内相对时间(便于看剧本)
    "wall_time": datetime.now().isoformat(),     # 现实绝对时间(便于看真实节奏)
    "frame_id": f"cam_03_{scenario_time:.3f}",   # 帧唯一标识
    ...
}
```

---

## 四、核心机制三:剧本展开

### 4.1 CVEvent 展开为 25 FPS 检测流

```python
async def stream(self):
    for frame_no in range(750):
        scenario_time = frame_no / 25.0  # 当前帧对应的场景时间
        
        # 为每个在场人员生成一条检测
        for person_id in ["P7", "P8", "P11"]:
            # 查这个人在 scenario_time 时刻的 CVEvent
            event = get_person_events_at(self.scenario, person_id, scenario_time)
            
            # 组装成 CVFrameLog
            yield CVFrameLog(
                frame_id=f"cam_03_{scenario_time:.3f}",
                timestamp=...,
                person_id=person_id,
                detections=[
                    CVFrameDetection("helmet", event.helmet, event.confidence),
                    CVFrameDetection("goggles", event.goggles, event.confidence - 0.03),
                    CVFrameDetection("protective_suit", event.protective_suit, event.confidence + 0.02),
                ]
            )
```

**关键**:每个场景时间点 → 查 `get_person_events_at(scenario, person_id, t)` → 拿到当前 PPE 状态。

### 4.2 SensorSchedule 展开为 1 Hz 读数流

```python
async def stream(self):
    for second in range(30):           # 0~29 秒
        scenario_time = second + 0.5   # 假设每"秒"代表一次读数
        
        # 为每个传感器生成读数
        readings = {}
        for sensor_id in ["gas_detector_03", "temperature_03", "flame_detector_03"]:
            schedule = get_sensor_reading_at(self.scenario, sensor_id, scenario_time)
            readings[sensor_id] = {
                "value": schedule.value,
                "status": schedule.status,
                ...
            }
        
        yield {
            "scenario_time": f"{scenario_time:.1f}s",
            "timestamp": ...,
            "readings": readings,
        }
```

**关键**:每秒查 `get_sensor_reading_at(scenario, sensor_id, t)` → 拿到当前读数。

### 4.3 PositionSchedule 展开为 1 Hz 定位流

结构与 SensorStream 几乎一致,字段不同(`position`、`area_id`、`speed`)。

---

## 五、3 个流的接口设计

### 5.1 Protocol 抽象(后续替换真实数据源零成本)

```python
from typing import Protocol, AsyncIterator, Dict, Any

class CVStreamSource(Protocol):
    """CV 检测流接口"""
    async def stream(self) -> AsyncIterator[Dict[str, Any]]: ...

class SensorStreamSource(Protocol):
    """传感器流接口"""
    async def stream(self) -> AsyncIterator[Dict[str, Any]]: ...

class PositioningStreamSource(Protocol):
    """定位流接口"""
    async def stream(self) -> AsyncIterator[Dict[str, Any]]: ...
```

### 5.2 Mock 实现 vs 真实实现

| 实现 | stream() 行为 |
|---|---|
| **MockCVPlayer** | 查 SCENARIOS_CV 时间表 → 生成假数据 → yield |
| **真实 MCP-05** | 订阅摄像头 RTSP → 调 CV 模型 → yield 真数据 |

**调用方代码不变**,只要换实现。

### 5.3 数据格式约定

**CV 单帧输出**:
```json
{
  "frame_id": "cam_03_8.040",
  "scenario_time": "8.040s",
  "wall_time": "2026-08-04T14:32:18.123",
  "person_id": "P7",
  "detections": [
    {"class_name": "helmet",          "value": false, "confidence": 0.91},
    {"class_name": "goggles",         "value": true,  "confidence": 0.88},
    {"class_name": "protective_suit", "value": true,  "confidence": 0.93}
  ]
}
```

**传感器单次输出**:
```json
{
  "scenario_time": "15.5s",
  "wall_time": "2026-08-04T14:32:25.456",
  "readings": {
    "gas_detector_03":    {"value": 0.7, "status": "warning", "unit": "%LEL"},
    "temperature_03":     {"value": 26.0, "status": "normal", "unit": "°C"},
    "flame_detector_03":  {"value": false, "status": "normal", "unit": "bool"}
  }
}
```

**定位单次输出**:
```json
{
  "scenario_time": "12.0s",
  "wall_time": "2026-08-04T14:32:22.789",
  "positions": {
    "P7":  {"position": [102.45, 88.30], "area_id": "动火区-A", "speed": 0.0},
    "P8":  {"position": [105.20, 88.10], "area_id": "动火区-A", "speed": 0.0},
    "P11": {"position": [150.00, 120.00], "area_id": "休息室", "speed": 1.4}
  }
}
```

---

## 六、智能体侧的采集器

### 6.1 AsyncCollector(协调 3 个流)

```python
class AsyncCollector:
    """并行运行 3 个流,把数据存入缓冲区供智能体查询"""
    
    def __init__(self, cv_source, sensor_source, pos_source):
        self.cv_source = cv_source
        self.sensor_source = sensor_source
        self.pos_source = pos_source
        
        self.cv_buffer = []              # 累积的 CV 日志
        self.latest_sensors = None       # 最新传感器读数
        self.latest_positions = None     # 最新定位读数
        self._tasks = []
    
    async def start(self):
        """启动 3 个后台消费任务"""
        self._tasks = [
            asyncio.create_task(self._consume_cv()),
            asyncio.create_task(self._consume_sensors()),
            asyncio.create_task(self._consume_pos()),
        ]
    
    async def _consume_cv(self):
        async for det in self.cv_source.stream():
            self.cv_buffer.append(det)
            # 可选:限制缓冲区大小,避免内存膨胀
    
    async def _consume_sensors(self):
        async for reading in self.sensor_source.stream():
            self.latest_sensors = reading
    
    async def _consume_pos(self):
        async for reading in self.pos_source.stream():
            self.latest_positions = reading
    
    def get_recent_cv_logs(self, window_sec=1.0):
        """智能体每周期调用,获取最近 N 秒的 CV 日志"""
        # 过滤:只看时间窗口内的
        # 返回 list
        ...
    
    async def stop(self):
        for task in self._tasks:
            task.cancel()
```

### 6.2 时序图

```
══════════════════════════════════════════════════════════════
t=0    t=0.04  t=0.08  ...  t=1.0   t=1.04  ...  t=30.0
│      │      │           │       │           │
▼      ▼      ▼           ▼       ▼           ▼
CV:    [F0]   [F1]  ...           [F25] ...   [F749]
       (累积到 cv_buffer)

传感器:        [S0]               [S1]  ...   [S29]
               (覆盖 latest_sensors)

定位:          [P0]               [P1]  ...   [P29]
               (覆盖 latest_positions)

智能体:                  [决策#0]            [决策#1]  ...
                        ▲
                        │
                        └── 每 1 秒,读取:
                            - cv_buffer 最近 25 条
                            - latest_sensors
                            - latest_positions
══════════════════════════════════════════════════════════════
```

---

## 七、生命周期管理

### 7.1 启动

```python
collector = AsyncCollector(
    cv_source=MockCVPlayer(scenario="B", speed=5.0),
    sensor_source=MockSensorStream(scenario="B", speed=5.0),
    pos_source=MockPositioningStream(scenario="B", speed=5.0),
)
await collector.start()
```

### 7.2 运行中

```python
for cycle in range(30):           # 30 个决策周期
    await asyncio.sleep(1.0)       # 等 1 秒
    cv_logs = collector.get_recent_cv_logs(window_sec=1.0)
    sensors = collector.latest_sensors
    positions = collector.latest_positions
    candidates = decision_engine.tick(cv_logs, sensors, positions)
```

### 7.3 结束

```python
# 30 秒跑完,或用户 Ctrl+C
await collector.stop()            # 取消 3 个后台任务
```

### 7.4 异常处理

```python
async def _consume_cv(self):
    try:
        async for det in self.cv_source.stream():
            self.cv_buffer.append(det)
    except asyncio.CancelledError:
        # 正常取消
        raise
    except Exception as e:
        # 流异常,记录日志
        logger.error(f"CV 流异常: {e}")
        raise
```

---

## 八、关键设计决策

### 8.1 为什么 CV 流是 25 个独立 yield,而不是 1 个 batch?

**方案 A(独立 yield)**:每帧 1 次 yield,3 人共 3 条/帧
```python
for frame in range(750):
    for person in [P7, P8, P11]:
        yield detection   # 共 2250 次 yield
```

**方案 B(batch yield)**:每帧 1 次 yield,内含 3 条
```python
for frame in range(750):
    yield [det_p7, det_p8, det_p11]   # 共 750 次 yield
```

**采用方案 B**,因为:
- 智能体不需要"逐条处理",是批量消费
- 减少异步切换开销
- 缓冲区管理更简单

**最终方案**:每秒 yield 1 个 batch,内含该秒所有帧(或该秒最后一帧作为代表)。

> ⚠️ 这个决策需要确认:**每秒 25 帧,智能体要不要逐帧处理?还是只要"这一秒发生了什么"?**

### 8.2 缓冲区策略

```python
# 方案 1:无限累积(简单,但内存可能膨胀)
self.cv_buffer.append(det)

# 方案 2:滑动窗口(只保留最近 N 秒)
self.cv_buffer = [d for d in self.cv_buffer if current_t - d.t < N]
self.cv_buffer.append(det)

# 方案 3:固定大小 deque
self.cv_buffer = deque(maxlen=750)  # 30s × 25fps
```

**采用方案 3**:`collections.deque(maxlen=750)`,O(1) 追加 + 自动淘汰老数据。

### 8.3 流结束的判定

异步生成器 yield 完所有数据后,自动抛 `StopAsyncIteration`,调用方 `async for` 自动退出。

```python
async def stream(self):
    for frame_no in range(750):
        yield data
    # 函数结束 → 自动 StopAsyncIteration
```

**智能体怎么知道流跑完了?** 不需要知道 — 如果流先结束,智能体下一周期就拿不到数据,自然超时。

---

## 九、与智能体决策的衔接

### 9.1 智能体每周期看到的"世界快照"

```python
class WorldSnapshot:
    """智能体在某个决策周期看到的完整世界状态"""
    cycle_no: int                    # 第几个周期(0~29)
    wall_time: str                   # 现实时间
    scenario_time: float             # 场景内时间(秒)
    cv_logs_in_last_second: List     # 最近 1 秒的 CV 日志(约 25 条 × 3 人 = 75 条)
    current_sensors: Dict            # 当前传感器读数
    current_positions: Dict          # 当前定位读数
```

### 9.2 决策引擎只接收快照

```python
async def main_loop():
    collector = AsyncCollector(...)
    await collector.start()
    
    for cycle in range(30):
        await asyncio.sleep(1.0)
        
        snapshot = WorldSnapshot(
            cycle_no=cycle,
            wall_time=datetime.now().isoformat(),
            scenario_time=cycle + 1.0,
            cv_logs_in_last_second=collector.get_recent_cv_logs(1.0),
            current_sensors=collector.latest_sensors,
            current_positions=collector.latest_positions,
        )
        
        candidates = decision_engine.tick(snapshot)
        if candidates:
            print(f"[周期 {cycle}] 候选事件: {candidates}")
    
    await collector.stop()
```

---

## 十、文件结构

```
流式播放/
├── README.md                          # 本文档
├── mock_cv_player.py                  # MockCVPlayer 类(待写)
├── mock_sensor_stream.py              # MockSensorStream 类(待写)
├── mock_positioning_stream.py         # MockPositioningStream 类(待写)
└── async_collector.py                 # AsyncCollector 类(待写)
```

---

## 十一、待确认的设计点

在写代码前,请确认以下决策:

| # | 决策点 | 推荐方案 | 备选 |
|---|---|---|---|
| **1** | CV 流的 yield 粒度 | 每秒 1 个 batch(内含 25 帧 × 3 人) | 每帧 1 个 batch(更细粒度) |
| **2** | 缓冲区策略 | `deque(maxlen=750)` 滑动窗口 | 无限累积 / 手动过滤 |
| **3** | 时间戳方案 | 双时间戳(scenario_time + wall_time) | 只用 wall_time |
| **4** | 速度档位 | 支持 1x / 5x / 10x,运行时可调 | 固定 1x |
| **5** | 流的生命周期 | `AsyncCollector.start() / stop()` 统一管理 | 各自独立管理 |
| **6** | 异常处理 | 记录日志 + 抛异常给主循环 | 静默忽略 |
| **7** | 是否支持"暂停" | ❌ 不支持(只支持"取消") | 支持 asyncio.Event 暂停 |
| **8** | 是否支持"跳到指定时间" | ❌ 不支持(从头播到尾) | 支持 seek |

**最重要的是 #1**:CV 流每秒产出 25 帧,智能体是否需要逐帧处理,还是只看"这一秒发生了什么"?

---

## 十二、下一步

确认以上设计后,开始写代码:

1. `mock_cv_player.py` — 优先级最高(最复杂)
2. `mock_sensor_stream.py` — 结构与 CV 类似但更简单
3. `mock_positioning_stream.py` — 与 Sensor 类似
4. `async_collector.py` — 把 3 个流整合起来

请确认或调整设计后,我再开始写代码。