# P8P9/models.py — 基础数据模型（枚举 + 转换表 + 颜色映射 + 校验常量）
#
# 本模块是 P8P9 的「常量层」，**不 import langchain / 任何 LLM SDK**。
# 所有外部（业务动作 / service / 卡片）只能读，不应修改。
#
# 设计依据：
#   - 文档 §2.0.1「2 agent + 3 service」架构
#   - 文档 §3.3 job_status 状态机定义
#   - 文档 §2.2 卡片标题颜色规则
#   - 文档 §2.5.2/§2.5.3 升级/降级校验常量
#   - 文档 §6.3.1 review comment 最小长度
#   - 文档 §7.2.2 退接次数上限
#
# 严禁：@tool 装饰器 / import langchain / 写副作用函数 / 全局可变状态。

from __future__ import annotations
from typing import Dict, FrozenSet, Optional


# ─── 枚举 ──────────────────────────────────────────────────────────────────────

# §3.3 job_status 全集（7 态；与 doc §2.4 一致）
JOB_STATUSES_NEW: FrozenSet[str] = frozenset({
    "open",
    "acknowledged",
    "rectifying",
    "materials_in_audit",
    "waiting_human_review",
    "ready_to_close",
    "closed",
})

# §3.1 event_status 全集（3 态）
EVENT_STATUSES_NEW: FrozenSet[str] = frozenset({
    "pending",
    "disposing",
    "disposed",
})


# ─── 状态机转移表 ──────────────────────────────────────────────────────────────

# §3.3 job_status 合法转换表
# key = 当前态，value = 允许的目标态集合
# None 表示初始化（从无 → open）
LEGAL_JOB_TRANSITIONS: Dict[Optional[str], FrozenSet[str]] = {
    None: frozenset({"open"}),
    "open": frozenset({"acknowledged"}),
    "acknowledged": frozenset({"rectifying", "open"}),       # rectifying 或退接回 open
    "rectifying": frozenset({"materials_in_audit", "open"}),  # 提交材料 或 退接
    "materials_in_audit": frozenset({"waiting_human_review", "ready_to_close"}),
    "waiting_human_review": frozenset({"closed", "rectifying"}),  # 通过 / 驳回回 rectifying
    "ready_to_close": frozenset({"closed"}),
    "closed": frozenset(),  # 终态；不动
}

# §3.1 event_status 合法转换表
LEGAL_EVENT_TRANSITIONS: Dict[Optional[str], FrozenSet[str]] = {
    None: frozenset({"pending"}),
    "pending": frozenset({"disposing"}),
    "disposing": frozenset({"disposing", "disposed"}),  # 保持 或 完成
    "disposed": frozenset(),
}


# ─── 兼容映射（迁移用） ───────────────────────────────────────────────────────

# 把 A7 老的 event_status 字符串映射到新枚举；用于解析历史数据
LEGACY_EVENT_STATUS_MAP: Dict[str, str] = {
    "disposition_in_progress": "disposing",
    "pending_review": "disposing",
    "ready_to_close": "disposed",
    "waiting_close_confirmation": "disposed",
    "closed": "disposed",
    "suspended": "pending",
    "open": "pending",  # A7 旧代码 open 含义对齐新 pending
}


# ─── 风险等级 → 卡片颜色 ──────────────────────────────────────────────────────

# §2.2 标题颜色公式：job_status == closed → grey；否则按 display_risk_level
RISK_LEVEL_COLOR_MAP: Dict[int, str] = {
    0: "grey",
    1: "blue",
    2: "orange",
    3: "red",
    4: "red",
    5: "carmine",
}

RISK_LEVEL_NAME_MAP: Dict[int, str] = {
    0: "无",
    1: "轻微",
    2: "一般",
    3: "较重",
    4: "严重",
    5: "危急",
}

# 危急前缀（§2.4 卡片标题前缀）
RISK_LEVEL_EMOJI_MAP: Dict[int, str] = {
    0: "⚪",
    1: "🟢",
    2: "🟡",
    3: "🟠",
    4: "🔴",
    5: "🚨",
}


# ─── 校验常量（业务规则） ─────────────────────────────────────────────────────

# §2.5.2 escalate 校验
ESCALATE_REASON_MIN: int = 10
ESCALATE_MAX_DELTA: int = 4  # 单次最多升 N 级（防止 1→5 跳级）

# §2.5.3 downgrade 校验（更严）
DOWNGRADE_REASON_MIN: int = 20
DOWNGRADE_EVIDENCE_MIN: int = 1

# §6.3.1 review comment 校验
REVIEW_COMMENT_MIN: int = 10

# §4.6 relinquish 校验
RELINQUISH_REASON_MIN: int = 10

# §7.2.2 退接次数上限
MAX_RELINQUISH_COUNT: int = 2


# ─── 卡片模板路由 ────────────────────────────────────────────────────────────

# 把 job_status 映射到 build_job_card 内的构造器 key
JOB_STATUS_TO_TEMPLATE: Dict[str, str] = {
    "open": "open",
    "acknowledged": "acknowledged",
    "rectifying": "rectifying",
    "materials_in_audit": "materials_in_audit",
    "waiting_human_review": "waiting_human_review",
    "ready_to_close": "ready_to_close",
    "closed": "closed",
}


# ─── Card 2.0 Schema 常量 ─────────────────────────────────────────────────────

CARD_SCHEMA_VERSION: str = "2.0"
CARD_SEND_THROTTLE_SECONDS: float = 0.2  # §5.6

# 飞书「长记忆归档」 业务标识
ARCHIVE_FINAL_MARKER: str = "archived_to_lt"
ARCHIVE_MAX_RETRY_BACKOFF: int = 60 * 60  # 1 小时持续重试


# ─── 公共 API ────────────────────────────────────────────────────────────────

__all__ = [
    "JOB_STATUSES_NEW",
    "EVENT_STATUSES_NEW",
    "LEGAL_JOB_TRANSITIONS",
    "LEGAL_EVENT_TRANSITIONS",
    "LEGACY_EVENT_STATUS_MAP",
    "RISK_LEVEL_COLOR_MAP",
    "RISK_LEVEL_NAME_MAP",
    "RISK_LEVEL_EMOJI_MAP",
    "ESCALATE_REASON_MIN",
    "ESCALATE_MAX_DELTA",
    "DOWNGRADE_REASON_MIN",
    "DOWNGRADE_EVIDENCE_MIN",
    "REVIEW_COMMENT_MIN",
    "RELINQUISH_REASON_MIN",
    "MAX_RELINQUISH_COUNT",
    "JOB_STATUS_TO_TEMPLATE",
    "CARD_SCHEMA_VERSION",
    "CARD_SEND_THROTTLE_SECONDS",
    "ARCHIVE_FINAL_MARKER",
    "ARCHIVE_MAX_RETRY_BACKOFF",
]