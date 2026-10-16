---
name: shared-risk-demo
description: 最小共享风险队列示例，P6保存描述，主Agent调度P7研判，失败须主Agent明确重试。
---

通过管理员授权的工具访问共享SQLite。P6只用save；P7用claim、complete、fail；主Agent用query、retry。
P6完成保存即返回，不调用、提交或唤醒P7。主Agent通过平台有方向的submit_task授权安排P7。
领取后使用真实claim凭证完成或失败；不要编造凭证。job_id、身份和目录由平台注入。
模型失败、超时由平台执行包装记录；failed不会被普通领取消费。业务成果与历史保留。
这是平台机制验收示例，不消费mock_player，不实现完整P6/P7业务。
