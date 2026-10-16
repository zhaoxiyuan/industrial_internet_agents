"""
Mock 数据共享类型定义
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class CVEvent:
    """
    一段连续时间内的 CV 检测状态定义。

    描述某个作业人员在 [start_sec, end_sec] 时间段内的 PPE 检测状态。
    MockCVPlayer 会按 25 FPS 把这些状态展开成连续的检测日志。
    """
    start_sec: float
    end_sec: float
    person_id: str
    helmet: bool = True
    goggles: bool = True
    protective_suit: bool = True
    confidence: float = 0.93

@dataclass
class SensorSchedule:
    """
    一段连续时间内的传感器读数定义。

    描述某个传感器在 [start_sec, end_sec] 时间段内的读数状态。
    MockSensorStream 会按 1 Hz 把这些状态展开成连续的传感器数据。
    """
    start_sec: float
    end_sec: float
    value: float
    status: str = 'normal'
    sensor_id: Optional[str] = None
    sensor_type: Optional[str] = None
    unit: Optional[str] = None
    threshold: Optional[float] = None

@dataclass
class PositionSchedule:
    """
    一段连续时间内的定位状态定义。

    描述某个作业人员在 [start_sec, end_sec] 时间段内的位置。
    """
    start_sec: float
    end_sec: float
    position: List[float]
    area_id: str
    speed: float = 0.0
    worker_id: Optional[str] = None

@dataclass
class CVFrameDetection:
    """单帧中一个检测目标的输出"""
    class_name: str
    value: bool
    confidence: float

@dataclass
class CVFrameLog:
    """
    单帧的 CV 检测日志(每帧一条)。

    MockCVPlayer 每秒产出 25 条这样的日志。
    """
    frame_id: str
    timestamp: str
    person_id: str
    detections: List[CVFrameDetection]

@dataclass
class SensorReading:
    """单次传感器读数"""
    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    status: str
    threshold: Optional[float]
    timestamp: str

@dataclass
class PositionReading:
    """单次定位读数"""
    worker_id: str
    position: List[float]
    area_id: str
    speed: float
    is_in_danger_zone: bool
    timestamp: str
SCENARIO_DURATION_SEC = 30.0
CV_FPS = 25
SENSOR_HZ = 1
POSITION_HZ = 1
