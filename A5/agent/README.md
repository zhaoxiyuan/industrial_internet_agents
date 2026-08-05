# A5 Agent — 说明

> **定位**: 接收每秒快照 → 自主决策 + 输出 raw_event
> **状态**: ✅ 已完成

---

## 文件清单

| 文件 | 作用 |
|---|---|
| `a5_agent.py` | 主类: tick() + ReAct Agent + Mock LLM 降级 |
| `tools.py` | 8 个 LangChain @tool |
| `event_deduplicator.py` | 事件去重状态机(降级模式保留) |
| `system_prompt.py` | 系统提示词管理(get/update/reset) |
| `work_permit_rules.py` | 作业票规则提示词管理(前端可编辑) |
| `AGENT_DESIGN.md` | 完整设计文档 |
| `SENSOR_DETECTION.md` | 传感器&监护人检测设计 |

---

## 8 个工具

| 工具 | 作用 |
|---|---|
| `query_raw_logs` | 按 wall_time 查原始日志 |
| `get_current_snapshot` | 取某秒快照 |
| `analyze_ppe_compliance` | 1 秒多数表决 |
| `call_vl_expert` | VL 专家(占位) |
| `query_past_events` | 查已落盘 raw_event(去重) |
| `query_timeline` | 查违规趋势 |
| `check_sensor_alarm` | 检查传感器告警 |
| `check_supervisor_absence` | 检查监护人离岗 |

---

## 三维检测(降级模式)

`_rule_fallback` 每次 tick 检测:

| 维度 | 逻辑 | 场景 |
|---|---|---|
| PPE 缺失 | CV 多数表决(≥80%) | B/E |
| 传感器告警 | status="alarm" | C/E |
| 监护人离岗 | not in_danger_zone | D/E |

去重: 每 tick 查 `query_past_events`,同一事件覆盖写(更新 last_seen)。

---

## 事件生命周期

```
T=8  首次检测 → 写 raw_event(ongoing)
T=9  持续中  → 覆盖同一文件(更新 last_seen + duration)
...
T=13 恢复    → 覆盖写(status=closed)
```

---

## LLM 模式

| 模式 | 条件 | 行为 |
|---|---|---|
| Mock(默认) | 无 .env 配置 | `_rule_fallback` 降级 |
| OpenAI | `LLM_PROVIDER=openai` | ChatOpenAI |
| Ollama | `LLM_PROVIDER=ollama` | ChatOllama |

---

## 前端集成

- `demo/app.py` 每 tick 调 `agent.tick()`,通过 WebSocket 推送
- 前端页面底部可编辑 `work_permit_rules`(运行时生效)
- raw_event 文件持久化到 `logs/{scenario}/raw_events/`
