"""A7 中间件包。

承载 P8 Agent 的 LangGraph `AgentMiddleware` 实现，与 schema / storage 解耦。

公开 API：
- `P8ArchiveMiddleware`（p8_archive_middleware.py）—— 监 working_memory 终态 P8_job，
  自动 working → long_term；为 P8 唯一的归档写入入口。

设计原则：
- middleware 是**唯一**调 `A7.storage.save_archived_job` 的地方；LLM 工具不调。
- 失败时 logger.exception 但**不向上抛**（最终一致性；下一次 invoke 再尝试）。
- 返回 state patch（含 delete 哨兵 + long_term_memory 增量），由 LangGraph
  reducer 链合并。
"""
from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware

__all__ = ["P8ArchiveMiddleware"]
