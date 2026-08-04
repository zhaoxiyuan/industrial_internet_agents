"""
AsyncCollector — 固定流程:集中管理 3 个流 + 两类日志落盘

设计:
- 传入 scenario + speed + log_dir,内部自动创建 3 个流
- 每秒调用 flush_second(sec) 落盘,每次新建文件,永不覆盖

日志文件(每秒 4 个独立文件,共 120 个/场景):
  cv_{ts}_T00.json       ~ cv_{ts}_T29.json
  sensor_{ts}_T00.json   ~ sensor_{ts}_T29.json
  position_{ts}_T00.json ~ position_{ts}_T29.json
  snapshot_{ts}_T00.json ~ snapshot_{ts}_T29.json

前端调用:
  collector = AsyncCollector(scenario="B", speed=1.0, log_dir="logs/run_01")
  await collector.start()
  for sec in range(30):
      await asyncio.sleep(1.0 / speed)
      collector.flush_second(sec)
  await collector.stop()
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from 流式播放.mock_cv_player import MockCVPlayer
from 流式播放.mock_sensor_stream import MockSensorStream
from 流式播放.mock_positioning_stream import MockPositioningStream


class AsyncCollector:
    """固定流程:并行消费 3 个流,内存缓存 + 每秒落盘"""

    def __init__(
        self,
        scenario: str = "B",
        speed: float = 1.0,
        log_dir: Optional[str] = None,
    ):
        self.scenario = scenario
        self.speed = speed
        self.log_dir = Path(log_dir) if log_dir else None

        # 内部创建 3 个流
        self._cv = MockCVPlayer(scenario, speed)
        self._sensor = MockSensorStream(scenario, speed)
        self._pos = MockPositioningStream(scenario, speed)

        # ============ 原始日志(流直接输出,累加)============
        self._cv_raw: List[Dict] = []
        self._sensor_raw: List[Dict] = []
        self._position_raw: List[Dict] = []

        # ============ 控制 ============
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ============================================================
    # 生命周期
    # ============================================================

    async def start(self):
        """启动 3 个后台消费任务"""
        if self._running:
            return
        self._running = True
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

        self._tasks = [
            asyncio.create_task(self._consume(self._cv, "cv")),
            asyncio.create_task(self._consume(self._sensor, "sensor")),
            asyncio.create_task(self._consume(self._pos, "position")),
        ]

    async def stop(self):
        """停止所有任务"""
        self._running = False
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ============================================================
    # 流消费(追加到 list)
    # ============================================================

    async def _consume(self, source, source_type: str):
        try:
            async for log in source.stream():
                if not self._running:
                    break
                {
                    "cv":       lambda: self._cv_raw.append(log),
                    "sensor":   lambda: self._sensor_raw.append(log),
                    "position": lambda: self._position_raw.append(log),
                }[source_type]()
        except asyncio.CancelledError:
            pass

    # ============================================================
    # ★ 核心:每秒落盘(每次新建文件,永不覆盖)
    # ============================================================

    async def flush_second(self, sec: int):
        """
        每秒调用一次。
        把这一秒的数据写入 4 个独立文件。
        等待流确实经过了这一秒后写入,确保数据完整。
        """
        if not self.log_dir:
            return

        # 等待流确实经过了这一秒(看最新日志的场景时间是否 ≥ sec+1)
        for _ in range(200):   # 最多等 1 秒
            t = self._latest_scenario_time("cv")
            if t >= sec + 1.0:
                break
            await asyncio.sleep(0.005)

        ts = self._timestamp
        cv_sec = self.query_raw_logs(sec, sec + 1, "cv")
        sensor_sec = self.query_raw_logs(sec, sec + 1, "sensor")
        position_sec = self.query_raw_logs(sec, sec + 1, "position")

        self._write_file("cv",       cv_sec,       ts, sec)
        self._write_file("sensor",   sensor_sec,   ts, sec)
        self._write_file("position", position_sec, ts, sec)

        snap = self._build_single_snapshot(sec)
        self._write_file("snapshot", [snap], ts, sec)

    def _build_single_snapshot(self, sec: int) -> Dict:
        cv_logs = self.query_raw_logs(sec, sec + 1, "cv")
        sensor_logs = self.query_raw_logs(sec, sec + 1, "sensor")
        pos_logs = self.query_raw_logs(sec, sec + 1, "position")

        sensor_summary = {}
        if sensor_logs:
            for sid, r in sensor_logs[0].get("readings", {}).items():
                sensor_summary[sid] = {"value": r["value"], "status": r["status"]}

        pos_summary = {}
        if pos_logs:
            for wid, p in pos_logs[0].get("positions", {}).items():
                pos_summary[wid] = {"area_id": p["area_id"],
                                    "in_danger_zone": p["is_in_danger_zone"]}

        return {
            "second": sec,
            "time_range": [float(sec), float(sec + 1)],
            "cv_log_count": len(cv_logs),
            "sensor_summary": sensor_summary,
            "position_summary": pos_summary,
        }

    def _write_file(self, source_type: str, data: list, timestamp: str, sec: int):
        """每秒写一个独立文件(永不覆盖)"""
        filename = f"{source_type}_{timestamp}_T{sec:02d}.json"
        filepath = self.log_dir / filename
        is_snapshot = source_type == "snapshot"

        payload = {
            "scenario": self.scenario,
            "speed":    self.speed,
            "type":     source_type,
            "created_at": timestamp,
            "second":   sec,
            "log_count": len(data),
            "logs" if not is_snapshot else "snapshots": data,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 查询接口(供智能体)
    # ============================================================

    def _get_raw_logs(self, source_type: str) -> List[Dict]:
        return {"cv": self._cv_raw, "sensor": self._sensor_raw,
                "position": self._position_raw}.get(source_type, [])

    def _extract_second(self, scenario_time_str: str) -> float:
        try:
            return float(scenario_time_str.rstrip("s"))
        except (ValueError, AttributeError):
            return -1.0

    def _latest_scenario_time(self, source_type: str) -> float:
        """获取指定流的最新日志的场景时间,无数据时返回 -1"""
        logs = self._get_raw_logs(source_type)
        if not logs:
            return -1.0
        return self._extract_second(logs[-1].get("scenario_time", "0s"))

    def query_raw_logs(
        self,
        start_sec: float,
        end_sec: float,
        source_type: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> List[Dict]:
        """按时间范围查询原始日志"""
        types = [source_type] if source_type else ["cv", "sensor", "position"]
        results = []
        for stype in types:
            for log in self._get_raw_logs(stype):
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


# ============================================================
# 前端接口:一行调用跑完整个场景
# ============================================================

async def run_demo(
    scenario: str = "B",
    speed: float = 1.0,
    log_dir: Optional[str] = None,
    print_summary: bool = True,
) -> AsyncCollector:
    """前端一行调用: collector = await run_demo("B", 5.0, "logs/run_01")"""
    if log_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = f"logs/{scenario}_speed_{speed}_{ts}"

    collector = AsyncCollector(scenario, speed, log_dir)

    if print_summary:
        print(f"[场景={scenario} 速度={speed}x 日志={log_dir}]")

    await collector.start()

    # 主循环:30 秒,每秒落盘
    for sec in range(30):
        await asyncio.sleep(1.0 / speed)
        await collector.flush_second(sec)   # ← 每秒写入 4 个文件

        if print_summary:
            snap = collector.get_snapshot(sec)
            cv_n = len(snap["cv_logs"])
            sensor_reading = snap["sensors"][0]["readings"]["gas_detector_03"] if snap["sensors"] else None
            gas_status = sensor_reading["status"] if sensor_reading else "N/A"
            pos_data = snap["positions"][0]["positions"] if snap["positions"] else {}
            in_zone = sum(1 for p in pos_data.values() if p.get("is_in_danger_zone"))
            print(f"  [T={sec+1:2d}s] CV={cv_n:3d}条  气体={gas_status:7s}  危险区={in_zone}人")

    await asyncio.sleep(1.0)
    await collector.stop()

    if print_summary:
        print(f"  [完成] {len(collector._cv_raw)} CV + {len(collector._sensor_raw)} 传感器 + {len(collector._position_raw)} 定位")

    return collector
