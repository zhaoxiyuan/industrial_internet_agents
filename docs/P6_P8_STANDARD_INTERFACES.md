# P6-P8 标准接口契约

本文定义 P6 动态监测、P7 风险研判、P8 人机处置之间必须稳定的集成边界。模块内部可以重构，但本页标为“标准”的函数签名、路径和字段不应无版本变更地破坏。

## 1. 端到端流程

```text
P5 条件核验
  → P6 候选事件 candidate_events
  → P7 风险事件 risk_events / a6_*.json
  → P8 处置任务 P8Job
  → HITL 或飞书卡片
  → 终态归档
  → P9 闭环
```

## 2. 每阶段统一入口

所有阶段模块向 `agents/main_agent.py` 暴露相同接口：

```python
def execute_stage(job_id: str) -> dict:
    ...
```

返回对象至少包含：

| 字段 | 类型 | 约束 |
|---|---|---|
| `job_id` | string | 与调用参数一致 |
| `stage` | string | `P6`、`P7` 或 `P8` |
| `started_at` | string | ISO-8601，建议 UTC |
| `completed` | boolean | 阶段是否成功完成 |
| `completed_at` | string | 成功时存在 |
| `error` | string | 失败时存在，禁止与成功状态并存 |

阶段输出路径统一为 `data/jobs/{job_id}/p{n}_result.json`。P6/P7/P8 还可以维护各自的大体量或长期数据目录，但下游主流程只应依赖标准结果文件。

## 3. 标识符

| 标识符 | 示例 | 产生方 | 用途 |
|---|---|---|---|
| `job_id` | `20260914123000123` | 主工作流 | 跨 P6-P10 的一级关联键；HTTP 监测要求 17 位数字 |
| `task_id` | 业务任务 ID | P2/P5 | 兼容工具调用，不应替代 job_id 作为目录键 |
| `event_id` | `A5-...` | P6/A5 | 原始候选事件 ID |
| `a6_event_id` | `A6-...` | P7/A6 | 风险研判记录 ID |
| `p8_job_id` | `P8J-20260914-123000-001` | P8 | 处置任务 ID；可聚合多个 a6_event_id |
| `alert_id` | 通常等于 p8_job_id | 飞书适配层 | 卡片幂等与 card_id 索引键 |

禁止用 `p8_job_id` 代替 `job_id` 定位 `data/jobs/{job_id}`。

## 4. P6 → P7 契约

P7 只依赖 P6 结果中的 `candidate_events`：

```json
{
  "candidate_events": [
    {
      "event_id": "A5-...",
      "event_type": "candidate_event",
      "type": "PPE缺失",
      "description": "...",
      "timestamp": "ISO-8601",
      "confidence": 0.9,
      "evidence": [],
      "person": {},
      "severity": "PENDING_A6"
    }
  ]
}
```

硬性字段为 `event_id`。缺少 event_id 的条目由 P7 跳过。P6 不得在 `severity` 中伪造最终风险等级。

实时链路可以绕过 `p6_result.json`，直接调用：

```python
await trigger_a6_assessment(event_id, event_data, job_id=job_id)
```

## 5. P7 → P8 契约

主流程文件使用 `risk_events`；per-job 实时文件使用 `P7/a6_*.json`。P8 的 `read_p7_events(job_id)` 会读取两者。

P8 真正需要的规范字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `a6_event_id` 或 `event_id` | string | 风险事件标识 |
| `level` 或 `risk_level` | enum/string 或 int | `LOW/MEDIUM/HIGH/CRITICAL`，或 1-5 |
| `risk_basis` | string | 创建 P8Job 的风险依据 |
| `suggestions` | array | 推荐措施 |
| `evidence` | object/array | 可追溯证据 |

标准枚举映射：`1-2=LOW`、`3=MEDIUM`、`4=HIGH`、`5=CRITICAL`。下游判断通道时必须先归一化，不能直接比较数字和字符串。

## 6. P8 → 外部系统契约

### 工作记忆查询

```http
GET /api/jobs/{job_id}/working-memory
```

```json
{
  "status": "ok",
  "job_id": "...",
  "working_memory": [],
  "archived_recent": []
}
```

### 飞书卡片按钮值

每个按钮的 `value` 是 JSON 字符串，解码后至少包含：

```json
{
  "action": "approve",
  "alert_id": "P8J-...",
  "job_id": "20260914123000123"
}
```

`action` 是回调必填字段；新卡必须同时带 `alert_id` 和 `job_id`。缺少 job_id 的旧卡只做审计与视觉兼容，不驱动 P8Job 状态。

## 7. 响应与错误规范

LLM 工具优先使用 `agents.utils.response_utils`：

- 成功：`schema_version + command + timestamp + result + errors`
- 失败：`schema_version + error.code/message/details/recoverable/action`

阶段入口使用轻量阶段结果结构，不应与工具 envelope 混用。HTTP 接口必须使用合适的 HTTP 状态码；读取不到业务数据时，P7/P8 查询接口通常返回 200 和空数组。

## 8. 兼容性规则

1. 新路径必须传 job_id；不传 job_id 的全局路径仅用于历史调试。
2. 新增字段应向后兼容；删除或重命名标准字段需要升级 schema version。
3. JSON 时间统一 ISO-8601，新增代码优先使用带时区的 UTC。
4. 读取批量文件时允许跳过单个损坏文件，但必须记录日志。
5. 所有写接口必须考虑幂等：P8Job 按 p8_job_id upsert，飞书按 idempotency_key/alert_id 去重。

模块细节见 [P6](P6.md)、[P7](P7.md)、[P8](P8.md)、[飞书接口](FEISHU_API.md) 和 [飞书配置教程](FEISHU_CONFIG.md)。
