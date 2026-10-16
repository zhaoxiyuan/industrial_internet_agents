"""Job-scoped SQLite operations. Models never choose paths, identity or SQL."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def seconds(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return result.timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value).encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("IMMUTABLE_FILE_CONFLICT")
        return
    temp = path.with_name("." + secrets.token_hex(6) + ".tmp")
    try:
        with temp.open("xb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Store:
    def __init__(self, root, job_id):
        self.root = Path(root).resolve()
        if not self.root.is_dir() or not job_id:
            raise ValueError("STORAGE_CONFIGURATION_ERROR")
        self.job_id = job_id
        self.path = self.root / "P6_P7.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects(
                    job_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    version INTEGER NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(job_id,kind,id));
                CREATE TABLE IF NOT EXISTS audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    action TEXT NOT NULL, object_id TEXT, at TEXT NOT NULL, data TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, db, kind, key, required=True):
        row = db.execute("SELECT data FROM objects WHERE job_id=? AND kind=? AND id=?", (self.job_id, kind, str(key))).fetchone()
        if not row and required:
            raise ValueError("RECORD_NOT_FOUND_OR_FORBIDDEN")
        return json.loads(row[0]) if row else None

    def rows(self, db, kind):
        return [json.loads(r[0]) for r in db.execute("SELECT data FROM objects WHERE job_id=? AND kind=? ORDER BY id", (self.job_id, kind))]

    def put(self, db, kind, key, value, expected=None):
        state = self.get(db, 'job', 'state', False)
        if state and state.get('status')=='closed':raise ValueError('JOB_ALREADY_CLOSED')
        if getattr(self,'run_id',None) and not getattr(self,'cleanup',False):
            fence=self.get(db,'run_fence',self.run_id,False)
            if fence:raise ValueError('TASK_EXECUTION_REVOKED')
        old = self.get(db, kind, key, False)
        version = old["version"] if old else 0
        if expected is not None and version != expected:
            raise ValueError("VERSION_CONFLICT")
        value = {**value, "id": str(key), "job_id": self.job_id, "version": version + 1}
        db.execute("INSERT INTO objects VALUES(?,?,?,?,?) ON CONFLICT(job_id,kind,id) DO UPDATE SET version=excluded.version,data=excluded.data", (self.job_id, kind, str(key), version+1, canonical(value)))
        return value

    def audit(self, db, action, key, value):
        db.execute("INSERT INTO audit(job_id,action,object_id,at,data) VALUES(?,?,?,?,?)", (self.job_id, action, str(key), stamp(), canonical(value)))

    def config(self):
        with self.db() as db:
            return self.get(db, "config", "job")

    def expire(self, db, kind):
        for row in self.rows(db, kind):
            if row.get("status") == "processing" and row.get("expiry", 0) <= time.time():
                self.put(db, kind, row["id"], {**row, "status": "failed", "error": "lease_expired"})
                self.audit(db, "lease_expired", row["id"], {})

    def claim(self, db, kind, row, actor, duration):
        claim = {"kind": kind, "id": row["id"], "token": secrets.token_urlsafe(24)}
        value = self.put(db, kind, row["id"], {**row, "status": "processing", "token": claim["token"], "actor": actor, "owner_run_id":getattr(self,"run_id",None), "expiry": time.time()+duration, "attempts": row.get("attempts", 0)+1})
        self.audit(db, "claim_"+kind, row["id"], {"actor": actor})
        return {"status": "processing", "claim": claim, "record": public(value)}

    def lease(self, db, claim, actor, kind=None, completed=False):
        if kind and claim.get("kind") != kind:
            raise ValueError("CLAIM_KIND_INVALID")
        row = self.get(db, claim["kind"], claim["id"])
        if row.get("actor") != actor or not secrets.compare_digest(row.get("token", ""), claim.get("token", "!")):
            raise ValueError("LEASE_INVALID")
        if completed and row.get("status") == "completed":
            return row
        if row.get("status") != "processing" or row.get("expiry", 0) <= time.time():
            raise ValueError("LEASE_INVALID")
        return row

    def archive(self, stage, key, value):
        relative = f"executions/{stage}/{digest([self.job_id, key])}.json"
        atomic(self.root / relative, value)
        return relative


def public(row):
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in {"token", "source_token"}}


def context_store(context, role):
    if not context.get("job_id"):raise ValueError("WORK_PERMIT_REQUIRED")
    store = Store(context["storage"]["p6_p7"], context["job_id"])
    store.run_id=context.get('run_id')
    store.cleanup=context.get('_cleanup',False)
    config = store.config()
    if config.get("retired"):raise ValueError("WORK_PERMIT_REQUIRED")
    if context.get("agent_id") not in config.get("agents", {}).get(role, []):
        raise ValueError("ROLE_FORBIDDEN")
    return store, config


def initialize(root, job_id, config):
    """Administrator setup only. No model tool accepts arbitrary configuration."""
    required = {"agents", "consumer_id", "window_seconds", "session_id", "start_time", "rules", "work_permit", "player_url"}
    if not required <= set(config) or not config["work_permit"].get("permit_id"):
        raise ValueError("CONFIGURATION_REQUIRED")
    seconds(config["start_time"])
    if not 1 <= config["window_seconds"] <= 300 or not config["rules"]:
        raise ValueError("CONFIGURATION_INVALID")
    for rule in config["rules"]:
        if not {"risk_item", "source", "subject_id", "recovery_seconds"} <= set(rule) or rule["recovery_seconds"] <= 0:
            raise ValueError("RULE_INVALID")
    store = Store(root, job_id)
    with store.db() as db:
        old = store.get(db, "config", "job", False)
        clean = {k:v for k,v in (old or {}).items() if k not in {"id","job_id","version"}}
        if old and clean != config:
            raise ValueError("CONFIGURATION_IMMUTABLE")
        if not old:
            store.put(db, "config", "job", config)
            store.put(db, "job", "state", {"status":"active", "monitoring_closed":False, "p7_through":config["start_time"]})
    return store
