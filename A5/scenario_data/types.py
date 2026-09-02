"""
Mock 数据共享类型定义
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


# ============================================================
# CV 检测相关类型
# ============================================================

@dataclass
class CVEvent:
    """
    一段连续时间内的 CV 检测状态定义。

    描述某个作业人员在 [start_sec, end_sec] 时间段内的 PPE 检测状态。
    MockCVPlayer 会按 25 FPS 把这些状态展开成连续的检测日志。
    """
    start_sec: float                                    # 开始时间(秒,相对场景起点)
    end_sec: float                                      # 结束时间
    person_id: str                                      # 人员 ID
    helmet: bool = True                                 # 是否戴头盔
    goggles: bool = True                                # 是否戴护目镜
    protective_suit: bool = True                        # 是否穿防护服
    confidence: float = 0.93                            # 检测置信度(基础值)


# ============================================================
# 传感器相关类型
# ============================================================

@dataclass
class SensorSchedule:
    """
    一段连续时间内的传感器读数定义。

    描述某个传感器在 [start_sec, end_sec] 时间段内的读数状态。
    MockSensorStream 会按 1 Hz 把这些状态展开成连续的传感器数据。
    """
    start_sec: float                                    # 开始时间
    end_sec: float                                      # 结束时间
    value: float                                        # 传感器读数
    status: str = "normal"                              # normal / warning / alarm
    sensor_id: Optional[str] = None                     # 传感器 ID(由 key 注入)
    sensor_type: Optional[str] = None                   # 传感器类型(由 key 注入)
    unit: Optional[str] = None                          # 单位(由 key 注入)
    threshold: Optional[float] = None                   # 警戒阈值(由 key 注入)


# ============================================================
# 定位相关类型
# ============================================================

@dataclass
class PositionSchedule:
    """
    一段连续时间内的定位状态定义。

    描述某个作业人员在 [start_sec, end_sec] 时间段内的位置。
    """
    start_sec: float                                    # 开始时间
    end_sec: float                                      # 结束时间
    position: List[float]                               # [x, y] 坐标(米)
    area_id: str                                        # 所在区域 ID
    speed: float = 0.0                                  # 移动速度(米/秒)
    worker_id: Optional[str] = None                     # 人员 ID(由 key 注入)


# ============================================================
# 单帧 CV 检测输出格式(模拟 YOLO 输出)
# ============================================================

@dataclass
class CVFrameDetection:
    """单帧中一个检测目标的输出"""
    class_name: str                                     # 类别:helmet/goggles/protective_suit
    value: bool                                         # 检测值
    confidence: float                                   # 置信度


@dataclass
class CVFrameLog:
    """
    单帧的 CV 检测日志(每帧一条)。

    MockCVPlayer 每秒产出 25 条这样的日志。
    """
    frame_id: str                                       # 帧唯一标识,如 cam_03_8.040
    timestamp: str                                      # ISO 格式时间戳
    person_id: str                                      # 被检测的人员 ID
    detections: List[CVFrameDetection]                  # 该人员的所有检测项


# ============================================================
# 传感器单次读数(每秒一条)
# ============================================================

@dataclass
class SensorReading:
    """单次传感器读数"""
    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    status: str                                         # normal / warning / alarm
    threshold: Optional[float]
    timestamp: str


# ============================================================
# 定位单次读数(每秒一条)
# ============================================================

@dataclass
class PositionReading:
    """单次定位读数"""
    worker_id: str
    position: List[float]
    area_id: str
    speed: float
    is_in_danger_zone: bool                             # 是否在危险作业区内
    timestamp: str


# ============================================================
# VL 模型响应
# ============================================================

@dataclass
class VLResponse:
    """VL 模型返回的语义理解结果"""
    is_violation: bool
    violation_type: str                                 # PPE缺失-头盔 / 监护人脱岗 / 环境异常 / 无风险
    evidence_description: str
    risk_level: str                                     # low / medium / high / critical
    reasoning: str
    suggested_action: str
    confidence: float


# ============================================================
# 场景元数据
# ============================================================

SCENARIO_DURATION_SEC = 30.0        # 每个场景总时长
DECISION_CYCLE_SEC = 1.0           # 决策周期(已确认改为 1.0 秒)
CV_FPS = 25                        # CV 检测频率
SENSOR_HZ = 1                      # 传感器频率
POSITION_HZ = 1                    # 定位频率
VL_TRIGGER_THRESHOLD_SEC = 3.0     # VL 触发阈值(连续违规 3 秒)
VL_MOCK_LATENCY_SEC = 0.5         # Mock VL 模拟延迟