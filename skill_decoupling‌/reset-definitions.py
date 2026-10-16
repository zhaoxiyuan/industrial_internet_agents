"""Explicit admin reset: backup, then clear Agent/tool definitions and execution state."""
import argparse, json, sqlite3, shutil
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
if not args.execute:parser.error('Explicit --execute is required; stop the platform first')
data=ROOT/'data';backup=ROOT/'.runtime'/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True)
source=data/'platform.db'
if source.exists():
 with sqlite3.connect(source) as db, sqlite3.connect(backup/'platform.db') as saved:db.backup(saved)
 kinds=['agents','bindings','agent_grants','mcps','cli_services','http_services','sessions','operations','tool_runs','tool_invocations','tool_candidates']
 with sqlite3.connect(source) as db:
  counts=dict(db.execute('SELECT kind,count(*) FROM entities GROUP BY kind'))
  db.executemany('DELETE FROM entities WHERE kind=?',[(k,) for k in kinds])
  db.execute("DELETE FROM secrets WHERE id LIKE 'model:%' OR id LIKE 'mcp:%' OR id LIKE 'direct:%'")
  for table in ['messages','tasks','task_checkpoints','task_events','runtime_events']:
   if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('DELETE FROM '+table)
  for id,raw in db.execute("SELECT id,data FROM entities WHERE kind='connections'").fetchall():
   row=json.loads(raw)
   if row.get('inbound_agent_id'):
    row['inbound_agent_id']='';db.execute("UPDATE entities SET data=? WHERE kind='connections' AND id=?",(json.dumps(row,ensure_ascii=False),id))
 for name in ['checkpoints.sqlite3','checkpoints.sqlite3-wal','checkpoints.sqlite3-shm']:
  path=data/name
  if path.exists():shutil.move(str(path),str(backup/name))
 print(json.dumps({'backup':str(backup),'cleared':{k:counts.get(k,0) for k in kinds},'preserved':['skills','connections','mock_player data']},ensure_ascii=False))
else:print('No existing platform database')
