import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from types import SimpleNamespace
from app import ROOT
from platform_core.service import Service
from platform_core.common import Problem
from platform_core.heartbeat_channels import run

class ChannelsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tests/.tmp')
        self.service=Service(ROOT,Path(self.temp.name)/'data')
        def agent(name):return self.service.agents.save({'name':name,'model':'fixture','base_url':'http://127.0.0.1:1/v1','api_key':'fixture','timeout':600,'concurrency':3})
        self.main=agent('Main');self.p6=agent('P6')
        for a in (self.main,self.p6):self.service.tasks.grant({'caller_agent_id':self.main['id'],'target_agent_id':a['id'],'permissions':['schedule','result']})
        self.bound={'storage':self.temp.name,'job_id':'ticket'}
        self.task=self.service.tasks.submit(self.main['id'],'test')
        self.service.tasks.repo.update(self.task,status='running',business_scope=self.bound)
        self.job=MagicMock();self.job.config.return_value={'agents':{'main':[self.main['id']],'p6':[self.p6['id']]}}
        self.job.get.side_effect=lambda db,kind,*args: {'status':'active','monitoring_closed':False} if kind=='job' else {'max_parallel':3}
        self.patches=[patch('industrial_skills.store.Store',return_value=self.job),patch('platform_core.workflow.scope_for_agent',return_value=self.bound)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.service.close();self.temp.cleanup()
    def call(self,name,**args):return run(self.service,self.main['id'],name,args,self.task['id'])
    def ensure(self,channel=1,**args):return self.call('ensure_heartbeat_channel',agent_name='P6',channel=channel,description='ticket start/end P6 only',**args)
    def test_three_channels_replay_update_and_stop(self):
        for c in (1,2,3):self.assertEqual(self.ensure(c)['channel'],c)
        plans=self.service.schedules.list();self.assertEqual(len(plans),3)
        self.assertEqual(len({p['overlap_key'] for p in plans}),3)
        self.assertTrue(all(p['overlap_scope']=='chain' and p['timeout_seconds']==180 for p in plans))
        self.ensure(1);self.assertEqual(len(self.service.schedules.list()),3)
        self.assertEqual(self.ensure(2,interval_seconds=45)['interval_seconds'],45)
        self.call('control_heartbeat_channel',agent_name='P6',channel=2,action='stop')
        self.assertEqual(self.ensure(2)['status'],'stopped')
        self.call('control_heartbeat_channel',agent_name='P6',channel=2,action='resume')
        self.assertEqual(self.ensure(2)['status'],'active')
        self.call('control_heartbeat_channel',agent_name='P6',channel='all',action='stop')
        self.assertTrue(all(p['status']=='stopped' for p in self.service.schedules.list()))
    def test_main_preset_and_scope(self):
        r=self.call('ensure_heartbeat_channel',agent_name='Main',channel=1,description='Main only')
        self.assertEqual((r['interval_seconds'],r['overlap_scope'],r['timeout_seconds']),(180,'self',300))
        with self.assertRaises(Problem):self.call('ensure_heartbeat_channel',agent_name='Main',channel=2,description='invalid')
        self.bound={**self.bound,'job_id':'other-ticket'}
        self.service.tasks.repo.update(self.service.tasks.repo.get(self.task['id']),business_scope=self.bound)
        self.assertEqual(self.call('get_heartbeat_channels')['channels'],[])
    def test_legacy_does_not_duplicate_and_can_stop_all(self):
        self.service.schedules.save({'agent_id':self.p6['id'],'request_id':'legacy','enabled':True,'interval_seconds':30,'description':'old','input':{}},self.main['id'],self.task['id'])
        view=self.call('get_heartbeat_channels')
        self.assertEqual(view['channels'],[])
        self.assertEqual(view['legacy_schedules']['count'],1)
        self.assertNotIn('channel',view['legacy_schedules']['by_agent'][0])
        with self.assertRaises(Problem) as e:self.ensure()
        self.assertEqual(e.exception.code,'LEGACY_HEARTBEAT_EXISTS')
        self.call('control_heartbeat_channel',agent_name='P6',channel='all',action='stop')
        self.assertEqual(self.ensure()['status'],'active')
    def test_concurrent_ensure_and_capacity(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:self.ensure(),range(3)))
        self.assertEqual(len(self.service.schedules.list()),1)
        self.service.agents.save({'concurrency':1},self.p6['id'])
        with self.assertRaises(Problem):self.ensure(2)
    def test_model_catalog_only_exposes_channel_interface(self):
        skill=self.service.store.put('skills',{'name':'industrial-main','enabled':True},'test-main-skill')
        agent={**self.main,'skill_ids':['test-main-skill']}
        catalog,mapping=self.service.agents.catalog(agent)
        self.assertIn('ensure_heartbeat_channel',mapping)
        self.assertNotIn('set_heartbeat_schedule',mapping)
        self.assertNotIn('control_heartbeat_schedule',mapping)
        schema=mapping['ensure_heartbeat_channel']['schema']
        self.assertNotIn('request_id',schema['properties'])
        self.assertNotIn('version',schema['properties'])
    def test_closed_monitoring_blocks_resume(self):
        self.ensure()
        self.call('control_heartbeat_channel',agent_name='P6',channel=1,action='stop')
        self.job.get.side_effect=lambda db,kind,*args: {'status':'active','monitoring_closed':True} if kind=='job' else {'max_parallel':3}
        with self.assertRaises(Problem):self.call('control_heartbeat_channel',agent_name='P6',channel=1,action='resume')
if __name__=='__main__':unittest.main()
