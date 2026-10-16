"""Load code packages and configuration, without importing runtime records."""
import base64
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path('/opt/industrial-agents')
config = json.loads((ROOT/'config-only.json').read_text())
os.chmod(ROOT/'config-only.json',0o600)

def api(path, data=None, method=None):
    request = Request('http://127.0.0.1:18770'+path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={'Content-Type':'application/json'},method=method)
    try:
        with urlopen(request,timeout=90) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(path+': '+error.read().decode()) from None

before = api('/api/overview')
print(json.dumps({'existing_agents':len(before['agents']),'existing_sessions':len(before['sessions']),
    'existing_tasks':len(before['tasks']),'existing_schedules':len(before['schedules'])}))
assert not any(t['status'] in ('running','queued','waiting') or t.get('executor_active') for t in before['tasks']), 'Active tasks; inspect before configuring'
skills = {}
for name in config['skills'].values():
    skill = api('/api/skills',{'archive':base64.b64encode((ROOT/'P_skill/dist'/(name+'.zip')).read_bytes()).decode()})
    skills[name] = skill
api('/api/skill-storage/p6_p7',{'directory':str(ROOT/'state/shared'),'enabled':True})
for skill in skills.values():
    relative = Path(skill['path']).relative_to('data/skills').as_posix()
    api('/api/skills/'+skill['id']+'/runtime',{'container':'p-skill-python-executor','code_target':'/skills/'+relative,
        'storage':{'p6_p7':{'resource':'p6_p7','target':'/business'}}})

agents, roles = {}, {}
role_names = {'industrial-main':'main','p6-monitor':'p6','p7-risk-assessment':'p7','p8p9-disposition':'p8p9'}
existing = {a['name']:a for a in before['agents']}
for old in config['agents']:
    payload = {'name':old['name'],'skill_ids':[skills[n]['id'] for n in old['skill_names']]}
    actor = api('/api/agents/'+existing[old['name']]['id'],payload,'PATCH') if old['name'] in existing else api('/api/agents',payload)
    agents[old['id']] = actor['id']
    roles[role_names[old['skill_names'][0]]] = actor['id']

def adapt(value):
    if isinstance(value,dict): return {k:adapt(v) for k,v in value.items()}
    if isinstance(value,list): return [adapt(v) for v in value]
    if isinstance(value,str):
        for old,new in agents.items(): value=value.replace(old,new)
        return value.replace('https://ladylike-overripe-resource.ngrok-free.dev','http://103.236.71.83:18080')
    return value

agent_fields = ['name','description','enabled','model','base_url','api_key','system_prompt','max_tokens','model_timeout',
                'max_model_calls','concurrency','timeout','input_schema','output_schema','contract_version','artifact_output_field']
for old in config['agents']:
    payload = adapt({k:old[k] for k in agent_fields if k in old})
    payload['skill_ids'] = [skills[n]['id'] for n in old['skill_names']]
    api('/api/agents/'+agents[old['id']],payload,'PATCH')

connection_map = {}
for old in config['connections']:
    payload = {**old['config'],'name':old['name'],'enabled':old['enabled'],'inbound_agent_id':''}
    existing_connection = next((c for c in before['connections'] if c['name']==old['name']),None)
    saved = api('/api/connections/'+existing_connection['id'],payload,'PATCH') if existing_connection else api('/api/connections',payload)
    connection_map[old['id']] = saved['id']

api('/api/acquisition-sources/industrial',{**config['source'],'agents':{r:[a] for r,a in roles.items()},
    'player_url':'http://127.0.0.1:18090','allowed_job_ids':[]})
for old in config['bindings']:
    payload = {k:old[k] for k in ['tools','enabled','allowed_targets'] if k in old}
    payload['agent_id'] = agents[old['agent_id']]
    if old['source'].startswith('skill:'):
        name=config['skills'][old['source'][6:]]
        payload.update(source='skill:'+skills[name]['id'],source_id='industrial',job_id='',tools=[t['name'] for t in skills[name]['tools']])
    else:
        payload.update(source='builtin',connection_ref=connection_map[old['connection_ref']])
    previous = next((b for b in before['bindings'] if b['agent_id']==payload['agent_id'] and b['source']==payload['source']),None)
    api('/api/bindings/'+previous['id'],payload,'PATCH') if previous else api('/api/bindings',payload)
for old in config['grants']:
    api('/api/agent-grants',{'caller_agent_id':agents[old['caller_agent_id']], 'target_agent_id':agents[old['target_agent_id']],
        'permissions':old['permissions'],'enabled':old['enabled']})
for old in config['card_scopes']:
    cid=connection_map[old['connection_id']]
    api('/api/connections/'+cid+'/risk-cards',{'storage':str(ROOT/'state/shared'),'source_id':'industrial','job_id':'',
        'target':old['target'],'agents':[agents[a] for a in old['agents'] if a in agents],
        'simulation':bool(old.get('simulation')),'public_base':'http://103.236.71.83:18080'})

after = api('/api/overview')
assert len(skills)==len(agents)==4
for key in ('tasks','sessions','schedules'):
    assert {v['id'] for v in after[key]} == {v['id'] for v in before[key]}, key
for actor in [a for a in after['agents'] if a['id'] in agents.values()]:
    assert actor['key_configured'], 'Model key missing'
    catalog=api('/api/cli/catalog?agent_id='+actor['id'])
    assert catalog
evidence={'agents':[{k:a[k] for k in ('id','name','key_configured')} for a in after['agents']],
          'skills':[{'name':s['name'],'tools':len(s['tools'])} for s in after['skills']],
          'bindings':len(after['bindings']),'grants':len(after['agent_grants']),
          'connections':len(after['connections']),'tasks':len(after['tasks']),'sessions':len(after['sessions']),'schedules':len(after['schedules']),
          'roles':roles,'history_migrated':False}
(ROOT/'skill-setup-evidence.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence))
