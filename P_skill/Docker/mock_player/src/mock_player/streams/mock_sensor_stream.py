import asyncio
import time
from datetime import datetime
from typing import AsyncIterator, Dict, Any
from mock_player.scenario_data import SENSOR_CONFIG, SCENARIOS_SENSORS, get_sensor_reading_at, scenario_duration, sample_available, SENSOR_HZ

class MockSensorStream:
    """
    模拟传感器数据流,按 1 Hz 持续产出读数。

    每秒产出一个 dict,内含 3 个传感器(gas/temperature/flame)的当前读数。
    """

    def __init__(self, scenario: str, start_wall: datetime=None):
        """
        现实时间 1 秒 = 场景 1 秒,不支持倍速。

        Args:
            scenario: 场景 ID
            start_wall: 场景开始的 wall_time（datetime）。用于与 AsyncCollector
                        的 _start_wall 保持一致，确保 sensor log 的 wall_time
                        与 collector 查询时使用的 _start_wall 对齐。
                        如果不传，则使用 datetime.now()（向后兼容）。
        """
        if scenario not in SCENARIOS_SENSORS:
            raise ValueError(f'未知场景: {scenario},可选: {sorted(SCENARIOS_SENSORS.keys())}')
        self.scenario = scenario
        self.sensor_ids = sorted(SCENARIOS_SENSORS[scenario].keys())
        self._total_seconds = int(scenario_duration(scenario) * SENSOR_HZ)
        self._start_wall = start_wall

    @property
    def total_readings(self) -> int:
        return self._total_seconds

    def _make_reading(self, second: int) -> Dict[str, Any]:
        """
        生成第 N 秒的传感器读数(用一个 dict 包含全部 3 个传感器)。

        第二 N 用场景时间 N+0.5(秒中点)查询 SensorSchedule,
        这样在该秒的任意时刻都能拿到正确读数。
        """
        scenario_time = second + 0.5
        readings = {}
        for sensor_id in self.sensor_ids:
            if not sample_available(self.scenario,'sensor',sensor_id,scenario_time):continue
            schedule = get_sensor_reading_at(self.scenario, sensor_id, scenario_time)
            config = SENSOR_CONFIG[sensor_id]
            readings[sensor_id] = {'type': config['sensor_type'], 'value': schedule.value, 'unit': config['unit'], 'status': schedule.status, 'threshold_warning': config.get('threshold_warning'), 'threshold_alarm': config.get('threshold_alarm'), 'location': config.get('location')}
        if self._start_wall:
            from datetime import timedelta
            log_wall_time = (self._start_wall + timedelta(seconds=second + 0.5)).isoformat(timespec='milliseconds')
        else:
            log_wall_time = datetime.now().isoformat(timespec='milliseconds')
        return {'scenario_time': f'{scenario_time:.1f}s', 'wall_time': log_wall_time, 'readings': readings}

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """异步生成器:按 1 Hz 持续产出传感器读数"""
        scenario_start_wall = time.time()
        for second in range(self._total_seconds):
            await asyncio.sleep(1.0 / SENSOR_HZ)
            yield self._make_reading(second)
