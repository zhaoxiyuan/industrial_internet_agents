"""
Mock 数据库统一导出

本包只包含数据(剧本、配置、字典),不包含播放/调用工具。
工具类(玩家、流、模型)将在后续单独构建。
"""
from .types import (
    # 数据类型
    CVEvent,
    SensorSchedule,
    PositionSchedule,
    CVFrameDetection,
    CVFrameLog,
    SensorReading,
    PositionReading,
    VLResponse,
    # 常量
    SCENARIO_DURATION_SEC,
    DECISION_CYCLE_SEC,
    CV_FPS,
    SENSOR_HZ,
    POSITION_HZ,
    VL_TRIGGER_THRESHOLD_SEC,
    VL_MOCK_LATENCY_SEC,
)

# 场景数据集
from .mock_cv import (
    SCENARIOS_CV,
    get_cv_data,
    get_person_events_at,
    has_violation_in_window,
)

from .mock_sensors import (
    SENSOR_CONFIG,
    SCENARIOS_SENSORS,
    get_sensor_data,
    get_sensor_reading_at,
    get_sensor_status,
    has_any_alarm_in_window,
    list_alarmed_sensors_at,
)

from .mock_positioning import (
    AREAS,
    SCENARIOS_POSITIONING,
    get_positioning_data,
    get_worker_position_at,
    get_worker_area_at,
    is_worker_in_danger_zone,
    list_workers_in_area_at,
    list_absent_workers_in_window,
)

from .mock_vl import (
    VL_RESPONSES,
    VL_BY_TYPE,
    get_vl_response,
    get_responses_by_type,
    list_all_keys,
)

from .mock_work_permit import (
    WORK_PERMIT,
    load_work_permit,
    is_required_ppe,
    is_danger_zone,
    get_worker_info,
)


__all__ = [
    # ============ 类型 ============
    "CVEvent", "SensorSchedule", "PositionSchedule",
    "CVFrameDetection", "CVFrameLog", "SensorReading",
    "PositionReading", "VLResponse",
    # ============ 常量 ============
    "SCENARIO_DURATION_SEC", "DECISION_CYCLE_SEC",
    "CV_FPS", "SENSOR_HZ", "POSITION_HZ",
    "VL_TRIGGER_THRESHOLD_SEC", "VL_MOCK_LATENCY_SEC",
    # ============ CV 数据 ============
    "SCENARIOS_CV", "get_cv_data",
    "get_person_events_at", "has_violation_in_window",
    # ============ 传感器数据 ============
    "SENSOR_CONFIG", "SCENARIOS_SENSORS", "get_sensor_data",
    "get_sensor_reading_at", "get_sensor_status",
    "has_any_alarm_in_window", "list_alarmed_sensors_at",
    # ============ 定位数据 ============
    "AREAS", "SCENARIOS_POSITIONING", "get_positioning_data",
    "get_worker_position_at", "get_worker_area_at",
    "is_worker_in_danger_zone", "list_workers_in_area_at",
    "list_absent_workers_in_window",
    # ============ VL 数据 ============
    "VL_RESPONSES", "VL_BY_TYPE",
    "get_vl_response", "get_responses_by_type", "list_all_keys",
    # ============ 作业票 ============
    "WORK_PERMIT", "load_work_permit",
    "is_required_ppe", "is_danger_zone", "get_worker_info",
]