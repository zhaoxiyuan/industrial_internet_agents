"""
AsyncCollector - 并行运行 3 个流,内存缓存 + 落盘(单文件/流)

设计(简化版):
- 内存中保留所有原始日志(单 list/流)
- 智能体通过 query_raw_logs() 接口按时间范围查询
- 落盘:每流一个文件,文件名带时间戳(无需 manifest)
  logs/scenario_X_speed_Y_Z/
  ├── cv_20260804_094023.json          ← 全部 CV 日志
  ├── sensor_20260804_094023.json      ← 全部传感器读数
  └── position_20260804_094023.json    ← 全部定位读数
"""
import asyncio
import json
import time
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Protocol


class StreamSource(Protocol):
    """所有数据流的统一接口"""
    scenario: str
    speed: float

    async def stream(self): ...


class AsyncCollector:
    """并行运行 3 个流,内存缓存 + 单文件落盘"""

    def __init__(
        self,
        cv_source: StreamSource,
        sensor_source: StreamSource,
        pos_source: StreamSource,
        log_dir: Optional[str] = None,
    ):
        self.cv_source = cv_source
        self.sensor_source = sensor_source
        self.pos_source = pos_source
        self.log_dir = Path(log_dir) if log_dir else None
        self.speed = getattr(cv_source, "speed", 1.0)
        self.scenario = getattr(cv_source, "scenario", "unknown")

        # ============ 内存中保留全量原始日志 ============
        self._cv_logs: List[Dict] = []
        self._sensor_logs: List[Dict] = []
        self._position_logs: List[Dict] = []

        # ============ 控制 ============
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._start_time: Optional[float] = None

    # ============================================================
    # 生命周期
    # ============================================================

    async def start(self):
        """启动 3 个后台消费任务"""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

        self._tasks = [
            asyncio.create_task(self._consume(self.cv_source, "cv")),
            asyncio.create_task(self._consume(self.sensor_source, "sensor")),
            asyncio.create_task(self._consume(self.pos_source, "position")),
        ]

    async def stop(self):
        """停止所有任务,落盘全部日志"""
        self._running = False
        # 不主动 cancel,让消费任务自然结束(等待流跑完)
        # 给 5 秒兜底超时,防止 demo 主循环已退出但流未跑完的死等
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            # 超时则强制 cancel
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        # 落盘(单文件/流)
        if self.log_dir:
            self._save_to_disk()

    # ============================================================
    # 流消费(简单追加到 list)
    # ============================================================

    async def _consume(self, source: StreamSource, source_type: str):
        """消费一个流,把每条日志追加到对应 list"""
        try:
            async for log in source.stream():
                if source_type == "cv":
                    self._cv_logs.append(log)
                elif source_type == "sensor":
                    self._sensor_logs.append(log)
                elif source_type == "position":
                    self._position_logs.append(log)
        except asyncio.CancelledError:
            pass

    # ============================================================
    # 落盘(单文件/流,文件名带时间戳)
    # ============================================================

    def _save_to_disk(self):
        """所有流的日志落盘为单文件,文件名包含时间戳"""
        if not self.log_dir:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self._cv_logs:
            self._write_file("cv", self._cv_logs, ts)
        if self._sensor_logs:
            self._write_file("sensor", self._sensor_logs, ts)
        if self._position_logs:
            self._write_file("position", self._position_logs, ts)

    def _write_file(self, source_type: str, logs: List[Dict], timestamp: str):
        """写一个 JSON 文件"""
        if not self.log_dir:
            return
        filename = f"{source_type}_{timestamp}.json"
        filepath = self.log_dir / filename

        payload = {
            "scenario":    self.scenario,
            "type":        source_type,
            "created_at":  timestamp,
            "log_count":   len(logs),
            "logs":        logs,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 智能体查询接口(从内存)
    # ============================================================

    def _get_logs(self, source_type: str) -> List[Dict]:
        """获取指定类型的全量日志"""
        return {
            "cv": self._cv_logs,
            "sensor": self._sensor_logs,
            "position": self._position_logs,
        }.get(source_type, [])

    def _extract_second(self, scenario_time_str: str) -> float:
        """从 '8.040s' 提取浮点秒"""
        try:
            return float(scenario_time_str.rstrip("s"))
        except (ValueError, AttributeError):
            return -1.0

    def query_raw_logs(
        self,
        start_sec: float,
        end_sec: float,
        source_type: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        按时间范围查询原始日志(Demo 主要从内存读)。

        Args:
            start_sec:   起始秒(包含)
            end_sec:     结束秒(不包含)
            source_type: "cv" / "sensor" / "position",None=全部
            person_id:   人员 ID 过滤(仅 CV 有效)

        Returns:
            符合条件的日志列表

        用例:
            query_raw_logs(8, 13, "cv", "P7")        # P7 在 8-13s 的 CV 日志
            query_raw_logs(0, 30, "sensor")           # 整个场景的传感器读数
            query_raw_logs(15, 30, "position", "P11")  # P11 在 15s 后的定位
        """
        types = [source_type] if source_type else ["cv", "sensor", "position"]
        results = []
        for stype in types:
            for log in self._get_logs(stype):
                t = self._extract_second(log.get("scenario_time", "0s"))
                if start_sec <= t < end_sec:
                    if person_id and log.get("person_id") != person_id:
                        continue
                    results.append(log)
        return results

    def get_snapshot(self, second: int) -> Dict[str, Any]:
        """获取第 N 秒的世界快照(供智能体决策)"""
        return {
            "scenario_time": f"{second}.0s",
            "cv_logs":       self.query_raw_logs(second, second + 1, "cv"),
            "sensors":       self.query_raw_logs(second, second + 1, "sensor"),
            "positions":     self.query_raw_logs(second, second + 1, "position"),
        }

    def get_latest_snapshot(self) -> Dict[str, Any]:
        """获取最新一"秒"的快照"""
        all_secs = []
        for log in self._cv_logs + self._sensor_logs + self._position_logs:
            all_secs.append(self._extract_second(log.get("scenario_time", "0s")))
        if not all_secs:
            return {"scenario_time": "0.0s", "cv_logs": [], "sensors": [], "positions": []}
        latest_sec = int(max(all_secs))
        return self.get_snapshot(latest_sec)


# ============================================================
# 独立运行 demo
# ============================================================

async def main_demo(scenario: str, speed: float, log_dir: str = None):
    """演示 AsyncCollector 整合 3 个流"""
    from 流式播放.mock_cv_player import MockCVPlayer
    from 流式播放.mock_sensor_stream import MockSensorStream
    from 流式播放.mock_positioning_stream import MockPositioningStream

    collector = AsyncCollector(
        cv_source=MockCVPlayer(scenario, speed),
        sensor_source=MockSensorStream(scenario, speed),
        pos_source=MockPositioningStream(scenario, speed),
        log_dir=log_dir,
    )

    print("=" * 78)
    print(f"  AsyncCollector 整合 demo")
    print(f"  场景: {scenario}  |  速度: {speed}x")
    print(f"  日志目录: {log_dir or '(不落盘)'}")
    print("=" * 78)

    await collector.start()
    print("[已启动 3 个消费任务]")

    try:
        for sec in range(30):
            await asyncio.sleep(1.0 / speed)
            snap = collector.get_snapshot(sec)
            cv_n = len(snap["cv_logs"])
            sensor_reading = snap["sensors"][0]["readings"]["gas_detector_03"] if snap["sensors"] else None
            sensor_status = sensor_reading["status"] if sensor_reading else "N/A"
            pos_data = snap["positions"][0]["positions"] if snap["positions"] else {}
            in_zone = sum(1 for p in pos_data.values() if p.get("is_in_danger_zone"))
            print(
                f"[T={sec+1:2d}.0s]  "
                f"CV={cv_n:3d}条  "
                f"气体={sensor_status:7s}  "
                f"危险区内人数={in_zone}"
            )
    except asyncio.CancelledError:
        print("\n[用户中断]")

    await collector.stop()
    print("\n" + "=" * 78)
    print(f"  [OK] 运行结束")
    if log_dir:
        print(f"  日志已写入: {log_dir}")
        # 演示查询接口
        print(f"\n  查询示例: P7 在 8-13s 的 CV 日志数 = {len(collector.query_raw_logs(8, 13, 'cv', 'P7'))}")
        print(f"  查询示例: 整场传感器读数     = {len(collector.query_raw_logs(0, 30, 'sensor'))}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="AsyncCollector 整合 demo")
    parser.add_argument("--scenario", default="B", choices=["A","B","C","D","E"])
    parser.add_argument("--speed", type=float, default=5.0,
                        help="播放速度(默认 5x)")
    parser.add_argument("--log-dir", default=None,
                        help="日志目录(可选)")
    args = parser.parse_args()

    log_dir = args.log_dir
    if log_dir is None and "--save-logs" in sys.argv:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = f"logs/scenario_{args.scenario}_speed_{args.speed}_{timestamp}"

    asyncio.run(main_demo(args.scenario, args.speed, log_dir))


if __name__ == "__main__":
    main()