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

from A5.stream_players.mock_cv_player import MockCVPlayer
from A5.stream_players.mock_sensor_stream import MockSensorStream
from A5.stream_players.mock_positioning_stream import MockPositioningStream


class AsyncCollector:
    """固定流程:并行消费 3 个流,内存缓存 + 每秒落盘"""

    def __init__(
        self,
        scenario: str = "B",
        log_dir: Optional[str] = None,
    ):
        self.scenario = scenario
        self.log_dir = Path(log_dir) if log_dir else None

        # 内部创建 3 个流(实时 1:1,wall_time == scenario_time)
        self._cv = MockCVPlayer(scenario)
        self._sensor = MockSensorStream(scenario)
        self._pos = MockPositioningStream(scenario)

        # ============ 原始日志(流直接输出,累加)============
        self._cv_raw: List[Dict] = []
        self._sensor_raw: List[Dict] = []
        self._position_raw: List[Dict] = []

        # ============ 控制 ============
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._start_wall: Optional[datetime] = None  # 场景开始运行的现实时间

    # ============================================================
    # 生命周期
    # ============================================================

    async def start(self):
        """启动 3 个后台消费任务"""
        if self._running:
            return
        self._running = True
        self._start_wall = datetime.now()
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)

        # 同步 sensor/pos stream 的 start_wall 与 collector 保持一致
        # 确保 sensor log 的 wall_time 与 collector 查询时的时间基准对齐
        self._sensor._start_wall = self._start_wall
        self._pos._start_wall = self._start_wall

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

        # 内部按 sec 查询(转 wall_time)
        sec_wall = self._sec_to_wall(sec)
        next_wall = self._sec_to_wall(sec + 1)
        cv_sec = self.query_raw_logs(sec_wall, next_wall, "cv")
        sensor_sec = self.query_raw_logs(sec_wall, next_wall, "sensor")
        position_sec = self.query_raw_logs(sec_wall, next_wall, "position")

        self._write_file("cv",       cv_sec,       sec_wall)
        self._write_file("sensor",   sensor_sec,   sec_wall)
        self._write_file("position", position_sec, sec_wall)

        snap = self._build_single_snapshot(sec)
        self._write_file("snapshot", [snap], sec_wall)

    def _build_single_snapshot(self, sec: int) -> Dict:
        cv_logs = self.query_raw_logs(self._sec_to_wall(sec), self._sec_to_wall(sec+1), "cv")
        sensor_logs = self.query_raw_logs(self._sec_to_wall(sec), self._sec_to_wall(sec+1), "sensor")
        pos_logs = self.query_raw_logs(self._sec_to_wall(sec), self._sec_to_wall(sec+1), "position")

        # ── 传感器摘要 ──
        sensor_summary = {}
        if sensor_logs:
            for sid, r in sensor_logs[0].get("readings", {}).items():
                sensor_summary[sid] = {"value": r["value"], "status": r["status"]}

        # ── 位置摘要 ──
        pos_summary = {}
        if pos_logs:
            for wid, p in pos_logs[0].get("positions", {}).items():
                pos_summary[wid] = {"area_id": p["area_id"],
                                    "in_danger_zone": p["is_in_danger_zone"]}

        # ── CV 聚合(每人数,多数表决) ──
        # 把这一秒的原始 CV 日志按 person_id 分组,每人统计置信度
        cv_summary = {}
        if cv_logs:
            # 收集所有 person_id
            person_ids = sorted(set(log.get("person_id", "") for log in cv_logs))
            for pid in person_ids:
                if not pid:
                    continue
                person_logs = [log for log in cv_logs if log.get("person_id") == pid]
                total = len(person_logs)
                if total == 0:
                    continue
                # 统计每类 PPE 缺失帧数
                def _count_missing(class_name):
                    return sum(
                        1 for log in person_logs
                        if not next((d["value"] for d in log.get("detections", [])
                                     if d["class_name"] == class_name), True)
                    )
                n_helmet = _count_missing("helmet")
                n_goggles = _count_missing("goggles")
                n_suit = _count_missing("protective_suit")

                cv_summary[pid] = {
                    "total_frames":          total,
                    "helmet_missing_count":  n_helmet,
                    "helmet_missing_ratio":  round(n_helmet / total, 3),
                    "goggles_missing_count": n_goggles,
                    "goggles_missing_ratio": round(n_goggles / total, 3),
                    "suit_missing_count":    n_suit,
                    "suit_missing_ratio":   round(n_suit / total, 3),
                    "is_violating": (n_helmet / total >= 0.8 or
                                     n_goggles / total >= 0.8 or
                                     n_suit / total >= 0.8),
                }

        return {
            "time_range":    [float(sec), float(sec + 1)],
            "cv_log_count":  len(cv_logs),
            "cv_summary":    cv_summary,
            "sensor_summary": sensor_summary,
            "position_summary": pos_summary,
        }

    def _write_file(self, source_type: str, data: list, wall_time: str):
        """每秒写一个独立文件(永不覆盖)，文件名统一用 wall_time"""
        # wall_time 是 ISO 格式，转成安全文件名：: → -，. → _
        safe_time = wall_time.replace(":", "-").replace(".", "_")
        filename = f"{source_type}_{safe_time}.json"
        filepath = self.log_dir / filename
        is_snapshot = source_type == "snapshot"

        payload = {
            "scenario": self.scenario,
            "type":     source_type,
            "created_at": wall_time,
            "log_count": len(data),
            "logs" if not is_snapshot else "snapshots": data,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 查询接口(供agent)
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

    def _wall_to_sec(self, wall_time_str: str) -> float:
        """
        现实世界时间(ISO) → 场景内秒数。
        实时 1:1 映射:scenario_sec = wall_time - start_wall
        """
        if self._start_wall is None:
            return -1.0
        try:
            wall = datetime.fromisoformat(wall_time_str)
        except (ValueError, TypeError):
            return -1.0
        return (wall - self._start_wall).total_seconds()

    def _sec_to_wall(self, sec: int) -> str:
        """场景内秒数 → 现实世界时间(ISO)"""
        if self._start_wall is None:
            return ""
        from datetime import timedelta
        return (self._start_wall + timedelta(seconds=sec)).isoformat(timespec="milliseconds")

    def query_raw_logs(
        self,
        start_wall: str,
        end_wall: str,
        source_type: Optional[str] = None,
        person_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        按现实世界时间范围查询原始日志。
        start_wall/end_wall 为 ISO 格式(如 "2026-08-04T14:30:00")。
        """
        start_sec = self._wall_to_sec(start_wall)
        end_sec = self._wall_to_sec(end_wall)
        # end_sec < 0 → wall_time 字符串格式错误，直接放弃
        if end_sec < 0:
            return []
        # 钳位：_sec_to_wall 用 isoformat(timespec="milliseconds") 截掉微秒，
        # 导致 sec=0 时 _wall_to_sec 返回 -0.000~ （比 _start_wall 还早 ~0.5ms）。
        # 之前直接 return[] 会丢第一秒 snapshot 的 cv/sensor/position 数据；
        # 这里钳到 0 → 至少能查 [0, end_sec) 范围（虽然多包了 0~0.5ms 的边界但不会丢数据）
        if start_sec < 0:
            start_sec = 0.0

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

    def get_snapshot(self, wall_time: str) -> Dict[str, Any]:
        """
        根据现实世界时间获取对应秒的世界快照。
        wall_time: ISO 格式,表示"这一秒结束的时刻"。
        实际查询该秒 [wall_time-1s, wall_time) 的数据。
        """
        sec = self._wall_to_sec(wall_time)
        # wall_time 表示"这一秒结束",所以查询范围是 [sec-1, sec)
        # round() 修复浮点精度: _wall_to_sec 可能返回 8.999 而非 9.0
        sec_int = max(0, min(29, round(sec) - 1))
        if self._start_wall:
            from datetime import timedelta
            start_wall_dt = self._start_wall + timedelta(seconds=sec_int)
            end_wall_dt = self._start_wall + timedelta(seconds=sec_int + 1)
            start_wall = start_wall_dt.isoformat(timespec="milliseconds")
            end_wall = end_wall_dt.isoformat(timespec="milliseconds")
        else:
            start_wall = end_wall = wall_time

        return {
            "wall_time":     wall_time,
            "scenario_time": f"{sec_int}.0s",
            "cv_logs":       self.query_raw_logs(start_wall, end_wall, "cv"),
            "sensors":       self.query_raw_logs(start_wall, end_wall, "sensor"),
            "positions":     self.query_raw_logs(start_wall, end_wall, "position"),
        }


# ============================================================
# 前端接口:一行调用跑完整个场景
# ============================================================

async def run_demo(
    scenario: str = "B",
    log_dir: Optional[str] = None,
    print_summary: bool = True,
) -> AsyncCollector:
    """前端一行调用: collector = await run_demo("B", "logs/run_01")"""
    if log_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = f"logs/{scenario}_{ts}"

    collector = AsyncCollector(scenario, log_dir)

    if print_summary:
        print(f"[场景={scenario} 日志={log_dir}]")

    await collector.start()

    # main_loop:30 秒实时,每秒落盘
    for sec in range(30):
        await asyncio.sleep(1.0)
        await collector.flush_second(sec)

        if print_summary:
            current_wall = collector._sec_to_wall(sec + 1)
            snap = collector.get_snapshot(current_wall)
            cv_n = len(snap["cv_logs"])
            sensor_reading = snap["sensors"][0]["readings"]["gas_detector_03"] if snap["sensors"] else None
            gas_status = sensor_reading["status"] if sensor_reading else "N/A"
            pos_data = snap["positions"][0]["positions"] if snap["positions"] else {}
            in_zone = sum(1 for p in pos_data.values() if p.get("is_in_danger_zone"))
            print(f"  [T={sec+1:2d}s {current_wall[11:19]}] CV={cv_n:3d}条  气体={gas_status:7s}  危险区={in_zone}人")

    await asyncio.sleep(1.0)
    await collector.stop()

    if print_summary:
        print(f"  [完成] {len(collector._cv_raw)} CV + {len(collector._sensor_raw)} 传感器 + {len(collector._position_raw)} 定位")

    return collector
