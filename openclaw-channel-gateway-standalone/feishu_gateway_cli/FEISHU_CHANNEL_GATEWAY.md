# 飞书 + Channel Gateway 集成说明

## 1. 整体架构

```
飞书服务器  ──Webhook POST──>  Channel Gateway  ──写入──>  gateway-state.json
  （公网）                         :8787
                                          │
                                   Python 轮询客户端
                                   GET /v1/events
```

Channel Gateway 是消息通道网关，位于飞书服务器和你的业务逻辑之间：
- 接收飞书 Webhook 事件，标准化为统一格式
- 持久化存储事件到本地 JSON 文件
- 提供 REST 接口供下游消费

---

## 2. 两种订阅方式

飞书开放平台支持两种接收事件的方式，**互斥**：

| 订阅方式 | 原理 | 是否需要公网 URL |
|---------|------|----------------|
| **长连接** | 客户端主动连飞书服务器，保持长连接 | ❌ 不需要 |
| **Webhook** | 飞书服务器主动推送到你的公网地址 | ✅ 需要 |

Channel Gateway **只支持 Webhook 模式**，不支持长连接。

如果飞书应用已启用长连接，需要在飞书开放平台切换回 Webhook 模式。

---

## 3. 完整配置流程（WebSocket 模式）

### 第一步：创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)
2. 创建企业自建应用
3. 进入 **应用功能 → 机器人**，开启机器人能力
4. 获取 **App ID** 和 **App Secret**（应用凭证页面）

### 第二步：获取 open_id

让目标用户给 Bot 发一条消息，从 Gateway 日志或 `gateway-state.json` 中获取发送者的 `open_id`（`ou_xxx` 格式）。

### 第三步：安装 ngrok（内网穿透）

ngrok 将本地端口映射为公网可访问的 URL：

```powershell
# 安装（winget）
winget install Ngrok.Ngrok

# 注册并配置 authtoken（从 https://dashboard.ngrok.com 获取）
ngrok config add-authtoken 你的token

# 启动隧道，映射本地 8787 端口
ngrok http 8787
```

成功后会显示：
```
Forwarding  https://abc123.ngrok-free.dev -> http://localhost:8787
```

> 注意：ngrok 免费版每次重启 URL 会变化，需要重新在飞书平台更新配置。

### 第四步：配置 .env

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_DOMAIN=feishu
FEISHU_VERIFICATION_TOKEN=xxx
FEISHU_ENCRYPT_KEY=
```

> `FEISHU_ENCRYPT_KEY` 为空即可，不需要加密。

### 第五步：配置 config

`config/config.feishu.local.json` 示例：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8787,
    "apiKey": "${CG_API_KEY}"
  },
  "channels": {
    "feishu": {
      "enabled": true,
      "accounts": {
        "default": {
          "domain": "feishu",
          "appId": "${FEISHU_APP_ID}",
          "appSecret": "${FEISHU_APP_SECRET}",
          "verificationToken": "${FEISHU_VERIFICATION_TOKEN}"
        }
      }
    }
  }
}
```

注意：`encryptKey` 字段已移除，因为不需要加密。

### 第六步：启动 Gateway

```bash
node start.mjs --config config/config.feishu.local.json
```

### 第七步：飞书开放平台配置 Webhook URL

1. 进入应用 → **事件与回调 → 订阅请求 URL**
2. 选择 **Webhook** 订阅方式
3. 填入：`https://abc123.ngrok-free.dev/webhooks/feishu/default`
4. 填入 **Verification Token**（与 `.env` 中的 `FEISHU_VERIFICATION_TOKEN` 完全一致）
5. 保存 → 验证连接

### 第八步：订阅事件

在 **事件与回调 → 添加事件** 中添加：
- `im.message.receive_v1`（接收消息）

### 第九步：发布应用

在 **版本管理与发布** 中创建版本并发布，使应用对内可见。

### 第十步：启动轮询客户端

```bash
python polling_test.py
```

### 第十一步：发消息测试

让用户给 Bot 发一条消息，轮询客户端应该能收到事件。

---

## 4. 消息流程详解

### 4.1 飞书 → Gateway（Webhook 推送）

```
1. 用户在飞书中给 Bot 发消息
2. 飞书服务器 POST 到 Webhook URL
   POST https://公网URL/webhooks/feishu/default
3. Gateway 接收请求，校验 Verification Token
4. Gateway 解析事件，标准化为统一格式
5. Gateway 写入 gateway-state.json（持久化 + 去重）
6. Gateway 返回 200 OK 给飞书
```

### 4.2 Gateway → 下游消费（REST 轮询）

```
1. Python 客户端 GET /v1/events?after_sequence=0
2. Gateway 从 gateway-state.json 读取事件
3. 返回标准化事件列表（含 latestSequence）
4. Python 客户端处理事件
5. 下一次轮询用 after_sequence=上次的 latestSequence
```

### 4.3 下游 → 飞书（回复消息）

```
1. Python 客户端 POST /v1/messages/reply
   body: { "event_id": "evt_xxx", "text": "回复内容" }
2. Gateway 根据 event_id 找到原消息的会话信息
3. Gateway 调用飞书发送消息 API
4. 飞书服务器将消息投递给用户
```

---

## 5. 核心 ID 类型

| ID 类型 | 格式 | 用途 |
|--------|------|------|
| `open_id` | `ou_xxx` | 用户唯一标识，单聊时使用 |
| `chat_id` | `oc_xxx` | 群会话标识，群聊时使用 |
| `message_id` | `om_xxx` | 消息唯一 ID，用于回复 |
| `tenant_key` | 企业标识 | 多租户场景使用 |

---

## 6. 常见问题排查

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `FEISHU_TOKEN_INVALID` | Verification Token 不匹配 | 两边的 token 必须完全一致 |
| `FEISHU_SIGNATURE_INVALID` | encryptKey 不为空但无效 | 删除 config 中的 encryptKey 字段，或在飞书平台关闭加密 |
| `CONFIG_ENV_MISSING: FEISHU_ENCRYPT_KEY` | encryptKey 为空但配置引用了它 | 从 config JSON 中删除 encryptKey 字段 |
| 事件未到达 Gateway | 应用未发布/测试人员未添加 | 在飞书平台发布应用或添加测试人员 |
| URL 验证失败 | ngrok 未运行或 URL 不匹配 | 确认 ngrok 在线且 URL 完全一致 |
| 轮询返回空事件 | 还没有消息 | 让用户给 Bot 发消息 |
| ngrok URL 每次重启都变 | 免费版特性 | 每次重启后需重新在飞书平台更新 URL |

---

## 7. 启动命令汇总

### 启动 Gateway
```bash
node start.mjs --config config/config.feishu.local.json
```

### 启动轮询客户端
```bash
python polling_test.py
```

### 启动 ngrok 隧道
```bash
ngrok http 8787
```

### 验证 Gateway 状态
```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
```

### 查看当前事件
```bash
curl -sS "http://127.0.0.1:8787/v1/events?after_sequence=0" \
  -H "Authorization: Bearer gateway-api-key-16chars!"
```

### 查看 gateway-state.json
```bash
cat data/gateway-state.json
```

### 查看 ngrok 请求日志
```
http://127.0.0.1:4040
```

---

## 8. 文件说明

| 文件 | 作用 |
|------|------|
| `sendtest.py` | 飞书 Agent 客户端（主动发送消息） |
| `polling_test.py` | 轮询客户端（接收并打印事件） |
| `src/adapters/feishu.js` | 飞书适配器（Webhook 接收 + 消息发送） |
| `src/server.js` | HTTP 服务器（路由 + 事件接口） |
| `config/config.feishu.local.json` | 飞书通道配置 |
| `gateway-state.json` | 事件持久化存储（自动生成） |

---

## 9. 环境变量详解

`.env` 中每个字段的**完整信息流**：

### FEISHU_APP_ID / FEISHU_APP_SECRET

```
用途：获取 tenant_access_token（发送消息时需要）
位置：飞书开放平台 → 应用凭证
格式：App ID 为 cli_xxx，App Secret 为一串密钥
```

**信息流：**

```
Gateway 需要调用飞书 API 发送消息时：
1. 用 APP_ID + APP_SECRET 调用飞书 auth API
   POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
   body: { "app_id": "...", "app_secret": "..." }
2. 飞书返回 tenant_access_token（有效期 2 小时）
3. Gateway 缓存这个 token
4. 发送消息时在 Header 中带上：
   Authorization: Bearer {tenant_access_token}
```

**注意：** APP_ID 和 APP_SECRET 是飞书应用的身份凭证，用于证明"是谁在调用 API"。

---

### FEISHU_DOMAIN

```
用途：确定调用哪个飞书域名（中国站 or 国际版）
可选值：feishu（默认，中国站）/ lark（国际版）
```

**信息流：**

```
feishu → https://open.feishu.cn
lark   → https://open.larksuite.com

所有飞书 API 请求都发送到对应的域名。
```

---

### FEISHU_VERIFICATION_TOKEN

```
用途：飞书 Webhook 回调时，Gateway 校验请求是否来自飞书
位置：飞书开放平台 → 事件与回调 → 订阅请求 URL 配置页面
要求：必须与飞书平台填写的 Token 完全一致
```

**信息流：**

```
1. 你在飞书平台填入 Token（如：isyToouZbptRt6i51KV7fbmWWHVgTrpR）
2. 飞书每次 POST 请求都会带上这个 token
   Header: 无（token 在 body 里）
   Body: { "token": "isyToouZbptRt6i51KV7fbmWWHVgTrpR", ... }
3. Gateway 收到请求后，用 constantTimeEqual 对比两边 token
4. 一致 → 放行，继续处理
   不一致 → 401 Unauthorized，拒绝请求
```

**为什么用 constantTimeEqual？** 防止时序攻击（Timing Attack），不能用普通字符串比较。

**注意：** 这个 token 主要用于"URL 验证"阶段，防止别人随便 POST 假消息到你的 Gateway。

---

### FEISHU_ENCRYPT_KEY

```
用途：AES-256-CBC 加密事件的解密密钥（可选）
位置：飞书开放平台 → 事件与回调 → 加密策略
要求：如果飞书开启了加密，这里必须填入对应的 key；未开启加密则留空
```

**信息流：**

```
飞书开启加密时：
1. 飞书用 AES-256-CBC 加密事件 body
2. POST body 变成：{ "encrypt": "加密后的字符串" }
3. Gateway 用这里的 key 解密
   decryptFeishuPayload(encryptKey, body.encrypt)
4. 解密后才是原始事件 JSON

未开启加密时：
1. POST body 直接是原始 JSON，不需要解密
2. 这个字段留空即可，Gateway 跳过解密步骤
```

**本次配置：** 未开启加密，所以 FEISHU_ENCRYPT_KEY 为空，config.json 中也已移除该字段。

---

### CG_API_KEY

```
用途：Gateway REST API 的认证密钥（下游客户端调用时需要）
要求：至少 16 字符
```

**信息流：**

```
polling_test.py 发请求时：
GET /v1/events
Header: Authorization: Bearer gateway-api-key-16chars!

Gateway 收到请求后：
1. 提取 Header 中的 Bearer token
2. 与 CG_API_KEY 用 constantTimeEqual 对比
3. 一致 → 返回事件数据
   不一致 → 401 Unauthorized

sendtest.py（直接调飞书 API，不走 Gateway）不需要这个。
```

**为什么需要？** Gateway 的 REST API 是公开的，需要认证防止别人随便抓你的事件数据。

---

### 环境变量对照表

| 变量 | 方向 | 用途 | 有效期/作用域 |
|------|------|------|--------------|
| `FEISHU_APP_ID` | 飞书→Gateway | 应用身份标识 | 永久（应用级别） |
| `FEISHU_APP_SECRET` | 飞书→Gateway | 获取 token 的密钥 | 永久（应用级别） |
| `FEISHU_DOMAIN` | Gateway→飞书 | 确定 API 域名 | 永久 |
| `FEISHU_VERIFICATION_TOKEN` | 飞书→Gateway | 校验请求来源 | 永久（需两边一致） |
| `FEISHU_ENCRYPT_KEY` | 飞书→Gateway | 解密加密事件 | 永久（需两边一致） |
| `CG_API_KEY` | 下游→Gateway | REST API 认证 | 永久（需两边一致） |

### 信息流全景图

```
┌─────────────┐         ┌──────────────────────────────────────────┐
│  飞书服务器  │         │           Channel Gateway (:8787)          │
│             │         │                                          │
│  发送消息    │         │  ┌─────────────────────────────────────┐ │
│  POST       │────────▶│  │ 1. 接收 POST /webhooks/feishu/default │ │
│  Body: {    │         │  │ 2. 校验 FEISHU_VERIFICATION_TOKEN     │ │
│    token,   │         │  │    （失败→401）                       │ │
│    encrypt, │         │  │ 3. 解密 FEISHU_ENCRYPT_KEY（可选）    │ │
│    event... │         │  │    （失败→401）                       │ │
│  }          │         │  │ 4. 标准化事件                        │ │
│             │         │  │ 5. 写入 gateway-state.json           │ │
│             │◀────────│  │ 6. 返回 200 OK                       │ │
└─────────────┘         │  └─────────────────────────────────────┘ │
                        │                                          │
                        │  ┌─────────────────────────────────────┐ │
                        │  │ GET /v1/events                      │ │
                        │  │ 校验 CG_API_KEY（失败→401）          │ │
┌─────────────┐         │  │ 读取 gateway-state.json              │ │
│  Python     │◀────────│  │ 返回事件列表                         │ │
│  轮询客户端  │         │  └─────────────────────────────────────┘ │
│  polling    │         │                                          │
│  _test.py   │         │  ┌─────────────────────────────────────┐ │
└─────────────┘         │  │ POST /v1/messages/reply             │ │
                        │  │ 用 FEISHU_APP_ID + APP_SECRET        │ │
                        │  │ 获取 tenant_access_token             │ │
                        │  │ 调用飞书发送消息 API                 │ │
                        │  └─────────────────────────────────────┘ │
                        └──────────────────────────────────────────┘
```
