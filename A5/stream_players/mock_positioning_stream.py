"""
MockPositioningStream - 把 PositionSchedule 展开为 1 Hz 定位流

用法:
    python mock_positioning_stream.py --scenario D --speed 1.0
    python mock_positioning_stream.py --scenario D --speed 5.0 --max-readings 10
"""
import asyncio
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenario_data import (
    AREAS,
    SCENARIOS_POSITIONING,
    get_worker_position_at,
    SCENARIO_DURATION_SEC,
    POSITION_HZ,
)


class MockPositioningStream:
    """
    模拟定位数据流,按 1 Hz 持续产出工人位置。

    每秒产出一个 dict,内含 3 个工人(P7/P8/P11)的当前位置。
    """

    def __init__(self, scenario: str, start_wall: datetime = None):
        """
        现实时间 1 秒 = 场景 1 秒,不支持倍速。

        Args:
            scenario: 场景 ID
            start_wall: 场景开始的 wall_time（datetime）。用于与 AsyncCollector
                        的 _start_wall 保持一致。
        """
        if scenario not in SCENARIOS_POSITIONING:
            raise ValueError(
                f"未知场景: {scenario},可选: {sorted(SCENARIOS_POSITIONING.keys())}"
            )

        self.scenario = scenario
        self.worker_ids = sorted(SCENARIOS_POSITIONING[scenario].keys())
        self._total_seconds = int(SCENARIO_DURATION_SEC * POSITION_HZ)   # 30
        self._start_wall = start_wall  # 用于生成 log.wall_time，与 collector 对齐

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
            positions[worker_id] = {
                "position":          list(sch.position),
                "area_id":           sch.area_id,
                "area_type":         area_def.get("area_type", "未知"),
                "speed":             sch.speed,
                "is_in_danger_zone": area_def.get("is_danger_zone", False),
            }
        # wall_time: 使用与 collector._start_wall 对齐的时间基准
        if self._start_wall:
            from datetime import timedelta
            log_wall_time = (self._start_wall + timedelta(seconds=second + 0.5)).isoformat(timespec="milliseconds")
        else:
            log_wall_time = datetime.now().isoformat(timespec="milliseconds")

        return {
            "scenario_time": f"{scenario_time:.1f}s",
            "wall_time":     log_wall_time,
            "positions":     positions,
        }

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """异步生成器:按 1 Hz 持续产出定位读数"""
        scenario_start_wall = time.time()

        for second in range(self._total_seconds):
            await asyncio.sleep(1.0 / POSITION_HZ)  # 实时 1 秒
            yield self._make_reading(second)


# ============================================================
# 流式打印(独立运行入口)
# ============================================================

async def stream_print(scenario: str, max_readings: int = None):
    stream = MockPositioningStream(scenario)

    print("=" * 78)
    print(f"  MockPositioningStream stream_players")
    print(f"  场景: {scenario}  |  |  工人: {stream.worker_ids}")
    print(f"  总读数: {stream.total_readings}")
    print("=" * 78)

    count = 0
    try:
        async for reading in stream.stream():
            count += 1
            t = reading["scenario_time"]

            print(f"\n[T={t}]")
            for worker_id, p in reading["positions"].items():
                zone_mark = "[危险区]" if p["is_in_danger_zone"] else "[普通区]"
                pos_str = f"[{p['position'][0]:.1f}, {p['position'][1]:.1f}]"
                print(
                    f"  {worker_id}: {pos_str} | "
                    f"区域={p['area_id']} {zone_mark} | "
                    f"速度={p['speed']:.1f}m/s"
                )

            if max_readings and count >= max_readings:
                print(f"\n[达到 max_readings={max_readings},提前停止]")
                break

    except asyncio.CancelledError:
        print("\n[用户中断]")

    print("\n" + "=" * 78)
    print(f"  [OK] 播放完成,共 {count} 条定位读数")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description="MockPositioningStream stream_players定位数据"
    )
    parser.add_argument(
        "--scenario", default="D",
        choices=["A", "B", "C", "D", "E"],
        help="场景 ID(默认 D)",
    )

    parser.add_argument(
        "--max-readings", type=int, default=None,
        help="最大读数(测试用,默认跑完全部 30 条)",
    )
    args = parser.parse_args()

    asyncio.run(stream_print(args.scenario, args.max_readings))


if __name__ == "__main__":
    main()