"""
A6 Agent 工具集

包含两大类工具：
- A5DataTools: 读取 A5 生成的日志数据
- OutputTools: 记录处理结果
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any


# ============================================================
# 路径配置
# ============================================================

DEFAULT_A5_LOG_DIR = "A5/logs"
DEFAULT_A6_OUTPUT_DIR = "A6/logs"
DEFAULT_STATUS_FILE = "A6/logs/a5_log_status.json"
# 使用 __file__ 的绝对路径，避免相对路径在不同工作目录下解析不一致
_ACTUAL_DIR = Path(__file__).resolve().parent.parent
ACTIVE_VIOLATIONS_FILE = str(_ACTUAL_DIR / "logs" / "active_violations.json")


# ============================================================
# 工具类定义
# ============================================================

class A5DataTools:
    """
    A6 Agent 输入工具集

    用于读取 A5 生成的日志数据
    """

    def __init__(self, a5_log_dir: str = None):
        self.a5_log_dir = Path(a5_log_dir) if a5_log_dir else Path(DEFAULT_A5_LOG_DIR)
        self._status_cache: Optional[Dict] = None

    # --------------------------------------------
    # 状态管理
    # --------------------------------------------

    def _load_status(self) -> Dict:
        """加载状态文件"""
        status_file = Path(DEFAULT_STATUS_FILE)
        if status_file.exists():
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "log_dir": str(self.a5_log_dir),
            "last_updated": datetime.now().isoformat(),
            "files": {},
            "event_id_map": {},
            "summary": {"total": 0, "unprocessed": 0, "processing": 0, "error": 0, "processed": 0}
        }

    def _save_status(self, status: Dict) -> None:
        """保存状态文件"""
        status["last_updated"] = datetime.now().isoformat()
        status_file = Path(DEFAULT_STATUS_FILE)
        status_file.parent.mkdir(parents=True, exist_ok=True)
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        self._status_cache = status

    def _get_status(self) -> Dict:
        """获取状态（带缓存）"""
        if self._status_cache is None:
            self._status_cache = self._load_status()
        return self._status_cache

    def check_event_status(self, event_id: str) -> Dict:
        """
        查询 A5 事件 ID 的处理状态

        Args:
            event_id: A5 事件 ID（如 "A5-P7-1722933125"）

        Returns:
            {
                "event_id": str,           # 事件ID
                "exists": bool,            # 是否已存在
                "status": str,             # 状态：None/completed/updated
                "a6_output_id": str,       # 已有则返回 A6 输出ID
                "first_processed_at": str  # 首次处理时间
            }
        """
        # 永远从磁盘读取，避免并发时缓存不一致导致丢事件
        status = self._load_status()
        event_info = status.get("event_id_map", {}).get(event_id)

        if event_info is None:
            return {
                "event_id": event_id,
                "exists": False,
                "status": None,
                "a6_output_id": None,
                "first_processed_at": None
            }

        return {
            "event_id": event_id,
            "exists": True,
            "status": event_info.get("status"),
            "a6_output_id": event_info.get("a6_output_id"),
            "first_processed_at": event_info.get("first_processed_at")
        }

    def update_event_status(
        self,
        event_id: str,
        a6_output_id: str,
        new_status: str = "completed"
    ) -> None:
        """
        更新事件状态

        Args:
            event_id: A5 事件 ID
            a6_output_id: A6 输出的 ID
            new_status: 新状态（completed/updated）
        """
        # 永远从磁盘读取再写入（防止并发时 cache 不一致导致重复研判）
        status = self._load_status()

        if "event_id_map" not in status:
            status["event_id_map"] = {}

        existing = status["event_id_map"].get(event_id)

        if existing:
            # 已存在，更新状态
            status["event_id_map"][event_id]["status"] = new_status
        else:
            # 新建记录
            status["event_id_map"][event_id] = {
                "a6_output_id": a6_output_id,
                "status": new_status,
                "first_processed_at": datetime.now().isoformat()
            }

        self._save_status(status)

    # --------------------------------------------
    # A5 数据读取
    # --------------------------------------------

    def _sanitize_wall_time(self, wall_time: str) -> str:
        """将 wall_time 转为安全的文件名"""
        return wall_time.replace(":", "-").replace(".", "_")

    def _unsanitize_wall_time(self, safe_wall_time: str) -> str:
        """将安全文件名转回 wall_time"""
        return safe_wall_time.replace("-", ":").replace("_", ".")

    def _find_raw_event_file(self, event_id: str) -> Optional[Path]:
        """根据 event_id 查找对应的 raw_event 文件"""
        if not self.a5_log_dir.exists():
            return None

        # event_id 格式: A5-P7-1722933125
        # 需要扫描所有 raw_event 文件查找匹配的 event_id
        for f in self.a5_log_dir.glob("raw_event_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    for event in data.get("events", []):
                        if event.get("event_id") == event_id:
                            return f
            except (json.JSONDecodeError, IOError):
                continue
        return None

    def read_raw_event_by_id(self, event_id: str) -> Optional[Dict]:
        """
        根据 A5 事件 ID 读取原始事件数据

        Args:
            event_id: A5 事件 ID

        Returns:
            事件数据字典，包含 event_id、type、person、first_seen、last_seen、evidence 等
        """
        file_path = self._find_raw_event_file(event_id)
        if file_path is None:
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for event in data.get("events", []):
                    if event.get("event_id") == event_id:
                        return event
        except (json.JSONDecodeError, IOError):
            pass

        return None

    def read_raw_event_by_filename(self, filename: str) -> Optional[Dict]:
        """
        根据文件名读取 raw_event 文件

        Args:
            filename: raw_event 文件名（如 "raw_event_2026-08-06T15-43-05_504.json"）

        Returns:
            raw_event 文件内容
        """
        file_path = self.a5_log_dir / filename
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def list_raw_events(
        self,
        status_filter: str = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        列出 A5 日志目录中的 raw_event 文件

        Args:
            status_filter: 状态过滤（unprocessed/processing/error/processed）
            limit: 返回数量限制

        Returns:
            文件列表
        """
        status = self._get_status()
        files_info = status.get("files", {})
        results = []

        if not self.a5_log_dir.exists():
            return results

        for f in sorted(self.a5_log_dir.glob("raw_event_*.json")):
            safe_key = f.stem[len("raw_event_"):]
            file_status = files_info.get(safe_key, {}).get("status", "unprocessed")

            if status_filter and file_status != status_filter:
                continue

            results.append({
                "filename": f.name,
                "status": file_status,
                "safe_key": safe_key
            })

            if len(results) >= limit:
                break

        return results

    def read_surrounding_events(
        self,
        target_wall_time: str,
        time_window_minutes: int = 5
    ) -> List[Dict]:
        """
        读取目标时间前后时间窗口内的所有 raw_event

        Args:
            target_wall_time: 目标事件的时间戳（ISO格式）
            time_window_minutes: 时间窗口（分钟），默认前后5分钟

        Returns:
            时间窗口内的所有事件列表
        """
        try:
            target_dt = datetime.fromisoformat(target_wall_time.replace("-", ":").replace("_", "."))
        except ValueError:
            return []

        # 解析时间窗口
        from datetime import timedelta
        start_dt = target_dt - timedelta(minutes=time_window_minutes)
        end_dt = target_dt + timedelta(minutes=time_window_minutes)

        all_events = []

        if not self.a5_log_dir.exists():
            return all_events

        for f in self.a5_log_dir.glob("raw_event_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    wall_time_str = data.get("wall_time", "")
                    if not wall_time_str:
                        continue

                    # 解析 wall_time
                    try:
                        event_dt = datetime.fromisoformat(wall_time_str.replace("-", ":").replace("_", "."))
                    except ValueError:
                        continue

                    if start_dt <= event_dt <= end_dt:
                        for event in data.get("events", []):
                            event["_source_file"] = f.name
                            all_events.append(event)

            except (json.JSONDecodeError, IOError):
                continue

        return all_events

    # --------------------------------------------
    # 活跃违规聚合表（A6 自己维护）
    # --------------------------------------------

    def _load_active_violations(self) -> Dict:
        """从磁盘加载活跃违规表"""
        f = Path(ACTIVE_VIOLATIONS_FILE)
        if f.exists():
            try:
                with open(f, encoding="utf-8") as fp:
                    return json.load(fp)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save_active_violations(self, data: Dict) -> None:
        """保存活跃违规表到磁盘"""
        f = Path(ACTIVE_VIOLATIONS_FILE)
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)

    def query_active_violations(self, person_id: str = None) -> Dict:
        """
        查询当前活跃的违规事件（尚未关闭的）。
        用于判断当前收到的检测是否属于已在追踪的违规。

        Args:
            person_id: 可选，按人员 ID 过滤

        Returns:
            {
                "active_violations": [
                    {
                        "key": "P7+helmet_missing",
                        "person_id": "P7",
                        "violation_type": "helmet_missing",
                        "a6_event_id": "A6-...",
                        "first_seen": "2026-08-12T11:34:53.105",
                        "last_seen": "2026-08-12T11:35:03.105",
                        "duration_sec": 10,
                        "status": "ongoing"
                    },
                    ...
                ]
            }
        """
        active = self._load_active_violations()
        results = []
        for key, info in active.items():
            if person_id and info.get("person_id") != person_id:
                continue
            if info.get("status") == "ongoing":
                results.append({"key": key, **info})
        return {"active_violations": results}

    def query_surrounding_raw_events(self, wall_time: str, window_sec: int = 60) -> Dict:
        """
        查询指定 wall_time 前后 window_sec 秒内的所有 A5 原始检测输出。
        用于当需要更多信息时，查看当前事件前后的上下文。

        Args:
            wall_time: 目标事件的时间（ISO 格式或带 _/- 的混合格式）
            window_sec: 时间窗口秒数，默认前后 60 秒

        Returns:
            {
                "surrounding_events": [...],
                "total": N
            }
        """
        events = self.read_surrounding_events(wall_time, time_window_minutes=max(1, window_sec // 60))
        events_sorted = sorted(events, key=lambda e: e.get("wall_time", ""))
        return {"surrounding_events": events_sorted, "total": len(events_sorted)}

    def update_active_violation(
        self,
        key: str,
        a6_event_id: str,
        person_id: str,
        violation_type: str,
        first_seen: str,
        last_seen: str,
        status: str = "ongoing"
    ) -> None:
        """更新或创建一条活跃违规记录"""
        active = self._load_active_violations()
        try:
            from datetime import timedelta
            ft = datetime.fromisoformat(first_seen.replace("-", ":").replace("_", "."))
            lt = datetime.fromisoformat(last_seen.replace("-", ":").replace("_", "."))
            duration = int((lt - ft).total_seconds())
        except Exception:
            duration = 0
        active[key] = {
            "a6_event_id": a6_event_id,
            "person_id": person_id,
            "violation_type": violation_type,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "duration_sec": duration,
            "status": status,
        }
        self._save_active_violations(active)

    def close_active_violation(self, key: str) -> None:
        """关闭一条活跃违规记录"""
        active = self._load_active_violations()
        if key in active:
            active[key]["status"] = "closed"
            self._save_active_violations(active)

    def get_active_violation(self, key: str) -> Optional[Dict]:
        """根据 key 查询单条活跃违规"""
        active = self._load_active_violations()
        return active.get(key)


class OutputTools:
    """
    A6 输出工具集

    用于记录聚合事件、风险等级研判结果等
    """

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path(DEFAULT_A6_OUTPUT_DIR)

    def _get_date_dir(self) -> Path:
        """获取当天的输出目录"""
        date_str = datetime.now().strftime("%Y%m%d")
        return self.output_dir / "assessments" / date_str

    def _get_agg_dir(self) -> Path:
        """获取当天的聚合输出目录"""
        date_str = datetime.now().strftime("%Y%m%d")
        return self.output_dir / "aggregations" / date_str

    def _ensure_dir(self, dir_path: Path) -> None:
        """确保目录存在"""
        dir_path.mkdir(parents=True, exist_ok=True)

    def _generate_a6_event_id(self) -> str:
        """生成 A6 事件 ID"""
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M%S")
        seq = now.microsecond // 1000  # 毫秒级序列号
        return f"A6-{date_str}-{time_str}-{seq:03d}"

    def save_assessment(
        self,
        assessment: Dict,
        output_dir: str = None,
        filepath: str = None
    ) -> str:
        """
        保存单条风险研判结果

        Args:
            assessment: 研判结果字典
            output_dir: 保存目录（可选，默认按当天日期分目录）
            filepath:   指定文件路径（可选，用于覆盖已有研判文件）

        Returns:
            保存的文件路径
        """
        if filepath:
            # 覆盖模式：写入指定路径
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(assessment, f, ensure_ascii=False, indent=2)
            return filepath

        # 新建模式（原有逻辑）
        dir_path = Path(output_dir) if output_dir else self._get_date_dir()
        self._ensure_dir(dir_path)

        if "a6_event_id" not in assessment:
            assessment["a6_event_id"] = self._generate_a6_event_id()

        if "timestamp" not in assessment:
            assessment["timestamp"] = datetime.now().isoformat()

        safe_time = assessment["timestamp"].replace(":", "-").replace(".", "_")
        filename = f"a6_{safe_time}.json"
        filepath = dir_path / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(assessment, f, ensure_ascii=False, indent=2)

        return str(filepath)

    def save_aggregation_result(
        self,
        aggregation: Dict,
        output_dir: str = None
    ) -> str:
        """
        保存事件聚合结果

        Args:
            aggregation: 聚合结果，包含:
                - aggregated_event_id: 聚合后事件ID
                - original_event_ids: 原始事件ID列表
                - aggregation_reasoning: 聚合判断理由
                - first_seen: 聚合后首次时间
                - last_seen: 聚合后最近时间

        Returns:
            保存的文件路径
        """
        dir_path = Path(output_dir) if output_dir else self._get_agg_dir()
        self._ensure_dir(dir_path)

        # 添加时间戳
        if "timestamp" not in aggregation:
            aggregation["timestamp"] = datetime.now().isoformat()

        # 生成文件名
        agg_id = aggregation.get("aggregated_event_id", "unknown")
        safe_id = agg_id.replace(":", "-").replace(".", "_")
        filename = f"agg_{safe_id}.json"
        filepath = dir_path / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(aggregation, f, ensure_ascii=False, indent=2)

        return str(filepath)

    def batch_save_assessments(
        self,
        assessments: List[Dict],
        output_dir: str = None
    ) -> List[str]:
        """
        批量保存研判结果

        Returns:
            保存的文件路径列表
        """
        saved_paths = []
        for assessment in assessments:
            path = self.save_assessment(assessment, output_dir)
            saved_paths.append(path)
        return saved_paths

    def update_a5_log_status(
        self,
        filenames: List[str],
        new_status: str,
        extra_info: Dict = None
    ) -> bool:
        """
        更新 A5 日志状态索引文件

        Args:
            filenames: 要更新的文件名列表
            new_status: 新状态（processing/processed/error）
            extra_info: 额外信息（如 a6_output_id、error_message 等）

        Returns:
            是否更新成功
        """
        try:
            status_file = Path(DEFAULT_STATUS_FILE)
            if status_file.exists():
                with open(status_file, "r", encoding="utf-8") as f:
                    status = json.load(f)
            else:
                status = {
                    "files": {},
                    "event_id_map": {},
                    "summary": {"total": 0, "unprocessed": 0, "processing": 0, "error": 0, "processed": 0}
                }

            for filename in filenames:
                # 从文件名提取 safe_key
                if filename.startswith("raw_event_"):
                    safe_key = filename[len("raw_event_"):-5]
                else:
                    safe_key = filename

                if "files" not in status:
                    status["files"] = {}

                status["files"][safe_key] = {
                    "status": new_status,
                    "updated_at": datetime.now().isoformat()
                }

                if extra_info:
                    status["files"][safe_key].update(extra_info)

            # 更新 summary
            if "summary" not in status:
                status["summary"] = {"total": 0, "unprocessed": 0, "processing": 0, "error": 0, "processed": 0}

            counts = {"total": len(status["files"])}
            for key, info in status["files"].items():
                s = info.get("status", "unprocessed")
                counts[s] = counts.get(s, 0) + 1

            status["summary"] = {
                "total": counts.get("total", 0),
                "unprocessed": counts.get("unprocessed", 0),
                "processing": counts.get("processing", 0),
                "error": counts.get("error", 0),
                "processed": counts.get("processed", 0)
            }

            status["last_updated"] = datetime.now().isoformat()

            status_file.parent.mkdir(parents=True, exist_ok=True)
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)

            # 清除缓存，下次 _get_status() 会重新加载
            self._status_cache = None

            return True

        except Exception as e:
            print(f"Error updating status: {e}")
            return False

    def load_assessment(self, a6_event_id: str) -> Optional[Dict]:
        """
        根据 A6 事件 ID 加载研判结果

        Args:
            a6_event_id: A6 事件 ID

        Returns:
            研判结果字典（兼容旧调用），若需文件路径请用 load_assessment_with_path
        """
        data = self.load_assessment_with_path(a6_event_id)
        return data[0] if data else None

    def load_assessment_with_path(self, a6_event_id: str) -> Optional[tuple]:
        """
        根据 A6 事件 ID 加载研判结果及文件路径

        Returns:
            (研判结果字典, 文件路径str) 或 None
        """
        assessments_dir = self.output_dir / "assessments"
        if not assessments_dir.exists():
            return None

        for date_dir in assessments_dir.iterdir():
            if date_dir.is_dir():
                for f in date_dir.glob("a6_*.json"):
                    try:
                        with open(f, "r", encoding="utf-8") as fp:
                            data = json.load(fp)
                            if data.get("a6_event_id") == a6_event_id:
                                return (data, str(f))
                    except (json.JSONDecodeError, IOError):
                        continue
        return None
