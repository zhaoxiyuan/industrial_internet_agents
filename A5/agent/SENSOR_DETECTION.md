# 传感器 & 监护人检测 — 已实现

> **状态**: ✅ 已完成
> **覆盖场景**: A(正常) / B(PPE) / C(气体) / D(离岗) / E(多重)

---

## 检测维度

| 维度 | 工具 | `_rule_fallback` 逻辑 | 场景 |
|---|---|---|---|
| PPE 缺失 | `analyze_ppe_compliance` | 多数表决 ≥80% → ongoing / 恢复 → closed | B/E |
| 传感器告警 | `check_sensor_alarm` | status=alarm → ongoing / 恢复 → closed | C/E |
| 监护人脱岗 | `check_supervisor_absence` | not in_danger_zone → ongoing / 返回 → closed | D/E |

## 去重机制

三种违规使用相同的去重逻辑:

1. `query_past_events` 查此人/系统的历史 ongoing 事件
2. 已有 ongoing → 跳过
3. 违规消失 + 之前有 ongoing → 输出 closed
4. 文件按 wall_time 命名,不覆盖

## 改动文件

| 文件 | 改动 |
|---|---|
| `agent/tools.py` | 新增 `check_sensor_alarm` + `check_supervisor_absence`(共 8 工具) |
| `agent/a5_agent.py` | `_rule_fallback` 扩展:PPE + 传感器 + 监护人三段检测 |
| `agent/system_prompt.py` | 新增传感器和监护人检测能力说明 |
