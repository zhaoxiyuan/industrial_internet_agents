# P8 工具契约

原 P8 模块包含八个工具：update_job、hitl_decide、read_p7_events、open_work_ticket、resend_current_card、lookup_feishu_directory、list_active_p8_jobs、recall_jobs。Skill 工具目录只登记当前发布的五个 P8 MCP 工具；其余三个仍留在原模块源码中。

MCP 输入 Schema 与发布的函数签名匹配。原 Agent 的 tool_call_id 和 chat_ctx 不通过 MCP 传递。update_job/hitl_decide 返回 Command，依赖状态机上下文。open_work_ticket 会创建作业票并发卡，resend_current_card 会重新投递卡片。

当前发布到 MCP 的是 read_p7_events、open_work_ticket、resend_current_card、lookup_feishu_directory、recall_jobs。它们复用原有 P8 业务函数；MCP 进程直接访问共享作业目录与飞书配置，并持有与 P8P9、Gateway 通信所需的业务环境。open_work_ticket 需要明确提供 job_id 和 chat_id 或 group_name；MCP 不继承原 Agent 的 chat_ctx。update_job/hitl_decide 返回 Command，留在原 LangGraph 状态上下文中，不发布 MCP。list_active_p8_jobs 仍为占位，暂缓注册。登记为 ready 表示源码接口可发现；实际可调用还须平台发现成功、共享数据及现场服务可用。
