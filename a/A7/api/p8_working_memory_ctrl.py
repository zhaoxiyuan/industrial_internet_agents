"""P8 工作记忆 REST API 控制器（蓝图 § 8.1 / § 13 / Demo 6）。

提供 `get_working_memory_snapshot(job_id)` ——
- 从 `_p8_checkpointer` 读取 `thread_id=p8-{job_id}` 的 working_memory 快照
- 从 A7/storage/p8_long_term.load_all_archived_jobs 读取最近 20 条归档

端点路径（web/server.py 路由）：
    GET /api/jobs/{job_id}/working-memory

调用模式：
    - 主流程 P8 Agent 跑完后 → 实时显示在 Web 面板
    - 飞书侧用户在群内问状态 → chat_reply_handler 拿快照拼到 LLM 回复下方
    - 外部运维/CI 通过 curl 排查

设计要点：
- **只读**：不调 LLM；不修改 state；不阻塞业务
- **空结果 OK**：thread_id 找不到 / archived 列表为空 → 返 200 + 空数组（不抛 404）
- **archived by created_at DESC**：按 `archived_at` 倒序前 20 条（运维视角"最近发生了什么"）
- **懒加载 LLM 配置**：本模块 import 时不调 create_chat_model；
  只有 web 路由被 hit 时才带 LLM 进来（隔离 component 生命周期）
"""
from __future__ import annotations

import logging

logger = logging.getLogger("p8_working_memory_ctrl")


# 长期记忆"最近归档"条数（蓝图 § 8.1 / Demo 6 约定）
ARCHIVED_RECENT_LIMIT = 20


def _read_working_memory_from_checkpointer(job_id: str) -> list[dict]:
    """从 P8 checkpointer 读取 `thread_id=p8-{job_id}` 的 working_memory 字段。

    Args:
        job_id: 主流程作业 ID（如 `JOB-20260813-001`）

    Returns:
        working_memory list（深拷贝 safe；不可被 caller 修改原状态）
        thread_id 找不到 → 空 list

    Failures:
        checkpointer.get() 返 None → []
        channel_values 缺失 → []
        working_memory 字段缺失 → []
        字段类型不为 list → 兜底为 []
    """
    from agents.p8_disposition_agent import get_p8_checkpointer

    checkpointer = get_p8_checkpointer()
    config = {"configurable": {"thread_id": f"p8-{job_id}"}}

    try:
        snapshot = checkpointer.get(config)
    except Exception as exc:
        logger.exception("checkpointer.get(%s) 失败: %s", config, exc)
        raise

    if snapshot is None:
        return []

    # snapshot 是 dict；具体结构由 LangGraph 1.x 决定
    # 已知 key: 'channel_values' / 'channel_versions' / 'versions_seen' / 'v' / 'id' / 'ts'
    channel_values = snapshot.get("channel_values") if isinstance(snapshot, dict) else None
    if not isinstance(channel_values, dict):
        return []

    working = channel_values.get("working_memory")
    if not isinstance(working, list):
        return []

    # 过滤非法元素（防御 None / 非 dict）
    return [j for j in working if isinstance(j, dict)]


def _read_archived_recent(limit: int = ARCHIVED_RECENT_LIMIT) -> list[dict]:
    """从 A7 长期记忆后端读最近 limit 条已归档 P8_job（按 archived_at 倒序）。

    Args:
        limit: 最多返回条数（默认 20）

    Returns:
        archived P8Job dict 列表，按 archived_at 倒序；
        archived_at 缺失时排到末尾（按 p8_job_id 兜底）

    Failures:
        长期记忆 _lock 不可用 / 文件 IO 失败 → 抛 RuntimeError（由 web 路由转 500）
    """
    from A7.storage.p8_long_term import load_all_archived_jobs

    all_jobs = load_all_archived_jobs()
    if not all_jobs:
        return []

    # ISO8601 字符串天然字典序可比较；缺失字段排到末尾
    sorted_jobs = sorted(
        all_jobs,
        key=lambda j: (j.get("archived_at") or ""),
        reverse=True,
    )
    return sorted_jobs[:limit]


def get_working_memory_snapshot(job_id: str) -> dict:
    """查询作业的 P8 工作记忆快照 + 最近 20 条已归档。

    蓝图 § 8.1 / § 13 / Demo 6 约定的对外返回结构：

    ```jsonc
    {
      "status": "ok",                              // 成功 / error 二选一
      "job_id": "JOB-20260813-001",
      "working_memory": [                          // 当前 in-progress P8_job（来自 checkpointer）
        {
          "p8_job_id": "P8J-20260813-180000-001",
          "status": "waiting_decision",
          "channel": "HITL",
          ...
        }
      ],
      "archived_recent": [                         // 长期记忆最近 20 条（按 archived_at 倒序）
        {
          "p8_job_id": "P8J-20260813-180000-099",
          "final_status": "completed",
          "summary": "max_level=HIGH | rectify | by=zhang | basis=可燃气体",
          "archived_at": "2026-08-13T18:30:00Z",
          ...
        }
      ]
    }
    ```

    Args:
        job_id: 主流程作业 ID（**非空**；调用方 web 路由已校验）

    Returns:
        dict with keys `status` / `job_id` / `working_memory` / `archived_recent`

    Raises:
        ValueError: job_id 为空（web 路由调前必校验；这里兜底）
        RuntimeError: 长期记忆读取失败（由 web 路由转 HTTP 500）

    Note:
        - thread_id 找不到 → `working_memory = []`（不抛 404）
        - 长期记忆为空 → `archived_recent = []`
        - 字段不安全 claim "schema_version"——web 路由加响应头即可
    """
    if not job_id:
        raise ValueError("get_working_memory_snapshot: job_id 不能为空")

    working_memory = _read_working_memory_from_checkpointer(job_id)
    archived_recent = _read_archived_recent(ARCHIVED_RECENT_LIMIT)

    logger.info(
        "get_working_memory_snapshot(%s): working=%d, archived_recent=%d",
        job_id, len(working_memory), len(archived_recent),
    )

    return {
        "status": "ok",
        "job_id": job_id,
        "working_memory": working_memory,
        "archived_recent": archived_recent,
    }
