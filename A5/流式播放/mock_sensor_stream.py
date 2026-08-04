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

from demo.mock_data import (
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

    def __init__(self, scenario: str):
        """现实时间 1 秒 = 场景 1 秒,不支持倍速"""
        if scenario not in SCENARIOS_SENSORS:
            raise ValueError(
                f"未知场景: {scenario},可选: {sorted(SCENARIOS_SENSORS.keys())}"
            )

        self.scenario = scenario
        self.sensor_ids = sorted(SCENARIOS_SENSORS[scenario].keys())
        self._total_seconds = int(SCENARIO_DURATION_SEC * SENSOR_HZ)   # 30

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
        return {
            "scenario_time": f"{scenario_time:.1f}s",
            "wall_time":     datetime.now().isoformat(timespec="milliseconds"),
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
    print(f"  MockSensorStream 流式播放")
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
        description="MockSensorStream 流式播放传感器读数"
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