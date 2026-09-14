"""P8 卡片按钮回调专用 Agent（2026-08-20 新增）。

============================================================
设计要点（与 P8 处置 Agent 解耦的轻量决策 Agent）
============================================================

- **单一工具** ``apply_card_action``：slim system_prompt + 单决策工具
- **不复用 P8Agent**：避免重 system_prompt、避免 LLM 递归调 notify_feishu、决策可审计
- **显式 ACTION_TO_STATUS 映射表**（代码即文档）：覆盖所有按钮 action
- **写状态走 dump_working_memory**（per-job JSON 持久化）+ 终态走 save_archived_job 双写
- **不使用 langgraph checkpointer**（fire-and-forget 单次 invoke；不持久化内存状态）
- **HITL middleware 不启用**（卡片回调异步链路上，再走 HITL 等于"再点一次"）

调用链路：

::

    feishu_card.process_card_callback
      └── _handle_card_action_with_llm_async (daemon)
           └── run_card_action_agent(message, user_ctx, job_id)
                └── apply_card_action (tool, closure-bound job_id)
                     ├── load_working_memory(job_id)      读 per-job 列表
                     ├── 计算新 status / decision
                     ├── dump_working_memory(job_id, ...)  原子写 per-job JSON
                     └── 终态 → save_archived_job(..., job_id)  全局+per-job 双写

入口函数：

- :func:`create_card_action_agent`：构造/缓存 Agent 实例
- :func:`run_card_action_agent`：invoke 一次（fire-and-forget）
- :func:`make_apply_card_action_tool`：为指定 job_id 工厂化工具（closure 绑 job_id）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage
from langchain_core.tools import InjectedToolCallId, tool

from langchain.agents import create_agent

from A7.schema import P8JobStatus
from A7.storage import dump_working_memory, load_working_memory, save_archived_job

from agents.model.chat_model import create_chat_model_with_logging
from agents.utils.response_utils import make_error, make_response
from agents.utils.system_prompt import load_system_prompt

logger = logging.getLogger("a7.p8_card_action_agent")

# ============================================================
# Action → P8 状态机映射（代码即文档，覆盖所有按钮 action）
# ============================================================
# PUSH 卡片按钮（channel=PUSH，低风险）：
#   ack         → 已知悉；维持 notified（人在跟进）
#   handle      → 立即处理；维持 notified（人在跟）
#   false_alarm → 误报；终态 completed
# HITL 决策按钮（channel=HITL，高风险）：
#   approve / rectify → completed
#   reject           → rejected
#   escalate         → escalated
#   resume           → resumed
ACTION_TO_STATUS: Dict[str, str] = {
    # PUSH 三按钮
    "ack":         P8JobStatus.NOTIFIED.value,    # 已知悉：维持 notified
    "handle":      P8JobStatus.NOTIFIED.value,    # 立即处理：维持 notified
    "false_alarm": P8JobStatus.COMPLETED.value,   # 误报：终态
    # HITL 五按钮
    "approve":     P8JobStatus.COMPLETED.value,
    "rectify":     P8JobStatus.COMPLETED.value,
    "reject":      P8JobStatus.REJECTED.value,
    "escalate":    P8JobStatus.ESCALATED.value,
    "resume":      P8JobStatus.RESUMED.value,
}

# action → decision 字段映射（None = 不写 decision；PUSH 三按钮通常不写）
ACTION_TO_DECISION: Dict[str, Optional[str]] = {
    "ack":         None,
    "handle":      None,
    "false_alarm": "approve",    # 误报视同"批准关闭"
    "approve":     "approve",
    "rectify":     "rectify",
    "reject":      "reject",
    "escalate":    "escalate",
    "resume":      "resume",
}

_TERMINAL_STATUSES = {
    P8JobStatus.COMPLETED.value,
    P8JobStatus.REJECTED.value,
    P8JobStatus.ESCALATED.value,
    P8JobStatus.RESUMED.value,
}

_VALID_STATUSES = {s.value for s in P8JobStatus}


# ============================================================
# 工具工厂：make_apply_card_action_tool(job_id)
# ============================================================
# 用 closure 把 job_id 绑进工具；类似 update_job 工具的工厂模式。
# 工具本身不接收 job_id 参数（避免 LLM 误传别的 job_id 覆盖正确归属）。

def make_apply_card_action_tool(job_id: Optional[str]) -> Any:
    """工厂化 apply_card_action 工具（闭包绑 job_id）。

    Args:
        job_id: 主流程作业 ID。card callback 反查后从 action.value.job_id 传入；
                缺省时工具仅做"按 alert_id 改全局工作记忆"近似处理（向后兼容测试）。

    Returns:
        LangChain Tool 实例（Callable + .name + .description）
    """
    @tool(description=(
        "Apply P8_job state change triggered by Feishu card button click. "
        "The LLM should pick the right `status` / `decision` / `note` based on "
        "the button `action` (ack/handle/false_alarm/approve/reject/escalate/rectify/resume) "
        "and the current P8_job context. If the P8_job is already in a terminal "
        "status (completed/rejected/escalated/resumed), the tool auto-skips. "
        "Required: alert_id (= p8_job_id), action, operator_open_id, operator_name."
    ))
    def apply_card_action(
        alert_id: str,
        action: str,
        operator_open_id: str,
        operator_name: str,
        note: Optional[str] = None,
        new_status: Optional[str] = None,
    ) -> str:
        """Apply P8_job state change triggered by Feishu card button click.

        Args:
            alert_id: Business alert ID; equals p8_job_id (passed through Feishu
                card value JSON).
            action: Button action verb (ack/handle/false_alarm/approve/reject/
                escalate/rectify/resume).
            operator_open_id: Operator Feishu open_id (from callback payload).
            operator_name: Operator display name (from callback payload).
            note: LLM-inferred note appended to P8_job.note.
            new_status: Optional explicit status (LLM override); defaults to
                ACTION_TO_STATUS[action] mapping.

        Returns:
            JSON string of standard response (status=ok) or error
            (status=error, code=INVALID_ARGUMENT).
        """
        # 1. 校验 alert_id
        if not alert_id or not isinstance(alert_id, str) or not alert_id.strip():
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=f"alert_id={alert_id!r} is empty or invalid",
                recoverable=False,
            ), ensure_ascii=False)
        clean_alert_id = alert_id.strip()

        # 2. 校验 action
        if not action or action not in ACTION_TO_STATUS:
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=(
                    f"unknown action={action!r}; "
                    f"valid: {sorted(ACTION_TO_STATUS.keys())}"
                ),
                recoverable=False,
            ), ensure_ascii=False)
        clean_action = action.strip()

        # 3. 校验 job_id（path traversal 兜底）
        if job_id is not None:
            import re as _re
            if not _re.match(r"^[A-Za-z0-9_-]+$", job_id):
                return json.dumps(make_error(
                    code="INVALID_ARGUMENT",
                    message=f"job_id={job_id!r} has illegal chars",
                    recoverable=False,
                ), ensure_ascii=False)

        # 4. 读 working_memory（per-job JSON；job_id 为 None 时读全局为空）
        if job_id:
            try:
                working = load_working_memory(job_id)
            except Exception as exc:
                logger.exception(
                    "[apply_card_action] load_working_memory(%s) failed: %s",
                    job_id, exc,
                )
                return json.dumps(make_error(
                    code="STORAGE_ERROR",
                    message=f"load_working_memory failed: {exc}",
                    recoverable=True,
                ), ensure_ascii=False)
        else:
            working = []  # job_id 缺失时跳过 per-job 路径

        # 5. 定位目标 P8_job
        target = next(
            (j for j in working
             if isinstance(j, dict) and j.get("p8_job_id") == clean_alert_id),
            None,
        )
        if target is None:
            return json.dumps(make_error(
                code="P8_JOB_NOT_FOUND",
                message=(
                    f"P8_job pid={clean_alert_id!r} not found in job_id={job_id!r}"
                    f" (working_memory size={len(working)})"
                ),
                recoverable=False,
            ), ensure_ascii=False)

        # 6. 已是终态 → skip（保留原状态）
        if target.get("status") in _TERMINAL_STATUSES:
            logger.info(
                "[apply_card_action] skip: terminal pid=%s job=%s status=%s",
                clean_alert_id, job_id, target.get("status"),
            )
            return json.dumps(make_response("apply_card_action", {
                "skipped": True,
                "reason": "terminal_status",
                "current_status": target.get("status"),
                "alert_id": clean_alert_id,
            }), ensure_ascii=False)

        # 7. 计算新状态（LLM 显式指定优先；否则按映射表）
        resolved_status = (new_status or ACTION_TO_STATUS[clean_action]).strip()
        if resolved_status not in _VALID_STATUSES:
            return json.dumps(make_error(
                code="INVALID_ARGUMENT",
                message=f"illegal status={resolved_status!r}",
                recoverable=False,
            ), ensure_ascii=False)

        # 8. 构造 updated P8_job（保留所有原字段）
        updated = {**target, "status": resolved_status}
        decision_value = ACTION_TO_DECISION.get(clean_action)
        if decision_value:
            updated["decision"] = decision_value
        if note and note.strip():
            orig_note = str(target.get("note") or "")
            updated["note"] = (
                f"{orig_note} | card_action={clean_action} "
                f"by {operator_name or 'unknown'}({operator_open_id or '?'}): {note.strip()}"
            ).strip(" |")
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 9. reducer-style 更新 working_memory list
        new_working = [
            updated if (isinstance(j, dict) and j.get("p8_job_id") == clean_alert_id) else j
            for j in working
        ]

        # 10. 落盘 per-job JSON（dump_working_memory 内部 portalocker 兜底）
        if job_id:
            ok = dump_working_memory(job_id, new_working)
            if not ok:
                logger.warning(
                    "[apply_card_action] dump_working_memory failed pid=%s job=%s",
                    clean_alert_id, job_id,
                )

        # 11. 终态 → 双写到长期记忆
        if resolved_status in _TERMINAL_STATUSES and job_id:
            archived = {
                **updated,
                "job_id": job_id,
                "archived_at": updated["updated_at"],
            }
            try:
                save_archived_job(clean_alert_id, archived, job_id=job_id)
            except Exception as exc:
                logger.warning(
                    "[apply_card_action] save_archived_job failed: %s", exc,
                )

        logger.info(
            "[apply_card_action] job=%s pid=%s action=%s "
            "status %s -> %s by %s",
            job_id, clean_alert_id, clean_action,
            target.get("status"), resolved_status,
            operator_name or "?",
        )

        return json.dumps(make_response("apply_card_action", {
            "skipped": False,
            "alert_id": clean_alert_id,
            "action": clean_action,
            "old_status": target.get("status"),
            "new_status": resolved_status,
            "decision": updated.get("decision"),
            "job_id": job_id,
        }), ensure_ascii=False)

    return apply_card_action


# ============================================================
# user_ctx 注入（与 p8_disposition_agent 一致）
# ============================================================

_USER_CTX_BLOCK_TEMPLATE: str = """

---

## 当前用户身份（由卡片回调链路注入）

- **姓名**：{name}
- **角色**：{role}
- **open_id**：`{open_id}`

请直接用**姓名 + 角色**称呼操作人（如"李宗睿 - 作业负责人"），便于审计追溯。

> ⚠️ 若 `name="未知"` 且 `role="未识别用户"`，说明该用户的 open_id 不在
> .env FEISHU_USER_MAP 中，**严禁推测其姓名/角色**。
"""


def _format_user_context_block(user_ctx: Optional[Dict[str, str]]) -> str:
    """格式化 user_ctx 段（与 p8_disposition_agent._format_user_context_block 同模式）。"""
    if not user_ctx:
        return ""
    name = user_ctx.get("name") or "未知"
    role = user_ctx.get("role") or "未识别用户"
    open_id = user_ctx.get("open_id") or ""
    return _USER_CTX_BLOCK_TEMPLATE.format(name=name, role=role, open_id=open_id)


# ============================================================
# Agent 实例缓存（与 p8_disposition_agent 同模式：cache key 拼 user_ctx + job_id）
# ============================================================

_CARD_ACTION_AGENT_CACHE: Dict[str, Any] = {}


def _card_action_cache_key(
    user_ctx: Optional[Dict[str, str]],
    job_id: Optional[str],
) -> str:
    """构造 CardActionAgent 缓存 key（隔离 P8Agent）。"""
    ctx_json = json.dumps(user_ctx or {}, sort_keys=True, ensure_ascii=False)
    return f"card_action:{ctx_json}:job={job_id or '<none>'}"


def create_card_action_agent(
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,
) -> Any:
    """构造 CardActionAgent 实例（按 user_ctx + job_id 缓存复用）。

    Args:
        user_ctx: 操作人上下文（{name, role, open_id}）
        job_id: 主流程作业 ID（绑进工具的 closure）

    Returns:
        LangChain Agent 实例（无 HITL middleware；无 checkpointer）
    """
    cache_key = _card_action_cache_key(user_ctx, job_id)
    if cache_key in _CARD_ACTION_AGENT_CACHE:
        return _CARD_ACTION_AGENT_CACHE[cache_key]

    llm = create_chat_model_with_logging("P8_CARD_ACTION")
    tools = [make_apply_card_action_tool(job_id=job_id)]
    sys_prompt = (
        load_system_prompt("P8_CARD_ACTION")
        + _format_user_context_block(user_ctx)
    )
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=sys_prompt,
        # 显式无 middleware：无 P8ArchiveMiddleware（apply_card_action 内部已处理归档）
        # 无 HumanInTheLoopMiddleware（卡片回调异步链路不再二次确认）
    )
    _CARD_ACTION_AGENT_CACHE[cache_key] = agent
    logger.info(
        "[p8_card_action_agent] 创建新实例 job=%s ctx=%s",
        job_id, user_ctx.get("open_id") if user_ctx else "<none>",
    )
    return agent


def run_card_action_agent(
    message: str,
    *,
    user_ctx: Optional[Dict[str, str]] = None,
    job_id: Optional[str] = None,
) -> str:
    """运行 CardActionAgent 一次（fire-and-forget；返回 LLM 最终输出）。

    Args:
        message: 形如 ``[card_click] alert_id=... action=... ...`` 的合成用户消息
        user_ctx: 操作人上下文
        job_id: 主流程作业 ID

    Returns:
        LLM 最终 AI 消息内容（str）。失败抛异常由 caller 捕获（不阻断 toast）。
    """
    agent = create_card_action_agent(user_ctx=user_ctx, job_id=job_id)
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    # 沿用 p8_disposition_agent 的 extract_output 模式
    from agents.utils.agent_utils import extract_output
    return extract_output(result)


__all__ = [
    "ACTION_TO_STATUS",
    "ACTION_TO_DECISION",
    "make_apply_card_action_tool",
    "create_card_action_agent",
    "run_card_action_agent",
]