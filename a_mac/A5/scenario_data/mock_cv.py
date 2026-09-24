"""
Mock CV 检测数据库(仅数据,不含播放工具)

设计原则:
- 每个场景定义 30 秒(0~30s)内 3 个作业人员(P7/P8/P11)的 PPE 检测状态时间表
- 每段 CVEvent 表示某人在 [start_sec, end_sec] 期间的检测状态
- 默认状态:helmet=True, goggles=True, protective_suit=True
- 数据格式供后续 MockCVPlayer 展开为 25 FPS 的连续检测流

场景对照:
    A: 正常作业      (基线,0 个候选事件)
    B: 工人摘头盔    (P7 摘 2 次:5s + 3s)
    C: 可燃气体上升  (PPE 全程正常,违规来自传感器)
    D: 监护人离岗    (PPE 全程正常,违规来自定位)
    E: 多重违规      (P7 摘头盔 + 气体上升 + 监护人离岗叠加)
"""
from typing import Dict, List
from .types import CVEvent


# ============================================================
# 场景 A:正常作业(基线)
# ============================================================
SCENARIO_A_CV: Dict[str, List[CVEvent]] = {
    "P7": [
        CVEvent(0.0, 30.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
    ],
    "P8": [
        CVEvent(0.0, 30.0, person_id="P8",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.95),
    ],
    "P11": [
        # 监护人常规配置:不戴护目镜(要观察现场),但戴头盔和防护服
        CVEvent(0.0, 30.0, person_id="P11",
                helmet=True, goggles=False, protective_suit=True,
                confidence=0.91),
    ],
}


# ============================================================
# 场景 B:工人摘头盔(两次违规)
# ============================================================
SCENARIO_B_CV: Dict[str, List[CVEvent]] = {
    "P7": [
        # 0-8s:正常作业
        CVEvent(0.0, 8.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
        # 8-13s:第一次摘头盔,持续 5 秒(超过 3 秒阈值)
        CVEvent(8.0, 13.0, person_id="P7",
                helmet=False, goggles=True, protective_suit=True,
                confidence=0.91),
        # 13-22s:戴回去
        CVEvent(13.0, 22.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
        # 22-25s:第二次摘头盔,持续 3 秒(刚好达阈值)
        CVEvent(22.0, 25.0, person_id="P7",
                helmet=False, goggles=True, protective_suit=True,
                confidence=0.90),
        # 25-30s:戴回去
        CVEvent(25.0, 30.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
    ],
    "P8": [
        CVEvent(0.0, 30.0, person_id="P8",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.95),
    ],
    "P11": [
        CVEvent(0.0, 30.0, person_id="P11",
                helmet=True, goggles=False, protective_suit=True,
                confidence=0.91),
    ],
}


# ============================================================
# 场景 C:可燃气体上升(PPE 全程正常)
# ============================================================
SCENARIO_C_CV: Dict[str, List[CVEvent]] = {
    "P7": [
        CVEvent(0.0, 30.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
    ],
    "P8": [
        CVEvent(0.0, 30.0, person_id="P8",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.95),
    ],
    "P11": [
        CVEvent(0.0, 30.0, person_id="P11",
                helmet=True, goggles=False, protective_suit=True,
                confidence=0.91),
    ],
}


# ============================================================
# 场景 D:监护人离岗(PPE 全程正常)
# ============================================================
SCENARIO_D_CV: Dict[str, List[CVEvent]] = {
    "P7": [
        CVEvent(0.0, 30.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
    ],
    "P8": [
        CVEvent(0.0, 30.0, person_id="P8",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.95),
    ],
    "P11": [
        # 监护人 PPE 全程正常,违规来自定位(去休息室)
        CVEvent(0.0, 30.0, person_id="P11",
                helmet=True, goggles=False, protective_suit=True,
                confidence=0.91),
    ],
}


# ============================================================
# 场景 E:多重违规(同时发生)
# ============================================================
SCENARIO_E_CV: Dict[str, List[CVEvent]] = {
    "P7": [
        # 0-8s:正常
        CVEvent(0.0, 8.0, person_id="P7",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.94),
        # 8-30s:持续摘头盔(整个违规期)
        CVEvent(8.0, 30.0, person_id="P7",
                helmet=False, goggles=True, protective_suit=True,
                confidence=0.91),
    ],
    "P8": [
        CVEvent(0.0, 30.0, person_id="P8",
                helmet=True, goggles=True, protective_suit=True,
                confidence=0.95),
    ],
    "P11": [
        CVEvent(0.0, 30.0, person_id="P11",
                helmet=True, goggles=False, protective_suit=True,
                confidence=0.91),
    ],
}


# ============================================================
# 场景注册表
# ============================================================
SCENARIOS_CV: Dict[str, Dict[str, List[CVEvent]]] = {
    "A": SCENARIO_A_CV,
    "B": SCENARIO_B_CV,
    "C": SCENARIO_C_CV,
    "D": SCENARIO_D_CV,
    "E": SCENARIO_E_CV,
}


# ============================================================
# 数据查询辅助函数(纯函数,不依赖播放工具)
# ============================================================

def get_cv_data(scenario: str) -> Dict[str, List[CVEvent]]:
    """获取指定场景的 CV 数据"""
    if scenario not in SCENARIOS_CV:
        raise ValueError(f"未知场景: {scenario},可选: {list(SCENARIOS_CV.keys())}")
    return SCENARIOS_CV[scenario]


def get_person_events_at(scenario: str, person_id: str, t_sec: float) -> CVEvent:
    """
    获取某人在指定时间点的 CVEvent。
    若 t 超出所有 event 范围,返回最后一个;若没有 event,返回默认值。
    """
    events = get_cv_data(scenario).get(person_id, [])
    if not events:
        return CVEvent(0.0, 30.0, person_id=person_id)
    for ev in events:
        if ev.start_sec <= t_sec < ev.end_sec:
            return ev
    return events[-1]   # 超出范围,返回最后一个状态


def has_violation_in_window(scenario: str, person_id: str,
                            start_sec: float, end_sec: float) -> bool:
    """检查某人在某时间段内是否存在 PPE 违规"""
    events = get_cv_data(scenario).get(person_id, [])
    for ev in events:
        # 取两个时间段的交集
        overlap_start = max(ev.start_sec, start_sec)
        overlap_end = min(ev.end_sec, end_sec)
        if overlap_end <= overlap_start:
            continue
        if not ev.helmet or not ev.goggles or not ev.protective_suit:
            return True
    return False