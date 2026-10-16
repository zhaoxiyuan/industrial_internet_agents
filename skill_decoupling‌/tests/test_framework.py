"""Framework integration, provider registration and Docker-only boundary tests."""
import json, os, sqlite3, unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, MagicMock
import test_platform as fixtures
from platform_core.common import Problem, dumps
from platform_core.external_cli import RUNNER, LOCAL_ENGINE
from platform_core.langchain_tools import build_tools
from platform_core.skill_backend import SkillBackend
from langchain_core.tools import BaseTool
class FrameworkTests(unittest.TestCase):
 setUp=fixtures.PlatformTests.setUp
 tearDown=fixtures.PlatformTests.tearDown
 api=fixtures.PlatformTests.api
 wait_operation=fixtures.PlatformTests.wait_operation
 def agent(self): return self.service.agents.save({'name':'framework','model':'test','base_url':self.upstream.base+'/v1','api_key':'test'})
 def cli(self): return self.service.external_cli.save({'name':'container','description':'test CLI','container':'fixture','executable':'python','working_directory':'/app','commands':{'echo':['echo']},'mounts':[{'source':'source','target':'/app','read_only':True}]})
 def test_framework_tools_bypass_cli_bridge(self):
  agent=self.agent(); p=self.cli()
  self.service.agents.bind({'agent_id':agent['id'],'source':'cli:'+p['id'],'tools':['execute']})
  tools,_=build_tools(self.service,agent,'manual')
  self.assertEqual(len(tools),3);self.assertIsInstance(tools[0],BaseTool)
  with patch.object(self.service.cli,'invoke',side_effect=AssertionError('old bridge')),patch.object(self.service.external_cli,'run',return_value={'echo':'ok'}):
   self.assertEqual(tools[0].invoke({'command':'echo'})['structuredContent'],{'echo':'ok'})
 def test_skill_official_middleware_and_read_only(self):
  root=Path(self.temp.name)/'sample';root.mkdir()
  (root/'SKILL.md').write_text('---\nname: sample\ndescription: test expertise\n---\nSECRET_BODY',encoding='utf-8')
  skill=self.service.skills.import_skill({'source':root.relative_to(self.service.root).as_posix()})
  agent=self.agent();agent=self.service.agents.save({'skill_ids':[skill['id']]},agent['id'])
  def reply(payload):
   if not any(m['role']=='tool' for m in payload['messages']):
    system=' '.join(m['content'] if isinstance(m['content'],str) else dumps(m['content']) for m in payload['messages'] if m['role']=='system')
    self.assertIn('sample',system);self.assertNotIn('SECRET_BODY',system)
    return {'role':'assistant','content':'','tool_calls':[{'id':'read','type':'function','function':{'name':'read_file','arguments':dumps({'file_path':'/skills/sample/SKILL.md'})}}]}
   self.assertIn('SECRET_BODY',payload['messages'][-1]['content'])
   return {'role':'assistant','content':'Skill loaded'}
  self.upstream.model_reply=reply
  task=self.service.tasks.submit(agent['id'],'read skill');self.service.scheduler.start()
  final=self.service.tasks.wait(task['id']);self.assertEqual(final['status'],'succeeded',final)
  backend=SkillBackend(self.service,agent)
  with self.assertRaises(Problem):backend.read('/data/platform.db')
  self.service.agents.save({'skill_ids':[]},agent['id'])
  with self.assertRaises(Problem):backend.read('/skills/sample/SKILL.md')
  with closing(sqlite3.connect(self.service.data/'checkpoints.sqlite3')) as db:self.assertGreater(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0],0)
 def test_provider_import_preview_and_export(self):
  data={'kind':'cli','name':'example','container':'fixture','executable':'python','commands':{'status':['status']}}
  status,result=self.api('/api/tool-providers/import',data);self.assertEqual(status,200)
  self.assertEqual(self.service.store.list('cli_services'),[])
  status,p=self.api('/api/tool-providers',data);self.assertEqual(status,201)
  status,export=self.api('/api/tool-providers/'+p['id']+'/export');self.assertEqual(status,200);self.assertNotIn('secret_ref',export)
  status,_=self.api('/api/tool-providers/import',{**data,'token':'secret'});self.assertEqual(status,400)
 def test_discover_requires_confirmation_and_stale_candidate(self):
  status,p=self.api('/api/tool-providers',{'kind':'mcp','name':'test','endpoint':self.upstream.base+'/rpc'})
  status,op=self.api('/api/tool-providers/'+p['id']+'/discover',{});self.assertEqual(status,202)
  complete=self.wait_operation(op);self.assertEqual(complete['status'],'succeeded',complete)
  raw=self.service.store.get('mcps',p['provider_id']);self.assertFalse(raw['healthy'])
  status,confirmed=self.api('/api/tool-providers/'+p['id']+'/confirm',{'candidate_id':complete['result']['candidate_id']});self.assertEqual(status,200);self.assertTrue(confirmed['healthy'])
 def test_docker_exec_fixed_target_and_json_input(self):
  p=self.cli();client=MagicMock();client.__enter__.return_value=client
  container=MagicMock();container.status='running';container.id='resolved-id';container.attrs={'Mounts':[{'Source':'source','Destination':'/app','RW':False}]};client.containers.get.return_value=container
  with patch.object(self.service.external_cli,'docker',return_value=client),patch('platform_core.external_cli.shutil.which',return_value='docker'),patch('platform_core.external_cli.subprocess.run') as run:
   run.return_value.returncode=0;run.return_value.stdout=b'{"data":{"ok":true}}'
   result=self.service.external_cli.call(p['id'],'execute',{'command':'echo','input':{'text':'$(literal) 中文'}},p['revision'],'task')
   self.assertEqual(result,{'ok':True});argv=run.call_args.args[0]
   self.assertEqual(argv[:4],['docker','--host',LOCAL_ENGINE,'exec']);self.assertIn('resolved-id',argv);self.assertNotIn('-t',argv)
   payload=json.loads(run.call_args.kwargs['input']);self.assertEqual(payload['input']['text'],'$(literal) 中文');self.assertNotIn('shell',run.call_args.kwargs)
 def test_legacy_cli_disabled_and_cannot_execute(self):
  self.service.store.put('cli_services',{'name':'old','enabled':True,'execution_target':'platform-local','revision':1},'old')
  from platform_core.external_cli import ExternalCli
  cli=ExternalCli(self.service.store);self.assertFalse(self.service.store.get('cli_services','old')['enabled'])
  with self.assertRaises(Problem) as error:cli.call('old','anything',{},1,'task')
  self.assertEqual(error.exception.code,'CLI_MIGRATION_REQUIRED')
 def test_runner_error_receipt_not_success(self):
  p=self.cli();client=MagicMock();client.__enter__.return_value=client
  container=MagicMock();container.status='running';container.id='fixed';container.attrs={'Mounts':[{'Source':'source','Destination':'/app','RW':False}]};client.containers.get.return_value=container
  with patch.object(self.service.external_cli,'docker',return_value=client),patch('platform_core.external_cli.shutil.which',return_value='docker'),patch('platform_core.external_cli.subprocess.run') as run:
   run.return_value.returncode=0
   for code in ['CLI_TIMEOUT','CLI_OUTPUT_LIMIT','CLI_EXIT_ERROR','CLI_OUTPUT_INVALID']:
    run.return_value.stdout=json.dumps({'error':code,'terminated':True}).encode()
    with self.assertRaises(Problem) as error:self.service.external_cli.call(p['id'],'execute',{'command':'echo'},p['revision'],'task')
    self.assertEqual(error.exception.code,code)
 def test_unknown_invocation_not_replayed(self):
  from platform_core.runtime_middleware import PlatformMiddleware
  from types import SimpleNamespace
  agent=self.agent();task=self.service.tasks.submit(agent['id'],'test')
  self.service.tasks.repo.update(task,status='running')
  from platform_core.common import digest
  call={'name':'tool','id':'fixed','args':{'x':1}}
  self.service.store.put('tool_invocations',{'fingerprint':digest(['tool',{'x':1}]),'status':'started','task_id':task['id']},digest([task['id'],'fixed']))
  middleware=PlatformMiddleware(self.service.runtime,task);handler=MagicMock()
  with patch.object(self.service.agents,'catalog',return_value=([],{'tool':{}})):
   result=middleware.wrap_tool_call(SimpleNamespace(tool_call=call),handler)
  handler.assert_not_called();self.assertIn('RESULT_UNKNOWN',result.content)
 def test_cli_command_schema_blocks_string_identity(self):
  provider=self.service.external_cli.save({'name':'typed','container':'fixture','executable':'python','working_directory':'/app',
   'commands':{'read':['reader']},'command_schemas':{'read':{'type':'object','properties':{'task_id':{'type':'integer'}},'required':['task_id'],'additionalProperties':False}}})
  from platform_core.common import validate
  schema=provider['tools'][0]['input_schema']
  validate({'command':'read','input':{'task_id':1}},schema)
  with self.assertRaises(Problem):validate({'command':'read','input':{'task_id':'1'}},schema)
  agent=self.agent()
  self.service.agents.bind({'agent_id':agent['id'],'source':'cli:'+provider['id'],'tools':['execute']})
  tools,_=build_tools(self.service,agent,'manual')
  with patch.object(self.service.external_cli,'run',return_value={'ok':True}) as run:
   tools[0].invoke({'command':'read','input':{'task_id':'1'}})
   self.assertEqual(run.call_args.args[2],{'task_id':1})
   reply=tools[0].invoke({'command':'read','input':{'task_id':'not-an-id'}})
   self.assertFalse(reply['ok'])
   self.assertEqual(run.call_count,1)
  provider=self.service.external_cli.save({'name':'typed-list','container':'fixture','executable':'python','working_directory':'/app',
   'commands':{'read':['reader']},'command_schemas':{'read':{'type':'object','properties':{'ids':{'type':'array','items':{'type':'integer'}}},'required':['ids'],'additionalProperties':False}}})
  binding=self.service.store.list('bindings')[0]
  self.service.store.delete('bindings',binding['id'])
  self.service.agents.bind({'agent_id':agent['id'],'source':'cli:'+provider['id'],'tools':['execute']})
  tools,_=build_tools(self.service,agent,'manual')
  with patch.object(self.service.external_cli,'run',return_value={'ok':True}) as run:
   tools[0].invoke({'command':'read','input':{'ids':'["1", 2]'}})
   self.assertEqual(run.call_args.args[2],{'ids':[1,2]})
   tools[0].invoke({'command':'read','input':{'ids':{'item':'3'}}})
   self.assertEqual(run.call_args.args[2],{'ids':[3]})
   tools[0].invoke({'command':'read','input':{'ids':''}})
   self.assertEqual(run.call_args.args[2],{'ids':[]})
   self.assertFalse(tools[0].invoke({'command':'read','input':{'ids':'not-json'}})['ok'])
   self.assertFalse(tools[0].invoke({'command':'read','input':{'ids':{'item':1,'unknown':2}}})['ok'])
 def test_subtask_contract_normalizes_nested_role_lists(self):
  caller,target=self.agent(),self.agent()
  target=self.service.agents.save({'input_schema':{'type':'object','properties':{'roles':{'type':'object','additionalProperties':{'type':'array','items':{'type':'string'}}}},'required':['roles'],'additionalProperties':False}},target['id'])
  self.service.tasks.grant({'caller_agent_id':caller['id'],'target_agent_id':target['id'],'permissions':['submit']})
  parent=self.service.tasks.submit(caller['id'],'parent')
  parent=self.service.tasks.repo.update(parent,status='running')
  task=self.service.tasks.submit(target['id'],'child',{'roles':{'welder':{'item':['helmet','goggles']}}},parent_id=parent['id'])
  self.assertEqual(task['input'],{'roles':{'welder':['helmet','goggles']}})
 def test_cli_unknown_subcommand_blocked(self):
  p=self.cli()
  with patch.object(self.service.external_cli,'run') as run:
   with self.assertRaises(Problem):self.service.external_cli.call(p['id'],'execute',{'command':'shell'},p['revision'],'task')
   run.assert_not_called()
 def test_real_docker_execution_if_available(self):
  if not os.environ.get('PLATFORM_DOCKER_TEST_CONTAINER'):self.skipTest('未配置已运行测试容器；不构建 Docker')
  p=self.service.external_cli.save({'name':'docker integration','container':os.environ['PLATFORM_DOCKER_TEST_CONTAINER'],'executable':'python3','working_directory':'/tmp','commands':{'echo':['-c','import json,sys; print(json.dumps(json.load(sys.stdin)))']}})
  self.assertEqual(self.service.external_cli.call(p['id'],'execute',{'command':'echo','input':{'message':'中文'}},p['revision'],'test'),{'message':'中文'})

 def test_openapi_preview_and_unsupported_contract(self):
  document={'openapi':'3.0.3','servers':[{'url':self.upstream.base}],'paths':{'/query':{'get':{'operationId':'query','parameters':[{'name':'q','in':'query','schema':{'type':'string'}}],'responses':{'200':{'content':{'application/json':{'schema':{'type':'object','additionalProperties':True}}}}}}}}}
  status,result=self.api('/api/tool-providers/openapi',{'document':document})
  self.assertEqual(status,200);self.assertEqual(result['definitions'][0]['method'],'GET');self.assertEqual(self.service.store.list('http_services'),[])
  document['paths']['/query']['get']['parameters'][0]['in']='header'
  self.assertEqual(self.api('/api/tool-providers/openapi',{'document':document})[0],400)
  self.assertEqual(self.api('/api/tool-providers/import',{'kind':'cli','name':'bad','executable':'python'})[0],400)

 def test_stream_round_trip_and_reconnect_ids(self):
  self.upstream.model_reply=lambda payload:{'role':'assistant','content':'框架流式回执'}
  agent=self.agent();task=self.service.tasks.submit(agent['id'],'stream');self.service.scheduler.start()
  final=self.service.tasks.wait(task['id']);self.assertEqual(final['status'],'succeeded',final)
  events=self.service.runtime.events(task['id']);self.assertTrue(any(e['type']=='text_delta' for e in events))
  from urllib.request import urlopen
  with urlopen(self.base+'/api/tasks/'+task['id']+'/stream',timeout=5) as response:raw=response.read().decode()
  self.assertIn('text_delta',raw);self.assertIn('event: finished',raw);self.assertNotIn('test-key',raw)
  self.assertEqual(self.service.runtime.events(task['id'],events[-1]['id']),[])

 def test_docker_mount_drift_blocks_execution(self):
  p=self.cli();client=MagicMock();client.__enter__.return_value=client
  container=MagicMock();container.status='running';container.attrs={'Mounts':[{'Source':'other','Destination':'/app','RW':True}]};client.containers.get.return_value=container
  with patch.object(self.service.external_cli,'docker',return_value=client),patch('platform_core.external_cli.subprocess.run') as run:
   with self.assertRaises(Problem) as error:self.service.external_cli.call(p['id'],'execute',{'command':'echo'},p['revision'],'task')
   self.assertEqual(error.exception.code,'CLI_MOUNT_CHANGED');run.assert_not_called()

 def test_model_step_limit(self):
  p=self.service.direct.save({'name':'echo','endpoint':self.upstream.base+'/query','method':'GET','input_schema':{'type':'object','properties':{},'additionalProperties':False},'output_schema':{'type':'object','additionalProperties':True}})
  agent=self.agent();self.service.agents.bind({'agent_id':agent['id'],'source':'http:'+p['id'],'tools':['call']})
  def reply(payload):
   name=payload['tools'][0]['function']['name'];n=len([m for m in payload['messages'] if m['role']=='tool'])
   return {'role':'assistant','content':'','tool_calls':[{'id':'repeat_'+str(n),'type':'function','function':{'name':name,'arguments':'{}'}}]}
  self.upstream.model_reply=reply
  task=self.service.tasks.submit(agent['id'],'bounded');self.service.scheduler.start();final=self.service.tasks.wait(task['id'])
  self.assertEqual(final['error']['code'],'AGENT_STEP_LIMIT');self.assertEqual(len(self.service.store.list('tool_invocations')),12)

 def test_stream_drains_more_than_one_page(self):
  agent=self.agent();task=self.service.tasks.submit(agent['id'],'events')
  self.assertEqual(self.service.tasks.wait(task['id'])['status'],'succeeded')
  with self.service.store.db() as db:db.execute('DELETE FROM runtime_events WHERE task_id=?',(task['id'],))
  for n in range(205):self.service.runtime.event(task['id'],'text_delta',{'text':str(n)})
  from urllib.request import urlopen
  with urlopen(self.base+'/api/tasks/'+task['id']+'/stream',timeout=5) as response:raw=response.read().decode()
  self.assertEqual(raw.count('data: {"type":"text_delta"'),205);self.assertIn('event: finished',raw)

 def test_tool_callback_cannot_override_dispatch_item(self):
  agent=self.agent();p=self.service.direct.save({'name':'permissive','endpoint':self.upstream.base,'input_schema':{'type':'object','additionalProperties':True}})
  self.service.agents.bind({'agent_id':agent['id'],'source':'http:'+p['id'],'tools':['call']})
  tools,_=build_tools(self.service,agent,'manual')
  with patch.object(self.service.direct,'call',return_value={'ok':True}) as call:
   self.assertFalse(tools[0].invoke({'_item':{'tool':'fake'}})['isError'])
   self.assertEqual(call.call_args.args[1],{'_item':{'tool':'fake'}})
