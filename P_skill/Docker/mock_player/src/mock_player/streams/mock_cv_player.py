import asyncio
import time
from datetime import datetime
from typing import AsyncIterator, Dict, Any, List
from mock_player.scenario_data import SCENARIOS_CV, get_person_events_at, scenario_duration, sample_available, CV_FPS

class MockCVPlayer:
    """
    模拟 CV 模型,按 25 FPS 持续产出 PPE 检测日志。

    输入:场景 A-E 的 CVEvent 时间表(已定义在 demo/mock_data/mock_cv.py)
    输出:异步生成器,每帧为每个在场人员生成一条检测日志
    """
    CONFIDENCE_OFFSETS = {'helmet': 0.0, 'goggles': -0.03, 'protective_suit': +0.02}

    def __init__(self, scenario: str):
        """
        Args:
            scenario: 场景 ID,A/B/C/D/E
            注:现实时间 1 秒 = 场景 1 秒(实时,不支持倍速)
        """
        if scenario not in SCENARIOS_CV:
            raise ValueError(f'未知场景: {scenario},可选: {sorted(SCENARIOS_CV.keys())}')
        self.scenario = scenario
        self.person_ids = sorted(SCENARIOS_CV[scenario].keys())
        self._total_frames = int(scenario_duration(scenario) * CV_FPS)

    def person_ids_at(self, t):
        return [pid for pid in self.person_ids if sample_available(self.scenario,'cv',pid,t)]

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def total_logs(self) -> int:
        """整场总日志数(总帧数 × 人数)"""
        if self.scenario == 'F':
            return sum(len(self.person_ids_at(n / CV_FPS)) for n in range(self._total_frames))
        return self._total_frames * len(self.person_ids)

    def _make_frame_log(self, person_id: str, scenario_time: float) -> Dict[str, Any]:
        """
        单人单帧的检测日志。

        输入:人员 ID + 场景时间
        处理:
          1. 查 get_person_events_at → 拿到该时刻的 CVEvent
          2. 把 CVEvent 展开为 3 个 detection(每个 PPE 类别一条)
          3. 应用置信度偏移
        输出:组装好的帧日志 dict
        """
        event = get_person_events_at(self.scenario, person_id, scenario_time)
        base_conf = event.confidence
        detections = []
        for class_name, value in [('helmet', event.helmet), ('goggles', event.goggles), ('protective_suit', event.protective_suit)]:
            offset = self.CONFIDENCE_OFFSETS[class_name]
            conf = max(0.0, min(1.0, base_conf + offset))
            detections.append({'class_name': class_name, 'value': value, 'confidence': round(conf, 3)})
        return {'frame_id': f'cam_03_{scenario_time:.3f}', 'scenario_time': f'{scenario_time:.3f}s', 'wall_time': datetime.now().isoformat(timespec='milliseconds'), 'person_id': person_id, 'detections': detections}

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """
        异步生成器:按 25 FPS 实时产出检测日志。

        现实时间 1 秒 = 场景 1 秒,每帧间隔 40ms(1/25)。
        """
        for frame_no in range(self._total_frames):
            scenario_time = frame_no / CV_FPS
            await asyncio.sleep(1.0 / CV_FPS)
            for person_id in self.person_ids_at(scenario_time):
                yield self._make_frame_log(person_id, scenario_time)
