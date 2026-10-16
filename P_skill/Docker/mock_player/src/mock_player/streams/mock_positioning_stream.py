import asyncio
import time
from datetime import datetime
from typing import AsyncIterator, Dict, Any
from mock_player.scenario_data import AREAS, SCENARIOS_POSITIONING, get_worker_position_at, scenario_duration, POSITION_HZ

class MockPositioningStream:
    """
    模拟定位数据流,按 1 Hz 持续产出工人位置。

    每秒产出一个 dict,内含 3 个工人(P7/P8/P11)的当前位置。
    """

    def __init__(self, scenario: str, start_wall: datetime=None):
        """
        现实时间 1 秒 = 场景 1 秒,不支持倍速。

        Args:
            scenario: 场景 ID
            start_wall: 场景开始的 wall_time（datetime）。用于与 AsyncCollector
                        的 _start_wall 保持一致。
        """
        if scenario not in SCENARIOS_POSITIONING:
            raise ValueError(f'未知场景: {scenario},可选: {sorted(SCENARIOS_POSITIONING.keys())}')
        self.scenario = scenario
        self.worker_ids = sorted(SCENARIOS_POSITIONING[scenario].keys())
        self._total_seconds = int(scenario_duration(scenario) * POSITION_HZ)
        self._start_wall = start_wall

    @property
    def total_readings(self) -> int:
        return self._total_seconds

    def _make_reading(self, second: int) -> Dict[str, Any]:
        """生成第 N 秒的定位读数"""
        scenario_time = second + 0.5
        positions = {}
        for worker_id in self.worker_ids:
            sch = get_worker_position_at(self.scenario, worker_id, scenario_time)
            area_def = AREAS.get(sch.area_id, {})
            positions[worker_id] = {'position': list(sch.position), 'area_id': sch.area_id, 'area_type': area_def.get('area_type', '未知'), 'speed': sch.speed, 'is_in_danger_zone': area_def.get('is_danger_zone', False)}
        if self._start_wall:
            from datetime import timedelta
            log_wall_time = (self._start_wall + timedelta(seconds=second + 0.5)).isoformat(timespec='milliseconds')
        else:
            log_wall_time = datetime.now().isoformat(timespec='milliseconds')
        return {'scenario_time': f'{scenario_time:.1f}s', 'wall_time': log_wall_time, 'positions': positions}

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """异步生成器:按 1 Hz 持续产出定位读数"""
        scenario_start_wall = time.time()
        for second in range(self._total_seconds):
            await asyncio.sleep(1.0 / POSITION_HZ)
            yield self._make_reading(second)
