"""Overview is a lightweight index; explicit detail endpoints retain complete contracts."""
import unittest
import json
import test_platform as fixtures

class OverviewTests(unittest.TestCase):
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown
    api=fixtures.PlatformTests.api

    def test_heavy_contracts_and_results_are_deferred_without_loss(self):
        tool={'name':'business_tool','description':'工程契约'*2000,'input_schema':{'type':'object','properties':{'record_id':{'type':'string','description':'真实记录标识'}},'required':['record_id']},'output_schema':{'type':'object'},'entry':'scripts/tools.py:business_tool','dependencies':[],'storage':[],'timeout':30}
        skill=self.service.store.put('skills',{'name':'heavy','description':'业务说明','enabled':True,'digest':'abc','metadata':{'metadata':{'context-scope':'source'}},'body':'业务正文'*1000,'files':['SKILL.md'],'tools':[tool]})
        run=self.service.store.put('tool_runs',{'tool':'business_tool','result':{'evidence':'证据'*5000}})
        actor=self.service.agents.save({'name':'summary actor','model':'fixture','base_url':self.upstream.base+'/v1','api_key':'fixture-secret'})
        task=self.service.tasks.submit(actor['id'],'summary task')
        checkpoint=self.service.tasks.repo.checkpoint(task['id'])
        checkpoint.update(context_tokens=12600,context_estimated=True)
        task=self.service.tasks.repo.update(task,checkpoint,status='succeeded',result={'content':'详细成果'*3000})
        full=self.service.overview()
        code,overview=self.api('/api/overview');self.assertEqual(code,200)
        brief=next(s for s in overview['skills'] if s['id']==skill['id'])
        self.assertEqual(brief['tools'],[{'name':'business_tool'}]);self.assertNotIn('body',brief);self.assertNotIn('files',brief)
        self.assertNotIn('result',next(t for t in overview['tasks'] if t['id']==task['id']))
        brief_task=next(t for t in overview['tasks'] if t['id']==task['id'])
        self.assertEqual(brief_task['context_tokens'],12600)
        self.assertTrue(brief_task['context_estimated'])
        self.assertEqual(self.api('/api/tasks/'+task['id'])[1]['result'],task['result'])
        self.assertTrue(overview['details_deferred']);self.assertNotIn('result',next(r for r in overview['tool_runs'] if r['id']==run['id']))
        self.assertLess(len(json.dumps(overview)),len(json.dumps(full))/4)
        code,detail=self.api('/api/skills/'+skill['id']);self.assertEqual(code,200);self.assertEqual(detail['tools'][0],tool)
        code,provider=self.api('/api/tool-providers/skill:'+skill['id']);self.assertEqual(code,200);self.assertEqual(provider['tools'][0],tool)
        self.assertEqual(self.api('/api/tool-runs/'+run['id'])[1]['result'],run['result'])
        # Compacting must not mutate the stored model-facing description or schema.
        self.assertEqual(self.service.store.get('skills',skill['id'])['tools'][0],tool)

if __name__=='__main__':unittest.main()
