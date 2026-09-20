# 安全说明

## 1. 信任边界

```text
不可信聊天平台/公网
    -> Webhook Adapter（平台验签）
    -> Gateway 持久化边界
    -> 受信 Agent 网络（Bearer Token）
    -> 平台出站 API
```

`POST /v1/inbound` 是可信内部接口，不执行平台验签，只能暴露在受控网络并使用高强度 API Key。

## 2. API Key

- `server.apiKey` 至少 16 个字符；生产建议随机 32 字节以上；
- 仅接受 `Authorization: Bearer`；
- `allowQueryToken` 默认关闭，避免令牌进入 URL、代理日志和浏览器历史；
- 网关不提供用户级权限模型，建议在前置 API Gateway 中做 mTLS、IP ACL 和细粒度授权；
- 不要让聊天平台直接访问 `/v1/*`。

## 3. Webhook 验证

### Generic

优先使用 HMAC-SHA256：

```text
HMAC(secret, timestamp + "." + rawBody)
```

并校验时间偏差。不要只使用静态 Token，除非连接处于可信内网或平台无法签名。

### 飞书

- 配置 `verificationToken`；
- 配置加密密钥时校验 `X-Lark-Signature`；
- 普通事件在配置加密密钥后必须带签名；
- URL Verification 仅在成功解密并匹配 verification token 后响应 challenge；
- 反向代理必须原样转发请求体，不得重新序列化 JSON。

## 4. 密钥管理

配置支持 `${ENV_NAME}` 插值。生产环境：

- 配置文件只存环境变量引用；
- 使用 Kubernetes Secret、Docker Secret、systemd credentials 或云密钥服务；
- 禁止把密钥提交到 Git；
- 状态文件不应包含平台 access token；飞书 tenant token 仅在进程内存中缓存，飞书回调中的 verification token 在保存 `raw` 前会被替换为 `***redacted***`；
- 日志只记录 ID 和长度，不记录消息全文或 Secret；
- 配置对外展示前使用 `redactConfig()`。

## 5. SSRF 与出站地址

本实现要求上游 URL 使用 `http` 或 `https`，禁止 URL 内嵌凭据并禁止自动重定向。但它**没有完成 DNS/IP 级 SSRF 防护**。

生产要求：

- `delivery.callbackUrl` 和 `generic.outboundUrl` 只能由管理员配置，不能从入站事件动态指定；
- 使用出站防火墙或 Service Mesh 限制网关可访问地址；
- 公网部署时禁止配置任意用户可控 URL；
- 对 DNS rebinding、云元数据地址和内网管理地址使用网络层阻断；
- 如需更强保护，在 `assertStaticHttpUrl()` 中加入解析后的 IP allowlist。

## 6. 请求与响应边界

- 入站请求体默认最大 1 MiB；
- 上游响应默认最大 1 MiB；
- 所有上游请求有超时；
- HTTP redirect 被拒绝；
- JSON 字段长度有上限；
- 当前不下载媒体 URL，因此不会自动访问不可信媒体地址；
- 添加媒体下载功能时必须验证 MIME、大小、URL、域名和最终解析 IP。

## 7. 重放与幂等

### 入站

平台事件按稳定 ID 去重。时间窗口到期后，平台再次投递同 ID 可能形成新事件。对长期高风险操作，Agent 仍需保存已处理 `event.id` 或 `platformEventId`。

### 出站

- 客户端必须使用业务稳定的 `Idempotency-Key`；
- Generic 桥接器必须使用 `X-CG-Delivery-Id` 做平台侧幂等；
- 网络错误会标记为 `unknown`，默认不自动重放；
- 强制重试可能产生重复消息；
- 不要把随机幂等键用于定时任务重试，否则每次会被视为新消息。

## 8. Agent 回调风险

Gateway 重试回调会使 Agent 再次运行。Agent 工具必须按 `event.id` 幂等，或把副作用置于事务/工作流系统中。

不要让未经授权的聊天用户通过提示词直接调用高风险工具。Channel Gateway 只负责身份信息传递，不替代：

- 用户/群组 allowlist；
- RBAC；
- 工具审批；
- 设备控制安全联锁；
- 内容审计；
- 数据脱敏。

## 9. 状态文件

- 默认尝试目录 `0700`、文件 `0600`；
- 状态中可能包含消息正文和平台原始事件，属于敏感数据；
- 使用加密磁盘、最小权限用户和备份访问控制；
- 不要把 `data/gateway-state.json` 打包、上传或提交 Git；
- 单实例部署时确保只有一个进程写入该文件。

## 10. TLS 与反向代理

服务本身仅提供 HTTP。公网 Webhook 必须经 HTTPS 反向代理：

```text
Internet -> HTTPS reverse proxy -> 127.0.0.1:8787
```

建议：

- 只向公网开放 `/webhooks/*`；
- `/v1/*` 限制到内网、VPN 或 mTLS；
- 配置请求体大小和超时；
- 保留平台签名所需的原始请求体；
- 关闭代理缓存；
- 对 Webhook 做速率限制；
- 日志中隐藏 Authorization 和签名头。

## 11. 生产上线前清单

- [ ] 更换所有示例 Secret；
- [ ] 仅使用环境变量或 Secret Store；
- [ ] HTTPS 与可信反向代理；
- [ ] `/v1/*` 不暴露公网；
- [ ] Webhook 平台验签已开启；
- [ ] Agent 回调 Token 已开启；
- [ ] 网络 egress allowlist；
- [ ] 状态目录权限与备份策略；
- [ ] 出站调用使用稳定幂等键；
- [ ] Agent 工具副作用幂等；
- [ ] 死信和 `unknown` 状态告警；
- [ ] 负载超过单实例范围时更换共享存储；
- [ ] 平台权限按最小范围申请。
