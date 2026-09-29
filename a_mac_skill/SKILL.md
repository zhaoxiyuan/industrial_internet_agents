---
name: industrial-workflow
description: 工业现场 P6 监测、P7 风险记录查询、P8 工单处置及飞书双向对话。用于查询作业风险、启动或停止监测、处理作业票和回复值班群；依赖现场 a_mac 服务。
---

# 工业作业闭环

先读取平台提供的实例配置、工具可用状态、现场和执行身份。不可把包中声明视为运行成功。

- 监测与记录：阅读 [接口说明](references/interfaces.md)。平台通过 MCP 自动发现和调用已发布的工具；scripts/client.py 由 MCP 服务使用。开始监测会占用现场监测资源，成功受理后继续查状态。
- P8 处置：阅读 [P8 工具说明](references/p8-tools.md)。五个 P8 工具由 MCP 发布；update_job/hitl_decide 返回 LangGraph Command，必须在原运行时应用状态更新，不能当作普通 JSON 函数远程调用。list_active_p8_jobs 暂不注册。
- 飞书对话：阅读 [飞书流程](references/feishu.md)。绑定账号、会话和 event_id，回复成功后才 ACK；回复重试不得重复创建工单。
- 部署：阅读 [部署与限制](references/deployment.md)。保持 LangChain/LangGraph 框架，按实例分别配置 Agent、服务和模型位置。禁止导入时执行脚本或启动容器。

job_id 是业务关联，thread_id 是会话标识。网页、飞书群和定时运行分别隔离；定时任务每次新建 thread，只显式继承业务游标和必要摘要。

工具执行失败时返回真实错误；不得将占位响应、卡片受理或 HTTP 200 直接解释为作业完成。未就绪工具不能调用。保留工单、审批和审计记录。
