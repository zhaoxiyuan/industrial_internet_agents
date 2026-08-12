# OpenClaw Channel/Gateway 剥离边界说明

## 1. 参考基线

本项目参考：

```text
Repository: openclaw/openclaw
Reference release: v2026.7.1-2
Reference commit: 0790d9f
License: MIT
```

OpenClaw 的 Gateway 并非单一“聊天消息转发目录”。在原项目中，Gateway 同时连接通道、会话、Agent、工具、节点、配置、插件、控制端和持久化运行状态。直接复制 `src/gateway` 会引入大量非 Channel 依赖，不能形成真正独立的消息网关。

因此，本交付物采用“按职责剥离”而非“复制某一目录”的方式：保留 Channel Gateway 的消息职责，并以小型独立接口重新实现。

## 2. 职责映射

| OpenClaw 相关区域 | 本项目对应 | 处理方式 |
|---|---|---|
| `src/channels` | `src/adapters`、统一入站模型 | 保留通道标准化思想，缩减为独立 Adapter |
| `src/gateway` 的消息接入与发送职责 | `src/core/gateway.js`、`src/server.js` | 重写为 REST/Webhook/SSE 服务 |
| `src/plugin-sdk` 的通道接口概念 | `ChannelRegistry`、Adapter Contract | 不保持 ABI，仅保留最小职责模型 |
| `src/routing` 的会话路由职责 | `stableSessionKey()` | 缩减为平台会话/线程到稳定键的映射 |
| 入站队列、恢复、去重 | `JsonStateStore`、`DeliveryWorker` | 独立实现持久化、重试和死信 |
| 出站发送与回执 | `outboundIntents`、`receipts` | 独立实现幂等和未知结果保护 |
| 官方通道插件 | `FeishuAdapter`、`GenericWebhookAdapter` | 仅实现当前交付范围 |

## 3. 明确删除的耦合

未迁移：

- Agent Loop 与模型调用；
- Prompt 构建、上下文压缩和记忆；
- Tools、MCP 和审批；
- Node/设备控制；
- Control UI、macOS/移动端客户端；
- OpenClaw 会话转录格式；
- OpenClaw Gateway 的完整 WebSocket RPC 协议；
- 插件安装、发现、版本管理和沙箱；
- OpenClaw 配置迁移和 CLI 管理命令。

这些模块与“把聊天软件消息交给外部 Agent，再把回复发回”没有必要依赖关系。

## 4. 为什么不是 Drop-in Replacement

本项目不能直接加载现有 OpenClaw Channel Plugin，原因包括：

1. 未实现 OpenClaw Plugin SDK 的全部类型和生命周期；
2. 未实现 OpenClaw Gateway 的控制协议；
3. 未复用 OpenClaw 内部配置模型和会话转录；
4. Adapter 的错误与回执接口针对独立 HTTP 网关重新设计；
5. 目标是让 LangGraph 等外部运行时通过公开 API 使用，而不是替换原 OpenClaw 进程中的内部对象。

迁移一个 OpenClaw 通道插件时，需要把它的平台层代码封装为本项目的 `receive()` 和 `send()`，而不是直接复制插件目录后注册。

## 5. 保留的关键原则

- 平台适配器拥有平台协议、身份验证和消息发送细节；
- 核心网关拥有统一事件、分发、队列和回执；
- 入站和出站是两条独立但可关联的链路；
- 通道层不负责 Agent 推理；
- 消息处理必须可恢复、可去重和可审计；
- 对不确定发送结果避免盲目重试。

## 6. 代码来源声明

该压缩包不是 OpenClaw 源码仓库的镜像，也未包含完整上游源码。本交付在无法直接把原 Gateway 目录独立编译的前提下，根据官方仓库结构、官方文档和公开协议重新实现最小独立边界。

上游版权和许可证信息见 `THIRD_PARTY_NOTICES.md` 和 `UPSTREAM_LICENSE_OPENCLAW`。
