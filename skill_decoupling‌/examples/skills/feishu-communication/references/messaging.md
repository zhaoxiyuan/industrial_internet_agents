# 飞书消息参数与回执

`lookup_feishu_directory` 只查询该连接配置的 FEISHU_GROUP_MAP 和 FEISHU_USER_MAP。
名称查询结果不代表已经发送，也不代表查到了整个飞书组织。

发送示例：

```json
{"target":{"type":"group","name":"动火作业群"},"text":"这是一条经授权的测试消息。"}
```

不要传入 App ID、account_id、Authorization 或 request_id；这些是平台内部配置。
`status=sent` 与 message_id 表示飞书 API 已受理；并不代表群成员已经阅读。
`status=unknown` 表示不能确认结果。不要盲目重发。

用 feishu_events 获取入站事件的 event_id，使用 feishu_reply 回复该事件。
确认处理前检查实际回复回执。日志和凭据文件不向模型开放。
