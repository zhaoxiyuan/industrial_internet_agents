"""
A5Agent — 作业过程监测agent

调用模式:main_loop每 1 秒调一次 tick(wall_time, snapshot)。

核心逻辑(每个 person):
  1. 多数表决 (analyze_ppe_compliance)
  2. 事件去重 (EventDeduplicator)
  3. 调 VL    (call_vl_expert)
  4. 收集证据
  5. A5 终判  (不判 risk_level,A6 才判)
  6. 输出 raw_event

约束:
  - 接收 wall_time 后,只能查 wall_time 之前的数据(防未来)
  - 不判 risk_level,不发起处置

输出 raw_event 字段:
  source, event_id, type, person, second, evidence,
  supporting_sources, wall_time, timestamp
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from .tools import (
    set_dependencies,
    analyze_ppe_compliance,
    call_vl_expert,
    query_raw_logs,
    get_current_snapshot,
    ALL_TOOLS,
)
from .event_deduplicator import EventDeduplicator


class A5Agent:
    """A5 作业过程监测agent(main_loop每 1 秒调一次 tick)"""

    # 多数表决阈值(1 秒 25 帧中 ≥ 80% 帧违规)
    MAJORITY_THRESHOLD = 0.8

    def __init__(
        self,
        collector,
        work_permit: Dict[str, Any],
        vl_model=None,
        cooldown_sec: float = 2.0,
        raw_event_dir: Optional[str] = None,
    ):
        """
        Args:
            collector:    AsyncCollector 实例(供工具读取数据)
            work_permit:  作业票字典(必填)
            vl_model:     VL 模型(Mock 或真实),可选
            cooldown_sec: 事件去重冷却期(秒)
            raw_event_dir: raw_event 输出目录,None 则不落盘
        """
        self.collector = collector
        self.work_permit = work_permit
        self.vl_model = vl_model
        self.deduplicator = EventDeduplicator(cooldown_sec=cooldown_sec)
        self.raw_event_dir = Path(raw_event_dir) if raw_event_dir else None

        # 注入依赖到 tools(避免循环引用)
        set_dependencies(collector=collector, vl_model=vl_model, work_permit=work_permit)

        if self.raw_event_dir:
            self.raw_event_dir.mkdir(parents=True, exist_ok=True)

        # 输出文件路径
        self._timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._raw_events: List[Dict] = []

    # ============================================================
    # 主入口:main_loop每 1 秒调一次
    # ============================================================

    async def tick(
        self,
        wall_time: str,
        snapshot: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        处理当前秒的世界快照,返回 0~N 个 raw_event。

        Args:
            wall_time: 当前现实时间(ISO),如 "2026-08-04T14:32:12.000"
            snapshot:  当前秒的世界快照(main_loop传入)
                       {"wall_time", "scenario_time", "cv_logs", "sensors", "positions"}

        Returns:
            List[raw_event],每条:
            {
              "source": "A5",
              "event_id": "A5-P7-1734567890",
              "type": "PPE缺失-头盔",
              "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
              "second": 12,
              "wall_time": "2026-08-04T14:32:12.000",
              "evidence": {
                "video":       {"ratio": 0.88, "frame_count": 22},
                "vl_semantic": {...},
                "sensors":     {...},
                "location":    {...},
                "work_permit": {...},
              },
              "supporting_sources": ["video", "vl_semantic", "location", "work_permit"],
              "explanation": "工人 P7 摘头盔 5 秒,处于动火作业区..."
              "timestamp": "...",
            }
        """
        # ★ 设置 agent 当前时间上限(防止查未来)
        self.collector.set_agent_now(wall_time)

        raw_events: List[Dict] = []
        workers = self.work_permit.get("workers", {})

        for person_id, worker_info in workers.items():
            # ---------- 1. 多数表决 ----------
            # 根据角色决定必检 PPE(监护人不戴护目镜)
            worker_role = worker_info.get("role", "")
            if worker_role == "监护人":
                required_ppe = ["helmet"]   # 监护人只检查头盔
            else:
                required_ppe = self.work_permit.get("required_ppe", None)

            stats = analyze_ppe_compliance.invoke({
                "cv_logs":      snapshot.get("cv_logs", []),
                "person_id":    person_id,
                "threshold":    self.MAJORITY_THRESHOLD,
                "required_ppe": required_ppe,
            })

            is_violating = stats.get("is_violating", False)

            # 监护人特殊判定:位置离开动火区也算违规
            if person_id == "P11":
                pos_data = snapshot.get("positions", [{}])
                if pos_data:
                    p11_pos = pos_data[0].get("positions", {}).get("P11", {})
                    if not p11_pos.get("is_in_danger_zone", True):
                        is_violating = True

            # ---------- 2. 事件去重 ----------
            episode_status = self.deduplicator.check(person_id, is_violating)
            if episode_status != EventDeduplicator.NEW_EPISODE:
                continue

            # ---------- 3. 调 VL(按需)----------
            vl_evidence = await self._call_vl(person_id, stats, snapshot)

            # ---------- 4. 收集证据 ----------
            evidence = self._collect_evidence(person_id, stats, vl_evidence, snapshot)

            # ---------- 5. A5 终判 ----------
            if not self._is_real_violation(evidence):
                continue

            # ---------- 6. 输出 raw_event ----------
            event = self._build_raw_event(
                person_id, worker_info, snapshot, evidence,
                wall_time=wall_time,
            )
            raw_events.append(event)
            self._raw_events.append(event)

        # 落盘(每 tick 落一份累计)
        if self.raw_event_dir and raw_events:
            self._save_raw_events(raw_events, wall_time)

        return raw_events

    # ============================================================
    # 内部:调 VL
    # ============================================================

    async def _call_vl(
        self,
        person_id: str,
        stats: Dict,
        snapshot: Dict,
    ) -> Dict[str, Any]:
        """调 VL 专家(1 轮,够用)"""
        context = self._build_vl_context(person_id, snapshot)
        question = (
            f"图中作业人员 {person_id} 是否有 PPE 违规或危险行为?"
        )

        # 调 VL
        try:
            result = call_vl_expert.invoke({
                "question": question,
                "history":  stats,
                "context":  context,
            })
            return result
        except Exception as e:
            return {
                "is_violation": None,
                "violation_type": "unknown",
                "confidence": 0.0,
                "reasoning": f"VL 调用失败: {e}",
                "_placeholder": True,
            }

    def _build_vl_context(self, person_id: str, snapshot: Dict) -> Dict:
        """构造 VL 上下文"""
        pos_data = snapshot.get("positions", [{}])
        person_pos = {}
        if pos_data:
            person_pos = pos_data[0].get("positions", {}).get(person_id, {})

        sensor_data = snapshot.get("sensors", [{}])
        gas_status = "normal"
        if sensor_data:
            gas_reading = sensor_data[0].get("readings", {}).get("gas_detector_03", {})
            gas_status = gas_reading.get("status", "normal")

        return {
            "person_id":      person_id,
            "work_type":      "动火作业",
            "in_danger_zone": person_pos.get("is_in_danger_zone", False),
            "area_id":        person_pos.get("area_id", "unknown"),
            "gas_status":     gas_status,
            "required_ppe":   self.work_permit.get("required_ppe", []),
        }

    # ============================================================
    # 内部:收集证据
    # ============================================================

    def _collect_evidence(
        self,
        person_id: str,
        stats: Dict,
        vl_evidence: Dict,
        snapshot: Dict,
    ) -> Dict[str, Any]:
        """收集多源证据"""
        pos_data = snapshot.get("positions", [{}])
        person_pos = {}
        if pos_data:
            person_pos = pos_data[0].get("positions", {}).get(person_id, {})

        sensor_data = snapshot.get("sensors", [{}])
        sensor_alarm = False
        if sensor_data:
            for r in sensor_data[0].get("readings", {}).values():
                if r.get("status") == "alarm":
                    sensor_alarm = True
                    break

        evidence = {
            "video":       stats,
            "vl_semantic": vl_evidence,
            "sensors": {
                "alarm": sensor_alarm,
                "raw":   sensor_data[0] if sensor_data else {},
            },
            "location":    person_pos,
            "work_permit": {
                "required_ppe": self.work_permit.get("required_ppe", []),
                "supervisor_required": self.work_permit.get("supervisor_required", False),
            },
        }
        return evidence

    # ============================================================
    # 内部:A5 终判(不判 risk_level)
    # ============================================================

    def _is_real_violation(self, evidence: Dict) -> bool:
        """
        A5 终判规则(只判"是不是违规",不判"多严重"):
        1. CV 多数表决违规 + 至少 1 个其他证据源支持 → 违规
        2. 至少 3 个证据源独立支持 → 违规
        """
        video = evidence["video"]
        vl = evidence["vl_semantic"]
        loc = evidence["location"]
        sensor = evidence["sensors"]

        # 规则 1:VL 确认 + CV 异常
        if vl.get("is_violation") is True and video.get("is_violating"):
            return True

        # 规则 2:CV 异常 + 在危险区 + VL 没否认
        if (video.get("is_violating") and
            loc.get("is_in_danger_zone") and
            vl.get("is_violation") is not False):
            return True

        # 规则 3:CV 异常 + 传感器告警(VL 不可用时降级)
        if (video.get("is_violating") and
            sensor.get("alarm") and
            vl.get("is_violation") is not False):
            return True

        # 规则 4:监护人位置异常(离开危险区)
        worker_info_check = video.get("person_id")
        if worker_info_check and not loc.get("is_in_danger_zone"):
            # 这条已被 tick 阶段处理
            if video.get("is_violating"):
                return True

        return False

    # ============================================================
    # 内部:组装 raw_event
    # ============================================================

    def _build_raw_event(
        self,
        person_id: str,
        worker_info: Dict,
        snapshot: Dict,
        evidence: Dict,
        wall_time: str,
    ) -> Dict[str, Any]:
        """组装 raw_event(A5 输出物,无 risk_level)"""
        sec = self._extract_second(snapshot.get("scenario_time", "0s"))

        # 收集支持证据源
        supporting_sources = []
        if evidence["video"].get("is_violating"):
            supporting_sources.append("video")
        if evidence["vl_semantic"].get("is_violation"):
            supporting_sources.append("vl_semantic")
        if evidence["sensors"].get("alarm"):
            supporting_sources.append("sensor")
        if evidence["location"].get("is_in_danger_zone"):
            supporting_sources.append("location")
        if evidence["work_permit"].get("required_ppe"):
            supporting_sources.append("work_permit")

        # A5 的自然语言解释
        explanation = self._explain(person_id, evidence)

        return {
            "source":      "A5",
            "event_id":    f"A5-{person_id}-{int(time.time()*1000)}",
            "type":        evidence["vl_semantic"].get(
                              "violation_type",
                              "未分类违规"
                          ),
            "person": {
                "id":   person_id,
                "name": worker_info.get("name", person_id),
                "role": worker_info.get("role", "作业人员"),
            },
            "second":  sec,
            "wall_time": wall_time,
            "evidence": evidence,
            "supporting_sources": supporting_sources,
            "explanation": explanation,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "A5 不判定 risk_level,由 A6 完成",
        }

    def _explain(self, person_id: str, evidence: Dict) -> str:
        """
        A5 agent对这次违规的自然语言解释。
        输出格式: "依据 + 结论"
        """
        parts = []
        v = evidence["video"]
        if v.get("is_violating"):
            violations = v.get("violations", [])
            if violations:
                parts.append(
                    f"CV 多数表决显示 {person_id} 在 {v['total_frames']} 帧中"
                    f"有 {int(v['total_frames']*v.get(violations[0]+'_ratio', 0))}"
                    f"帧({v.get(violations[0]+'_ratio', 0):.0%})"
                    f"缺失 {','.join(violations)}"
                )

        vl = evidence["vl_semantic"]
        if vl.get("reasoning"):
            parts.append(f"VL 判断:{vl['reasoning']}")

        loc = evidence["location"]
        if not loc.get("is_in_danger_zone", True):
            parts.append(f"{person_id} 不在危险作业区({loc.get('area_id', '未知')})")
        elif loc.get("is_in_danger_zone"):
            parts.append(f"{person_id} 在危险区({loc.get('area_id', '未知')})")

        sensor = evidence["sensors"]
        if sensor.get("alarm"):
            parts.append("现场传感器触发告警")

        return " | ".join(parts) if parts else "综合多源证据判定违规"

    def _extract_second(self, scenario_time_str: str) -> int:
        try:
            return int(float(scenario_time_str.rstrip("s")))
        except (ValueError, AttributeError):
            return 0

    # ============================================================
    # 落盘
    # ============================================================

    def _save_raw_events(self, events: List[Dict], wall_time: str):
        """把当前 tick 输出的 raw_event 落盘(增量)"""
        if not self.raw_event_dir:
            return

        filename = f"raw_event_{self._timestamp}_{wall_time.replace(':', '').replace('-', '').replace('.', '_')}.json"
        filepath = self.raw_event_dir / filename

        payload = {
            "agent":   "A5",
            "wall_time": wall_time,
            "new_events_count": len(events),
            "events":  events,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 辅助方法
    # ============================================================

    def all_events(self) -> List[Dict]:
        """返回本次运行所有 raw_event"""
        return self._raw_events.copy()

    def summary(self) -> Dict[str, Any]:
        """本次运行的统计摘要"""
        by_person = {}
        by_type = {}
        for ev in self._raw_events:
            by_person[ev["person"]["id"]] = by_person.get(ev["person"]["id"], 0) + 1
            by_type[ev["type"]] = by_type.get(ev["type"], 0) + 1
        return {
            "total_events": len(self._raw_events),
            "by_person":   by_person,
            "by_type":     by_type,
        }