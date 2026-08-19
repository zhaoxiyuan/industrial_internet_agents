"""P8: 人机协同处置（蓝图版本）

按蓝图 § 6.1 / § 7 / § 11.3 重写：
- 6 个工具（update_job / hitl_decide / read_p7_events / notify_feishu /
  list_active_p8_jobs / recall_jobs），全部基于 LangGraph state reducer +
  P8ArchiveMiddleware + channel_gateway_client 真实联通
- state_schema=P8State（含 working_memory / long_term_memory）
- checkpointer 单例（_p8_checkpointer）供 A7/api/p8_working_memory_ctrl 读取
- 中间件：HumanInTheLoopMiddleware + P8ArchiveMiddleware
- 长期记忆（罗盘长期记忆）：recall_jobs 通过 A7.storage 索引/数据层接口
- 飞书推送：notify_feishu 通过 channel_gateway_client.send_message 真实联通

向后兼容：
- disposition_demo(message, history=None) -> str 签名一字不动
  （chat_reply.py L330 / Gradio ChatInterface 依赖）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Optional

from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import HumanMessage, ToolMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .model.chat_model import create_chat_model_with_logging
from .model.config import get_llm_params
from .utils.agent_utils import extract_output
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.logging_handler import get_agent_config

from .utils.system_prompt import load_system_prompt
from .utils import get_stage_logger

# 配置日志
import logging
logger = logging.getLogger("p8_disposition_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ★★★ 长期记忆接入（罗盘长期记忆） ★★★
from A7.storage import (
    search_archived_descriptions,   # 索引层：子串搜索（LLM "两步走" 第一步）
    get_archived_job,              # 数据层：精确查询（LLM "两步走" 第二步）
    load_all_index_entries,        # 索引层：列出全部（前端面板 / LLM "列出所有归档"）
    INDEX_FILE, ARCHIVE_FILE,      # 路径常量（诊断用）
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


def _user_ctx_cache_key(user_ctx: Optional[Dict[str, str]], variant: str) -> str:
    """user_ctx → cache key 字符串。

    Args:
        user_ctx: 身份 dict；None 表示"无身份"模式。
        variant: agent 变体标识（"basic" / "hitl"）。

    Returns:
        cache key 字符串（json.dumps 保证 dict 稳定哈希；None 用固定 sentinel）。
    """
    if user_ctx is None:
        return f"{variant}:<none>"
    try:
        ctx_json = json.dumps(user_ctx, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        # user_ctx 含不可 JSON 序列化的值（如 datetime）→ 用 repr 兜底
        ctx_json = repr(sorted(user_ctx.items()))
    return f"{variant}:{ctx_json}"


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

    # 验证 P8JobUpdate（自动校验 a6_event_ids 唯一性 + p8_job_id 格式）
    try:
        update = P8JobUpdate(
            a6_event_ids=a6_event_ids,
            p8_job_id=p8_job_id,
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
# 工具 3：read_p7_events — 读 p7_result.json（蓝图 § 7 工具 5）
# ============================================================
@tool(description=(
    "读取 P7 风险研判阶段输出（data/jobs/{job_id}/p7_result.json）。"
    "返回 risk_event 列表（每个 event 含 event_id / level / risk_basis / ...）。"
    "Bot 模式下用户在群内问『这个作业有什么风险』时调用。"
))
def read_p7_events(job_id: str) -> str:
    """读取 P7 阶段产出的风险事件列表。

    Args:
        job_id: 主流程作业 ID（如 JOB-20260813-001）

    Returns:
        标准 JSON 响应：
        - 找到 p7_result.json → {"status":"ok", "events":[...]}
        - 文件缺失 → {"status":"ok", "events":[]}（空列表而非 404）
    """
    if not job_id:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="read_p7_events: job_id 不能为空",
            recoverable=False,
        ), ensure_ascii=False)

    # 项目根 = <root>/agents/p8_disposition_agent.py → parent.parent.parent
    project_root = Path(__file__).resolve().parent.parent.parent
    p7_path = project_root / "data" / "jobs" / job_id / "p7_result.json"

    if not p7_path.exists():
        # 不抛 404 — 蓝图 § 8.1 约定：缺数据 → 空列表
        return json.dumps(make_response(
            "read_p7_events",
            {"job_id": job_id, "events": [], "note": "p7_result.json 不存在"},
        ), ensure_ascii=False)

    try:
        data = json.loads(p7_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return json.dumps(make_error(
            code="P7_READ_FAILED",
            message=f"p7_result.json 解析失败: {exc}"[:200],
            recoverable=True,
        ), ensure_ascii=False)

    events = data.get("events", []) or data.get("risk_events", []) or []
    return json.dumps(make_response(
        "read_p7_events",
        {"job_id": job_id, "events": events, "event_count": len(events)},
    ), ensure_ascii=False)


# ============================================================
# 工具 4：notify_feishu — 推送飞书交互式告警卡片（蓝图 § 8.2）
# ============================================================
# 2026-08-19 重构：改用 feishu_gateway_cli.feishu_sender 的 send_to_group_card 封装。
#
# 设计边界（与 chat_reply.py 的分工）：
#   - chat_reply.py（普通回消息）→ 走 msg_type=text，不包卡片
#   - notify_feishu（告警/处置推送）→ **必须** Card 2.0 + 必带 options（按钮）
#     按钮按下后飞书回调 → Gateway 透传到 web/api/feishu/card-callback → P8 处置 HITL
#
# 收件人限制：
#   - **只支持群发**（chat_id / group_name）—— 飞书单聊 DM 暂不支持 interactive 卡片
#     （feishu_sender 没有 send_to_user_card）
#   - name / open_id 不再接受（避免 LLM 误用 DM 通道）
#
# options 必填：
#   - 格式 ["label:action", ...]，与 CLI 的 --option 参数一致
#   - 至少 1 个按钮；空 options 返回 INVALID_ARGUMENT
#   - 由 feishu_sender.parse_options 解析；解析失败抛 ValueError
# ============================================================
@tool(description=(
    "通过 OpenClaw Channel Gateway 推送飞书交互式告警卡片（Card 2.0 schema）。"
    "P8_agent 在 channel=PUSH 的 P8_job 创建/变更后调用此工具推送告警。\n"
    "★ 必须传 options —— 每个 button 含 label + action，"
    "飞书用户点击后飞书回调 Gateway → web /api/feishu/card-callback，"
    "由 P8 处置 Agent 处理 HITL 决策。\n"
    "★ 必须传 group 收件人（chat_id 或 group_name）—— 单聊 DM 暂不支持卡片。\n"
    "底层走 feishu_gateway_cli.feishu_sender.send_to_group_card "
    "→ channel_gateway_client.send_message → Gateway (port 8787) → 飞书 API。"
    "成功后 P8_job.status 应变更为 'notified'。"
))
def notify_feishu(
    p8_job_id: str,
    title: str,
    body: str,
    risk_level: str,
    assignee_role: str,
    job_id: str,
    a6_event_ids: list[str],
    options: list[str],  # ★ 必填：["label:action", ...]（如 ["已知悉:ack", "立即处理:handle"]）
    *,
    chat_id: Optional[str] = None,
    group_name: Optional[str] = None,
    alert_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """推送飞书告警卡片（蓝图 § 8.2；2026-08-19 重构走 feishu_sender 封装）。

    Args:
        p8_job_id:     P8_job ID
        title:         卡片标题（含 emoji + 风险等级）
        body:          卡片正文
        risk_level:    风险等级（LOW/MEDIUM/HIGH/CRITICAL）
        assignee_role: 责任岗位（"属地责任人 + 班组长" 等描述性文字；仅作记录/审计，
                       **不是**收件人 ID）
        job_id:        主流程作业 ID
        a6_event_ids:  关联 A6 输出 ID 列表
        options:       ★ 必填。按钮列表，格式 ["label:action", ...]；至少 1 个；
                       与 feishu_sender CLI 的 --option 参数同构。

        --- 收件人（互斥必填其一；仅支持群发）---
        chat_id:       飞书群 ID（"oc_xxx"）直传；不读 GROUP_MAP
        group_name:    按 FEISHU_GROUP_MAP.name 反查 chat_id

        --- 可选 ---
        alert_id:      业务告警 ID；落盘 alert_id→card_id 映射，callback 异步更新卡片
        account_id:    Gateway 账号 ID（多账号机器人场景；如 'P8'）；不传走默认账号

    Returns:
        标准 JSON 响应（含 channel gateway 返回的 intent id / status）
    """
    # 收件人互斥校验（2 选 1：仅支持群发）
    provided = [
        ("chat_id",    chat_id),
        ("group_name", group_name),
    ]
    given = [(k, v) for k, v in provided if v and str(v).strip()]
    if len(given) == 0:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="notify_feishu: 必须传 chat_id 或 group_name（仅支持群发；单聊 DM 不支持卡片）",
            recoverable=False,
        ), ensure_ascii=False)
    if len(given) > 1:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"notify_feishu: 收件人参数互斥，只能传一个；当前传了 {[k for k, _ in given]}",
            recoverable=False,
        ), ensure_ascii=False)

    # options 必填校验（至少 1 个按钮）
    if not options or not isinstance(options, list) or len(options) == 0:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="notify_feishu: options 必填且至少 1 个按钮（格式 ['label:action', ...]）",
            recoverable=False,
        ), ensure_ascii=False)

    # 构造 PushMessage（Pydantic 自动回填 idempotency_key）
    try:
        push_msg = PushMessage(
            title=title,
            body=body,
            a6_event_ids=a6_event_ids,
            assignee_role=assignee_role,
            risk_level=RiskLevel(risk_level),
            job_id=job_id,
            p8_job_id=p8_job_id,
            # idempotency_key 留空 → @model_validator 自动填 p8_job_id
        )
    except Exception as exc:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message=f"notify_feishu 参数校验失败: {exc}",
            recoverable=False,
        ), ensure_ascii=False)

    # 调 feishu_sender.send_to_group_card 封装
    # 2026-08-19：之前 notify_feishu 直调 channel_gateway_client.send_message，
    # 漏传 conversation_id + receive_id_type，且把 assignee_role 误当 account_id，
    # Gateway 必报 VALIDATION_ERROR。改用 feishu_sender 的封装后这些参数自动填好。
    try:
        # 延迟 import 避免循环（A7→agents→A7）；feishu_sender 本身就是 Gateway 适配层
        from feishu_gateway_cli.feishu_sender import (
            send_to_group_card,
            build_feishu_card,
            parse_options,
        )

        # 解析 options（与 CLI 的 parse_options 行为一致：label:action 切分）
        parsed_options = parse_options(options)  # -> [(label, action), ...]
        if not parsed_options:
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=f"notify_feishu: options 解析后无有效按钮；原始 options={options!r}",
                recoverable=False,
            ), ensure_ascii=False)

        # 构造 Card 2.0 JSON dict（必带 options 按钮）
        resolved_alert_id = alert_id or push_msg.p8_job_id
        card = build_feishu_card(
            text=push_msg.body,                    # 卡片正文用 body（title 走 header）
            options=parsed_options,                # ★ 必带按钮（hits HITL 回调）
            title=push_msg.title,
            alert_id=resolved_alert_id,            # 默认 alert_id=p8_job_id
        )

        result = send_to_group_card(
            card=card,
            chat_id=chat_id,
            group_name=group_name,
            account_id=account_id,
            alert_id=resolved_alert_id,
            idempotency_key=push_msg.idempotency_key,
        )
    except Exception as exc:
        logger.exception("notify_feishu: feishu_sender 调用失败: %s", exc)
        return json.dumps(make_error(
            code="FEISHU_PUSH_FAILED",
            message=f"飞书推送失败: {exc}"[:200],
            recoverable=True,
        ), ensure_ascii=False)

    logger.info(
        "notify_feishu: pid=%s, options_count=%d, intent=%s, status=%s",
        p8_job_id, len(parsed_options),
        getattr(result, "intent_id", None), getattr(result, "status", None),
    )

    return json.dumps(make_response(
        "notify_feishu",
        {
            "p8_job_id":       p8_job_id,
            "options_count":   len(parsed_options),
            "intent_id":       getattr(result, "intent_id", None),
            "status":          getattr(result, "status", None),
            "message_id":      getattr(result, "platform_message_id", None),
            "alert_id":        resolved_alert_id,
        },
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
# ============================================================
@tool(description=(
    "从长期记忆查询历史 P8_job。仅在用户明确要求时调用（如 '昨天那个事件最后怎么处理的'）。"
    "query: 关键词（如 '昨天可燃气体' / 'P8J-20260813-180000-001'）。"
    "默认返回索引层一句话描述（轻量；最多 20 条）；如需完整归档详情，"
    "请再用 detail_p8_job_id='<p8_job_id>' 再调一次。"
    "★ 长期记忆入口（罗盘长期记忆）：数据源 = A7/storage/p8_long_term.py"
))
def recall_jobs(query: str, detail_p8_job_id: Optional[str] = None) -> str:
    """从长期记忆查询历史 P8_job（罗盘长期记忆 LLM 工具入口）。

    ★★★ 长期记忆 LLM 入口（罗盘长期记忆） ★★★
    本函数调用 A7/storage/p8_long_term.py 的索引层 + 数据层接口，
    LLM 通过本工具访问长期记忆。

    参数:
        query: 关键词（用于索引层子串搜索；如 '可燃气体' / 'HIGH' / p8_job_id）
        detail_p8_job_id: 可选；指定后直接走数据层精确查询（"两步走" 第二步）
    返回:
        标准 JSON 响应：detail_p8_job_id 给定时返回单条完整 archived P8Job；
        否则返回 [(p8_job_id, 一句话描述), ...] 列表
    """
    if not query and not detail_p8_job_id:
        return json.dumps(make_error(
            code="INVALID_ARGUMENT",
            message="recall_jobs: query 与 detail_p8_job_id 至少给一个",
            recoverable=False,
        ), ensure_ascii=False)

    # === 路径 A：精确查询（"两步走" 第二步 — 拿详情） ===
    if detail_p8_job_id:
        archived = get_archived_job(detail_p8_job_id)
        if archived is None:
            return json.dumps(make_error(
                code="LONG_TERM_NOT_FOUND",
                message=f"长期记忆无 p8_job_id={detail_p8_job_id}",
                recoverable=False,
            ), ensure_ascii=False)
        return json.dumps(make_response(
            "recall_jobs (detail)",
            {"p8_job_id": detail_p8_job_id, "archived_job": archived},
        ), ensure_ascii=False)

    # === 路径 B：索引层子串搜索（"两步走" 第一步 — 拿概览） ===
    hits = search_archived_descriptions(query, limit=20)
    if not query:
        hits = load_all_index_entries()

    return json.dumps(make_response(
        "recall_jobs (index)",
        {
            "query": query,
            "hits": [{"p8_job_id": pid, "description": desc} for pid, desc in hits],
            "count": len(hits),
            "next_step_hint": (
                "若用户要看某条详情，请再用 detail_p8_job_id='<p8_job_id>' 再调一次"
            ),
        },
    ), ensure_ascii=False)


# ============================================================
# Agent 工厂（蓝图 § 7）
# ============================================================

def create_disposition_agent(user_ctx: Optional[Dict[str, str]] = None):
    """创建 P8 人机协同处置 Agent（基础版本，无 HITL）。

    蓝图 § 7：state_schema=P8State, middleware=[HITL, Archive],
    checkpointer=_p8_checkpointer；无 HITL 版本不挂 HITL middleware（仅 Archive）。

    Args:
        user_ctx: 2026-08-19 新增。chat_reply_handler 构造的"当前用户"身份 dict，
                  含 ``role`` / ``name`` / ``open_id`` 字段（未识别时含 ``note``）。
                  注入到 system_prompt 末尾，让 LLM 知道跟谁对话、避免幻觉身份。
                  默认 None（Gradio / 离线调用场景，无身份注入）。
    """
    cache_key = _user_ctx_cache_key(user_ctx, "basic")
    if cache_key in _AGENT_CACHE:
        return _AGENT_CACHE[cache_key]

    llm = create_chat_model_with_logging("P8")
    tools = [
        update_job, hitl_decide, read_p7_events,
        notify_feishu, list_active_p8_jobs, recall_jobs,
    ]

    sys_prompt = load_system_prompt("P8") + _format_user_context_block(user_ctx)

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=sys_prompt,
        state_schema=P8State,
        middleware=[P8ArchiveMiddleware()],
        checkpointer=_p8_checkpointer,
    )
    _AGENT_CACHE[cache_key] = agent
    return agent


def create_disposition_agent_with_hitl(user_ctx: Optional[Dict[str, str]] = None):
    """创建 P8 人机协同处置 Agent - 支持 HumanInTheLoop（蓝图 § 7）。

    HumanInTheLoopMiddleware 在指定工具调用前中断等待人工确认；
    P8ArchiveMiddleware 在 after_model 自动归档终态 P8_job。

    Args:
        user_ctx: 同 create_disposition_agent。chat_reply 默认走基础版（非 HITL），
                  此参数保留以保持 API 对称、便于未来切换。
    """
    cache_key = _user_ctx_cache_key(user_ctx, "hitl")
    if cache_key in _AGENT_CACHE:
        return _AGENT_CACHE[cache_key]

    llm = create_chat_model_with_logging("P8")
    tools = [
        update_job, hitl_decide, read_p7_events,
        notify_feishu, list_active_p8_jobs, recall_jobs,
    ]

    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "update_job":          True,   # 创建/更新 P8_job 必须确认
            "hitl_decide":         True,   # 进入 HITL 决策必须确认
            "notify_feishu":       True,   # 飞书推送必须确认
            "read_p7_events":      False,  # 只读放行
            "list_active_p8_jobs": False,  # 只读放行
            "recall_jobs":         False,  # 长期记忆只读放行
        }
    )

    sys_prompt = load_system_prompt("P8") + _format_user_context_block(user_ctx)

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=sys_prompt,
        state_schema=P8State,
        middleware=[hitl_middleware, P8ArchiveMiddleware()],
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
) -> str:
    """运行 P8 人机协同处置 Agent（基础版，无 HITL）。

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
    """
    agent = create_disposition_agent(user_ctx=user_ctx)
    agent_config = get_agent_config(
        thread_id=thread_id,
        agent_name="P8",
        llm_params=get_llm_params(),
    )
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def disposition_demo(
    message: str,
    history: list = None,
    *,
    user_ctx: Optional[Dict[str, str]] = None,
    thread_id: Optional[str] = None,
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

    Args:
        message:   用户消息文本。
        history:   Gradio 兼容参数（不使用；P8 状态由 MemorySaver 通过 thread_id 维护）。
        user_ctx:  chat_reply_handler 构造的"当前用户"身份 dict（keyword-only）。
        thread_id: LangGraph thread_id（keyword-only；``None`` 回退 ``"default"``）。
                   Bot 模式由 chat_reply 注入；主流程 / 测试可显式指定。
    """
    # 历史参数仅用于 Gradio 兼容；P8 状态由 MemorySaver 通过 thread_id 维护
    return run_disposition_agent(
        message,
        user_ctx=user_ctx,
        thread_id=thread_id or "default",   # ← None 回退 "default"（向后兼容）
    )
