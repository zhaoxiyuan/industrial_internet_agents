"""Verify loaded tools with a read-only business query; no model or Feishu call."""
import json
import sqlite3
from pathlib import Path
from urllib.request import Request, urlopen

root=Path('/opt/industrial-agents')
setup=json.loads((root/'skill-setup-evidence.json').read_text())
token=(root/'state/platform/.cli-admin-token').read_text().strip()
request=Request('http://127.0.0.1:18770/api/cli/run',
    data=json.dumps({'agent_id':setup['roles']['main'],'command':'query_pipeline','arguments':{}}).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
with urlopen(request,timeout=90) as response: result=json.load(response)
assert result['ok'],result
assert result['structuredContent']['status']=='work_permit_required',result
db=sqlite3.connect(root/'state/shared/P6_P7.sqlite3')
assert db.execute('select count(*) from objects').fetchone()[0]==0
assert db.execute("select count(*) from acquisition_objects where kind!='config'").fetchone()[0]==0
print(json.dumps({'docker_skill_execution':'passed','pipeline_status':result['structuredContent']['status'],
    'business_jobs':0,'history_migrated':False,'model_called':False,'feishu_message_sent':False}))
