"""P8 working_memory per-job JSON 持久化（2026-08-20 新增）。

解决 MemorySaver 进程内丢失问题：dump working_memory 列表到
``data/jobs/{job_id}/P8/working_memory.json``；启动时 lazy load。

设计要点：
- **per-job 文件互不干扰**：``data/jobs/{job_id}/P8/working_memory.json``
- **文件锁**（portalocker）：跨进程并发同 job 兜底（5s 超时）
- **失败语义**：load 失败 → 返空（不阻断 agent 启动）；dump 失败 → logger.warning
- **与 reducer 兼容**：dump 完整列表；load 时整体读出供 agent 注入

调用模式：
- ``create_disposition_agent(job_id=...)`` → 启动时调 ``load_working_memory``
- ``P8ArchiveMiddleware.after_model`` → 终态归档后调 ``dump_working_memory``
- ``run_disposition_agent`` invoke end → 调 ``flush_working_memory``
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("p8_working_memory_store")

try:
    import portalocker
    _HAS_PORTALOCKER = True
except ImportError:  # pragma: no cover - 跨平台 fallback
    _HAS_PORTALOCKER = False


def _get_working_memory_path(job_id: str) -> Path:
    """获取 per-job working_memory 文件路径：``data/jobs/{job_id}/P8/working_memory.json``。

    Raises:
        ValueError: job_id 为空或含非法字符（path traversal 防护）
    """
    if not job_id or not isinstance(job_id, str):
        raise ValueError("[p8_working_memory_store] job_id 必须为非空字符串")
    if not re.match(r"^[A-Za-z0-9_-]+$", job_id):
        raise ValueError(f"[p8_working_memory_store] job_id 非法: {job_id!r}")
    from agents.workflow.file_utils import get_job_dir
    return Path(get_job_dir(job_id)) / "P8" / "working_memory.json"


def _atomic_dump(path: Path, payload: list) -> None:
    """原子写：tmp + os.replace（与 p8_long_term._flush_json 同模式）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def load_working_memory(job_id: str) -> list[dict]:
    """从 per-job JSON 加载 working_memory 列表（启动时调）。

    Args:
        job_id: 主流程作业 ID

    Returns:
        working_memory list；文件不存在 / 解析失败 → 返空 list（**不抛**，
        避免阻断 agent 启动）

    Note:
        - 不持文件锁（启动时单进程加载，无需并发保护）
        - 损坏文件 → logger.warning + 返空（让 LLM 看到干净状态）
        - 顶层非 list / 元素非 dict → 静默过滤
    """
    try:
        path = _get_working_memory_path(job_id)
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        if not isinstance(data, list):
            logger.warning(
                "[p8_working_memory_store] %s 顶层不是 list（type=%s），返空",
                path, type(data).__name__,
            )
            return []
        # 过滤非 dict 元素（防御 None / 非法）
        return [j for j in data if isinstance(j, dict)]
    except Exception as exc:
        logger.exception(
            "[p8_working_memory_store] load_working_memory(%s) 失败: %s",
            job_id, exc,
        )
        return []


def dump_working_memory(job_id: str, working_memory: list[dict]) -> bool:
    """dump working_memory 列表到 per-job JSON（终态归档后 / 主流程结束调）。

    Args:
        job_id: 主流程作业 ID
        working_memory: 当前 working_memory 列表（list[dict]）

    Returns:
        True 成功；False 失败（logger.warning）

    Note:
        - 跨进程并发用 portalocker 文件锁兜底（同 job 多进程写）
        - 失败时不抛 RuntimeError（不影响主流程；middleware 已捕获异常）
        - 写入路径含原子 tmp + os.replace
    """
    try:
        path = _get_working_memory_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [j for j in (working_memory or []) if isinstance(j, dict)]

        if _HAS_PORTALOCKER:
            with portalocker.Lock(str(path) + ".lock", timeout=5):
                _atomic_dump(path, payload)
        else:
            # fallback 不加锁（仅单进程场景；记录 warning 提醒运维）
            logger.debug(
                "[p8_working_memory_store] portalocker 未安装，跳过文件锁（仅单进程安全）",
            )
            _atomic_dump(path, payload)

        logger.info(
            "[p8_working_memory_store] dump_working_memory(%s) → %d 条",
            job_id, len(payload),
        )
        return True
    except Exception as exc:
        logger.exception(
            "[p8_working_memory_store] dump_working_memory(%s) 失败: %s",
            job_id, exc,
        )
        return False


def flush_working_memory(job_id: str) -> bool:
    """从 MemorySaver 实时读取 working_memory 并 dump（execute_p8 主流程结束调）。

    Args:
        job_id: 主流程作业 ID

    Returns:
        True 成功；False 失败

    Note:
        - 读 ``_p8_checkpointer.get({"thread_id": f"p8-{job_id}"})`` 取最新
          ``channel_values.working_memory``
        - 与 :func:`dump_working_memory` 不同：本函数是**显式 flush**接口，
          agent 可控调用
    """
    try:
        from agents.p8_disposition_agent import get_p8_checkpointer
        checkpointer = get_p8_checkpointer()
        config = {"configurable": {"thread_id": f"p8-{job_id}"}}
        snapshot = checkpointer.get(config)
        if not snapshot:
            return dump_working_memory(job_id, [])
        channel_values = snapshot.get("channel_values") if isinstance(snapshot, dict) else None
        working = channel_values.get("working_memory") if isinstance(channel_values, dict) else None
        if not isinstance(working, list):
            working = []
        return dump_working_memory(job_id, working)
    except Exception as exc:
        logger.exception(
            "[p8_working_memory_store] flush_working_memory(%s) 失败: %s",
            job_id, exc,
        )
        return False


__all__ = ["load_working_memory", "dump_working_memory", "flush_working_memory"]