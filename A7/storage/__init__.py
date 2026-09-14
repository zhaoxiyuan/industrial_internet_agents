"""A7.storage — P8 长期记忆后端（per-job JSON 持久化 — 2026-08-20 重构）。

子模块：
- p8_long_term : 长期记忆后端（per-job ``archived.json`` 持久化 + 跨 job 按需聚合读取）
- p8_working_memory_store : per-job working_memory JSON 持久化（解决 MemorySaver 进程内丢失）

设计要点（2026-08-20 重构）：
- **per-job 真相源**：所有 P8 状态（working_memory + 长期记忆）统一存 ``data/jobs/{job_id}/P8/``
- **无全局目录**：删除旧 ``data/jobs/_long_term/``；跨 job 视图通过 ``load_all_archived_jobs()``
  按需扫描所有 per-job 文件聚合（O(J)，J ≤ 1000）
- **无内存缓存**：每次读直接扫描磁盘；无模块级 lock / 无 init
- **索引条目按需构造**：``search_archived_descriptions`` 时实时调 ``_make_index_entry`` 计算
- **写入单 per-job**：`save_archived_job(pid, job, job_id)` 必填 job_id，写单个 ``archived.json``

长期记忆接口（罗盘长期记忆）一览：
    索引层（LLM 每次 invoke 看 — 按需构造）：
        - get_index_entry(p8_job_id) → 一句话描述
        - search_archived_descriptions(query, limit) → [(p8_job_id, desc), ...]
    数据层（按需精确加载）：
        - get_archived_job(p8_job_id) → 完整 archived P8Job dict
        - search_archived_jobs(query, limit) → [archived P8Job dict, ...]
        - load_all_archived_jobs() → [archived P8Job dict, ...]（跨 job 聚合）
    写入入口（仅 P8ArchiveMiddleware / CardActionAgent 调用）：
        - save_archived_job(p8_job_id, archived_job, job_id) → True（job_id 必填）
    维护 / 测试入口：
        - reset_archive(job_id=None)

working_memory per-job 持久化（2026-08-20 新增）：
    - load_working_memory(job_id) → 从 ``data/jobs/{job_id}/P8/working_memory.json`` 加载 list
    - dump_working_memory(job_id, list) → 原子写入 per-job JSON
    - flush_working_memory(job_id) → 从 MemorySaver 实时读取并 dump
"""
from .p8_long_term import (
    # [1] 索引层接口（罗盘长期记忆 - 索引层；按需构造）
    get_index_entry,
    search_archived_descriptions,
    # [2] 数据层接口（罗盘长期记忆 - 数据层）
    get_archived_job,
    search_archived_jobs,
    load_all_archived_jobs,
    # [3] 写入入口（罗盘长期记忆 - 写入；job_id 必填）
    save_archived_job,
    # [4] 维护 / 测试入口
    reset_archive,
)
from .p8_working_memory_store import (  # 2026-08-20 新增
    load_working_memory,
    dump_working_memory,
    flush_working_memory,
)

__all__ = [
    # [1] 索引层接口（罗盘长期记忆 - 索引层）
    "get_index_entry",
    "search_archived_descriptions",
    # [2] 数据层接口（罗盘长期记忆 - 数据层）
    "get_archived_job",
    "search_archived_jobs",
    "load_all_archived_jobs",
    # [3] 写入入口（罗盘长期记忆 - 写入）
    "save_archived_job",
    # [4] 维护 / 测试入口
    "reset_archive",
    # [5] 2026-08-20 新增：working_memory per-job 持久化
    "load_working_memory",
    "dump_working_memory",
    "flush_working_memory",
]