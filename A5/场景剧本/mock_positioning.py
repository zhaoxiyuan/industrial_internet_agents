"""
Mock 定位数据库(仅数据,不含流式播放工具)

设计原则:
- 动火作业现场 3 个作业人员(P7/P8/P11)的实时位置轨迹
- 每个 PositionSchedule 描述某人在 [start_sec, end_sec] 内的位置
- 危险作业区(danger_zones)由作业票定义,定位用于判断是否在区域内

场景对照:
    A: 全员在动火区-A 不动
    B: 全员在动火区-A 不动(违规仅 PPE)
    C: 全员在动火区-A 不动(违规仅传感器)
    D: P11 在 T=8s 走向休息室,T=15s 到达并停留
    E: P7 摘头盔 + P11 在 T=12s 离岗(双重违规)
"""
from typing import Dict, List
from .types import PositionSchedule


# ============================================================
# 区域定义(动火作业区 + 周边设施)
# ============================================================

AREAS: Dict[str, Dict] = {
    "动火区-A": {
        "area_id": "动火区-A",
        "area_type": "危险作业区",
        "is_danger_zone": True,
        "center": [102.0, 89.0],
        "bounds": [[100.0, 87.0], [108.0, 92.0]],   # [[x_min, y_min], [x_max, y_max]]
    },
    "休息室": {
        "area_id": "休息室",
        "area_type": "辅助设施",
        "is_danger_zone": False,
        "center": [150.0, 120.0],
        "bounds": [[148.0, 118.0], [152.0, 122.0]],
    },
    "装置外": {
        "area_id": "装置外",
        "area_type": "厂区道路",
        "is_danger_zone": False,
        "center": [200.0, 50.0],
        "bounds": None,
    },
}


# ============================================================
# 场景 A:全员在动火区
# ============================================================
SCENARIO_A_POSITIONING: Dict[str, List[PositionSchedule]] = {
    "P7": [
        PositionSchedule(0.0, 30.0,
                         position=[102.45, 88.30],
                         area_id="动火区-A", speed=0.0),
    ],
    "P8": [
        PositionSchedule(0.0, 30.0,
                         position=[105.20, 88.10],
                         area_id="动火区-A", speed=0.0),
    ],
    "P11": [
        PositionSchedule(0.0, 30.0,
                         position=[100.10, 90.50],
                         area_id="动火区-A", speed=0.2),
    ],
}


# ============================================================
# 场景 B:全员在动火区(违规仅 PPE)
# ============================================================
SCENARIO_B_POSITIONING: Dict[str, List[PositionSchedule]] = {
    "P7": [
        PositionSchedule(0.0, 30.0,
                         position=[102.45, 88.30],
                         area_id="动火区-A", speed=0.0),
    ],
    "P8": [
        PositionSchedule(0.0, 30.0,
                         position=[105.20, 88.10],
                         area_id="动火区-A", speed=0.0),
    ],
    "P11": [
        PositionSchedule(0.0, 30.0,
                         position=[100.10, 90.50],
                         area_id="动火区-A", speed=0.2),
    ],
}


# ============================================================
# 场景 C:全员在动火区(违规仅传感器)
# ============================================================
SCENARIO_C_POSITIONING: Dict[str, List[PositionSchedule]] = {
    "P7": [
        PositionSchedule(0.0, 30.0,
                         position=[102.45, 88.30],
                         area_id="动火区-A", speed=0.0),
    ],
    "P8": [
        PositionSchedule(0.0, 30.0,
                         position=[105.20, 88.10],
                         area_id="动火区-A", speed=0.0),
    ],
    "P11": [
        PositionSchedule(0.0, 30.0,
                         position=[100.10, 90.50],
                         area_id="动火区-A", speed=0.2),
    ],
}


# ============================================================
# 场景 D:监护人 P11 离岗
# ============================================================
SCENARIO_D_POSITIONING: Dict[str, List[PositionSchedule]] = {
    "P7": [
        PositionSchedule(0.0, 30.0,
                         position=[102.45, 88.30],
                         area_id="动火区-A", speed=0.0),
    ],
    "P8": [
        PositionSchedule(0.0, 30.0,
                         position=[105.20, 88.10],
                         area_id="动火区-A", speed=0.0),
    ],
    "P11": [
        # 0-8s:在动火区正常监护
        PositionSchedule(0.0, 8.0,
                         position=[100.10, 90.50],
                         area_id="动火区-A", speed=0.0),
        # 8-15s:走向休息室(直线移动,7 秒走 50 米 → 1.4 m/s)
        PositionSchedule(8.0, 15.0,
                         position=[150.00, 120.00],
                         area_id="休息室", speed=1.4),
        # 15-30s:在休息室停留
        PositionSchedule(15.0, 30.0,
                         position=[150.00, 120.00],
                         area_id="休息室", speed=0.0),
    ],
}


# ============================================================
# 场景 E:多重违规(P7 摘头盔 + P11 离岗)
# ============================================================
SCENARIO_E_POSITIONING: Dict[str, List[PositionSchedule]] = {
    "P7": [
        PositionSchedule(0.0, 30.0,
                         position=[102.45, 88.30],
                         area_id="动火区-A", speed=0.0),
    ],
    "P8": [
        PositionSchedule(0.0, 30.0,
                         position=[105.20, 88.10],
                         area_id="动火区-A", speed=0.0),
    ],
    "P11": [
        # 0-12s:在岗(比 P7 摘头盔晚 4 秒开始离岗)
        PositionSchedule(0.0, 12.0,
                         position=[100.10, 90.50],
                         area_id="动火区-A", speed=0.0),
        # 12-19s:走向休息室
        PositionSchedule(12.0, 19.0,
                         position=[150.00, 120.00],
                         area_id="休息室", speed=1.4),
        # 19-30s:在休息室
        PositionSchedule(19.0, 30.0,
                         position=[150.00, 120.00],
                         area_id="休息室", speed=0.0),
    ],
}


# ============================================================
# 场景注册表
# ============================================================
SCENARIOS_POSITIONING: Dict[str, Dict[str, List[PositionSchedule]]] = {
    "A": SCENARIO_A_POSITIONING,
    "B": SCENARIO_B_POSITIONING,
    "C": SCENARIO_C_POSITIONING,
    "D": SCENARIO_D_POSITIONING,
    "E": SCENARIO_E_POSITIONING,
}


# ============================================================
# 数据查询辅助函数
# ============================================================

def get_positioning_data(scenario: str) -> Dict[str, List[PositionSchedule]]:
    """获取指定场景的定位数据"""
    if scenario not in SCENARIOS_POSITIONING:
        raise ValueError(f"未知场景: {scenario},可选: {list(SCENARIOS_POSITIONING.keys())}")
    return SCENARIOS_POSITIONING[scenario]


def get_worker_position_at(scenario: str, worker_id: str, t_sec: float) -> PositionSchedule:
    """
    获取某工人在指定时间点的位置。
    t 超出范围时,返回最后一个 schedule。
    """
    schedules = get_positioning_data(scenario).get(worker_id, [])
    if not schedules:
        raise ValueError(f"场景 {scenario} 中无工人 {worker_id}")
    for sch in schedules:
        if sch.start_sec <= t_sec < sch.end_sec:
            return sch
    return schedules[-1]


def get_worker_area_at(scenario: str, worker_id: str, t_sec: float) -> str:
    """获取某工人在指定时间点所在的区域 ID"""
    return get_worker_position_at(scenario, worker_id, t_sec).area_id


def is_worker_in_danger_zone(scenario: str, worker_id: str, t_sec: float,
                             danger_zones: List[str]) -> bool:
    """检查某工人某时刻是否在危险作业区内"""
    area = get_worker_area_at(scenario, worker_id, t_sec)
    return area in danger_zones


def list_workers_in_area_at(scenario: str, area_id: str, t_sec: float) -> List[str]:
    """获取某时刻在指定区域的所有工人 ID"""
    positioning = get_positioning_data(scenario)
    result = []
    for worker_id in positioning.keys():
        if get_worker_area_at(scenario, worker_id, t_sec) == area_id:
            result.append(worker_id)
    return result


def list_absent_workers_in_window(scenario: str, worker_ids: List[str],
                                  start_sec: float, end_sec: float,
                                  danger_zones: List[str]) -> List[str]:
    """
    检查某时间段内有哪些工人离开了危险作业区。
    返回在该时间段内"曾经不在危险区"的工人 ID 列表。
    """
    absent_workers = []
    positioning = get_positioning_data(scenario)
    for worker_id in worker_ids:
        if worker_id not in positioning:
            continue
        for sch in positioning[worker_id]:
            # 取时间段交集
            overlap_start = max(sch.start_sec, start_sec)
            overlap_end = min(sch.end_sec, end_sec)
            if overlap_end <= overlap_start:
                continue
            # 在交集时段内不在危险区
            if sch.area_id not in danger_zones:
                if worker_id not in absent_workers:
                    absent_workers.append(worker_id)
                break
    return absent_workers