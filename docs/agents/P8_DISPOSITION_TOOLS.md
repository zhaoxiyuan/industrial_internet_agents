# P8 Agent 工具 I/O 参考（P8_DISPOSITION_TOOLS）

> 文件：[agents/p8_disposition_agent.py](../../agents/p8_disposition_agent.py)
> 蓝图版本（§ 6.1 / § 7 / § 11.3），2026-08-19。

## 0. 总览

P8 人机协同处置 Agent 暴露 **6 个工具 + 3 个入口函数**。

| 工具 / 函数 | 类型 | HITL | 修改 state？ | 备注 |
|---|---|---|---|---|
| [update_job](#1-update_job) | 工具 | ✅ 必须确认 | ✅ working_memory 写入 | 创建 / 更新 P8_job |
| [hitl_decide](#2-hitl_decide) | 工具 | ✅ 必须确认 | ✅ working_memory patch | 强制进入 HITL 决策 |
| [read_p7_events](#3-read_p7_events) | 工具 | ❌ 自动批准 | ❌ 只读 | 读 `data/jobs/{job_id}/p7_result.json` |
| [notify_feishu](#4-notify_feishu) | 工具 | ✅ 必须确认 | ❌ 外部副作用 | 飞书 Card 2.0 + 必带 buttons |
| [list_active_p8_jobs](#5-list_active_p8_jobs) | 工具 | ❌ 自动批准 | ❌ 只读占位 | 真实读取走 REST 端点 |
| [recall_jobs](#6-recall_jobs) | 工具 | ❌ 自动批准 | ❌ 只读 | 罗盘长期记忆 LLM 入口 |
| [create_disposition_agent](#7-create_disposition_agent) | 工厂 | — | — | 基础版（无 HITL middleware） |
| [create_disposition_agent_with_hitl](#8-create_disposition_agent_with_hitl) | 工厂 | — | — | HITL 版（生产用） |
| [run_disposition_agent](#9-run_disposition_agent) | 入口 | — | — | 调一次；含 thread_id / user_ctx |
| [disposition_demo](#10-disposition_demo) | 入口 | — | — | Gradio/chat_reply 兼容 |

> **HITL 中断矩阵**（在 [`create_disposition_agent_with_hitl`](#8-create_disposition_agent_with_hitl) 中注册）：
> `update_job` / `hitl_decide` / `notify_feishu` → 中断等待人工确认；
> `read_p7_events` / `list_active_p8_jobs` / `recall_jobs` → 自动放行（只读）。

---

## 1. `update_job`

### 用途

**创建** 或 **更新** P8_job。LLM 从 P7 风险研判结果推断风险等级与依据后调用，
是 P8 工作记忆（working_memory）的**唯一写入入口**。

### 签名

```python
def update_job(
    a6_event_ids: list[str],
    level: str,
    risk_basis: str,
    p8_job_id: Optional[str] = None,
    job_id: Optional[str] = None,           # 2026-08-20 新增
    status: Optional[str] = None,
    channel: Optional[str] = None,
    note: Optional[str] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `a6_event_ids` | `list[str]` | ✅ | 关联的 A6 输出 ID 列表（≥1）；N=1 单事件处置，N>1 风险叠加（聚合 P8_job） |
| `level` | `str` | ✅ | 风险等级：`LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `risk_basis` | `str` | ✅ | 风险依据（拼接各 a6_event 的 basis；聚合 P8_job 必须记录聚合原因） |
| `p8_job_id` | `Optional[str]` | ❌ | `None` → 新建（自动生成 `P8J-YYYYMMDD-HHMMSS-NNN`）；已有 ID → 更新该 P8_job |
| `job_id` | `Optional[str]` | ❌ | **2026-08-20 新增**。主流程作业 ID（如 `JOB-20260813-001` / 17 位时间戳）；透传到 working_memory / 长期归档 → per-job 持久化依据。Bot 模式 + 无作业上下文可留 None。 |
| `status` | `Optional[str]` | ❌ | 目标状态（`pending` / `notified` / `waiting_decision` / `completed` / `rejected` / ...） |
| `channel` | `Optional[str]` | ❌ | 通道：`HITL` / `PUSH` |
| `note` | `Optional[str]` | ❌ | 备注（覆盖式） |
| `tool_call_id` | `str` | ⚙️ 由 LangGraph 注入 | 不传会抛 `Every tool call MUST have a corresponding ToolMessage` |

### 出参

LangGraph `Command(update={...})`：

```python
Command(update={
    "messages": [ToolMessage(...)],   # 成功/失败 JSON 都包成 ToolMessage
    "working_memory": [job],          # reducer 按 p8_job_id 自动 upsert
})
```

成功响应 JSON：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "update_job",
  "data": {
    "status": "ok",
    "p8_job_id": "P8J-20260819-100000-001",
    "job": {
      "p8_job_id": "P8J-...",
      "a6_event_ids": ["A6-001"],
      "risk_basis": "可燃气体浓度超标 1.2 倍...",
      "max_level": "HIGH",
      "urgency_emoji": "🟠",
      "assignee_role": "属地责任人 + 班组长",
      "channel": "HITL",
      "status": "pending",
      "note": "",
      "due_at": "2026-08-19T11:00:00+00:00",
      "created_at": "2026-08-19T10:00:00+00:00"
    }
  }
}
```

### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_LEVEL` | level 不是 LOW/MEDIUM/HIGH/CRITICAL | False |
| `INVALID_ARGUMENT` | risk_basis 为空 / P8JobUpdate 校验失败 | False |

### 副作用

- ✅ `working_memory` 写入（reducer 按 `p8_job_id` upsert）
- ✅ `messages` 追加 ToolMessage
- 🔁 **终态监听**：[`P8ArchiveMiddleware`](../../A7/middleware/p8_archive_middleware.py) 在 after_model 检测
  `status ∈ {completed, rejected, escalated, resumed}` → 自动调用
  [`A7.storage.save_archived_job`](../../A7/storage/p8_long_term.py) 写入长期记忆 + 从 working_memory 删除

### 默认字段计算（蓝图 § 5.3）

| level | urgency_emoji | assignee_role | due_at（hours） | channel |
|-------|---------------|---------------|-----------------|---------|
| LOW | 🟢 | 属地巡查员 | 24 | PUSH |
| MEDIUM | 🟡 | 属地责任人 | 4 | PUSH |
| HIGH | 🟠 | 属地责任人 + 班组长 | 1 | HITL |
| CRITICAL | 🔴 | HSE 经理 | 0（实时） | HITL |

---

## 2. `hitl_decide`

### 用途

把指定 P8_job 标记为「等待人工决策」—— 强制 `channel=HITL` + `status=waiting_decision`。
**仅修改状态字段**，不动 `risk_basis` / `max_level` / `assignee_role` 等元数据。

### 签名

```python
def hitl_decide(
    p8_job_id: str,
    options: list[str],
    note: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `p8_job_id` | `str` | ✅ | 必须以 `P8J-` 开头 |
| `options` | `list[str]` | ✅（空时回退默认） | 候选决策列表（如 `["approve", "rectify", "reject", "escalate"]`）；空列表 → 默认 4 项 |
| `note` | `str` | ❌ | 备注（写入 note 字段） |
| `tool_call_id` | `str` | ⚙️ 注入 | 同 update_job |

### 出参

```python
Command(update={
    "messages": [ToolMessage(...)],
    "working_memory": [patch],   # patch 含 p8_job_id + 改的字段
})
```

成功响应 JSON：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "hitl_decide",
  "data": {
    "status": "ok",
    "p8_job_id": "P8J-...",
    "patch": {
      "p8_job_id": "P8J-...",
      "channel": "HITL",
      "status": "waiting_decision",
      "note": "...",
      "options": ["approve", "rectify", "reject", "escalate"],
      "hitl_at": "2026-08-19T10:00:00+00:00"
    }
  }
}
```

> ⚠️ **patch 行为说明**：LangGraph reducer 按 `p8_job_id` **整对象 upsert**（不支持 partial update）。
> 当前实现把 patch 作为"最小可观察"——既有 `risk_basis` / `max_level` / `due_at` 等字段可能被覆盖。
> 蓝图中明确要求 LLM 在下一次 invoke 用 `update_job(p8_job_id=..., ...)` 补全元数据。

### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | p8_job_id 缺失 / 不以 `P8J-` 开头 | False |

### 副作用

- ✅ `working_memory` upsert（同 pid 覆盖）
- ✅ `messages` 追加 ToolMessage
- ⚙️ 触发 `HumanInTheLoopMiddleware` 中断（若 Agent 挂载了 HITL middleware）

---

## 3. `read_p7_events`

### 用途

读 P7 风险研判阶段输出（`data/jobs/{job_id}/p7_result.json`）。只读，不修改 state。
Bot 模式下用户在群内问「这个作业有什么风险」时调用。

### 签名

```python
def read_p7_events(job_id: str) -> str:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `job_id` | `str` | ✅ | 主流程作业 ID（如 `JOB-20260813-001`） |

### 出参

标准 JSON 字符串：

| 场景 | 响应 |
|------|------|
| 找到 p7_result.json | `{"status":"ok", "tool":"read_p7_events", "data":{"job_id":..., "events":[...], "event_count":N}}` |
| 文件缺失 | `{"status":"ok", "tool":"read_p7_events", "data":{"job_id":..., "events":[], "note":"p7_result.json 不存在"}}`（**空数组而非 404**） |
| JSON 解析失败 | `{"status":"error", "error":{"code":"P7_READ_FAILED", ...}}` |

### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | job_id 为空 | False |
| `P7_READ_FAILED` | JSON 解析异常 | True |

### 副作用

- ❌ 不修改任何 state
- ✅ 仅读盘（`project_root/data/jobs/{job_id}/p7_result.json`）

---

## 4. `notify_feishu`

### 用途

通过 OpenClaw Channel Gateway 推送飞书**交互式告警卡片**（Card 2.0 schema）。
P8_agent 在 `channel=PUSH` 的 P8_job 创建/变更后调用，把告警推到飞书群。

### 设计边界（与 chat_reply.py 的分工）

| 场景 | 走哪个 | 格式 |
|------|--------|------|
| 普通对话 / 提问 | `chat_reply.py` 自动 | msg_type=text（纯文本） |
| 告警 / 处置推送（HITL 按钮需求） | `notify_feishu` 工具 | msg_type=interactive + Card 2.0 + 必带 buttons |

### 签名

```python
def notify_feishu(
    p8_job_id: str,
    title: str,
    body: str,
    risk_level: str,
    assignee_role: str,
    job_id: str,
    a6_event_ids: list[str],
    options: list[str],                # ★ 必填
    *,
    chat_id:    Optional[str] = None,  # 飞书群 ID（oc_xxx）直传
    group_name: Optional[str] = None,  # 按 FEISHU_GROUP_MAP.name 反查
    alert_id:   Optional[str] = None,  # 默认 = p8_job_id
    account_id: Optional[str] = None,  # Gateway 账号 ID（多账号 bot）
) -> str:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `p8_job_id` | `str` | ✅ | P8_job ID（与 update_job 共享） |
| `title` | `str` | ✅ | 卡片标题（含 emoji + 风险等级） |
| `body` | `str` | ✅ | 卡片正文（lark_md 语法） |
| `risk_level` | `str` | ✅ | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `assignee_role` | `str` | ✅ | 责任岗位（描述性文字；仅记录/审计，**不是**收件人 ID） |
| `job_id` | `str` | ✅ | 主流程作业 ID |
| `a6_event_ids` | `list[str]` | ✅ | 关联 A6 输出 ID 列表（≥1） |
| `options` | `list[str]` | ✅ **必填** | 按钮列表 `["label:action", ...]`；至少 1 个 |
| `chat_id` | `Optional[str]` | 互斥二选一 | 飞书群 ID（`oc_xxx`）直传 |
| `group_name` | `Optional[str]` | 互斥二选一 | 按 `FEISHU_GROUP_MAP.name` 反查 chat_id |
| `alert_id` | `Optional[str]` | ❌ | 业务告警 ID（callback 异步更新卡片）；默认 = `p8_job_id` |
| `account_id` | `Optional[str]` | ❌ | Gateway 账号 ID（多账号 bot） |

### 收件人约束

- **仅支持群发**：`chat_id` / `group_name` 二选一；`name` / `open_id` 不再接受（单聊 DM 暂不支持 interactive 卡片）
- **互斥校验**：传两个返回 `INVALID_ARGUMENT`
- **缺失校验**：都不传返回 `INVALID_ARGUMENT`

### options 约束（2026-08-19 收紧）

- **必填**：至少 1 个按钮
- **格式**：`["label:action", ...]`（与 CLI `--option` 同构）
- **解析**：`feishu_sender.parse_options` —— 缺冒号 / label 为空 / action 为空均失败

### 出参

成功响应 JSON：

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "notify_feishu",
  "data": {
    "p8_job_id": "P8J-...",
    "options_count": 3,
    "intent_id": "<gateway_intent_id>",
    "status": "sent",
    "message_id": "<feishu_message_id>",
    "alert_id": "gas_20260819_001"
  }
}
```

### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | 收件人缺失 / options 缺失 / 互斥冲突 / parse_options 失败 | False |
| `FEISHU_PUSH_FAILED` | feishu_sender 异常（Gateway 4xx/5xx） | True |

### 副作用

- ❌ 不修改 LangGraph state（外部副作用）
- ✅ 飞书群收到 Card 2.0 + buttons
- ✅ 飞书用户点按钮 → 飞书 callback → Gateway → `web /api/feishu/card-callback` → P8 处置 Agent 自动继续

### 发送链路

```
LLM → notify_feishu(p8_job_id, title, body, ..., options=[...], group_name="应急响应群")
  → parse_options → [(label, action), ...]
  → build_feishu_card(text=body, options=[...], title=title, alert_id=alert_id)
  → feishu_sender.send_to_group_card(card, group_name=..., alert_id=..., idempotency_key=p8_job_id)
    → channel_gateway_client.send_message(
        conversation_id=<chat_id>,
        receive_id_type="chat_id",
        msg_type="interactive",
        content=<Card 2.0 JSON>,
      )
  → Gateway :8787/v1/messages/send → 飞书 im/v1/messages
  → 用户点击 → 飞书 callback → Gateway → web /api/feishu/card-callback → P8 Agent 继续
```

---

## 5. `list_active_p8_jobs`

### 用途

LLM 工具入口占位：**不**直接读 LangGraph state（state 在 runtime 里，工具不可访问）。
真实读取走 REST 端点 [`GET /api/jobs/{job_id}/working-memory`](#)。

### 签名

```python
def list_active_p8_jobs() -> str:
```

### 入参

无。

### 出参

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "list_active_p8_jobs",
  "data": {
    "active_p8_jobs": [],
    "note": "工作记忆查询走 REST 端点 GET /api/jobs/{job_id}/working-memory；Bot 模式下 chat_reply 会自动把 working_memory 快照拼到回复下方。"
  }
}
```

### 错误码

无（永远返回 `status=ok` 占位）。

### 副作用

- ❌ 完全只读

### 真实读取路径

| 调用方 | 路径 |
|--------|------|
| 前端 Web 面板 | `GET /api/jobs/{job_id}/working-memory`（[`A7/api/p8_working_memory_ctrl.py`](../../A7/api/p8_working_memory_ctrl.py)） |
| chat_reply daemon | 自动把 working_memory 快照拼到 LLM 回复下方 |
| 单元测试 | [`tests/test_a7_api_p8_working_memory.py`](../../tests/test_a7_api_p8_working_memory.py) |

---

## 6. `recall_jobs`

### 用途

**罗盘长期记忆 LLM 入口**。从 [`A7/storage/p8_long_term.py`](../../A7/storage/p8_long_term.py) 索引层 + 数据层接口查询历史 P8_job。仅在用户**明确**要求查询历史时调用（"昨天那个事件最后怎么处理的？"）。

### 「两步走」检索模式

1. **第一步**：调 `recall_jobs(query=...)` → 拿到索引层一句话描述（最多 20 条）
2. **第二步**：用户确认后，再调 `recall_jobs(detail_p8_job_id="<p8_job_id>")` → 拿完整归档数据

### 签名

```python
def recall_jobs(
    query: str,
    detail_p8_job_id: Optional[str] = None,
) -> str:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `str` | 与 detail_p8_job_id 二选一 | 关键词（用于索引层子串搜索；如 `'可燃气体'` / `'HIGH'` / `p8_job_id`） |
| `detail_p8_job_id` | `Optional[str]` | 与 query 二选一 | 指定后走数据层精确查询 |

### 出参

#### 路径 A：精确查询（detail_p8_job_id 给定）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "recall_jobs (detail)",
  "data": {
    "p8_job_id": "P8J-...",
    "archived_job": { /* 完整 archived P8Job dict */ }
  }
}
```

#### 路径 B：索引层子串搜索（默认）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "recall_jobs (index)",
  "data": {
    "query": "可燃气体",
    "hits": [
      {"p8_job_id": "P8J-...", "description": "[HIGH] 可燃气体浓度超标 1.2 倍；approve by 李宗睿 @ 2026-08-13 18:00"}
    ],
    "count": 1,
    "next_step_hint": "若用户要看某条详情，请再用 detail_p8_job_id='<p8_job_id>' 再调一次"
  }
}
```

### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | query 与 detail_p8_job_id 都为空 | False |
| `LONG_TERM_NOT_FOUND` | detail_p8_job_id 在长期记忆里不存在 | False |

### 副作用

- ❌ 完全只读（访问 [`A7/storage/p8_long_term.py`](../../A7/storage/p8_long_term.py) 的索引层 + 数据层接口）
- ✅ 索引条目格式：`[<max_level>] <risk_basis 前 30 字>；<decision> by <decider> @ <archived_at 截 YYYY-MM-DD HH:MM>`

---

## 7. `create_disposition_agent`

### 用途

创建 P8 Agent **基础版**（无 HITL middleware）。仅 P8ArchiveMiddleware 在 after_model 监听终态。

### 签名

```python
def create_disposition_agent(
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增
) -> CompiledStateGraph:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_ctx` | `Optional[Dict[str, str]]` | ❌ | chat_reply_handler 构造的 dict，含 `role` / `name` / `open_id`（未识别时含 `note`）。注入到 system_prompt 末尾；同身份复用 Agent 实例（cache key = `json.dumps(user_ctx, sort_keys=True)`） |
| `job_id` | `Optional[str]` | ❌ | **2026-08-20 新增**。主流程作业 ID；非空时启用 per-job 持久化（middleware 触发 `data/jobs/{job_id}/P8/archived.json` 双写 + working_memory dump）。Bot 模式 + 无作业上下文传 `None`。cache key 拼 `job={job_id}` 防止 working_memory 跨 job 串台。 |

### 出参

`CompiledStateGraph` 实例（LangGraph 编译后的 Agent）。

### 关键配置

| 项 | 值 |
|---|---|
| `model` | `create_chat_model_with_logging("P8")` |
| `tools` | 6 个工具（update_job / hitl_decide / read_p7_events / notify_feishu / list_active_p8_jobs / recall_jobs） |
| `system_prompt` | `load_system_prompt("P8") + _format_user_context_block(user_ctx)` |
| `state_schema` | `P8State`（含 `working_memory` / `long_term_memory`） |
| `middleware` | `[P8ArchiveMiddleware()]` |
| `checkpointer` | `_p8_checkpointer`（`MemorySaver()` 单例） |

### 副作用

- ✅ `_AGENT_CACHE[cache_key] = agent`（同身份复用）
- ✅ `_p8_checkpointer` 是全局单例，跨进程内跨 invoke 共享

---

## 8. `create_disposition_agent_with_hitl`

### 用途

创建 P8 Agent **HITL 版**（生产用）。在 7 的基础上额外挂 `HumanInTheLoopMiddleware`，
对写入类工具调用前**中断等待人工确认**。

### 签名

```python
def create_disposition_agent_with_hitl(
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增
) -> CompiledStateGraph:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_ctx` | `Optional[Dict[str, str]]` | ❌ | 同 7。chat_reply_handler 构造的 dict；同身份复用 Agent 实例（cache key = `"hitl:<user_ctx_json>:job=<job_id>"`） |
| `job_id` | `Optional[str]` | ❌ | **2026-08-20 新增**。主流程作业 ID；非空时启用 per-job 持久化（middleware 触发 `data/jobs/{job_id}/P8/archived.json` 双写 + working_memory dump）。Bot 模式 + 无作业上下文传 `None`。cache key 拼 `job={job_id}` 防止 working_memory 跨 job 串台（与基础版相同语义）。 |

### HITL 中断矩阵

```python
HumanInTheLoopMiddleware(interrupt_on={
    "update_job":          True,   # 创建/更新 P8_job 必须确认
    "hitl_decide":         True,   # 进入 HITL 决策必须确认
    "notify_feishu":       True,   # 飞书推送必须确认
    "read_p7_events":      False,  # 只读放行
    "list_active_p8_jobs": False,  # 只读放行
    "recall_jobs":         False,  # 长期记忆只读放行
})
```

### 出参

`CompiledStateGraph` 实例。

### 与基础版的差异

| 项 | 基础版 | HITL 版 |
|---|---|---|
| `middleware` | `[P8ArchiveMiddleware(job_id=job_id)]` | `[HumanInTheLoopMiddleware(...), P8ArchiveMiddleware(job_id=job_id)]` |
| 写入类工具 | 直接执行 | 中断等待 `confirm_and_continue(...)` |
| cache key | `"basic:<user_ctx_json>:job=<job_id>"` | `"hitl:<user_ctx_json>:job=<job_id>"` |
| 适用场景 | 单元测试 / 离线仿真 | chat_reply 生产 / 前端交互 |

---

## 9. `run_disposition_agent`

### 用途

**基础版入口** —— 调一次 Agent，提取最后一条 AI 消息作为返回字符串。无 HITL 中断。

### 签名

```python
def run_disposition_agent(
    message: str,
    *,
    thread_id: str = "default",
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增
) -> str:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | `str` | ✅ | 用户消息文本 |
| `thread_id` | `str` | ❌ 默认 `"default"` | LangGraph thread_id；**主流程 `f"p8-{job_id}"`**；Bot 模式 `chat_id`（群）或 `open_id`（单聊）；不同 thread_id 在 MemorySaver 下完全隔离 working_memory / messages |
| `user_ctx` | `Optional[Dict[str, str]]` | ❌ | 同 create_disposition_agent；**不**影响 thread_id |

### 出参

`str` —— 最后一条 AI 消息内容（经 `extract_output(result)`）。

### 调用链

```
run_disposition_agent(message, thread_id=..., user_ctx=..., job_id=...)
  → agent = create_disposition_agent(user_ctx=user_ctx, job_id=job_id)
  → agent_config = get_agent_config(thread_id=thread_id, agent_name="P8", llm_params=...)
  → result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
  → if job_id: flush_working_memory(job_id)   # 2026-08-20 新增：invoke end dump
  → extract_output(result)
```

---

## 10. `disposition_demo`

### 用途

**Gradio ChatInterface / chat_reply 兼容入口** —— **位置参数签名 `(message, history=None)` 不可破坏**。

### 签名

```python
def disposition_demo(
    message: str,
    history: list = None,
    *,
    user_ctx: Optional[Dict[str, str]] = None,
    thread_id: Optional[str] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增：主流程作业 ID
) -> str:
```

### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | `str` | ✅ | 用户消息文本（位置参数 1） |
| `history` | `list` | ❌ 默认 `None` | Gradio 兼容参数（**不使用**；P8 状态由 MemorySaver 通过 thread_id 维护） |
| `user_ctx` | `Optional[Dict[str, str]]` | ❌ | chat_reply_handler 注入身份（keyword-only） |
| `thread_id` | `Optional[str]` | ❌ | LangGraph thread_id（keyword-only；`None` 回退 `"default"`） |
| `job_id` | `Optional[str]` | ❌ | **2026-08-20 新增**。主流程作业 ID（keyword-only）。chat_reply_handler 从消息正文 `[job_id=...]` 前缀解析；无前缀 → `None`（Bot 临时会话，不写 per-job 文件）。 |

### 出参

`str` —— 最后一条 AI 消息内容。

### 实现

```python
return run_disposition_agent(
    message,
    user_ctx=user_ctx,
    thread_id=thread_id or "default",   # ← None 回退 "default"（向后兼容）
    job_id=job_id,                      # 2026-08-20 透传
)
```

### 调用方

| 调用方 | 位置 | thread_id 来源 |
|------|------|---------------|
| [chat_reply.py L702](../../A7/adapters/chat_reply.py) | 飞书 daemon 调 | `_compute_thread_id(event, chat_id, open_id)`：群=chat_id / 单聊=open_id |
| [`web/server.py`](../../web/server.py) Gradio 路由 | Web UI 调 | `default`（单用户测试） |
| 单元测试 | 测试 | 显式传 |

### 向后兼容约束

`chat_reply.py L702` 硬依赖位置参数 `(message, history)`，**不可改成 keyword-only**。
2026-08-19 新增 `user_ctx` / `thread_id` 均以 keyword-only 形式加入，不破坏现有调用方。

---

## 附录 A：标准响应 schema

所有工具返回的 JSON 都遵循 [`agents/utils/response_utils.py`](../../agents/utils/response_utils.py)：

```python
SCHEMA_VERSION = "1.0"

def make_response(tool_name: str, data: dict) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": "ok", "tool": tool_name, "data": data}

def make_error(code: str, message: str, recoverable: bool = False) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": "error",
            "error": {"code": code, "message": message, "recoverable": recoverable}}
```

成功响应：
```json
{"schema_version": "1.0", "status": "ok", "tool": "<tool_name>", "data": {...}}
```

错误响应：
```json
{"schema_version": "1.0", "status": "error", "error": {"code": "...", "message": "...", "recoverable": false}}
```

---

## 附录 B：交叉引用

- 蓝图总览：[`docs/P8_人机协同处置_需求与Demo设计.md`](../../docs/P8_人机协同处置_需求与Demo设计.md)
- 系统提示词：[`agents/system_prompt/P8_DISPOSITION_SYSTEM_PROMPT.md`](../../agents/system_prompt/P8_DISPOSITION_SYSTEM_PROMPT.md)
- 状态 schema：[`A7/schema/p8_state.py`](../../A7/schema/p8_state.py)
- 长期记忆后端：[`A7/storage/p8_long_term.py`](../../A7/storage/p8_long_term.py)
- 归档 middleware：[`A7/middleware/p8_archive_middleware.py`](../../A7/middleware/p8_archive_middleware.py)
- REST 端点：[`A7/api/p8_working_memory_ctrl.py`](../../A7/api/p8_working_memory_ctrl.py)
- chat_reply 适配层：[`A7/adapters/chat_reply.py`](../../A7/adapters/chat_reply.py)
- 飞书卡片发送器：[`openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py`](../../openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py)