"""Deferred task creation and safe upgrade from the previous consumer ledger."""
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from mock_player.storage import Store,parse_time,iso
from mock_player.engine import second_data
from mock_player.aggregation import aggregate
from mock_player.tasks import Tasks
from mock_player.models import ToolError
from mock_player.server import create_app


def seed(root,count=6):
    store=Store(root);stamp=datetime(2026,10,14,tzinfo=timezone.utc);start=parse_time(stamp.isoformat())
    for sec in range(count):
        data=second_data('A',sec,stamp);store.commit('A',sec,data,aggregate(sec,data['cv'],data['sensor'],data['position']),start,False)
    store.update(state='completed',started_at=stamp.isoformat(),scenario='A',current_second=count)
    return store,start


def complete(tasks,t):return tasks.complete(t['task_id'],t['lease_token'],{'schema_version':3,'result_id':'source-'+str(t['task_id']),'agent_id':'p6'})


def test_claim_only_creates_one_index_and_all_records_show_status(tmp_path):
    store,start=seed(tmp_path);tasks=Tasks(store)
    assert tasks.list()['items']==[] and tasks.seconds()['counts']['pending']==6
    records=store.records({'source':'ppe'},limit=100)
    assert all(r['processing']=={'task_id':None,'state':'pending'} for r in records['items'])
    a=tasks.claim(2)['task'];assert len(tasks.list()['items'])==1
    assert tasks.seconds()['counts']['processing']==2 and tasks.seconds()['counts']['pending']==4
    complete(tasks,a);b=tasks.claim(3)['task']
    assert b['time_range']==[iso(start+2000000),iso(start+5000000)]
    assert b['window_seconds']==3 and tasks.seconds()['counts']['processing']==3
    with store.db() as db:
        columns={r[1] for r in db.execute('PRAGMA table_info(processing_tasks)')}
        assert 'consumer_id' not in columns and 'job_id' not in columns
    store.close()


def test_idempotent_claim_and_fixed_retry_range(tmp_path):
    store,_=seed(tmp_path);tasks=Tasks(store)
    first=tasks.claim(2,'p6',request_id='run-1')['task']
    same=tasks.claim(2,'p6',request_id='run-1')['task'];assert first==same
    with pytest.raises(ToolError):tasks.claim(3,'p6',request_id='run-1')
    tasks.fail(first['task_id'],first['lease_token'],{'code':'fixture'},terminal=True)
    assert tasks.claim(3)['status']=='blocked'
    tasks.retry(first['task_id'])
    retry=tasks.claim(3,'p6',request_id='run-2')['task']
    assert retry['task_id']==first['task_id'] and retry['time_range']==first['time_range']
    assert tasks.claim(3,'p6',request_id='run-2')['task']==retry
    with pytest.raises(ToolError):tasks.complete(first['task_id'],first['lease_token'],{'schema_version':3,'result_id':'bad','agent_id':'p6'})
    complete(tasks,retry);assert tasks.seconds()['counts']['completed']==2
    store.close()


@pytest.mark.parametrize('value',[0,301,True,1.2,None])
def test_invalid_length_never_allocates_task(tmp_path,value):
    store,_=seed(tmp_path);tasks=Tasks(store)
    with pytest.raises(ToolError):tasks.claim(value)
    assert tasks.list()['items']==[];store.close()


def test_http_rejects_job_and_consumer_and_receipt_business_fields(tmp_path):
    store,_=seed(tmp_path);store.close()
    with TestClient(create_app(tmp_path)) as client:
        for field in ['consumer_id','job_id','work_permit']:
            assert client.post('/v2/tasks/claim',json={'window_seconds':2,field:'forbidden'}).status_code==400
        assert client.get('/v2/consumers').status_code==404
        assert client.get('/v2/tasks').json()['items']==[]
        t=client.post('/v2/tasks/claim',json={'window_seconds':2}).json()['task']
        value={'schema_version':3,'result_id':'wrong','agent_id':'p6','job_id':'ticket'}
        assert client.post(f"/v2/tasks/{t['task_id']}/complete",json={'lease_token':t['lease_token'],'result':value}).status_code==400
        assert client.get('/v2/data/seconds').json()['counts']['processing']==2


def make_v2(root,tmp):
    root.mkdir();fresh,start=seed(tmp/'fresh');fresh.close()
    with sqlite3.connect(tmp/'fresh/mock_player.sqlite3') as src,sqlite3.connect(root/'mock_player.sqlite3') as dest:
        dest.executescript((Path(__file__).parent/'fixtures/schema_v2.sql').read_text(encoding='utf-8-sig'))
        for table in ['persons','sensors','areas','cv_observations','sensor_readings','position_readings','ppe_second_stats','player_state']:
            rows=src.execute('SELECT * FROM '+table).fetchall()
            for row in rows:dest.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',row)
        state=json.loads(dest.execute('SELECT state_json FROM player_state').fetchone()[0]);state['schema_version']=2
        dest.execute('UPDATE player_state SET state_json=?',(json.dumps(state),))
        dest.execute("INSERT INTO agent_consumers(consumer_id,window_seconds,max_attempts,created_at_us) VALUES('test',2,3,?)",(start,))
        for index in range(3):
            dest.execute('INSERT INTO processing_tasks(id,consumer_id,window_start_us,window_end_us,cutoff_sequence,state,attempt_count,created_at_us,updated_at_us) VALUES(?,?,?,?,?,?,?,?,?)',
                         (19+index,'test',start+index*2000000,start+(index+1)*2000000,999999,'completed' if index==0 else 'pending',1 if index==0 else 0,start,start))
    return start


def test_v2_upgrade_preserves_data_and_completed_seconds_without_phantom_tasks(tmp_path):
    root=tmp_path/'old';start=make_v2(root,tmp_path)
    store=Store(root);tasks=Tasks(store)
    assert len(tasks.list()['items'])==1 and tasks.list()['items'][0]['task_id']==19
    assert tasks.seconds()['counts']['completed']==2 and tasks.seconds()['counts']['pending']==4
    t=tasks.claim(3)['task'];assert t['task_id']>21 and t['time_range'][0]==iso(start+2000000)
    assert store.data_status()['counts']['ppe']==18
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM legacy_v2_processing_tasks').fetchone()[0]==3
        assert db.execute('PRAGMA user_version').fetchone()[0]==3
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert list((root/'backups').glob('player-before-v3-*.sqlite3'))
    store.close()


def test_upgrade_failure_rolls_back_schema_and_data(tmp_path):
    root=tmp_path/'old';make_v2(root,tmp_path)
    with patch('mock_player.upgrade.schema',side_effect=RuntimeError('simulated upgrade failure')):
        with pytest.raises(RuntimeError):Store(root)
    with sqlite3.connect(root/'mock_player.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM processing_tasks').fetchone()[0]==3
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='legacy_v2_processing_tasks'").fetchone()
    reopened=Store(root);assert reopened.data_status()['counts']['ppe']==18;reopened.close()
