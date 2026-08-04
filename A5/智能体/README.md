# A5 智能体 — 实现说明

> **定位**:接收日志 → 识别候选事件 → 输出 raw_event
> **编制日期**:2026-08-04

---

## 一、明确边界

| 谁 | 做什么 | **不**做什么 |
|---|---|---|
| **A5 智能体**(本模块) | 接收日志,识别"出现了什么候选事件",输出 raw_event | **不**判断风险等级(那是 A6)<br>**不**发起处置(那是 A7)<br>**不**派单、不通知、不停工 |
| A6 风险研判 | 接收 raw_event → 判风险等级 → 分级处理 | — |
| A7 处置闭环 | 接收分级事件 → 派单/通知/停工 | — |

**A5 输出物**:raw_event(候选事件,无 risk_level)

---

## 二、A5 与主循环的关系

A5 **不嵌入主循环**,而是**独立的模块**,通过**回调**被触发。

```
═══════════════════════════════════════════════════════════

主循环(独立运行)                         智能体(独立模块)
┌─────────────────────────────────┐      ┌──────────────────────┐
│  AsyncCollector                  │      │  A5Agent             │
│  - 跑 3 个流                      │      │                      │
│  - 每秒写日志到 logs/             │      │  注册回调            │
│  - 每秒写 snapshot.json          │      │  on_window_complete  │
│                                  │ ───→ │                      │
│  当窗口完成时,回调 agent         │      │  tick(snapshot)      │
└─────────────────────────────────┘      │  ↓                   │
                                          │  输出 raw_event       │
                                          │  → logs/raw_event_*   │
                                          └──────────────────────┘

═══════════════════════════════════════════════════════════
```

**关键**:
- Agent 是**独立模块**,可单独测试、单独部署
- 触发方式:**事件驱动**(回调),不是轮询
- 输入方式:**喂日志**(读 snapshot 或磁盘 JSON)

---

## 三、Agent 的工具集

> 这些工具不是 LangChain tool,而是 A5Agent 类的方法或注入的依赖

### 数据查询类(从 AsyncCollector 取)

| 方法 | 作用 |
|---|---|
| `collector.get_snapshot(sec)` | 取第 N 秒的世界快照(cv/sensor/position) |
| `collector.query_raw_logs(start, end, type, person_id)` | 按时间范围查询原始日志 |

### 决策辅助类(Agent 自带)

| 方法 | 作用 |
|---|---|
| `agent._majority_vote(cv_logs, person_id)` | 1 秒内多数表决(25 帧中多少帧违规) |
| `agent._collect_sources(person_id, snapshot)` | 列出当前的所有证据源 |
| `agent._is_new_episode(person_id, is_violating)` | 事件去重判断 |

### 外部专家类

| 方法 | 作用 |
|---|---|
| `vl.ask(question, history, context)` | 多轮问 VL,得到语义证据 |

### 输出类

| 方法 | 作用 |
|---|---|
| `agent._emit_raw_event(event)` | 写一条 raw_event 到 logs/raw_event_* |

---

## 四、Agent 循环(独立于主循环)

每 1 秒被主循环回调一次:

```python
async def on_window_complete(sec):
    """主循环调用入口"""
    snapshot = collector.get_snapshot(sec)
    raw_events = await agent.tick(snapshot)
    for ev in raw_events:
        collector.write_raw_event(ev)
```

`agent.tick(snapshot)` 内部:

```
输入 snapshot
     ↓
[1] 多数表决
     对每个作业人员:这 1 秒 25 帧中 ≥80% 违规?
     ↓
[2] 事件去重
     EventDeduplicator.check() → "new_episode" / "ongoing" / "cooldown" / "none"
     ↓ (new_episode 才继续)
[3] 调 VL(按需,1~3 轮)
     ↓
[4] 收集证据源
     video + vl + sensor + location + work_permit
     ↓
[5] A5 最终判断
     综合多源决定:是否输出 raw_event
     ↓
输出 List[RawEvent]
```

---

## 五、raw_event 输出格式

**A5 只输出"候选事件",不带 risk_level**:

```json
{
  "source": "A5",
  "event_id": "A5-1700000000-P7-3",
  "type": "PPE缺失-头盔",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
  "second": 12,
  "evidence": {
    "video":       {"ratio": 0.88, "frame_count": 22},
    "vl_semantic": {"is_violation": true, "evidence": "..."},
    "sensors":     {"gas": "normal"},
    "location":    {"area_id": "动火区-A", "in_danger_zone": true},
    "work_permit": {"required_ppe": ["helmet", ...]}
  },
  "supporting_sources": ["video", "vl_semantic", "location", "work_permit"],
  "timestamp": "..."
}
```

**字段约束**:
- ✅ 有 type(违规类型)
- ✅ 有 evidence(证据链)
- ❌ **无 risk_level**(A6 才加)
- ❌ **无 action / status**(A7 才处理)

---

## 六、文件结构

```
智能体/
├── README.md              ← 本文档
├── __init__.py
├── event_deduplicator.py  ← 事件去重(EventDeduplicator)
└── a5_agent.py            ← 主类(A5Agent + tick 入口)
```

> 注意:没有 `risk_calibrator.py`,因为风险等级判定是 A6 的事

---

## 七、5 个场景的 raw_event 输出

| 场景 | 输出 raw_event 数 | 说明 |
|---|---|---|
| A 正常 | **0** | 全程无违规 |
| B 摘头盔 | **2** | T=8(摘 5s)+ T=22(摘 3s)= 两个独立事件 |
| C 气体上升 | **1** | T=18 气体 alarm 时 |
| D 监护人离岗 | **1** | T=8 起 P11 离岗 |
| E 多重违规 | **2-3** | P7 摘头盔、P11 离岗、气体 alarm 可能同时触发 |

---

## 八、与主循环集成

```python
# main_loop.py 中
from 智能体.a5_agent import A5Agent

collector = AsyncCollector(scenario="B", speed=1.0, log_dir="logs/run")
agent = A5Agent()

# 注册回调:每写完一个秒窗口,触发 agent
async def on_window(sec):
    snapshot = collector.get_snapshot(sec)
    raw_events = await agent.tick(snapshot)
    for ev in raw_events:
        collector.write_raw_event(ev)

# 主循环
await collector.start()
for sec in range(30):
    await asyncio.sleep(1.0 / speed)
    collector.flush_second(sec)
    await on_window(sec)            # ← 触发 A5
await collector.stop()
```

---

## 九、A5Agent 主循环(tick 方法)代码骨架

```python
class A5Agent:
    def __init__(self, vl_model, work_permit):
        self.vl = vl_model
        self.permit = work_permit
        self.deduplicator = EventDeduplicator(cooldown_sec=2.0)

    async def tick(self, snapshot) -> List[Dict]:
        raw_events = []
        for person_id in self.permit["workers"]:
            stats = self._majority_vote(snapshot["cv_logs"], person_id)
            is_violating = stats["ratio"] >= 0.8

            # 1. 事件去重
            episode = self.deduplicator.check(person_id, is_violating)
            if episode != "new_episode":
                continue

            # 2. 调 VL(按需)
            vl_evidence = await self._call_vl(person_id, stats, snapshot)

            # 3. 收集证据
            sources = self._collect_sources(person_id, snapshot, vl_evidence)

            # 4. A5 最终判断(只看证据,不判等级)
            if not self._is_real_violation(stats, vl_evidence, sources):
                continue

            # 5. 输出 raw_event(无 risk_level)
            raw_events.append(self._build_raw_event(person_id, snapshot, sources))

        return raw_events
```

---

## 十、设计要点

| 设计 | 说明 |
|---|---|
| **A5 独立运行** | 主循环 + Agent 可以拆成两个进程/线程 |
| **事件驱动** | Agent 不轮询,被回调触发 |
| **A5 不判风险等级** | 只输出候选事件,risk_level 由 A6 决定 |
| **A5 不发起处置** | 输出 raw_event 后,流转到 A6 |
| **工具简化** | 不引入 LangGraph/LangChain 复杂度,直接方法调用 |
| **可独立测试** | 喂预定义 snapshot 就能单测 agent |

---

## 十一、确认后开工

确认以下设计后,我会写:
1. `event_deduplicator.py`(状态机,约 60 行)
2. `a5_agent.py`(主类 + tick,约 150 行)

不再有 `risk_calibrator.py`(因为 A5 不判风险等级)。