"""P8: 人机协同处置（v2 混合版 = 蓝图版工具集 + P8P9 状态机卡片管线）

按蓝图 § 6.1 / § 7 / § 11.3 重写：
- 7 个工具，全部基于 LangGraph state reducer + P8ArchiveMiddleware + P8P9 状态机：
    * update_job           — 写 P8 working_memory（旧工具，保留）
    * hitl_decide          — 进入 HITL 决策（旧工具，保留）
    * read_p7_events       — 读 P7 风险研判输出（旧工具，保留）
    * open_work_ticket     — ★ 开启 P8P9 作业票（创建 job + 发飞书 Card 2.0 卡片）
    * resend_current_card  — ★ 重发 P8P9 job 卡片（防网络波动导致卡片死掉）
    * list_active_p8_jobs  — 读 working_memory 列出 in-progress P8_job（旧工具，保留）
    * recall_jobs          — 长期记忆查询（旧工具，保留）
- 2026-09-17 重构：notify_feishu → open_work_ticket + resend_current_card
    旧版 notify_feishu 调 feishu_gateway_cli 直推卡片（写 P8_job 表）
    v2 走 P8P9 状态机新管线（state_machine + cards + business_actions），
    状态写入 data/jobs/{17位 ERP job_id}/closure_state.json（与主流程 p5/p7_result.json 同目录），
    卡片由 card_render 渲染。
- state_schema=P8State（含 working_memory / long_term_memory）
- checkpointer 单例（_p8_checkpointer）供 A7/api/p8_working_memory_ctrl 读取
- 中间件：HumanInTheLoopMiddleware（开卡 confirm；重发放行）+ P8ArchiveMiddleware
- 长期记忆（罗盘长期记忆）：recall_jobs 通过 A7.storage 索引/数据层接口
- 飞书卡片：open_work_ticket / resend_current_card 通过 P8P9/services/card_render 真实联通

向后兼容：
- disposition_demo(message, history=None) -> str 签名一字不动
  （chat_reply.py L330 / Gradio ChatInterface 依赖）
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.tools import tool, InjectedToolCallId, InjectedToolArg
from langchain_core.messages import HumanMessage, ToolMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .model.chat_model import create_chat_model_with_logging, get_llm_params
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.logging_handler import get_agent_config
from .utils.system_prompt import load_system_prompt

# ★★★ 长期记忆接入（罗盘长期记忆） ★★★
# 2026-08-20 重构：删除全局 _long_term/ 目录，所有 P8 状态 per-job 持久化；
# load_all_index_entries / INDEX_FILE / ARCHIVE_FILE 已废弃，不再导出。
from A7.storage import (
    search_archived_descriptions,   # 索引层：子串搜索（LLM "两步走" 第一步；按需扫描 per-job）
    get_archived_job,              # 数据层：精确查询（LLM "两步走" 第二步；按需扫描 per-job）
)
from A7.schema import (
    P8Job, P8JobUpdate, P8JobStatus, RiskLevel, Channel, ActionType, P8State,
    PushMessage,
)
from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware

logger = logging.getLogger("a7.p8_disposition_agent")


# ============================================================
# Agent 层级 Checkpointer - 全局单例
# ============================================================
# 蓝图 § 7.7：thread_id 约定 f"p8-{job_id}"，跨 invoke 共享同一 checkpointer
# （chat_reply 多次 invoke / 主流程单次 invoke / Ctrl 端点读取共享同一 namespace）
_p8_checkpointer = MemorySaver()


def get_p8_checkpointer() -> MemorySaver:
    """导出 P8 checkpointer 单例（A7/api/p8_working_memory_ctrl 调用）。

    Returns:
        :class:`MemorySaver` 实例，跨进程内全局单例
    """
    return _p8_checkpointer


# ============================================================
# user_ctx 注入（2026-08-19）
# ============================================================
# chat_reply_handler 从飞书 event 抽取 sender.open_id → 反查 .env FEISHU_USER_MAP
# → 构造 dict（role + name + open_id）→ 传 disposition_demo(..., user_ctx=...)
# → create_disposition_agent(user_ctx=...) 把"当前用户"段拼到 system_prompt 末尾。
#
# 设计要点：
# - **始终传 dict**（未识别时含 "未识别用户"/"未知" 占位 + note）→ 杜绝 LLM 幻觉身份
# - **按 user_ctx cache agent 实例**：相同身份复用同一 compiled graph（性能无损）
# - **cache key 用 JSON 序列化**：dict 不可哈希，统一走 json.dumps(sort_keys=True)
# - **不入仓 user_ctx 内容本身**（隐私）：cache 只存 agent 实例，不存原 dict

_USER_CTX_BLOCK_TEMPLATE: str = """

---

## 当前用户身份（由 chat_reply_handler 注入）

- **姓名**：{name}
- **角色**：{role}
- **open_id**：`{open_id}`

请直接用**姓名 + 角色**称呼用户（如"李宗睿 - 作业负责人"），便于对话得体。

> ⚠️ 若 `name="未知"` 且 `role="未识别用户"`，说明该用户的 open_id 不在 .env
> FEISHU_USER_MAP 中，**严禁推测其姓名/角色**，仅用"您"或"该用户"指代。
"""


def _format_user_context_block(user_ctx: Optional[Dict[str, str]]) -> str:
    """把 user_ctx dict 格式化为 system_prompt 末尾追加的"当前用户"段。

    Args:
        user_ctx: chat_reply_handler 构造的 dict，含 role/name/open_id 字段
                  （未识别时含 note 字段说明原因）。

    Returns:
        Markdown 格式的"当前用户身份"段（含 --- 分隔符）。
        user_ctx 为 None 时返回空串（Gradio / 离线调用场景）。
    """
    if not user_ctx:
        return ""
    return _USER_CTX_BLOCK_TEMPLATE.format(
        name=user_ctx.get("name", "未知"),
        role=user_ctx.get("role", "未识别用户"),
        open_id=user_ctx.get("open_id", "") or "(缺失)",
    )


# ============================================================
# job_id 注入 + bootstrap（2026-08-20 新增）
# ============================================================
# 设计要点：
# - **job_id → system_prompt 段**：让 LLM 明确知道当前作业归属，避免跨 job 串台
# - **cache key 含 job_id**：同 user_ctx 不同 job 必须各自独立 agent 实例
#   （否则 working_memory / 长期归档按 thread_id 隔离但 system_prompt 串台）
# - **bootstrap：仅 log 加载状态，不强制 put 到 MemorySaver**
#   （MemorySaver 进程内特性，跨重启"伪恢复"不可靠；强制 put 风险大于收益）
# - **invoke end flush**：调用方（run_disposition_agent）主动 dump，
#   保证下一次启动能读到最新 working_memory

_JOB_ID_BLOCK_TEMPLATE: str = """


---

## 当前主流程作业（2026-08-20 新增）

- **作业 ID**：`{job_id}`

P8 工作记忆 / 长期归档均归属此 job；按此作业清理 per-job 文件时定位用。
"""


# ============================================================
# chat_ctx 注入（2026-09-17 新增 — 自动补齐 chat_id / group_name）
# ============================================================
# 设计要点：
# - chat_reply.py 已知当前消息来源群 chat_id（从飞书 event 抽出），并按 chat_type 判定 group/dm。
# - chat_id → 从 .env FEISHU_GROUP_MAP 反查 group_name（动火作业群 / 巡检群 等）
# - 注入到 system_prompt：LLM 已知当前群，调 open_work_ticket 时可直接省略 chat_id（工具兜底再补）
# - open_work_ticket / resend_current_card：若 LLM 没传 chat_id/group_name，从 chat_ctx 兜底填入
# - 仅 chat_reply.py 注入；Gradio / execute_p8 / CLI 不传 → chat_ctx=None → 老逻辑
# ============================================================

def _lookup_group_name(chat_id: str) -> Optional[str]:
    """从 .env FEISHU_GROUP_MAP 反查群名（动火作业群 / 巡检群 等）。

    Args:
        chat_id: 飞书群 ID（oc_xxx）。

    Returns:
        群名（含 description）；未匹配返回 None（不强行编造）。
    """
    if not chat_id:
        return None
    raw = os.environ.get("FEISHU_GROUP_MAP", "").strip()
    if not raw:
        return None
    try:
        group_map = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("FEISHU_GROUP_MAP JSON 解析失败，跳过反查")
        return None
    info = group_map.get(chat_id)
    if not isinstance(info, dict):
        return None
    name = (info.get("name") or "").strip()
    if not name:
        return None
    desc = (info.get("description") or "").strip()
    return f"{name}（{desc}）" if desc else name


_CHAT_CTX_BLOCK_TEMPLATE: str = """


---

## 当前对话来源群（2026-09-17 新增 — 由 chat_reply_handler 自动注入）

- **chat_id**：`{chat_id}`
- **chat_type**：`{chat_type}`
- **群名**：`{group_name}`（按 FEISHU_GROUP_MAP 反查；未匹配时显示 "<未匹配>"）

> ★ 调 `open_work_ticket` / `resend_current_card` 时，**chat_id / group_name 可省略**——
> 工具内部会从 chat_ctx 兜底自动填入当前群，避免每次反问用户。
> ★ 仅当前会话使用；换群后 P8 agent 会自动用新群的 chat_id 注入。"""


def _format_chat_context_block(chat_ctx: Optional[Dict[str, str]]) -> str:
    """把 chat_ctx dict 格式化为 system_prompt 末尾追加的"当前对话来源群"段。

    Args:
        chat_ctx: chat_reply_handler 构造的 dict，含 chat_id / chat_type 字段；
                  group_name 会按 FEISHU_GROUP_MAP 自动反查。

    Returns:
        Markdown 格式的"当前对话来源群"段。chat_ctx 为 None / 缺 chat_id 时返回空串。
    """
    if not chat_ctx:
        return ""
    chat_id = (chat_ctx.get("chat_id") or "").strip()
    if not chat_id:
        return ""
    chat_type = (chat_ctx.get("chat_type") or "group").strip() or "group"
    group_name = _lookup_group_name(chat_id) or "<未匹配>"
    return _CHAT_CTX_BLOCK_TEMPLATE.format(
        chat_id=chat_id,
        chat_type=chat_type,
        group_name=group_name,
    )


def _format_job_id_block(job_id: Optional[str]) -> str:
    """job_id → system_prompt 末尾追加的"当前作业"段；None/空 → 返回空串。

    Args:
        job_id: 主流程作业 ID；None / 空 → 返回空串（Bot 模式或无作业上下文）

    Returns:
        Markdown 格式的"当前作业"段；None/空 → 空串
    """
    if not job_id:
        return ""
    return _JOB_ID_BLOCK_TEMPLATE.format(job_id=job_id)


def _bootstrap_working_memory_into_checkpointer(job_id: str) -> None:
    """启动时从 per-job JSON 加载 working_memory（伪恢复，仅 log）。

    Note:
        - 不强制 put 到 MemorySaver（避免污染 reducer 状态）
        - MemorySaver 进程内特性决定跨进程"伪恢复"不可靠
        - 加载失败不阻断 agent 创建（仅 logger.warning）
        - 完整持久化建议升级 SqliteSaver（参见方案 C 决策 D1）
    """
    try:
        from A7.storage.p8_working_memory_store import load_working_memory
        working = load_working_memory(job_id)
        if not working:
            logger.info("P8 bootstrap: job_id=%s per-job 文件为空/不存在", job_id)
            return
        config = {"configurable": {"thread_id": f"p8-{job_id}"}}
        existing_snapshot = _p8_checkpointer.get(config)
        existing_working = (
            existing_snapshot.get("channel_values", {}).get("working_memory", [])
            if existing_snapshot else []
        )
        if existing_working:
            logger.info(
                "P8 bootstrap: job_id=%s 已有 %d 条 working_memory，跳过加载",
                job_id, len(existing_working),
            )
            return
        # 仅 log（不强制 put；MemorySaver 进程内单例，跨重启不持久）
        logger.info(
            "P8 bootstrap: job_id=%s 从 per-job JSON 读到 %d 条 working_memory"
            "（MemorySaver 跨重启需 SqliteSaver；当前仅在 invoke end flush 持久化）",
            job_id, len(working),
        )
    except Exception as exc:
        logger.warning(
            "P8 bootstrap: job_id=%s 加载失败（不阻断 agent 创建）: %s",
            job_id, exc,
        )


def _user_ctx_cache_key(
    user_ctx: Optional[Dict[str, str]],
    variant: str,
    job_id: Optional[str] = None,        # 2026-08-20 新增
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增
) -> str:
    """user_ctx + variant + job_id + chat_ctx → cache key 字符串。

    Args:
        user_ctx: 身份 dict；None 表示"无身份"模式。
        variant:  agent 变体标识（"basic" / "hitl"）。
        job_id:   2026-08-20 新增。主流程作业 ID；None → Bot 模式占位。
        chat_ctx: 2026-09-17 新增。对话来源群上下文（含 chat_id/chat_type）；
                  同 user_ctx 不同 chat_id 必须返回不同 key（防止跨群串台）。

    Returns:
        cache key 字符串（json.dumps 保证 dict 稳定哈希；None 用固定 sentinel）。
        同 user_ctx 不同 job_id / chat_id 必须返回不同 key。
    """
    if user_ctx is None:
        ctx_part = "<none>"
    else:
        try:
            ctx_part = json.dumps(user_ctx, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            ctx_part = repr(sorted(user_ctx.items()))
    if chat_ctx is None:
        chat_part = "<none>"
    else:
        try:
            chat_part = json.dumps(chat_ctx, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            chat_part = repr(sorted(chat_ctx.items()))
    return f"{variant}:{ctx_part}:job={job_id or '<none>'}:chat={chat_part}"


# Agent 缓存：按 (variant, user_ctx) 复用 compiled graph 实例。
# 同身份同变体的多次 invoke 走同一 agent → 零重建开销。
# 不同身份 → 各自独立 agent → system_prompt 隔离（防止身份串台）。
_AGENT_CACHE: Dict[str, Any] = {}


def _clear_agent_cache() -> int:
    """清空 agent 缓存（测试 / 调试用；返回被清空的条目数）。"""
    n = len(_AGENT_CACHE)
    _AGENT_CACHE.clear()
    return n


# ============================================================
# 业务规则常量（蓝图 § 5.3）
# ============================================================
LEVEL_TO_URGENCY_EMOJI = {
    RiskLevel.LOW:      "🟢",
    RiskLevel.MEDIUM:   "🟡",
    RiskLevel.HIGH:     "🟠",
    RiskLevel.CRITICAL: "🔴",
}

LEVEL_TO_DEFAULT_ASSIGNEE = {
    RiskLevel.LOW:      "属地巡查员",
    RiskLevel.MEDIUM:   "属地责任人",
    RiskLevel.HIGH:     "属地责任人 + 班组长",
    RiskLevel.CRITICAL: "HSE 经理",
}

LEVEL_TO_DUE_HOURS = {
    RiskLevel.LOW:      24,
    RiskLevel.MEDIUM:   4,
    RiskLevel.HIGH:     1,
    RiskLevel.CRITICAL: 0,  # 实时
}

LEVEL_TO_CHANNEL = {
    RiskLevel.LOW:      Channel.PUSH,
    RiskLevel.MEDIUM:   Channel.PUSH,
    RiskLevel.HIGH:     Channel.HITL,
    RiskLevel.CRITICAL: Channel.HITL,
}


def _now_iso() -> str:
    """当前 UTC 时间（ISO8601，带时区）。"""
    return datetime.now(timezone.utc).isoformat()


def _due_at_iso(max_level: RiskLevel) -> str:
    """按风险等级算 due_at（蓝图 § 5.3 § 4）。"""
    hours = LEVEL_TO_DUE_HOURS[max_level]
    if hours == 0:
        # 实时：now + 0h
        return _now_iso()
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _gen_p8_job_id() -> str:
    """生成 P8_job ID（格式 P8J-YYYYMMDD-HHMMSS-NNN，蓝图 § 4.2）。"""
    now = datetime.now(timezone.utc)
    # 取微秒后 3 位作为 NNN（同一秒内多次创建仍能区分）
    suffix = now.microsecond // 1000
    return f"P8J-{now.strftime('%Y%m%d-%H%M%S')}-{suffix:03d}"


def _compute_job_defaults(level: RiskLevel, risk_basis: str) -> dict[str, Any]:
    """按 level 算 P8Job 默认字段。

    Returns:
        dict 含 urgency_emoji / assignee_role / due_at / channel
    """
    return {
        "urgency_emoji": LEVEL_TO_URGENCY_EMOJI[level],
        "assignee_role": LEVEL_TO_DEFAULT_ASSIGNEE[level],
        "due_at":        _due_at_iso(level),
        "channel":       LEVEL_TO_CHANNEL[level],
    }


# ============================================================
# 工具 1：update_job — 创建/更新 P8_job（蓝图 § 6.1）
# ============================================================
#
# P8JobUpdate 字段：a6_event_ids / p8_job_id / status / channel / note
# max_level / risk_basis 不在 P8JobUpdate 内（仅创建时算，后续不变）
# 工具函数额外接收 level + risk_basis（LLM 推断 + 传入），构造完整 P8Job
# ============================================================
@tool(description=(
    "创建或更新 P8_job。"
    "传 p8_job_id → 按 ID 更新既有 P8_job（status/channel/note 可单独变更）；"
    "不传 → 创建新 P8_job（自动生成 P8J-YYYYMMDD-HHMMSS-NNN ID）。"
    "a6_event_ids 至少 1 个 event ID；N=1 单事件处置，N>1 风险叠加（聚合 P8_job）。"
    "返回 Command(update={'working_memory': [job]}) —— LangGraph reducer 按 pid upsert。"
    "★ 工作记忆（working_memory）写入；终态由 P8ArchiveMiddleware 自动归档。"
))
def update_job(
    a6_event_ids: list[str],
    level: str,
    risk_basis: str,
    p8_job_id: Optional[str] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增：主流程作业 ID（per-job 持久化依据）
    status: Optional[str] = None,
    channel: Optional[str] = None,
    note: Optional[str] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """创建或更新 P8_job。

    Args:
        a6_event_ids: 关联的 A6 输出 ID 列表（≥1）
        level:        风险等级（LOW/MEDIUM/HIGH/CRITICAL）—— LLM 从 a6 events 推断
        risk_basis:   风险依据（拼接各 a6_event 的 basis；聚合 P8_job 必须记录聚合原因）
        p8_job_id:    None → 新建；已存在 ID → 更新该 P8_job
        job_id:       2026-08-20 新增。主流程作业 ID（如 JOB-20260813-001 / 17 位时间戳）；
                      透传到 working_memory / 长期归档 → per-job 清理依据。
                      Bot 模式 + 无作业上下文场景可留 None。
        status:       要变更的目标状态（pending/notified/waiting_decision/...）
        channel:      要设置的通道（HITL/PUSH）
        note:         要追加的备注（覆盖式）
        tool_call_id: 由 LangGraph 注入的当前 tool_call_id（必传，否则
                      LangGraph 会抛 "Every tool call MUST have a corresponding ToolMessage"）

    Returns:
        LangGraph Command（reducer 自动 upsert to working_memory）
    """
    # 2026-08-19 修复：所有返回路径必须含 ToolMessage，否则 LangGraph 抛
    # "Expected to have a matching ToolMessage in Command.update for tool 'update_job', got: []"
    # 错误路径与成功路径统一走 _make_tool_msg(...) 辅助函数。
    def _make_tool_msg(content: str) -> Command:
        return Command(update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
            "working_memory": [],
        })

    try:
        level_enum = RiskLevel(level)
    except ValueError:
        return _make_tool_msg(json.dumps(make_error(
            code="INVALID_LEVEL",
            message=f"无效的 level: {level}；应为 LOW/MEDIUM/HIGH/CRITICAL",
            recoverable=False,
        ), ensure_ascii=False))

    if not risk_basis or not risk_basis.strip():
        return _make_tool_msg(json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="risk_basis 不能为空",
            recoverable=False,
        ), ensure_ascii=False))

    # 验证 P8JobUpdate（自动校验 a6_event_ids 唯一性 + p8_job_id 格式 + job_id 透传）
    try:
        update = P8JobUpdate(
            a6_event_ids=a6_event_ids,
            p8_job_id=p8_job_id,
            job_id=job_id,                 # 2026-08-20 新增
            status=P8JobStatus(status) if status else None,
            channel=Channel(channel) if channel else None,
            note=note,
        )
    except Exception as exc:
        return _make_tool_msg(json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"update_job 参数校验失败: {exc}",
            recoverable=False,
        ), ensure_ascii=False))

    # 构造完整 P8Job dict（max_level / risk_basis / urgency_emoji / assignee_role / due_at / channel）
    defaults = _compute_job_defaults(level_enum, risk_basis)
    now = _now_iso()

    job: dict[str, Any] = {
        "p8_job_id":      update.p8_job_id or _gen_p8_job_id(),
        "job_id":         update.job_id,       # 2026-08-20 新增：主流程作业归属
        "a6_event_ids":   list(update.a6_event_ids),
        "risk_basis":     risk_basis,
        "max_level":      level_enum,
        "urgency_emoji":  defaults["urgency_emoji"],
        "assignee_role":  defaults["assignee_role"],
        "channel":        update.channel or defaults["channel"],
        "status":         update.status or P8JobStatus.PENDING,
        "note":           update.note or "",
        "due_at":         defaults["due_at"],
        "created_at":     now,
    }

    logger.info(
        "update_job: pid=%s, level=%s, channel=%s, status=%s, a6_count=%d",
        job["p8_job_id"], level_enum, job["channel"], job["status"], len(a6_event_ids),
    )

    # 2026-08-20 修复：update_job 返回 Command 立刻 per-job 持久化
    #
    # 触发背景（飞书 chat_reply 场景）：
    #   - 用户消息无 [job_id=...] 前缀 → chat_reply 调 run_disposition_agent(job_id=None)
    #   - run_disposition_agent 的 flush_working_memory 跳过（job_id 为空）
    #   - LLM 从上下文推断出 job_id 并传入 update_job
    #   - P8_job 状态非终态（pending）→ P8ArchiveMiddleware.after_model 不触发 dump
    #   → per-job working_memory.json 永远不落盘
    #
    # 修复：在 update_job 内直接 per-job 持久化（与 LangGraph reducer 同语义：按
    # p8_job_id upsert；保留既有 entries，覆盖同 pid）。失败不抛（不阻断 LLM 主流程）。
    #
    # 兼容性：与 P8ArchiveMiddleware / run_disposition_agent.flush_working_memory 的
    # 写入目标一致（同 job_id → 同文件）；portalocker 文件锁保证多进程安全。
    if job.get("job_id"):
        try:
            from A7.storage.p8_working_memory_store import (
                load_working_memory,
                dump_working_memory,
            )
            existing = load_working_memory(job["job_id"])
            # reducer: 按 p8_job_id upsert（保留既有 entries，覆盖同 pid）
            merged = {
                j["p8_job_id"]: j
                for j in existing
                if isinstance(j, dict) and isinstance(j.get("p8_job_id"), str)
            }
            merged[job["p8_job_id"]] = job
            dump_working_memory(job["job_id"], list(merged.values()))
            logger.info(
                "update_job: per-job working_memory dump → job_id=%s pid=%s total=%d",
                job["job_id"], job["p8_job_id"], len(merged),
            )
        except Exception as exc:
            # 不阻断主流程；P8ArchiveMiddleware / run_disposition_agent 仍有兜底
            logger.warning(
                "update_job: per-job working_memory dump 失败（不阻断主流程）: %s",
                exc,
            )

    # LangGraph Command：reducer 自动按 p8_job_id upsert
    # 2026-08-19 修复：成功路径也必须返回 ToolMessage，否则 LangGraph 抛
    # "Every tool call MUST have a corresponding ToolMessage"。
    return Command(update={
        "messages": [ToolMessage(
            content=json.dumps(make_response(
                "update_job",
                {"status": "ok", "p8_job_id": job["p8_job_id"], "job": job},
            ), ensure_ascii=False),
            tool_call_id=tool_call_id,
        )],
        "working_memory": [job],
    })


# ============================================================
# 工具 2：hitl_decide — 设置 channel=HITL + status=waiting_decision（蓝图 § 6.1）
# ============================================================
#
# 把指定 P8_job 标记为"等待人工决策"，由 HumanInTheLoopMiddleware 中断
# ============================================================
@tool(description=(
    "将 P8_job 标记为等待人工决策。"
    "强制 channel=HITL + status=waiting_decision；HumanInTheLoopMiddleware 会自动中断，"
    "等待用户在前端/CLI/飞书侧确认。"
    "仅修改状态字段，不修改 risk_basis / max_level / assignee_role 等元数据。"
))
def hitl_decide(
    p8_job_id: str,
    options: list[str],
    note: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """把 P8_job 标记为 waiting_decision。

    Args:
        p8_job_id: 要操作的 P8_job ID
        options:   候选决策列表（如 ["approve", "rectify", "reject", "escalate"]）
        note:      备注（写入 note 字段；可空）
        tool_call_id: 由 LangGraph 注入的当前 tool_call_id（必传）

    Returns:
        LangGraph Command（patch 后 working_memory 仍含该 P8_job，但状态变更）
    """
    # 2026-08-19 修复：所有返回路径必须含 ToolMessage。
    if not p8_job_id or not p8_job_id.startswith("P8J-"):
        return Command(update={
            "messages": [ToolMessage(
                content=json.dumps(make_error(
                    code="INVALID_ARGUMENT",
                    message=f"hitl_decide: 无效的 p8_job_id={p8_job_id}",
                    recoverable=False,
                ), ensure_ascii=False),
                tool_call_id=tool_call_id,
            )],
            "working_memory": [],
        })

    if not options:
        options = ["approve", "rectify", "reject", "escalate"]

    # patch 字段：channel=HITL, status=waiting_decision, note=<note>
    # reducer 按 pid upsert → 其他字段保留
    patch = {
        "p8_job_id": p8_job_id,
        "channel":   "HITL",
        "status":    "waiting_decision",
        "note":      note,
        "options":   list(options),  # 候选决策（供前端展示）
        "hitl_at":   _now_iso(),
    }

    logger.info("hitl_decide: pid=%s, options=%s", p8_job_id, options)

    # reducer 必须保留其他字段；这里 patch 只覆盖待改字段即可
    # （LangGraph reducer 按 pid upsert 整个 dict → 既有字段会被 patch 覆盖）
    # 因此要先读出现有 job 然后合并；但 reducer 不支持 partial update
    # 解决方案：在 patch 里包含 pid + 改的字段 + 哨兵注释说明 reducer 行为
    # 实际：必须传完整 job；这里用最小 patch 让上层 LLM 在下一次 invoke 时再 update_job 补全
    # 2026-08-19 修复：成功路径必须返回 ToolMessage。
    return Command(update={
        "messages": [ToolMessage(
            content=json.dumps(make_response(
                "hitl_decide",
                {"status": "ok", "p8_job_id": p8_job_id, "patch": patch},
            ), ensure_ascii=False),
            tool_call_id=tool_call_id,
        )],
        "working_memory": [patch],
    })


# ============================================================
# 工具 3：read_p7_events — 读 P7 风险研判产物（蓝图 § 7 工具 5）
# ============================================================
# 2026-08-20 修复：P6/P7 per-job 化后，A6 评估文件路径由
# `data/jobs/{job_id}/p7_result.json`（主流程聚合）改为
# `data/jobs/{job_id}/P7/a6_*.json`（per-job 阶段产物，每个文件 = 一条 risk event）。
# 本工具同时扫两个位置，主流程产物优先（向后兼容）；per-job 目录追加。
# ============================================================
@tool(description=(
    "读取 P7 风险研判阶段输出，返回 risk_event 列表"
    "（每个 event 含 event_id / level / risk_basis / suggestions ...）。\n"
    "数据源（按优先级合并）：\n"
    "  1) 主流程格式：data/jobs/{job_id}/p7_result.json（events 或 risk_events 数组）\n"
    "  2) per-job 格式：data/jobs/{job_id}/P7/a6_*.json（每个文件 = 一条 risk event）\n"
    "Bot 模式下用户在群内问『这个作业有什么风险』时调用。"
))
def read_p7_events(job_id: str) -> str:
    """读取 P7 阶段产出的风险事件列表。

    Args:
        job_id: 主流程作业 ID（如 JOB-20260813-001 / 17 位时间戳）

    Returns:
        标准 JSON 响应：
        - 找到 P7 数据 → {"status":"ok", "events":[...], "sources":[...]}
        - 无任何 P7 数据 → {"status":"ok", "events":[]}（空列表而非 404）
    """
    if not job_id:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="read_p7_events: job_id 不能为空",
            recoverable=False,
        ), ensure_ascii=False)

    # 使用 file_utils.get_job_dir（与 P6/P7 per-job 路径解析一致；
    # 之前用 Path(__file__).parent.parent.parent 会多解一层到 agent-skill/ 而非 industrial_internet_agents/）
    try:
        from agents.workflow.file_utils import get_job_dir
        job_dir = Path(get_job_dir(job_id))
    except Exception as exc:
        return json.dumps(make_error(
            code="P7_PATH_RESOLVE_FAILED",
            message=f"job_dir 解析失败: {exc}"[:200],
            recoverable=True,
        ), ensure_ascii=False)

    events: list = []
    sources: list = []

    # === 1) 主流程格式（向后兼容）===
    p7_result_path = job_dir / "p7_result.json"
    if p7_result_path.exists():
        try:
            data = json.loads(p7_result_path.read_text(encoding="utf-8"))
            main_events = data.get("events", []) or data.get("risk_events", []) or []
            events.extend(main_events)
            sources.append("p7_result.json")
        except Exception as exc:
            return json.dumps(make_error(
                code="P7_READ_FAILED",
                message=f"p7_result.json 解析失败: {exc}"[:200],
                recoverable=True,
            ), ensure_ascii=False)

    # === 2) per-job 格式（P6/P7 独立触发的真实数据）===
    p7_dir = job_dir / "P7"
    if p7_dir.exists() and p7_dir.is_dir():
        a6_files = sorted(p7_dir.glob("a6_*.json"))
        for f in a6_files:
            try:
                a6 = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                # 单文件损坏不阻断整体读取（参考 P7_READ_FAILED 处理但降级为 warning）
                logger.warning(f"[read_p7_events] 跳过损坏文件 {f.name}: {exc}")
                continue
            # 映射 a6_*.json → 标准 risk event 结构
            events.append({
                "event_id":         a6.get("a6_event_id"),
                "type":             a6.get("event_type"),
                "risk_level":       a6.get("risk_level"),
                "risk_level_name":  a6.get("risk_level_name"),
                "risk_basis":       a6.get("risk_basis"),
                "suggestions":      a6.get("suggestions", []) or [],
                "reasoning":        a6.get("reasoning"),
                "evidence":         a6.get("evidence", {}) or {},
                "involved_persons": a6.get("involved_persons", []) or [],
                "first_seen":       a6.get("first_seen"),
                "last_seen":        a6.get("last_seen"),
                "wall_time":        a6.get("wall_time"),
                "timestamp":        a6.get("timestamp"),
                "source_file":      f.name,
            })
        if a6_files:
            sources.append(f"P7/a6_*.json({len(a6_files)})")

    # 无任何 P7 数据 → 蓝图 § 8.1：缺数据 → 空列表（不抛 404）
    if not sources:
        return json.dumps(make_response(
            "read_p7_events",
            {"job_id": job_id, "events": [], "note": "P7 数据不存在（p7_result.json 与 P7/ 均未找到）"},
        ), ensure_ascii=False)

    return json.dumps(make_response(
        "read_p7_events",
        {"job_id": job_id, "events": events, "event_count": len(events), "sources": sources},
    ), ensure_ascii=False)


# ============================================================
# 工具 4 (v2)：open_work_ticket — 开启 P8P9 作业票（创建 job + 发飞书卡片）
# ============================================================
# 2026-09-17 重构背景：
# - 旧版 notify_feishu 调 feishu_gateway_cli.feishu_sender.send_to_group_card，
#   把卡片 JSON 推给 Gateway；写的是 P8_job 表（per-job working_memory）。
# - P8P9 模块（state_machine + cards + business_actions）落地后，
#   作业票的状态机改走 data/jobs/{17位}/closure_state.json（与主流程同目录）。
# - v2：本工具通过 P8P9 agent_interface 创建 P8P9 job（job_status=open）
#   + 绑 card + 调 P8P9/services/card_render 真实发飞书 Card 2.0 卡片
#   （每 event 一张，含「接取任务」按钮）。
#
# 边界：
# - P8 唯一允许"创建 P8P9 job + 发飞书卡片"的入口；后续状态转换必须走卡片按钮 / Web 端。
# - events 必填且 ≥1；chat_id 或 group_name 必填其一（仅支持群发）。
# - job_id 必填且必须是 17 位数字 ERP 工单号（如 '20260917000000003'），
#   必须与 data/jobs/{job_id}/{p5|p7}_result.json 目录同名。
#   P8 严禁自行生成 P8P9-... 形式 ID。
# ============================================================
@tool(description=(
    "开启 P8P9 作业票（创建 job + 群内发飞书 Card 2.0 卡片）。"
    "★ P8 唯一允许的『创建 P8P9 作业』入口 —— 仅能写 job_status=open，"
    "其余任何状态转换（acknowledge / submit / relinquish / escalate / downgrade / "
    "record_review）必须由飞书卡片按钮或 Web 详情页触发，P8 agent 严禁自行执行。\n"
    "参数：\n"
    "  events:      P7 风险事件列表（≥1 个；每个含 risk_event_id / risk_level / event_type）。\n"
    "  risk_basis:  风险依据（可空）。\n"
    "  job_id:      ★★ 必填 17 位 ERP 数字工单号（如 '20260917000000003'）。"
    "必须与 data/jobs/{job_id}/ 主流程目录同名；P8 严禁自行生成 P8P9-... 形式 ID。\n"
    "  chat_id:     飞书群 ID（oc_xxx）；二选一必填；★ 当前群已自动注入时可省略。\n"
    "  group_name:  按 FEISHU_GROUP_MAP.name 反查 chat_id；二选一必填；可省略。\n"
    "  account_id:  Gateway 账号 ID（多账号机器人场景）。\n"
    "成功后会向 chat_id/group_name 指定的飞书群发送 open 态接取卡片（含「接取任务」按钮）。\n"
    "★ 当前对话来源群已在 system_prompt 注入；LLM 不必显式传 chat_id，工具内部兜底填入。"
))
def open_work_ticket(
    events: list[dict],
    risk_basis: str = "",
    job_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    group_name: Optional[str] = None,
    account_id: Optional[str] = None,
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增：运行时注入（LLM 不可见）
) -> str:
    """开启 P8P9 作业票（v2：发 P8P9 状态机新版的飞书卡片）。

    Args:
        events:      P7 风险事件列表。必须 ≥1；每个 event 含 risk_event_id / risk_level。
        risk_basis:  风险依据（LLM 从 events 推断；空字符串 OK）。
        job_id:      ★ 必填 17 位 ERP 数字工单号（如 '20260917000000003'）；
                     必须与 data/jobs/{job_id}/ 主流程目录同名。
                     已有 ID → 幂等返回现有 state（不覆盖）。
        chat_id:     飞书群 ID（oc_xxx）；LLM 可省略——缺省时从 chat_ctx 兜底。
        group_name:  按 FEISHU_GROUP_MAP.name 反查 chat_id；LLM 可省略——缺省时从 chat_ctx 反查。
        account_id:  Gateway 账号 ID（多账号机器人场景）。
        chat_ctx:    ★ 运行时注入（InjectedToolArg，LLM 不可见）。
                     来自 create_disposition_agent 的 chat_ctx；含 chat_id / chat_type / group_name。
                     当 LLM 没传 chat_id/group_name 时，自动用 chat_ctx.chat_id 兜底，
                     并按 FEISHU_GROUP_MAP 反查 group_name 填入 state.card_binding。

    Returns:
        标准 JSON 响应。
        成功：{"status": "ok", "job_id", "version", "cards_sent", "card_message_ids", ...}
        失败：{"status": "error", "code", "message"}
    """
    # ── 0. chat_ctx 兜底（LLM 没传 chat_id/group_name 时自动填）──
    # 2026-09-17 新增：P8 agent 在 chat_reply / 群聊场景已自动注入 chat_ctx；
    # LLM 调 open_work_ticket 不必每次显式传 chat_id / group_name。
    if chat_ctx and isinstance(chat_ctx, dict):
        ctx_chat_id = (chat_ctx.get("chat_id") or "").strip()
        if ctx_chat_id:
            if not chat_id or not str(chat_id).strip():
                chat_id = ctx_chat_id
                logger.info("open_work_ticket: chat_id 缺省，从 chat_ctx 兜底填入 %s", chat_id)
            if not group_name or not str(group_name).strip():
                # chat_ctx.group_name 由调用方在 chat_reply 层按 FEISHU_GROUP_MAP 反查填入；
                # 这里再保险一次（防止调用方忘填）。
                grp = _lookup_group_name(ctx_chat_id)
                if grp:
                    group_name = ctx_chat_id   # 实际仍用 chat_id（feishu_sender 二选一）
                    # 但为了响应里能看到群名，记到 binding 的 group_name

    # ── 1. 收件人互斥校验 ──
    provided = [("chat_id", chat_id), ("group_name", group_name)]
    given = [(k, v) for k, v in provided if v and str(v).strip()]
    if len(given) == 0:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="open_work_ticket: 必须传 chat_id 或 group_name（仅支持群发；单聊 DM 不支持卡片）；"
                    "如已在飞书群对话中触发本工具但仍报此错，说明 chat_reply 未注入 chat_ctx，"
                    "请检查 chat_reply_handler 是否调用 disposition_demo(..., chat_ctx={...})",
            recoverable=False,
        ), ensure_ascii=False)

    # ── 2. events 校验 ──
    if not events or not isinstance(events, list) or len(events) == 0:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="open_work_ticket: events 必填且 ≥1",
            recoverable=False,
        ), ensure_ascii=False)
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=f"open_work_ticket: events[{i}] 不是 dict",
                recoverable=False,
            ), ensure_ascii=False)
        if not e.get("risk_event_id"):
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=f"open_work_ticket: events[{i}] 缺 risk_event_id",
                recoverable=False,
            ), ensure_ascii=False)
        if e.get("risk_level") is None:
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=f"open_work_ticket: events[{i}] 缺 risk_level",
                recoverable=False,
            ), ensure_ascii=False)

    # ── 3. job_id 必填 + 17 位 ERP 数字工单号强校验 ──
    # 2026-09-17：P8P9 状态机与主流程作业同目录（data/jobs/{17位}/），
    # job_id 必须 = 17 位 ERP 工单号；不允许 P8 自行生成 P8P9-YYYYMMDD-...。
    if not job_id or not str(job_id).strip():
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="open_work_ticket: job_id 必填，必须是 17 位数字 ERP 工单号"
                    "（如 '20260917000000003'）；P8 严禁自行生成 P8P9-... 形式 ID。"
                    "请用户提供 17 位 ERP 工单号（与 data/jobs/{job_id}/ 主流程目录同名）。",
            recoverable=False,
        ), ensure_ascii=False)
    import re
    if not re.match(r"^\d{17}$", str(job_id).strip()):
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"open_work_ticket: job_id={job_id!r} 格式错误，必须是 17 位纯数字 ERP 工单号"
                    f"（如 '20260917000000003'）。",
            recoverable=False,
        ), ensure_ascii=False)
    job_id = str(job_id).strip()

    # ── 4. 调 P8P9 agent_interface + card_render ──
    try:
        from P8P9 import agent_interface
        from P8P9.services.card_render import send_all_open_closure_cards

        # 4a. 初始化 job（job_status=open；幂等：已存在则返回现有 state）
        init_result = agent_interface.initialize_job_for_agent(
            job_id, actor="P8-DispositionAgent", events=events,
        )

        # 4b. 绑 card（写入 card_binding 到 state）
        bind_result = agent_interface.bind_card_for_agent(
            job_id, chat_id=chat_id,
            actor={"open_id": "P8-DispositionAgent", "name": "P8 Agent"},
            group_name=group_name, account_id=account_id,
        )

        # 4c. 发飞书卡片（per-event 一张 open 态卡片，含「接取任务」按钮）
        send_result = send_all_open_closure_cards(
            job_id,
            actor="P8-DispositionAgent",
            chat_id=chat_id,
            group_name=group_name,
            account_id=account_id,
            is_resend=False,  # open_work_ticket 是首次发送
        )
    except Exception as exc:
        logger.exception("open_work_ticket 失败：job_id=%s err=%s", job_id, exc)
        return json.dumps(make_error(
            code="OPEN_WORK_TICKET_FAILED",
            message=f"开启作业票失败: {exc}"[:300],
            recoverable=True,
        ), ensure_ascii=False)

    # 提取每个 event 的 card message_id（飞书回调识别用）
    # 2026-09-17 修复：P8P9/_send_event_card 实际返回 {"send_result": "<message_id>"},
    # 旧版曾返回 {"message_id": ...}。两种格式都兼容。
    card_message_ids = []
    if isinstance(send_result, dict):
        for r in send_result.get("results", []):
            if not isinstance(r, dict):
                continue
            mid = r.get("send_result") or r.get("message_id")
            if mid and r.get("status") in ("sent", "ok", "updated"):
                card_message_ids.append(mid)

    logger.info(
        "open_work_ticket: job_id=%s events=%d cards=%d",
        job_id, len(events), len(card_message_ids),
    )

    return json.dumps(make_response(
        "open_work_ticket",
        {
            "status": "ok",
            "job_id": job_id,
            "version": init_result.get("version"),
            "job_status": init_result.get("job_status"),
            "events_count": len(events),
            "cards_sent": len(card_message_ids),
            "card_message_ids": card_message_ids,
            "binding_status": bind_result.get("status"),
        },
    ), ensure_ascii=False)


# ============================================================
# 工具 4b (v2)：resend_current_card — 重发 P8P9 job 卡片（防网络波动）
# ============================================================
# 与 open_work_ticket 配对：用户报"卡片死掉 / 按钮没渲染 / 消息丢失"时调用。
# 读 state.card_binding 反查 chat_id → 调 send_all_open_closure_cards 重发。
# 不修改 job_status；不影响 version；不影响业务字段。
# ============================================================
@tool(description=(
    "重发当前 P8P9 job 的飞书卡片到原绑定群。"
    "★ 防网络波动：卡片显示异常（按钮没渲染 / 模板错乱 / 消息丢失）时调用。\n"
    "★ 不修改 job_status / version / 业务字段；纯展示修复。\n"
    "参数：job_id: 17 位 ERP 数字工单号（如 '20260917000000003'）；"
    "P8P9 状态机与主流程同目录，禁止 P8P9-... 命名空间。\n"
    "依赖：state.card_binding.chat_id 必须已绑（open_work_ticket 自动绑）。"
))
def resend_current_card(
    job_id: str,
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增：运行时注入（LLM 不可见）
) -> str:
    """重发当前 P8P9 job 的飞书卡片。

    Args:
        job_id:   17 位数字 ERP 工单号（如 '20260917000000003'）；P8P9 与主流程同目录。
        chat_ctx: ★ 运行时注入（InjectedToolArg，LLM 不可见）。
                  来自 chat_reply / create_disposition_agent；含 chat_id / chat_type。
                  重发时优先用 state.card_binding.chat_id（open 时已绑）；缺失时用 chat_ctx 兜底。

    Returns:
        标准 JSON 响应。
    """
    # 2026-09-17 修复：job_id 必须是 17 位数字 ERP 工单号（P8P9 与主流程同目录一一对应），
    # 不再是旧的 P8P9-YYYYMMDD-HHMMSS-NNN 形式。
    if not job_id or not str(job_id).strip() or not re.match(r"^\d{17}$", str(job_id).strip()):
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"resend_current_card: 无效 job_id={job_id!r}（必须是 17 位数字 ERP 工单号）",
            recoverable=False,
        ), ensure_ascii=False)

    try:
        from P8P9.state_machine import ClosureService, StateNotFound
        from P8P9.services.card_render import send_all_open_closure_cards

        svc = ClosureService()
        state = svc.get_state(job_id)
        binding = state.get("card_binding") or {}
        chat_id = binding.get("chat_id")
        if not chat_id:
            return json.dumps(make_error(
                code="NO_CARD_BINDING",
                message=f"resend_current_card: job_id={job_id} 未绑定 chat_id；无法重发",
                recoverable=False,
            ), ensure_ascii=False)

        send_result = send_all_open_closure_cards(
            job_id,
            actor="P8-ResendAgent",
            chat_id=chat_id,
            group_name=binding.get("group_name"),
            account_id=binding.get("account_id"),
            is_resend=True,  # 2026-09-17：resend 用不同 idempotency_key 避免 Gateway 409
        )

        card_message_ids = []
        if isinstance(send_result, dict):
            for r in send_result.get("results", []):
                if not isinstance(r, dict):
                    continue
                mid = r.get("send_result") or r.get("message_id")
                if mid and r.get("status") in ("sent", "ok", "updated"):
                    card_message_ids.append(mid)

        logger.info(
            "resend_current_card: job_id=%s chat_id=%s cards=%d",
            job_id, chat_id, len(card_message_ids),
        )

        return json.dumps(make_response(
            "resend_current_card",
            {
                "status": "ok",
                "job_id": job_id,
                "chat_id": chat_id,
                "cards_sent": len(card_message_ids),
                "card_message_ids": card_message_ids,
                "job_status": state.get("job_status"),
                "version": state.get("version"),
            },
        ), ensure_ascii=False)

    except StateNotFound:
        return json.dumps(make_error(
            code="STATE_NOT_FOUND",
            message=f"resend_current_card: job_id={job_id} 不存在",
            recoverable=False,
        ), ensure_ascii=False)
    except Exception as exc:
        logger.exception("resend_current_card 失败：job_id=%s err=%s", job_id, exc)
        return json.dumps(make_error(
            code="RESEND_FAILED",
            message=f"重发卡片失败: {exc}"[:300],
            recoverable=True,
        ), ensure_ascii=False)


# ============================================================
# 工具 5：list_active_p8_jobs — 读 working_memory（蓝图 § 7 工具 7）
# ============================================================
@tool(description=(
    "列出当前所有 in-progress P8_job（从 working_memory 读）。"
    "Bot 模式下用户在群内问『现在有哪些处置任务』时调用。"
    "★ 工作记忆（working_memory）只读出口 —— 不修改 state。"
))
def list_active_p8_jobs() -> str:
    """列出当前 in-progress P8_job（蓝图 § 7）。

    Note: LLM 工具无法直接读 LangGraph state；
    本工具实现为：通过 channel_gateway_client 的 query 路径或返回空列表占位。
    真实读取走 A7/api/p8_working_memory_ctrl（REST 端点）。
    """
    # LLM 工具本身无法读 working_memory（state 在 LangGraph runtime 里）；
    # 工作记忆查询由前端/chat_reply 通过 REST 端点 /api/jobs/{job_id}/working-memory 完成。
    # 这里返回提示，引导 LLM 引导用户查 REST 或 chat_reply 上下文。
    return json.dumps(make_response(
        "list_active_p8_jobs",
        {
            "active_p8_jobs": [],
            "note": "工作记忆查询走 REST 端点 GET /api/jobs/{job_id}/working-memory；"
                    "Bot 模式下 chat_reply 会自动把 working_memory 快照拼到回复下方。",
        },
    ), ensure_ascii=False)


# ============================================================
# 工具 6：recall_jobs — 长期记忆查询（罗盘长期记忆 LLM 工具入口）
# ============================================================
# 与过渡版同名签名 —— LLM 调用约定不变
#
# 2026-09-18 修复：P8P9 v2.1 重构后，归档实际写到
#   data/jobs/_long_term/<job_id>.json  （[P8P9/business_actions.py:651](P8P9/business_actions.py#L651)）
# 而 A7/storage/p8_long_term.py 还在读老的
#   data/jobs/{job_id}/P8/archived.json  （[A7/storage/p8_long_term.py:88](A7/storage/p8_long_term.py#L88)）
# 两边错位 → P8 agent 实际查不到任何 P8P9 归档。
# 修复：本工具**优先**读 _long_term/（P8P9 真实数据源），**回退**到 A7.storage
# （老 P8Job 蓝图，向后兼容）。
# ============================================================
@tool(description=(
    "从长期记忆查询历史 P8_job/P8P9 job。仅在用户明确要求时调用（如 '昨天那个事件最后怎么处理的'）。"
    "query: 关键词（如 '昨天可燃气体' / 'P8J-20260813-180000-001' / 17 位 ERP job_id）。"
    "默认返回索引层一句话描述（轻量；最多 20 条）；如需完整归档详情，"
    "请再用 detail_job_id='<job_id>' 再调一次。"
    "★ 长期记忆入口（罗盘长期记忆）：数据源 = data/jobs/_long_term/{job_id}.json（P8P9 真实归档），"
    "回退到 A7/storage/p8_long_term.py（老 P8Job 蓝图，向后兼容）"
))
def recall_jobs(query: str = "", detail_job_id: Optional[str] = None) -> str:
    """从长期记忆查询历史 job（罗盘长期记忆 LLM 工具入口）。

    ★★★ 长期记忆 LLM 入口（罗盘长期记忆） ★★★
    数据源（按优先级）：
      1) P8P9 v2.1 真实归档：data/jobs/_long_term/{job_id}.json
      2) 老 P8Job 蓝图：data/jobs/{job_id}/P8/archived.json（A7/storage/p8_long_term.py）

    参数:
        query:         关键词（用于索引层子串搜索；如 '可燃气体' / 'HIGH' / 17 位 job_id）
        detail_job_id: 可选；指定后直接走数据层精确查询（"两步走" 第二步）。
                       既接受 17 位 ERP job_id（P8P9），也接受 P8J-... 形式 p8_job_id（老蓝图）。
    返回:
        标准 JSON 响应：detail_job_id 给定时返回单条完整 archived job；
        否则返回 [(job_id, 一句话描述), ...] 列表（合并 _long_term/ + P8/archived.json）
    """
    if not query and not detail_job_id:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="recall_jobs: query 与 detail_job_id 至少给一个",
            recoverable=False,
        ), ensure_ascii=False)

    # === 路径 A：精确查询（"两步走" 第二步 — 拿详情） ===
    if detail_job_id:
        archived = _get_archived_job_compat(detail_job_id)
        if archived is None:
            return json.dumps(make_error(
                code="LONG_TERM_NOT_FOUND",
                message=f"长期记忆无 job_id={detail_job_id}（_long_term/ 与 P8/archived.json 都查过）",
                recoverable=False,
            ), ensure_ascii=False)
        return json.dumps(make_response(
            "recall_jobs (detail)",
            {"job_id": detail_job_id, "archived_job": archived},
        ), ensure_ascii=False)

    # === 路径 B：索引层子串搜索（"两步走" 第一步 — 拿概览） ===
    hits = _search_archived_descriptions_compat(query, limit=20)
    return json.dumps(make_response(
        "recall_jobs (index)",
        {
            "query": query,
            "hits": [{"job_id": jid, "description": desc} for jid, desc in hits],
            "count": len(hits),
            "next_step_hint": (
                "若用户要看某条详情，请再用 detail_job_id='<job_id>' 再调一次"
            ),
        },
    ), ensure_ascii=False)


# ============================================================
# 工具 6b：list_archived_jobs — 长期记忆表查询（recall_jobs 增强版）
# ============================================================
# 2026-09-18 新增：recall_jobs 只能子串匹配；要「列全部 / 按时间 / 按 level / 按
# decision 过滤」必须新增 tool。本工具返回结构化表格（每行 = 一条 archived job），
# LLM 可直接读表或继续传 detail_job_id 给 recall_jobs 看详情。
#
# 数据源（与 recall_jobs 一致）：
#   1) data/jobs/_long_term/{job_id}.json  （P8P9 真实归档）
#   2) data/jobs/{job_id}/P8/archived.json （老 P8Job 蓝图，via A7.storage）
#
# 过滤参数（全部可选；同时满足 AND）：
#   since / until        — archived_at 时间窗口（ISO 8601，如 "2026-09-17T00:00:00Z"）
#   min_level / max_level — display_risk_level（1=一般 / 2=较重 / 3=严重 / 4=特别严重）
#   decision             — "approved" / "rejected"（精确匹配 review.last_decision）
#   decider_open_id      — 审核人 open_id 精确匹配
#   limit                — 默认 50，最大 200
# ============================================================
@tool(description=(
    "列出已归档的 P8P9 job（recall_jobs 增强版），返回结构化表格。"
    "★ 适用场景：用户问『最近归档了哪些作业』『approved 的有哪些』『所有严重级历史作业』时调用。\n"
    "★ 与 recall_jobs 的区别：recall_jobs 是关键词子串模糊搜索（轻量索引层）；"
    "本工具是结构化表查询（可按时间/等级/decision/decider 过滤，返回每条完整元信息）。\n"
    "过滤参数（全部可选，AND）：\n"
    "  since / until        — archived_at 时间窗口（ISO 8601，如 '2026-09-17T00:00:00Z'）\n"
    "  min_level / max_level — 风险等级（1=一般/2=较重/3=严重/4=特别严重）\n"
    "  decision             — 'approved' 或 'rejected'（精确匹配 review.last_decision）\n"
    "  decider_open_id      — 审核人 open_id 精确匹配\n"
    "  limit                — 默认 50，最大 200\n"
    "返回：每行含 job_id / archived_at / max_level / decision / decider / index_entry。"
    "★ 不传任何过滤参数 = 列出全部归档（按 archived_at DESC）。"
))
def list_archived_jobs(
    since: Optional[str] = None,
    until: Optional[str] = None,
    min_level: Optional[int] = None,
    max_level: Optional[int] = None,
    decision: Optional[str] = None,
    decider_open_id: Optional[str] = None,
    limit: int = 50,
) -> str:
    """列出已归档的 P8P9 job（结构化表查询）。

    数据源（与 recall_jobs 一致）：
      1) data/jobs/_long_term/{job_id}.json  （P8P9 真实归档）
      2) data/jobs/{job_id}/P8/archived.json （老 P8Job 蓝图，via A7/storage.load_all_archived_jobs）
    """
    if limit <= 0 or limit > 200:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"list_archived_jobs: limit 必须在 (0, 200]，实际={limit}",
            recoverable=False,
        ), ensure_ascii=False)

    rows: List[Dict[str, Any]] = []

    # ── 1) 扫 P8P9 _long_term/（优先） ──
    lt_dir = _long_term_dir()
    if lt_dir.exists():
        for jf in lt_dir.glob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(_extract_table_row_from_long_term(data))

    # ── 2) 回退老蓝图 ──
    try:
        from A7.storage.p8_long_term import load_all_archived_jobs
        for archived in load_all_archived_jobs():
            # 老 P8Job dict 结构（与 P8P9 不同）：直接包含 p8_job_id / max_level / ...
            # 但本工具统一成表格 row
            rows.append(_extract_table_row_from_old_blueprint(archived))
    except Exception as exc:
        logger.warning("list_archived_jobs: 老蓝图 load_all_archived_jobs 失败: %s", exc)

    # ── 3) 过滤（AND） ──
    filtered = _filter_table_rows(
        rows,
        since=since, until=until,
        min_level=min_level, max_level=max_level,
        decision=decision, decider_open_id=decider_open_id,
    )

    # ── 4) 按 archived_at DESC 排序 + 截断 ──
    filtered.sort(key=lambda r: r.get("archived_at") or "", reverse=True)
    rows_out = filtered[:limit]

    return json.dumps(make_response(
        "list_archived_jobs",
        {
            "filters": {
                "since": since, "until": until,
                "min_level": min_level, "max_level": max_level,
                "decision": decision, "decider_open_id": decider_open_id,
                "limit": limit,
            },
            "count": len(rows_out),
            "total_matched": len(filtered),
            "rows": rows_out,
            "next_step_hint": (
                "要看某条详情，用 recall_jobs(detail_job_id='<job_id>')"
            ),
        },
    ), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# 工具 7：lookup_feishu_contact — 飞书群/作业人员映射查询
# 2026-09-20 新增：读 .env 中的 FEISHU_GROUP_MAP / FEISHU_USER_MAP 做双向反查
# 数据源：项目根 .env（即 a/.env，路径 = p8_disposition_agent.py 上两级目录的 .env）
# ─────────────────────────────────────────────────────────────────────────────

_FEISHU_MAP_KEYS = {
    "group": "FEISHU_GROUP_MAP",
    "name": "FEISHU_USER_MAP",
}


def _read_env_value(env_path: Path, key: str) -> str:
    """从 .env 文件读一个 key 的字符串值（含双引号包裹的自动去壳）。

    简单 regex 解析，不依赖 python-dotenv。每次 tool 调用都现读 → 改完 .env
    不必重启 agent（只重启 chat_reply 进程一次即可重载模块；之后 .env 改动即生效）。
    """
    if not env_path.exists():
        return ""
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    pattern = rf"^{re.escape(key)}\s*=\s*(.*)$"
    m = re.search(pattern, content, re.MULTILINE)
    if not m:
        return ""
    val = m.group(1).strip()
    # 去外壳引号
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        val = val[1:-1]
    return val


def _load_feishu_map(env_path: Path, key: str) -> Dict[str, Any]:
    """从 .env 读 FEISHU_USER_MAP / FEISHU_GROUP_MAP 的 JSON 值 → dict。

    容错：缺失/损坏 → 返回 {}，不抛错（避免 tool 阻塞 agent）。

    2026-09-20 fallback：.env 现有写法是把 JSON 套了双层引号 + 反斜杠转义
    （"{\\\"...\\\":\\\"...\\\"}"），python-dotenv 读到的也是带 \\\" 的字面量，
    json.loads 直接失败。需要 strip 反斜杠再 loads。
    """
    raw = _read_env_value(env_path, key)
    if not raw:
        return {}
    # 1) 先尝试直接 json.loads
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            return v
    except (json.JSONDecodeError, ValueError):
        pass
    # 2) fallback：raw 含 \\\" / \\' → 还原成普通 JSON 再试
    if '\\"' in raw or "\\'" in raw:
        try:
            v = json.loads(raw.replace('\\"', '"').replace("\\'", "'"))
            if isinstance(v, dict):
                return v
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


@tool(description=(
    "查询飞书群（FEISHU_GROUP_MAP）或作业人员（FEISHU_USER_MAP）映射。"
    "type 必传：'group' 查群（chat_id → {name, description}）；"
    "'name' 查人（open_id → {role, name}）。"
    "查询模式（由 name / id 是否传入决定）："
    "(1) 只传 type → 返回该类型完整映射表；"
    "(2) 传 type + name → 用 name 查 id，返回所有匹配（可能有重名）；"
    "(3) 传 type + id → 用 id 反查 name/role；"
    "(4) 传 type + name + id → 双向校验：name→id 与给定 id 一致才算 match。"
    "数据源：项目根 .env 中的 FEISHU_GROUP_MAP / FEISHU_USER_MAP。"
    "只读查询，不会修改任何状态，可放心使用。"
))
def lookup_feishu_contact(
    type: Annotated[str, "必传。'group' 查群（chat_id 形如 oc_xxx）；'name' 查人（open_id 形如 ou_xxx）"],
    name: Annotated[Optional[str], "群名或人名（可空）。与 id 同时传时做双向校验"] = None,
    id: Annotated[Optional[str], "群 chat_id (oc_xxx) 或 人 open_id (ou_xxx)（可空）"] = None,
) -> str:
    # 1. 校验 type
    if type not in _FEISHU_MAP_KEYS:
        return json.dumps(make_response(
            "lookup_feishu_contact",
            {
                "error": f"type 必须是 'group' 或 'name'，收到 {type!r}",
                "valid_values": list(_FEISHU_MAP_KEYS.keys()),
            },
        ), ensure_ascii=False)

    # 2. 定位 .env：项目根（即 a/.env）。p8_disposition_agent.py 在 agents/ 下，
    #    所以 ROOT = 上两级目录。
    project_root = Path(__file__).resolve().parent.parent
    env_key = _FEISHU_MAP_KEYS[type]
    mapping = _load_feishu_map(project_root / ".env", env_key)

    # 3. 模式判断
    has_name = bool(name and name.strip())
    has_id = bool(id and id.strip())

    # 模式 1：只传 type → 列出全部
    if not has_name and not has_id:
        return json.dumps(make_response(
            "lookup_feishu_contact",
            {
                "type": type,
                "mode": "list_all",
                "source_env_key": env_key,
                "count": len(mapping),
                "mapping": mapping,
                "warning": (f".env 中 {env_key} 未配置或解析失败") if not mapping else None,
            },
        ), ensure_ascii=False)

    # 模式 2：传 type + name → 用 name 查 id
    if has_name and not has_id:
        matches = []
        for k, v in mapping.items():
            if isinstance(v, dict) and v.get("name") == name:
                matches.append({"id": k, **v})
        return json.dumps(make_response(
            "lookup_feishu_contact",
            {
                "type": type,
                "mode": "name_to_id",
                "source_env_key": env_key,
                "name": name,
                "count": len(matches),
                "matches": matches,
            },
        ), ensure_ascii=False)

    # 模式 3：传 type + id → 用 id 反查 name
    if has_id and not has_name:
        data = mapping.get(id)
        if data is None:
            return json.dumps(make_response(
                "lookup_feishu_contact",
                {
                    "type": type,
                    "mode": "id_to_name",
                    "source_env_key": env_key,
                    "id": id,
                    "found": False,
                },
            ), ensure_ascii=False)
        return json.dumps(make_response(
            "lookup_feishu_contact",
            {
                "type": type,
                "mode": "id_to_name",
                "source_env_key": env_key,
                "id": id,
                "found": True,
                "data": data,
            },
        ), ensure_ascii=False)

    # 模式 4：传 type + name + id → 双向校验
    matched_ids = []
    for k, v in mapping.items():
        if isinstance(v, dict) and v.get("name") == name:
            matched_ids.append(k)

    if id in matched_ids:
        return json.dumps(make_response(
            "lookup_feishu_contact",
            {
                "type": type,
                "mode": "cross_check",
                "source_env_key": env_key,
                "name": name,
                "id": id,
                "match": True,
                "data": mapping[id],
            },
        ), ensure_ascii=False)

    return json.dumps(make_response(
        "lookup_feishu_contact",
        {
            "type": type,
            "mode": "cross_check",
            "source_env_key": env_key,
            "name": name,
            "id": id,
            "match": False,
            "name_resolves_to": matched_ids,
        },
    ), ensure_ascii=False)


def _extract_table_row_from_long_term(data: Dict[str, Any]) -> Dict[str, Any]:
    """从 _long_term/{job_id}.json 提取表行（job_id / archived_at / max_level / decider / decision）。"""
    cs = data.get("closure_state") or {}
    review = cs.get("review") or {}
    history = review.get("history") or []
    last_decision = review.get("last_decision") or ""
    decider = ""
    if history:
        decider = history[-1].get("by", "") or ""
    return {
        "source": "p8p9_long_term",
        "job_id": data.get("job_id") or cs.get("job_id") or "",
        "archived_at": data.get("archived_at") or cs.get("archived_at") or "",
        "max_level": cs.get("display_risk_level"),
        "decision": last_decision,
        "decider": decider,
        "index_entry": data.get("index_entry") or "",
    }


def _extract_table_row_from_old_blueprint(archived: Dict[str, Any]) -> Dict[str, Any]:
    """从老 P8Job 蓝图 archived dict 提取表行。"""
    return {
        "source": "p8_old_blueprint",
        "job_id": archived.get("p8_job_id") or archived.get("job_id") or "",
        "archived_at": archived.get("archived_at") or "",
        "max_level": archived.get("max_level"),
        "decision": archived.get("decision") or "",
        "decider": archived.get("decider") or archived.get("by") or "",
        "index_entry": archived.get("index_entry") or "",
    }


def _filter_table_rows(
    rows: List[Dict[str, Any]],
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    min_level: Optional[int] = None,
    max_level: Optional[int] = None,
    decision: Optional[str] = None,
    decider_open_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """AND 过滤；时间窗口用前缀字符串比较（ISO 8601 字典序 = 时间序）。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        # archived_at 时间窗口
        ts = (r.get("archived_at") or "").strip()
        if since and ts and ts < since:
            continue
        if until and ts and ts > until:
            continue
        # level
        lvl = r.get("max_level")
        if isinstance(lvl, int):
            if min_level is not None and lvl < min_level:
                continue
            if max_level is not None and lvl > max_level:
                continue
        # decision
        if decision and r.get("decision") != decision:
            continue
        # decider
        if decider_open_id and r.get("decider") != decider_open_id:
            continue
        out.append(r)
    return out


# ─── 兼容层：合并 _long_term/（P8P9 真实）+ A7/storage（老蓝图） ───────────

# 2026-09-18：P8P9 v2.1 归档路径 = data/jobs/_long_term/{job_id}.json
# 复用 P8P9/state_machine.py 的 base_dir 计算（避免 hardcode data/）
_LONG_TERM_DIRNAME = "_long_term"


def _long_term_dir() -> Path:
    """P8P9 长期记忆目录（[P8P9/business_actions.py:651](P8P9/business_actions.py#L651)）。"""
    try:
        from P8P9 import state_machine as _sm
        base = Path(_sm.__file__).resolve().parent.parent  # P8P9/state_machine.py → project root
    except Exception:
        base = Path(__file__).resolve().parent.parent
    return base / "data" / "jobs" / _LONG_TERM_DIRNAME


def _long_term_path(job_id: str) -> Path:
    return _long_term_dir() / f"{job_id}.json"


def _get_archived_job_compat(job_id: str) -> Optional[Dict[str, Any]]:
    """优先 P8P9 _long_term/；回退老 A7/storage p8_long_term.py。"""
    p = _long_term_path(job_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("recall_jobs: _long_term/%s.json 解析失败: %s", job_id, exc)
    # 回退老蓝图
    try:
        return get_archived_job(job_id)
    except Exception as exc:
        logger.warning("recall_jobs: A7.storage.get_archived_job(%s) 失败: %s", job_id, exc)
        return None


def _search_archived_descriptions_compat(query: str, limit: int = 20):
    """合并搜索 _long_term/（P8P9）+ 老蓝图（A7.storage）。"""
    out: List[tuple] = []

    # 1) 扫 P8P9 _long_term/ 子串匹配 index_entry + closure_state.archived_to_lt/...
    lt_dir = _long_term_dir()
    if lt_dir.exists():
        for jf in sorted(lt_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            jid = data.get("job_id") or jf.stem
            entry = (data.get("index_entry") or "").lower()
            # 子串匹配 index_entry + job_id
            q = (query or "").lower().strip()
            if q and (q in (jid or "").lower() or q in entry):
                out.append((jid, data.get("index_entry") or ""))

    # 2) 回退老蓝图
    try:
        out.extend(search_archived_descriptions(query, limit=limit))
    except Exception as exc:
        logger.warning("recall_jobs: A7.storage.search_archived_descriptions 失败: %s", exc)

    # 去重 + 截断
    seen, deduped = set(), []
    for jid, desc in out:
        if jid in seen:
            continue
        seen.add(jid)
        deduped.append((jid, desc))
    return deduped[:limit]


# ============================================================
# Agent 工厂（蓝图 § 7）
# ============================================================

def create_disposition_agent(
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增：当前对话来源群
):
    """创建 P8 人机协同处置 Agent（基础版本，无 HITL）。

    蓝图 § 7：state_schema=P8State, middleware=[HITL, Archive],
    checkpointer=_p8_checkpointer；无 HITL 版本不挂 HITL middleware（仅 Archive）。

    2026-08-20 改造：
    - ``job_id`` 非空时 → middleware 触发 per-job 双写 + working_memory dump
    - 启动时若 job_id 非空，懒加载 ``data/jobs/{job_id}/P8/working_memory.json``
      （伪恢复，详见 :func:`_bootstrap_working_memory_into_checkpointer`）
    - cache key 拼接 ``job={job_id}`` 防止 working_memory 跨 job 串台

    2026-09-17 改造：
    - ``chat_ctx`` 非空时 → 从 .env FEISHU_GROUP_MAP 反查群名，注入到 system_prompt
      "当前对话来源群"段；tool 内部兜底：LLM 调 open_work_ticket / resend_current_card
      没传 chat_id 时自动从 chat_ctx 填入，避免反问用户。
    - cache key 拼接 ``chat={chat_ctx}`` 防止跨群串台。

    Args:
        user_ctx: 2026-08-19 新增。chat_reply_handler 构造的"当前用户"身份 dict，
                  含 ``role`` / ``name`` / ``open_id`` 字段（未识别时含 ``note``）。
                  注入到 system_prompt 末尾，让 LLM 知道跟谁对话、避免幻觉身份。
                  默认 None（Gradio / 离线调用场景，无身份注入）。
        job_id:   2026-08-20 新增。主流程作业 ID；非空时启用 per-job 持久化。
                  Bot 模式 + 无作业上下文场景传 None。
        chat_ctx: 2026-09-17 新增。当前对话来源群上下文 dict（含 chat_id / chat_type）。
                  非空时 LLM 已知当前群；省略时（Gradio / 离线）不注入，老逻辑。
    """
    cache_key = _user_ctx_cache_key(user_ctx, "basic", job_id=job_id, chat_ctx=chat_ctx)
    if cache_key in _AGENT_CACHE:
        return _AGENT_CACHE[cache_key]

    # 2026-08-20 新增：启动时 bootstrap working_memory
    if job_id:
        _bootstrap_working_memory_into_checkpointer(job_id)

    llm = create_chat_model_with_logging("P8")
    # 2026-09-17 v2：notify_feishu → open_work_ticket + resend_current_card
    # 旧版 notify_feishu 调 feishu_gateway_cli 直推卡片；v2 走 P8P9 状态机新管线
    # 2026-09-18 v2.1：recall_jobs 路径修复 + 新增 list_archived_jobs（recall_jobs 增强版）
    # 2026-09-20：新增 lookup_feishu_contact（飞书群/人员映射查询）
    tools = [
        update_job, hitl_decide, read_p7_events,
        open_work_ticket, resend_current_card, list_active_p8_jobs,
        recall_jobs, list_archived_jobs,
        lookup_feishu_contact,
    ]

    sys_prompt = (
        load_system_prompt("P8")
        + _format_user_context_block(user_ctx)
        + _format_chat_context_block(chat_ctx)   # 2026-09-17 新增：当前对话来源群
        + _format_job_id_block(job_id)          # 2026-08-20 新增
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=sys_prompt,
        state_schema=P8State,
        middleware=[P8ArchiveMiddleware(job_id=job_id)],   # 2026-08-20：透传
        checkpointer=_p8_checkpointer,
    )
    _AGENT_CACHE[cache_key] = agent
    return agent


def create_disposition_agent_with_hitl(
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增
):
    """创建 P8 人机协同处置 Agent - 支持 HumanInTheLoop（蓝图 § 7）。

    HumanInTheLoopMiddleware 在指定工具调用前中断等待人工确认；
    P8ArchiveMiddleware 在 after_model 自动归档终态 P8_job。

    2026-08-20 改造：同 :func:`create_disposition_agent`，``job_id`` 透传到 middleware。
    2026-09-17 改造：``chat_ctx`` 透传，system_prompt 注入当前群。

    Args:
        user_ctx: 同 create_disposition_agent。
        job_id:   同 create_disposition_agent。
        chat_ctx: 2026-09-17 新增。当前对话来源群上下文。
    """
    cache_key = _user_ctx_cache_key(user_ctx, "hitl", job_id=job_id, chat_ctx=chat_ctx)
    if cache_key in _AGENT_CACHE:
        return _AGENT_CACHE[cache_key]

    # 2026-08-20 新增：启动时 bootstrap working_memory
    if job_id:
        _bootstrap_working_memory_into_checkpointer(job_id)

    llm = create_chat_model_with_logging("P8")
    # 2026-09-17 v2：notify_feishu → open_work_ticket + resend_current_card
    # 旧版 notify_feishu 调 feishu_gateway_cli 直推卡片；v2 走 P8P9 状态机新管线
    # 2026-09-18 v2.1：recall_jobs 路径修复 + 新增 list_archived_jobs（recall_jobs 增强版）
    # 2026-09-20：新增 lookup_feishu_contact（飞书群/人员映射查询）
    tools = [
        update_job, hitl_decide, read_p7_events,
        open_work_ticket, resend_current_card, list_active_p8_jobs,
        recall_jobs, list_archived_jobs,
        lookup_feishu_contact,
    ]

    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "update_job":              True,   # 创建/更新 P8_job 必须确认
            "hitl_decide":             True,   # 进入 HITL 决策必须确认
            "open_work_ticket":        True,   # 2026-09-17 v2：开启 P8P9 作业票（发卡片）必须确认
            "resend_current_card":     False,  # 2026-09-17 v2：重发卡片是幂等展示修复，不阻断（防网络波动）
            "read_p7_events":          False,  # 只读放行
            "list_active_p8_jobs":     False,  # 只读放行
            "recall_jobs":             False,  # 长期记忆只读放行
            "list_archived_jobs":      False,  # 2026-09-18：归档列表只读放行
            "lookup_feishu_contact":   False,  # 2026-09-20：飞书群/人映射只读查询放行
        }
    )

    sys_prompt = (
        load_system_prompt("P8")
        + _format_user_context_block(user_ctx)
        + _format_chat_context_block(chat_ctx)   # 2026-09-17 新增
        + _format_job_id_block(job_id)
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=sys_prompt,
        state_schema=P8State,
        middleware=[hitl_middleware, P8ArchiveMiddleware(job_id=job_id)],   # 2026-08-20：透传
        checkpointer=_p8_checkpointer,
    )
    _AGENT_CACHE[cache_key] = agent
    return agent


# ============================================================
# 运行入口（蓝图 § 7 工具 11）
# ============================================================

def run_disposition_agent(
    message: str,
    *,
    thread_id: str = "default",
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增：当前对话来源群
) -> str:
    """运行 P8 人机协同处置 Agent（基础版，无 HITL）。

    2026-08-20 改造：``job_id`` 非空时 → invoke end 自动
    :func:`flush_working_memory` 持久化 working_memory 到 per-job JSON。

    2026-09-17 改造：``chat_ctx`` 透传到 create_disposition_agent，
    注入到 system_prompt（让 LLM 知道当前群）+ tool 兜底（chat_id 缺省时自动填）。

    Args:
        message:   用户消息
        thread_id: LangGraph thread_id。
                   - 主流程：``f"p8-{job_id}"``
                   - Bot 模式（chat_reply）：``chat_id``（群）或 ``open_id``（单聊）；
                     旧版兼容回退 ``"default"``。
                   不同 thread_id 在 MemorySaver 下完全隔离 working_memory / messages。
        user_ctx:  2026-08-19 新增。chat_reply 注入的"当前用户"身份 dict。
                   透传到 create_disposition_agent，按 user_ctx 复用 agent 实例。
                   **不**影响 thread_id。
        job_id:    2026-08-20 新增。主流程作业 ID；非空时启用 per-job 持久化。
                   Bot 模式 + 无作业上下文场景传 None（不持久化）。
        chat_ctx:  2026-09-17 新增。当前对话来源群上下文 dict（含 chat_id / chat_type）。
                   非空时 LLM 已知当前群；省略时（Gradio / 离线）不注入，老逻辑。
    """
    agent = create_disposition_agent(user_ctx=user_ctx, job_id=job_id, chat_ctx=chat_ctx)
    agent_config = get_agent_config(
        thread_id=thread_id,
        agent_name="P8",
        llm_params=get_llm_params(),
    )
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)

    # 2026-08-20 新增：invoke end flush working_memory 到 per-job JSON
    # flush 失败不阻断 LLM 输出（已成功 invoke；持久化是 best-effort）
    if job_id:
        try:
            from A7.storage.p8_working_memory_store import flush_working_memory
            flush_working_memory(job_id)
        except Exception as exc:
            logger.warning(
                "run_disposition_agent: flush_working_memory(%s) 失败: %s",
                job_id, exc,
            )

    return extract_output(result)


def disposition_demo(
    message: str,
    history: list = None,
    *,
    user_ctx: Optional[Dict[str, str]] = None,
    thread_id: Optional[str] = None,
    job_id: Optional[str] = None,   # 2026-08-20 新增：主流程作业 ID
    chat_ctx: Optional[Dict[str, str]] = None,   # 2026-09-17 新增：当前对话来源群
) -> str:
    """Gradio ChatInterface / chat_reply 兼容入口（chat_reply.py L330 硬依赖）。

    位置参数签名 ``(message, history=None)`` 不可破坏 —— chat_reply.py 通过
    `disposition_demo(message, history)` 调用位置参数。
    2026-08-19 新增两个 keyword-only 参数：
        - ``user_ctx``：chat_reply 用 ``disposition_demo(message, history=None,
          user_ctx=user_ctx)`` 注入身份（缓存 Agent 实例）。
        - ``thread_id``（2026-08-19 新增）：chat_reply 按"群=chat_id / 单聊=open_id"
          决定 LangGraph checkpointer key。``None`` → 回退 ``"default"``
          （向后兼容过渡版 / 单元测试）。
    2026-08-20 新增第三个 keyword-only 参数：
        - ``job_id``：chat_reply 从消息正文 ``[job_id=...]`` 解析或 None（Bot 临时会话）。
          透传到 ``run_disposition_agent`` → middleware 触发 per-job 持久化。
          ``None`` → 不持久化（D5 决策：Bot 临时会话不持久 working_memory）。
    2026-09-17 新增第四个 keyword-only 参数：
        - ``chat_ctx``：当前对话来源群上下文（含 chat_id / chat_type）。
          透传到 run_disposition_agent → 注入 system_prompt + tool 兜底。

    Args:
        message:   用户消息文本。
        history:   Gradio 兼容参数（不使用；P8 状态由 MemorySaver 通过 thread_id 维护）。
        user_ctx:  chat_reply_handler 构造的"当前用户"身份 dict（keyword-only）。
        thread_id: LangGraph thread_id（keyword-only；``None`` 回退 ``"default"``）。
                   Bot 模式由 chat_reply 注入；主流程 / 测试可显式指定。
        job_id:    2026-08-20 新增。主流程作业 ID（keyword-only；``None`` → Bot 临时会话）。
        chat_ctx:  2026-09-17 新增。当前对话来源群上下文（keyword-only）。
    """
    # 历史参数仅用于 Gradio 兼容；P8 状态由 MemorySaver 通过 thread_id 维护
    return run_disposition_agent(
        message,
        user_ctx=user_ctx,
        thread_id=thread_id or "default",   # ← None 回退 "default"（向后兼容）
        job_id=job_id,                      # 2026-08-20 透传
        chat_ctx=chat_ctx,                  # 2026-09-17 透传
    )


def execute_stage(job_id: str) -> dict:
    """P8 阶段执行入口：启动指定作业的人机协同处置。"""
    from datetime import datetime, timezone
    from .utils import get_stage_logger

    log = get_stage_logger("P8")
    log.log_enter(job_id)
    project_root = Path(__file__).resolve().parent.parent
    p7_path = project_root / "data" / "jobs" / job_id / "p7_result.json"
    risk_events = []
    if p7_path.exists():
        try:
            risk_events = json.loads(p7_path.read_text(encoding="utf-8")).get(
                "risk_events", []
            )
        except (OSError, json.JSONDecodeError):
            pass

    result = {
        "job_id": job_id,
        "stage": "P8",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }
    try:
        message = f"处理作业 {job_id} 的 P7 风险事件（共 {len(risk_events)} 个）"
        if risk_events:
            message += "；请先调 read_p7_events 工具读 p7_result.json"
        result["p8_llm_summary"] = run_disposition_agent(
            message, thread_id=f"p8-{job_id}", job_id=job_id
        )
        result.update({
            "completed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        if risk_events:
            result["pending_confirmation"] = {
                "type": "p8_decision",
                "message": "P8 处置任务已创建；请查看工作记忆或飞书通知确认",
            }
    except Exception as exc:
        log.log_error(job_id, exc)
        result["error"] = str(exc)

    p8_path = project_root / "data" / "jobs" / job_id / "p8_result.json"
    p8_path.parent.mkdir(parents=True, exist_ok=True)
    p8_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.log_exit(job_id, result)
    return result
