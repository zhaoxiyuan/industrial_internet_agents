# A5 作业过程监测智能体 — Demo 开发说明

> **文档版本**:v1.1
> **编制日期**:2026-08-05
> **对应项目**:`广东石化作业场景闭环管理智能体工具链项目`
> **对应章节**:5.2 "1+8" 业务智能体体系 → A5 作业过程监测智能体
> **状态**:全部完成 ✅ (scenario_data + stream_players + main_loop + agent + demo前端)

---

## 一、A5 智能体定位

### 1.1 角色定义

**A5 = 现场永不疲倦的"安全员"**

- **职责**:在作业进行过程中,持续采集多模态数据(视频、传感器、定位),通过 CV/VL 模型识别违章行为或环境异常,输出**带证据**的**候选风险事件**
- **不做什么**:不判断风险等级(那是 A6)、不发起处置(那是 A7)、不下达停工指令(人在回路)
- **工作模式**:**持续运行**,而非一次性调用

### 1.2 上下游关系

```
↑ 上游
├── A0 总控智能体   → 下发作业任务,启动 A5
├── A2 上下文智能体 → 提供标准化作业上下文包
└── A3 资源关联智能体 → 提供摄像头/传感器/定位绑定关系

A5 自身 → 持续监测,输出候选风险事件

↓ 下游
└── A6 风险研判智能体 ← 接收候选事件,做风险分级
```

### 1.3 对应业务阶段

- **P6 作业过程动态监测**:A5 是该阶段的**主责智能体**

### 1.4 对应 Skill 与 Tool

| Skill/Tool 编号 | 名称 | 用途 |
|---|---|---|
| **S10** | 视频风险识别 | 调用 CV/VL 识别违章、人员和环境风险 |
| **S11** | 时序事件聚合 | 将连续帧结果聚合为稳定业务事件 |
| **S12** | 多源证据融合 | 融合视频、传感器、定位和作业票信息 |
| **S22** | 弱网与断网降级 | 模型或网络不可用时切换预定义流程 |
| T14-T23 | 现场资源类 Tool | 视频/传感器/定位/报警查询 |

---

## 二、Demo 场景选择:**动火作业(电焊补焊)**

### 2.1 选型理由

| 维度 | 动火作业 | 高处作业 | 受限空间 |
|---|---|---|---|
| 视觉信号丰富度 | ⭐⭐⭐⭐⭐ 火花/PPE/多人 | ⭐⭐ PPE/位置 | ⭐⭐ 内部看不清 |
| 传感器种类 | ⭐⭐⭐⭐⭐ 气体/温度/火焰 | ⭐⭐ 较少 | ⭐⭐⭐⭐⭐ 必须配 |
| 作业票复杂度 | ⭐⭐⭐⭐ 二级动火 | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| 模拟难度 | ⭐⭐ 容易 | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| 演示效果 | ⭐⭐⭐⭐⭐ 火花+PPE 直观 | ⭐⭐⭐ | ⭐⭐ |

**结论**:动火作业最适合 demo,数据好模拟,效果最直观。

### 2.2 场景设定

```
═══════════════════════════════════════════════════════════
场景设定
═══════════════════════════════════════════════════════════

📍 地点:加氢裂化装置 R-101 反应器底部西侧
🔧 作业:管线补焊(二级动火)
⏰ 时间:14:00 - 18:00(本次 demo 演示 14:30-14:45)
👥 人员:
    - P7  张师傅(焊工,主操作)
    - P8  王师傅(焊工,辅助)
    - P11 赵师傅(监护人)

🎬 演示场景(可切换):
    场景A:正常作业(基线,无任何违规)
    场景B:工人摘头盔(单项 PPE 违规)
    场景C:可燃气体浓度上升(环境恶化)
    场景D:监护人离岗(管理失控)
    场景E:多人同时违规(高风险叠加)
```

---

## 三、外围工具的"假数据"模拟设计

> **核心原则**:Demo 不部署任何真实 AI 模型。所有外部数据源都用脚本模拟,输出符合真实接口规范的假数据。

### 3.1 模拟 CV 检测(替代真实 YOLO 模型)

**思路**:`ScenarioPlayer` 按时间轴播放预定义的检测事件,完全模拟 CV 输出格式(每秒 25 条)。

```python
# A5/demo/mock_data/mock_cv.py

# 场景B 的检测剧本(P7 摘头盔)
SCENARIO_B_DETECTIONS = [
    # (时间偏移秒, person_id, helmet, goggles, suit, conf)
    (0.0,  "P7",  True,  True,  True,  0.94),
    (0.04, "P7",  True,  True,  True,  0.93),
    (0.08, "P7",  True,  True,  True,  0.95),
    # ... 中间帧省略 ...
    (4.0,  "P7",  False, True,  True,  0.88),  # 14:32:14 摘头盔
    (4.04, "P7",  False, True,  True,  0.91),
    # ... 持续 5 秒 ...
    (9.0,  "P7",  True,  True,  True,  0.94),   # 又戴回去
    # P8、P11 全程正常 ...
]

class MockCVPlayer:
    """模拟 CV 模型持续输出(每秒 25 帧)"""
    def __init__(self, scenario: str, speed: float = 1.0):
        self.detections = SCENARIOS[scenario]
        self.speed = speed  # 1.0=实时,5.0=5 倍速
    
    async def stream(self):
        """异步生成器,逐帧产出"""
        start = time.time()
        for offset_sec, person_id, helmet, goggles, suit, conf in self.detections:
            # 等待到对应时间点
            target = start + offset_sec / self.speed
            now = time.time()
            if target > now:
                await asyncio.sleep(target - now)
            yield {
                "frame_id": f"cam_03_{offset_sec:.3f}",
                "timestamp": datetime.now().isoformat(),
                "person_id": person_id,
                "detections": [
                    {"class": "helmet", "value": helmet, "conf": conf},
                    {"class": "goggles", "value": goggles, "conf": conf - 0.03},
                    {"class": "protective_suit", "value": suit, "conf": conf + 0.02},
                ]
            }
```

### 3.2 模拟传感器数据

```python
# A5/demo/mock_data/mock_sensors.py

SCENARIO_C_SENSORS = {
    "gas_detector_03": {
        "type": "可燃气体浓度",
        "unit": "%LEL",
        "threshold": 1.0,
        "abnormal_schedule": [
            # (起始秒, 终止秒, 数值)
            (0,  10, 0.2),   # 正常
            (13, 18, 0.6),   # 升高
            (18, 25, 0.85),  # 接近警戒
            (25, 30, 1.0),   # 达到警戒值!
        ]
    },
    # temperature_03、flame_detector_03 类似 ...
}

class MockSensorStream:
    """模拟传感器数据流(每秒 1 次)"""
    async def stream(self):
        while True:
            await asyncio.sleep(1.0)
            yield self._read_at(time.time() - self.start_time)
```

### 3.3 模拟定位数据

```python
# A5/demo/mock_data/mock_positioning.py

WORKER_TRAJECTORIES = {
    "scenario_b": {  # P7 摘头盔,但人不动
        "P7":  {"area_id": "动火区-A", "position": [102.45, 88.30], "speed": 0.0},
        "P8":  {"area_id": "动火区-A", "position": [105.20, 88.10], "speed": 0.0},
        "P11": {"area_id": "动火区-A", "position": [100.10, 90.50], "speed": 0.2},
    },
    "scenario_d": {  # 监护人 P11 离岗
        "P7":  {"area_id": "动火区-A", "position": [102.45, 88.30], "speed": 0.0},
        "P8":  {"area_id": "动火区-A", "position": [105.20, 88.10], "speed": 0.0},
        "P11": {
            "schedule": [
                (0, 25, [100.10, 90.50], 0.0),     # 在岗
                (25, 35, [150.00, 120.00], 1.2),   # 走向休息室!
            ]
        },
    },
}

class MockPositioningStream:
    """模拟定位数据(每秒 1 次)"""
    async def stream(self):
        while True:
            await asyncio.sleep(1.0)
            yield self._locate_at(time.time() - self.start_time)
```

### 3.4 模拟 VL 模型(脚本返回)

```python
# A5/demo/mock_data/mock_vl.py

VL_RESPONSES = {
    "P7_no_helmet_near_welding": {
        "is_violation": True,
        "violation_type": "PPE缺失-头盔",
        "evidence_description": "工人 P7 处于动火作业点附近,未佩戴安全帽。",
        "risk_level": "high",
        "reasoning": "动火作业中未佩戴安全帽是严重违章。",
        "suggested_action": "立即提醒佩戴。",
        "confidence": 0.93
    },
    "P11_away_from_supervised_area": {
        "is_violation": True,
        "violation_type": "监护人脱岗",
        "risk_level": "high",
        "confidence": 0.95
    },
}

class MockVLModel:
    """模拟 VL 模型(根据输入返回预定义响应)"""
    async def interpret(self, image_bytes, history, context):
        key = self._match_key(history, context)
        await asyncio.sleep(0.5)  # 模拟推理耗时
        return VL_RESPONSES.get(key, VL_RESPONSES["default"])
```

### 3.5 模拟作业票(静态)

```python
# A5/demo/mock_data/mock_work_permit.py

WORK_PERMIT = {
    "permit_id": "PTW-2026-0803-动火-007",
    "permit_type": "动火作业许可证",
    "level": "二级动火",
    "work_content": "加氢裂化装置 R-101 反应器底部管线补焊",
    "location": {
        "area_id": "动火区-A",
        "device_id": "R-101",
        "expected_position": [102.45, 88.30]
    },
    "planned_window": {
        "start": "2026-08-03T14:00:00",
        "end":   "2026-08-03T18:00:00"
    },
    "workers": {
        "P7":  {"name": "张师傅", "role": "焊工",   "qualification_valid": True},
        "P8":  {"name": "王师傅", "role": "焊工",   "qualification_valid": True},
        "P11": {"name": "赵师傅", "role": "监护人", "qualification_valid": True},
    },
    "required_ppe": ["helmet", "goggles", "protective_suit", "safety_shoes"],
    "supervisor_required": True,
    "jsa_risks": [
        {"risk": "焊接火花引燃可燃气体", "level": "high"},
        {"risk": "高处坠落", "level": "medium"},
    ],
    "approvals": ["李工(申请人)", "陈主任(属地)", "孙工(安全)", "周总(动火审批)"],
    "status": "in_progress"
}
```

---

## 四、A5 智能体的任务流程(Demo 版)

### 4.1 整体架构(新版)

```
═══════════════════════════════════════════════════════════
A5 Demo 架构:固定流程 → 产生日志 → 智能体 ↔ VL(右侧)
═══════════════════════════════════════════════════════════

   ┌──────────┐    ┌──────────┐    ┌──────────┐
   │ CV 流    │    │ 传感器流 │    │ 定位流   │
   │(25 FPS)  │    │ (1 Hz)   │    │ (1 Hz)   │
   └────┬─────┘    └────┬─────┘    └────┬─────┘
        │               │               │
        │    保存原始日志(每个流各存一份)        │
        ↓               ↓               ↓
   ┌────────┐      ┌────────┐      ┌────────┐
   │cv_logs│      │sensor_  │      │position│
   │.json  │      │logs    │      │_logs   │
   └───┬────┘      └───┬────┘      └───┬────┘
       └───────┬───────┼───────────────┘
               │       │
               ↓       ↓
        ┌──────────────────────────┐
        │  固定流程(AsyncCollector) │   ← 数据流管道
        │  按秒分桶 + 落盘(快照)    │     (不需要智能)
        └────────────┬─────────────┘
                     ↓
              ┌──────────────┐
              │  日志/快照    │              ← A5 的输入
              └──────┬───────┘
                     ↓
        ╔═════════════════════════════════════╗    ┌──────────────┐
        ║                                    ║    │  VL 模型    │
        ║         A5 智能体(决策者)        ║ ←→ │ (Mock/真)   │
        ║                                    ║    │              │
        ║  工具(可调用):                    ║    │  专家,被叫   │
        ║   ✦ query_raw_logs(时间范围)    ║    │  醒才工作    │
        ║                                   ║    │              │
        ║  自主决策:                        ║    │  只回答问题   │
        ║   ✦ 调不调 VL?(阈值判断)         ║    │  不做最终    │
        ║   ✦ 这是新事件还是同一事件?       ║    │  判断         │
        ║                                   ║    └──────────────┘
        ║   ✦ 要不要上调风险等级?          ║           ↑
        ║                                   ║ ←─────────┘
        ║   ✦ [最终判断]算不算违规?         ║   (VL 答完问题,
        ║                                   ║    A5 自主决定)
        ║   A5 才是最终判断者(融合 + 决策)  ║
        ╚══════════════════╤════════════════╝
                          ↓
                  ┌──────────────┐
                  │  候选事件输出 │              → 交给 A6
                  └──────────────┘

═══════════════════════════════════════════════════════════
```

**核心定位**:
- **固定流程**:数据流管道,只负责搬运数据(不需要智能)
- **3 个流各自保存原始日志**:CV/sensor/position 独立成文件,便于回溯和查询
- **A5 智能体**:接收快照 + **可调用工具查询原始日志**,自主决策
- **VL 模型**:A5 右侧的"专家",**只回答问题,不做最终判断**;按需调用,可能多轮对话

### 4.2 任务流程(每 1 秒一轮)

```
═══════════════════════════════════════════════════════════
T=N 秒的决策循环
═══════════════════════════════════════════════════════════

[输入] snapshot = {cv_logs[~75条], sensors[1条], positions[1条]}
   ↓
[步骤 1] 1 秒内多数表决(去抖动)
   └─ 对每个人统计这 1 秒 25 帧内"违规帧数"
   └─ 例如:P7 25 帧中 22 帧没戴头盔 → helmet_violation_ratio = 0.88
   ↓
[步骤 2] 自主决策 A:是否调 VL?
   └─ 阈值:helmet_violation_ratio ≥ 0.8 → 调
   └─ 例外:监护人离岗 ≥ 5 秒 → 调
   ↓
[步骤 3] 自主决策 B:这次算新事件还是同一事件?  ← 自主性 #2
   └─ 见 §4.3 事件去重逻辑
   ↓
[步骤 4] 与 VL 多轮对话(按需 1~N 轮)
   └─ 第 1 轮:"图中是否有违规?"
   └─ VL 答:"不违规"
   └─ 第 2 轮:"那 P7 的头盔在哪?"   ← 智能体追问
   └─ VL 答:"地上"
   └─ 见 §4.5 多轮对话模式
   ↓
[步骤 5] 自主决策 C:风险等级是否要上调?  ← 自主性 #3
   └─ 见 §4.4 风险等级校准
   ↓
[步骤 6] 多源融合 → 输出候选事件 → 交给 A6
═══════════════════════════════════════════════════════════
```

### 4.3 自主性 #2 — 事件去重(防噪音)

**问题**:同一违规持续 N 秒,要不要发 N 个事件?

**答案**:只发 **1 个事件**;但违规**中断后再次发生**,算**新事件**。

#### 去重状态机

```
         ┌──────────────────┐
   ┌────→│   NO_ACTIVE      │←─────────────────────┐
   │     │   (没有活跃违规)  │                      │
   │     └────────┬─────────┘                      │
   │              │ 检测到新违规                    │
   │              ↓                                │
   │     ┌──────────────────┐                      │
   │     │  VIOLATION_NEW   │ ← 新事件,标记 episode_id=N+1
   │     │  (刚发生)        │                      │
   │     └────────┬─────────┘                      │
   │              │ 持续违规中...                   │
   │              ↓                                │
   │     ┌──────────────────┐                      │
   │     │  VIOLATION_ONGOING│ ← 同一事件,不重复发│
   │     │  (持续中)        │                      │
   │     └────────┬─────────┘                      │
   │              │ 违规消失(P7 戴回头盔)         │
   │              │                                 │
   │              ↓                                 │
   │     ┌──────────────────┐                       │
   │     │  COOLDOWN        │ ← 等待"恢复确认"窗口 │
   │     │  (冷却期)        │   (默认 2 秒)        │
   │     └────────┬─────────┘                       │
   │              │ 冷却期内再次违规                │
   │              └─────────────────────────────────┘
   │                (视为同一事件的延续)
   │              │ 冷却期满无违规
   │              ↓
   │     ┌──────────────────┐
   └─────│  EPISODE_CLOSED  │
         │  (事件已结束)    │
         └──────────────────┘
```

#### 数据结构

```python
class EventDeduplicator:
    """事件去重器:为每个人跟踪当前的违规事件状态"""
    
    def __init__(self, cooldown_sec=2.0):
        self.cooldown_sec = cooldown_sec
        # 每人当前的违规状态
        self._states = {}  # person_id -> EpisodeState
    
    def check(self, person_id, current_violation: bool) -> str:
        """
        输入:这 1 秒该人是否违规
        输出:决策结果
          - "new_episode": 新事件(应输出)
          - "ongoing": 同一事件进行中(不输出)
          - "closed": 事件结束
          - "none": 无活跃事件
        """
        state = self._states.get(person_id)
        
        # 场景 1:之前没有活跃状态
        if state is None:
            if current_violation:
                # 新违规开始 → 新事件
                self._states[person_id] = EpisodeState(
                    episode_id=uuid4(),
                    status="new",
                    started_at=current_time(),
                )
                return "new_episode"
            return "none"
        
        # 场景 2:有活跃事件
        if current_violation:
            if state.status == "cooldown":
                # 冷却期内又违规 → 视为同一事件延续
                state.status = "ongoing"
                state.last_violation_at = current_time()
                return "ongoing"
            else:
                # 持续违规中
                state.last_violation_at = current_time()
                return "ongoing"
        
        # 当前无违规
        if state.status == "cooldown":
            # 冷却期满,正式关闭
            self._states.pop(person_id)
            return "closed"
        
        # 从违规进入冷却
        state.status = "cooldown"
        state.cooldown_started_at = current_time()
        return "ongoing"   # 这一秒还在冷却期内,不算关闭
    
    def tick_cooldown(self):
        """每秒调用一次,检查冷却是否到期"""
        now = current_time()
        for person_id, state in list(self._states.items()):
            if state.status == "cooldown":
                if now - state.cooldown_started_at >= self.cooldown_sec:
                    self._states.pop(person_id)
```

#### 5 个场景的去重效果

| 场景 | 违规模式 | 输出事件数 |
|---|---|---|
| A 正常 | 全程无违规 | **0** |
| B 摘头盔 | T=8-13s 摘 5s + T=22-25s 摘 3s,中间 T=13-22s 戴回去 | **2**(两次摘是两个事件) |
| C 气体上升 | T=18-22s alarm,其他时间正常 | **1** |
| D 监护人离岗 | T=15s 后持续离岗到 T=30s | **1** |
| E 多重违规 | P7 摘头盔(T=8 起)+ P11 离岗(T=12 起),持续到 T=30s | **2-3** |

### 4.4 自主性 #3 — 风险等级校准

**问题**:VL 返回 `medium`,但智能体看到了其他证据,要不要**上调**?

**答案**:智能体基于多源证据,**自主决定是否上调风险等级**。

#### 校准矩阵

| VL 返回等级 | 其他证据 | 智能体最终输出 |
|---|---|---|
| **low** | 无其他证据 | low(直接输出) |
| **low** | + sensor alarm | **→ high**(上调) |
| **low** | + P11 离岗 | **→ high**(上调) |
| **medium** | + sensor alarm | **→ high**(上调) |
| **medium** | + sensor alarm + P11 离岗 | **→ critical**(连续上调) |
| **high** | 无其他证据 | high(保持) |
| **high** | + sensor alarm | **→ critical**(上调) |
| **critical** | 任何证据 | critical(封顶) |

#### 校准算法

```python
class RiskCalibrator:
    """风险等级校准器:基于 VL 结果 + 多源证据,智能体自主上调"""
    
    # 风险等级优先级(数值越大越严重)
    RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    
    def calibrate(self, vl_risk_level: str, supporting_sources: List[str]) -> str:
        """
        输入:
          - vl_risk_level: VL 返回的风险等级
          - supporting_sources: 多源证据列表
            (如 ["video", "vl_semantic", "sensor", "location"])
        
        输出:校准后的最终风险等级
        """
        # 基础等级 = VL 返回的
        current = self.RISK_ORDER.get(vl_risk_level, 1)
        
        # ── 智能体的自主上调逻辑 ──
        # 规则 1:传感器报警 → 至少 high
        if "sensor" in supporting_sources:
            current = max(current, self.RISK_ORDER["high"])
        
        # 规则 2:监护人同时不在 → 至少 critical
        # (意味着现场完全失去管理)
        if self._is_supervisor_absent(supporting_sources):
            current = max(current, self.RISK_ORDER["critical"])
        
        # 规则 3:3 个及以上证据源叠加 → 自动 +1 级
        if len(supporting_sources) >= 3:
            current = min(current + 1, self.RISK_ORDER["critical"])
        
        # 反向查等级名
        for name, level in self.RISK_ORDER.items():
            if level == current:
                return name
        return vl_risk_level  # 兜底
```

#### 5 个场景的校准效果

| 场景 | VL 单源输出 | 智能体上调后 |
|---|---|---|
| A 正常 | 无事件 | — |
| B 摘头盔 | high(单一 PPE 违规) | high(证据未叠加,不调) |
| C 气体上升 | high(气体 alarm) | **critical**(sensor 触发+多源叠加) |
| D 监护人离岗 | high(定位+P11 不在) | **critical**(监护人不在 = 现场失控) |
| E 多重违规 | 各事件 medium/high | **全部 critical**(3 源以上叠加) |

### 4.5 原始日志存储与查询工具

#### 4.5.1 为什么需要保存原始日志?

```
场景:智能体判定 P7 摘头盔,但想确认"他真的摘了多久"
→ 智能体需要回看历史帧 → 必须能查原始日志
```

**两类日志,用途不同**:

| 类型 | 用途 | 格式 | 大小/场景 |
|---|---|---|---|
| **原始日志**(本节) | 完整记录,可回溯 | 单文件/流 | 每场景 ~2250 条 CV + 30 传感器 + 30 定位 |
| **快照**(上节) | 智能体实时决策用 | 每秒 1 个 dict | 内存中,跑完可丢弃 |

#### 4.5.2 存储结构(简化版)

```
logs/scenario_{ID}_speed_{S}_{时间戳}/
├── cv_<时间戳>.json           ← 全部 CV 日志(2250 条)
├── sensor_<时间戳>.json       ← 全部传感器读数(30 条)
└── position_<时间戳>.json     ← 全部定位读数(30 条)
```

**特点**:
- ✅ 无 `manifest.json`(不需要,文件少)
- ✅ 无 `cv/` `sensor/` `position/` 子目录
- ✅ 单文件/流,文件名带时间戳避免覆盖
- ✅ 每个文件 = 一个流的全部日志

**每个文件结构**:
```json
{
  "scenario": "B",
  "type": "cv",
  "created_at": "20260804_103444",
  "log_count": 2250,
  "logs": [ /* 全部原始日志数组 */ ]
}
```

#### 4.5.3 智能体的查询工具

**A5 通过 `AsyncCollector.query_raw_logs()` 方法直接查询**(无需单独的 Tool 类):

```python
# 已经在 AsyncCollector 中实现
class AsyncCollector:
    def query_raw_logs(
        self,
        start_sec: float,             # 起始秒(包含)
        end_sec: float,               # 结束秒(不包含)
        source_type: Optional[str] = None,   # "cv" | "sensor" | "position" | None=全部
        person_id: Optional[str] = None,      # 仅 CV 有效
    ) -> List[Dict]:
        """
        按时间范围 [start_sec, end_sec) 查询原始日志(从内存读)。

        用例:
          - "P7 在 8-13 秒做了什么?" → collector.query_raw_logs(8, 13, "cv", "P7")
          - "气体从什么时候开始 alarm?" → collector.query_raw_logs(0, 30, "sensor")
          - "P11 什么时候离开的?" → collector.query_raw_logs(0, 30, "position", "P11")
        """
        types = [source_type] if source_type else ["cv", "sensor", "position"]
        results = []
        for stype in types:
            for log in self._get_logs(stype):
                t = self._extract_second(log.get("scenario_time", "0s"))
                if start_sec <= t < end_sec:
                    if person_id and log.get("person_id") != person_id:
                        continue
                    results.append(log)
        return results
```

**两种读取方式**:

| 时机 | 方式 | 适用 |
|---|---|---|
| **运行时**(智能体决策中) | 直接调用 `collector.query_raw_logs()` | 内存中的数据,O(N) 过滤 |
| **场景跑完后**(离线分析) | 读 `cv_<ts>.json` 文件,按 `scenario_time` 过滤 | 持久化数据,适合复盘 |

#### 4.5.4 在 A5 中的使用示例

```python
# 智能体处理 P7 摘头盔事件时
async def investigate_p7_helmet(self):
    # 1. 调 VL 得到结论
    vl_answer = await self.call_vl(...)

    # 2. 想确认:P7 真的持续 5 秒没戴头盔吗?
    raw_logs = self.collector.query_raw_logs(
        start_sec=8.0, end_sec=13.0,
        source_type="cv", person_id="P7"
    )

    # 3. 智能体基于原始日志做二次判断
    no_helmet_count = sum(
        1 for log in raw_logs
        if not next(d for d in log["detections"] if d["class_name"] == "helmet")["value"]
    )
    total = len(raw_logs)
    actual_ratio = no_helmet_count / total   # 5 秒 × 25fps = 125 帧,接近 100% 没戴

    # 4. 用实际数据校验 VL 的结论
    if actual_ratio < 0.5:
        return self._downgrade_decision(vl_answer)  # 原始数据不支持 → 降级
```

**这个工具的意义**:
- **可解释性**:每个候选事件都能附上"原始日志证据"
- **可调试**:智能体决策错了?查原始日志复盘
- **可扩展**:未来支持"查询某 1 秒的所有传感器报警"

### 4.6 决策核心代码

```python
class A5DecisionEngine:
    """A5 智能体:接收日志,自主决定"""
    
    def __init__(self, vl_model, work_permit):
        self.vl = vl_model
        self.permit = work_permit
        self.deduplicator = EventDeduplicator(cooldown_sec=2.0)
        self.calibrator = RiskCalibrator()
    
    async def tick(self, snapshot) -> List[Dict]:
        candidates = []
        
        for person_id in self.permit["workers"]:
            stats = self._analyze_ppe(snapshot["cv_logs"], person_id)
            
            # ── 决策 1:这 1 秒是否违规? ──
            is_violating = stats["helmet_ratio"] >= 0.8
            # 例外:监护人离岗
            if person_id == "P11":
                position = snapshot["positions"]["positions"].get(person_id, {})
                is_violating = is_violating or not position.get("is_in_danger_zone", True)
            
            # ── 决策 2:这是新事件吗?(自主性 #2) ──
            episode_status = self.deduplicator.check(person_id, is_violating)
            if episode_status != "new_episode":
                continue
            
            # ── 决策 3:与 VL 多轮对话(只拿语义证据,不替 A5 决策) ──
            vl_evidence = await self._multi_turn_dialog_with_vl(
                person_id, stats, snapshot
            )
            
            # ── 决策 4:A5 融合多源证据 + 自主判断 ──
            supporting_sources = self._collect_sources(person_id, snapshot, vl_evidence)
            
            # ★ A5 才是最终判断者 ★
            # A5 综合:CV 检测 + VL 语义 + 传感器 + 定位 + 作业票 → 自主决定
            decision = self._a5_final_decision(
                person_id=person_id,
                stats=stats,
                vl_evidence=vl_evidence,
                supporting_sources=supporting_sources,
            )
            
            if not decision["is_violation"]:
                continue
            
            # ── 决策 5:风险等级校准(自主性 #3) ──
            final_risk = self.calibrator.calibrate(
                decision["risk_level"], supporting_sources
            )
            
            # ── 输出候选事件 ──
            candidates.append(self._build_event(
                person_id, stats, decision, snapshot,
                risk_level=final_risk,
                supporting_sources=supporting_sources,
                vl_evidence=vl_evidence,
            ))
        
        return candidates
    
    def _analyze_ppe(self, cv_logs, person_id) -> Dict:
        """1 秒内多数表决"""
        person_logs = [l for l in cv_logs if l["person_id"] == person_id]
        total = len(person_logs)
        if total == 0:
            return {"total": 0, "violation_count": 0, "ratio": 0}
        no_helmet_count = sum(
            1 for log in person_logs
            if not next(d for d in log["detections"] if d["class_name"] == "helmet")["value"]
        )
        return {
            "total": total,
            "violation_count": no_helmet_count,
            "ratio": no_helmet_count / total,
        }
    
    def _collect_sources(self, person_id, snapshot, vl_evidence) -> List[str]:
        """
        A5 收集所有证据源。
        注意:VL 只是其中一个证据源,不是决策者。
        """
        sources = ["video"]   # CV 总是有
        # VL 语义(如果 VL 提供了有效证据)
        if vl_evidence and vl_evidence.get("has_meaningful_answer"):
            sources.append("vl_semantic")
        # sensor
        if any(r["status"] == "alarm" for r in snapshot["sensors"]["readings"].values()):
            sources.append("sensor")
        # location
        pos = snapshot["positions"]["positions"].get(person_id, {})
        if pos.get("is_in_danger_zone"):
            sources.append("location")
        # work_permit
        if "helmet" in self.permit["required_ppe"]:
            sources.append("work_permit")
        return sources
    
    def _a5_final_decision(
        self, person_id, stats, vl_evidence, supporting_sources
    ) -> Dict:
        """
        ★ A5 的最终判断 ★
        
        输入:
          - stats: CV 检测统计
          - vl_evidence: VL 多轮对话的语义证据(只是参考,不是决策)
          - supporting_sources: 多源证据列表
        
        输出:违规判断(由 A5 自主决定,不是 VL)
        """
        # ── A5 的判断规则(融合多源,而非只看 VL) ──
        
        # 规则 1:VL 提供明确证据 + CV 检测到异常 → 算违规
        if vl_evidence and vl_evidence.get("confirms_violation"):
            return {
                "is_violation": True,
                "risk_level": vl_evidence.get("suggested_risk", "medium"),
                "violation_type": vl_evidence.get("violation_type", "unknown"),
                "reasoning": f"A5 判定:VL 确认 + CV 证据 {stats['ratio']:.0%} 异常",
            }
        
        # 规则 2:CV 高度异常 + 传感器告警 → 即使 VL 不确定,也算违规
        if stats["ratio"] >= 0.9 and "sensor" in supporting_sources:
            return {
                "is_violation": True,
                "risk_level": "high",
                "violation_type": "PPE缺失-头盔",
                "reasoning": "A5 判定:CV 95% 异常 + 传感器告警(强证据,无需 VL 确认)",
            }
        
        # 规则 3:CV 异常 + 在危险区 → 算违规
        if stats["ratio"] >= 0.8 and "location" in supporting_sources:
            return {
                "is_violation": True,
                "risk_level": "medium",
                "violation_type": "PPE缺失-头盔",
                "reasoning": "A5 判定:CV 异常 + 在作业危险区",
            }
        
        # 默认:不算违规
        return {
            "is_violation": False,
            "risk_level": "low",
            "violation_type": "无",
            "reasoning": "A5 判定:证据不足以确认违规",
        }
    
    async def _multi_turn_dialog_with_vl(
        self, person_id, stats, snapshot, max_turns=3
    ) -> Dict:
        """
        与 VL 的多轮对话 — VL 仅提供语义证据,不做最终判断。
        
        VL 的角色:被审问的"目击者",如实回答 A5 的提问。
        A5 的角色:提问 + 综合判断。
        
        对话示例:
          Turn 1: A5 问:"P7 当前 PPE 状态?周围环境?"
          Turn 1: VL 答:"P7 没戴头盔,在焊接点附近 2 米,看到火星"
          Turn 2: A5 追问:"P7 动作是擦汗还是持续违规?"
          Turn 2: VL 答:"持续 1 秒低头,不像擦汗"
          
        返回 vl_evidence,供 A5 综合判断使用:
          {
            "has_meaningful_answer": True,
            "confirms_violation": True,         # VL 是否"确认"违规(只是参考)
            "violation_type": "PPE缺失-头盔",
            "suggested_risk": "high",
            "key_facts": ["P7 没戴头盔", "在焊接点附近", "看到火星"],
          }
        """
        conversation_history = []
        vl_facts = []   # 累积 VL 提到的事实
        
        for turn in range(max_turns):
            question = self._generate_vl_question(
                person_id, stats, snapshot, conversation_history
            )
            
            # VL 返回本次回答(模拟:基于上下文提取事实)
            vl_answer = await self.vl.interpret(
                question=question,
                history=stats,
                context=self._build_vl_context(person_id, snapshot),
                conversation=conversation_history,
            )
            
            conversation_history.append({
                "turn": turn,
                "question": question,
                "answer": vl_answer,
            })
            
            # 提取 VL 提到的事实(简单的关键字匹配,真实 VL 会更复杂)
            vl_facts.extend(vl_answer.get("facts", []))
            
            # 如果 A5 已经收集到足够信息,可以提前结束
            if turn >= 1 and len(vl_facts) >= 3:
                break
        
        # 组装成 vl_evidence(供 A5 决策用)
        return {
            "has_meaningful_answer": len(vl_facts) > 0,
            "confirms_violation": self._extract_violation_signal(vl_facts),
            "violation_type": self._extract_violation_type(vl_facts),
            "suggested_risk": self._extract_risk_signal(vl_facts),
            "key_facts": vl_facts,
            "conversation": conversation_history,
        }
    
    def _extract_violation_signal(self, facts: List[str]) -> bool:
        """从 VL 的事实描述中提取违规信号(简单关键字匹配)"""
        violation_keywords = ["违规", "未佩戴", "摘了", "脱岗", "离开", "超标", "alarm"]
        return any(kw in fact for fact in facts for kw in violation_keywords)
    
    def _extract_violation_type(self, facts: List[str]) -> str:
        if any("头盔" in f or "helmet" in f for f in facts):
            return "PPE缺失-头盔"
        if any("脱岗" in f or "离开" in f for f in facts):
            return "监护人脱岗"
        if any("气体" in f or "alarm" in f for f in facts):
            return "环境异常-可燃气体"
        return "unknown"
    
    def _extract_risk_signal(self, facts: List[str]) -> str:
        if any("critical" in f.lower() or "严重" in f for f in facts):
            return "critical"
        if any("high" in f.lower() or "高" in f for f in facts):
            return "high"
        if any("medium" in f.lower() or "中" in f for f in facts):
            return "medium"
        return "low"
    
    def _generate_vl_question(self, person_id, stats, snapshot, history) -> str:
        """根据上下文生成下一轮提问(Demo 中固定 prompt)"""
        if len(history) == 0:
            # 第 1 轮:直接判断
            return f"图中 {person_id} 是否有 PPE 违规或危险行为?"
        elif len(history) == 1:
            # 第 2 轮:追问细节
            return f"该人员当前姿态是什么?周围环境是否支持作业?"
        else:
            # 第 3 轮:综合风险
            return "综合以上情况,这次违规的风险等级是什么?"
```

### 4.7 智能体的核心职责

| 职责 | 干什么 | 不干什么 |
|---|---|---|
| **看日志** | 接收每 1 秒的世界快照 | 不处理图像 |
| **去抖动** | 1 秒内多数表决(25 帧中 ≥ 20 帧违规即视为违规) | 不做精确识别 |
| **去重** | **同一违规只发 1 个事件**(自主性 #2) | 不跨人合并 |
| **调 VL** | 按需调专家,被叫才醒 | 不持续运行 |
| **融合 + 校准** | 多源证据打分 + **风险等级上调**(自主性 #3) | 不计算模型精度 |
| **输出** | 候选事件 → A6 | 不分派处置 |

---

## 五、实际程序结构(已实现)

```
A5/                                 # 项目根目录
├── README.md                       # 本文档(总览)
│
├── demo/                           # 模拟数据与场景
│   └── mock_data/                  # 5 个场景的剧本数据(纯数据,不含工具)
│       ├── types.py                # 数据类型:CVEvent/SensorSchedule/PositionSchedule
│       ├── mock_cv.py              # 场景 A-E 的 CV 检测时间表
│       ├── mock_sensors.py         # 场景 A-E 的传感器时间表
│       ├── mock_positioning.py     # 场景 A-E 的人员轨迹
│       ├── mock_vl.py              # VL 预定义响应(8 条响应)
│       └── mock_work_permit.py     # 作业票(PTW-2026-0803-动火-007)
│
├── 流式播放/                       # 模拟数据源 + 固定流程
│   ├── README.md                   # 流式播放设计说明
│   ├── mock_cv_player.py           # MockCVPlayer(25 FPS,去掉 speed 实时)
│   ├── mock_sensor_stream.py       # MockSensorStream(1 Hz,去掉 speed 实时)
│   ├── mock_positioning_stream.py  # MockPositioningStream(1 Hz,去掉 speed 实时)
│   └── async_collector.py          # AsyncCollector(集中管理 3 流 + 落盘 + 防未来)
│
├── 主循环/                         # 主循环入口
│   ├── README.md                   # 主循环设计说明
│   └── main_loop.py                # 批量运行 + A5 集成(--scenario ABCDE, --no-agent)
│
├── 智能体/                         # A5 决策者(独立模块)
│   ├── README.md                   # 智能体设计说明
│   ├── __init__.py
│   ├── tools.py                    # 4 个 LangChain @tool 声明(查询/快照/表决/VL)
│   ├── event_deduplicator.py       # 事件去重状态机(NO_ACTIVE/NEW_EPISODE/ACTIVE/COOLDOWN)
│   └── a5_agent.py                 # A5Agent 主类(tick 方法,每 1 秒被主循环调)
│
└── .gitignore                      # 排除 __pycache__/ logs/
```
```

---

## 六、使用方式(已实现)

### 6.1 CLI 运行

```bash
python main_loop/main_loop.py --scenario B      # 单场景+Agent
python main_loop/main_loop.py --scenario ABCDE   # 全部5场景
python main_loop/main_loop.py --scenario B --no-agent  # 纯固定流程
```

### 6.2 Web 前端

```bash
python demo/app.py                      # 启动服务
# 浏览器打开 http://localhost:5001      # 选择场景,实时监控
```

### 6.3 场景预期输出

| 场景 | raw_event | 类型 |
|---|---|---|
| A | 0 | 无违规 |
| B | 2 (ongoing+closed ×2) | PPE缺失-头盔 |
| C | 1 | 环境异常-传感器告警 |
| D | 1 | 监护人脱岗 |
| E | 3 | PPE+监护人+传感器 |

### 6.4 纯固定流程(无 Agent)

```bash
python 主循环/main_loop.py --scenario B                    # 单场景
python 主循环/main_loop.py --scenario ABCDE                 # 全部 5 场景
python 主循环/main_loop.py --scenario AAAAA                 # A 连跑 5 遍
python 主循环/main_loop.py --scenario ABCDEACC              # 任意组合
python 主循环/main_loop.py --scenario B --no-agent          # 不启动 A5
python 主循环/main_loop.py --scenario ABCDE --log-dir logs/batch
```


---

## 七、关键设计决策(已落地)

| 决策 | 实现 |
|---|---|
| **固定实时 1:1** | 去掉所有 speed/speed 参数,现实 1 秒 = 场景 1 秒 |
| **wall_time 为主时间** | 所有工具接收 ISO 格式时间,内部 scenario_time 只做辅助 |
| **防未来** | `set_agent_now()` 设置时间上限,`query_raw_logs` 拒绝查未来 |
| **日志只增不改** | 每秒新建文件 `{type}_{ts}_T{sec:02d}.json`,永覆盖 |
| **Agent 不判风险** | raw_event 无 risk_level,A6 才判 |
| **Agent 不处置** | 只输出 raw_event,不发起通知/停工 |
| **事件覆盖写** | 同一事件持续更新同一文件(last_seen + duration) |
| **多维检测** | PPE(CV表决) + 传感器(alarm) + 监护人(位置) |
| **作业票规则可编辑** | 前端 textarea 编辑,运行时生效 |

---

## 八、实现清单(全部完成)

| # | 模块 | 状态 |
|---|---|---|
| 1 | `scenario_data/` 5 场景数据 | ✅ |
| 2 | `stream_players/` 3 模拟流 + AsyncCollector | ✅ |
| 3 | `main_loop/` CLI + 批量运行 | ✅ |
| 4 | `agent/tools.py` 8 个工具 | ✅ |
| 5 | `agent/a5_agent.py` ReAct Agent | ✅ |
| 6 | `agent/system_prompt.py` 提示词管理 | ✅ |
| 7 | `agent/work_permit_rules.py` 规则管理 | ✅ |
| 8 | `demo/` FastAPI + WebSocket 前端 | ✅ |

---

## 九、程序结构

```
A5/
├── scenario_data/          5 场景剧本数据
├── stream_players/         3 模拟流 + AsyncCollector
├── main_loop/              CLI 入口(批量运行)
├── agent/                  A5 Agent(8工具+三维检测)
│   ├── tools.py, a5_agent.py
│   ├── system_prompt.py, work_permit_rules.py
│   └── event_deduplicator.py
├── demo/                   Web 前端(FastAPI+WebSocket)
│   ├── app.py, README.md
│   └── templates/index.html
└── .gitignore
```

---

## 十、参考资源

- 项目主文档:`广东石化作业场景闭环管理智能体工具链项目实施任务计划书.docx`
- 对应章节:5.2 "1+8" 业务智能体体系、6.2 Tool 清单、6.3 Skill 清单
- 子模块 README:
  - `主循环/README.md` — 主循环设计
  - `流式播放/README.md` — 流式播放设计
  - `智能体/README.md` — 智能体设计
- 相关技术:
  - Python `asyncio` 异步编程
  - `langchain_core.tools` 工具声明

---

**编制人**:项目工具链研发组
**当前版本**:v1.0(核心模块全部完成)
**下一版本**:v1.1(预计接入真实 VL 模型 + 更多测试场景)

---

## 七、Demo 运行效果示例

```bash
$ python -m A5.demo.main_demo --scenario B --speed 5 --duration 30
```

```
═══════════════════════════════════════════
  A5 作业过程监测智能体 - Demo
  场景: scenario_b
  速度: 5.0x
═══════════════════════════════════════════

📋 作业票: PTW-2026-0803-动火-007
   作业内容: 加氢裂化装置 R-101 反应器底部管线补焊
   必戴 PPE: ['helmet', 'goggles', 'protective_suit', 'safety_shoes']

[14:32:14.000] CV: P7 helmet:✅ P8:✅ P11:✅
[14:32:14.500] 周期 #29: 无异常
[14:32:15.000] CV: P7 helmet:✅ P8:✅ P11:✅
[14:32:15.500] 周期 #30: 无异常
[14:32:16.000] CV: P7 helmet:✅ P8:✅ P11:✅
[14:32:16.500] 周期 #31: 无异常
[14:32:17.000] CV: P7 helmet:❌ P8:✅ P11:✅ ← P7 头盔消失!
[14:32:17.500] 周期 #32: P7 未戴头盔 0.5 秒 → 继续观察
[14:32:18.000] CV: P7 helmet:❌ P8:✅ P11:✅
[14:32:18.500] 周期 #33: P7 未戴头盔 1.0 秒 → 继续观察
[14:32:19.000] CV: P7 helmet:❌ P8:✅ P11:✅
[14:32:19.500] 周期 #34: P7 未戴头盔 1.5 秒 → 继续观察
[14:32:20.000] CV: P7 helmet:❌ P8:✅ P11:✅
[14:32:20.500] 周期 #35: P7 未戴头盔 2.0 秒 → 继续观察
[14:32:21.000] CV: P7 helmet:❌ P8:✅ P11:✅
[14:32:21.500] 周期 #36: P7 未戴头盔 2.5 秒 → 继续观察
[14:32:22.000] CV: P7 helmet:❌ P8:✅ P11:✅
[14:32:22.500] 周期 #37: P7 未戴头盔 3.0 秒 → 🔔 触发 VL!
                调用 VL: P7_no_helmet_near_welding
                VL 返回: 严重违规,风险等级 high,置信度 0.93
[14:32:23.000] CV: P7 helmet:❌ P8:✅ P11:✅
[14:32:23.500] 周期 #38: 融合证据...
                ✓ 视频证据 (134 帧确认)
                ✓ VL 语义证据 (high)
                ✓ 定位证据 (P7 在动火区)
                ✓ 作业票证据 (PPE 列表含 helmet)
                ✗ 传感器证据 (可燃气体正常)

🚨 [候选风险事件 38]
   类型: PPE缺失-头盔
   人员: {'id': 'P7', 'name': '张师傅', 'role': '焊工'}
   风险: high
   证据源: ['video', 'vl_semantic', 'location', 'work_permit']
   融合分数: 0.80

[14:32:24.000] CV: P7 helmet:✅ P8:✅ P11:✅ ← P7 戴回去了
...

✅ Demo 运行完成,共 60 个决策周期
   共发现 1 个候选风险事件
```

---

## 八、Demo 实现清单

| 模块 | 工作量 | 关键文件 |
|---|---|---|
| 模拟数据源 | 0.5 天 | `demo/mock_data/*.py` (5 个文件) |
| 状态聚合器 | 0.5 天 | `src/agent/state_manager.py` |
| 决策引擎 | 1 天 | `src/agent/decision_engine.py` |
| 异步采集器 | 0.5 天 | `src/collector/async_collector.py` |
| Demo 主程序 | 0.5 天 | `demo/main_demo.py` |
| 场景剧本 | 0.5 天 | `demo/scenarios/*.py` (5 个场景) |
| 测试 | 0.5 天 | `tests/test_*.py` |
| **总计** | **4 天** | |

---

## 九、关键设计决策

### 9.1 不使用 LangGraph 的理由(Demo 阶段)

- Demo 阶段不需要**检查点持久化**(运行 30 秒就结束)
- Demo 阶段不需要**跨节点共享状态**(单线程 + 内存即可)
- Demo 阶段不需要**循环边**(用 while 循环更直观)
- **保留扩展接口**:后续接 LangGraph 时,只需将 `decision_engine.tick()` 改写为 LangGraph 节点

### 9.2 不使用真实模型的策略

| 真实组件 | Demo 替代方案 | 替换原因 |
|---|---|---|
| CV 模型(YOLO) | `MockCVPlayer` 播放预定义检测流 | 避免部署 GPU/模型权重 |
| VL 模型(Qwen-VL) | `MockVLModel` 返回预定义响应 | 避免 API 调用费用 |
| 传感器 | `MockSensorStream` 按时间表读数 | 现场无真实传感器 |
| UWB 定位 | `MockPositioningStream` 模拟轨迹 | 现场无定位基站 |
| 作业票系统 | 静态 JSON | 现场无票证系统 |

### 9.3 核心接口预留

所有 mock 数据源都实现**与真实组件相同的接口**,后续替换为真实组件时**零改动**:

```python
# 抽象接口(真实组件和 mock 都实现)
class CVStream(Protocol):
    async def stream(self) -> AsyncIterator[Dict]: ...

class VLModel(Protocol):
    async def interpret(self, image: bytes, history: Dict, context: Dict) -> Dict: ...

class SensorStream(Protocol):
    async def stream(self) -> AsyncIterator[Dict]: ...
```

---

## 十、参考资源

- 项目主文档:`广东石化作业场景闭环管理智能体工具链项目实施任务计划书.docx`
- 对应章节:5.2 "1+8" 业务智能体体系、6.2 Tool 清单、6.3 Skill 清单
- 相关技术:
  - Python `asyncio` 异步编程
  - `dataclass` / `pydantic` 数据建模
  - `pytest` 异步测试

---

**编制人**:项目工具链研发组
**待审核**:业务场景组 / 模型与视觉组
**下一版本**:v0.3(预计补充实际运行截图与多场景对比)