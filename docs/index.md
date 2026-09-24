# 文档索引

> 工业互联网边缘智能 Agent 项目文档总览。

## 子项目文档

### P8P9 — 风险处置 + 审核

- [P8P9 模块总览](P8P9/P8P9.md) — 模块结构 / 7 状态 / 7 业务动作 / 3 service / HTTP 路由
- [P8 附件上传（方案 B · 独立上传服务）](P8P9/P8_UPLOAD.md) — 2026-09-20 新增；解决飞书 Card 2.0 无文件上传组件的限制

## 设计约定

- 所有 Web API 端点必须打印入口/出口/异常日志（CLAUDE.md）
- 不 import langchain 到 service 层（service 必须纯 Python）
- Agent 只通过白名单接口（`initialize_job_for_agent` / `bind_card_for_agent` / `get_state_for_agent`）访问 state
- 修改代码后必须 `python -m py_compile` + 关键 `from module import func` 验证

## 部署

- a/ 子项目 Docker 化：参见 `a/deploy/README.md`（2026-09-20 部署完成）
- 7 服务编排：nginx + gateway + p6 + p8p9 + app_config + feishu_cfg + webui