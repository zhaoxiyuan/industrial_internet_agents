# LangGraph 集成说明

## 1. 推荐映射

Gateway 事件：

```text
event.session.key -> LangGraph configurable.thread_id
event.sender.id   -> user_id 或运行上下文
event.message.text -> HumanMessage/content
event.id          -> 运行幂等和审计关联ID
```

`session.key` 是哈希后的稳定字符串，长度适合直接作为 LangGraph `thread_id`。

## 2. 方式一：Gateway 主动回调本地 LangGraph

配置：

```json
{
  "delivery": {
    "callbackUrl": "http://127.0.0.1:8000/channel-events",
    "callbackToken": "replace-token",
    "autoAck": true
  }
}
```

示例实现位于：

```text
examples/langgraph/app.py
```

核心逻辑：

```python
result = await graph.ainvoke(
    {"messages": [{"role": "user", "content": event["message"]["text"]}]},
    {"configurable": {"thread_id": event["session"]["key"]}},
)
```

回调响应：

```json
{
  "ack": true,
  "messages": [
    { "text": "LangGraph 最终回复" }
  ]
}
```

网关负责发送回复、记录回执和重试整个处理链。

### 事务边界

若 Agent 已产生外部副作用后才因网络问题使回调失败，网关会重试同一事件。因此 Agent 侧仍需使用 `event.id` 做幂等，尤其是：

- 写数据库；
- 发邮件；
- 控制设备；
- 创建工单；
- 调用具有副作用的工具。

## 3. 方式二：Agent 主动轮询 Gateway

流程：

```text
GET /v1/events?status=pending
-> graph.ainvoke(..., thread_id=session.key)
-> POST /v1/messages/reply
-> POST /v1/events/{id}/ack
```

这种方式使 Agent 掌握 ACK 时机，适合已有 Worker 体系。为防多个 Worker 重复领取，生产环境应扩展一个显式 claim/lease API，或把 Store 换成支持 `SELECT ... FOR UPDATE SKIP LOCKED` 的数据库实现。当前 REST 轮询接口只提供读取，不提供分布式租约。

## 4. 方式三：SSE 消费

建立：

```text
GET /v1/events/stream?after_sequence=<last>
```

收到 `inbound` 后执行 LangGraph，再发送回复和 ACK。断线后用最后成功持久化的序号重连。

SSE 客户端可能重复收到事件，因此仍须按 `event.id` 幂等。

## 5. 方式四：对接 LangGraph Agent Server

可在一个轻量 Bridge 中完成：

1. 以 `event.session.key` 查找或创建 Agent Server Thread；
2. 调用 thread run；
3. 等待最终输出或消费流；
4. 调用 Gateway `/v1/messages/reply`；
5. ACK Gateway 事件。

两类 thread ID 策略：

- **直接使用 `session.key`**：若 Agent Server 接受客户端指定 thread ID，映射最简单；
- **维护映射表**：`session.key -> agent_server_thread_id`，适合服务器生成 UUID 的部署。

不要把飞书 `chat_id`、微信用户 ID 等原始平台标识直接暴露为跨系统线程主键；Gateway 的哈希会话键更适合用于边界隔离。

## 6. Checkpointer

开发示例可使用 `InMemorySaver`，但进程重启后上下文会丢失。生产环境应使用持久化 Checkpointer，例如 PostgreSQL/SQLite 对应实现，并确保：

- Gateway 入站状态与 LangGraph checkpoint 分开存储；
- 两者用 `event.id`、`session.key` 和 LangGraph run ID 关联；
- 迁移或清理 checkpoint 时不删除 Gateway 尚未 ACK 的事件；
- 人工中断、审批和恢复继续复用同一 `thread_id`。

## 7. 多智能体路由

Gateway 不决定使用哪个 Agent。可在回调服务中按以下字段路由：

```text
event.channel
event.accountId
event.conversation.type
event.sender.id
event.message.mentions
event.metadata
```

示例：

```python
if event["channel"] == "feishu" and event["conversation"]["type"] == "group":
    graph = group_assistant_graph
else:
    graph = personal_assistant_graph
```

业务路由与平台协议分开后，替换 QQ、微信或飞书 Adapter 不需要修改 LangGraph 图。

## 8. 回复策略

Agent 回调响应可以返回零条或多条消息：

```json
{
  "ack": true,
  "messages": []
}
```

适用于只记录、不回复的事件。需要异步较长任务时，不建议长期占用回调连接；可以：

1. 快速 ACK 并返回一条“任务已受理”；
2. 后续通过 `/v1/messages/send` 主动发送最终结果；
3. 使用业务任务 ID 作为 Idempotency-Key。
