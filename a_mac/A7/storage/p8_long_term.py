"""P8 长期记忆后端（per-job JSON 持久化 — 2026-08-20 重构）。

本模块是 P8 "长期记忆" 的**唯一存储入口**——所有 P8 已完成 P8_job 的
长期保留 / 跨进程访问 / LLM 检索都通过本模块进行。

# ============================================================
# ★★★ 长期记忆接口（罗盘长期记忆）  ★★★
# ============================================================
#
# 长期记忆为 P8 提供统一接口：
#
#   [1] 索引层接口（LLM 每次 invoke 都看 — 轻量；按需构造）
#       - get_index_entry(p8_job_id)
#       - search_archived_descriptions(query, limit)
#
#   [2] 数据层接口（按需精确加载 — 完整）
#       - get_archived_job(p8_job_id)
#       - search_archived_jobs(query, limit)
#       - load_all_archived_jobs()
#
#   [3] 写入入口（仅 P8ArchiveMiddleware / CardActionAgent 调用 — LLM 不调）
#       - save_archived_job(p8_job_id, archived_job, job_id)
#
#   [4] 维护 / 测试入口（不暴露给 LLM）
#       - reset_archive(job_id=None)
#
# ============================================================
# 使用模式（LLM 两步走检索）：
#   step1. search_archived_descriptions("可燃气体") → [(p8_job_id, desc), ...]
#   step2. get_archived_job(p8_job_id) → 完整 archived P8Job dict
# ============================================================

设计要点（2026-08-20 重构）：
1. **per-job 真相源**：所有已归档 P8_job 存储在
   ``data/jobs/{job_id}/P8/archived.json``（dict 结构：p8_job_id → archived dict）
2. **无全局目录**：删除旧 ``data/jobs/_long_term/p8_archive{,.index}.json``；
   行业追溯需求由跨 job 扫描 ``load_all_archived_jobs()`` / ``search_*`` 满足
3. **无内存缓存**：每次读取直接扫描磁盘；无模块级 lock、无 init、无 _archive/_index
4. **索引条目按需构造**：``_make_index_entry()`` 在 ``search_archived_descriptions``
   时对每条 archived_job 实时计算（不再单独持久化 index 文件）
6. **失败语义**：JSON 损坏 / IO 失败时静默跳过该文件（聚合读容错；不抛异常）

参照主设计文档：
- docs/P8_人机协同处置_文件组织与职责.md § 5
- docs/P8_人机协同处置_需求与Demo设计.md § 4.1 / § 6.4 / § 11.3
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("p8_long_term")


# ============================================================================
# 路径常量（per-job 真相源）
# ============================================================================
# __file__ = <root>/A7/storage/p8_long_term.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_JOBS_ROOT = _PROJECT_ROOT / "data" / "jobs"


# ============================================================================
# per-job 归档路径（保留 path traversal 防护）
# ============================================================================
def _get_job_archive_path(job_id: str) -> Path:
    """获取 per-job 归档文件路径：data/jobs/{job_id}/P8/archived.json。

    Args:
        job_id: 主流程作业 ID

    Returns:
        Path 对象（**不**自动创建目录；调用方按需 mkdir）

    Raises:
        ValueError: job_id 为空或含非法字符（path traversal 防护）
    """
    if not job_id or not isinstance(job_id, str):
        raise ValueError("[p8_long_term] _get_job_archive_path: job_id 必须为非空字符串")
    # path traversal 防护：仅允许 [A-Za-z0-9_-]
    if not re.match(r"^[A-Za-z0-9_-]+$", job_id):
        raise ValueError(f"[p8_long_term] _get_job_archive_path: job_id 非法: {job_id!r}")
    from agents.workflow.file_utils import get_job_dir
    return Path(get_job_dir(job_id)) / "P8" / "archived.json"


# ============================================================================
# 私有 IO helper（参数化任意 path）
# ============================================================================

def _load_json(path: Path) -> dict:
    """从 JSON 文件加载 dict；文件不存在或为空则返回空 dict。

    Raises:
        RuntimeError: 文件存在但 JSON 损坏 / 不是 dict
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            raise RuntimeError(
                f"[p8_long_term] {path.name} 顶层不是 dict，实际 {type(data).__name__}"
            )
        return data
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"[p8_long_term] {path.name} JSON 损坏：{e}。"
            f"如需重置可调用 reset_archive(job_id=...)（注意：会清空该 job 全部长期记忆）"
        ) from e


def _flush_json(path: Path, data: dict) -> None:
    """原子写 dict 到 JSON 文件（tmp + os.replace）。

    Raises:
        RuntimeError: 写盘失败
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        # ensure_ascii=False 让中文描述 / 风险依据可读
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)  # 原子替换（同分区）
    except Exception as e:
        # 清理可能残留的 tmp 文件
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise RuntimeError(
            f"[p8_long_term] 写盘失败 {path.name}：{e}"
        ) from e


# ============================================================================
# 索引条目生成器（按需构造；不再单独持久化）
# ============================================================================

def _make_index_entry(archived_job: dict) -> str:
    """从完整 archived P8Job 自动生成一句话描述（索引条目）。

    格式：``[<max_level>] <risk_basis 前 30 字>；<decision> by <decider> @ <archived_at 截 YYYY-MM-DD HH:MM>``

    Args:
        archived_job: 数据层存储的完整 archived P8Job dict；
                      必含字段：max_level / risk_basis / decision / note / archived_at

    Returns:
        一句话描述字符串（不超过约 100 字）

    Examples:
        >>> _make_index_entry({
        ...     "max_level": "HIGH", "risk_basis": "可燃气体浓度超标（CH4 4.8%）",
        ...     "decision": "rectify", "note": "by operator:zhang",
        ...     "archived_at": "2026-08-13T18:30:00Z",
        ... })
        '[HIGH] 可燃气体浓度超标（CH4 4.8%）；rectify by operator:zhang @ 2026-08-13 18:30'
    """
    max_level = archived_job.get("max_level", "?")
    risk_basis = archived_job.get("risk_basis", "")
    # 前 30 字截断（中文按字符；超过加省略号）
    if len(risk_basis) > 30:
        risk_basis = risk_basis[:30] + "…"
    decision = archived_job.get("decision") or "N/A"

    # 决策者提取：note 通常为 "by operator:zhang" 形式
    note = archived_job.get("note", "")
    decider = "system"
    if "by " in note:
        decider = note.split("by ")[-1].strip()

    # archived_at 截到分钟
    archived_at = archived_job.get("archived_at", "")
    if len(archived_at) >= 16:
        archived_at = archived_at[:16].replace("T", " ")

    return f"[{max_level}] {risk_basis}；{decision} by {decider} @ {archived_at}"


# ============================================================================
# 扫描工具（按需聚合）
# ============================================================================

def _iter_archive_files() -> Iterator[Path]:
    """yield 所有 data/jobs/*/P8/archived.json。

    跳过 _ 开头目录（兼容 _long_term/_legacy 等历史/元数据目录）。
    """
    if not _JOBS_ROOT.exists():
        return
    for p in _JOBS_ROOT.glob("*/P8/archived.json"):
        # 跳过 _ 开头目录（如 _long_term, _legacy）
        if p.parent.parent.name.startswith("_"):
            continue
        yield p


def _load_all() -> dict[str, dict]:
    """扫描所有 per-job archived.json；合并为 {p8_job_id: archived_job}。

    容错语义：单文件 JSON 损坏 / IO 失败 → 跳过该文件 + logger.warning，
    聚合读不抛异常（避免脏文件阻断 LLM 调用）。
    """
    out: dict[str, dict] = {}
    for path in _iter_archive_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[p8_long_term] 跳过损坏文件 %s: %s", path, exc)
            continue
        if not isinstance(data, dict):
            logger.warning(
                "[p8_long_term] %s 顶层非 dict（type=%s），跳过",
                path, type(data).__name__,
            )
            continue
        out.update(data)
    return out


# ============================================================================
# [3] 写入入口（仅 P8ArchiveMiddleware / CardActionAgent 调用 — LLM 不调）
# ============================================================================

def save_archived_job(
    p8_job_id: str,
    archived_job: dict,
    job_id: str,                    # 2026-08-20 重构：改为必填
) -> bool:
    """保存归档 P8_job 到 per-job 文件。

    ★★★ 长期记忆写入入口（罗盘长期记忆） ★★★
    调用方：**P8ArchiveMiddleware** / **CardActionAgent**（监听 status 进入终态时触发）。
    LLM 不调此函数。

    Args:
        p8_job_id: 归档 P8_job 的标识符（如 "P8J-20260813-180000-001"）
        archived_job: 完整 archived P8Job dict（含 summary / archived_at 等附加字段）
        job_id: **必填**——主流程作业 ID；写入 data/jobs/{job_id}/P8/archived.json

    Returns:
        True 成功

    Raises:
        RuntimeError: 写盘失败
        ValueError: p8_job_id 或 job_id 为空

    Note:
        - **2026-08-20 重构**：job_id 改为必填（删除 _long_term/ 双写）。
          调用方必须传入 job_id，否则抛 ValueError（避免无主归档）。
        - 写入后立即落盘（不依赖进程退出）
        - 单文件 upsert：加载现有 per-job archived.json dict，p8_job_id 键覆盖写入
    """
    if not p8_job_id:
        raise ValueError("[p8_long_term] save_archived_job: p8_job_id 不能为空")
    if not job_id:
        raise ValueError("[p8_long_term] save_archived_job: job_id 不能为空（per-job 化后必填）")

    # 注入 job_id 到 archived_job（确保 archived dict 自带归属）
    if "job_id" not in archived_job:
        archived_job = {**archived_job, "job_id": job_id}

    path = _get_job_archive_path(job_id)
    existing = _load_json(path) if path.exists() else {}
    existing[p8_job_id] = archived_job
    _flush_json(path, existing)

    logger.info(
        "[p8_long_term] per-job 写入: job_id=%s pid=%s path=%s",
        job_id, p8_job_id, path,
    )
    return True


# ============================================================================
# [2] 数据层接口（按需精确加载 — 完整 archived P8Job）
# ============================================================================

def get_archived_job(p8_job_id: str) -> Optional[dict]:
    """按 p8_job_id 查询已归档 P8_job（数据层；按需精确加载）。

    ★★★ 长期记忆精确查询（罗盘长期记忆） ★★★
    调用方：recall_jobs 工具（LLM 看到索引条目后再来取详情）

    Args:
        p8_job_id: 归档 P8_job 的标识符

    Returns:
        完整 archived P8Job dict；不存在则返回 None
    """
    return _load_all().get(p8_job_id)


def search_archived_jobs(query: str, limit: int = 20) -> list[dict]:
    """子串搜索（数据层）：query 命中 risk_basis / note / a6_event_ids 任一字段。

    ★★★ 长期记忆全文搜索（罗盘长期记忆 — 数据层） ★★★
    调用方：recall_jobs 工具 / 调试 CLI

    Args:
        query: 关键词（如 "可燃气体" / "P8J-20260813-180000-001"）
        limit: 最多返回多少条（默认 20）

    Returns:
        命中的完整 archived P8Job dict 列表

    Note:
        如需节省 token，请用 search_archived_descriptions()（仅返回索引）。
        该函数返回完整 dict，单条可能 1-2K tokens。
    """
    if not query:
        return []
    q = query.lower()
    archive = _load_all()
    results: list[dict] = []
    for pid, job in archive.items():
        haystack_parts = [
            job.get("risk_basis", ""),
            job.get("note", ""),
            pid,
        ]
        # a6_event_ids 是列表，需要 join
        a6 = job.get("a6_event_ids", [])
        if isinstance(a6, list):
            haystack_parts.extend(a6)
        haystack = "\n".join(str(p) for p in haystack_parts).lower()
        if q in haystack:
            results.append(job)
            if len(results) >= limit:
                break
    return results


def load_all_archived_jobs() -> list[dict]:
    """返回所有已归档 P8_job 列表（数据层 snapshot）。

    ★★★ 长期记忆全量加载（罗盘长期记忆 — 数据层） ★★★
    调用方：A7/api/p8_working_memory_ctrl._read_archived_recent（web 路由）

    Returns:
        所有 archived P8Job dict 的快照列表（按 p8_job_id 排序）

    Note:
        2026-08-20 重构：跨 job 扫描所有 data/jobs/*/P8/archived.json，
        O(J) 次文件读；J ≤ 1000 时 < 50ms。
    """
    items = list(_load_all().values())
    items.sort(key=lambda j: j.get("p8_job_id", ""))
    return items


# ============================================================================
# [1] 索引层接口（LLM 每次 invoke 都看 — 轻量；按需构造）
# ============================================================================

def get_index_entry(p8_job_id: str) -> Optional[str]:
    """按 p8_job_id 查询索引条目（轻量；一句话描述）。

    ★★★ 长期记忆索引查询（罗盘长期记忆 — 索引层） ★★★
    调用方：调试 CLI / 前端面板

    Args:
        p8_job_id: 归档 P8_job 的标识符

    Returns:
        一句话描述字符串；不存在则返回 None
    """
    job = _load_all().get(p8_job_id)
    return _make_index_entry(job) if job else None


def search_archived_descriptions(query: str, limit: int = 20) -> list[tuple[str, str]]:
    """子串搜索（索引层）：query 命中一句话描述或 p8_job_id。

    ★★★ 长期记忆索引搜索（罗盘长期记忆 — 索引层） ★★★
    调用方：recall_jobs 工具的"两步走"检索第一步（LLM 看到结果后选 p8_job_id 再 get_archived_job）

    Args:
        query: 关键词（如 "可燃气体" / "HIGH"）
        limit: 最多返回多少条（默认 20）

    Returns:
        [(p8_job_id, 一句话描述), ...] 列表；按 p8_job_id 排序
    """
    if not query:
        return []
    q = query.lower()
    archive = _load_all()
    results: list[tuple[str, str]] = []
    for pid, job in archive.items():
        desc = _make_index_entry(job)
        if q in desc.lower() or q in pid.lower():
            results.append((pid, desc))
            if len(results) >= limit:
                break
    results.sort(key=lambda x: x[0])
    return results


# ============================================================================
# [4] 维护 / 测试入口（不暴露给 LLM）
# ============================================================================

def reset_archive(job_id: Optional[str] = None) -> None:
    """清空 per-job 归档数据。

    **仅供测试 / 调试使用**——生产环境**禁止**调用。

    Args:
        job_id: 给定 → 仅删 ``data/jobs/{job_id}/P8/archived.json``；
                None → 遍历 ``data/jobs/*/P8/archived.json`` 全删（跳过 _ 开头目录）

    Note:
        2026-08-20 重构：不再有全局 _long_term/ 文件可清；清空范围限定为 per-job archived.json。
        测试隔离：setUpClass 调用 ``reset_archive()`` 清盘，配合 backup/restore 保证不污染其他测试。
    """
    if job_id:
        path = _get_job_archive_path(job_id)
        if path.exists():
            try:
                path.unlink()
                logger.info("[p8_long_term] reset_archive: 删 %s", path)
            except OSError as e:
                raise RuntimeError(
                    f"[p8_long_term] reset_archive 删 {path} 失败：{e}"
                ) from e
        return

    # job_id=None → 清所有 per-job archived.json
    for path in _iter_archive_files():
        try:
            path.unlink()
            logger.info("[p8_long_term] reset_archive: 删 %s", path)
        except OSError as e:
            raise RuntimeError(
                f"[p8_long_term] reset_archive 删 {path} 失败：{e}"
            ) from e


__all__ = [
    # [3] 写入入口
    "save_archived_job",
    # [2] 数据层接口
    "get_archived_job",
    "search_archived_jobs",
    "load_all_archived_jobs",
    # [1] 索引层接口
    "get_index_entry",
    "search_archived_descriptions",
    # [4] 维护 / 测试入口
    "reset_archive",
]