"""Read-only verification of v5 evidence on the existing public platform. No fixtures."""
import json
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
r=ROOT/'.runtime'
e=json.loads((r/'v5-live-evidence.json').read_text())
def ro(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
p=ro(r/'platform/data/platform.db');b=ro(r/'shared/P6_P7.sqlite3')
def objects(kind):return [json.loads(x[0]) for x in b.execute('SELECT data FROM objects WHERE job_id=? AND kind=?',(e['job_id'],kind))]
tasks=[json.loads(x[0]) for x in p.execute('SELECT payload FROM tasks')]
by_id={t['id']:t for t in tasks}
assert by_id[e['timeout_task']]['status']=='failed'
assert by_id[e['timeout_task']]['error']['code']=='TASK_TIMEOUT'
assert e['timeout_result']['window_status']=='failed'
assert e['late_write_rejected'] in ('P6_RUN_REPLACED','TASK_EXECUTION_REVOKED')
assert e['cv_pagination']['distinct']
descriptions=objects('description');assert len(descriptions)>=2
assert all(len(d['payload']['observations'])==15 and len(d['payload']['descriptions'])==15 for d in descriptions)
assert by_id[e['p7_task']]['status']=='succeeded'
assert any(d['status']=='completed' for d in descriptions)
mains=sorted([t for t in tasks if t.get('schedule_id')==e['heartbeat_plan']],key=lambda t:t.get('started') or t['created'])
assert len(mains)>=2 and all(t.get('session_id') is None and t['root_task_id']==t['id'] for t in mains)
executed=[t for t in mains if t.get('started')]
for previous,current in zip(executed,executed[1:]):assert previous.get('finished') and previous['finished']<=current['started']
assert e['independent_heartbeat']['main_started_while_p6_running']
assert e['manual_main_overlap_rejected']['error']['code']=='AGENT_BUSY'
plan=json.loads(p.execute('SELECT payload FROM schedules WHERE id=?',(e['heartbeat_plan'],)).fetchone()[0])
assert plan['status']=='stopped' and plan['skipped_count']>0
state=objects('job')[0];assert state['status']=='closed' and state['closure_mode']=='human_override' and state['unresolved_at_close']
active=json.loads(b.execute("SELECT data FROM acquisition_objects WHERE source_id='industrial' AND kind='active_job' AND id='current'").fetchone()[0]);assert not active.get('job_id')
assert any(x['status']=='failed' and x.get('error')=='HUMAN_CLOSED_JOB' for x in objects('p6_window'))
assert all(t['status'] in ('succeeded','failed','cancelled','interrupted') for t in tasks if (t.get('business_scope') or {}).get('job_id')==e['job_id'])
connections=[json.loads(x[0]) for x in p.execute("SELECT data FROM entities WHERE kind='connections'")]
assert [x['name'] for x in connections]==['P8P9']
assert not objects('disposition') and not objects('card_intent')
assert by_id[e['claim_recovery_p7']]['status']=='succeeded'
assert by_id[e['claim_recovery_p8']]['error']['code']=='TASK_TIMEOUT'
assert e['lost_reply_recovery']['notification_status']=='failed'
assert e['old_plan_resume_rejected']['error']['code']=='JOB_ALREADY_CLOSED'
assert e['claim_recovery_close']['status']=='closed'
assert not p.execute("SELECT id FROM schedules WHERE status='active'").fetchall()
print(json.dumps({'status':'passed','environment':'existing public Mac platform','job_id':e['job_id'],
 'completed_p6_windows':len(descriptions),'channels_per_window':15,'main_rounds':len(mains),'heartbeat_skips':plan['skipped_count'],
 'human_closed_with_blockers':len(state['unresolved_at_close']),'no_feishu_cards_created':True},ensure_ascii=False))
