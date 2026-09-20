# 飞书配置教程

本文说明如何把企业自建飞书应用接入 Channel Gateway 和 P8。飞书业务接口及卡片数据协议见 [飞书接口](FEISHU_API.md)，Gateway 通用 REST 协议见 [Gateway 标准接口](GATEWAY.md)。

## 1. 配置结果

完成后链路应为：

```text
飞书用户/群
  ↕ Webhook 与 OpenAPI
Channel Gateway :8787
  ↕ /v1/events、/v1/messages/*
P8 chat_reply daemon
  ↕ P8Job 与 CardActionAgent
Web 服务 :8080 /api/feishu/card-callback
```

项目使用账号名 `P8`。同一张卡片的创建、发送、按钮回调后的更新必须使用同一个 App 身份。

## 2. 创建并发布飞书应用

1. 在飞书开放平台创建企业自建应用。
2. 开启机器人能力，记录 `App ID` 和 `App Secret`。
3. 开通消息发送/接收及“创建与更新卡片”权限；卡片使用 CardKit JSON 2.0。
4. 在“事件与回调”中选择 Webhook 订阅方式，添加消息接收事件。
5. 配置请求地址后发布应用，或把联调人员加入测试范围。

本 Gateway 使用 Webhook，不使用飞书长连接模式。本地开发必须通过反向代理或内网穿透提供 HTTPS 公网地址。

## 3. 配置环境变量

推荐启动配置页面，它会同时维护项目根 `.env`、Gateway 的 `.env` 和 `config.feishu.local.json`，写入前会创建 `.bak`：

```powershell
python openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_config_app.py --host 127.0.0.1 --port 5003
```

浏览器打开 `http://127.0.0.1:5003`，填写 Gateway、飞书账号、用户和群聊四部分。

核心配置的等价结构如下。真实 secret 只能写进未纳入 Git 的 `.env`，不得写进 JSON 示例或提交到仓库。

```dotenv
GATEWAY_HOST=http://127.0.0.1:8787
CG_API_KEY=至少16位的随机字符串
CG_DEFAULT_CHANNEL=feishu
CG_DEFAULT_ACCOUNT_ID=P8

FEISHU_P8_DOMAIN=feishu
FEISHU_P8_APP_ID=cli_xxx
FEISHU_P8_APP_SECRET=xxx
FEISHU_P8_VERIFICATION_TOKEN=xxx

FEISHU_USER_MAP={"ou_xxx":{"role":"安全员","name":"张三"}}
FEISHU_GROUP_MAP={"oc_xxx":{"name":"作业处置群","description":"P8告警群"}}
```

配置页面还会在项目根 `.env` 保存不含 secret 的 `FEISHU_ACCOUNTS` 元数据，并将账号凭据展开为 Gateway 可读取的 `FEISHU_<ACCOUNT>_*` 变量。不要同时手工维护互相冲突的 `default` 与 `P8` 账号。

### 映射数据结构

```json
{
  "FEISHU_USER_MAP": {
    "ou_xxx": {"role": "安全员", "name": "张三"}
  },
  "FEISHU_GROUP_MAP": {
    "oc_xxx": {"name": "作业处置群", "description": "P8 告警群"}
  }
}
```

- `FEISHU_USER_MAP` 的主键是用户 `open_id`，用于单聊寻址和回调身份显示。
- `FEISHU_GROUP_MAP` 的主键是群 `chat_id`，用于按 `group_name` 反查目标群。
- 人与群是独立映射，不要把 `chat_id` 冗余写入用户条目。
- 旧 `FEISHU_CONVERSATION_MAP` 和 `FEISHU_GROUP_<ROLE>` 已废弃。

## 4. Gateway 账号配置

仓库已提供 `openclaw-channel-gateway-standalone/config/config.feishu.local.json`，P8 账号块应引用同名环境变量：

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
        "P8": {
          "domain": "${FEISHU_P8_DOMAIN}",
          "appId": "${FEISHU_P8_APP_ID}",
          "appSecret": "${FEISHU_P8_APP_SECRET}",
          "verificationToken": "${FEISHU_P8_VERIFICATION_TOKEN}"
        }
      }
    }
  }
}
```

Gateway REST API Key 与飞书 Verification Token 用途不同：前者保护 `/v1/*`，后者校验飞书 Webhook。两者不要复用。

## 5. 配置 Webhook

Gateway 的飞书事件入口格式是：

```text
https://<公网域名>/webhooks/feishu/P8
```

在飞书开放平台填写该 URL，并确保：

- 公网域名能转发到本机或服务器 `8787` 端口；
- URL 中账号名与 Gateway 配置的 `P8` 完全一致，区分大小写；
- Verification Token 与 `FEISHU_P8_VERIFICATION_TOKEN` 一致；
- 使用加密回调时还需按 Gateway 配置提供对应 Encrypt Key；未启用加密时不要引用空的环境变量。

飞书卡片按钮的业务回调由 Web 服务处理：

```text
POST https://<业务公网域名>/api/feishu/card-callback
```

URL 验证请求会返回 `challenge`。实际按钮事件应在 2 秒窗口内快速返回 toast，P8 状态修改和 CardKit 原位更新放到后台线程执行。

## 6. 启动与验证

一键启动 P8 联调所需的三个进程：

```powershell
python A7/adapters/p8_service_manager.py start
python A7/adapters/p8_service_manager.py status
```

启动顺序固定为 Gateway → `chat_reply` → Web；对应端口为 `8787` 和 `8080`。停止或重启：

```powershell
python A7/adapters/p8_service_manager.py stop
python A7/adapters/p8_service_manager.py restart
```

最小检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8787/healthz
Invoke-RestMethod http://127.0.0.1:8787/readyz
```

随后完成一次闭环测试：给机器人发消息 → Gateway `/v1/events` 出现事件 → P8 回复 → 推送一张风险卡片 → 点击按钮 → `data/card_callbacks.jsonl` 新增记录 → 原消息卡片变为已处理状态。

## 7. 配置页面标准接口

配置服务仅建议监听 `127.0.0.1`，不应直接暴露公网。

| 方法与路径 | 请求/返回 | 作用 |
|---|---|---|
| `GET /` | HTML | 配置页面 |
| `GET /api/feishu/config` | 遮罩后的配置 JSON | 读取 `.env`、账号与映射 |
| `POST /api/feishu/config` | `managed/accounts/user_map/group_map` | 原子保存并备份配置 |
| `POST /api/feishu/config/delete` | `type + key` 或账号 ID | 幂等删除用户、群或账号 |
| `POST /api/feishu/test/gateway` | Gateway 地址与 Key | 测试 `/healthz` |
| `POST /api/feishu/test/resolve` | 收件人条件 | 预演目标解析，不发送消息 |

## 8. 常见问题

| 现象 | 检查项 |
|---|---|
| Webhook URL 验证失败 | 公网转发、P8 路径、Verification Token、应用是否已发布 |
| `/v1/*` 返回 401 | 项目与 Gateway 使用的 `CG_API_KEY` 是否一致 |
| 找不到收件人或群 | `ou_`/`oc_` 是否写反，JSON 是否有效，名称是否唯一 |
| 卡片能发不能更新，错误 `300311` | 更新时使用了不同飞书 App；创建、发送、更新必须同账号 |
| 卡片更新报 `300317` | 同一卡片的 `sequence` 没有严格递增 |
| 点击时更新报 `200810` | 回调交互窗口内更新；应先返回，再后台有限退避更新 |
| 卡片过期 `200750` | CardKit 实体超过有效期，重新创建并发送 |
| 收不到消息 | 未订阅消息事件、机器人不在会话、测试范围/版本未生效 |

## 9. 安全要求

- `.env`、`.env.bak`、Gateway `.env` 和 token 不得提交 Git。
- 日志不得输出 App Secret、完整 token、Authorization 头或未脱敏回调体。
- `CG_API_KEY` 使用至少 16 位随机值；生产环境对 Gateway 和 Web 回调同时配置 TLS、来源限制和反向代理。
- 修改账号后重启 Gateway 与 `chat_reply`，避免进程继续使用旧环境变量。
