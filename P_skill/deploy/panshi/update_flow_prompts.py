"""Update only Main/P6/P7 instructions, preserving model and business configuration."""
import base64,json,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path('/opt/industrial-agents')
sys.path.insert(0,str(ROOT/'P_skill/scripts'))
from explicit_prompt_recipes import RECIPES, TARGET_RULE
def api(path,data=None,method=None):
 with urlopen(Request('http://127.0.0.1:18770'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'},method=method),timeout=90) as r:return json.load(r)
o=api('/api/overview')
assert not any(t['status'] in ('queued','running','waiting') or t.get('executor_active') for t in o['tasks']), 'Active tasks: defer prompt update'
roles={'main':'industrial-main','P6':'p6-monitor','P7':'p7-risk-assessment','p8p9':'p8p9-disposition'}
if '--main-only' in sys.argv:
 roles={'main':'industrial-main'}
agents={r:next(a for a in o['agents'] if a['name']=='P-skill validation '+r.lower()) for r in roles}
backup=ROOT/'prompt-backups'/str(time.time_ns())
backup.mkdir(parents=True,mode=0o700)
(backup/'prompts.json').write_text(json.dumps({r:{'id':a['id'],'system_prompt':a.get('system_prompt','')} for r,a in agents.items()},ensure_ascii=False))
for role,name in roles.items():
 old=next(s for s in o['skills'] if s['name']==name)
 (backup/(name+'.md')).write_bytes((ROOT/'platform'/old['path']/'SKILL.md').read_bytes())
 skill=api('/api/skills',{'archive':base64.b64encode((ROOT/'P_skill/dist'/(name+'.zip')).read_bytes()).decode()})
 assert skill['id']==old['id'], 'Skill identity changed'
 relative=Path(skill['path']).relative_to('data/skills').as_posix()
 api('/api/skills/'+skill['id']+'/runtime',{'container':'p-skill-python-executor','code_target':'/skills/'+relative,'storage':{'p6_p7':{'resource':'p6_p7','target':'/business'}}})
 for b in o['bindings']:
  if b['agent_id']==agents[role]['id'] and b['source']=='skill:'+skill['id']:
   api('/api/bindings/'+b['id'],{'tools':[t['name'] for t in skill['tools']]},'PATCH')
 catalog=api('/api/cli/catalog?agent_id='+agents[role]['id'])['commands']
 names={c['name'].split('__')[-1]:c['name'] for c in catalog if c['name'].startswith('cap_')}
 recipe=RECIPES.get(role, TARGET_RULE+'\n'+agents[role].get('system_prompt',''))
 for short,full in sorted(names.items(),key=lambda pair:-len(pair[0])):
  recipe=recipe.replace(short,full)
 prompt='你是工业监测平台的 '+role+'。使用目录中的完整工具名执行下列流程。读取 Skill 后继续业务调用，不能空回复或只读说明就结束。绑定 Skill ID='+skill['id']+'（'+name+'）。\n'+recipe
 if role not in RECIPES:
  prompt=agents[role].get('system_prompt','')
  if TARGET_RULE not in prompt: prompt=TARGET_RULE+'\n'+prompt
 patch={'system_prompt':prompt}
 if role in RECIPES: patch['max_tokens']=8000
 api('/api/agents/'+agents[role]['id'],patch,'PATCH')
 print(json.dumps({'role':role,'agent_id':agents[role]['id'],'skill_id':skill['id'],'updated':True}))
print('backup='+str(backup))
