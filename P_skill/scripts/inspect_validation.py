"""Read-only concise diagnostics; never prints model/Feishu credential tables."""
import json
import sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'.runtime'
db=sqlite3.connect(root/'platform/data/platform.db')
for table in ('tasks', 'task_events'):
    exists=db.execute("SELECT name FROM sqlite_master WHERE name=?",(table,)).fetchone()
    if exists:
        print(table,[r[1] for r in db.execute('PRAGMA table_info('+table+')')])
for raw, in db.execute("SELECT data FROM entities WHERE kind='tool_runs' ORDER BY created DESC LIMIT 12"):
    row=json.loads(raw)
    print(json.dumps({k:row[k] for k in ('run_id','tool','error','transport') if k in row},ensure_ascii=False))
for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    if 'task' in row[0]:print('table',row[0])
business=sqlite3.connect(root/'shared/P6_P7.sqlite3')
print('business',business.execute('SELECT kind,COUNT(*) FROM objects GROUP BY kind').fetchall())
for kind,raw in business.execute("SELECT kind,data FROM objects WHERE kind IN ('window','description','event')"):
    r=json.loads(raw);print(kind,{k:r[k] for k in ('id','status','error','publish_state','state','time_range') if k in r})
for raw, in db.execute('SELECT payload FROM tasks ORDER BY rowid DESC LIMIT 5'):
    r=json.loads(raw);print('task',r.get('id'),r.get('status'),str(r.get('result'))[:2200])
