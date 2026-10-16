"""Atomic v2 -> v3 ledger upgrade. Old consumer ledgers remain archival only."""
import json
import sqlite3
from pathlib import Path
from contextlib import closing


def schema(db):
    for statement in (Path(__file__).parent/'schema.sql').read_text().split(';'):
        if statement.strip(): db.execute(statement)


def upgrade(store, db):
    version=db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0,2,3): raise RuntimeError(f'Unsupported database version: {version}')
    if version==3:return
    db.execute('BEGIN IMMEDIATE')
    if version==2:
        if db.execute("SELECT 1 FROM processing_tasks WHERE state='processing'").fetchone():
            raise RuntimeError('Finish active v2 leases before upgrade')
        backup=store.root/'backups';backup.mkdir(exist_ok=True)
        from .storage import now_us
        target=backup/('player-before-v3-'+str(now_us())+'.sqlite3')
        with closing(sqlite3.connect(store.path)) as src,closing(sqlite3.connect(target)) as dst:src.backup(dst)
        import os
        os.chmod(target,0o600)
        for index in ('task_queue','result_time','evidence_result'):db.execute('DROP INDEX IF EXISTS '+index)
        for table in ('agent_consumers','processing_tasks','agent_results','agent_evidence'):
            db.execute('ALTER TABLE '+table+' RENAME TO legacy_v2_'+table)
    schema(db)
    if version==2:
        db.execute('INSERT INTO acquisition_seconds(window_start_us,window_end_us) SELECT DISTINCT window_start_us,window_end_us FROM ppe_second_stats')
        old=db.execute("SELECT * FROM legacy_v2_processing_tasks WHERE attempt_count>0 ORDER BY state='completed' DESC,id").fetchall()
        for row in old:
            buckets=db.execute('SELECT * FROM acquisition_seconds WHERE window_start_us>=? AND window_start_us<?',(row['window_start_us'],row['window_end_us'])).fetchall()
            if not buckets or any(b['task_id'] is not None for b in buckets):continue
            state=row['state'] if row['state'] in ('completed','failed','retryable') else 'failed'
            columns=[c[1] for c in db.execute('PRAGMA table_info(processing_tasks)')]
            value=dict(row);value['window_seconds']=max(1,min(300,(row['window_end_us']-row['window_start_us'])//1000000));value['max_attempts']=3;value['state']=state
            db.execute('INSERT INTO processing_tasks('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[value.get(c) for c in columns])
            db.execute('UPDATE acquisition_seconds SET state=?,task_id=? WHERE window_start_us>=? AND window_start_us<?',(state,row['id'],row['window_start_us'],row['window_end_us']))
        # Never reuse IDs of unclaimed v2 task indexes discarded from the active ledger.
        high=db.execute("SELECT seq FROM sqlite_sequence WHERE name='legacy_v2_processing_tasks'").fetchone()
        if high:
            db.execute("INSERT INTO sqlite_sequence(name,seq) SELECT 'processing_tasks',? WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='processing_tasks')",(high[0],))
            db.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='processing_tasks'",(high[0],))
        state=json.loads(db.execute('SELECT state_json FROM player_state WHERE id=1').fetchone()[0]);state['schema_version']=3
        db.execute('UPDATE player_state SET state_json=? WHERE id=1',(json.dumps(state),))
