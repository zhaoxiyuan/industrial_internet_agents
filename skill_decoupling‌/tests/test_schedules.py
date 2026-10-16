"""Controlled-clock scheduling boundaries and real platform/LangGraph integration.

All schedules and tasks use disposable databases. No production plan is created.
"""
import copy
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from unittest.mock import patch

from app import ROOT
from platform_core.common import Problem, now
from platform_core.langchain_tools import build_tools
from platform_core.service import Service
import test_platform as fixtures


class Clock:
    def __init__(self): self.value = time.time()
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'tests'/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tests'/'.tmp')
        self.service = Service(ROOT, Path(self.temp.name)/'data')
        self.clock = Clock(); self.service.schedules.clock = self.clock
        self.target = self.agent('target')
        self.owner = self.agent('owner')
        self.grant = self.service.tasks.grant({'caller_agent_id':self.owner['id'],'target_agent_id':self.target['id'],'permissions':['schedule']})

    def tearDown(self):
        self.service.close(); self.temp.cleanup()

    def agent(self, name):
        return self.service.agents.save({'name':name,'model':'fixture','base_url':'http://127.0.0.1:1/v1','api_key':'fixture',
            'input_schema':{'type':'object','properties':{'window_seconds':{'type':'integer','minimum':1}},'additionalProperties':False}})

    def data(self, **overrides):
        return {'agent_id':self.target['id'],'enabled':True,'request_id':'create','interval_seconds':3,
                'description':'process a window','input':{'window_seconds':5},'first_run':'immediate','overlap_policy':'skip','overlap_key':'',**overrides}

    def plan(self, **overrides):
        return self.service.schedules.save(self.data(**overrides), self.owner['id'])

    def finish(self, task_id, **changes):
        return self.service.tasks.repo.update(self.service.tasks.repo.get(task_id),status='succeeded',finished=now(),**changes)

    def invoke(self, caller, name, args, run_id):
        tools, _ = build_tools(self.service, caller, run_id)
        if name in ('set_heartbeat_schedule','submit_task','agent_status') and 'agent_id' in args:
            args=dict(args);args['agent_name']=self.service.store.get('agents',args.pop('agent_id'))['name']
        return next(t for t in tools if t.name==name).invoke(args)

    def running(self, agent):
        task = self.service.tasks.submit(agent['id'],'test context')
        return self.service.tasks.repo.update(task,status='running')

    def test_default_disabled_and_unchanged_agent_save_preserves_progress(self):
        self.assertEqual(self.service.schedules.list(),[])
        config={k:v for k,v in self.data().items() if k not in ('agent_id','request_id')}
        saved=self.service.agents.save({'heartbeat':config},self.target['id'])
        id=saved['heartbeat']['schedule_id'];self.service.schedules.tick()
        before=self.service.schedules.get(id)
        self.service.agents.save({'name':'renamed'},self.target['id'])
        self.service.agents.save({'heartbeat':config},self.target['id'])
        self.assertEqual(self.service.schedules.get(id),before)
        self.assertEqual(len(self.service.schedules.list()),1)
        self.service.schedules.control(id,'pause')
        self.service.agents.save({'name':'base edit'},self.target['id'])
        self.assertEqual(self.service.schedules.get(id)['status'],'paused')

    def test_admin_update_retains_tool_plan_authorization(self):
        p=self.plan()
        updated=self.service.schedules.save(self.data(schedule_id=p['id'],version=p['version'],request_id='admin-update',description='admin adjusted'))
        self.assertEqual(updated['owner_agent_id'],self.owner['id'])
        self.assertEqual(updated['grant_id'],self.grant['id'])
        self.service.schedules.tick();self.assertEqual(self.service.schedules.get(p['id'])['fired_count'],1)

    def test_types_and_contract_validation(self):
        for value in (0,-1,1.5,'3',True,None,86401):
            with self.subTest(value=value), self.assertRaises(Problem): self.plan(interval_seconds=value)
        for changes in ({'input':[]},{'input':{'unknown':5}},{'enabled':'true'},{'first_run':'soon'},{'overlap_policy':'queue'}):
            with self.subTest(changes=changes), self.assertRaises(Problem): self.plan(**changes)
        with self.assertRaises(Problem):self.service.agents.save({'heartbeat':{'enabled':True,'interval_seconds':0}},self.target['id'])
        self.assertEqual(self.service.schedules.list(),[])
        current=self.running(self.owner)
        for value in ('3', True):
            result=self.invoke(self.owner,'set_heartbeat_schedule',self.data(interval_seconds=value),current['id'])
            self.assertTrue(result['isError'], result)
        paused=self.plan(enabled=False,description='')
        with self.assertRaises(Problem):self.service.schedules.control(paused['id'],'resume')

    def test_actual_worker_cancel_and_pool_rejection(self):
        entered,release=threading.Event(),threading.Event()
        def work(id):
            entered.set();release.wait(5)
        p=self.plan()
        with patch.object(self.service.runtime,'advance',side_effect=work):
            try:
                self.service.scheduler.tick();self.assertTrue(entered.wait(2))
                task=self.service.schedules.get(p['id'])['last_task_id']
                self.service.tasks.cancel(task)
                self.assertTrue(self.service.tasks.repo.get(task)['cancel_requested'])
                self.service.tasks.repo.update(self.service.tasks.repo.get(task),status='cancelled',finished=now())
                self.clock.advance(12);self.service.scheduler.tick()
                self.assertEqual(len(self.service.tasks.repo.list()),1)
                self.assertTrue(self.service.tasks.repo.get(task)['executor_active'])
                release.set();self.service.scheduler.active[task].result(timeout=2)
                self.service.scheduler.tick()
                self.assertFalse(self.service.tasks.repo.get(task)['executor_active'])
            finally:release.set()
        self.clock.advance(3)
        with patch.object(self.service.scheduler.pool,'submit',side_effect=RuntimeError('closed')):
            self.service.scheduler.tick()
        task=self.service.tasks.repo.get(self.service.schedules.get(p['id'])['last_task_id'])
        self.assertFalse(task['executor_active']);self.assertEqual(task['error']['code'],'EXECUTOR_UNAVAILABLE')

    def test_database_serializes_independent_schedule_controllers(self):
        from platform_core.schedules import Schedules
        # Separate task locks, same database, without running a second platform startup recovery.
        clone=copy.copy(self.service);clone.tasks=copy.copy(self.service.tasks);clone.tasks.lock=threading.RLock()
        other=Schedules(clone,self.clock)
        self.plan()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda schedules:schedules.tick(),[self.service.schedules,other]))
        self.assertEqual(len(self.service.tasks.repo.list()),1)

    def test_queued_execution_revalidates_current_contract(self):
        p=self.plan();self.service.schedules.tick()
        self.service.agents.save({'input_schema':{'type':'object','properties':{},'additionalProperties':False}},self.target['id'])
        self.service.scheduler.tick()
        task=self.service.tasks.repo.get(self.service.schedules.get(p['id'])['last_task_id'])
        self.assertEqual(task['status'],'failed');self.assertEqual(task['error']['code'],'PARAMETER_INVALID')

    def test_first_run_update_cursor_pause_resume_stop(self):
        p=self.plan(first_run='after_interval');self.service.schedules.tick()
        self.assertEqual(self.service.tasks.repo.list(),[])
        self.clock.advance(3);self.service.schedules.tick()
        p=self.service.schedules.get(p['id']);self.assertEqual(p['fired_count'],1)
        self.assertEqual(p['next_run_at'],self.clock()+3)
        changed=self.service.schedules.save(self.data(schedule_id=p['id'],version=p['version'],request_id='update',interval_seconds=10,first_run='after_interval'),self.owner['id'])
        self.assertEqual(changed['next_run_at'],self.clock()+10)
        self.service.schedules.control(p['id'],'pause');self.clock.advance(100);self.service.schedules.tick()
        self.assertEqual(len(self.service.tasks.repo.list()),1)
        resumed=self.service.schedules.control(p['id'],'resume');self.assertEqual(resumed['next_run_at'],self.clock()+10)
        stopped=self.service.schedules.control(p['id'],'stop');self.assertIsNone(stopped['next_run_at'])
        self.assertEqual(self.service.tasks.repo.get(p['last_task_id'])['status'],'queued')

    def test_tool_creation_main_finishes_and_schedule_survives(self):
        current=self.running(self.owner)
        result=self.invoke(self.owner,'set_heartbeat_schedule',self.data(),current['id'])
        self.assertFalse(result['isError'],result);plan=result['structuredContent']
        self.finish(current['id'])
        self.service.schedules.tick();first=self.service.schedules.get(plan['id'])['last_task_id']
        task=self.service.tasks.repo.get(first)
        self.assertIsNone(task['parent_task_id']);self.assertIsNone(task['caller_agent_id'])
        self.assertEqual(task['created_by_task_id'],current['id']);self.assertEqual(task['schedule_owner_agent_id'],self.owner['id'])
        self.finish(first);self.clock.advance(3);self.service.schedules.tick()
        self.assertNotEqual(first,self.service.schedules.get(plan['id'])['last_task_id'])
        # A new owner task can read this plan, but the ordinary parent-chain gate stays strict.
        later=self.running(self.owner)
        self.assertFalse(self.invoke(self.owner,'get_heartbeat_schedule',{'schedule_id':plan['id']},later['id'])['isError'])
        with self.assertRaises(Problem):self.service.tasks.view(self.owner['id'],first,'status',later['id'])

    def test_request_idempotency_conflict_and_update_version(self):
        p=self.plan();self.service.schedules.tick()
        self.assertEqual(self.plan(),p)  # Return original durable response, not a new plan.
        with self.assertRaises(Problem) as e:self.plan(description='different')
        self.assertEqual(e.exception.code,'SCHEDULE_IDEMPOTENCY_CONFLICT')
        with self.assertRaises(Problem):self.plan(schedule_id=p['id'],request_id='update',version=p['version'],description='new')
        current=self.service.schedules.get(p['id'])
        update=self.plan(schedule_id=p['id'],request_id='update',version=current['version'],description='new')
        self.assertEqual(update['description'],'new')
        self.assertEqual(len(self.service.schedules.list()),1)

    def test_queued_running_waiting_and_live_executor_skip(self):
        p=self.plan();self.service.schedules.tick();id=self.service.schedules.get(p['id'])['last_task_id']
        for status in ('queued','running','waiting'):
            self.service.tasks.repo.update(self.service.tasks.repo.get(id),status=status)
            self.clock.advance(3);self.service.schedules.tick()
            self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.service.tasks.repo.update(self.service.tasks.repo.get(id),status='cancelled',executor_active=True)
        self.clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.service.tasks.repo.update(self.service.tasks.repo.get(id),executor_active=False)
        self.clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)
        self.assertEqual(self.service.schedules.get(p['id'])['skipped_count'],4)

    def test_multiple_plans_keys_and_descendant_execution(self):
        first=self.plan();second=self.plan(request_id='two')
        self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.assertEqual(self.service.schedules.get(second['id'])['skipped_count'],1)
        other=self.agent('other')
        separate=self.service.schedules.save(self.data(agent_id=other['id'],request_id='other',overlap_key='consumer-X'))
        tied=self.service.schedules.save(self.data(request_id='tied',overlap_key='consumer-X'))
        self.service.schedules.tick()
        self.assertEqual(len(self.service.tasks.repo.list()),2)
        self.assertEqual(self.service.schedules.get(tied['id'])['skipped_count'],1)
        # Child still executing retains its root schedule's overlap scope.
        root=self.service.schedules.get(first['id'])['last_task_id']
        self.service.tasks.repo.update(self.service.tasks.repo.get(root),status='running')
        self.service.tasks.grant({'caller_agent_id':self.target['id'],'target_agent_id':other['id'],'permissions':['submit']})
        child=self.service.tasks.submit(other['id'],'child',parent_id=root)
        self.finish(root)
        self.clock.advance(3);self.service.schedules.tick()
        self.assertEqual(self.service.schedules.get(first['id'])['last_task_id'],root)

    def test_concurrent_trigger_claim_and_crash_rollback(self):
        p=self.plan()
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:self.service.schedules.tick(),range(8)))
        self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.finish(self.service.schedules.get(p['id'])['last_task_id']);self.clock.advance(3)
        original=self.service.tasks.repo.create
        def interrupted(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('simulated interruption after task insertion')
        before=self.service.schedules.get(p['id'])
        with patch.object(self.service.tasks.repo,'create',side_effect=interrupted),self.assertRaises(RuntimeError):self.service.schedules.tick()
        self.assertEqual(self.service.schedules.get(p['id']),before)
        self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)
        with self.service.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM schedule_firings').fetchone()[0],2)

    def test_no_catchup_storm_and_restart_recovery(self):
        p=self.plan();self.service.schedules.tick();task_id=self.service.schedules.get(p['id'])['last_task_id']
        self.service.tasks.repo.update(self.service.tasks.repo.get(task_id),status='running',executor_active=True)
        data=self.service.data;self.service.close();self.service=Service(ROOT,data);self.service.schedules.clock=self.clock
        old=self.service.tasks.repo.get(task_id)
        self.assertEqual(old['status'],'interrupted');self.assertFalse(old['executor_active'])
        self.clock.advance(90);self.service.schedules.tick()
        self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.clock.value=old['executor_recovery_until']+100
        self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)

    def test_lost_docker_receipt_requires_exit_verification_after_restart(self):
        p=self.plan();self.service.schedules.tick();task_id=self.service.schedules.get(p['id'])['last_task_id']
        self.finish(task_id)
        self.service.store.put('execution_holds',{'run_id':task_id,'state':'executing','until':self.clock()+8})
        data=self.service.data;self.service.close();self.service=Service(ROOT,data);self.service.schedules.clock=self.clock
        self.clock.advance(1000);self.service.schedules.tick()
        self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.assertEqual(self.service.schedules.get(p['id'])['last_error']['code'],'EXECUTION_EXIT_UNCONFIRMED')
        self.service.schedules.acknowledge_exit(p['id'],task_id,True)
        self.clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)
        saved=self.service.schedules.get(p['id']);self.assertGreater(saved['next_run_at'],self.clock())
        self.assertLessEqual(saved['next_run_at'],self.clock()+3)
        self.assertGreater(saved['missed_count'],50)
        self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)

    def test_schedule_authorization_not_submit_and_read_scope(self):
        stranger=self.agent('stranger')
        self.service.tasks.grant({'caller_agent_id':stranger['id'],'target_agent_id':self.target['id'],'permissions':['submit']})
        _,catalog=self.service.agents.catalog(stranger);self.assertNotIn('set_heartbeat_schedule',catalog)
        with self.assertRaises(Problem):self.service.schedules.save(self.data(request_id='stranger'),stranger['id'])
        p=self.plan();self.service.schedules.tick();task_id=self.service.schedules.get(p['id'])['last_task_id']
        self.finish(task_id,result={'private':'assessment'},artifacts=[{'path':'private'}])
        detail=self.service.schedules.detail(p['id'],self.owner['id'])
        self.assertNotIn('result',detail['recent_runs'][0]);self.assertNotIn('artifacts',detail['recent_runs'][0])
        self.assertIn('result',self.service.schedules.detail(p['id'])['recent_runs'][0])
        self.service.tasks.grant({**self.grant,'permissions':['schedule','result']},self.grant['id'])
        self.assertEqual(self.service.schedules.detail(p['id'],self.owner['id'])['recent_runs'][0]['result'],{'private':'assessment'})
        self.service.tasks.grant({'caller_agent_id':stranger['id'],'target_agent_id':self.target['id'],'permissions':['schedule','result']})
        with self.assertRaises(Problem):self.service.schedules.detail(p['id'],stranger['id'])
        with self.assertRaises(Problem):self.service.schedules.control(p['id'],'stop',stranger['id'])

    def test_revocation_disabled_target_contract_change_and_model_missing(self):
        for change,code in [('grant','AGENT_FORBIDDEN'),('disabled','AGENT_DISABLED'),('contract','PARAMETER_INVALID'),('model','MODEL_NOT_CONFIGURED')]:
            with self.subTest(change=change):
                # Each case gets a fresh target and explicit grant.
                target=self.agent(change)
                grant=self.service.tasks.grant({'caller_agent_id':self.owner['id'],'target_agent_id':target['id'],'permissions':['schedule']})
                p=self.service.schedules.save(self.data(agent_id=target['id'],request_id=change),self.owner['id'])
                if change=='grant':self.service.store.delete('agent_grants',grant['id'])
                elif change=='disabled':self.service.agents.save({'enabled':False},target['id'])
                elif change=='contract':self.service.agents.save({'input_schema':{'type':'object','properties':{},'additionalProperties':False}},target['id'])
                else:self.service.agents.save({'clear_key':True},target['id'])
                self.service.schedules.tick();saved=self.service.schedules.get(p['id'])
                self.assertEqual(saved['status'],'blocked');self.assertEqual(saved['last_error']['code'],code)
                self.assertIsNone(saved['last_task_id'])

    def test_execution_failure_timeout_and_uncertain_exit(self):
        p=self.plan();self.service.schedules.tick();task_id=self.service.schedules.get(p['id'])['last_task_id']
        self.service.tasks.repo.update(self.service.tasks.repo.get(task_id),status='failed',error={'code':'MODEL_TIMEOUT','message':'model timeout'},finished=now(),executor_active=True)
        self.clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.assertEqual(self.service.schedules.detail(p['id'])['last_execution_error']['code'],'MODEL_TIMEOUT')
        self.service.tasks.repo.update(self.service.tasks.repo.get(task_id),executor_active=False)
        self.service.store.put('execution_holds',{'run_id':task_id,'state':'uncertain','until':self.clock()-1})
        self.clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),1)
        self.assertEqual(self.service.schedules.get(p['id'])['last_error']['code'],'EXECUTION_EXIT_UNCONFIRMED')
        with self.assertRaises(Problem):self.service.schedules.acknowledge_exit(p['id'],task_id,False)
        self.service.schedules.acknowledge_exit(p['id'],task_id,True)
        self.clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)


class ScheduleIntegrationTests(unittest.TestCase):
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown
    api=fixtures.PlatformTests.api

    def test_sdk_timeout_is_recorded_without_immediate_retry(self):
        clock=Clock();self.service.schedules.clock=clock
        entered,release=threading.Event(),threading.Event()
        def delayed(payload):
            entered.set();release.wait(3)
            return {'role':'assistant','content':'late reply'}
        self.upstream.model_reply=delayed
        agent=self.service.agents.save({'name':'slow fixture','model':'fixture','base_url':self.upstream.base+'/v1','api_key':'test'})
        plan=self.service.schedules.save({'agent_id':agent['id'],'request_id':'timeout','enabled':True,'interval_seconds':3,'first_run':'immediate','description':'timeout test','input':{}})
        original=self.service.runtime.chat_model
        def fast_timeout(config):
            return original({**config,'model_timeout':.2})  # Accelerate SDK timeout, not a fake timeout result.
        try:
            with patch.object(self.service.runtime,'chat_model',side_effect=fast_timeout):
                self.service.scheduler.tick()
                task_id=self.service.schedules.get(plan['id'])['last_task_id']
                self.assertTrue(entered.wait(3))
                clock.advance(3);self.service.schedules.tick()
                self.assertEqual(self.service.schedules.get(plan['id'])['skipped_count'],1)
                self.service.scheduler.active[task_id].result(timeout=5)
                task=self.service.tasks.repo.get(task_id)
                self.assertEqual(task['error']['code'],'MODEL_TIMEOUT')
                self.assertEqual(self.service.schedules.detail(plan['id'])['last_execution_error']['code'],'MODEL_TIMEOUT')
                self.service.scheduler.tick();self.assertEqual(len(self.service.tasks.repo.list()),1)
                clock.advance(3);self.service.schedules.tick();self.assertEqual(len(self.service.tasks.repo.list()),2)
                self.service.schedules.control(plan['id'],'stop')
        finally:release.set()

    def test_http_admin_and_real_scheduler_langgraph_chain(self):
        agent=self.service.agents.save({'name':'heartbeat-fixture','model':'fixture','base_url':self.upstream.base+'/v1','api_key':'test'})
        self.upstream.model_reply=lambda payload:{'role':'assistant','content':'scheduled execution finished'}
        status,plan=self.api('/api/schedules',{'agent_id':agent['id'],'request_id':'admin','enabled':True,'interval_seconds':1,'first_run':'immediate','description':'test heartbeat','input':{}})
        self.assertEqual(status,201)
        self.service.scheduler.start()
        limit=time.monotonic()+12
        while time.monotonic()<limit:
            tasks=[t for t in self.service.tasks.repo.list() if t.get('schedule_id')==plan['id']]
            if sum(t['status']=='succeeded' for t in tasks)>=2:break
            time.sleep(.05)
        self.assertGreaterEqual(sum(t['status']=='succeeded' for t in tasks),2,tasks)
        self.assertTrue(all(t['parent_task_id'] is None for t in tasks))
        self.assertGreaterEqual(len(self.upstream.model_requests),2)
        for action in ('pause','resume','stop'):
            code,value=self.api('/api/schedules/'+plan['id']+'/'+action,{})
            self.assertEqual(code,200)
            self.assertEqual(value['status'],{'pause':'paused','resume':'active','stop':'stopped'}[action])
        code,detail=self.api('/api/schedules/'+plan['id'])
        self.assertEqual(code,200);self.assertTrue(detail['recent_runs'])
        task=next(t for t in tasks if t['status']=='succeeded')
        self.assertTrue((self.service.data/'artifacts'/task['id']/'execution.json').exists())

    def test_model_creates_plan_then_ends_no_parent_model_on_ticks(self):
        owner=self.service.agents.save({'name':'owner','model':'owner','base_url':self.upstream.base+'/v1','api_key':'test'})
        target=self.service.agents.save({'name':'target','model':'target','base_url':self.upstream.base+'/v1','api_key':'test'})
        self.service.tasks.grant({'caller_agent_id':owner['id'],'target_agent_id':target['id'],'permissions':['schedule']})
        def reply(payload):
            if payload['model']=='target':return {'role':'assistant','content':'heartbeat done'}
            if any(m['role']=='tool' for m in payload['messages']):return {'role':'assistant','content':'plan created; parent done'}
            args={'agent_name':target['name'],'request_id':'model-plan','enabled':True,'interval_seconds':1,'description':'periodic work','input':{},'first_run':'after_interval'}
            return {'role':'assistant','content':'','tool_calls':[{'id':'create-plan','type':'function','function':{'name':'set_heartbeat_schedule','arguments':json.dumps(args)}}]}
        self.upstream.model_reply=reply
        parent=self.service.tasks.submit(owner['id'],'create a heartbeat plan and finish')
        self.service.scheduler.start();final=self.service.tasks.wait(parent['id'],15)
        self.assertEqual(final['status'],'succeeded',final)
        owner_calls=sum(p['model']=='owner' for p in self.upstream.model_requests)
        end=time.monotonic()+12
        while time.monotonic()<end:
            if sum(t.get('schedule_id') is not None and t['status']=='succeeded' for t in self.service.tasks.repo.list())>=2:break
            time.sleep(.05)
        plan=self.service.schedules.list()[0];self.service.schedules.control(plan['id'],'stop')
        self.assertGreaterEqual(plan['fired_count'],2)
        self.assertEqual(sum(p['model']=='owner' for p in self.upstream.model_requests),owner_calls)
        self.assertTrue(any(r['tool']=='set_heartbeat_schedule' for r in self.service.store.list('tool_runs')))


@unittest.skipUnless(os.environ.get('PLATFORM_HEARTBEAT_REAL_MODEL_TEST')=='1', '真实模型心跳需显式启用，协议模型不代表真实推理')
class RealModelHeartbeatTests(unittest.TestCase):
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown

    def test_real_model_target_after_creator_finishes(self):
        live=ROOT/'data/platform.db'
        if not live.exists():self.skipTest('没有现有模型配置')
        configured=[]
        with sqlite3.connect('file:'+live.resolve().as_posix()+'?mode=ro',uri=True) as db:
            for row in db.execute("SELECT data FROM entities WHERE kind='agents'"):
                agent=json.loads(row[0])
                key=db.execute('SELECT value FROM secrets WHERE id=?',(agent.get('secret_ref',''),)).fetchone()
                if agent.get('enabled',True) and agent.get('model') and agent.get('base_url') and key and key[0]:
                    configured.append((agent,key[0]))
        if not configured:self.skipTest('没有已配置模型凭据')
        original,key=configured[0]
        target=self.service.agents.save({'name':'isolated real heartbeat','model':original['model'],'base_url':original['base_url'],
            'api_key':key,'model_timeout':30,'timeout':60,'max_tokens':256,'system_prompt':'仅用一句话回答心跳测试完成。不要调用任何工具。'})
        owner=self.service.agents.save({'name':'isolated creator','model':'fixture','base_url':self.upstream.base+'/v1','api_key':'fixture'})
        self.service.tasks.grant({'caller_agent_id':owner['id'],'target_agent_id':target['id'],'permissions':['schedule']})
        current=self.service.tasks.submit(owner['id'],'create isolated heartbeat')
        self.service.tasks.repo.update(current,status='running')
        tools,_=build_tools(self.service,owner,current['id'])
        result=next(t for t in tools if t.name=='set_heartbeat_schedule').invoke({'agent_name':target['name'],'request_id':'real-model-heartbeat',
            'enabled':True,'interval_seconds':86400,'first_run':'immediate','description':'这是隔离测试，请简短回答心跳测试完成。','input':{}})
        self.assertFalse(result['isError'],result);plan=result['structuredContent']
        self.service.tasks.repo.update(self.service.tasks.repo.get(current['id']),status='succeeded',finished=now())
        self.service.schedules.tick();task_id=self.service.schedules.get(plan['id'])['last_task_id']
        self.service.schedules.control(plan['id'],'stop')  # No repeated remote inference.
        self.service.scheduler.start()
        final=self.service.tasks.wait(task_id,70)
        self.assertEqual(final['status'],'succeeded',final.get('error'))
        self.assertTrue(final['result']['content'])
        self.assertEqual(self.upstream.model_requests,[])  # Creator was not awakened.
        self.assertTrue((self.service.data/'artifacts'/task_id/'execution.json').exists())


if __name__=='__main__':unittest.main()
