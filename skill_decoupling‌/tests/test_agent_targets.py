import unittest
from platform_core.agent_targets import target_schema, resolve_target
from platform_core.contracts import TASK_TOOLS
from platform_core.common import Problem, validate

class Store:
    def __init__(self):
        self.agents=[{'id':'main-id','name':'Main'}, {'id':'p6-id','name':'P6'}]
        self.grants=[{'enabled':True,'caller_agent_id':'main-id','target_agent_id':a['id'],
                      'permissions':['schedule','overview'] if a['name']=='Main' else ['schedule','overview','submit']} for a in self.agents]
    def list(self,kind): return self.agents if kind=='agents' else self.grants

class TargetsTest(unittest.TestCase):
    def test_schema_names_only_and_original_unchanged(self):
        schema=target_schema(TASK_TOOLS['agent_status']['schema'],Store().agents)
        validate({'agent_name':'P6'},schema)
        self.assertNotIn('agent_id',schema['properties'])
        self.assertIn('agent_id',TASK_TOOLS['agent_status']['schema']['properties'])
        with self.assertRaises(Problem): validate({'agent_name':'p6-id'},schema)
    def test_target_is_executor_not_creator(self):
        self.assertEqual(resolve_target(Store(),'main-id','set_heartbeat_schedule','P6'),'p6-id')
        self.assertEqual(resolve_target(Store(),'main-id','set_heartbeat_schedule','Main'),'main-id')
    def test_permissions_checked_per_operation(self):
        with self.assertRaises(Problem): resolve_target(Store(),'main-id','submit_task','Main')
    def test_duplicate_and_rename_rejected(self):
        store=Store();store.agents[0]['name']='P6'
        with self.assertRaises(Problem) as e: resolve_target(store,'main-id','set_heartbeat_schedule','P6')
        self.assertEqual(e.exception.code,'AGENT_NAME_AMBIGUOUS')
        with self.assertRaises(Problem): resolve_target(Store(),'main-id','set_heartbeat_schedule','old name')
    def test_dispatch_converts_name_to_id_before_backend(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from platform_core.agents import Agents
        store=Store();store.get=lambda kind,key: next(a for a in store.agents if a['id']==key)
        call=Mock(return_value={'ok':True})
        service=SimpleNamespace(store=store,tasks=SimpleNamespace(repo=SimpleNamespace(get=lambda _: {'status':'running'}),call=call))
        schema=target_schema(TASK_TOOLS['agent_status']['schema'],store.agents)
        Agents(service).execute('main-id',{'source':'tasks','tool':'agent_status','schema':schema},{'agent_name':'P6'},'run')
        self.assertEqual(call.call_args.args[2],{'agent_id':'p6-id'})
if __name__=='__main__': unittest.main()
