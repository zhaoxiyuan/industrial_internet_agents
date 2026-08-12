# Channel Gateway API 使用说明

## 1. 基本约定

默认基地址：

```text
http://127.0.0.1:8787
```

除 `/healthz`、`/readyz` 和 `/webhooks/*` 外，所有 `/v1/*` 接口都要求：

```http
Authorization: Bearer <server.apiKey>
```

请求和响应均使用 UTF-8 JSON。服务返回：

```http
Content-Type: application/json; charset=utf-8
X-Request-Id: <request-id>
```

调用方可传入 `X-Request-Id`；未传入时由服务生成 UUID。

## 2. 错误格式

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "eventId is required",
    "request_id": "...",
    "retryable": false,
    "ambiguous": false,
    "details": {}
  }
}
```

字段含义：

- `retryable`：错误是否可能通过后续重试恢复；
- `ambiguous`：平台是否可能已经执行操作，但网关没有得到确定结果；
- `details`：附加信息，例如上游状态码、发送意图 ID 或恢复建议。

## 3. 统一入站事件模型

```json
{
  "id": "evt_...",
  "sequence": 1,
  "platformEventId": "platform-event-001",
  "channel": "feishu",
  "accountId": "default",
  "kind": "message",
  "occurredAt": "2026-08-10T18:00:00.000Z",
  "receivedAt": "2026-08-10T18:00:00.120Z",
  "sender": {
    "id": "ou_xxx",
    "name": "Alice",
    "type": "user"
  },
  "conversation": {
    "id": "oc_xxx",
    "type": "group",
    "threadId": "om_root_xxx",
    "parentId": "om_parent_xxx"
  },
  "message": {
    "id": "om_xxx",
    "type": "text",
    "text": "你好",
    "replyToId": null,
    "mentions": [],
    "media": []
  },
  "session": {
    "key": "cg:v1:feishu:default:..."
  },
  "metadata": {},
  "raw": {},
  "delivery": {
    "status": "pending",
    "attempts": 0,
    "nextAttemptAt": 1786384800120,
    "claimedAt": null,
    "completedAt": null,
    "lastError": null
  }
}
```

`delivery.status` 可能值：

```text
pending -> processing -> acked
                    \-> pending（重试）
                    \-> dead_letter
```

没有启用 Agent 回调时，事件保持 `pending`，由外部消费者通过 REST/SSE 读取后调用 ACK 接口。

## 4. 健康检查

### `GET /healthz`

仅说明 HTTP 进程可响应。

```json
{
  "status": "ok",
  "time": "2026-08-10T18:00:00.000Z"
}
```

### `GET /readyz`

说明状态存储已加载，可接受请求。

```json
{
  "status": "ready",
  "store": {
    "ready": true,
    "events": { "pending": 1 },
    "outbound": {},
    "receipts": 0,
    "nextSequence": 2,
    "stateFile": "gateway-state.json"
  }
}
```

## 5. Webhook 入站

### `POST /webhooks/{channel}/{accountId}`

- `accountId` 可省略，默认 `default`；
- 该接口不使用管理 API Key；鉴权由对应 Adapter 负责；
- 成功写入持久化状态后才返回成功；
- 平台重复事件返回原事件 ID，并在 `duplicates` 中列出。

成功响应：

```json
{
  "accepted": true,
  "event_ids": ["evt_..."],
  "duplicates": [],
  "ignored": false
}
```

### 5.1 Loopback Webhook

请求头：

```http
X-CG-Webhook-Token: <loopback.accounts.<id>.webhookToken>
```

请求体：

```json
{
  "platform_event_id": "event-001",
  "sender_id": "user-001",
  "sender_name": "Alice",
  "conversation_id": "chat-001",
  "conversation_type": "direct",
  "message_id": "message-001",
  "message_type": "text",
  "text": "你好",
  "thread_id": null,
  "metadata": {}
}
```

也可传完整嵌套结构：

```json
{
  "platform_event_id": "event-001",
  "sender": { "id": "user-001", "name": "Alice", "type": "user" },
  "conversation": { "id": "chat-001", "type": "direct" },
  "message": { "id": "message-001", "type": "text", "text": "你好" }
}
```

### 5.2 Generic Webhook：HMAC 模式

配置：

```json
{
  "signatureRequired": true,
  "webhookSecret": "secret",
  "maxSkewSeconds": 300
}
```

签名原文：

```text
<timestamp>.<原始HTTP请求体字节>
```

签名算法：

```text
hex(HMAC-SHA256(webhookSecret, signingPayload))
```

请求头：

```http
X-CG-Timestamp: 1786384800
X-CG-Signature: sha256=<hex-digest>
```

`timestamp` 可以使用秒或毫秒。超过允许时间偏差的请求被拒绝。

### 5.3 Generic Webhook：Token 模式

当未配置 `webhookSecret` 时，可配置 `webhookToken`：

```http
X-CG-Webhook-Token: <token>
```

或：

```http
Authorization: Bearer <token>
```

### 5.4 飞书 Webhook

地址：

```text
/webhooks/feishu/{accountId}
```

Adapter 处理：

- URL Verification；
- `verificationToken`；
- `X-Lark-Request-Timestamp`、`X-Lark-Request-Nonce`、`X-Lark-Signature`；
- 加密事件解密；
- `im.message.receive_v1` 文本标准化；
- 其他事件返回 `ignored=true`。

URL Verification 响应：

```json
{
  "challenge": "..."
}
```

## 6. 服务元信息与通道

### `GET /v1/meta`

返回服务版本、参考上游版本和存储统计。

### `GET /v1/channels`

返回已注册 Adapter、别名、启用状态、账号和能力：

```json
{
  "channels": [
    {
      "id": "feishu",
      "label": "Feishu / Lark",
      "aliases": ["lark"],
      "enabled": true,
      "accounts": ["default"],
      "capabilities": {
        "inbound": { "webhook": true, "encryptedWebhook": true, "text": true },
        "outbound": { "text": true, "replyTo": true }
      }
    }
  ]
}
```

## 7. 可信内部入站

### `POST /v1/inbound`

适用于已在前置服务中完成平台验签、鉴权和格式转换的场景。

```json
{
  "platform_event_id": "bridge-event-001",
  "channel": "generic",
  "account_id": "default",
  "kind": "message",
  "occurred_at": "2026-08-10T18:00:00.000Z",
  "sender": {
    "id": "user-001",
    "name": "Alice",
    "type": "user"
  },
  "conversation": {
    "id": "chat-001",
    "type": "group",
    "thread_id": "thread-001"
  },
  "message": {
    "id": "message-001",
    "type": "text",
    "text": "你好",
    "mentions": [],
    "media": []
  },
  "metadata": {}
}
```

首次写入返回 HTTP 202，重复事件返回 HTTP 200。

## 8. 读取和确认入站事件

### `GET /v1/events`

查询参数：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `after_sequence` | `0` | 只返回序号大于该值的事件 |
| `limit` | `100` | 1–1000 |
| `status` | 空 | `pending`、`processing`、`acked`、`dead_letter` 等 |
| `channel` | 空 | 按通道过滤 |
| `session_key` | 空 | 按统一会话键过滤 |

### `GET /v1/events/{eventId}`

读取单个事件。

### `POST /v1/events/{eventId}/ack`

`status` 只允许 `acked` 或 `ignored`：

```json
{
  "status": "acked",
  "details": {
    "consumer": "langgraph-worker-1",
    "run_id": "run-001"
  }
}
```

### `POST /v1/events/{eventId}/retry`

把事件重新置为 `pending` 并立即允许处理。

### `GET /v1/dead-letters`

读取死信事件。

### `POST /v1/dead-letters/{eventId}/retry`

把死信重新放入待处理队列。

## 9. SSE 事件流

### `GET /v1/events/stream`

请求头：

```http
Authorization: Bearer <api-key>
Accept: text/event-stream
```

可传：

```text
after_sequence=<last-sequence>
```

或使用标准：

```http
Last-Event-ID: <last-sequence>
```

连接建立后先返回当前回放快照边界：

```text
event: ready
data: {"replay_from":0,"snapshot_sequence":12}
```

随后回放 `inbound` 事件，回放结束时返回：

```text
event: replay-complete
data: {"replayed":12,"last_sequence":12}
```

实时阶段可能输出：

- `inbound`；
- `event-status`；
- `outbound`；
- `receipt`。

连接空闲时使用 SSE 注释行发送 heartbeat。只有与入站事件关联的 `inbound` 和 `event-status` 使用数值型 SSE `id`；`Last-Event-ID` 因而始终可作为 `after_sequence` 使用。回放只包含持久化的入站事件，其他实时通知不做历史回放。

浏览器原生 `EventSource` 不能设置 `Authorization` 请求头。生产环境建议使用支持自定义请求头的 `fetch` 流客户端，或在受控反向代理中注入鉴权。`allowQueryToken` 默认关闭。

## 10. 主动发送消息

### `POST /v1/messages/send`

```json
{
  "channel": "feishu",
  "account_id": "default",
  "to": {
    "conversation_id": "oc_xxx",
    "receive_id_type": "chat_id"
  },
  "text": "主动消息",
  "metadata": {
    "source": "scheduler"
  }
}
```

建议请求头：

```http
Idempotency-Key: job-20260810-user-001
```

也可在请求体传 `idempotencyKey` 或 `idempotency_key`。

成功响应：

```json
{
  "intent": {
    "id": "out_...",
    "status": "sent",
    "idempotencyKey": "job-20260810-user-001",
    "receiptId": "rcpt_..."
  },
  "receipt": {
    "id": "rcpt_...",
    "platformMessageId": "om_xxx",
    "evidence": "platform_api_accepted"
  },
  "replayed": false
}
```

同一幂等键和相同请求重复提交时返回原结果，`replayed=true`。同一幂等键对应不同请求时返回 HTTP 409 `IDEMPOTENCY_CONFLICT`。

## 11. 回复入站事件

### `POST /v1/messages/reply`

```json
{
  "event_id": "evt_...",
  "text": "收到",
  "metadata": {}
}
```

默认继承原事件的：

- `channel`；
- `accountId`；
- `conversation.id`；
- `message.id` 作为 `replyToId`；
- `conversation.threadId`。

调用方也可显式覆盖这些字段。

## 12. 出站意图与回执

### `GET /v1/outbound/intents`

查询参数：`status`、`limit`。

状态：

```text
created -> sending -> sent
                   -> failed
                   -> unknown
```

### `GET /v1/outbound/intents/{intentId}`

读取发送意图。

### `POST /v1/outbound/intents/{intentId}/retry`

普通重试：

```json
{}
```

对 `unknown` 强制重试：

```json
{
  "force": true
}
```

`force=true` 可能导致平台重复消息，必须先人工核查。

### `GET /v1/receipts`

读取最近的发送回执。

## 13. Agent 回调协议

当配置 `delivery.callbackUrl` 时，网关自动 POST：

```http
Content-Type: application/json
Authorization: Bearer <delivery.callbackToken>
X-CG-Event-Id: evt_...
X-CG-Delivery-Attempt: 1
```

请求体：

```json
{
  "version": "1.0",
  "type": "channel.inbound",
  "event": {}
}
```

Agent 响应：

```json
{
  "ack": true,
  "messages": [
    {
      "text": "回复1"
    },
    {
      "text": "回复2",
      "idempotencyKey": "custom-key"
    }
  ]
}
```

`messages` 中的每一项与 `/v1/messages/reply` 的消息字段兼容。没有显式幂等键时，网关使用：

```text
agent:<event-id>:<message-index>
```

以下情况触发重试：

- 网络错误或超时；
- HTTP 5xx；
- 响应不是 JSON 对象；
- `ack=false`；
- `autoAck=false` 且未返回 `ack=true`；
- 自动回复发送失败。

明确的 HTTP 4xx 被视为永久回调错误，直接进入死信；其他错误按指数退避，达到 `maxAttempts` 后进入死信。

## 14. Generic 出站回调协议

配置 `generic.accounts.<id>.outboundUrl` 后，网关 POST：

```json
{
  "version": "1.0",
  "delivery_id": "out_...",
  "channel": "generic",
  "account_id": "default",
  "to": {
    "conversation_id": "chat-001",
    "receive_id_type": null
  },
  "message": {
    "type": "text",
    "text": "回复内容",
    "reply_to_id": "message-001",
    "thread_id": "thread-001"
  },
  "metadata": {}
}
```

请求头：

```http
X-CG-Delivery-Id: out_...
Authorization: Bearer <outboundBearerToken>
```

桥接服务应返回：

```json
{
  "message_id": "platform-message-001",
  "conversation_id": "chat-001"
}
```

平台侧也应使用 `X-CG-Delivery-Id` 做幂等处理。仅依靠网关本地幂等不能防止“请求已到平台但响应在网络中丢失”造成的重复。

## 15. OpenAPI

完整机器可读定义：

```text
openapi/openapi.yaml
```
