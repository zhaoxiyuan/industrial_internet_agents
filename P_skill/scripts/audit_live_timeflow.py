"""Read-only assertions against the existing Mac public platform after live runs."""
import json,sqlite3,collections
from pathlib import Path
from urllib.request import urlopen

r=Path.home()/'IndustrialAgents/p_skill_validation/source/P_skill/.runtime'
p=sqlite3.connect('file:'+str(r/'platform/data/platform.db')+'?mode=ro',uri=True)
b=sqlite3.connect('file:'+str(r/'shared/P6_P7.sqlite3')+'?mode=ro',uri=True)
a=sqlite3.connect('file:'+str(r/'player/mock_player.sqlite3')+'?mode=ro',uri=True)
def objects(kind):return [json.loads(x[0]) for x in b.execute('select data from objects where kind=?',(kind,))]
def entities(kind):return [json.loads(x[0]) for x in p.execute('select data from entities where kind=?',(kind,))]
tasks=json.load(urlopen('http://127.0.0.1:18770/api/tasks'))['tasks']
setup=json.loads((r/'setup.json').read_text())
assert not any(t['status'] in ('running','waiting','queued') for t in tasks)
windows=sorted(objects('p6_window'),key=lambda x:x['time_range'][0]);descriptions=objects('description')
assert len(windows)==len(descriptions)==3
assert all(x['status']=='completed' for x in windows+descriptions)
assert all(windows[i]['time_range'][1]==windows[i+1]['time_range'][0] for i in range(2))
p6=[t for t in tasks if t['executor_agent_id']==setup['agents']['p6']]
assert len(p6)==3 and all(t['status']=='succeeded' for t in p6)
assert max(t['started'] for t in p6)<min(t['finished'] for t in p6)
events=objects('event');ds=objects('disposition')
assert collections.Counter(e['state'] for e in events)=={'closed':2,'open':1}
assert len(ds)==3 and all(d['status']=='waiting_human_review' and d['opinion']['decision']=='insufficient' and not d.get('human_review') for d in ds)
assert all(d['delivery']['status']=='simulated' and d['delivery']['sequence']>=2 for d in ds)
assert len({d['delivery']['card_id'] for d in ds})==3
state=objects('job')[0]
assert state['monitoring_closed'] and state['monitoring_cutoff']==windows[-1]['time_range'][1] and state['status']=='active'
assert not any(t['executor_agent_id']==setup['agents']['p10'] for t in tasks)
assert a.execute('select count(*) from processing_tasks').fetchone()[0]==0
assert a.execute('select count(*) from acquisition_seconds').fetchone()[0]==30
skills=entities('skills');counts={s['name']:len(s['tools']) for s in skills}
assert counts['industrial-main']==8 and counts['p6-monitor']==4 and counts['p7-risk-assessment']==6 and counts['p8p9-disposition']==8
retired={'read_work_permits','publish_job_description','claim_window','query_acquisition','set_window_seconds','close_monitoring','save_source_observations'}
assert not any(t['name'] in retired for s in skills for t in s['tools'])
assess=[x[0] for x in b.execute("select object_id from audit where action='complete_assessment' order by seq")]
by_id={x['id']:x for x in descriptions}
assert [by_id[i]['time_range'] for i in assess]==[w['time_range'] for w in windows]
summary={'public_url':'https://ladylike-overripe-resource.ngrok-free.dev/skill/#/skills','session_id':'b34527bdf5814f5f8c98c32955431d02','checks':'passed',
 'tool_counts':counts,'tasks':[{k:t.get(k) for k in ('id','executor_agent_id','status','started','finished','error')} for t in tasks],
 'time_windows':[w['time_range'] for w in windows],'p6_parallel':True,'p7_ordered':True,'events':dict(collections.Counter(e['state'] for e in events)),
 'dispositions':[{'id':d['id'],'status':d['status'],'decision':d['opinion']['decision'],'delivery':d['delivery']} for d in ds],
 'monitoring_cutoff':state['monitoring_cutoff'],'job_status':state['status'],'player_raw_seconds':30,'player_processing_tasks':0,
 'audit_counts':dict(b.execute('select action,count(*) from audit group by action').fetchall()),'p10_calls':0,
 'backup':json.loads((r/'timeflow-reset.json').read_text())['backup']}
(r/'timeflow-final-evidence.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
