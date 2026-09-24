"""
EventDeduplicator — A5 agent的事件去重状态机

每个作业人员维护一个状态:
  NO_ACTIVE   → 初始,无活跃事件
  NEW_EPISODE → 新事件刚刚发出
  ACTIVE      → 事件进行中(持续违规)
  COOLDOWN    → 违规结束,等 2 秒确认

约束:agent 每秒调用一次 check(),根据 is_violating 推进状态。

示例(P7 在场景 B 第 8-13s 摘头盔):
  T=7  NO_ACTIVE  + False → NO_ACTIVE  → 无输出
  T=8  NO_ACTIVE  + True  → NEW_EPISODE → 输出 raw_event ★
  T=9  NEW_EPISODE+ True  → ACTIVE      → 无
  T=10 ACTIVE     + True  → ACTIVE      → 无
  ...
  T=13 ACTIVE     + False → COOLDOWN    → 无
  T=14 COOLDOWN   + False → COOLDOWN    → 无
  T=15 COOLDOWN   + False → NO_ACTIVE   → 无(超时)
"""
from typing import Dict, Any
import time


class EventDeduplicator:
    """
    每个 person_id 独立的状态机。
    main_loop每秒调一次 check(),根据 is_violating 推进。
    返回 "new_episode" 表示需要输出新事件。
    """

    # 状态常量
    NO_ACTIVE = "no_active"
    NEW_EPISODE = "new_episode"
    ACTIVE = "active"
    COOLDOWN = "cooldown"

    def __init__(self, cooldown_sec: float = 2.0):
        self.cooldown_sec = cooldown_sec
        # person_id -> 状态 dict
        self._states: Dict[str, Dict[str, Any]] = {}

    def check(self, person_id: str, is_violating: bool) -> str:
        """
        每秒调用。返回当前状态/动作。

        Returns:
            "new_episode" - 应输出新事件
            "ongoing"     - 事件进行中(不输出)
            "cooldown"    - 冷却中(不输出)
            "none"        - 无事件(不输出)
        """
        state = self._states.get(person_id)
        now = time.time()

        # ---- 场景 1:首次评估 → NO_ACTIVE ----
        if state is None:
            if is_violating:
                self._states[person_id] = {
                    "status": self.NEW_EPISODE,
                    "episode_id": f"A5-{person_id}-{int(now)}",
                    "started_at": now,
                    "cooldown_started_at": None,
                }
                return self.NEW_EPISODE
            return self.NONE if hasattr(self, "NONE") else self.NO_ACTIVE

        # ---- 场景 2:已有状态 ----
        cur_status = state["status"]

        if cur_status == self.NEW_EPISODE:
            if is_violating:
                state["status"] = self.ACTIVE
                return self.ACTIVE
            else:
                state["status"] = self.COOLDOWN
                state["cooldown_started_at"] = now
                return self.COOLDOWN

        if cur_status == self.ACTIVE:
            if is_violating:
                return self.ACTIVE
            else:
                state["status"] = self.COOLDOWN
                state["cooldown_started_at"] = now
                return self.COOLDOWN

        if cur_status == self.COOLDOWN:
            if is_violating:
                # 冷却期内又违规 → 视作同一事件
                state["status"] = self.ACTIVE
                state["cooldown_started_at"] = None
                return self.ACTIVE
            else:
                # 检查冷却是否到期
                if now - state["cooldown_started_at"] >= self.cooldown_sec:
                    self._states.pop(person_id, None)
                    return self.NO_ACTIVE
                return self.COOLDOWN

        # 默认
        return self.NO_ACTIVE

    # 为兼容性保留 NONE 别名
    @property
    def NONE(self):
        return self.NO_ACTIVE

    def get_state(self, person_id: str) -> Dict[str, Any]:
        """查看某人的当前状态(供调试)"""
        return self._states.get(person_id, {"status": self.NO_ACTIVE})

    def reset(self, person_id: str = None):
        """重置状态(调试用)"""
        if person_id:
            self._states.pop(person_id, None)
        else:
            self._states.clear()