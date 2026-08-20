# P8 卡片按钮回调 Agent（p8_card_action_agent）

## 概述

**飞书卡片按钮点击的专用决策 Agent**（2026-08-20 新增），负责根据用户在飞书卡片上点击的按钮，修改对应 P8_job 的状态。

> **不复用 P8Agent**：避免重 system_prompt、避免 LLM 递归调 `notify_feishu`、决策可审计。
> 详细背景：见 [`P8_DISPOSITION_AGENT.md` § 卡片按钮回调](P8_DISPOSITION_AGENT.md#卡片按钮回调--cardactionagent2026-08-20-新增)。

## 主要功能

1. 接收飞书卡片按钮点击事件（ack/handle/false_alarm/approve/reject/escalate/rectify/resume）
2. 调 LLM 决定如何修改 P8_job 状态（status + decision + note）
3. 持久化：dump per-job `working_memory.json`；终态双写到长期记忆
4. 异步触发，失败不影响飞书回调响应（toast 仍即时返回）

## 入口函数

| 函数 | 说明 |
|------|------|
| `create_card_action_agent(user_ctx=None, job_id=None)` | 构造 Agent 实例（按 `user_ctx + job_id` 缓存复用） |
| `run_card_action_agent(message, *, user_ctx=None, job_id=None)` | 运行 CardActionAgent 一次（fire-and-forget） |
| `make_apply_card_action_tool(job_id)` | **工厂**：为指定 `job_id` 返回绑定好的 `apply_card_action` tool |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `apply_card_action` | 飞书卡片按钮被点击后，CardActionAgent 唯一工具 | 按 ACTION_TO_STATUS 映射表 + LLM 推理修改 P8_job | ❌ 不需要 |

> 单一工具：避免 LLM 误调 `notify_feishu` / `update_job` / `hitl_decide` 等其它工具。
> `job_id` 通过 **closure** 绑进工具（不让 LLM 传，避免覆盖正确归属）。

### `apply_card_action` 入参

| 参数 | 必填 | 说明 |
|------|------|------|
| `alert_id` | ✅ | 业务告警 ID（= `p8_job_id`），从飞书 callback `action.value` 透传 |
| `action` | ✅ | 按钮 verb：`ack` / `handle` / `false_alarm` / `approve` / `rectify` / `reject` / `escalate` / `resume` |
| `operator_open_id` | ✅ | 操作人飞书 open_id（从飞书 callback `event.operator.open_id` 透传） |
| `operator_name` | ✅ | 操作人姓名（callback `event.operator.user_name`，缺省查 USER_MAP） |
| `note` | ❌ | LLM 推理出的备注（追加到 `P8_job.note`） |
| `new_status` | ❌ | LLM 显式指定的新 status（缺省按 `ACTION_TO_STATUS[action]` 映射） |

### 错误码

| Code | 触发条件 |
|------|----------|
| `INVALID_ARGUMENT` | `alert_id` 空 / `action` 未知 / `status` 非法 / `job_id` 含非法字符 |
| `P8_JOB_NOT_FOUND` | `working_memory` 中找不到 `alert_id` 对应的 P8_job |
| `STORAGE_ERROR` | `load_working_memory` 失败 |

### Action → 状态机映射表

```python
ACTION_TO_STATUS = {
    # PUSH 三按钮（channel=PUSH）
    "ack":         "notified",      # 已知悉：维持
    "handle":      "notified",      # 立即处理：维持
    "false_alarm": "completed",     # 误报：终态
    # HITL 五按钮（channel=HITL）
    "approve":     "completed",
    "rectify":     "completed",
    "reject":      "rejected",
    "escalate":    "escalated",
    "resume":      "resumed",
}

ACTION_TO_DECISION = {
    "ack":         None,           # 不写 decision 字段
    "handle":      None,
    "false_alarm": "approve",      # 误报视同批准关闭
    "approve":     "approve",
    "rectify":     "rectify",
    "reject":      "reject",
    "escalate":    "escalate",
    "resume":      "resume",
}
```

终态集合（`skipped=true` 自动触发）：`{completed, rejected, escalated, resumed}`。

## HITL 支持

**无 HITL middleware**。异步链路不再二次确认。

> 为什么不需要：卡片按钮点击本身就是"操作人决策"（已经经过飞书 UI 确认）；
> 异步 daemon 链路再走 HITL 等于"再点一次"，反而引入额外人工成本。

## 推送策略

**N/A** — CardActionAgent 不推送任何消息（禁止 `notify_feishu` 递归调用）。

## 卡片回调异步分发

[`openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_card.py`](../../openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_card.py)：

- `_extract_action(payload)`：扩展返回 `job_id`（从 `action.value.job_id` 解析；旧卡片无该字段 → `None`）
- `_handle_card_action_with_llm_async(...)`：daemon 线程 fire-and-forget 调 CardActionAgent
  - `job_id is None` → 跳过（旧卡片向后兼容）
  - 异常 → `logger.exception`（不阻塞 toast 响应）
- `process_card_callback(...)`：紧跟 `_replace_card_async` 后触发新 dispatcher（独立 daemon）

## 文件位置

| 文件 | 用途 |
|------|------|
| `A7/middleware/p8_card_action_agent.py` | Agent + 工具 + ACTION_TO_STATUS 表 |
| `agents/system_prompt/P8_CARD_ACTION_SYSTEM_PROMPT.md` | Slim system prompt |
| `tests/test_p8_card_action_agent.py` | 13 个测试（CA1-CA13） |

## 长期记忆后端

复用 [`A7.storage`](../../A7/storage/) 的 `dump_working_memory`（per-job 落盘）+ `save_archived_job`（终态双写）— 与 P8 处置 Agent 同模式，但**直接**调（不经 middleware）。

| 写入入口 | 调用时机 |
|---------|---------|
| `dump_working_memory(job_id, new_working)` | 非终态状态变更 |
| `save_archived_job(pid, archived, job_id=job_id)` | 终态状态变更（per-job + 全局双写） |

**无 MemorySaver**：CardActionAgent 不使用 langgraph checkpointer；每次 invoke 是独立 fire-and-forget 单元。

## 设计要点

1. **不复用 P8Agent**：避免 LLM 误调 `notify_feishu` 触发递归推送；保持决策可审计
2. **closure 绑 job_id**：`make_apply_card_action_tool(job_id)` 把 job_id 闭包进工具，工具签名不暴露
3. **无 HITL / 无 checkpointer**：异步链路单次 invoke 即可
4. **action 集中映射表**：`ACTION_TO_STATUS` / `ACTION_TO_DECISION` 代码即文档（CA1 测试守护）
5. **向后兼容**：旧卡片（action.value 缺 `job_id`）→ daemon 自动跳过，仅走审计 + 视觉替换

## 持久化时序

```
apply_card_action invoke
  1. load_working_memory(job_id)              读 per-job JSON
  2. 定位 target P8_job（按 alert_id = p8_job_id）
  3. 终态判断 → skip / 计算 new_status
  4. 构造 updated P8_job dict（保留所有原字段）
  5. reducer-style 更新 working_memory list
  6. dump_working_memory(job_id, new_working)  原子写（portalocker 兜底）
  7. 终态 → save_archived_job(pid, archived, job_id=job_id)  全局+per-job 双写
```

## 失败语义

- **load 失败** → 返 `STORAGE_ERROR` 给 LLM
- **dump 失败** → `logger.warning` + LLM 收到 ok（下次 flush 兜底）
- **save_archived_job 失败** → `logger.warning`（全局层失败才阻断）
- **CardActionAgent LLM 失败** → 顶层 daemon `logger.exception`（不阻断飞书 toast）

## 测试

[`tests/test_p8_card_action_agent.py`](../../tests/test_p8_card_action_agent.py)：13 个测试

| Test ID | 覆盖 |
|---------|------|
| CA1 | `ACTION_TO_STATUS` 表覆盖 8 个 action；`ACTION_TO_DECISION` 完整 |
| CA2 | `make_apply_card_action_tool(job_id)` 返回绑定 job_id 的工具 |
| CA3 | `apply_card_action`: ack → status=notified（维持）+ working_memory 落盘 |
| CA4 | `apply_card_action`: approve → status=completed + decision=approve + 双写归档 |
| CA5 | `apply_card_action`: reject → status=rejected + decision=reject |
| CA6 | `apply_card_action`: alert_id 空 → `INVALID_ARGUMENT` |
| CA7 | `apply_card_action`: 未知 action → `INVALID_ARGUMENT` |
| CA8 | `apply_card_action`: 已是终态 → `skipped=true`（不修改 working_memory） |
| CA9 | `create_card_action_agent` 返回 LangChain Agent；同 cache key 复用 |
| CA10 | `run_card_action_agent` 签名透传 user_ctx / job_id |
| CA11 | feishu_card `_handle_card_action_with_llm_async`：job_id 缺失时跳过 |
| CA12 | feishu_card `_handle_card_action_with_llm_async`：job_id 存在时启动 daemon |
| CA13 | `build_feishu_card` job_id 参数写入每个按钮 value JSON |