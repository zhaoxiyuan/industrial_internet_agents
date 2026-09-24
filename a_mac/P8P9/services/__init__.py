# P8P9/services/__init__.py — 3 个 service 编排
#
# 本包对外暴露 3 个 service（§2.0.1）：
#   - card_render: 卡片渲染 / 发送
#   - callback_router: 飞书 callback 路由
#   - audit_scheduler: P9 审核调度（mock）
#
# 严禁 import langchain。Service 是「业务 ↔ 飞书 ↔ 状态机」的桥，不掺 LLM。