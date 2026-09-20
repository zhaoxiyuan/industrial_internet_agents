# 部署说明

## 1. 本机运行

```bash
cp config/config.example.json config/config.local.json
export CHANNEL_GATEWAY_API_KEY='replace-with-a-long-random-key'
export LOOPBACK_WEBHOOK_TOKEN='replace-loopback-token'
node src/index.js --config config/config.local.json
```

日志是每行一个 JSON 对象，可直接由 Loki、Fluent Bit 或 Filebeat 采集。

## 2. Docker

构建：

```bash
docker build -t channel-gateway:0.1.0 .
```

运行：

```bash
docker run --rm \
  -p 127.0.0.1:8787:8787 \
  -e CHANNEL_GATEWAY_API_KEY='replace-with-a-long-random-key' \
  -e LOOPBACK_WEBHOOK_TOKEN='replace-loopback-token' \
  -v channel-gateway-data:/app/data \
  channel-gateway:0.1.0
```

容器默认使用 `config/config.container.json`，监听 `0.0.0.0:8787`。示例使用 Docker named volume，避免宿主机目录 UID/GID 不匹配；端口发布时仍建议只绑定宿主机回环地址，由反向代理暴露必要路径。

使用 Compose：

```bash
cp .env.example .env
# 修改 .env

docker compose up --build
```

## 3. systemd 示例

```ini
[Unit]
Description=Standalone Channel Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=channel-gateway
Group=channel-gateway
WorkingDirectory=/opt/openclaw-channel-gateway-standalone
EnvironmentFile=/etc/channel-gateway.env
ExecStart=/usr/bin/node src/index.js --config config/config.production.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/channel-gateway

[Install]
WantedBy=multi-user.target
```

生产配置将 `storage.directory` 指向：

```text
/var/lib/channel-gateway
```

## 4. Nginx 路由边界

仅向公网开放 Webhook：

```nginx
server {
    listen 443 ssl http2;
    server_name gateway.example.com;

    client_max_body_size 1m;

    location /webhooks/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_request_buffering on;
        proxy_read_timeout 20s;
    }

    location /healthz {
        proxy_pass http://127.0.0.1:8787;
    }

    location /readyz {
        proxy_pass http://127.0.0.1:8787;
    }

    location /v1/ {
        deny all;
    }
}
```

Agent 从内网直接访问 `127.0.0.1:8787`，或另设只允许 VPN/mTLS 的内部虚拟主机。

## 5. 飞书部署

### 5.1 配置

```bash
cp config/config.feishu.example.json config/config.feishu.local.json

export CHANNEL_GATEWAY_API_KEY='...'
export FEISHU_APP_ID='cli_xxx'
export FEISHU_APP_SECRET='...'
export FEISHU_VERIFICATION_TOKEN='...'
export FEISHU_ENCRYPT_KEY='...'

node src/index.js --config config/config.feishu.local.json
```

中国站：

```json
"domain": "feishu"
```

国际站：

```json
"domain": "lark"
```

### 5.2 飞书开放平台

在应用事件订阅中配置：

```text
https://gateway.example.com/webhooks/feishu/default
```

订阅事件：

```text
im.message.receive_v1
```

应用还需开通与接收、发送消息相关的权限，并发布可用版本。飞书控制台中的 Verification Token 和 Encrypt Key 必须与配置一致。

### 5.3 URL 校验失败排查

检查：

1. 公网 HTTPS 证书有效；
2. Nginx 没有修改请求 JSON；
3. 回调路径账号 ID 正确；
4. `verificationToken` 和 `encryptKey` 无多余空格；
5. 系统时间同步；
6. 日志中的错误码是 `FEISHU_TOKEN_INVALID`、`FEISHU_SIGNATURE_INVALID` 还是 `FEISHU_DECRYPT_FAILED`；
7. 反向代理请求体限制不小于飞书事件体。

### 5.4 发不出消息

检查：

- `appId`、`appSecret`；
- 应用是否已发布和安装；
- Bot 是否在目标群；
- 目标 `chat_id` 是否来自相同租户和应用可见范围；
- 日志及发送意图 `lastError.details.upstream_body`；
- `/v1/outbound/intents?status=failed` 和 `status=unknown`。

## 6. LangGraph 回调部署

启动示例 Agent：

```bash
cd examples/langgraph
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export AGENT_CALLBACK_TOKEN='replace-callback-token'
uvicorn app:app --host 127.0.0.1 --port 8000
```

再启动 Gateway，使用 `config/config.callback.example.json`。

## 7. 监控

当前没有 Prometheus endpoint，可用以下接口和日志构建监控：

- `/readyz`：事件状态计数；
- `/v1/dead-letters`：死信数量；
- `/v1/outbound/intents?status=unknown`：不确定发送；
- JSON 日志中的 `error_code`、`duration_ms`、`event_id`、`intent_id`；
- 状态文件大小和磁盘剩余空间。

建议告警：

```text
readyz != 200
pending 持续增长
dead_letter > 0
unknown > 0
callback 重试率升高
state file 写入失败
磁盘空间不足
```

## 8. 多副本与高可用

当前版本不要让两个实例挂载并写同一 `gateway-state.json`。需要高可用时：

1. 把 Store 替换为共享数据库/队列；
2. 入站使用唯一约束 `(channel, account_id, platform_event_id)`；
3. 领取事件使用租约和数据库锁；
4. 出站幂等键使用唯一约束；
5. 回执和意图在同一事务中更新；
6. SSE 改用数据库通知、Redis Pub/Sub 或消息总线；
7. 对平台 Webhook 使用负载均衡。
