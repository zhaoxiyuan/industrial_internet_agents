"""
a/web/ — a/ 子项目的精简 Web 子包

【职责范围 - 仅 P6-P9 业务】
这是从原项目 web/server.py 拆分出来的**端口转发 + P8P9 业务辅助 Web 服务**,
不依赖 P1-P5 主流程。

【保留的 endpoint】
1. POST /api/feishu/card-callback
   飞书卡片按钮回调入口。飞书 → Node Gateway → 转发到这里 → 转发到 P8P9
   web_server (http://127.0.0.1:8089/feishu/card/callback)。
   这是飞书交互闭环的关键 — 没有这个 endpoint,用户在卡片上点的按钮
   不会到达 P8P9 状态机。

2. GET /api/jobs/{job_id}/working-memory
   P8 工作记忆快照查询(P8P9 WebSocket 推送的累积事件)。

3. GET /api/feishu/card-callbacks
   已注册的飞书卡片 ID 索引(用于排查卡片回调)。

【剔出的 endpoint(原 web/server.py 有,a/ 不需要)】
- /api/config*       配置管理 — 改用 frontend/app_config.py
- /api/prompt/{stage}  prompt 在线编辑 — 跟 a/ 的 P6-P9 业务无关
- /api/workflow/*    主流程编排 — 强依赖 agents.main_agent(P1-P10)
- /api/test/llm      LLM 连接测试 — 改用 frontend/app_config.py
- /api/workflow/parse-docx  作业票 docx 解析 — 主流程功能

【未来扩展】
如需添加 P6-P9 相关的新辅助 endpoint(例如 P6 事件查询、P7 风险历史、
P9 审核历史等),应在此文件中追加,并在 docstring 中说明用途。

【静态资源】
GET /                       极简首页(说明 a/ 是 P6-P9 子项目,链接到 P6/P8P9
                            各自的"自带"前端:p6_monitor_agent.py :5002,
                            P8P9/web_server.py :8089)
GET /p8_detail.html         复制自原 web/ 目录,与 P8P9 业务强相关

【端口】
8080(同根目录 webui 端口,但只暴露 P6-P9 相关 endpoint)
"""
