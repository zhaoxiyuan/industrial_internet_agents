import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from platform_core.common import Problem
from platform_core.pipeline_batch import run


@pytest.fixture
def batch():
 data={};calls=[];active=[]
 agents={role:{'id':role,'timeout':1800,'concurrency':3 if role=='p6' else 1} for role in ('p6','p7')}
 parent={'id':'root','executor_agent_id':'main','status':'running','business_scope':{'job_id':'ticket','storage':'shared'}}
 def get(kind,key,required=True):return agents[key] if kind=='agents' else data.get(key)
 def put(kind,value,key):data[key]=value;return value
 def submit(agent,description,**kwargs):
  existing=next((x for x in calls if x['request_id']==kwargs['request_id']),None)
  if existing:return existing
  row={'id':'child'+str(len(calls)), 'agent':agent,'description':description,**kwargs};calls.append(row);return row
 service=SimpleNamespace(store=SimpleNamespace(get=get,put=put),tasks=SimpleNamespace(lock=threading.RLock(),authorize=MagicMock(),submit=submit,repo=SimpleNamespace(get=lambda _:parent,list=lambda:active)))
 job=MagicMock();job.config.return_value={'agents':{'main':['main'],'p6':['p6'],'p7':['p7']}};job.get.return_value={'max_parallel':3}
 with patch('industrial_skills.store.Store',return_value=job),patch('platform_core.workflow.ensure_open'):
  yield service,calls,active,agents


def test_batch_wires_real_dependencies_and_replay_is_idempotent(batch):
 service,calls,_,_=batch
 result=run(service,'main',{'p6_count':2},'root')
 assert [c['agent'] for c in calls]==['p6','p6','p7']
 assert calls[-1]['depends_on']==result['p6_task_ids']==['child0','child1']
 assert all(c['input']=={} and c['parent_id']=='root' for c in calls)
 assert [c['timeout_seconds'] for c in calls]==[180,180,600]
 assert len({c['request_id'] for c in calls})==3
 assert run(service,'main',{'p6_count':2},'root')==result and len(calls)==3
 with pytest.raises(Problem):run(service,'main',{'p6_count':1},'root')
 service.tasks.authorize.assert_any_call('main','p7','submit')


def test_zero_count_only_drains_p7(batch):
 service,calls,_,_=batch
 run(service,'main',{'p6_count':0},'root')
 assert len(calls)==1 and calls[0]['agent']=='p7' and calls[0]['depends_on']==[]


def test_no_second_p7_and_concurrency_limit(batch):
 service,calls,active,_=batch
 active.append({'status':'running','executor_agent_id':'p7','business_scope':{'job_id':'ticket','storage':'shared'}})
 assert run(service,'main',{'p6_count':1},'root')['reason']=='p7_in_flight'
 active[0]['executor_agent_id']='p6'
 assert run(service,'main',{'p6_count':3},'root')['reason']=='p6_concurrency_limit'
 assert calls==[]


def test_authorization_and_timeout_fail_before_any_submit(batch):
 service,calls,_,agents=batch
 service.tasks.authorize.side_effect=Problem('TASK_FORBIDDEN','denied',403)
 with pytest.raises(Problem):run(service,'main',{'p6_count':2},'root')
 assert calls==[]
 service.tasks.authorize.side_effect=None;agents['p7']['timeout']=300
 with pytest.raises(Problem):run(service,'main',{'p6_count':2},'root')
 assert calls==[]


def test_wrong_actor_and_invalid_count_cannot_dispatch(batch):
 service,calls,_,_=batch
 for actor,count in [('other',1),('main',4),('main',True)]:
  with pytest.raises(Problem):run(service,actor,{'p6_count':count},'root')
 assert calls==[]
