---
name: feishu-communication
description: 当用户需要给飞书群或用户发送文本、查询群目录、处理入站消息或回复飞书对话时使用。
metadata:
  version: "1.0.0"
---

# 飞书沟通

根据当前 Agent 获授权的飞书功能完成任务。这个 Skill 提供操作流程，不包含
飞书账号、密钥、MCP 地址或服务启动配置。

1. 阅读 [收发流程](references/messaging.md)，核对连接和目标。
2. 先查询配置的群目录；名称有歧义时请用户确认 ID。
3. 发送文本使用 target.type=group/user 与 name 或 id。连接账号由平台注入。
4. 只根据实际回执说明发送结果；结果未知时禁止重复发送。
5. 回复入站事件成功后再确认 processed；不需回复时明确 ignored。

当工具未绑定或服务不可用时，说明缺少的能力，不宣称已经执行。
