import base64
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import ToolError, TERMINAL
from .scenario_data import AREAS, SENSOR_CONFIG, SCENARIOS_CV, scenario_duration

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
TABLES = dict(cv="cv_observations", sensor="sensor_readings", position="position_readings", ppe="ppe_second_stats")


def now_us():
    delta = datetime.now(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def iso(microseconds):
    return (EPOCH + timedelta(microseconds=microseconds)).isoformat(timespec="microseconds")


def now():
    return iso(now_us())


def parse_time(value):
    try:
        if not isinstance(value, str): raise ValueError()
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None: raise ValueError()
        delta = stamp.astimezone(timezone.utc) - EPOCH
        return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    except (ValueError, TypeError, OverflowError):
        raise ToolError("Expected a timezone-aware ISO timestamp")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def bounds(start, end):
    if (start is None) != (end is None): raise ToolError("from_time and to_time must be provided together")
    if start is None: return None, None
    a, b = parse_time(start), parse_time(end)
    if a >= b: raise ToolError("Expected from_time < to_time")
    return a, b


class Store:
    def __init__(self, directory, migrate_legacy=True):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "mock_player.sqlite3"
        self.lock = open(self.root / "service.lock", "a+b")
        self.lock.seek(0, os.SEEK_END)
        if not self.lock.tell(): self.lock.write(b"0"); self.lock.flush()
        self.lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("Another service owns this data directory")
        try:
            with self.db() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 2, 3): raise RuntimeError(f"Unsupported database version: {version}")
                db.execute("PRAGMA journal_mode=WAL")
                from .upgrade import upgrade
                upgrade(self, db)
                state = dict(schema_version=3, state="idle", scenario=None, started_at=None,
                             ended_at=None, duration_sec=30, current_second=0,
                             counts=dict(cv=0, sensor=0, position=0, ppe=0), error=None)
                db.execute("INSERT OR IGNORE INTO player_state(id,state_json) VALUES(1,?)", (canonical(state),))
                names = {"P7": ("张师傅", "焊工"), "P8": ("王师傅", "焊工"), "P11": ("赵师傅", "监护人")}
                for pid in sorted({p for scenario in SCENARIOS_CV.values() for p in scenario}):
                    db.execute("INSERT OR IGNORE INTO persons VALUES(?,?,?)", (pid, *names.get(pid, (pid, "作业人员"))))
                for sid, cfg in SENSOR_CONFIG.items():
                    db.execute("INSERT OR IGNORE INTO sensors VALUES(?,?,?,?,?,?)", (sid, cfg["sensor_type"], cfg["unit"], cfg.get("threshold_warning"), cfg.get("threshold_alarm"), cfg.get("location")))
                for aid, cfg in AREAS.items():
                    db.execute("INSERT OR IGNORE INTO areas VALUES(?,?,?,?)", (aid, cfg["area_type"], int(cfg["is_danger_zone"]), canonical(cfg)))
            if migrate_legacy:
                from .migration import migrate
                migrate(self)
            if self.get()["state"] not in TERMINAL | {"idle"}:
                self.update(state="interrupted", ended_at=now())
        except Exception:
            self.close()
            raise

    def close(self):
        self.lock.close()

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write: db.execute("BEGIN IMMEDIATE")
            with db: yield db
        finally: db.close()

    def get(self, db=None):
        if db is not None: return json.loads(db.execute("SELECT state_json FROM player_state WHERE id=1").fetchone()[0])
        with self.db() as connection: return self.get(connection)

    def update(self, **changes):
        with self.db(write=True) as db:
            state = self.get(db); state.update(changes)
            db.execute("UPDATE player_state SET state_json=? WHERE id=1", (canonical(state),))
        return state

    def clear_history(self):
        """Clear player-owned history atomically, retaining configuration and a backup."""
        tables = ('agent_evidence', 'agent_results', 'acquisition_seconds', 'processing_tasks',
                  'cv_observations', 'sensor_readings', 'position_readings', 'ppe_second_stats')
        with self.db(write=True) as db:
            if self.get(db)['state'] in {'running', 'stopping'}:
                raise ToolError('请先停止播放，再清理历史数据', 409, 'playback_active')
            if db.execute("SELECT 1 FROM processing_tasks WHERE state='processing' LIMIT 1").fetchone():
                raise ToolError('仍有处理中任务，请等待完成或处理租约后再清理', 409, 'tasks_active')
            existing={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tables=tuple(t for t in ('legacy_v2_agent_evidence','legacy_v2_agent_results','legacy_v2_processing_tasks','legacy_v2_agent_consumers') if t in existing)+tables
            counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}
            backups = self.root / 'backups'
            backups.mkdir(exist_ok=True)
            os.chmod(backups, 0o700)
            backup = backups / ('player-before-clear-' + str(now_us()) + '.sqlite3')
            # The write reservation fences all writers. A separate read connection
            # snapshots committed history without backing up an open write transaction.
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
            os.chmod(backup, 0o600)
            for table in tables:
                db.execute('DELETE FROM ' + table)
            state = dict(schema_version=3, state='idle', scenario=None, started_at=None,
                         ended_at=None, duration_sec=30, current_second=0,
                         counts=dict(cv=0, sensor=0, position=0, ppe=0), error=None)
            # Never reuse evidence IDs or ingest cursors referenced by other services.
            db.execute('UPDATE player_state SET state_json=?, legacy_imported=1 WHERE id=1', (canonical(state),))
        return dict(ok=True, deleted=counts, backup=backup.relative_to(self.root).as_posix(),
                    player=state)

    def begin(self, scenario):
        with self.db(write=True) as db:
            if self.get(db)["state"] in {"running", "stopping"}: raise ToolError("Playback already active", 409, "conflict")
            last_end = db.execute("SELECT MAX(window_end_us) FROM ppe_second_stats").fetchone()[0] or 0
            start = max(now_us(), last_end)
            state = dict(schema_version=3, state="running", scenario=scenario, started_at=iso(start), ended_at=None,
                         duration_sec=scenario_duration(scenario), current_second=0, counts=dict(cv=0, sensor=0, position=0, ppe=0), error=None)
            db.execute("UPDATE player_state SET state_json=? WHERE id=1", (canonical(state),))
        return state

    def commit(self, scenario, second, batches, snapshot, start_us, update_player=True):
        window_start = start_us + second * 1_000_000
        received = now_us()
        with self.db(write=True) as db:
            existing = db.execute("SELECT COUNT(*) FROM ppe_second_stats WHERE window_start_us=?", (window_start,)).fetchone()[0]
            if existing:
                if existing != len(snapshot["cv_summary"]): raise RuntimeError("Incomplete existing second")
                previous=db.execute("SELECT DISTINCT scenario FROM ppe_second_stats WHERE window_start_us=?",(window_start,)).fetchall()
                if len(previous)!=1 or previous[0][0]!=scenario:raise RuntimeError("Conflicting data at an existing timestamp")
                return
            seq = db.execute("SELECT next_sequence FROM player_state WHERE id=1").fetchone()[0]
            counts = dict(cv=0, sensor=0, position=0, ppe=0)
            entries = []
            for source, logs in batches.items():
                for log in logs:
                    t = float(log["scenario_time"].rstrip("s"))
                    if source == "cv": entries.append((t, source, log["person_id"], log))
                    else:
                        for key, value in log["readings" if source == "sensor" else "positions"].items():
                            entries.append((t, source, key, value))
            entries.sort(key=lambda item: (item[0], item[1], item[2]))
            for t, source, key, value in entries:
                seq += 1; stamp = start_us + round(t * 1_000_000)
                if source == "cv":
                    det = {d["class_name"]: d for d in value["detections"]}
                    db.execute("INSERT INTO cv_observations(ingest_seq,person_id,camera_id,observed_at_us,received_at_us,scenario,scenario_second,helmet,helmet_confidence,goggles,goggles_confidence,protective_suit,suit_confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (seq, key, "cam_03", stamp, received, scenario, t, int(det["helmet"]["value"]), det["helmet"]["confidence"], int(det["goggles"]["value"]), det["goggles"]["confidence"], int(det["protective_suit"]["value"]), det["protective_suit"]["confidence"]))
                elif source == "sensor":
                    db.execute("INSERT INTO sensor_readings(ingest_seq,sensor_id,observed_at_us,received_at_us,scenario,scenario_second,value,value_type,status) VALUES(?,?,?,?,?,?,?,?,?)",
                               (seq, key, stamp, received, scenario, t, float(value["value"]), "boolean" if type(value["value"]) is bool else "number", value["status"]))
                else:
                    db.execute("INSERT INTO position_readings(ingest_seq,person_id,area_id,observed_at_us,received_at_us,scenario,scenario_second,x,y,speed) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (seq, key, value["area_id"], stamp, received, scenario, t, *value["position"][:2], value["speed"]))
                counts[source] += 1
            for pid, stats in snapshot["cv_summary"].items():
                seq += 1
                db.execute("INSERT INTO ppe_second_stats(ingest_seq,person_id,window_start_us,window_end_us,received_at_us,scenario,scenario_second,total_frames,helmet_missing_count,helmet_missing_ratio,goggles_missing_count,goggles_missing_ratio,suit_missing_count,suit_missing_ratio,is_violating) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (seq, pid, window_start, window_start+1_000_000, received, scenario, second, stats["total_frames"], stats["helmet_missing_count"], stats["helmet_missing_ratio"], stats["goggles_missing_count"], stats["goggles_missing_ratio"], stats["suit_missing_count"], stats["suit_missing_ratio"], int(stats["is_violating"])))
                counts["ppe"] += 1
            db.execute("UPDATE player_state SET next_sequence=? WHERE id=1", (seq,))
            db.execute('INSERT INTO acquisition_seconds(window_start_us,window_end_us) VALUES(?,?)',(window_start,window_start+1000000))
            if update_player:
                state = self.get(db)
                if parse_time(state["started_at"]) != start_us: raise RuntimeError("Playback time base changed")
                for source, count in counts.items(): state["counts"][source] += count
                state["current_second"] = second + 1
                db.execute("UPDATE player_state SET state_json=? WHERE id=1", (canonical(state),))

    def intervals(self, db, start=None, end=None):
        sql="SELECT window_start_us,window_end_us FROM acquisition_seconds WHERE 1=1";args=[]
        if start is not None:sql+=" AND window_end_us>?";args.append(start)
        if end is not None:sql+=" AND window_start_us<?";args.append(end)
        return [tuple(row) for row in db.execute(sql+" ORDER BY window_start_us",args)]

    def coverage(self, db, start, end):
        cursor = start; missing = []
        for left, right in self.intervals(db,start,end):
            if right <= start: continue
            if left >= end: break
            if left > cursor: missing.append([iso(cursor), iso(min(left, end))])
            cursor = max(cursor, min(right, end))
        if cursor < end: missing.append([iso(cursor), iso(end)])
        return dict(complete=not missing, missing_ranges=missing)

    def data_status(self):
        with self.db() as db:
            db.execute("BEGIN")
            intervals = [tuple(r) for r in reversed(db.execute("SELECT window_start_us,window_end_us FROM acquisition_seconds ORDER BY window_start_us DESC LIMIT 5").fetchall())]; recent = None
            if intervals:
                left, right = intervals[-1]
                for a, b in reversed(intervals[:-1]):
                    if b != left or right-a > 5_000_000: break
                    left = a
                recent = [iso(left), iso(right)]
            return dict(database=str(self.path), schema_version=3, player=self.get(db),
                        earliest_time=iso(db.execute("SELECT MIN(window_start_us) FROM acquisition_seconds").fetchone()[0]) if intervals else None,
                        committed_through=iso(intervals[-1][1]) if intervals else None, latest_window=recent,
                        counts={s: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for s,t in TABLES.items()},
                        persons=[dict(r) for r in db.execute("SELECT * FROM persons")],
                        sensors=[dict(r) for r in db.execute("SELECT * FROM sensors")],
                        areas=[dict(r) for r in db.execute("SELECT area_id,area_type,is_danger_zone FROM areas")])

    def encode_cursor(self, kind, filters, sequence, cutoff=None):
        key = hashlib.sha256(canonical([kind, filters]).encode()).hexdigest()
        return base64.urlsafe_b64encode(canonical(dict(key=key, seq=sequence, cutoff=cutoff)).encode()).decode()

    def decode_cursor(self, kind, filters, token):
        key = hashlib.sha256(canonical([kind, filters]).encode()).hexdigest()
        try:
            item = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
            if item["key"] != key or type(item["seq"]) is not int or item["seq"] < 0: raise ValueError()
            if item.get("cutoff") is not None and (type(item["cutoff"]) is not int or item["cutoff"] < item["seq"]): raise ValueError()
            return item["seq"], item.get("cutoff")
        except Exception: raise ToolError("Cursor does not match this query")

    def record(self, db, source, row):
        value = dict(row); value["record_ref"] = dict(table=TABLES[source], id=value["id"])
        value["observed_at"] = iso(value.get("observed_at_us", value.get("window_start_us")))
        value["received_at"] = iso(value["received_at_us"])
        if source == "ppe":
            value["time_range"] = [iso(value["window_start_us"]), iso(value["window_end_us"])]
            value["is_violating"] = bool(value["is_violating"])
        if source == "sensor":
            if value["value_type"] == "boolean": value["value"] = bool(value["value"])
            value["device"] = dict(db.execute("SELECT * FROM sensors WHERE sensor_id=?", (value["sensor_id"],)).fetchone())
        if source == "position":
            value["in_danger_zone"] = bool(db.execute("SELECT is_danger_zone FROM areas WHERE area_id=?", (value["area_id"],)).fetchone()[0])
        return dict(source=source, record_ref=value["record_ref"], data=value)

    def records(self, filters, token=None, limit=100, live=False):
        if not 1 <= limit <= 1000: raise ToolError("limit must be 1..1000")
        source = filters.get("source", "all")
        if source not in {*TABLES, "all"}: raise ToolError("Invalid source")
        start, end = bounds(filters.get("from_time"), filters.get("to_time"))
        seq, cutoff = self.decode_cursor("pull" if live else "records", filters, token) if token else (0, None)
        with self.db() as db:
            db.execute("BEGIN")
            high = db.execute("SELECT next_sequence FROM player_state WHERE id=1").fetchone()[0]
            if filters.get("task_id") is not None:
                if live or start is not None: raise ToolError("Use task_id only for a bounded records query")
                task=db.execute("SELECT * FROM processing_tasks WHERE id=?",(filters["task_id"],)).fetchone()
                if not task: raise ToolError("Unknown task",404,"not_found")
                start,end=task["window_start_us"],task["window_end_us"]
                cutoff=task["cutoff_sequence"]
            elif live or cutoff is None: cutoff = high
            candidates = []
            for kind in TABLES if source == "all" else [source]:
                pid, sid = filters.get("person_id"), filters.get("sensor_id")
                if (pid and kind == "sensor") or (sid and kind != "sensor"): continue
                time_column = "window_start_us" if kind == "ppe" else "observed_at_us"
                sql = f"SELECT * FROM {TABLES[kind]} WHERE ingest_seq>? AND ingest_seq<=?"; args = [seq, cutoff]
                if start is not None: sql += f" AND {time_column}>=? AND {time_column}<?"; args += [start, end]
                if pid: sql += " AND person_id=?"; args.append(pid)
                if sid: sql += " AND sensor_id=?"; args.append(sid)
                candidates.extend((r["ingest_seq"], kind, r) for r in db.execute(sql+" ORDER BY ingest_seq LIMIT ?", [*args, limit+1]))
            candidates.sort(key=lambda item: item[0]); selected = candidates[:limit]
            last = selected[-1][0] if selected else seq
            items=[]
            for _,kind,row in selected:
                item=self.record(db,kind,row)
                stamp=row['window_start_us'] if kind=='ppe' else row['observed_at_us']
                bucket=db.execute('SELECT state,task_id FROM acquisition_seconds WHERE window_start_us<=? AND window_end_us>?',(stamp,stamp)).fetchone()
                item['processing']=dict(task_id=bucket['task_id'] if bucket else None,state=bucket['state'] if bucket else 'pending')
                items.append(item)
            return dict(items=items,
                        next_cursor=self.encode_cursor("pull" if live else "records", filters, last, None if live else cutoff),
                        has_more=len(candidates)>limit, cutoff_sequence=cutoff)

    def get_record(self,source,ident):
        if source not in TABLES: raise ToolError("Invalid source")
        with self.db() as db:
            row=db.execute(f"SELECT * FROM {TABLES[source]} WHERE id=?",(ident,)).fetchone()
            if not row: raise ToolError("Unknown record",404,"not_found")
            return self.record(db,source,row)

    def window(self, from_time=None, to_time=None, task_id=None):
        with self.db() as db:
            db.execute("BEGIN")
            cutoff = db.execute("SELECT next_sequence FROM player_state WHERE id=1").fetchone()[0]
            if task_id is not None:
                if from_time is not None or to_time is not None: raise ToolError("Use task_id or time bounds")
                task = db.execute("SELECT * FROM processing_tasks WHERE id=?", (task_id,)).fetchone()
                if not task: raise ToolError("Unknown task", 404, "not_found")
                start, end, cutoff = task["window_start_us"], task["window_end_us"], task["cutoff_sequence"]
            else:
                start, end = bounds(from_time, to_time)
                if start is None: raise ToolError("Provide task_id or from_time/to_time")
            if end-start > 300_000_000: raise ToolError("Window may not exceed 300 seconds; use query records for longer history")
            result = dict(time_range=[iso(start),iso(end)], cutoff_sequence=cutoff, completeness=self.coverage(db,start,end))
            for kind in ("ppe", "sensor", "position"):
                column = "window_start_us" if kind == "ppe" else "observed_at_us"
                rows = db.execute(f"SELECT * FROM {TABLES[kind]} WHERE {column}>=? AND {column}<? AND ingest_seq<=? ORDER BY {column},id", (start,end,cutoff))
                result[kind] = [self.record(db,kind,row) for row in rows]
            result["persons"] = [dict(r) for r in db.execute("SELECT * FROM persons")]
            result["sensors"] = [dict(r) for r in db.execute("SELECT * FROM sensors")]
            result["areas"] = [dict(r) for r in db.execute("SELECT area_id,area_type,is_danger_zone FROM areas")]
            return result
