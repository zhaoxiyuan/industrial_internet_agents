# A5 智能体 — 设计说明

> **定位**:接收每秒快照 → 自主决策(去重 + 调 VL + 终判)+ 输出 raw_event
> **文件**:`tools.py`, `event_deduplicator.py`, `a5_agent.py`
> **状态**:已完成

---

## 一、明确边界

| 谁 | 做什么
|---|---|
| **A5**(本模块) | 接收日志 → 识别候选事件 → 输出 raw_event
| **A6** | 接收 raw_event → 判风险等级 → 分级推送
| **A7** | 接收分级事件 → 派单/通知/停工

**A5 不判风险等级,不发起处置。**

---

## 二、与主循环关系

A5 是独立模块,**被主循环每秒回调一次**:

```
主循环(每 1 秒):
    wall_time = collector._sec_to_wall(sec+1)
    snapshot  = collector.get_snapshot(wall_time)
    events   = await agent.tick(wall_time, snapshot)
```

Agent 没有自己的 while 循环 / 监听线程 / 事件驱动。

---

## 三、工具清单(3 个文件)

### 3.1 `tools.py` — 4 个 LangChain `@tool`

| 工具 | 作用 | 参数 |
|---|---|---|
| `query_raw_logs` | 按 wall_time 查原始日志 | start/end_wall(ISO), source_type, person_id |
| `get_current_snapshot` | 取某秒快照 | wall_time(ISO) |
| `analyze_ppe_compliance` | 1 秒多数表决 | cv_logs, person_id, threshold, required_ppe |
| `call_vl_expert` | 调 VL 专家(**占位**) | question, history, context |

### 3.2 `event_deduplicator.py` — 事件去重

每个作业人员独立的状态机:

```
NO_ACTIVE →(违规)→ NEW_EPISODE(输出事件)→ ACTIVE(持续违规)
ACTIVE →(恢复)→ COOLDOWN →(冷却期满)→ NO_ACTIVE
COOLDOWN →(再违规)→ ACTIVE(视为同一事件延续)
```

### 3.3 `a5_agent.py` — 主循环(每秒一次)

`tick(wall_time, snapshot)` 内部逻辑:

```
1. 多数表决   → analyze_ppe_compliance
2. 事件去重   → deduplicator.check()
3. 调 VL      → call_vl_expert(占位,Demo 不阻塞)
4. 收集证据   → 多源(video + vl + sensor + location + permit)
5. A5 终判   → _is_real_violation()
6. 输出 raw_event → 无 risk_level
```

---

## 四、raw_event 输出格式

```json
{
  "source": "A5",
  "event_id": "A5-P7-1734567890",
  "type": "PPE缺失-头盔",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
  "second": 12,
  "wall_time": "2026-08-04T14:32:12.000",
  "evidence": {
    "video":       {"ratio": 0.88, "frame_count": 22},
    "vl_semantic": {...},
    "sensors":     {...},
    "location":    {...},
    "work_permit": {...}
  },
  "supporting_sources": ["video", "vl_semantic", "location", "work_permit"],
  "explanation": "CV 多数表决... | VL 判断:... | 在危险区...",
  "note": "A5 不判定 risk_level,由 A6 完成"
}
```

---

## 五、事件去重效果(场景 B 示例)

```
T=7   NO_ACTIVE  + 无违规 → NO_ACTIVE  → 不输出
T=8   NO_ACTIVE  + 违规   → NEW_EPISODE → 输出 raw_event #1 ★
T=9   NEW_EPISODE+ 违规   → ACTIVE      → 不输出
...
T=13  ACTIVE     + 无违规 → COOLDOWN    → 不输出
T=16  COOLDOWN   + 无违规 → NO_ACTIVE   → 不输出(冷却到期)
T=22  NO_ACTIVE  + 违规   → NEW_EPISODE → 输出 raw_event #2 ★
```

---

## 六、防未来

```python
# 主循环调用前
collector.set_agent_now(wall_time)

# agent.tick 内部自动调用 set_agent_now
# tools.query_raw_logs/get_snapshot 拒绝查询 > agent_now 的时间
```

---

## 七、特殊处理

| 场景 | 处理 |
|---|---|
| **监护人 P11** | `required_ppe=["helmet"]`(不检查护目镜) |
| **VL 不可用** | 返回 `_placeholder=True`,智能体降级到规则判断 |
| **多数表决阈值** | 默认 0.8(25 帧中 20 帧违规) |
| **冷却期** | 2 秒(防抖动,短暂恢复不算新事件) |
