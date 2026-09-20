# P8P9/state_machine.py — 状态机 service（§3.2）
#
# 「2 agent + 3 service」架构中的 **1 个 service**（实际是基础设施层）。
# 不 import langchain，不依赖 LLM，纯 Python 文件 I/O + 内存锁。
#
# 设计依据：
#   - §3.2 状态机定义
#   - §5.3 原子写（tempfile + os.replace + fsync）
#   - §2.0.1 service 边界（与 agent 隔离）
#
# 公开 API：
#   - ClosureService.initialize_job(job_id, *, actor, events=None) -> dict
#   - ClosureService.get_state(job_id) -> dict
#   - ClosureService.set_job_status(job_id, new_status, *, actor, expected_version, **fields) -> dict
#   - ClosureService.set_event_status(job_id, risk_event_id, new_status, *, actor, expected_version) -> dict
#   - ClosureService.list_events(job_id, *, include_closed=False) -> list[dict]
#
# 异常类（业务方捕获）：
#   - StateNotFound
#   - IllegalTransition
#   - VersionConflict
#   - BusinessConsistencyViolation
#   - CardinalityExceeded
#   - InputValidationError
#
# 持久化布局：
#   {base_dir}/{job_id}/closure_state.json
#
# 线程安全：
#   - 全局 _LOCKS[job_id] = threading.Lock() 字典（lazy 创建）。
#   - 所有「读 → 修改 → 写」必须 wrap 在 with _LOCKS[job_id] 内。

from __future__ import annotations
import os
import json
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    LEGAL_JOB_TRANSITIONS,
    LEGAL_EVENT_TRANSITIONS,
    LEGACY_EVENT_STATUS_MAP,
    JOB_STATUSES_NEW,
    EVENT_STATUSES_NEW,
)


# ─── 异常类 ──────────────────────────────────────────────────────────────────

class StateNotFound(Exception):
    """job_id 不存在或未调用 initialize_job。"""


class IllegalTransition(Exception):
    """违反 §3.3 状态机转换规则。message 包含 from_ → to。"""


class VersionConflict(Exception):
    """乐观锁冲突：expected_version != 当前 version。"""


class BusinessConsistencyViolation(Exception):
    """违反业务一致性约束（§7.2）：如 接取人 ≠ 上传人。"""


class CardinalityExceeded(Exception):
    """违反基数约束：如 退接次数 > MAX_RELINQUISH_COUNT。"""


class InputValidationError(Exception):
    """输入字段不合法：如 reason < 10 字。"""


# ─── 状态机 service ──────────────────────────────────────────────────────────

class ClosureService:
    """§3.2 状态机 service。

    线程安全（per-job Lock）；持久化层 = tempfile + os.replace（§5.3）。
    原子写测试：write → 抛错时文件内容不变（旧版未损坏）。

    Example:
        svc = ClosureService()
        svc.initialize_job("JOB-001", actor="P8")
        state = svc.get_state("JOB-001")
        svc.set_job_status("JOB-001", "acknowledged", actor={"open_id": "ou_x"}, expected_version=0)
    """

    # 全局 per-job Lock 池（lazy 创建）。
    # 必须是 RLock（可重入）：business_actions.submit_rectification_materials
    # 会在「外层 _lock_for → 内部 set_job_status」模式下做两阶段 transition
    # （acknowledged → rectifying → materials_in_audit）；set_job_status 内部
    # 也会调 _lock_for。非可重入的 Lock 会导致同一线程自死锁 → 飞书 200341 超时。
    _LOCKS: Dict[str, threading.RLock] = {}
    _LOCKS_GUARD: threading.Lock = threading.Lock()

    def __init__(self, base_dir: str = None) -> None:
        if base_dir is None:
            import os
            # 2026-09-17：与主流程作业同目录（data/jobs/{17位}/closure_state.json），
            # P8P9 状态机不再有独立子目录。job_id 必须使用 17 位 ERP 工单号
            # （如 20260917000000003），保证 P8P9 与主流程（p5/p7_result.json）
            # 作业目录一一对应、绝不重名。
            base_dir = os.environ.get("P8P9_BASE_DIR", "data/jobs")
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _lock_for(self, job_id: str) -> threading.RLock:
        with self._LOCKS_GUARD:
            lock = self._LOCKS.get(job_id)
            if lock is None:
                lock = threading.RLock()
                self._LOCKS[job_id] = lock
            return lock

    def _state_path(self, job_id: str) -> Path:
        job_dir = self.base_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir / "closure_state.json"

    # ─── 公开 API ────────────────────────────────────────────────────────────

    def initialize_job(
        self,
        job_id: str,
        *,
        actor: str,
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """§3.2：None → open。

        Args:
            job_id: 作业 ID（业务方传）
            actor: 创建者标识（通常是 "P8-DispositionAgent"）
            events: 初始 risk_event 列表（来自 P7 mock 数据）

        Returns:
            写入的 state dict（包含 job_status="open" / version=0 / events）

        Raises:
            InputValidationError: job_id 非法字符 / events 含非法字段
        """
        if not _safe_job_id(job_id):
            raise InputValidationError(f"job_id 含非法字符: {job_id!r}")

        events = events or []
        normalized_events = [_normalize_event(e, idx) for idx, e in enumerate(events)]

        now = _now_iso()
        state: Dict[str, Any] = {
            "schema_version": "1.1",
            "job_id": job_id,
            "version": 0,
            "job_status": "open",
            "display_risk_level": _max_risk_level(normalized_events),
            "card_binding": None,
            "accepted_by": None,
            "materials": {"submissions": []},
            "review": {},
            "risk_changes": [],
            "p7_reassessments": [],
            "relinquish_count": {},
            "job_status_history": [
                {"to": "open", "by": actor, "at": now, "from": None}
            ],
            "job_closure_status": "pending",
            "events": normalized_events,
            "confirmations": [],
            "report": {},
            "closure_blockers": [],
            "created_at": now,
            "updated_at": now,
            "archived_to_lt": False,
            "archived_at": None,
            "archive_attempts": 0,
            "links": {},
        }
        # 写入持久化层（不走 set_job_status，因为这是初始化）
        self._atomic_write(job_id, state)
        return state

    def get_state(self, job_id: str) -> Dict[str, Any]:
        """读 closure_state.json；不存在抛 StateNotFound。"""
        path = self._state_path(job_id)
        if not path.exists():
            raise StateNotFound(f"job_id={job_id} 未初始化")
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            raise StateNotFound(f"job_id={job_id} state 文件损坏")

    def list_events(
        self, job_id: str, *, include_closed: bool = False
    ) -> List[Dict[str, Any]]:
        """§2.4.1 折叠展示用的事件清单。include_closed=False 默认隐藏 disposed 事件。"""
        state = self.get_state(job_id)
        events = state.get("events", [])
        if not include_closed:
            events = [e for e in events if e.get("event_status") != "disposed"]
        return events

    def set_job_status(
        self,
        job_id: str,
        new_status: str,
        *,
        actor: Dict[str, Any],
        expected_version: Optional[int] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """§3.2 唯一合法写 job_status 入口。

        校验：
          1. job_status ∈ JOB_STATUSES_NEW
          2. 当前态 → new_status ∈ LEGAL_JOB_TRANSITIONS（§3.3）
          3. expected_version == state.version（乐观锁）
          4. 合并 fields（accepted_by / materials / review / risk_changes ...）

        副作用：
          - 写 job_status_history（追加一条）
          - updated_at = now
          - version += 1
          - 调 _recompute_display_risk_level + _recompute_job_status（兜底）
          - 不直接调 card_render（解耦；card_render 由 business_actions 触发）
        """
        if new_status not in JOB_STATUSES_NEW:
            raise InputValidationError(f"非法 job_status={new_status!r}")

        with self._lock_for(job_id):
            state = self.get_state(job_id)
            current_status = state.get("job_status")
            self._assert_transition(current_status, new_status)

            if expected_version is not None and state.get("version") != expected_version:
                raise VersionConflict(
                    f"job_id={job_id} expected_version={expected_version} "
                    f"!= actual={state.get('version')}"
                )

            now = _now_iso()
            old_status = state.get("job_status")
            state["job_status"] = new_status
            state["version"] = state.get("version", 0) + 1
            state["updated_at"] = now

            # 合并 fields（actor 是 actor dict，fields 是任意业务字段）
            for k, v in fields.items():
                state[k] = v

            # 写历史
            history = state.setdefault("job_status_history", [])
            history.append({
                "from": old_status,
                "to": new_status,
                "by": actor.get("open_id") if isinstance(actor, dict) else actor,
                "by_name": actor.get("name") if isinstance(actor, dict) else None,
                "at": now,
            })

            # 终态触发 archive marker
            if new_status == "closed":
                # 不立即归档；archive 由 business_actions.record_closure_review 触发
                # 这里仅标记 job_closure_status
                state["job_closure_status"] = "closing"

            # 重新计算派生字段（兜底；正常路径已被 business_actions 计算过）
            self._recompute_display_risk_level_inplace(state)

            self._atomic_write(job_id, state)
            return state

    def patch_fields(
        self,
        job_id: str,
        *,
        actor: Dict[str, Any],
        expected_version: Optional[int] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """仅 patch 任意业务字段（不触发状态机转换 / 不写 job_status_history）。

        用于：
          - audit_scheduler 写 review.p9_opinion
          - archive_with_retry 写 archived_to_lt / archived_at
          - relinquish 写 relinquish_count

        与 set_job_status 的区别：
          - 不校验 LEGAL_JOB_TRANSITIONS
          - 不写 job_status_history
          - 不动 job_status
          - 仍走 lock + atomic_write + 乐观锁

        注：agent_interface.bind_card_for_agent 不能直接调本方法（防止 agent 写任意字段）。
        请用 bind_card(...) 入口（仅允许写 card_binding）。

        Raises:
            StateNotFound: job 未初始化
            VersionConflict: expected_version != 当前
        """
        with self._lock_for(job_id):
            state = self.get_state(job_id)

            if expected_version is not None and state.get("version") != expected_version:
                raise VersionConflict(
                    f"job_id={job_id} expected_version={expected_version} "
                    f"!= actual={state.get('version')}"
                )

            now = _now_iso()
            for k, v in fields.items():
                state[k] = v
            state["updated_at"] = now
            state["version"] = state.get("version", 0) + 1
            state.setdefault("_patch_log", []).append({
                "fields": sorted(fields.keys()),
                "by": actor.get("open_id") if isinstance(actor, dict) else actor,
                "by_name": actor.get("name") if isinstance(actor, dict) else None,
                "at": now,
            })

            self._recompute_display_risk_level_inplace(state)

            self._atomic_write(job_id, state)
            return state

    def bind_card(
        self,
        job_id: str,
        *,
        actor: Dict[str, Any],
        binding: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """白名单写入：仅允许写 card_binding 字段。

        agent_interface.bind_card_for_agent 唯一可用的写入入口。
        不能写 risk_changes / materials / review / accepted_by / relinquish_count 等业务字段。

        若 binding 缺关键字段（chat_id），抛 InputValidationError。
        若当前已绑定到相同 chat_id，则返回 idempotent 结果，不递增 version。
        """
        if not isinstance(binding, dict) or not binding.get("chat_id"):
            raise InputValidationError(
                "bind_card.binding 必须为 dict 且含 chat_id"
            )

        with self._lock_for(job_id):
            state = self.get_state(job_id)

            # 幂等：已绑定到相同 chat_id → 直接返回
            existing = state.get("card_binding") or {}
            if existing.get("chat_id") == binding["chat_id"]:
                return {
                    "job_id": job_id,
                    "chat_id": binding["chat_id"],
                    "group_name": existing.get("group_name") or binding.get("group_name"),
                    "account_id": existing.get("account_id") or binding.get("account_id"),
                    "bound_by": existing.get("bound_by", ""),
                    "bound_at": existing.get("bound_at", ""),
                    "status": "exists",
                    "version": state.get("version"),
                }

            # 乐观锁（仅当调用方传 expected_version 时校验）
            if expected_version is not None and state.get("version") != expected_version:
                raise VersionConflict(
                    f"job_id={job_id} expected_version={expected_version} "
                    f"!= actual={state.get('version')}"
                )

            now = _now_iso()
            state["card_binding"] = {
                "card_id": binding.get("card_id"),
                "alert_id": binding.get("alert_id"),
                "chat_id": binding["chat_id"],
                "group_name": binding.get("group_name"),
                "account_id": binding.get("account_id"),
                "bound_at": now,
                "bound_by": actor.get("open_id") if isinstance(actor, dict) else actor,
                "risk_event_id": binding.get("risk_event_id"),
            }
            state["updated_at"] = now
            state["version"] = state.get("version", 0) + 1
            state.setdefault("_patch_log", []).append({
                "fields": ["card_binding"],
                "by": actor.get("open_id") if isinstance(actor, dict) else actor,
                "by_name": actor.get("name") if isinstance(actor, dict) else None,
                "at": now,
            })

            self._atomic_write(job_id, state)
            return {
                "job_id": job_id,
                "chat_id": binding["chat_id"],
                "group_name": binding.get("group_name"),
                "account_id": binding.get("account_id"),
                "bound_by": actor.get("open_id") if isinstance(actor, dict) else actor,
                "bound_at": now,
                "status": "bound",
                "version": state.get("version"),
            }

    def set_event_status(
        self,
        job_id: str,
        risk_event_id: str,
        new_status: str,
        *,
        actor: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """§3.1 event 级状态变更。

        校验：
          1. new_status ∈ EVENT_STATUSES_NEW
          2. event 存在
          3. event_status → new_status ∈ LEGAL_EVENT_TRANSITIONS
          4. 乐观锁（job 级 version）

        写后调 _recompute_job_status_inplace（重新派生 job_status）。
        """
        if new_status not in EVENT_STATUSES_NEW:
            raise InputValidationError(f"非法 event_status={new_status!r}")

        with self._lock_for(job_id):
            state = self.get_state(job_id)

            if expected_version is not None and state.get("version") != expected_version:
                raise VersionConflict(
                    f"job_id={job_id} expected_version={expected_version} "
                    f"!= actual={state.get('version')}"
                )

            event = _find_event(state, risk_event_id)
            if event is None:
                raise InputValidationError(
                    f"job_id={job_id} 不存在 risk_event_id={risk_event_id}"
                )

            old_status = event.get("event_status", "pending")
            self._assert_event_transition(old_status, new_status)

            now = _now_iso()
            event["event_status"] = new_status
            event["updated_at"] = now
            if new_status == "disposed":
                event["closed_at"] = now
                event["closed_by"] = actor.get("open_id") if isinstance(actor, dict) else actor

            state["updated_at"] = now
            state["version"] = state.get("version", 0) + 1

            # 重新派生 job_status + display_risk_level
            self._recompute_display_risk_level_inplace(state)
            self._recompute_job_status_inplace(state)

            self._atomic_write(job_id, state)
            return state

    # ─── 私有：派生计算 ──────────────────────────────────────────────────────

    def _recompute_display_risk_level_inplace(self, state: Dict[str, Any]) -> None:
        """§2.2：max(events[*].risk_level)。events 为空 → 0。"""
        state["display_risk_level"] = _max_risk_level(state.get("events", []))

    def _recompute_job_status_inplace(self, state: Dict[str, Any]) -> None:
        """§3.1 兜底：所有 event disposed 且当前 closed → 保持 closed。
        其它情况不主动改 job_status（由 business_actions 显式触发）。
        """
        # 仅用作"防止 events 全 disposed 但 job_status 仍是非 closed 态"的兜底
        events = state.get("events", [])
        if not events:
            return
        all_disposed = all(e.get("event_status") == "disposed" for e in events)
        current = state.get("job_status")
        # 这里只是统计快照；不改 job_status（避免意外覆盖业务流）
        state.setdefault("_derived", {})["all_events_disposed"] = all_disposed
        state["_derived"]["pending_event_count"] = sum(
            1 for e in events if e.get("event_status") != "disposed"
        )

    # ─── 私有：断言 ──────────────────────────────────────────────────────────

    def _assert_transition(self, from_: Optional[str], to: str) -> None:
        allowed = LEGAL_JOB_TRANSITIONS.get(from_)
        if allowed is None or to not in allowed:
            raise IllegalTransition(
                f"非法 job_status 转换: {from_!r} → {to!r}; "
                f"allowed from {from_!r} = {sorted(allowed) if allowed else '∅'}"
            )

    def _assert_event_transition(self, from_: Optional[str], to: str) -> None:
        allowed = LEGAL_EVENT_TRANSITIONS.get(from_)
        if allowed is None or to not in allowed:
            raise IllegalTransition(
                f"非法 event_status 转换: {from_!r} → {to!r}; "
                f"allowed from {from_!r} = {sorted(allowed) if allowed else '∅'}"
            )

    # ─── 私有：原子写（§5.3） ────────────────────────────────────────────────

    def _atomic_write(self, job_id: str, state: Dict[str, Any]) -> None:
        """tempfile + os.replace + fsync。

        异常时：原文件保留（旧版未损坏），临时文件清理。
        """
        path = self._state_path(job_id)
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".closure_state_", suffix=".json.tmp",
                dir=str(self.base_dir / job_id),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        # Windows 上 os.fsync 对某些文件类型可能不支持；不致命
                        pass
            finally:
                if tmp_path:
                    # mkstemp 已把 fd 给了 fdopen；fdopen 已关闭
                    pass
            os.replace(tmp_path, path)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise


# ─── 模块级辅助函数 ──────────────────────────────────────────────────────────

def _safe_job_id(job_id: str) -> bool:
    """防 path traversal：仅允许字母 / 数字 / 下划线 / 中划线。"""
    if not job_id or len(job_id) > 128:
        return False
    import re
    return bool(re.match(r"^[A-Za-z0-9_-]+$", job_id))


def _now_iso() -> str:
    """ISO8601 + 时区。"""
    return datetime.now(timezone.utc).isoformat()


def _max_risk_level(events: List[Dict[str, Any]]) -> int:
    """max(events[*].risk_level)；空 → 0。"""
    if not events:
        return 0
    return max(int(e.get("risk_level", 0)) for e in events)


def _find_event(state: Dict[str, Any], risk_event_id: str) -> Optional[Dict[str, Any]]:
    for e in state.get("events", []):
        if e.get("risk_event_id") == risk_event_id:
            return e
    return None


def _normalize_event(event: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """events 列表归一化：补 risk_event_id / event_status / 时间戳。"""
    e = dict(event)  # shallow copy
    e.setdefault("risk_event_id", f"A6-MOCK-{idx:03d}")
    # 兼容旧字段
    legacy_status = e.get("event_status") or e.get("status")
    if legacy_status and legacy_status in LEGACY_EVENT_STATUS_MAP:
        legacy_status = LEGACY_EVENT_STATUS_MAP[legacy_status]
    e["event_status"] = legacy_status or "pending"
    e["risk_level"] = int(e.get("risk_level", 1))
    e.setdefault("created_at", _now_iso())
    e.setdefault("updated_at", _now_iso())
    # 兼容字段名：disposition
    if "disposition" not in e:
        e["disposition"] = {}
    if "review" not in e:
        e["review"] = {}
    if "closure_blockers" not in e:
        e["closure_blockers"] = []
    return e


__all__ = [
    "StateNotFound",
    "IllegalTransition",
    "VersionConflict",
    "BusinessConsistencyViolation",
    "CardinalityExceeded",
    "InputValidationError",
    "ClosureService",
]