# Channel Adapter 开发说明

## 1. Adapter 接口

新增通道需实现：

```javascript
export class MyAdapter {
  constructor(logger) {
    this.logger = logger;
    this.id = "mychannel";
    this.meta = {
      label: "My Channel",
      aliases: ["alias"]
    };
    this.capabilities = {
      inbound: { webhook: true, text: true },
      outbound: { text: true, replyTo: true }
    };
  }

  async receive(context) {
    // 返回 { events: [...] }
  }

  async send(context) {
    // 返回 { platformMessageId, conversationId, raw }
  }
}
```

随后在 `src/app.js` 注册：

```javascript
registry.register(new MyAdapter(logger));
```

并在配置中增加：

```json
{
  "channels": {
    "mychannel": {
      "enabled": true,
      "accounts": {
        "default": {}
      }
    }
  }
}
```

## 2. `receive()` 输入

```javascript
{
  accountId,
  accountConfig,
  headers,   // 已转为小写键
  rawBody,   // Buffer，验签必须使用原始字节
  body       // JSON解析结果
}
```

不要使用重新序列化后的 JSON 做平台签名校验，因为空格、键顺序和转义变化会使签名失效。

## 3. `receive()` 输出

普通事件：

```javascript
{
  events: [normalizedEvent]
}
```

平台 URL 校验等即时响应：

```javascript
{
  response: {
    status: 200,
    body: { challenge: "..." }
  },
  events: []
}
```

忽略非目标事件：

```javascript
{
  events: [],
  ignored: true,
  ignoredReason: "unsupported event type"
}
```

## 4. 统一事件最低字段

```javascript
normalizeTrustedInbound({
  channel: "mychannel",
  accountId,
  platformEventId: "stable-platform-event-id",
  kind: "message",
  occurredAt: new Date().toISOString(),
  sender: {
    id: "platform-user-id",
    name: "optional name",
    type: "user"
  },
  conversation: {
    id: "platform-chat-id",
    type: "direct", // direct/group/channel
    threadId: "optional-thread-id",
    parentId: "optional-parent-message-id"
  },
  message: {
    id: "platform-message-id",
    type: "text",
    text: "message text",
    replyToId: undefined,
    mentions: [],
    media: []
  },
  raw: platformPayload,
  metadata: {}
});
```

### ID 选择要求

- `platformEventId`：平台每次事件投递稳定不变，用于去重；
- `message.id`：平台消息 ID，用于回复；
- `conversation.id`：聊天、群、频道或用户会话 ID；
- `threadId`：只有平台明确区分线程时填写；
- 不要把随机生成值用于 `platformEventId`，否则重复事件无法去重。

## 5. `send()` 输入

```javascript
{
  request: {
    channel,
    accountId,
    to: {
      conversationId,
      receiveIdType
    },
    text,
    replyToId,
    threadId,
    metadata,
    idempotencyKey
  },
  accountConfig,
  intentId
}
```

## 6. `send()` 输出

```javascript
{
  platformMessageId: "required-platform-message-id",
  conversationId: "actual-target-conversation-id",
  raw: platformResponse
}
```

平台明确成功但没有返回消息 ID 时，不应伪造成功；应抛出 `PlatformSendError` 并设置 `ambiguous=true`。

## 7. 错误分类

使用：

```javascript
throw new PlatformSendError("PLATFORM_ERROR", "message", {
  details: {},
  ambiguous: false,
  safeToRetry: true,
  retryable: false
});
```

含义：

| 情况 | `ambiguous` | 典型本地状态 |
|---|---:|---|
| 平台明确返回鉴权失败、参数错误、限流拒绝 | `false` | `failed` |
| DNS、连接重置、客户端超时，无法确认平台是否收到 | `true` | `unknown` |
| 平台 5xx 且不能确认是否已落库 | `true` | `unknown` |
| 平台明确返回成功并给出消息 ID | 不适用 | `sent` |

`safeToRetry` 表示平台结果是否明确到足以允许受控重试；它不等于网关会自动重试出站消息。出站重试始终由调用方或运维决定。

## 8. QQ / 微信 / 企业微信接入建议

### 方案 A：Generic Bridge

先用平台 SDK 或机器人框架建立一个小型桥接服务：

```text
平台事件 -> 桥接服务验签 -> POST /webhooks/generic/account
网关出站 -> generic.outboundUrl -> 桥接服务 -> 平台发送API
```

优点：

- 不修改网关核心；
- 平台 SDK 与网关 Node 版本解耦；
- 个人微信等非标准接入可以隔离风险；
- 一个 Generic Adapter 可服务多个平台账号。

桥接器应：

- 生成稳定 `platform_event_id`；
- 验证平台签名和时间戳；
- 使用 `X-CG-Delivery-Id` 做出站幂等；
- 不把平台 access token 写入入站消息；
- 限制媒体 URL 和文件大小。

### 方案 B：原生 Adapter

适合平台有稳定官方 API、需要平台特有线程/卡片/流式消息能力的场景。把 token 获取、事件验签和出站发送全部封装在 Adapter 内。

## 9. 测试最低要求

每个新 Adapter 至少覆盖：

1. 正确签名通过；
2. 错误签名拒绝；
3. 重复平台事件去重；
4. 普通文本标准化；
5. 群聊与私聊 ID 映射；
6. 平台成功发送并生成回执；
7. 明确拒绝归类为 `failed`；
8. 网络错误归类为 `unknown`；
9. 进程重启后的状态恢复；
10. 敏感字段不进入日志。
