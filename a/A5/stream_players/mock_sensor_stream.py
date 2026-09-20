"""
MockSensorStream - 把 SensorSchedule 展开为 1 Hz 传感器读数流

用法:
    python mock_sensor_stream.py --scenario C --speed 1.0
    python mock_sensor_stream.py --scenario C --speed 5.0 --max-readings 10
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
    SENSOR_CONFIG,
    SCENARIOS_SENSORS,
    get_sensor_reading_at,
    SCENARIO_DURATION_SEC,
    SENSOR_HZ,
)


class MockSensorStream:
    """
    模拟传感器数据流,按 1 Hz 持续产出读数。

    每秒产出一个 dict,内含 3 个传感器(gas/temperature/flame)的当前读数。
    """

    def __init__(self, scenario: str, start_wall: datetime = None):
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
            raise ValueError(
                f"未知场景: {scenario},可选: {sorted(SCENARIOS_SENSORS.keys())}"
            )

        self.scenario = scenario
        self.sensor_ids = sorted(SCENARIOS_SENSORS[scenario].keys())
        self._total_seconds = int(SCENARIO_DURATION_SEC * SENSOR_HZ)   # 30
        self._start_wall = start_wall  # 用于生成 log.wall_time，与 collector 对齐

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
            schedule = get_sensor_reading_at(self.scenario, sensor_id, scenario_time)
            config = SENSOR_CONFIG[sensor_id]
            readings[sensor_id] = {
                "type":             config["sensor_type"],
                "value":            schedule.value,
                "unit":             config["unit"],
                "status":           schedule.status,
                "threshold_warning": config.get("threshold_warning"),
                "threshold_alarm":   config.get("threshold_alarm"),
                "location":          config.get("location"),
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
            "readings":      readings,
        }

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """异步生成器:按 1 Hz 持续产出传感器读数"""
        scenario_start_wall = time.time()

        for second in range(self._total_seconds):
            await asyncio.sleep(1.0 / SENSOR_HZ)  # 实时 1 秒
            yield self._make_reading(second)


# ============================================================
# 流式打印(独立运行入口)
# ============================================================

async def stream_print(scenario: str, max_readings: int = None):
    stream = MockSensorStream(scenario)

    print("=" * 78)
    print(f"  MockSensorStream stream_players")
    print(f"  场景: {scenario}  |  |  传感器: {stream.sensor_ids}")
    print(f"  总读数: {stream.total_readings}")
    print("=" * 78)

    count = 0
    try:
        async for reading in stream.stream():
            count += 1
            t = reading["scenario_time"]

            print(f"\n[T={t}]")
            for sensor_id, r in reading["readings"].items():
                # 状态用 ASCII 标识
                status_mark = {
                    "normal":  "[OK]",
                    "warning": "[WARN]",
                    "alarm":   "[ALARM]",
                }.get(r["status"], "[?]")

                threshold_info = ""
                if r["threshold_alarm"]:
                    threshold_info = f" (warn>={r['threshold_warning']}, alarm>={r['threshold_alarm']})"

                print(
                    f"  {sensor_id} ({r['type']}): "
                    f"{r['value']} {r['unit']} "
                    f"{status_mark}{threshold_info}"
                )

            if max_readings and count >= max_readings:
                print(f"\n[达到 max_readings={max_readings},提前停止]")
                break

    except asyncio.CancelledError:
        print("\n[用户中断]")

    print("\n" + "=" * 78)
    print(f"  [OK] 播放完成,共 {count} 条传感器读数")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description="MockSensorStream stream_players传感器读数"
    )
    parser.add_argument(
        "--scenario", default="C",
        choices=["A", "B", "C", "D", "E"],
        help="场景 ID(默认 C)",
    )

    parser.add_argument(
        "--max-readings", type=int, default=None,
        help="最大读数(测试用,默认跑完全部 30 条)",
    )
    args = parser.parse_args()

    asyncio.run(stream_print(args.scenario, args.max_readings))


if __name__ == "__main__":
    main()