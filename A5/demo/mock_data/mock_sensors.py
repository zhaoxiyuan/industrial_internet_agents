"""
Mock 传感器数据库(仅数据,不含流式播放工具)

设计原则:
- 动火作业现场的关键传感器:可燃气体、温度、火焰探测器
- 每个传感器配置:类型、单位、阈值
- 场景通过 SensorSchedule 时间表定义 30 秒内的读数变化
- 数据格式供后续 MockSensorStream 展开为 1 Hz 的连续读数

场景对照:
    A: 全部正常
    B: 全部正常(违规仅来自 PPE)
    C: 可燃气体 0.2→0.4→0.7→1.0(alarm)→0.3 完整升降
    D: 全部正常(违规来自定位)
    E: 气体上升 + 温度轻微上升(与 P7 摘头盔、P11 离岗叠加)
"""
from typing import Dict, List
from .types import SensorSchedule


# ============================================================
# 传感器配置(固定,不随场景变化)
# ============================================================

SENSOR_CONFIG: Dict[str, Dict] = {
    "gas_detector_03": {
        "sensor_id": "gas_detector_03",
        "sensor_type": "可燃气体浓度",
        "unit": "%LEL",
        "threshold_warning": 0.7,        # 警戒线
        "threshold_alarm": 1.0,          # 报警线
        "location": "动火区-A 东侧 3 米",
    },
    "temperature_03": {
        "sensor_id": "temperature_03",
        "sensor_type": "环境温度",
        "unit": "°C",
        "threshold_warning": 35.0,
        "threshold_alarm": 45.0,
        "location": "动火区-A 中央",
    },
    "flame_detector_03": {
        "sensor_id": "flame_detector_03",
        "sensor_type": "火焰探测",
        "unit": "bool",
        "threshold_warning": None,
        "threshold_alarm": None,
        "location": "动火区-A 北侧",
    },
}


# ============================================================
# 场景 A:全部正常
# ============================================================
SCENARIO_A_SENSORS: Dict[str, List[SensorSchedule]] = {
    "gas_detector_03": [
        SensorSchedule(0.0, 30.0, value=0.2, status="normal"),
    ],
    "temperature_03": [
        SensorSchedule(0.0, 30.0, value=26.0, status="normal"),
    ],
    "flame_detector_03": [
        SensorSchedule(0.0, 30.0, value=False, status="normal"),
    ],
}


# ============================================================
# 场景 B:全部正常(违规仅 PPE)
# ============================================================
SCENARIO_B_SENSORS: Dict[str, List[SensorSchedule]] = {
    "gas_detector_03": [
        SensorSchedule(0.0, 30.0, value=0.2, status="normal"),
    ],
    "temperature_03": [
        SensorSchedule(0.0, 30.0, value=26.0, status="normal"),
    ],
    "flame_detector_03": [
        SensorSchedule(0.0, 30.0, value=False, status="normal"),
    ],
}


# ============================================================
# 场景 C:可燃气体完整升降曲线
# ============================================================
SCENARIO_C_SENSORS: Dict[str, List[SensorSchedule]] = {
    "gas_detector_03": [
        # 0-8s:基线正常
        SensorSchedule(0.0, 8.0, value=0.2, status="normal"),
        # 8-13s:缓慢上升,达警戒线
        SensorSchedule(8.0, 13.0, value=0.4, status="normal"),
        # 13-18s:超过警戒线
        SensorSchedule(13.0, 18.0, value=0.7, status="warning"),
        # 18-22s:达到报警线!
        SensorSchedule(18.0, 22.0, value=1.0, status="alarm"),
        # 22-30s:采取措施后回落
        SensorSchedule(22.0, 30.0, value=0.3, status="normal"),
    ],
    "temperature_03": [
        SensorSchedule(0.0, 30.0, value=26.0, status="normal"),
    ],
    "flame_detector_03": [
        SensorSchedule(0.0, 30.0, value=False, status="normal"),
    ],
}


# ============================================================
# 场景 D:全部正常(违规来自定位)
# ============================================================
SCENARIO_D_SENSORS: Dict[str, List[SensorSchedule]] = {
    "gas_detector_03": [
        SensorSchedule(0.0, 30.0, value=0.2, status="normal"),
    ],
    "temperature_03": [
        SensorSchedule(0.0, 30.0, value=26.0, status="normal"),
    ],
    "flame_detector_03": [
        SensorSchedule(0.0, 30.0, value=False, status="normal"),
    ],
}


# ============================================================
# 场景 E:气体上升 + 温度轻微上升(多重叠加)
# ============================================================
SCENARIO_E_SENSORS: Dict[str, List[SensorSchedule]] = {
    "gas_detector_03": [
        # 0-10s:正常
        SensorSchedule(0.0, 10.0, value=0.2, status="normal"),
        # 10-15s:开始上升(比 P7 摘头盔晚 2 秒)
        SensorSchedule(10.0, 15.0, value=0.5, status="normal"),
        # 15-20s:警戒
        SensorSchedule(15.0, 20.0, value=0.8, status="warning"),
        # 20-30s:持续报警(整个违规后期一直高)
        SensorSchedule(20.0, 30.0, value=1.1, status="alarm"),
    ],
    "temperature_03": [
        SensorSchedule(0.0, 15.0, value=26.0, status="normal"),
        # 温度随气体上升略升(模拟叠加效应)
        SensorSchedule(15.0, 30.0, value=32.0, status="normal"),
    ],
    "flame_detector_03": [
        # 20s 后出现异常火焰(违章作业叠加导致)
        SensorSchedule(0.0, 20.0, value=False, status="normal"),
        SensorSchedule(20.0, 30.0, value=True, status="alarm"),
    ],
}


# ============================================================
# 场景注册表
# ============================================================
SCENARIOS_SENSORS: Dict[str, Dict[str, List[SensorSchedule]]] = {
    "A": SCENARIO_A_SENSORS,
    "B": SCENARIO_B_SENSORS,
    "C": SCENARIO_C_SENSORS,
    "D": SCENARIO_D_SENSORS,
    "E": SCENARIO_E_SENSORS,
}


# ============================================================
# 数据查询辅助函数
# ============================================================

def get_sensor_data(scenario: str) -> Dict[str, List[SensorSchedule]]:
    """获取指定场景的传感器数据"""
    if scenario not in SCENARIOS_SENSORS:
        raise ValueError(f"未知场景: {scenario},可选: {list(SCENARIOS_SENSORS.keys())}")
    return SCENARIOS_SENSORS[scenario]


def get_sensor_reading_at(scenario: str, sensor_id: str, t_sec: float) -> SensorSchedule:
    """
    获取某传感器在指定时间点的读数。
    t 超出范围时,返回最后一个 schedule(代表延续状态)。
    """
    schedules = get_sensor_data(scenario).get(sensor_id, [])
    if not schedules:
        raise ValueError(f"场景 {scenario} 中无传感器 {sensor_id}")
    for sch in schedules:
        if sch.start_sec <= t_sec < sch.end_sec:
            return sch
    return schedules[-1]


def get_sensor_status(scenario: str, sensor_id: str, t_sec: float) -> str:
    """获取某传感器在指定时间点的状态(normal/warning/alarm)"""
    reading = get_sensor_reading_at(scenario, sensor_id, t_sec)
    return reading.status


def has_any_alarm_in_window(scenario: str,
                             start_sec: float,
                             end_sec: float) -> bool:
    """检查某时间段内是否有任何传感器达到 alarm 状态"""
    sensors = get_sensor_data(scenario)
    for sensor_id, schedules in sensors.items():
        for sch in schedules:
            overlap_start = max(sch.start_sec, start_sec)
            overlap_end = min(sch.end_sec, end_sec)
            if overlap_end <= overlap_start:
                continue
            if sch.status == "alarm":
                return True
    return False


def list_alarmed_sensors_at(scenario: str, t_sec: float) -> List[str]:
    """获取指定时间点所有处于 alarm 状态的传感器 ID"""
    sensors = get_sensor_data(scenario)
    alarmed = []
    for sensor_id in sensors.keys():
        if get_sensor_status(scenario, sensor_id, t_sec) == "alarm":
            alarmed.append(sensor_id)
    return alarmed