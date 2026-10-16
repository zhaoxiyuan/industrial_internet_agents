"""Minimal four-state queue; short transactions, fenced leases and immutable history."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@contextmanager
def database(context):
    root = Path(context['storage']['queue'])
    connection = sqlite3.connect(root / 'P6_P7.sqlite3', timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript('''
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, job TEXT NOT NULL, source TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL, worker TEXT, token TEXT, expires REAL,
          result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0, UNIQUE(job,source));
        CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY, record TEXT, action TEXT, reason TEXT, created REAL);
        ''')
        connection.execute('BEGIN IMMEDIATE')
        for row in connection.execute("SELECT id FROM records WHERE job=? AND status='processing' AND expires<=?", (context['job_id'], time.time())).fetchall():
            connection.execute("UPDATE records SET status='failed',error='lease_expired' WHERE id=?", (row['id'],))
            history(connection, row['id'], 'failed', 'lease_expired')
        yield connection, root
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def history(db, record, action, reason=''):
    db.execute('INSERT INTO history(record,action,reason,created) VALUES(?,?,?,?)', (record, action, reason, time.time()))


def archive(root, record, kind, value):
    directory = root / 'executions' / record
    directory.mkdir(parents=True, exist_ok=True)
    raw = encoded(value)
    name = kind + '-' + hashlib.sha256(raw.encode()).hexdigest() + '.json'
    target = directory / name
    if target.exists():
        if target.read_text(encoding='utf-8') != raw:
            raise ValueError('ARCHIVE_CONFLICT')
        return
    temporary = directory / (uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(raw, encoding='utf-8')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def get(db, context, record):
    row = db.execute('SELECT * FROM records WHERE id=? AND job=?', (record, context['job_id'])).fetchone()
    if not row:
        raise ValueError('RECORD_FORBIDDEN')
    return row


def receipt(row):
    return {'record_id': row['id'], 'status': row['status']}


def lease(row, claim, context):
    if row['status'] != 'processing' or row['token'] != claim['token'] or row['worker'] != context['agent_id'] or row['expires'] <= time.time():
        raise ValueError('LEASE_INVALID')


def save(arguments, context):
    with database(context) as (db, root):
        payload = encoded({'description': arguments['description']})
        old = db.execute('SELECT * FROM records WHERE job=? AND source=?', (context['job_id'], arguments['source_id'])).fetchone()
        if old:
            if old['payload'] != payload:
                raise ValueError('WRITE_CONFLICT')
            return receipt(old)
        record = uuid.uuid4().hex
        archive(root, record, 'P6', json.loads(payload))
        db.execute("INSERT INTO records(id,job,source,payload,status) VALUES(?,?,?,?,'pending')", (record, context['job_id'], arguments['source_id'], payload))
        history(db, record, 'pending')
        return {'record_id': record, 'status': 'pending'}


def claim(arguments, context):
    with database(context) as (db, root):
        row = db.execute("SELECT * FROM records WHERE job=? AND status='pending' ORDER BY rowid LIMIT 1", (context['job_id'],)).fetchone()
        if not row:
            return {'status': 'no_work'}
        token = uuid.uuid4().hex
        db.execute("UPDATE records SET status='processing',worker=?,token=?,expires=?,attempts=attempts+1 WHERE id=?", (context['agent_id'], token, time.time()+arguments['lease_seconds'], row['id']))
        history(db, row['id'], 'processing')
        return {'record_id': row['id'], 'status': 'processing', 'description': json.loads(row['payload'])['description'], 'claim': {'record_id': row['id'], 'token': token}}


def complete(arguments, context):
    with database(context) as (db, root):
        row = get(db, context, arguments['claim']['record_id'])
        result = encoded(arguments['assessment'])
        if row['status'] == 'completed' and row['token'] == arguments['claim']['token'] and row['worker'] == context['agent_id']:
            if row['result'] != result:
                raise ValueError('COMPLETION_CONFLICT')
            return receipt(row)
        lease(row, arguments['claim'], context)
        archive(root, row['id'], 'P7', arguments['assessment'])
        db.execute("UPDATE records SET status='completed',result=? WHERE id=?", (result, row['id']))
        history(db, row['id'], 'completed')
        return {'record_id': row['id'], 'status': 'completed'}


def fail(arguments, context):
    with database(context) as (db, root):
        row = get(db, context, arguments['claim']['record_id'])
        if row['token'] != arguments['claim']['token'] or row['worker'] != context['agent_id']:
            raise ValueError('LEASE_INVALID')
        # A completed assessment is never rolled back by runtime compensation.
        if row['status'] in ('completed', 'failed'):
            return receipt(row)
        lease(row, arguments['claim'], context)
        archive(root, row['id'], 'failure', {'reason': arguments['reason'], 'attempt': row['attempts']})
        db.execute("UPDATE records SET status='failed',error=? WHERE id=?", (arguments['reason'], row['id']))
        history(db, row['id'], 'failed', arguments['reason'])
        return {'record_id': row['id'], 'status': 'failed'}


def retry(arguments, context):
    with database(context) as (db, root):
        row = get(db, context, arguments['record_id'])
        if row['status'] != 'failed':
            raise ValueError('RETRY_REQUIRES_FAILED')
        if row['attempts'] >= 3:
            raise ValueError('RETRY_LIMIT')
        db.execute("UPDATE records SET status='pending',worker=NULL,token=NULL,expires=NULL,error=NULL WHERE id=?", (row['id'],))
        history(db, row['id'], 'retry', arguments['reason'])
        return {'record_id': row['id'], 'status': 'pending'}


def query(arguments, context):
    with database(context) as (db, root):
        row = get(db, context, arguments['record_id'])
        events = [dict(e) for e in db.execute('SELECT action,reason,created FROM history WHERE record=? ORDER BY seq', (row['id'],))]
        return {**receipt(row), 'description': json.loads(row['payload'])['description'], 'assessment': json.loads(row['result']) if row['result'] else None, 'error': row['error'], 'attempts': row['attempts'], 'history': events}
