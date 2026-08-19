"""P8 归档中间件：监听 working_memory 中进入终态的 P8_job，自动 working → long_term。

设计要点（对应主设计文档 § 6.4 / § 4.1 / § 11.3）：

1. **自动副作用**：LLM 调 `update_job(status="completed" | "rejected" | "escalated" | "resumed")`
   后，框架自动触发 `after_model` 钩子 → 此 middleware → 归档 + 从 working_memory 移除。
   LLM **永远不调** `archive_job` 工具（计划 § 6.4.6 改造前后对比）。

2. **唯一的写入入口**：本 middleware 是 `A7.storage.save_archived_job` 的**唯一**调用方；
   `recall_jobs` 工具仅做读取。

3. **失败语义**：当 `save_archived_job` 抛 `RuntimeError` / `ValueError` 时，
   middleware 选择**不向上抛**（避免 LLM 整个 invoke 失败），仅 logger.exception；
   下一次 `after_model` 命中相同 job 时会再次尝试（**最终一致性而非原子**）。
   `p8_job_id` 仍保留在 working_memory 以便重试。

4. **state patch 语义**：
   - 钩子返回 `dict[str, Any]` 时被 LangGraph reducer 合并
   - 用 `__p8__delete__` 哨兵删除 working_memory 中已归档条目（reducer 接受）
   - `long_term_memory` 是 plain dict，LangGraph 默认 `merge` 语义（覆盖同 pid）

依赖：
- `langchain.agents.middleware.AgentMiddleware` —— LangGraph 1.x 钩子
- `A7.schema.p8_state._TERMINAL_STATUSES` —— 终态判定
- `A7.storage.p8_long_term.save_archived_job` —— 长期记忆写入
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware

from A7.schema.p8_state import _TERMINAL_STATUSES

logger = logging.getLogger("p8_archive_middleware")


def _summarize_p8_job(job: dict) -> str:
    """从 P8_job 字段自动汇总 summary。

    格式：``max_level=<X> | decision=<Y> | by=<Z>[ | aggregated N events]``

    与 A7/storage.p8_long_term._make_index_entry 的字段保持一致，但格式更紧凑，
    用于 archived dict 的 `summary` 字段（数据层详细内容）。
    """
    max_level = job.get("max_level") or job.get("level") or "?"
    decision = job.get("decision") or "N/A"
    note = job.get("note", "") or ""
    decider = "system"
    if "by " in note:
        decider = note.split("by ")[-1].strip()

    parts = [
        f"max_level={max_level}",
        f"decision={decision}",
        f"by={decider}",
    ]

    a6 = job.get("a6_event_ids")
    if isinstance(a6, list) and len(a6) > 1:
        parts.append(f"aggregated {len(a6)} events")

    # 注入风险依据（截断到 80 字）
    risk_basis = job.get("risk_basis", "") or ""
    if risk_basis:
        if len(risk_basis) > 80:
            risk_basis = risk_basis[:80] + "…"
        parts.append(f"basis={risk_basis}")

    return " | ".join(parts)


class P8ArchiveMiddleware(AgentMiddleware):
    """P8 归档中间件：监听 working_memory 终态 → 自动归档到长期记忆。

    触发时机：每次 LLM 响应后（after_model）。每次扫描 `working_memory`
    列表中所有 `status ∈ _TERMINAL_STATUSES` 的 P8_job，调
    `A7.storage.p8_long_term.save_archived_job()` 持久化，并通过
    `__p8__delete__` 哨兵从 working_memory 移除。

    Returns:
        - 若扫描到任意终态 P8_job → `{"working_memory": [sentinels], "long_term_memory": {pid: archived}}`
        - 若无终态 P8_job → `None`（不修改 state；零开销）

    Failure modes:
        - `save_archived_job` 抛异常 → logger.exception，不向上抛（保留 in working_memory 等待重试）
        - 无效字段（缺 p8_job_id / status）→ 跳过该 job（不影响其他 job 归档）
    """

    name: str = "P8ArchiveMiddleware"  # LangGraph 标识

    def after_model(
        self,
        state: dict[str, Any],
        runtime: Any,
    ) -> Optional[dict[str, Any]]:
        """after_model 钩子：扫描终态 P8_job 并归档。

        参数:
            state: LangGraph 状态 dict（key 含 `working_memory` / `long_term_memory`）
            runtime: LangGraph Runtime（未使用；保留签名）

        返回:
            state patch dict（被 reducer 合并）或 None（无变更）
        """
        working = state.get("working_memory") or []
        if not working:
            return None

        # 1. 收集需要归档的 P8_job
        to_archive: list[dict] = []
        delete_sentinels: list[dict] = []
        long_term_patch: dict[str, dict] = {}

        for job in working:
            if not isinstance(job, dict):
                continue
            status = job.get("status")
            pid = job.get("p8_job_id")
            if not isinstance(pid, str) or not pid:
                # 非法元素（无 p8_job_id）静默跳过
                continue
            if status not in _TERMINAL_STATUSES:
                continue

            # 终态 → 归档
            archived_job = dict(job)  # 浅拷贝避免污染原 dict
            archived_job["summary"] = _summarize_p8_job(job)
            archived_job["archived_at"] = datetime.now(timezone.utc).isoformat()
            # 标记 final_status 便于审计（与 p8_result.json.archived_jobs 字段对齐）
            archived_job["final_status"] = status

            to_archive.append(archived_job)
            # 同步写 sentinel；放在 try 块外确保即使存储失败也能删除（避免下次再扫到）
            delete_sentinels.append({"__p8__delete__": pid})
            long_term_patch[pid] = archived_job

        if not to_archive:
            return None

        # 2. 持久化（单 job 失败不影响其他 job）
        succeeded_pids: list[str] = []
        failed_pids: list[tuple[str, str]] = []
        for archived_job in to_archive:
            pid = archived_job["p8_job_id"]
            try:
                # ★★★ 长期记忆写入入口（罗盘长期记忆） ★★★
                from A7.storage.p8_long_term import save_archived_job
                save_archived_job(pid, archived_job)
                succeeded_pids.append(pid)
            except Exception as exc:
                logger.exception(
                    "P8ArchiveMiddleware: save_archived_job(%s) 失败: %s",
                    pid, exc,
                )
                failed_pids.append((pid, str(exc)[:200]))
                # 失败时从 sentinel 列表中移除（保留在 working_memory 等待重试）
                delete_sentinels = [
                    s for s in delete_sentinels if s.get("__p8__delete__") != pid
                ]
                long_term_patch.pop(pid, None)

        # 3. 即使全部失败也返回 patch（空 patch 等价于 None；仍返回以保持一致性）
        logger.info(
            "P8ArchiveMiddleware: scanned=%d, archived=%d, failed=%d",
            len(to_archive), len(succeeded_pids), len(failed_pids),
        )

        if not succeeded_pids:
            return None

        return {
            "working_memory": delete_sentinels,
            "long_term_memory": long_term_patch,
        }
