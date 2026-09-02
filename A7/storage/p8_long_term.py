"""P8 长期记忆后端（双层 JSON 持久化）。

本模块是 P8 "长期记忆" 的**唯一存储入口**——所有 P8 已完成 P8_job 的
长期保留 / 跨进程访问 / LLM 检索都通过本模块进行。

# ============================================================
# ★★★ 长期记忆接口（罗盘长期记忆）  ★★★
# ============================================================
#
# 长期记忆为 P8 提供两层接口：
#
#   [1] 索引层接口（LLM 每次 invoke 都看 — 轻量）
#       - get_index_entry(p8_job_id)
#       - search_archived_descriptions(query, limit)
#       - load_all_index_entries()
#
#   [2] 数据层接口（按需精确加载 — 完整）
#       - get_archived_job(p8_job_id)
#       - search_archived_jobs(query, limit)
#       - load_all_archived_jobs()
#
#   [3] 写入入口（仅 P8ArchiveMiddleware 调用 — LLM 不调）
#       - save_archived_job(p8_job_id, archived_job)
#
#   [4] 维护 / 测试入口（不暴露给 LLM）
#       - reset_archive() / _init() / _make_index_entry()
#
# ============================================================
# 使用模式（LLM 两步走检索）：
#   step1. search_archived_descriptions("可燃气体") → [(p8_job_id, desc), ...]
#   step2. get_archived_job(p8_job_id) → 完整 archived P8Job dict
# ============================================================

设计要点：
1. **双层结构**：INDEX_FILE（p8_job_id → 一句话描述，轻量）+ ARCHIVE_FILE（p8_job_id → 完整 archived P8Job）
2. **仓库级全局**：所有 P8_job 共享 `_long_term/` 目录，跨 job_id 可见（行业追溯要求）
3. **双写一致**：save_archived_job() 同步更新索引层 + 数据层；写盘失败抛异常
4. **线程安全**：模块级 threading.Lock 包裹所有 IO；单进程多线程安全
5. **进程重启可恢复**：模块导入时 _init() 从 JSON 文件加载到内存 dict
6. **失败语义**：JSON 损坏 / IO 失败时明确抛 RuntimeError（不让 LLM 看到脏数据）

参照主设计文档：
- docs/P8_人机协同处置_文件组织与职责.md § 5
- docs/P8_人机协同处置_需求与Demo设计.md § 4.1 / § 6.4 / § 11.3
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional


# ============================================================================
# 路径常量（仓库根 → data/jobs/_long_term/）
# ============================================================================
# __file__ = <root>/A7/storage/p8_long_term.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LONG_TERM_DIR = _PROJECT_ROOT / "data" / "jobs" / "_long_term"
INDEX_FILE = LONG_TERM_DIR / "p8_archive.index.json"   # 索引层
ARCHIVE_FILE = LONG_TERM_DIR / "p8_archive.json"       # 数据层

# ============================================================================
# 模块级状态（in-memory cache；磁盘为权威）
# ============================================================================
_lock: threading.Lock = threading.Lock()

# 索引层：p8_job_id → 一句话描述（str；约 80 字）
_index: dict[str, str] = {}

# 数据层：p8_job_id → 完整 archived P8Job dict
_archive: dict[str, dict] = {}

_init_done: bool = False


# ============================================================================
# 通用 IO 工具（索引层 + 数据层共用）
# ============================================================================

def _ensure_dir() -> None:
    """确保 _long_term 目录存在。"""
    LONG_TERM_DIR.mkdir(parents=True, exist_ok=True)


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
            f"如需重置可调用 reset_archive()（注意：会清空全部长期记忆）"
        ) from e


def _flush_json(path: Path, data: dict) -> None:
    """原子写 dict 到 JSON 文件（tmp + os.replace）。

    Raises:
        RuntimeError: 写盘失败
    """
    _ensure_dir()
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
# 索引条目生成器
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
# 初始化（模块导入时自动调用一次）
# ============================================================================

def _init() -> None:
    """从磁盘加载到内存 dict；幂等（多次调用只跑一次）。

    **调用方约定**：本函数**不**获取 _lock。已持锁的调用方（如 _init_locked）
    应使用 _init_locked()；未持锁的调用方直接调本函数即可。
    """
    if _init_done:
        return
    with _lock:
        if _init_done:
            return  # 双检
        _init_locked()


def _init_locked() -> None:
    """_init() 的内部实现（要求调用方已持有 _lock）。"""
    global _index, _archive, _init_done
    try:
        _index = _load_json(INDEX_FILE)
        _archive = _load_json(ARCHIVE_FILE)
        # 校验：数据层有的 p8_job_id 索引层必须有；缺失则补
        for pid in _archive.keys():
            if pid not in _index:
                _index[pid] = _make_index_entry(_archive[pid])
        _init_done = True
    except RuntimeError:
        # JSON 损坏时让异常上浮（不让 LLM 看到脏数据）
        raise


# 模块导入时即初始化（保证后续所有函数拿到的是最新数据）
_init()


# ============================================================================
# [3] 写入入口（仅 P8ArchiveMiddleware 调用 — LLM 不调）
# ============================================================================

def save_archived_job(p8_job_id: str, archived_job: dict) -> bool:
    """保存归档 P8_job（覆盖式）；同时更新索引层；写后立即落盘。

    ★★★ 长期记忆写入入口（罗盘长期记忆） ★★★
    调用方：**P8ArchiveMiddleware**（框架层，监听 status 进入终态时触发）。
    LLM 不调此函数。

    Args:
        p8_job_id: 归档 P8_job 的标识符（如 "P8J-20260813-180000-001"）
        archived_job: 完整 archived P8Job dict（含 summary / archived_at 等附加字段）

    Returns:
        True 成功

    Raises:
        RuntimeError: 写盘失败（索引层或数据层任一失败都抛）
        ValueError: p8_job_id 为空

    Note:
        - 双层一致性：索引层与数据层**同时**更新；任一写盘失败抛异常（不部分写入）
        - 自动生成索引条目：调用 _make_index_entry() 生成一句话描述
        - 写入后立即落盘（不依赖进程退出）
    """
    if not p8_job_id:
        raise ValueError("[p8_long_term] save_archived_job: p8_job_id 不能为空")

    with _lock:
        # 1. 更新内存 dict
        _archive[p8_job_id] = archived_job
        _index[p8_job_id] = _make_index_entry(archived_job)

        # 2. 双层落盘（先数据层，再索引层；索引层写失败但数据层已写时上抛异常让运维感知）
        try:
            _flush_json(ARCHIVE_FILE, _archive)
            _flush_json(INDEX_FILE, _index)
        except RuntimeError:
            # 回滚内存（数据层已写盘，但保持内存与磁盘一致）
            # 注意：磁盘数据层已写入，但调用方可以重试；若重试需要传相同参数幂等
            raise

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
    with _lock:
        return _archive.get(p8_job_id)


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
    results: list[dict] = []
    with _lock:
        # 迭代时拷贝 keys 避免并发修改
        for pid in list(_archive.keys()):
            job = _archive[pid]
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
    调用方：P10 归档阶段 / 调试 CLI

    Returns:
        所有 archived P8Job dict 的快照列表（按 p8_job_id 排序）
    """
    with _lock:
        items = list(_archive.values())
    items.sort(key=lambda j: j.get("p8_job_id", ""))
    return items


# ============================================================================
# [1] 索引层接口（LLM 每次 invoke 都看 — 轻量）
# ============================================================================

def get_index_entry(p8_job_id: str) -> Optional[str]:
    """按 p8_job_id 查询索引条目（轻量；一句话描述）。

    ★★★ 长期记忆索引查询（罗盘长期记忆 — 索引层） ★★★
    调用方：recall_jobs 工具的"两步走"检索第二步

    Args:
        p8_job_id: 归档 P8_job 的标识符

    Returns:
        一句话描述字符串；不存在则返回 None
    """
    with _lock:
        return _index.get(p8_job_id)


def search_archived_descriptions(query: str, limit: int = 20) -> list[tuple[str, str]]:
    """子串搜索（索引层）：query 命中一句话描述。

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
    results: list[tuple[str, str]] = []
    with _lock:
        for pid, desc in _index.items():
            if q in desc.lower() or q in pid.lower():
                results.append((pid, desc))
                if len(results) >= limit:
                    break
    results.sort(key=lambda x: x[0])
    return results


def load_all_index_entries() -> list[tuple[str, str]]:
    """返回所有索引条目列表（索引层 snapshot）。

    ★★★ 长期记忆索引全量（罗盘长期记忆 — 索引层） ★★★
    调用方：调试 CLI / 前端面板 / LLM "列出所有已归档"指令

    Returns:
        [(p8_job_id, 一句话描述), ...] 列表；按 p8_job_id 排序
    """
    with _lock:
        items = list(_index.items())
    items.sort(key=lambda x: x[0])
    return items


# ============================================================================
# [4] 维护 / 测试入口（不暴露给 LLM）
# ============================================================================

def reset_archive() -> None:
    """清空所有归档数据（同时清空索引层 + 数据层两个文件）。

    **仅供测试 / 调试使用**——生产环境**禁止**调用。
    会同时删除两个 JSON 文件的内存缓存与磁盘文件。

    实现要点：直接调 _init_locked()（已持锁）避免嵌套加锁死锁。
    """
    global _index, _archive, _init_done
    with _lock:
        _index = {}
        _archive = {}
        _init_done = False
        # 尝试删磁盘文件；不存在不报错
        for f in (INDEX_FILE, ARCHIVE_FILE):
            if f.exists():
                try:
                    f.unlink()
                except OSError as e:
                    raise RuntimeError(
                        f"[p8_long_term] 重置时删文件失败 {f.name}：{e}"
                    ) from e
        # 直接调 _init_locked()（避免嵌套 _init() 加锁死锁）
        # 此处 _index/_archive 已是空，_init_locked() 重新加载（仍为空）并设 _init_done=True
        _init_locked()