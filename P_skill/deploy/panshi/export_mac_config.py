"""Export configuration allowlists only; SQLite sources are opened read-only."""
import json
import os
import sqlite3
from pathlib import Path

root = Path.home()/'IndustrialAgents/p_skill_validation/source/P_skill/.runtime'
db = sqlite3.connect(f'file:{root}/platform/data/platform.db?mode=ro', uri=True)
def rows(kind):
    return [json.loads(x[0]) for x in db.execute('select data from entities where kind=?', (kind,))]
names = {'industrial-main','p6-monitor','p7-risk-assessment','p8p9-disposition'}
skills = {s['id']:s['name'] for s in rows('skills') if s['name'] in names}
agents = [a for a in rows('agents') if any(s in skills for s in a['skill_ids'])]
assert len(agents) == 4
agent_ids = {a['id'] for a in agents}
bindings = [b for b in rows('bindings') if b['agent_id'] in agent_ids and (b['source'].startswith('skill:') or b['source']=='builtin')]
connection_ids = {b['connection_ref'] for b in bindings if b['source']=='builtin'}
fields = {'app_id':'FEISHU_APP_ID','app_secret':'FEISHU_APP_SECRET','verification_token':'FEISHU_VERIFICATION_TOKEN','encrypt_key':'FEISHU_ENCRYPT_KEY','api_base':'FEISHU_API_BASE','groups':'FEISHU_GROUP_MAP','users':'FEISHU_USER_MAP'}
connections = []
for c in rows('connections'):
    if c['id'] not in connection_ids:
        continue
    env = {}
    for line in (root/'platform/data/functions'/c['id']/'.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value = line.split('=',1)
            try: env[key] = json.loads(value)
            except ValueError: env[key] = value.strip('\"\'')
    connections.append({'id':c['id'],'name':c['name'],'enabled':c['enabled'],'config':{k:env.get(v, {} if k in ('groups','users') else '') for k,v in fields.items()}})
for a in agents:
    key = db.execute('select value from secrets where id=?',(a['secret_ref'],)).fetchone()
    a['api_key'] = key[0] if key else ''
    a['skill_names'] = [skills[s] for s in a['skill_ids'] if s in skills]
business = sqlite3.connect((root/'shared/P6_P7.sqlite3').as_uri()+'?mode=ro&immutable=1', uri=True)
source = json.loads(business.execute("select data from acquisition_objects where source_id='industrial' and kind='config' and id='source'").fetchone()[0])
source = {k:source[k] for k in ('rules','default_window_seconds','max_attempts') if k in source}
result = {'agents':agents,'skills':skills,'bindings':bindings,'connections':connections,'source':source,
          'grants':[g for g in rows('agent_grants') if g['caller_agent_id'] in agent_ids and g['target_agent_id'] in agent_ids],
          'card_scopes':[s for s in rows('risk_card_scopes') if s['connection_id'] in connection_ids]}
out = Path.home()/'IndustrialAgents/p_skill_validation/panshi-config-only.json'
out.write_text(json.dumps(result),encoding='utf-8')
os.chmod(out,0o600)
print(json.dumps({'agents':len(agents),'skills':list(skills.values()),'connections':len(connections),'bindings':len(bindings),'grants':len(result['grants']),'runtime_history_exported':False}))
