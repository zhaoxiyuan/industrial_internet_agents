"""Agent lifecycle safeguards and history preservation; no external model calls."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler,Request

from platform_core.service import Service
from platform_core.agent_lifecycle import deletion_preview,remove_agent
from platform_core.common import Problem,uid


class AgentDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(Path(self.temp.name))
        self.agent=self.service.agents.save({'name':'Delete me','model':'test','base_url':'https://example.invalid/v1','api_key':'private-key'})
    def tearDown(self):self.service.close();self.temp.cleanup()
    def task(self,status,**extra):
        return self.service.tasks.repo.create({'id':uid(),'executor_agent_id':self.agent['id'],'status':status,'phase':status,'description':'Retained history',**extra},{})
    def test_atomic_cleanup_preserves_history_and_shared_resources(self):
        other=self.service.agents.save({'name':'Keep me'})
        self.service.tasks.grant({'caller_agent_id':other['id'],'target_agent_id':self.agent['id'],'permissions':['submit']})
        self.service.store.put('bindings',{'agent_id':self.agent['id'],'source':'fixture','tools':['tool']})
        self.service.store.put('bindings',{'agent_id':other['id'],'source':'fixture','tools':['tool']})
        task=self.task('succeeded');session=self.service.store.put('sessions',{'agent_id':self.agent['id'],'name':'History'})
        self.service.store.put('skills',{'name':'Shared Skill','enabled':True})
        preview=deletion_preview(self.service,self.agent['id'])
        self.assertTrue(preview['can_delete']);self.assertEqual((preview['bindings'],preview['grants']),(1,1))
        remove_agent(self.service,self.agent['id'])
        self.assertFalse(self.service.store.get('agents',self.agent['id'],False))
        self.assertEqual(self.service.store.secret('model:'+self.agent['id']),'')
        self.assertEqual(len(self.service.store.list('bindings')),1)
        self.assertEqual(self.service.store.list('agent_grants'),[])
        self.assertEqual(self.service.tasks.repo.get(task['id']),task)
        self.assertEqual(self.service.store.get('sessions',session['id']),session)
        self.assertEqual(len(self.service.store.list('skills')),1)
        overview=self.service.overview()
        self.assertEqual(len(overview['agent_topology']['nodes']),1)
        self.assertEqual(overview['deleted_agents'][0]['name'],'Delete me')
        self.assertNotIn('private-key',json.dumps(overview['deleted_agents']))
    def test_preview_is_not_authorization_for_later_busy_delete(self):
        self.assertTrue(deletion_preview(self.service,self.agent['id'])['can_delete'])
        for status in ['queued','running','waiting']:
            task=self.task(status)
            with self.assertRaises(Problem) as error:remove_agent(self.service,self.agent['id'])
            self.assertEqual(error.exception.code,'AGENT_BUSY')
            self.service.tasks.repo.update(task,status='cancelled')
        self.assertTrue(self.service.store.secret('model:'+self.agent['id']))
    def test_terminal_executor_and_uncertain_hold_block_deletion(self):
        task=self.task('cancelled',executor_active=True)
        self.assertFalse(deletion_preview(self.service,self.agent['id'])['can_delete'])
        self.service.tasks.repo.update(task,executor_active=False)
        hold=self.service.store.put('execution_holds',{'run_id':task['id'],'state':'uncertain'})
        with self.assertRaises(Problem):remove_agent(self.service,self.agent['id'])
        self.service.store.put('execution_holds',{**hold,'state':'exited'},hold['id'])
        self.assertTrue(deletion_preview(self.service,self.agent['id'])['can_delete'])
    def test_schedule_operations_and_business_references_block(self):
        self.service.agents.save({'heartbeat':{'enabled':True,'interval_seconds':60,'description':'Fixture','input':{}}},self.agent['id'])
        schedule=self.service.schedules.list()[0]
        for action in ['pause','stop']:
            self.service.schedules.control(schedule['id'],action)
            self.assertEqual(deletion_preview(self.service,self.agent['id'])['can_delete'],action=='stop')
        cases=[('operations',{'resource':'agent:'+self.agent['id'],'status':'running'},'AGENT_OPERATION_BUSY'),
               ('connections',{'inbound_agent_id':self.agent['id']},'AGENT_INBOUND_IN_USE'),
               ('risk_card_scopes',{'agents':[self.agent['id']]},'AGENT_CARD_SCOPE_IN_USE')]
        for kind,value,code in cases:
            row=self.service.store.put(kind,value)
            with self.assertRaises(Problem) as error:remove_agent(self.service,self.agent['id'])
            self.assertEqual(error.exception.code,code)
            self.service.store.delete(kind,row['id'])
    def test_real_http_preview_delete_and_not_found(self):
        from app import make_server
        server=make_server(self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        opener=build_opener(ProxyHandler({}));base=f'http://127.0.0.1:{server.server_port}/api/agents/{self.agent["id"]}'
        try:
            with opener.open(base+'/deletion') as response:self.assertTrue(json.load(response)['can_delete'])
            with opener.open(Request(base,method='DELETE')) as response:self.assertTrue(json.load(response)['ok'])
            with self.assertRaises(HTTPError) as error:opener.open(Request(base,method='DELETE'))
            self.assertEqual(error.exception.code,404)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
