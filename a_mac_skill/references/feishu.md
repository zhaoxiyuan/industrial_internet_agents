# 飞书双向对话

使用 gateway 服务的 GET /v1/events 拉取事件，POST /v1/messages/reply 回复原 event_id，POST /v1/messages/send 主动发送，POST /v1/events/{event_id}/ack 确认消费。具体字段以 agents/channel_gateway_client.py 为准。

认证取实例的 gatewayTokenSecretRef，不把密钥写进 Skill。按账号、群/私聊、Agent 建立会话映射；事件去重键包含账号和 event_id。一个路由只设置一个主响应者。

external 模式默认已有 A7.adapters.chat_reply 消费者负责多轮对话；平台接管前先交接消费游标和消费者租约，不得两边同时响应。卡片按钮继续由 Gateway 转发至 P8P9 状态机。消息投递失败与业务失败分别记录，回复失败只重试回复。
