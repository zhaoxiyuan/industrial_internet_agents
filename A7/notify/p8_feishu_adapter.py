"""A7/notify — P8 飞书通道适配器（Feishu adapter for P8 disposition notifications）。

================================================================================
职责（与 docs/P8_人机协同处置_文件组织与职责.md § 7/§ 8 对齐）
================================================================================

P8 的"人机协同处置"在 channel=PUSH 时，需要把 P8_job 推到对应责任岗位的飞书收件人。
本模块是 **P8 业务语义层**：

    P8_agent.notify_feishu(p8_job_id, message)
            │
            ▼
    ┌──────────────────────────────────────────────────────────┐
    │ A7/notify/p8_feishu_adapter.py          （本模块）       │
    │   - 拼装消息正文（单事件 / 聚合两版模板，§ 8.2）         │
    │   - 查 recipients（assignee_role → 飞书 user_map 多人）   │
    │   - 设置 idempotency_key = p8_job_id（同一 P8_job        │
    │     重复发送只产生一条，避免对责任人骚扰）                │
    │   - 设置 gateway metadata 透传审计字段                    │
    │   - 把"飞书推送失败"映射成 status="failed" 返回；        │
    │     不抛异常（§ 8.3：不阻塞 P8_job 推进）                │
    └──────────────────────────────────────────────────────────┘
            │
            ▼
    agents/channel_gateway_client.py   （底层 HTTP 客户端；不动）
            │
            ▼
    OpenClaw Channel Gateway Standalone  （127.0.0.1:8787）

================================================================================
公开 API
================================================================================

高层（LLM 工具调用层）：
    send_feishu(p8_job, job_id) -> FeishuNotifyResult
        单事件 / 聚合 P8_job → 飞书推送主入口（按 USER_MAP 群发）

辅助（测试 / 预校验用）：
    get_conversation_id(assignee_role) -> str
        把责任岗位解析为第一个匹配收件人的 conversation_id（向后兼容）
    resolve_recipients(assignee_role) -> List[RecipientInfo]
        返回与 role 匹配的全部收件人（USER_MAP 数组 → 过滤 role）
    get_user_map() -> Dict[str, Dict[str, str]]
        直接读取 FEISHU_USER_MAP（原始 dict）

数据类：
    FeishuNotifyResult        推送结果（status / message_id / error / recipients / ...）
    RecipientInfo             单个收件人（open_id, chat_id, name, receive_id_type）
    RecipientStatus           单个收件人推送结果（status / message_id / error）

异常：
    FeishuNotifyError                 所有适配器异常的基类
    FeishuNotifyConfigurationError    配置缺失（FEISHU_USER_MAP 等）

================================================================================
收件人解析策略（USER_MAP 模型，2026-08-13 重构）
================================================================================

FEISHU_USER_MAP 是 JSON 字符串，结构：

    {
        "ou_alice_xxx": {
            "chat_id": "oc_group_xxx",      # 群聊 ID（可选；空 → 走 open_id 单聊）
            "role":    "车间主任",            # 责任岗位（中文）
            "name":    "张三"                 # 中文姓名（人只记得自己叫什么）
        },
        "ou_bob_yyy": {
            "chat_id": "",
            "role":    "车间主任",
            "name":    "李四"
        }
    }

解析步骤：
    1. os.getenv("FEISHU_USER_MAP") → JSON 解析为 dict
    2. 遍历每条 (open_id, info)；info.role == assignee_role → 加入 recipients
    3. 每条 recipient 根据是否有 chat_id 决定 receive_id_type：
         - 有 chat_id → receive_id_type="chat_id"（群发到群）
         - 无 chat_id → receive_id_type="open_id"（单聊到个人）

向后兼容：旧 FEISHU_CONVERSATION_MAP / FEISHU_GROUP_<ROLE> 已废弃；
         如发现则 logger.warning 提示运维清理 .env。

================================================================================
设计原则
================================================================================

- **不直接调飞书 HTTP**：所有 HTTP 都走 `agents/channel_gateway_client.py`，
  避免双客户端、双重配置、双重重试逻辑。
- **不阻塞 P8_job**：飞书失败 → 返回 status="failed"，绝不抛异常；
  LLM 看到失败可自行决定是否重试或换通道（§ 8.3）。
- **强幂等**：idempotency_key = p8_job_id；网关对相同 key 返回 cached receipt，
  P8_agent 重启 / 重发 / 并发均不会产生重复消息。
- **可观测**：入口 / 出口 / 异常三处均打日志（含 p8_job_id、assignee_role、
  推送状态、回执 ID）；便于审计与排查。
- **群发**：同一 role 可对应多个收件人；send_feishu 逐个调用 gateway，
  只要全部成功 → status="ok"；任一失败 → status="failed"，但已发的不会撤回。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agents.channel_gateway_client import GatewayError, send_message

from A7.schema.p8_models import P8Job, PushMessage, RiskLevel


# ============================================================
# 1. 模块日志（与 channel_gateway_client.py 风格一致）
# ============================================================

logger = logging.getLogger("p8_feishu_adapter")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    ))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ============================================================
# 2. 业务常量（与 docs/P8_人机协同处置_文件组织与职责.md § 7 对齐）
# ============================================================
#
# 这些常量是"出厂默认"映射表；与 P8 业务规则提示词（agents/rules/P8_RULES.md
# 或 A7/prompt/p8_prompt.py § 2）保持一一对应。
# 修改时请同时更新两边。
# ============================================================

# ----- 风险等级 → 默认责任岗位（§ 7 表格） -----
# Key = RiskLevel enum value；Value = 中文责任岗位名（与 P8Job.assignee_role 对齐）。
RISK_DEFAULT_ASSIGNEE: Dict[RiskLevel, str] = {
    RiskLevel.LOW: "作业负责人",
    RiskLevel.MEDIUM: "班组长",
    RiskLevel.HIGH: "车间主任",
    RiskLevel.CRITICAL: "厂长 / HSE 经理",
}

# ----- 风险等级 → 飞书消息前缀（§ 7 表格） -----
# 形如 "[🟠 紧急]"；用于消息标题前缀。
RISK_PUSH_PREFIX: Dict[RiskLevel, str] = {
    RiskLevel.LOW: "🟢 [一般]",
    RiskLevel.MEDIUM: "🟡 [重要]",
    RiskLevel.HIGH: "🟠 [紧急]",
    RiskLevel.CRITICAL: "🔴 [🚨危急]",
}

# ----- 风险等级 → 响应时限（小时；与 § 7 due_at 计算对齐） -----
# 0 表示 CRITICAL 是"实时"。
RISK_RESPONSE_TIME_HOURS: Dict[RiskLevel, int] = {
    RiskLevel.LOW: 24,
    RiskLevel.MEDIUM: 4,
    RiskLevel.HIGH: 1,
    RiskLevel.CRITICAL: 0,
}

# ----- 消息正文分隔线（28 个 "━"，与 § 8.2 模板对齐） -----
SEPARATOR: str = "━" * 28

# ----- 飞书通道名（适配器固定） -----
FEISHU_CHANNEL: str = "feishu"

# ----- 飞书默认 receive_id_type（群聊 chat_id） -----
# 现在 send_feishu 按每个 recipient 动态决定 receive_id_type（chat_id / open_id）；
# 此常量仍导出给旧调用方参考。
DEFAULT_RECEIVE_ID_TYPE: str = "chat_id"

# ----- 开发 / Sandbox 占位符前缀 -----
# 当 USER_MAP 未配置或 role 无匹配时使用；提醒调用方补环境变量。
DEV_PLACEHOLDER_PREFIX: str = "feishu_dev_"

# ----- USER_MAP 环境变量名 -----
FEISHU_USER_MAP_KEY: str = "FEISHU_USER_MAP"

# ----- 已废弃的旧环境变量名（仅用于启动期 warning 提示） -----
_LEGACY_KEYS: Tuple[str, ...] = ("FEISHU_CONVERSATION_MAP",)
_LEGACY_GROUP_PREFIX: str = "FEISHU_GROUP_"


# ============================================================
# 3. 异常类型
# ============================================================

class FeishuNotifyError(Exception):
    """P8 飞书适配器的基础异常类。

    所有本模块主动抛出的异常都应继承本类，便于上层 try/except 一把抓。
    注意：本模块约定"飞书推送失败 → 不抛异常，仅返回 status='failed'"，
    所以本异常通常只在配置缺失 / 入参非法时抛出。
    """

    def __init__(self, message: str, *, code: str = "FEISHU_NOTIFY_ERROR"):
        super().__init__(message)
        self.code = code


class FeishuNotifyConfigurationError(FeishuNotifyError):
    """飞书适配器配置缺失异常。

    触发场景：USER_MAP 未配置且调用方明确要求严格模式（strict=True）。
    默认 send_feishu 不抛此异常，而是返回 status="failed" + warning 日志，
    便于开发 / Sandbox 环境跑通。
    """

    def __init__(self, assignee_role: str):
        super().__init__(
            f"未配置责任岗位 '{assignee_role}' 的飞书收件人。"
            f"请设置环境变量 {FEISHU_USER_MAP_KEY}（JSON 字符串）。",
            code="FEISHU_NOTIFY_CONFIG_MISSING",
        )
        self.assignee_role = assignee_role


# ============================================================
# 4. 数据类
# ============================================================

@dataclass
class RecipientInfo:
    """单个收件人信息（USER_MAP 一条 → RecipientInfo）。

    Attributes:
        open_id: 飞书用户 open_id（ou_xxx）；作为 USER_MAP dict 的 key
        chat_id: 飞书会话 ID（oc_xxx 群聊；空字符串表示无群 → 走 open_id 单聊）
        name:    中文姓名（人只记得自己叫什么）
        role:    责任岗位（冗余存一份便于调试）
        receive_id_type: "chat_id" 或 "open_id"（由 chat_id 是否为空决定）
    """
    open_id: str
    chat_id: str
    name: str
    role: str
    receive_id_type: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class RecipientStatus:
    """单个收件人推送结果。

    Attributes:
        open_id: 飞书用户 open_id
        chat_id: 飞书会话 ID（实际发送目标）
        name:    中文姓名（便于日志定位责任人）
        status:  "ok" | "failed"
        message_id: 网关回执中的 platform_message_id（成功时非空）
        error:   错误描述字符串（失败时非空）
    """
    open_id: str
    chat_id: str
    name: str
    status: str
    message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class FeishuNotifyResult:
    """飞书推送结果（send_feishu 返回值）。

    Attributes:
        status: "ok" | "partial" | "failed" | "skipped"
            - "ok"      所有 recipients 都成功
            - "partial" 部分成功（recipients 中 ok + failed 都有）
            - "failed"  全部失败 或 配置缺失
            - "skipped" 推送被跳过（如 conversation_id 缺失且非 strict 模式）
        message_id: 第一个成功 recipient 的 platform_message_id
        intent_id:  第一个成功 recipient 的 intent_id
        receipt_id: 第一个成功 recipient 的 receipt_id
        error: 错误描述字符串（全部失败时非空）
        replayed: 第一个成功 recipient 的 replayed 标记
        conversation_id: 第一个成功 recipient 的实际会话 ID（向后兼容字段）
        p8_job_id: 关联的 P8_job ID（用于审计 / 调试）
        recipients: 每个收件人的推送结果（List[RecipientStatus]）
        raw: 网关原始响应 dict（仅第一个 recipient 的）
    """

    status: str                                       # "ok" | "partial" | "failed" | "skipped"
    message_id: Optional[str] = None
    intent_id: Optional[str] = None
    receipt_id: Optional[str] = None
    error: Optional[str] = None
    replayed: bool = False
    conversation_id: Optional[str] = None
    p8_job_id: Optional[str] = None
    recipients: List[RecipientStatus] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转字典，便于 JSON 序列化。

        raw 字段在序列化时只保留顶层摘要，避免日志爆炸。
        recipients 转 list-of-dict。
        """
        d = asdict(self)
        # raw 摘要：仅保留顶层几个关键字段
        if d.get("raw"):
            raw = d["raw"]
            d["raw"] = {
                "intent_status": (raw.get("intent") or {}).get("status"),
                "receipt_status": (raw.get("receipt") or {}).get("status"),
                "error_code": (raw.get("error") or {}).get("code"),
            }
        return d


# ============================================================
# 5. 私有辅助函数：模板渲染
# ============================================================

def _format_due_at(due_at_iso: str) -> str:
    """把 ISO8601 UTC 时间渲染成飞书消息里的可读形式。

    策略：保留 "YYYY-MM-DD HH:MM"（UTC）；如解析失败，原样返回。
    不做时区转换——消息正文里的"截止时间"统一是 UTC，与 P8Job.due_at 字段一致。

    Args:
        due_at_iso: ISO8601 字符串（来自 P8Job.due_at）

    Returns:
        "YYYY-MM-DD HH:MM" 形式的字符串（UTC）

    Examples:
        >>> _format_due_at("2026-08-13T18:30:00+00:00")
        '2026-08-13 18:30'
        >>> _format_due_at("not a date")
        'not a date'
    """
    if not due_at_iso:
        return ""
    # 截取前 16 个字符（"YYYY-MM-DDTHH:MM"），把 "T" 替换为空格
    if len(due_at_iso) >= 16 and due_at_iso[4] == "-" and due_at_iso[10] == "T":
        return due_at_iso[:10] + " " + due_at_iso[11:16]
    return due_at_iso


def _level_for_event(p8_job: P8Job, a6_event_id: str) -> str:
    """聚合 P8_job 中某 a6_event 的等级（用于聚合模板逐行渲染）。

    策略：当前 P8Job.level_distribution 记录的是"等级分布计数"
    （{HIGH: 1, MEDIUM: 2}），不携带 per-event 映射。本函数无法精确反查
    单 event 的等级（这是 P8 模型当前的设计权衡；要 per-event 精度需扩展模型）。

    **降级策略**：
        - 优先用 max_level（聚合后的最高等级；信息损失最小）
        - 极端缺失（max_level 也异常）→ 用字面量 "N/A"

    注意：消息正文中的"per-event 等级"是辅助展示；
    责任岗位 / due_at / 通道决策等关键决策仍基于 max_level，不受本函数影响。

    Args:
        p8_job: P8_job 实例
        a6_event_id: A6 输出 ID（本参数暂未使用；为未来扩展预留）

    Returns:
        等级字符串（"HIGH" / "MEDIUM" / ...）
    """
    if p8_job.max_level is not None:
        return (
            p8_job.max_level.value
            if isinstance(p8_job.max_level, RiskLevel)
            else str(p8_job.max_level)
        )
    return "N/A"


def _response_time_text(max_level: RiskLevel) -> str:
    """把响应时限（小时）渲染成中文短语（与 § 8.2 模板对齐）。

    Args:
        max_level: 风险等级枚举

    Returns:
        "24 小时内" / "4 小时内" / "1 小时内" / "实时"
    """
    hours = RISK_RESPONSE_TIME_HOURS.get(max_level, 24)
    if hours == 0:
        return "实时"
    return f"{hours} 小时内"


def _render_single_template(p8_job: P8Job, job_id: str) -> Tuple[str, str]:
    """渲染单事件模板（N=1；§ 8.2 第一段）。

    Args:
        p8_job: P8_job 实例（len(a6_event_ids) == 1）
        job_id: 主流程作业 ID

    Returns:
        (title, body) 元组
    """
    prefix = RISK_PUSH_PREFIX.get(p8_job.max_level, "⚪ [未知]")
    title = f"{prefix} A6 风险研判 {p8_job.max_level.value} 级"
    body = (
        f"{title}\n"
        f"{SEPARATOR}\n"
        f"风险事件 ID:    {p8_job.a6_event_ids[0]}\n"
        f"风险描述:       {p8_job.risk_basis}\n"
        f"建议处置动作:   rectify (组织整改)\n"
        f"建议责任岗位:   {p8_job.assignee_role}\n"
        f"处理时限:       {_response_time_text(p8_job.max_level)}\n"
        f"截止时间:       {_format_due_at(p8_job.due_at)} (UTC)\n"
        f"关联任务 ID:    {job_id}\n"
        f"处置 P8_job ID: {p8_job.p8_job_id}\n"
        f"{SEPARATOR}\n"
        f"请相关人员及时处置。"
    )
    return title, body


def _render_aggregate_template(p8_job: P8Job, job_id: str) -> Tuple[str, str]:
    """渲染聚合模板（N>1；§ 8.2 第二段）。

    Args:
        p8_job: P8_job 实例（len(a6_event_ids) > 1）
        job_id: 主流程作业 ID

    Returns:
        (title, body) 元组
    """
    prefix = RISK_PUSH_PREFIX.get(p8_job.max_level, "⚪ [未知]")
    title = f"{prefix} A6 风险研判 风险叠加处置 {p8_job.max_level.value} 级"

    # 逐行渲染各 a6_event
    event_lines: List[str] = []
    for eid in p8_job.a6_event_ids:
        level_str = _level_for_event(p8_job, eid)
        event_lines.append(f"  • {eid}  {level_str}  ...")  # 描述字段留 "..." 占位

    body = (
        f"{title}\n"
        f"{SEPARATOR}\n"
        f"处置 P8_job ID: {p8_job.p8_job_id}\n"
        f"风险叠加事件:\n" + "\n".join(event_lines) + "\n"
        f"聚合等级:       {p8_job.max_level.value} (max)\n"
        f"聚合描述:       {p8_job.risk_basis}\n"
        f"建议处置动作:   escalate (升级至 HSE 经理统一处置)\n"
        f"建议责任岗位:   {p8_job.assignee_role}\n"
        f"处理时限:       {_response_time_text(p8_job.max_level)}\n"
        f"截止时间:       {_format_due_at(p8_job.due_at)} (UTC)\n"
        f"关联任务 ID:    {job_id}\n"
        f"{SEPARATOR}\n"
        f"该 P8_job 合并多个并发风险事件，请一次性决策处置。"
    )
    return title, body


def _build_push_message(p8_job: P8Job, job_id: str) -> PushMessage:
    """构造飞书消息对象（按 N=1 / N>1 走不同模板）。

    这是模板渲染层和 gateway 调用层的边界：
    上层只关心"我要发什么"，gateway 只关心"怎么发"。

    Args:
        p8_job: P8_job 实例
        job_id: 主流程作业 ID

    Returns:
        PushMessage 实例（idempotency_key 已自动填 = p8_job_id）
    """
    if len(p8_job.a6_event_ids) == 1:
        title, body = _render_single_template(p8_job, job_id)
    else:
        title, body = _render_aggregate_template(p8_job, job_id)
    return PushMessage(
        title=title,
        body=body,
        a6_event_ids=list(p8_job.a6_event_ids),
        assignee_role=p8_job.assignee_role,
        risk_level=p8_job.max_level,
        job_id=job_id,
        p8_job_id=p8_job.p8_job_id,
        # idempotency_key 留空 → PushMessage 的 model_validator 自动用 p8_job_id 填
    )


# ============================================================
# 6. 私有辅助函数：USER_MAP 解析（2026-08-13 重构）
# ============================================================

def _warn_legacy_env_once() -> None:
    """检测到旧 FEISHU_CONVERSATION_MAP / FEISHU_GROUP_<ROLE> 时打一次 warning。

    已废弃的环境变量名只在进程启动后第一次调用时打 warning，避免重复刷屏。
    """
    if getattr(_warn_legacy_env_once, "_done", False):
        return
    legacy_seen: List[str] = []
    if os.getenv("FEISHU_CONVERSATION_MAP", "").strip():
        legacy_seen.append("FEISHU_CONVERSATION_MAP")
    for k in os.environ:
        if k.startswith(_LEGACY_GROUP_PREFIX) and os.getenv(k, "").strip():
            legacy_seen.append(k)
    if legacy_seen:
        logger.warning(
            "[p8-notify] 检测到已废弃的飞书收件人配置 %s；"
            "请迁移到 %s（JSON 字符串）。新配置生效后这些变量可删除。",
            legacy_seen, FEISHU_USER_MAP_KEY,
        )
    _warn_legacy_env_once._done = True  # type: ignore[attr-defined]


def get_user_map() -> Dict[str, Dict[str, str]]:
    """从环境变量读取 FEISHU_USER_MAP（原始 dict）。

    Returns:
        {open_id: {"chat_id": "...", "role": "...", "name": "..."}, ...}
        空 dict 表示未配置或解析失败（失败会打 warning）

    Examples:
        >>> os.environ["FEISHU_USER_MAP"] = '{"ou_xxx": {"chat_id": "oc_xxx", "role": "车间主任", "name": "张三"}}'
        >>> get_user_map()
        {'ou_xxx': {'chat_id': 'oc_xxx', 'role': '车间主任', 'name': '张三'}}
    """
    _warn_legacy_env_once()
    raw = os.getenv(FEISHU_USER_MAP_KEY, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[p8-notify] %s 不是合法 JSON：%s；忽略",
            FEISHU_USER_MAP_KEY, exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "[p8-notify] %s 顶层必须是 JSON object（{open_id: {chat_id, role, name}, ...}）",
            FEISHU_USER_MAP_KEY,
        )
        return {}
    # 字段清洗：每条 info 必须是 dict；缺字段用空字符串补
    cleaned: Dict[str, Dict[str, str]] = {}
    for open_id, info in parsed.items():
        if not isinstance(info, dict):
            continue
        cleaned[str(open_id)] = {
            "chat_id": str(info.get("chat_id", "") or "").strip(),
            "role": str(info.get("role", "") or "").strip(),
            "name": str(info.get("name", "") or "").strip(),
        }
    return cleaned


def resolve_recipients(assignee_role: str) -> List[RecipientInfo]:
    """根据责任岗位解析收件人列表（USER_MAP → filter by role）。

    一对多：同一 role 可对应多个收件人（同一岗位有多个责任人时全发）。
    一对一：role 唯一匹配时只返回 1 个。

    Args:
        assignee_role: 责任岗位字符串（来自 P8Job.assignee_role）

    Returns:
        RecipientInfo 列表；空列表表示未匹配（调用方应走 DEV_FALLBACK）

    Examples:
        >>> resolve_recipients("车间主任")
        [RecipientInfo(open_id='ou_alice', chat_id='oc_xxx', name='张三', role='车间主任', receive_id_type='chat_id'),
         RecipientInfo(open_id='ou_bob',   chat_id='',       name='李四', role='车间主任', receive_id_type='open_id')]
    """
    user_map = get_user_map()
    recipients: List[RecipientInfo] = []
    for open_id, info in user_map.items():
        if info.get("role") != assignee_role:
            continue
        chat_id = info.get("chat_id", "")
        receive_id_type = "chat_id" if chat_id else "open_id"
        recipients.append(RecipientInfo(
            open_id=open_id,
            chat_id=chat_id,
            name=info.get("name", ""),
            role=info.get("role", ""),
            receive_id_type=receive_id_type,
        ))
    return recipients


# ============================================================
# 7. 公开 API
# ============================================================

def get_conversation_id(assignee_role: str) -> str:
    """把责任岗位解析为飞书 conversation_id（公开 API，便于预校验）。

    向后兼容旧 API：返回第一个匹配收件人的 conversation_id（chat_id 或 open_id）。
    多收件人场景请改用 :func:`resolve_recipients`。

    Args:
        assignee_role: 责任岗位字符串

    Returns:
        conversation_id；未配置则返回 dev 占位符（feishu_dev_<role>）

    Examples:
        >>> get_conversation_id("车间主任")   # 已设置 USER_MAP 时
        'oc_xxx'
        >>> get_conversation_id("未知岗位")
        'feishu_dev_未知岗位'   # 占位符（同时打 warning 日志）
    """
    recipients = resolve_recipients(assignee_role)
    if recipients:
        first = recipients[0]
        return first.chat_id or first.open_id
    placeholder = f"{DEV_PLACEHOLDER_PREFIX}{assignee_role}"
    logger.warning(
        "[p8-notify] 未配置责任岗位 '%s' 的飞书收件人；"
        "使用占位符 '%s'。请在 .env 设置 %s 并加上此 role。",
        assignee_role, placeholder, FEISHU_USER_MAP_KEY,
    )
    return placeholder


def _send_to_recipient(
    recipient: RecipientInfo,
    msg: PushMessage,
    metadata: Dict[str, Any],
    *,
    channel: str,
    account_id: str,
    idempotency_key: str,
) -> Tuple[RecipientStatus, Dict[str, Any]]:
    """向单个收件人发送（gateway 单次调用）。

    Args:
        recipient: 收件人信息
        msg: 已渲染好的 PushMessage
        metadata: gateway metadata（审计字段）
        channel: 目标通道
        account_id: 飞书账号 ID
        idempotency_key: 幂等键

    Returns:
        (RecipientStatus, raw_response)
    """
    receive_id = recipient.chat_id if recipient.receive_id_type == "chat_id" else recipient.open_id
    try:
        result = send_message(
            text=msg.body,
            channel=channel,
            account_id=account_id,
            conversation_id=receive_id,
            receive_id_type=recipient.receive_id_type,
            metadata={**metadata, "recipient_open_id": recipient.open_id,
                      "recipient_name": recipient.name},
            idempotency_key=idempotency_key,
        )
    except GatewayError as exc:
        logger.warning(
            "[p8-notify] send_to_recipient 网关业务错误: open_id=%s code=%s message=%s",
            recipient.open_id, exc.code, exc.message,
        )
        return RecipientStatus(
            open_id=recipient.open_id,
            chat_id=receive_id,
            name=recipient.name,
            status="failed",
            error=f"[{exc.code}] {exc.message}",
        ), {"error": {"code": exc.code, "message": exc.message, "status_code": exc.status_code}}
    except Exception as exc:
        logger.exception(
            "[p8-notify] send_to_recipient 异常: open_id=%s err=%s",
            recipient.open_id, exc,
        )
        return RecipientStatus(
            open_id=recipient.open_id,
            chat_id=receive_id,
            name=recipient.name,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        ), {"error": {"code": "EXCEPTION", "message": str(exc)}}

    gateway_status = (result.status or "unknown").lower()
    if gateway_status in ("sent", "sending"):
        return RecipientStatus(
            open_id=recipient.open_id,
            chat_id=receive_id,
            name=recipient.name,
            status="ok",
            message_id=result.platform_message_id,
        ), result.raw or {}
    return RecipientStatus(
        open_id=recipient.open_id,
        chat_id=receive_id,
        name=recipient.name,
        status="failed",
        error=f"gateway intent.status={gateway_status}",
    ), result.raw or {}


def send_feishu(
    p8_job: P8Job,
    job_id: str,
    *,
    channel: str = FEISHU_CHANNEL,
    account_id: str = "default",
    metadata_overrides: Optional[Dict[str, Any]] = None,
) -> FeishuNotifyResult:
    """飞书多收件人推送主入口（channel=PUSH 路径）。

    这是 P8 notify_feishu 工具（未来在 agents/p8_disposition_agent.py 中实现）
    调用的底层入口。本函数已经把 P8_job 翻译成飞书消息 + 调用网关 + 解析回执，
    上层工具只需要决定"要不要发"，不需要关心"怎么发"。

    群发语义：
        - resolve_recipients(assignee_role) 可能返回 0..N 个收件人
        - 0 个 → DEV_FALLBACK（status="failed"，p8_job_id 入审计即可）
        - 1+ 个 → 逐个调 gateway；聚合 status = ok/partial/failed
        - 同一 P8_job 共享 idempotency_key，保证 N 次群发只产生一条消息/人

    失败语义（§ 8.3）：
        飞书推送失败 → 仅记日志 + 返回 status="failed"/"partial"，
        **绝不抛异常**。调用方（LLM）看到失败可自行决定是否重试或换通道。

    Args:
        p8_job: P8_job 实例（聚合或单事件均可；模板自动选择）
        job_id: 主流程作业 ID（如 JOB-20260813-001；非 p8_job_id）
        channel: 目标通道名（默认 "feishu"；调用方通常无需修改）
        account_id: 飞书账号 ID（默认 "default"；多账号场景可指定）
        metadata_overrides: 额外 metadata 字段（可选；
            与默认 metadata 合并；同 key 时覆盖默认）

    Returns:
        FeishuNotifyResult：聚合 status / 首个成功 recipient 的 message_id /
        每个 recipient 单独的 status（recipients 字段）

    Examples:
        >>> from A7.schema.p8_models import P8Job, RiskLevel
        >>> job = P8Job(
        ...     p8_job_id="P8J-20260813-180000-001",
        ...     a6_event_ids=["A6-...-230"],
        ...     risk_basis="可燃气体超标",
        ...     max_level=RiskLevel.HIGH,
        ...     level_distribution={RiskLevel.HIGH: 1},
        ...     urgency_emoji="🟠",
        ...     assignee_role="车间主任",
        ...     due_at="2026-08-13T19:00:00+00:00",
        ...     created_at="2026-08-13T18:00:00+00:00",
        ... )
        >>> result = send_feishu(job, "JOB-20260813-001")
        >>> result.status in ("ok", "partial", "failed", "skipped")
        True
        >>> len(result.recipients) >= 0
        True
    """
    # ---- 0. 入参校验 ----
    if p8_job is None:
        raise FeishuNotifyError("p8_job 不能为空", code="FEISHU_NOTIFY_INVALID_INPUT")
    if not job_id:
        raise FeishuNotifyError("job_id 不能为空", code="FEISHU_NOTIFY_INVALID_INPUT")
    if not p8_job.a6_event_ids:
        raise FeishuNotifyError(
            f"P8_job {p8_job.p8_job_id} 没有关联任何 a6_event_id",
            code="FEISHU_NOTIFY_INVALID_INPUT",
        )

    # ---- 1. 入口日志 ----
    logger.info(
        "[p8-notify] send_feishu 进入: p8_job_id=%s job_id=%s "
        "assignee_role=%s max_level=%s a6_event_count=%d",
        p8_job.p8_job_id, job_id,
        p8_job.assignee_role, p8_job.max_level.value,
        len(p8_job.a6_event_ids),
    )

    # ---- 2. 构造 PushMessage ----
    msg = _build_push_message(p8_job, job_id)

    # ---- 3. 解析收件人列表（USER_MAP → filter by role） ----
    recipients = resolve_recipients(p8_job.assignee_role)

    if not recipients:
        # 兜底：未配置任何收件人
        placeholder = f"{DEV_PLACEHOLDER_PREFIX}{p8_job.assignee_role}"
        logger.warning(
            "[p8-notify] 未配置责任岗位 '%s' 的飞书收件人（%s 为空或无 role 匹配）；"
            "使用占位符 '%s'。请在 .env 设置 %s。",
            p8_job.assignee_role, FEISHU_USER_MAP_KEY, placeholder, FEISHU_USER_MAP_KEY,
        )
        # 占位符也尝试发一次，便于本地冒烟（gateway 会拒收）
        # 这里直接返回 failed，避免污染网关
        result = FeishuNotifyResult(
            status="failed",
            error=f"未配置收件人（{FEISHU_USER_MAP_KEY} 缺少 role='{p8_job.assignee_role}'）",
            p8_job_id=p8_job.p8_job_id,
            recipients=[],
        )
        logger.warning(
            "[p8-notify] send_feishu 响应: p8_job_id=%s status=failed reason=no_recipients",
            p8_job.p8_job_id,
        )
        return result

    # ---- 4. 构造 gateway metadata（审计字段） ----
    metadata: Dict[str, Any] = {
        "p8_job_id": p8_job.p8_job_id,
        "job_id": job_id,
        "risk_level": p8_job.max_level.value,
        "assignee_role": p8_job.assignee_role,
        "channel_decision": "PUSH",
        "a6_event_count": len(p8_job.a6_event_ids),
        "recipient_count": len(recipients),
    }
    if metadata_overrides:
        metadata.update(metadata_overrides)

    # ---- 5. 逐个收件人推送 ----
    statuses: List[RecipientStatus] = []
    first_ok: Optional[RecipientStatus] = None
    first_raw: Dict[str, Any] = {}

    for recipient in recipients:
        status, raw = _send_to_recipient(
            recipient=recipient,
            msg=msg,
            metadata=metadata,
            channel=channel,
            account_id=account_id,
            idempotency_key=msg.idempotency_key,
        )
        statuses.append(status)
        if first_ok is None and status.status == "ok":
            first_ok = status
        if not first_raw:
            first_raw = raw

    # ---- 6. 聚合结果 ----
    n_ok = sum(1 for s in statuses if s.status == "ok")
    n_fail = sum(1 for s in statuses if s.status == "failed")
    if n_ok == len(statuses):
        agg_status = "ok"
    elif n_ok == 0:
        agg_status = "failed"
    else:
        agg_status = "partial"

    # 错误描述：部分失败时拼所有失败原因
    error_msg: Optional[str] = None
    if agg_status in ("failed", "partial"):
        errors = [f"{s.name or s.open_id}: {s.error}" for s in statuses if s.status == "failed"]
        error_msg = "; ".join(errors) if errors else None

    # 取首个成功的字段作为顶层 summary（向后兼容）
    message_id = first_ok.message_id if first_ok else None
    intent_id = None
    receipt_id = None
    replayed = False
    conversation_id = first_ok.chat_id if first_ok else None
    if first_ok and first_raw:
        # 从 raw 中提 intent_id / receipt_id / replayed
        intent = first_raw.get("intent") or {}
        receipt = first_raw.get("receipt") or {}
        intent_id = intent.get("id")
        receipt_id = receipt.get("id")
        replayed = bool(first_raw.get("replayed", False))

    feishu_result = FeishuNotifyResult(
        status=agg_status,
        message_id=message_id,
        intent_id=intent_id,
        receipt_id=receipt_id,
        error=error_msg,
        replayed=replayed,
        conversation_id=conversation_id,
        p8_job_id=p8_job.p8_job_id,
        recipients=statuses,
        raw=first_raw,
    )

    # ---- 7. 出口日志 ----
    log_fn = logger.info if agg_status == "ok" else logger.warning
    log_fn(
        "[p8-notify] send_feishu 响应: p8_job_id=%s status=%s recipients=%d "
        "ok=%d fail=%d first_message_id=%s first_conversation=%s",
        p8_job.p8_job_id, agg_status,
        len(statuses), n_ok, n_fail,
        message_id, conversation_id,
    )
    return feishu_result


# ============================================================
# 8. 公开导出
# ============================================================

__all__ = [
    # 业务常量
    "RISK_DEFAULT_ASSIGNEE",
    "RISK_PUSH_PREFIX",
    "RISK_RESPONSE_TIME_HOURS",
    "SEPARATOR",
    "FEISHU_CHANNEL",
    "DEFAULT_RECEIVE_ID_TYPE",
    "DEV_PLACEHOLDER_PREFIX",
    "FEISHU_USER_MAP_KEY",
    # 异常
    "FeishuNotifyError",
    "FeishuNotifyConfigurationError",
    # 数据类
    "FeishuNotifyResult",
    "RecipientInfo",
    "RecipientStatus",
    # 公开 API
    "send_feishu",
    "get_conversation_id",
    "resolve_recipients",
    "get_user_map",
]