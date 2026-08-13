"""A7.storage — P8 长期记忆后端（双层 JSON 持久化）。

子模块：
- p8_long_term : 长期记忆的 9 个核心函数（双层 JSON + 线程安全 + 进程可恢复）

设计要点：
- **双层结构**：索引层（轻量；LLM 每次 invoke 看）+ 数据层（完整；按需加载）
- **仓库级**：所有 P8_job 共享 `_long_term/` 目录，跨 job_id 可见
- **双写一致**：save_archived_job() 同步更新两层；写盘失败抛异常
- **线程安全**：模块级 threading.Lock 包裹所有 IO
- **进程重启可恢复**：模块导入时自动 _init() 从 JSON 加载到内存

长期记忆接口（罗盘长期记忆）一览：
    索引层（LLM 每次 invoke 看）：
        - get_index_entry(p8_job_id) → 一句话描述
        - search_archived_descriptions(query, limit) → [(p8_job_id, desc), ...]
        - load_all_index_entries() → [(p8_job_id, desc), ...]
    数据层（按需精确加载）：
        - get_archived_job(p8_job_id) → 完整 archived P8Job dict
        - search_archived_jobs(query, limit) → [archived P8Job dict, ...]
        - load_all_archived_jobs() → [archived P8Job dict, ...]
    写入入口（仅 P8ArchiveMiddleware 调用）：
        - save_archived_job(p8_job_id, archived_job) → True
    维护 / 测试入口：
        - reset_archive()
"""
from .p8_long_term import (
    LONG_TERM_DIR,
    INDEX_FILE,
    ARCHIVE_FILE,
    # [1] 索引层接口（LLM 每次 invoke 看）
    get_index_entry,
    search_archived_descriptions,
    load_all_index_entries,
    # [2] 数据层接口（按需精确加载）
    get_archived_job,
    search_archived_jobs,
    load_all_archived_jobs,
    # [3] 写入入口（P8ArchiveMiddleware 调用）
    save_archived_job,
    # [4] 维护 / 测试入口
    reset_archive,
)

__all__ = [
    # 路径常量
    "LONG_TERM_DIR",
    "INDEX_FILE",
    "ARCHIVE_FILE",
    # [1] 索引层接口（罗盘长期记忆 - 索引层）
    "get_index_entry",
    "search_archived_descriptions",
    "load_all_index_entries",
    # [2] 数据层接口（罗盘长期记忆 - 数据层）
    "get_archived_job",
    "search_archived_jobs",
    "load_all_archived_jobs",
    # [3] 写入入口（罗盘长期记忆 - 写入）
    "save_archived_job",
    # [4] 维护 / 测试入口
    "reset_archive",
]