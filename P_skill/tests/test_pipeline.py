import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from industrial_skills.store import initialize, digest, iso, Store
from industrial_skills.acquisition import initialize_source, source_store
from industrial_skills import p6, p7, disposition, main
from mock_player.storage import Store as PlayerStore
from mock_player.engine import second_data
from mock_player.aggregation import aggregate
from mock_player.tasks import Tasks

START = '2026-10-13T00:00:00+00:00'


def config(url):
    return {'agents': {'p6':['p6'],'p7':['p7'],'p8p9':['p8p9'],'main':['main']}, 'consumer_id':'test',
       'window_seconds':2,'session_id':'test','start_time':START,'work_permit':{'permit_id':'TEST-ONLY','job_id':'20261013000000001'},
       'player_url':url, 'operators':['ou_worker'],'reviewers':['ou_reviewer'],'test_mode':True,
       'rules':[{'source':'ppe','subject_id':'1','risk_item':risk,'field':field,'operator':'gt','threshold':0.5,'recovery_seconds':2}
                for risk,field in [('helmet','helmet_missing_ratio'),('goggles','goggles_missing_ratio')]]}


def seed(directory):
    store = PlayerStore(directory)
    start = datetime.fromisoformat(START)
    for sec in range(6):
        batch = second_data('A',sec,start)
        snapshot = aggregate(sec,batch['cv'],batch['sensor'],batch['position'])
        store.commit('A',sec,batch,snapshot,int(start.timestamp()*1e6),False)
    # Fixture mutates only synthetic data to force cross-window continuity and parallel risks.
    with store.db(write=True) as db:
        pid = db.execute('SELECT person_id FROM ppe_second_stats LIMIT 1').fetchone()[0]
        db.execute("UPDATE ppe_second_stats SET helmet_missing_ratio=CASE WHEN scenario_second<4 THEN 1 ELSE 0 END, goggles_missing_ratio=CASE WHEN scenario_second<2 THEN 1 ELSE 0 END WHERE person_id=?",(pid,))
    store.update(state='completed',started_at=START,scenario='A',current_second=6)
    return store,pid


@pytest.fixture
def pipeline(tmp_path):
    player,pid=seed(tmp_path/'player')
    tasks=Tasks(player)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import urlsplit,parse_qs
            p=urlsplit(self.path);q={k:v[0] for k,v in parse_qs(p.query).items()}
            if p.path=='/v4/data/windows':
                from mock_player.windows import list_windows
                result=list_windows(player,**{k:int(v) if k in ('limit','window_seconds') else v for k,v in q.items()})
            elif p.path=='/v2/data/window':result=player.window(**{k:int(v) if k=='task_id' else v for k,v in q.items()})
            elif p.path=='/v2/player/status':result=player.get()
            elif p.path=='/v2/data/status':result=player.data_status()
            elif p.path=='/v2/data/seconds':result=tasks.seconds(limit=int(q.get('limit',100)))
            elif p.path.startswith('/v2/tasks/'):result=tasks.get(int(p.path.rsplit('/',1)[1]))
            else:result=tasks.list(q.get('consumer_id'),limit=int(q.get('limit',100)))
            self.reply(result)
        def do_POST(self):
            a=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                if self.path=='/v2/consumers':result=tasks.register(**a)
                elif self.path=='/v2/tasks/claim':result=tasks.claim(**a)
                else:
                    _,_,_,ident,action=self.path.split('/')
                    if action=='complete':a['value']=a.pop('result')
                    result=getattr(tasks,action)(int(ident),**a)
                self.reply(result)
            except Exception as e:self.reply({'error':type(e).__name__},409)
        def reply(self,result,status=200):
            raw=json.dumps(result).encode();self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root=tmp_path/'shared';root.mkdir()
    cfg=config('http://127.0.0.1:'+str(server.server_port))
    for rule in cfg['rules']:rule['subject_id']=pid
    store=initialize(root,'20261013000000001',cfg)
    initialize_source(root,'industrial',{'agents':{'p6':['p6'],'main':['main']},'player_url':cfg['player_url'],'rules':cfg['rules'],'allowed_job_ids':[store.job_id],'default_window_seconds':2})
    with store.db() as db:
        store.put(db,'acquisition_link','current',{'source_id':'industrial'})
        store.put(db,'time_schedule','current',{'window_seconds':2,'max_parallel':3})
    src=source_store(root,'industrial')
    with src.db() as db:src.put(db,'active_job','current',{'job_id':store.job_id})
    def context(role):
        import uuid
        return {'agent_id':role,'source_id':'industrial',**({'source_id':'industrial'} if role=='p6' else {'job_id':store.job_id}),'run_id':str(uuid.uuid4()) if role=='p6' else 'test-'+role,'storage':{'p6_p7':str(root)}}
    context.source=source_store(root,'industrial')
    context.player = player
    yield store,context
    server.shutdown();server.server_close();player.close()


def monitor(context):
    actor=context('p6');context.last_p6=actor
    claim=p6.read_current_window({},actor)
    args={'summary':'Synthetic window evidence','descriptions':[{'observation':i,'explanation':'Observed PPE missing'} for i in claim['required_observations']],'limitations':['simulated source']}
    return claim,args,p6.save_current_description(args,actor)


def assess(context):
    ctx=context('p7');claim=p7.claim_description({},ctx)
    facts=claim['record']['payload']['observations']
    args={'assessments':[{'key':o['key'],'risk_level':2,'basis':'PPE required by permit','advice':['Restore protective equipment']} for o in facts if any(s['state']=='abnormal' for s in o['segments'])]}
    return claim,args,p7.complete_assessment(args,ctx)


def test_continuity_parallel_risks_idempotency_and_report_gate(pipeline):
    store,context=pipeline
    first,args,saved=monitor(context)
    assert p6.save_current_description(args,context.last_p6)==saved
    _,assessment,result=assess(context)
    assert not result['closed_events']
    assert p7.complete_assessment(assessment,context('p7'))==result
    with store.db() as db:events=store.rows(db,'event')
    assert len(events)==2
    with pytest.raises(ValueError,match='EVENT_NOT_CLOSED'):
        p7.report_closed_event({'event_id':events[0]['id'],'summary':'bad'},context('p7'))
    monitor(context);_,_,second=assess(context)
    assert len(second['closed_events'])==1
    monitor(context);_,_,third=assess(context)
    assert len(third['closed_events'])==1
    with store.db() as db:
        events=store.rows(db,'event')
        assert len(events)==2
        helmet=next(e for e in events if e['risk_item']=='helmet')
        assert len(helmet['record_ids'])==3
    for event in events:
        report=p7.report_closed_event({'event_id':event['id'],'summary':'closed with recovery evidence'},context('p7'))
        assert p7.report_closed_event({'event_id':event['id'],'summary':'closed with recovery evidence'},context('p7'))==report
    main.close_time_monitoring({'cutoff':'2026-10-13T00:00:06+00:00','reason':'Explicit test monitoring cutoff'},context('main'))
    assert not disposition.verify_job_closable({},context('p8p9'))['closable']
    exported=main.prepare_report_data({},context('main'))
    assert exported['contains_mock'] and (store.root/exported['data_root']/'jobs'/store.job_id/'closure_state.json').exists()


def test_lease_scope_failed_predecessor_and_retry(pipeline):
    store,context=pipeline
    monitor(context);monitor(context)
    claim=p7.claim_description({},context('p7'))
    assert p7.claim_description({},context('p7'))['status']=='blocked'
    p7.fail_description({'claim':claim['claim'],'reason':'test failure'},context('p7'))
    assert p7.claim_description({},context('p7'))['status']=='blocked'
    main.retry_failed({'kind':'description','record_id':claim['claim']['id'],'reason':'explicit retry'},context('main'))
    again=p7.claim_description({},context('p7'))
    with pytest.raises(ValueError,match='LEASE_INVALID'):
        p7.complete_assessment({'assessments':[]},context('p7'))
    forged=context('p7');forged['agent_id']='unauthorized'
    with pytest.raises(ValueError,match='ROLE_FORBIDDEN'):p7.claim_description({},forged)
    with store.db() as db:
        row=store.get(db,'description',again['claim']['id']);store.put(db,'description',row['id'],{**row,'expiry':time.time()-1})
    assert p7.claim_description({},context('p7'))['status']=='blocked'


def test_concurrent_claim_is_single_writer(pipeline):
    _,context=pipeline;monitor(context)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values=list(pool.map(lambda _:p7.claim_description({},context('p7')),range(4)))
    assert sum(v['status']=='processing' for v in values)==1


def test_missing_evidence_and_unknown_do_not_close(pipeline):
    store,context=pipeline
    monitor(context);assess(context)
    monitor(context)
    with store.db() as db:
        row=next(r for r in store.rows(db,'description') if r['status']=='pending')
        for o in row['payload']['observations']:
            for s in o['segments']:s.update(state='unknown',evidence_refs=[])
        store.put(db,'description',row['id'],row)
    _,_,result=assess(context)
    assert not result['closed_events']
    with store.db() as db:assert all(e['state']=='uncertain' for e in store.rows(db,'event'))


def test_recurrence_after_recovery_is_a_new_event(pipeline):
    store,context=pipeline
    for _ in range(3):monitor(context);assess(context)
    with store.db() as db:
        first=store.rows(db,'description')[0]
        from copy import deepcopy
        row=deepcopy(first);row.update(id='recurrence',status='pending',time_range=['2026-10-13T00:00:06+00:00','2026-10-13T00:00:08+00:00'])
        row['payload']['observations']=deepcopy(first['payload']['observations'])
        for observation in row['payload']['observations']:
            observation['segments']=[{'state':'abnormal','time_range':row['time_range'],'evidence_refs':[]}]
        store.put(db,'description','recurrence',row)
        store.put(db,'window','recurrence',{'status':'completed','publish_state':'ready','time_range':row['time_range']})
    assess(context)
    with store.db() as db:
        events=store.rows(db,'event')
        assert len(events)==4 and sum(e['state']=='open' for e in events)==2
