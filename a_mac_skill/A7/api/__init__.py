"""A7 REST API 控制器包。

承载 P8 Agent 的对外 REST 查询接口；调用方：
- `web/server.py` —— HTTP 端点（GET /api/jobs/{job_id}/working-memory）
- `A7/adapters/chat_reply.py` —— 飞书侧 chat_reply_handler 在 ReplyAfterLlm 阶段
  可调 get_working_memory_snapshot() 拼装工作记忆快照附在 LLM 回复下方
- 前端 Web 面板 —— 实时显示 in-progress P8_job

公开 API：
- `get_working_memory_snapshot(job_id)` —— 查询主流程 working_memory + 已归档最近 20 条

设计原则：
- 控制器**只读** checkpointer + 长期记忆后端；不调 LLM、不修改 state
- job_id 命名空间：主流程 `p8-{job_id}`（与 `run_disposition_agent` 同约定）
- thread_id 找不到 → working_memory = `[]`（不抛 404；与蓝图 § 8.1 一致）
- 长期记忆读取失败 → 抛 RuntimeError 由 web 路由转 500
"""
from A7.api.p8_working_memory_ctrl import get_working_memory_snapshot

__all__ = ["get_working_memory_snapshot"]
