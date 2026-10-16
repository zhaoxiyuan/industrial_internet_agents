"""Real HTTP conversation metadata, permanent deletion and execution boundaries."""
import unittest
from platform_core.service import Service
from app import ROOT
import test_platform as fixtures


class SessionTests(unittest.TestCase):
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown
    api=fixtures.PlatformTests.api

    def setUp(self):
        fixtures.PlatformTests.setUp(self)
        self.agent=self.service.agents.save({'name':'conversation fixture','model':'fixture','base_url':self.upstream.base+'/v1','api_key':'test'})

    def session(self,name='conversation'):
        code,value=self.api('/api/sessions',{'agent_id':self.agent['id'],'name':name})
        self.assertEqual(code,201);return value

    def test_create_rename_validation_and_restart(self):
        with self.opener.open(self.base+'/web/chat-content.js') as response:
            self.assertEqual(response.status,200)
            self.assertIn('const ChatContent',response.read().decode())
        s=self.session('用户命名');code,value=self.api('/api/sessions/'+s['id'],{'name':'风险研判'},'PATCH')
        self.assertEqual(code,200);self.assertEqual(value['name'],'风险研判')
        for data in ({'name':''},{'name':'x'*81},{'name':123},{'name':'合法','agent_id':'other'}):
            code,_=self.api('/api/sessions/'+s['id'],data,'PATCH');self.assertEqual(code,400)
        self.assertEqual(self.service.store.get('sessions',s['id'])['name'],'风险研判')
        # Read a new service instance only after the old scheduler is closed.
        self.service.close();other=Service(ROOT,self.service.data)
        try:self.assertEqual(other.sessions.active(s['id'])['name'],'风险研判')
        finally:other.close()

    def test_delete_permanently_removes_session_messages_and_restore_endpoint(self):
        s=self.session();message={'role':'assistant','content':'<think>hidden</think>## 结果\n| 字段 | 值 |\n|---|---|\n| 状态 | completed |'}
        self.service.store.add_message(s['id'],message)
        code,result=self.api('/api/sessions/'+s['id'],method='DELETE');self.assertEqual(code,200)
        self.assertNotIn(s['id'],[x['id'] for x in self.service.overview()['sessions']])
        self.assertNotIn('deleted_sessions',self.service.overview())
        self.assertIsNone(self.service.store.get('sessions',s['id'],required=False))
        self.assertEqual(self.service.store.messages(s['id']),[])
        self.assertEqual(self.api('/api/sessions/'+s['id']+'/messages')[0],404)
        self.assertEqual(self.api('/api/sessions/'+s['id']+'/messages',{'content':'send'})[0],404)
        self.assertEqual(self.api('/api/sessions/'+s['id']+'/restore',{})[0],404)

    def test_legacy_recycled_session_can_be_permanently_deleted_without_affecting_others(self):
        old=self.session();other=self.session('keep')
        self.service.store.put('sessions',{**old,'deleted_at':'2026-10-16T00:00:00+00:00'},old['id'])
        self.service.store.add_message(old['id'],{'role':'user','content':'remove'})
        kept={'role':'user','content':'keep'};self.service.store.add_message(other['id'],kept)
        self.assertEqual(self.api('/api/sessions/'+old['id'],method='DELETE')[0],200)
        self.assertIsNone(self.service.store.get('sessions',old['id'],required=False))
        self.assertEqual(self.service.store.messages(old['id']),[])
        self.assertEqual(self.service.store.messages(other['id']),[kept])

    def test_busy_and_terminal_worker_guard(self):
        s=self.session();task=self.service.tasks.submit(self.agent['id'],'work',session_id=s['id'])
        self.assertEqual(self.api('/api/sessions/'+s['id'],method='DELETE')[0],409)
        self.service.tasks.repo.update(task,status='cancelled',executor_active=True)
        self.assertEqual(self.api('/api/sessions/'+s['id'],method='DELETE')[0],409)
        self.service.tasks.repo.update(self.service.tasks.repo.get(task['id']),executor_active=False)
        self.assertEqual(self.api('/api/sessions/'+s['id'],method='DELETE')[0],200)
        self.assertEqual(self.service.tasks.repo.get(task['id'])['status'],'cancelled')
        self.assertTrue((self.service.data/'artifacts'/task['id']/'execution.json').exists())

    def test_other_agent_cannot_execute_in_session(self):
        from platform_core.common import Problem
        session = self.session()
        other = self.service.agents.save({'name':'other agent','model':'fixture','base_url':self.upstream.base+'/v1','api_key':'test'})
        with self.assertRaises(Problem) as rejected:
            self.service.tasks.submit(other['id'], 'cross agent', session_id=session['id'])
        self.assertEqual(rejected.exception.code, 'TASK_CONTEXT_INVALID')

    def test_session_message_still_runs_through_existing_langgraph(self):
        s=self.session();self.upstream.model_reply=lambda payload:{'role':'assistant','content':'<think>private</think>## 结果\n\n| 状态 | 值 |\n|---|---|\n| completed | 1 |'}
        code,task=self.api('/api/sessions/'+s['id']+'/messages',{'content':'check'})
        self.assertEqual(code,202);self.service.scheduler.start()
        final=self.service.tasks.wait(task['id'],45)
        self.assertEqual(final['status'],'succeeded',final.get('error'))
        self.assertNotIn('<think>',final['result']['content'])
        self.assertIn('| completed | 1 |',final['result']['content'])


if __name__=='__main__':unittest.main()
