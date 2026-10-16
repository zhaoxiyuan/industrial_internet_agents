import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:18770/api/overview',timeout=20) as response:o=json.load(response)
active=[{'id':t['id'],'agent':t['executor_agent_id'],'status':t['status'],'executor_active':t.get('executor_active')} for t in o['tasks'] if t['status'] in ('running','queued','waiting') or t.get('executor_active')]
print(json.dumps({'active_tasks':active,'agent_names':{a['id']:a['name'] for a in o['agents']}}))
