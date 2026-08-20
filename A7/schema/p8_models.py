"""P8 数据模型（Pydantic）。

定义 P8_job / 消息 / 配置的数据结构；与主设计文档 § 4.2 保持同步。

- 工作记忆中的 P8_job 结构（§ 4.2）：N 个 a6_event_id + 1 个状态 + 1 个通道决策
- 状态机（§ 4.2）：pending → notified → waiting_decision → {completed | rejected | escalated | resumed}
- 终态集合（§ 6.4）：{completed, rejected, escalated, resumed}，P8ArchiveMiddleware 在 status 进入终态时自动归档
- 决策动作（§ 6.1）：approve / reject / escalate / rectify / pause / resume
- 风险等级：LOW / MEDIUM / HIGH / CRITICAL（与 A6 的 risk_event level 对齐）
- 通道决策：HITL（高风险走人工确认）/ PUSH（低风险走飞书直达）
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================================
# Enums
# ============================================================================

class P8JobStatus(str, Enum):
    """P8_job 状态机（§ 4.2 + § 6.4）。

    状态转移：
        pending  ──update_job(notified)──►  notified
                                                │
                                                ├── channel=HITL + hitl_decide()──► waiting_decision
                                                │                                       │
                                                │                                       ├── approve/rectify ──► completed  ┐
                                                │                                       ├── reject          ──► rejected   ┤
                                                │                                       ├── escalate        ──► escalated  ─┼─► 终态
                                                │                                       └── resume          ──► resumed    ┘
                                                │
                                                └── channel=PUSH + notify_feishu()  ──► completed  ──► 终态

    终态（_TERMINAL_STATUSES）：completed / rejected / escalated / resumed
    非终态：pending / notified / waiting_decision
    """
    PENDING = "pending"                       # LLM 已创建 P8_job 但尚未推送/通知
    NOTIFIED = "notified"                     # 已调 notify_feishu() 发出飞书消息
    WAITING_DECISION = "waiting_decision"     # channel=HITL + 已调 hitl_decide()，阻塞等待人工决策
    COMPLETED = "completed"                   # 终态：approve/rectify 后，任务完成
    REJECTED = "rejected"                     # 终态：reject 后，任务驳回
    ESCALATED = "escalated"                   # 终态：escalate 后，升级至 HSE 经理
    RESUMED = "resumed"                       # 终态：resume 后，从暂停恢复


class RiskLevel(str, Enum):
    """风险等级（与 A6 risk_event.level 对齐）。

    通道决策（§ 5.3 业务规则）：
        LOW/MEDIUM → channel=PUSH（飞书消息直达）
        HIGH/CRITICAL → channel=HITL（必须人工确认）

    due_at 计算（§ 5.3 § 4）：
        LOW      → 24h
        MEDIUM   → 4h
        HIGH     → 1h
        CRITICAL → 0h（实时）
    """
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Channel(str, Enum):
    """P8 决策通道（§ 4.2 + § 6.1）。

    HITL：人工 in-the-loop；LLM 调 hitl_decide() 后框架中断，等待人工从 CLI/Chat/API 决策
    PUSH：消息推送；LLM 调 notify_feishu() 后直接发出飞书，等待异步反馈
    """
    HITL = "HITL"
    PUSH = "PUSH"


class ActionType(str, Enum):
    """P8 决策动作（§ 6.1）。

    终结动作（status → 终态 + completed_at 设置）：
        approve  → status=completed
        reject   → status=rejected
        rectify  → status=completed（整改完成）
        escalate → status=escalated（升级）
        resume   → status=resumed（从暂停恢复）
    中间动作（status → 中间态，可继续 confirm）：
        pause    → status=paused（暂存为中间态，文档未列入 P8JobStatus；保留作扩展位）

    实际 P8 状态机中："pause" 不在 P8JobStatus 枚举内，因为 P8 处置阶段不暂停任务；
    保留 ActionType.PAUSE 仅为对齐 `disposition_confirm` 旧接口契约。
    """
    APPROVE = "approve"     # 批准
    REJECT = "reject"       # 驳回
    ESCALATE = "escalate"   # 升级
    RECTIFY = "rectify"     # 整改
    PAUSE = "pause"         # 暂停（保留位）
    RESUME = "resume"       # 恢复


# ============================================================================
# Pydantic 数据模型
# ============================================================================

class P8Job(BaseModel):
    """工作记忆中的 P8_job（N:1，支持风险叠加）。

    字段说明（与主设计文档 § 4.2 严格对齐）：
        p8_job_id           P8 自生成 ID，格式 P8J-YYYYMMDD-HHMMSS-NNN；
                            不复用 a6_event_id，因为一个 P8_job 可能聚合多个 event
        a6_event_ids        该 P8_job 关联的所有 A6 输出 ID（数组，N≥1）；
                            N=1 时为单事件处置；N>1 时为风险叠加
        risk_basis          风险依据描述（拼接各 a6_event 的 basis；聚合 P8_job
                            必须记录"聚合依据"用于追溯，详见 § 5.3 § 6）
        max_level           多个 risk_event 风险等级的最高值；
                            用于通道决策（§ 5.3 § 3）和 due_at 计算（§ 5.3 § 4）
        level_distribution  等级分布（可选），如 {"HIGH": 1, "MEDIUM": 2}；
                            便于审计与追溯叠加构成
        urgency_emoji       紧急程度 emoji（LOW=🟢 / MEDIUM=🟡 / HIGH=🟠 / CRITICAL=🔴）
        assignee_role       默认责任岗位（按 § 5.3 § 2 映射）；
                            实际可由用户在前端手动指定，LLM 按指令执行
        status              P8_job 状态机当前节点（见 P8JobStatus）
        channel             决策通道（HITL / PUSH），决定走哪种决策路径
        decision            决策动作（approve/reject/...）；仅决策到达后填入
        note                自由文本备注（决策者 ID、聚合原因等；
                            行业合规 § 5.3 § 6 要求记录"决策者 ID"）
        due_at              处理时限（ISO8601 UTC）；按 max_level 计算
        created_at          创建时间（ISO8601 UTC）

    业务约束：
        - a6_event_ids 内元素必须互不重复（同 P8_job 不引用同 event 两次）
        - 终态 P8_job 会被 P8ArchiveMiddleware 自动从 working_memory 移到 long_term_memory
          （参见 A7/storage/p8_long_term.py 与 § 6.4）
    """

    # ===== 标识字段 =====
    p8_job_id: str = Field(
        ...,  # 必填
        pattern=r"^P8J-\d{8}-\d{6}-\d{3}$",  # 格式：P8J-YYYYMMDD-HHMMSS-NNN
        description="P8 自生成 ID（格式 P8J-YYYYMMDD-HHMMSS-NNN）",
    )

    # 2026-08-20 新增：主流程作业归属（per-job 持久化 + 按 job 清理依据）
    job_id: Optional[str] = Field(
        default=None,
        description="主流程作业 ID（如 JOB-20260813-001 / 17 位时间戳）；"
                    "Bot 模式 + 无作业上下文场景为 None。",
    )

    # ===== 关联字段 =====
    a6_event_ids: list[str] = Field(
        ...,  # 必填
        min_length=1,  # 至少 1 个 event；空数组无意义
        description="该 P8_job 关联的所有 A6 输出 ID（数组，N≥1）",
    )

    # ===== 风险描述字段 =====
    risk_basis: str = Field(
        ...,
        min_length=1,
        description="风险依据描述（聚合 P8_job 必须记录聚合依据）",
    )
    max_level: RiskLevel = Field(
        ...,
        description="多个 risk_event 风险等级的最高值（用于决策/due_at）",
    )
    level_distribution: dict[RiskLevel, int] = Field(
        default_factory=dict,  # 可选；空 dict 表示单事件
        description="等级分布（可选），如 {HIGH: 1, MEDIUM: 2}",
    )
    urgency_emoji: str = Field(
        ...,
        description="紧急程度 emoji（LOW=🟢 / MEDIUM=🟡 / HIGH=🟠 / CRITICAL=🔴）",
    )

    # ===== 责任与通道字段 =====
    assignee_role: str = Field(
        ...,
        description="默认责任岗位（按 § 5.3 § 2 映射）",
    )
    channel: Optional[Channel] = Field(
        default=None,  # 创建 P8_job 时未必已确定通道；update_job 时才设
        description="决策通道（HITL/PUSH），决定走哪种决策路径",
    )

    # ===== 状态与决策字段 =====
    status: P8JobStatus = Field(
        default=P8JobStatus.PENDING,  # 默认初始状态
        description="P8_job 状态机当前节点",
    )
    decision: Optional[ActionType] = Field(
        default=None,
        description="决策动作（approve/reject/...）；仅决策到达后填入",
    )
    note: str = Field(
        default="",
        description="自由文本备注（决策者 ID、聚合原因等）",
    )

    # ===== 时间字段（ISO8601 UTC） =====
    due_at: str = Field(
        ...,
        description="处理时限（ISO8601 UTC）",
    )
    created_at: str = Field(
        ...,
        description="创建时间（ISO8601 UTC）",
    )

    # ===== 业务约束校验 =====
    @field_validator("a6_event_ids")
    @classmethod
    def check_no_duplicates(cls, v: list[str]) -> list[str]:
        """a6_event_ids 内部互不重复（同 P8_job 不引用同 event 两次）。

        Args:
            v: 候选 a6_event_ids 列表

        Returns:
            校验通过的列表（不变）

        Raises:
            ValueError: 存在重复元素时
        """
        if len(v) != len(set(v)):
            raise ValueError("a6_event_ids must be unique within a P8_job")
        return v


class P8JobUpdate(BaseModel):
    """update_job 工具的入参模型（§ 6.1）。

    LLM 调用 update_job(...) 时传此结构；支持新建（p8_job_id 为 None 时新建）
    或更新（p8_job_id 指定时按 ID 更新）。

    字段说明：
        a6_event_ids    必填：至少 1 个 event；新建时所有 event 关联到新 P8_job
        p8_job_id       可选：None → 新建；已存在 → 更新该 P8_job
        status          可选：要变更的目标状态（pending/notified/waiting_decision/...）
        channel         可选：要设置的通道（HITL/PUSH）
        note            可选：要追加的备注（覆盖式）

    说明：
        - decision 不在此：决策由 hitl_decide() 注入，不由 update_job 直接修改
        - max_level / risk_basis 不在此：仅创建时计算，后续不变
    """

    a6_event_ids: list[str] = Field(
        ...,
        min_length=1,
        description="至少 1 个 event；新建/追加到 P8_job",
    )
    p8_job_id: Optional[str] = Field(
        default=None,
        pattern=r"^P8J-\d{8}-\d{6}-\d{3}$",  # None 或符合 P8J 格式
        description="None → 新建；否则按 ID 更新",
    )
    # 2026-08-20 新增：主流程作业归属（与 P8Job.job_id 对齐；per-job 持久化依据）
    job_id: Optional[str] = Field(
        default=None,
        description="主流程作业 ID（如 JOB-20260813-001）；None → Bot 模式或无作业上下文。",
    )
    status: Optional[P8JobStatus] = Field(
        default=None,
        description="要变更的目标状态",
    )
    channel: Optional[Channel] = Field(
        default=None,
        description="要设置的通道",
    )
    note: Optional[str] = Field(
        default=None,
        description="要追加的备注（覆盖式）",
    )

    # ===== 业务约束校验 =====
    @field_validator("a6_event_ids")
    @classmethod
    def check_no_duplicates(cls, v: list[str]) -> list[str]:
        """a6_event_ids 内部互不重复。

        Args:
            v: 候选列表

        Returns:
            校验通过的列表

        Raises:
            ValueError: 存在重复元素时
        """
        if len(v) != len(set(v)):
            raise ValueError("a6_event_ids must be unique within a P8_job update")
        return v


class PushMessage(BaseModel):
    """飞书推送消息模板（§ 8.2）。

    openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py 构造此结构后，调用
    agents/channel_gateway_client.send_message(...) 发出。

    字段说明：
        title           消息标题（含 emoji + 风险等级）
        body            消息正文（单事件 / 聚合两版模板，详见 § 8.2）
        a6_event_ids    关联的 A6 输出 ID（用于消息正文内嵌展示）
        assignee_role   责任岗位（用于 conversation_id 映射）
        risk_level      风险等级（用于标题前缀）
        job_id          主流程作业 ID（如 JOB-20260813-001；非 P8_job ID）
        p8_job_id       P8 自生成的 P8_job ID（与 a6_event_id 区分）
        idempotency_key 幂等键（飞书 gateway 去重）；
                        默认 = p8_job_id，确保同一 P8_job 不会被重复推送
    """

    title: str = Field(
        ...,
        min_length=1,
        description="消息标题（含 emoji + 风险等级）",
    )
    body: str = Field(
        ...,
        min_length=1,
        description="消息正文（单事件 / 聚合两版模板）",
    )
    a6_event_ids: list[str] = Field(
        ...,
        min_length=1,
        description="关联的 A6 输出 ID",
    )
    assignee_role: str = Field(
        ...,
        description="责任岗位（用于 conversation_id 映射）",
    )
    risk_level: RiskLevel = Field(
        ...,
        description="风险等级（用于标题前缀）",
    )
    job_id: str = Field(
        ...,
        description="主流程作业 ID（如 JOB-20260813-001）",
    )
    p8_job_id: str = Field(
        ...,
        pattern=r"^P8J-\d{8}-\d{6}-\d{3}$",
        description="P8 自生成的 P8_job ID",
    )
    idempotency_key: str = Field(
        default="",  # 默认空字符串；构造时按 p8_job_id 自动填
        description="幂等键（默认 = p8_job_id）",
    )

    @model_validator(mode="after")
    def fill_default_idempotency_key(self) -> "PushMessage":
        """idempotency_key 为空时自动用 p8_job_id 填充（model 级 after validator）。

        原因：pydantic v2 对使用默认值（未显式传入）的字段**不会触发** @field_validator，
        故改用 @model_validator(mode='after') 在整个模型构造完成后统一处理，
        保证 idempotency_key = "" 时也能正确回填。

        Returns:
            self（链式调用）
        """
        if not self.idempotency_key and self.p8_job_id:
            self.idempotency_key = self.p8_job_id
        return self