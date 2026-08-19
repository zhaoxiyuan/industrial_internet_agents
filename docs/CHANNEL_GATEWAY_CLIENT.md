# `agents/channel_gateway_client.py` — 接口说明

> **一句话**：本模块是 **OpenClaw Channel Gateway Standalone**（默认 `http://127.0.0.1:8787`）的 Python 同步 REST 客户端，负责把 Gateway 的 HTTP 接口包成易用的 Python 函数 + 数据类。
>
> **它是什么 / 不是什么**：
> - ✅ 主动向飞书发消息、回复入站消息、轮询入站事件、ACK、查询出站历史
> - ✅ 解析 Gateway 返回的 JSON，提供 `SendMessageResult` / `PollResult` 数据类
> - ✅ 把网络错误 / 业务错误拆成不同异常，方便上层捕获重试
> - ❌ **不**直接调飞书 Open API —— 都走 Gateway，由 Gateway 鉴权 + 持久化 + 重试
> - ❌ **不**是异步 / 流式客户端 —— 用的是 `requests` 同步调用，需要流式请用 `iter_inbound_events` 生成器

---

## 1. 怎么用：3 步上手

### 第 1 步：配 `.env`（项目根目录）

```env
GATEWAY_HOST=http://127.0.0.1:8787
CG_API_KEY=gateway-api-key-16chars!
```

`CG_API_KEY` 没配也能 import，但调用受保护接口（`/v1/*`）会返回 401。

### 第 2 步：启动 Gateway

参考 `tests/test_channel_gateway.md` §1。也可以用 `feishu_gateway_cli.start_gateway`：

```bash
python -m feishu_gateway_cli.start_gateway start
```

### 第 3 步：调用接口

```python
from agents.channel_gateway_client import send_message, poll_inbound_events

# 发消息
res = send_message(
    text="你好",
    conversation_id="oc_xxx",
    receive_id_type="chat_id",
)
print(res.status)  # 'sent' | 'sending' | 'failed' | 'unknown'

# 拉事件
poll = poll_inbound_events(after_sequence=0, limit=10)
print(poll.events, poll.latest_sequence)
```

---

## 2. 接口速览（13 个公开 API）

按使用频率分组。所有接口在模块顶层**都有同名便捷函数**（共享一个进程级默认客户端）；如果需要多实例 / 自定义 host，可直接 `ChannelGatewayClient(config=...)`。

### 🔌 健康检查（无副作用，优先验证）

| 接口 | HTTP | 鉴权 | 用途 |
|------|------|------|------|
| `client.health()` | `GET /healthz` | ❌ 不要 | 进程是否存活 |
| `client.ready()` | `GET /readyz` | ✅ 要 | 状态存储是否就绪 |

### 📤 主动发消息（核心）

| 接口 | HTTP | 用途 |
|------|------|------|
| `send_message(text, ...)` / `client.send_message(...)` | `POST /v1/messages/send` | 主动推一条消息给指定接收方 |
| `reply_to_event(event_id, text, ...)` / `client.reply_to_event(...)` | `POST /v1/messages/reply` | 在已有入站事件上回复（自动继承上下文） |

### 📥 入站事件轮询

| 接口 | HTTP | 用途 |
|------|------|------|
| `poll_inbound_events(after_sequence=0, ...)` | `GET /v1/events` | 单次拉取 |
| `iter_inbound_events(initial_sequence=0, ...)` | `GET /v1/events` | 生成器版持续轮询 |
| `ack_event(event_id, status="acked", ...)` | `POST /v1/events/{id}/ack` | 标记已处理，避免重复投递 |
| `client.retry_event(event_id=...)` | `POST /v1/events/{id}/retry` | 把已 ack 的事件重新置回 pending |

### 📊 出站查询（用于排查 / 监控）

| 接口 | HTTP | 用途 |
|------|------|------|
| `client.list_outbound_intents(status=..., limit=...)` | `GET /v1/outbound/intents` | 列出已发送的"出站意图" |
| `client.list_receipts(limit=...)` | `GET /v1/receipts` | 列出飞书回执 |
| `client.list_dead_letters()` | `GET /v1/dead-letters` | 列出投递失败的死信 |

### 🛠 客户端管理

| 接口 | 用途 |
|------|------|
| `get_default_client()` | 获取（懒加载）默认客户端；进程共享 |
| `configure_default_client(config)` | 替换默认客户端（测试 / 非默认网关） |

---

## 3. 详细接口（每个都讲清楚）

### 3.1 `GatewayConfig` — 配置数据类

不是接口，是个配置容器。`.env` 里的变量通过 `GatewayConfig.from_env()` 自动读。

| 字段 | 默认 | 来源 env |
|------|------|---------|
| `host` | `http://127.0.0.1:8787` | `GATEWAY_HOST` 或 `CHANNEL_GATEWAY_HOST` |
| `api_key` | `""` | `CG_API_KEY` 或 `CHANNEL_GATEWAY_API_KEY` |
| `default_channel` | `"feishu"` | `CG_DEFAULT_CHANNEL` |
| `default_account_id` | `"default"` | `CG_DEFAULT_ACCOUNT_ID` |
| `timeout` | `15.0` | `CG_TIMEOUT`（秒） |

**注意**：`host` 自动 `rstrip("/")`，避免拼成 `//v1/messages/send`。

### 3.2 `health()` — 进程存活检查

**做什么**：问 Gateway "你活着吗"。
**HTTP**：`GET /healthz`（**唯一**不需要鉴权的端点）。
**何时用**：启动后第一秒验证 / 健康探活 / 容器 readiness probe。
**返回**：dict，例如 `{"status": "ok"}` 或 `{"status": "healthy"}`。
**失败抛**：`requests.RequestException`（网络问题，常见 `Connection refused`）。

```python
from agents.channel_gateway_client import get_default_client
print(get_default_client().health())
```

### 3.3 `ready()` — 状态存储就绪

**做什么**：比 health 更严格——进程活着 + 状态存储（事件落盘的 JSON 文件）能正常读写。
**HTTP**：`GET /readyz`，**需要 Bearer 鉴权**。
**何时用**：发消息前确认 Gateway 真的"准备好了"。
**返回**：dict，例如 `{"ready": true}` 或 `{"ready": false, "reason": "..."}`。
**失败抛**：`GatewayError`（401 = key 错）。

```python
from agents.channel_gateway_client import get_default_client
print(get_default_client().ready())
```

### 3.4 `send_message(...)` — 主动发消息 ⭐

**做什么**：把一条文本消息交给 Gateway，Gateway 异步调飞书 API 投递。
**HTTP**：`POST /v1/messages/send`。
**何时用**：Agent 主动推送告警 / 通知 / 单聊（例：P8 通知 → `oc_xxx` 群 / `ou_xxx` 用户）。

#### 参数表

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `text` | △ | `""` | 消息正文（**至少 `text` 或 `content` 传一个**） |
| `to` | △ | `{}` | 完整 `{"conversation_id": "...", "receive_id_type": "..."}`，显式传入时优先级最高 |
| `receive_id` | △ | — | 飞书接收方 ID（`oc_xxx` 或 `ou_xxx`） |
| `receive_id_type` | △ | `"chat_id"` | `"chat_id"` 群聊 / `"open_id"` 单聊 / `"email"` |
| `conversation_id` | △ | — | 与 `receive_id` 同义，兼容旧调用 |
| `channel` | — | `"feishu"` | 通道名（多通道时切换） |
| `account_id` | — | `"default"` | 账号 ID（多账号机器人场景） |
| `msg_type` | — | `"text"` | 消息类型：`"text"`（纯文本） / `"interactive"`（飞书交互式 Card，2026-08-17 新增） |
| `content` | △ | `None` | 卡片 JSON 字符串（`msg_type="interactive"` 时必填；建议 `json.dumps(card, ensure_ascii=False)`） |
| `metadata` | — | `None` | 透传字段，飞书 API 不识别的会原样回 |
| `idempotency_key` | — | 自动生成 | 幂等键；**业务唯一**，建议传 `f"p8-{job_id}"` 形式 |

> △ = 与 `to` 二选一；优先用 `to` 写法，更明确。

#### 返回：`SendMessageResult`

| 字段 | 类型 | 说明 |
|------|------|------|
| `intent_id` | str? | 出站意图 ID（Gateway 视角的"消息 id"） |
| `status` | str | `"sent"`（已投递） / `"sending"`（投递中） / `"failed"`（失败） / `"unknown"`（解析不出来） |
| `idempotency_key` | str? | 实际使用的幂等键 |
| `receipt_id` | str? | 回执 ID |
| `platform_message_id` | str? | 飞书侧消息 ID（`om_xxx`），用于追踪 |
| `evidence` | str? | 投递证据（异常时含 stack） |
| `replayed` | bool | `True` 表示本次响应是幂等键命中（之前已发过） |
| `raw` | dict | 完整响应，便于调试 |

#### 三个常见用法

```python
from agents.channel_gateway_client import send_message

# (1) 群聊
r = send_message(
    text="【P8 通知】可燃气体浓度异常，请处置",
    conversation_id="oc_你的群ID",
    receive_id_type="chat_id",
    idempotency_key="p8-job-20260813-001",
)
print(r.intent_id, r.status, r.platform_message_id)

# (2) 单聊
r = send_message(
    text="hi",
    receive_id="ou_你的open_id",
    receive_id_type="open_id",
)

# (3) 幂等验证（同 key 第二次调用应 replayed=True）
r1 = send_message(text="x", conversation_id="oc_x", idempotency_key="K")
r2 = send_message(text="x", conversation_id="oc_x", idempotency_key="K")
assert r1.intent_id == r2.intent_id and r2.replayed is True

# (4) 飞书交互式 Card（msg_type="interactive"，2026-08-17 新增）
import json
card = {
    "config": {"wide_screen_mode": True},
    "header": {"template": "red", "title": {"tag": "plain_text", "content": "气体浓度告警"}},
    "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": "【告警】可燃气体浓度异常"}},
        {"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": "立即处理"},
             "type": "primary", "value": {"action": "handle", "alert_id": "gas_001"}},
        ]},
    ],
}
r = send_message(
    conversation_id="oc_你的群ID",
    receive_id_type="chat_id",
    msg_type="interactive",
    content=json.dumps(card, ensure_ascii=False),
    idempotency_key="p8-gas-001",
)
```

#### 失败语义

| 异常 | 触发条件 | 处理建议 |
|------|---------|---------|
| `ValueError("text 不能为空")` | text 为空 / 空白 | 自己保证 |
| `requests.RequestException` | 网络错误（连不上 / 超时） | 重试 |
| `GatewayError(status=400)` | 参数错（飞书 API 拒绝） | 检查 chat_id / Bot 权限 |
| `GatewayError(status=401)` | `CG_API_KEY` 错 | 改 env |
| `GatewayError(status=403)` | Bot 不在群 / 无发送权限 | 把 Bot 加进群 |
| `GatewayError(retryable=True)` | 网关标记可重试 | sleep + 重试 |

---

### 3.5 `reply_to_event(...)` — 回复入站事件

**做什么**：在已有入站事件（用户给 Bot 发消息）上"回复"。
**HTTP**：`POST /v1/messages/reply`。
**何时用**：Bot 收到用户消息后，自动回复。
**与 `send_message` 的区别**：不用指定 channel/accountId/conversationId —— Gateway **自动继承**原事件的上下文（除非显式覆盖）。

#### 参数表

| 参数 | 必填 | 说明 |
|------|------|------|
| `event_id` | ✅ | 入站事件 ID（`evt_xxx`，从 `poll_inbound_events` 拿到） |
| `text` | ✅ | 回复正文（同时会作为卡片 fallback 文本） |
| `msg_type` | — | 消息类型：`"text"`（默认，纯文本）/ `"interactive"`（飞书交互式 Card）/ `"post"`（富文本）。`text` 之外需配合 `content`；仅当网关后端支持时生效（**2026-08-19 新增**，网关 `replyToEvent` 现在透传 `msgType`+`content`） |
| `content` | △ | 自定义 content 字符串（如飞书卡片的 JSON 字符串）。`msg_type="interactive"` 时必传（建议 `json.dumps(card, ensure_ascii=False)`） |
| `channel` / `account_id` / `conversation_id` / `receive_id_type` | — | 显式覆盖默认继承 |
| `reply_to_id` | — | 覆盖默认 `message.id`（作为"被回复的消息"） |
| `thread_id` | — | 覆盖默认会话 thread |
| `metadata` / `idempotency_key` | — | 同 `send_message` |

#### 返回：同样是 `SendMessageResult`

```python
from agents.channel_gateway_client import poll_inbound_events, reply_to_event, ack_event
import json

poll = poll_inbound_events(after_sequence=0, limit=1)
if poll.events:
    ev = poll.events[0]

    # 方式 1：纯文本回复（旧调用方式仍兼容）
    reply_to_event(
        event_id=ev["id"],
        text=f"收到：{ev.get('message', {}).get('text', '')}",
    )

    # 方式 2：飞书 interactive 卡片（2026-08-19，markdown 可正常渲染）
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "**📋 风险概览**\n- 已处理"}}
        ],
    }
    reply_to_event(
        event_id=ev["id"],
        text="**📋 风险概览**\n- 已处理",  # 同时作为 fallback 文本
        msg_type="interactive",
        content=json.dumps(card, ensure_ascii=False),
    )

    ack_event(event_id=ev["id"], status="acked", details={"consumer": "p8"})
```

---

### 3.6 `poll_inbound_events(...)` — 单次轮询

**做什么**：从 Gateway 拉"从某个 sequence 之后"的所有入站事件。
**HTTP**：`GET /v1/events?after_sequence=N&limit=K`。
**何时用**：批处理 / 测试 / 不想自己写循环。
**天然断点续传**：传入上次的 `latest_sequence`，下次接着拉。

#### 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `after_sequence` | `0` | 只返回 `sequence > after_sequence` 的事件；首次传 0 |
| `limit` | `100` | 1–1000；超过被 Gateway 截断 |
| `status` | `None` | 按 `delivery.status` 过滤：`pending` / `processing` / `acked` / `dead_letter` |
| `channel` | `None` | 按通道过滤 |
| `session_key` | `None` | 按会话 key 过滤（`cg:v1:<channel>:<account>:...`） |

#### 返回：`PollResult`

| 字段 | 类型 | 说明 |
|------|------|------|
| `events` | `List[dict]` | 事件列表 |
| `latest_sequence` | int | Gateway 视角的"最大 sequence"，下次用这个接着拉 |
| `raw` | dict | 完整响应 |

#### 事件结构（每条 dict）

```json
{
  "id": "evt_xxx",
  "sequence": 7,
  "channel": "feishu",
  "accountId": "default",
  "kind": "message",
  "sender": {"id": "ou_xxx", "type": "user"},
  "conversation": {"id": "oc_xxx", "type": "group"},
  "message": {"id": "om_xxx", "type": "text", "text": "你好"},
  "occurredAt": "2026-08-13T10:08:08.448Z",
  "receivedAt": "2026-08-13T10:08:08.978Z",
  "delivery": {"status": "pending", "attempts": 0, ...},
  "raw": {...飞书原始 webhook...}
}
```

```python
from agents.channel_gateway_client import poll_inbound_events
r = poll_inbound_events(after_sequence=0, limit=10)
for ev in r.events:
    text = ev.get("message", {}).get("text", "")
    sender = ev.get("sender", {}).get("id", "?")
    print(f"seq={ev['sequence']} from={sender} text={text[:50]}")
print(f"最新 seq={r.latest_sequence}（下次传这个）")
```

---

### 3.7 `iter_inbound_events(...)` — 持续轮询（生成器）

**做什么**：在 `poll_inbound_events` 基础上持续拉，**事件来了立即 yield**。
**何时用**：长跑 worker（飞书 Bot 守护进程）。
**用法**：直接 `for event in iter_inbound_events(...)` 即可。

#### 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `initial_sequence` | `0` | 起始 sequence |
| `poll_interval` | `1.0` | 空轮询（无事件）时 sleep 秒数 |
| `idle_sleep` | `0.5` | 拉到事件后的退避秒数（避免 busy loop） |
| `stop_on_empty` | `False` | True → 一次空轮询就退出（批处理用） |
| `max_iterations` | `None` | 最大迭代次数（防无限循环） |
| `channel` / `session_key` | `None` | 透传给 `poll_inbound_events` |
| `on_event` | `None` | 每条事件回调（先于 `yield` 调用，可用于 ACK / 记录） |
| `on_error` | `None` | 错误回调（默认只记日志） |

#### 用法示例

```python
from agents.channel_gateway_client import iter_inbound_events

# 长跑 worker（生产）
for ev in iter_inbound_events(initial_sequence=0, poll_interval=2.0):
    print(ev.get("message", {}).get("text"))
    # 不在这里 ACK；由 on_event 回调处理

# 单次消费（测试 / 批处理）
for ev in iter_inbound_events(stop_on_empty=True, max_iterations=3):
    print(ev["id"])

# 带回调（自动 ACK）
def handle(ev):
    print("ack", ev["id"])
    from agents.channel_gateway_client import ack_event
    ack_event(event_id=ev["id"])

for ev in iter_inbound_events(on_event=handle):
    pass
```

---

### 3.8 `ack_event(...)` — 标记事件已处理

**做什么**：把事件从"待投递"队列移除（或打 `ignored` 标签），**避免重复处理**。
**HTTP**：`POST /v1/events/{id}/ack`。
**何时用**：成功处理完一条入站事件后；或明确想丢弃某条事件。

#### 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `event_id` | ✅ | 事件 ID |
| `status` | `"acked"` | `"acked"`（处理完）或 `"ignored"`（明确丢弃） |
| `details` | `None` | 审计信息 dict（例：`{"consumer": "p8-agent", "run_id": "..."}`） |

#### 返回：dict（Gateway 原始响应）

```python
from agents.channel_gateway_client import ack_event
print(ack_event(event_id="evt_xxx", status="acked", details={"consumer": "p8"}))
```

---

### 3.9 `client.retry_event(event_id=...)` — 重投事件

**做什么**：把"已 ack 的事件"重新置回 `pending`，立即允许处理。
**HTTP**：`POST /v1/events/{id}/retry`。
**何时用**：测试失败重试 / 调试。
**注意**：幂等——同 id 重复调用也只产生 1 个新投递机会。

```python
from agents.channel_gateway_client import poll_inbound_events, retry_event
r = poll_inbound_events(after_sequence=0, limit=1, status="acked")
if r.events:
    retry_event(event_id=r.events[0]["id"])
```

---

### 3.10 `list_outbound_intents(...)` — 出站意图列表

**做什么**：列出 Gateway 已接收的"主动发消息"请求（带状态）。
**HTTP**：`GET /v1/outbound/intents?status=sent&limit=10`。
**何时用**：排查"我发了没？飞书收到了没？"。
**注意**：status 不传则返回所有状态（`sent` / `sending` / `failed`）。

```python
from agents.channel_gateway_client import get_default_client
print(get_default_client().list_outbound_intents(status="failed", limit=10))
```

### 3.11 `list_receipts(limit=...)` — 出站回执

**做什么**：列出飞书实际收到的消息回执（带 `platform_message_id`）。
**何时用**：和 `list_outbound_intents` 对账——"意图发了，回执有没有"。

```python
from agents.channel_gateway_client import get_default_client
print(get_default_client().list_receipts(limit=10))
```

### 3.12 `list_dead_letters()` — 死信列表

**做什么**：列出投递彻底失败的事件（重试超过阈值后被踢到这里）。
**何时用**：监控告警 / 排查长期失败原因。

```python
from agents.channel_gateway_client import get_default_client
print(get_default_client().list_dead_letters())
```

---

## 4. 数据类（不用 new，直接读字段）

| 类 | 字段 |
|----|------|
| `GatewayConfig` | `host` / `api_key` / `default_channel` / `default_account_id` / `timeout` |
| `SendMessageResult` | `intent_id` / `status` / `idempotency_key` / `receipt_id` / `platform_message_id` / `evidence` / `replayed` / `raw` |
| `PollResult` | `events` / `latest_sequence` / `raw` |
| `GatewayError` | `code` / `message` / `status_code` / `request_id` / `retryable` / `ambiguous` / `details` |

`SendMessageResult` 和 `PollResult` 是 `@dataclass`，字段直接 `.attr` 访问；`raw` 是 Gateway 原始 dict，留给调试。

---

## 5. 异常

### `GatewayError`（业务错误，HTTP 4xx/5xx）

```python
from agents.channel_gateway_client import send_message, GatewayError
try:
    send_message(text="x", conversation_id="oc_x", receive_id_type="chat_id")
except GatewayError as e:
    print(f"code={e.code} status={e.status_code} retryable={e.retryable} ambiguous={e.ambiguous}")
    print(f"request_id={e.request_id} details={e.details}")
```

| 字段 | 含义 |
|------|------|
| `code` | 网关业务码（`UNAUTHORIZED` / `INVALID_TARGET` / ...） |
| `status_code` | HTTP 状态码 |
| `retryable` | True = 网关建议你重试 |
| `ambiguous` | True = 状态不确定（可能发也可能没发） |
| `request_id` | 关联 `X-Request-Id`，找日志用 |
| `details` | 额外上下文 |

### `requests.RequestException`（网络错误）

`Connection refused` / `Timeout` / `SSLError` 等。直接捕获重试。

### `ValueError`（参数错）

`text 不能为空` / `event_id 不能为空` / `status 仅支持 'acked' 或 'ignored'`。

---

## 6. 客户端管理

### `get_default_client()`

**懒加载**进程级共享客户端。第一次调用时用 `GatewayConfig.from_env()` 构造。
**何时直接调用**：要访问 `client.config` 或 `client.close()` 时。
**线程安全**：内部用 `threading.Lock` 保护懒加载；`requests.Session` 本身线程安全。

### `configure_default_client(config)`

**替换**默认客户端。已存在的旧客户端会被 `close()`。
**何时用**：测试（指向 mock 网关）/ 多 Gateway 切换。

```python
from agents.channel_gateway_client import (
    configure_default_client, GatewayConfig, get_default_client,
)

# 切到测试网关
configure_default_client(GatewayConfig(host="http://127.0.0.1:9999", api_key="test"))
# ... 用默认客户端发请求 ...
# 切回生产
configure_default_client(GatewayConfig.from_env())
```

### `client.close()`

关闭底层 `requests.Session`。进程退出时会自动调用。

---

## 7. 完整最小示例（P8 端到端）

```python
"""P8 → Gateway → 飞书：最小可跑示例"""
from agents.channel_gateway_client import (
    send_message, poll_inbound_events, reply_to_event, ack_event,
    iter_inbound_events,
)

# 1. 主动推送（一次性）
res = send_message(
    text="【P8 通知】可燃气体浓度异常，请 1h 内回复",
    conversation_id="oc_你的群ID",
    receive_id_type="chat_id",
    idempotency_key="p8-job-20260813-001",
)
print(f"send → status={res.status} intent={res.intent_id}")

# 2. 拉一次入站事件（看飞书那边有没有人回复）
poll = poll_inbound_events(after_sequence=0, limit=10)
for ev in poll.events:
    text = ev.get("message", {}).get("text", "")
    sender = ev.get("sender", {}).get("id")
    print(f"incoming seq={ev['sequence']} from={sender} text={text[:60]}")

    # 3. 回一条
    reply_to_event(event_id=ev["id"], text=f"已收到您的反馈：{text[:30]}")
    # 4. ACK（防重复）
    ack_event(event_id=ev["id"], details={"consumer": "p8"})

# 5. 或者长跑（生产）
# for ev in iter_inbound_events(poll_interval=2.0):
#     process(ev)
#     ack_event(event_id=ev["id"])
```

---

## 8. 故障排查速查

| 现象 | 大概率原因 | 排查命令 |
|------|---------|---------|
| `Connection refused` | Gateway 没启动 | `netstat -ano \| findstr ":8787"` |
| `401 Unauthorized` | `CG_API_KEY` 与 Gateway `--api-key` 不一致 | `cat .env` vs `cat openclaw-channel-gateway-standalone/.env` |
| `400 code=INVALID_TARGET` | chat_id / open_id 失效 | 重新建群 / 重新拉 Bot |
| `403 code=NOT_IN_CHAT` | Bot 不在该群 | 把 Bot 加进群 |
| `ambiguous=True` | 网关返回矛盾状态（发送中网络抖） | 几秒后重试 / 查 `list_outbound_intents` |
| `status='failed'` in `SendMessageResult` | `result.evidence` 有详细原因 | `print(result.raw)` |
| `events=[]` 永远为空 | Bot 没收到消息 / webhook 没配 | 飞书开发者后台 → 事件订阅 URL |
| `feishu_dev_xxx` 占位符 | (与本模块无关；是 USER_MAP 没配的兜底) | 检查 `.env` 的 `FEISHU_USER_MAP` |

---

## 9. 与项目其他模块的关系

```
P8 Agent 业务层
    ↓ 用 resolve_recipients() 拿收件人列表
feishu_gateway_cli.feishu_sender  (P8 飞书适配层，多收件人聚合)
    ↓ 对每个收件人调 send_message() 一次
agents.channel_gateway_client  ← 本文档讲的模块
    ↓ HTTP POST /v1/messages/send
OpenClaw Channel Gateway Standalone (Node.js :8787)
    ↓ 调飞书 Open API
Feishu Open Platform
```

**`channel_gateway_client` 是叶子节点**：它不知道 P1-P10 任何业务概念，只关心 HTTP 和 JSON。所有 P 编号 Agent 都通过它（直接 / 间接）跟飞书通信。

### 9.5 `feishu_gateway_cli` 适配层：`send_to_group` 新签名 + `FEISHU_GROUP_MAP`（2026-08-17）

> 本节属于上层适配层 `openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py`（不是 `channel_gateway_client` 本身）。
> 该层包了一层"按名称反查 ID"的便利函数，让调用方不用记 `oc_xxx` 这种 ID。

**`send_to_group` 新签名**（互斥参数）：

```python
from feishu_gateway_cli import send_to_group

# 路径 1：按 chat_id 直传（旧路径）
result = send_to_group(text="【告警】...", chat_id="oc_xxx")

# 路径 2：按群聊名称反查 FEISHU_GROUP_MAP 取 chat_id（新路径）
result = send_to_group(text="【告警】...", group_name="应急响应群")
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | `str` | ✅ | 消息正文（调用方自行拼装） |
| `chat_id` | `Optional[str]` | 互斥 | 飞书群 ID（`oc_xxx`），与 `group_name` 二选一 |
| `group_name` | `Optional[str]` | 互斥 | 群聊名称，按 `FEISHU_GROUP_MAP` 反查 `chat_id` |

**CLI（互斥必填其一）**：

```bash
# 旧路径
python -m feishu_gateway_cli.feishu_sender send_group --text "..." --chat-id oc_xxx

# 新路径
python -m feishu_gateway_cli.feishu_sender send_group --text "..." --group-name "应急响应群"
```

**`FEISHU_GROUP_MAP` 数据模型**（`.env` JSON 字符串；主键 `chat_id`）：

```json
{
  "oc_xxx1": {"name": "应急响应群", "description": "P8 告警群"},
  "oc_xxx2": {"name": "日常巡检群", "description": ""}
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| 主键（dict key）| ✅ | `chat_id`（飞书群 ID） |
| `name` | ✅ | 群聊名称；`send_to_group` 按 `name` 反查时使用；为空视为占位条目（前端显示"未命名"） |
| `description` | ❌ | 群聊描述（可空），仅作元数据 |

**为什么需要独立的 `GROUP_MAP`？**

- 同一 `chat_id` 可被 `FEISHU_USER_MAP` 多个 user 共享；群名/描述只需配一次，避免 USER_MAP 行间数据冗余 / 不同步
- `send_to_group` 按名称发送，方便运维不用记 ID
- `feishu_config_app` 的两个 section（③ 群聊 / ④ 收件人）各自独立编辑

**与 USER_MAP 的关系**：

| 维度 | `FEISHU_USER_MAP` | `FEISHU_GROUP_MAP` |
|------|-------------------|---------------------|
| 索引维度 | 人（`open_id`）| 群（`chat_id`） |
| 主用途 | `send_to_user` 按 `name` 反查 `open_id` | `send_to_group` 按 `name` 反查 `chat_id` |
| 解耦性 | ✅ 两者不互相依赖（2026-08-17 清理：USER_MAP 移除 chat_id 字段） | ✅ 两者不互相依赖 |

**实时捕获 chat_id/open_id（2026-08-17 重构）**：选中事件后，前端按 `conversation_type` 自动把主键填到对应 section 的"+ 添加"输入框：

- 群聊 → 「③ 飞书群聊」section 的 `#new-gm-chat-id` 主键输入框（用户去填 name + 点"+ 添加"）
- 单聊 → 「④ 飞书收件人」section 的 `#new-um-open-id` 主键输入框（用户去填 role + name + 点"+ 添加"）

不再有独立的 save-captured-form；写入由 3/4 的"添加"按钮 + 页面底部「💾 保存到 .env」统一落盘完成。

**即时删除（2026-08-17 新增）**：`POST /api/feishu/config/delete` 端点按主键删除 USER_MAP 或 GROUP_MAP 单条配置，立即调 `upsert_env_entries` 写盘 .env（写前备份 .env.bak）：

| Body 字段 | 必填 | 取值 | 说明 |
|----------|------|------|------|
| `type`   | ✅ | `"user"` \| `"group"` | 选 `user` 删 `FEISHU_USER_MAP` 主键；选 `group` 删 `FEISHU_GROUP_MAP` 主键 |
| `key`    | ✅ | `ou_xxx` \| `oc_xxx` | 要删除的主键 |

| 返回字段 | 说明 |
|---------|------|
| `deleted: bool` | true = 实际删了；false = 主键不存在（幂等跳过） |
| `remaining_size: int` | 删后剩余条目数 |
| `updated: [str]` | 实际写入的 env key（`["FEISHU_USER_MAP"]` 或 `["FEISHU_GROUP_MAP"]`） |
| `backup: str` | 备份文件路径（`.env.bak`） |

**不级联**：删 GROUP_MAP 一行时，关联的 USER_MAP 条目**不被删除**。前端点删除时弹 `confirm()` 二次确认。

**前端（feishu_config_app UI）**：

- 「③ 飞书群聊（FEISHU_GROUP_MAP）」section：独立编辑区（增/删/改；删除调 `/api/feishu/config/delete`）
- 「④ 飞书收件人（FEISHU_USER_MAP）」表格：3 字段（open_id / role / name），无 chat_id 列（2026-08-17 清理）

---

## 10. 测试

参考 `tests/test_channel_gateway.md`（逐接口命令行验证），与 `agents/_test_notify_e2e.py`（P8 端到端）互补。

---

## 11. 关联链接

- [openclaw-channel-gateway-standalone/README.md](../openclaw-channel-gateway-standalone/README.md)
- [openclaw-channel-gateway-standalone/openapi/openapi.yaml](../openclaw-channel-gateway-standalone/openapi/openapi.yaml)
- [docs/P8_人机协同处置_文件组织与职责.md §7.1 适配器](./P8_人机协同处置_文件组织与职责.md)
- [tests/test_channel_gateway.md §15 一键回归](../tests/test_channel_gateway.md)
- [openclaw-channel-gateway-standalone/feishu_gateway_cli/start_gateway.py](../openclaw-channel-gateway-standalone/feishu_gateway_cli/start_gateway.py)（Gateway 进程管理）