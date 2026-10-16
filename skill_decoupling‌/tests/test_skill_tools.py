"""Skill regression with real child Python/SQLite and explicit Docker test doubles.

PortableChild is ONLY a test adapter: production remains Docker-only. It executes
the production loader in disposable child processes; it does not prove Docker
mounts or real paid model inference. No Skill imports in the test/platform process.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import ROOT
from platform_core.common import Problem
from platform_core.langchain_tools import build_tools
from platform_core.service import Service
from platform_core.skill_tools import LOADER, declarations
import test_platform as fixtures


class PortableChild:
    def __init__(self, test):
        self.test = test
        self.count = 0

    def __call__(self, row, command, payload, run_id):
        self.count += 1
        payload = copy.deepcopy(payload)
        payload['root'] = row['mounts'][0]['source']
        for logical, target in payload['context']['storage'].items():
            payload['context']['storage'][logical] = next(m['source'] for m in row['mounts'] if m['target'] == target)
        try:
            process = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', LOADER], input=json.dumps(payload).encode(), capture_output=True, timeout=row['timeout'])
        except subprocess.TimeoutExpired:
            raise Problem('CLI_TIMEOUT', 'test child execution timeout', 504)
        if process.returncode:
            raise Problem('CLI_EXIT_ERROR', 'test child exit failure', 502)
        try:
            return json.loads(process.stdout)
        except ValueError:
            raise Problem('CLI_OUTPUT_INVALID', 'test child invalid output', 502)


class SkillToolTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'tests' / '.tmp'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.source = self.root / 'shared-risk-demo'
        shutil.copytree(ROOT / 'examples/skills/shared-risk-demo', self.source)
        self.service = Service(self.root)
        self.shared = self.root / 'persistent'
        self.shared.mkdir()
        self.service.skill_tools.configure_storage('p6_p7', {'directory': str(self.shared)})
        self.skill = self.service.skills.import_skill({'source': 'shared-risk-demo'})
        self.service.skill_tools.configure(self.skill['id'], {'container': 'existing', 'code_target': '/skill', 'storage': {'queue': {'resource': 'p6_p7', 'target': '/data/queue'}}})
        self.agents = {}
        for role, names in [('p6', ['save']), ('p7', ['claim', 'complete', 'fail']), ('main', ['query', 'retry'])]:
            agent = self.service.agents.save({'name': role, 'skill_ids': [self.skill['id']]})
            binding = self.service.agents.bind({'agent_id': agent['id'], 'source': 'skill:' + self.skill['id'], 'tools': names, 'job_id': 'job-1'})
            self.agents[role] = agent, binding
        self.child = PortableChild(self)
        self.patch = patch.object(self.service.external_cli, 'run', side_effect=self.child)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.service.close()
        self.temp.cleanup()

    def tools(self, role, run='test'):
        agent = self.service.store.get('agents', self.agents[role][0]['id'])
        tools, mapping = build_tools(self.service, agent, run)
        return {item['tool']: next(t for t in tools if t.name == name) for name, item in mapping.items()}

    def call(self, role, name, args, run='test'):
        result = self.tools(role, run)[name].invoke(args)
        if result.get('isError'):
            error = result['structuredContent']['error']
            raise Problem(error['code'], error['message'])
        return result['structuredContent']

    def save(self, source='source-1'):
        return self.call('p6', 'save', {'source_id': source, 'description': '头盔缺失风险描述'})

    def query(self, record):
        return self.call('main', 'query', {'record_id': record})

    def test_import_declaration_authorization_and_langchain(self):
        self.assertEqual(self.child.count, 0)  # import/configure/bind never execute
        self.assertEqual(len(self.skill['tools']), 6)
        self.assertIn('skill:' + self.skill['id'], [p['id'] for p in self.service.tool_providers.list()])
        agent = self.service.agents.save({'name': 'no-grant', 'skill_ids': [self.skill['id']]})
        _, mapping = self.service.agents.catalog(agent)
        self.assertFalse(any(i.get('binding_source', '').startswith('skill:') for i in mapping.values()))
        result = self.save()
        self.assertEqual(result['status'], 'pending')
        self.assertGreater(self.child.count, 0)
        self.assertTrue((self.shared / 'P6_P7.sqlite3').exists())
        self.assertTrue(list((self.shared / 'executions').rglob('*.json')))
        self.assertTrue(self.service.store.list('tool_runs'))
        candidate=self.service.tool_providers.discover('skill:'+self.skill['id'])
        self.assertEqual(len(candidate['tools']),6)
        self.service.tool_providers.confirm('skill:'+self.skill['id'],{'candidate_id':candidate['candidate_id']})

    def test_invalid_declarations_paths_and_entry(self):
        original = json.loads((self.source / 'tools.json').read_text(encoding='utf-8'))
        cases = [(lambda m: m['tools'][0].update(entry='../outside.py:save'), 'PATH_INVALID'),
                 (lambda m: m['tools'][0].update(entry='scripts/queue.py:absent'), 'SKILL_ENTRY_INVALID'),
                 (lambda m: m['tools'].append(m['tools'][0]), 'TOOL_NAME_COLLISION'),
                 (lambda m: m['tools'][0].update(input_schema={'type':'object'}), 'SKILL_TOOLS_INVALID')]
        for mutate, code in cases:
            data = copy.deepcopy(original); mutate(data)
            (self.source / 'tools.json').write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(Problem) as caught:
                self.service.skills.import_skill({'source': 'shared-risk-demo'})
            self.assertEqual(caught.exception.code, code)
        (self.source/'tools.json').write_text(json.dumps(original),encoding='utf-8')
        (self.source/'scripts/queue.pyc').write_bytes(b'untrusted cached bytecode')
        with self.assertRaises(Problem) as caught:
            self.service.skills.import_skill({'source':'shared-risk-demo'})
        self.assertEqual(caught.exception.code,'SKILL_INVALID')

    def test_parameters_scope_and_unauthorized_calls(self):
        with self.assertRaises(Problem):
            self.service.skill_tools.configure_storage('private',{'directory':str(self.service.data)})
        with self.assertRaises(Exception):
            self.call('p6', 'save', {'source_id': 's', 'description': 'd', 'path': 'C:/secrets'})
        binding = self.agents['p7'][1]
        with self.assertRaises(Problem):
            self.service.agents.bind({**binding, 'tools': ['retry'] , 'job_id': ''}, binding['id'])
        with self.assertRaises(Problem):
            self.service.agents.bind({**binding, 'tools': ['claim']}, binding['id'])
        record = self.save()['record_id']
        main, binding = self.agents['main']
        stale = self.tools('main')['query']
        self.service.agents.bind({**binding, 'job_id': 'job-2'}, binding['id'])
        outcome = stale.invoke({'record_id': record})
        self.assertTrue(outcome['isError'])
        self.assertEqual(outcome['structuredContent']['error']['code'], 'BINDING_STALE')
        with self.assertRaises(Problem) as caught:
            self.query(record)
        self.assertEqual(caught.exception.code, 'RECORD_FORBIDDEN')

    def test_four_states_idempotency_and_persistence(self):
        record = self.save()['record_id']
        self.assertEqual(self.save()['record_id'], record)
        with self.assertRaises(Problem):
            self.call('p6', 'save', {'source_id':'source-1','description':'different'})
        claimed = self.call('p7', 'claim', {'lease_seconds': 60}, 'failed-model')
        self.assertEqual(self.query(record)['status'], 'processing')
        self.service.skill_tools.finish_run('failed-model', 'model_failed')
        self.assertEqual(self.query(record)['status'], 'failed')
        self.assertEqual(self.call('p7', 'claim', {'lease_seconds':60})['status'], 'no_work')
        self.call('main', 'retry', {'record_id': record, 'reason': '主Agent明确安排重试'})
        claimed2 = self.call('p7', 'claim', {'lease_seconds':60}, 'completed')
        with self.assertRaises(Problem):
            self.call('p7', 'complete', {'claim': claimed['claim'], 'assessment': 'stale'})
        args = {'claim':claimed2['claim'], 'assessment':'研判成功'}
        self.assertEqual(self.call('p7', 'complete', args)['status'], 'completed')
        self.assertEqual(self.call('p7', 'complete', args)['status'], 'completed')
        with self.assertRaises(Problem):
            self.call('p7', 'complete', {**args, 'assessment':'different'})
        self.service.skill_tools.finish_run('completed', 'assessment_not_committed')
        state = self.query(record)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['assessment'], '研判成功')
        self.assertEqual([e['action'] for e in state['history']], ['pending','processing','failed','retry','processing','completed'])
        # Every earlier child has exited; a brand new process still sees the DB.
        self.assertEqual(self.query(record)['status'], 'completed')

    def test_atomic_claim_concurrency_and_expired_fencing(self):
        record = self.save()['record_id']
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda n: self.call('p7', 'claim', {'lease_seconds':1}, 'worker-'+str(n)), range(4)))
        winners = [c for c in claims if c['status'] == 'processing']
        self.assertEqual(len(winners), 1)
        time.sleep(1.1)
        with self.assertRaises(Problem) as caught:
            self.call('p7', 'complete', {'claim': winners[0]['claim'], 'assessment':'late'})
        self.assertEqual(caught.exception.code, 'LEASE_INVALID')
        # A rejected transaction rolls back; query persists the expiry transition.
        self.assertEqual(self.query(record)['error'], 'lease_expired')
        self.assertEqual(self.call('p7', 'claim', {'lease_seconds':30})['status'], 'no_work')

    def test_revocation_disable_update_uninstall_and_data_survival(self):
        record = self.save()['record_id']
        old_tool = self.tools('p6')['save']
        agent, binding = self.agents['p6']
        self.service.agents.save({'skill_ids':[]}, agent['id'])
        self.assertTrue(old_tool.invoke({'source_id':'s2','description':'d'})['isError'])
        self.service.agents.save({'skill_ids':[self.skill['id']]}, agent['id'])
        self.service.agents.bind({**binding,'enabled':False},binding['id'])
        self.assertNotIn('save',self.tools('p6'))
        self.assertTrue(old_tool.invoke({'source_id':'s2','description':'d'})['isError'])
        self.service.agents.bind({**binding,'enabled':True},binding['id'])
        self.service.store.put('skills', {**self.skill, 'enabled':False}, self.skill['id'])
        self.assertNotIn('save', self.tools('p6'))
        self.service.store.put('skills', self.skill, self.skill['id'])
        (self.source/'SKILL.md').write_text((self.source/'SKILL.md').read_text(encoding='utf-8')+'\n更新版本',encoding='utf-8')
        updated = self.service.skills.import_skill({'source':'shared-risk-demo'})
        self.assertEqual(updated['id'], self.skill['id'])
        self.assertNotIn('save', self.tools('p6'))
        self.assertTrue(old_tool.invoke({'source_id':'s2','description':'d'})['isError'])
        for a,b in self.agents.values():
            self.service.agents.save({'skill_ids':[]}, a['id'])
        self.service.skills.remove(self.skill['id'])
        self.assertTrue((self.shared/'P6_P7.sqlite3').exists())
        self.assertTrue(list((self.shared/'executions').rglob('*.json')))

    def test_missing_dependency_code_change_invalid_output_exit_and_tool_timeout(self):
        skill = self.service.store.get('skills', self.skill['id'])
        # Edit source, then import a new approved snapshot and explicitly reauthorize.
        manifest = json.loads((self.source/'tools.json').read_text(encoding='utf-8'))
        manifest['tools'][0]['dependencies'] = ['missing_skill_dependency_xyz']
        (self.source/'tools.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.service.skills.import_skill({'source':'shared-risk-demo'})
        self.service.agents.bind(self.agents['p6'][1], self.agents['p6'][1]['id'])
        with self.assertRaises(Problem) as caught: self.save()
        self.assertEqual(caught.exception.code, 'SKILL_DEPENDENCY_MISSING')
        self.service.store.put('skills', skill, skill['id'])
        self.service.agents.bind(self.agents['p6'][1], self.agents['p6'][1]['id'])
        record = self.save()['record_id']
        self.call('p7', 'claim', {'lease_seconds':60}, 'timeout-tool')
        original = (self.source/'scripts/queue.py').read_text(encoding='utf-8')
        (self.source/'scripts/queue.py').write_text(original+'\n\ndef slow(arguments, context):\n    time.sleep(5)\n    return {"status":"completed"}\n',encoding='utf-8')
        manifest['tools'][0]['dependencies']=['sqlite3']
        manifest['tools'].append({**manifest['tools'][0],'name':'slow','entry':'scripts/queue.py:slow','input_schema':{'type':'object','properties':{},'additionalProperties':False},'timeout':1})
        (self.source/'tools.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.skill = self.service.skills.import_skill({'source':'shared-risk-demo'})
        binding = self.agents['p7'][1]
        self.service.agents.bind({**binding,'tools':['claim','complete','fail','slow']},binding['id'])
        start=time.monotonic()
        with self.assertRaises(Problem) as caught: self.call('p7','slow',{},'timeout-tool')
        self.assertEqual(caught.exception.code,'CLI_TIMEOUT')
        self.assertLess(time.monotonic()-start,4)
        # Refresh query authorization after the Skill revision.
        self.service.agents.bind(self.agents['main'][1],self.agents['main'][1]['id'])
        state=self.query(record)
        self.assertEqual(state['status'],'failed');self.assertEqual(state['error'],'tool_timeout')
        code=self.service.root/self.skill['path']/'scripts/queue.py'
        code.write_text(code.read_text(encoding='utf-8')+'\n# tamper',encoding='utf-8')
        with self.assertRaises(Problem) as caught: self.query(record)
        self.assertEqual(caught.exception.code,'SKILL_CODE_CHANGED')

    def test_actual_docker_mount_gate_not_config_only(self):
        self.patch.stop()
        client = MagicMock(); container = MagicMock()
        container.status='running';container.attrs={'Mounts':[{'Source':'wrong','Destination':'/skill','RW':False}]}
        client.containers.get.return_value=container
        with patch.object(self.service.external_cli,'docker',return_value=client), patch('platform_core.external_cli.subprocess.run') as process:
            with self.assertRaises(Problem) as caught: self.save()
            self.assertEqual(caught.exception.code,'CLI_MOUNT_CHANGED')
            process.assert_not_called()
        container.attrs={'Mounts':[{'Source':str((self.service.root/self.skill['path']).resolve()),'Destination':'/skill','RW':False},
                                   {'Source':str(self.shared),'Destination':'/data/queue','RW':True}]}
        container.id='resolved-test-container'
        with patch.object(self.service.external_cli,'docker',return_value=client), patch('platform_core.external_cli.shutil.which',return_value='docker'), patch('platform_core.external_cli.subprocess.run') as process:
            process.return_value.returncode=0
            process.return_value.stdout=b'{"error":"CLI_TERMINATION_FAILED","terminated":false}'
            with self.assertRaises(Problem) as caught: self.save()
            self.assertEqual(caught.exception.code,'CLI_TERMINATION_FAILED')
        self.patch.start()

    def test_subprocess_exit_output_and_storage_revision(self):
        original = (self.source/'scripts/queue.py').read_text(encoding='utf-8')
        original += '\n\ndef exit_bad(arguments, context):\n    raise SystemExit(7)\n\ndef contract_bad(arguments, context):\n    return {"status":42}\n\ndef output_bad(arguments, context):\n    print("bad-json")\n    return {"status":"pending"}\n'
        (self.source/'scripts/queue.py').write_text(original,encoding='utf-8')
        manifest=json.loads((self.source/'tools.json').read_text(encoding='utf-8'))
        for name in ['exit_bad','contract_bad','output_bad']:
            manifest['tools'].append({**manifest['tools'][0],'name':name,'entry':'scripts/queue.py:'+name})
        (self.source/'tools.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.skill=self.service.skills.import_skill({'source':'shared-risk-demo'})
        binding=self.agents['p6'][1]
        self.service.agents.bind({**binding,'tools':['save','exit_bad','contract_bad','output_bad']},binding['id'])
        for name, expected in [('exit_bad','CLI_EXIT_ERROR'),('contract_bad','SKILL_OUTPUT_INVALID'),('output_bad','CLI_OUTPUT_INVALID')]:
            with self.assertRaises(Problem) as caught:
                self.call('p6',name,{'source_id':'s','description':'d'})
            self.assertEqual(caught.exception.code,expected)
        stale=self.tools('p6')['save']
        self.service.skill_tools.configure_storage('p6_p7',{'directory':str(self.shared),'enabled':False})
        self.assertNotIn('save',self.tools('p6'))
        self.assertTrue(stale.invoke({'source_id':'s','description':'d'})['isError'])

    def test_mount_subdirectory_and_read_only_requirements(self):
        from platform_core.external_cli import mount_matches
        actual={'Source':'/run/desktop/mnt/host/c/project/snapshots','Destination':'/skills','RW':False}
        expected={'source':'C:\\project\\snapshots\\digest\\demo','target':'/skills/digest/demo','read_only':True}
        self.assertTrue(mount_matches(actual,expected,True))
        self.assertFalse(mount_matches({**actual,'RW':True},expected,True))
        self.assertFalse(mount_matches(actual,{**expected,'target':'/skills/other/demo'},True))


class SkillRuntimeTests(unittest.TestCase):
    """Real HTTP SDK + LangGraph, deterministic local model protocol fixture."""
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown
    api=fixtures.PlatformTests.api

    def setup_skill(self):
        self.shared=Path(self.temp.name)/'persistent';self.shared.mkdir()
        status,_=self.api('/api/skill-storage/p6_p7',{'directory':str(self.shared)})
        self.assertEqual(status,200)
        status,self.skill=self.api('/api/skills',{'source':'examples/skills/shared-risk-demo'})
        self.assertEqual(status,201)
        status,_=self.api('/api/skills/'+self.skill['id']+'/runtime',{'container':'existing','code_target':'/skill','storage':{'queue':{'resource':'p6_p7','target':'/data/queue'}}})
        self.assertEqual(status,200)
        self.agents={}
        for role,names in [('p6',['save']),('p7',['claim','complete','fail']),('main',['query','retry'])]:
            agent=self.service.agents.save({'name':role,'skill_ids':[self.skill['id']], 'model':'fixture','base_url':self.upstream.base+'/v1','api_key':'fixture','model_timeout':10,
                'output_schema':{'type':'object','properties':{'status':{'type':'string'}},'required':['status'],'additionalProperties':False}})
            binding=self.service.agents.bind({'agent_id':agent['id'],'source':'skill:'+self.skill['id'],'tools':names,'job_id':'job-1'})
            self.agents[role]=agent,binding
        self.child=PortableChild(self)

    tools=SkillToolTests.tools
    call=SkillToolTests.call
    save=SkillToolTests.save
    query=SkillToolTests.query

    def reply(self, mode):
        def respond(payload):
            tools=[m for m in payload['messages'] if m['role']=='tool']
            if not tools:
                name=next(t['function']['name'] for t in payload['tools'] if t['function']['name'].endswith('__claim'))
                return {'role':'assistant','content':'','tool_calls':[{'id':'claim-1','type':'function','function':{'name':name,'arguments':json.dumps({'lease_seconds':60})}}]}
            if mode=='timeout':
                time.sleep(11)
                return {'role':'assistant','content':'{"status":"completed"}'}
            if mode=='failed':
                return {'role':'assistant','content':'{"status":"failed"}'}
            if len(tools)==1:
                claim=json.loads(tools[0]['content'])['structuredContent']['claim']
                name=next(t['function']['name'] for t in payload['tools'] if t['function']['name'].endswith('__complete'))
                return {'role':'assistant','content':'','tool_calls':[{'id':'complete-1','type':'function','function':{'name':name,'arguments':json.dumps({'claim':claim,'assessment':'模拟研判输出'})}}]}
            return {'role':'assistant','content':'{"status":"completed"}'}
        return respond

    def test_model_failure_and_real_sdk_timeout_are_persisted(self):
        self.setup_skill()
        with patch.object(self.service.external_cli,'run',side_effect=self.child):
            for mode,reason in [('failed','model_failed'),('timeout','model_timeout')]:
                record=self.save(mode)['record_id']
                self.upstream.model_reply=self.reply(mode)
                task=self.service.tasks.submit(self.agents['p7'][0]['id'],'process one risk')
                self.service.scheduler.start()
                result=self.service.tasks.wait(task['id'])
                if mode=='timeout':
                    self.assertEqual(result['status'],'failed',result)
                    self.assertEqual(result['error']['code'],'MODEL_TIMEOUT')
                state=self.query(record)
                self.assertEqual(state['status'],'failed',state)
                self.assertEqual(state['error'],reason)
                self.assertTrue([r for r in self.service.store.list('tool_runs') if r.get('transport')=='langchain-tool' and r.get('task_id')==task['id']])

    def test_main_schedules_p7_p6_cannot_and_completed_is_preserved(self):
        self.setup_skill()
        main=self.agents['main'][0];p7=self.agents['p7'][0]
        self.service.tasks.grant({'caller_agent_id':main['id'],'target_agent_id':p7['id'],'permissions':['submit','status','result']})
        with patch.object(self.service.external_cli,'run',side_effect=self.child):
            record=self.save()['record_id']
            self.assertNotIn('submit_task',self.tools('p6'))
            parent=self.service.tasks.submit(main['id'],'主Agent安排P7')
            self.service.tasks.repo.update(parent,status='running')
            self.upstream.model_reply=self.reply('success')
            child=self.call('main','submit_task',{'agent_id':p7['id'],'description':'研判已保存风险描述'},parent['id'])
            self.service.scheduler.start()
            result=self.service.tasks.wait(child['task_id'])
            self.assertEqual(result['status'],'succeeded',result)
            state=self.query(record)
            self.assertEqual(state['status'],'completed')
            self.assertEqual(state['assessment'],'模拟研判输出')
            self.assertEqual(self.service.tasks.repo.get(child['task_id'])['caller_agent_id'],main['id'])


@unittest.skipUnless(os.environ.get('PLATFORM_SKILL_DOCKER_TEST_CONTAINER'), '真实 Skill Docker 容器未配置；非模拟通过')
class RealSkillDockerTests(unittest.TestCase):
    def test_real_shared_mount_langchain_child_and_database(self):
        # Administrator pre-mounts these two directories on an existing container.
        code_root=ROOT/'tests'/'.docker-code';data_root=ROOT/'tests'/'.docker-data'
        self.assertTrue(code_root.is_dir());self.assertTrue(data_root.is_dir())
        with tempfile.TemporaryDirectory(dir=code_root) as code, tempfile.TemporaryDirectory(dir=data_root) as shared, tempfile.TemporaryDirectory(dir=ROOT/'tests'/'.tmp') as private:
            service=Service(ROOT,Path(private)/'data')
            try:
                service.skills.snapshots=Path(code)
                skill=service.skills.import_skill({'source':'examples/skills/shared-risk-demo'})
                service.skill_tools.configure_storage('real_shared',{'directory':shared})
                relative=(ROOT/skill['path']).relative_to(code_root).as_posix()
                service.skill_tools.configure(skill['id'],{'container':os.environ['PLATFORM_SKILL_DOCKER_TEST_CONTAINER'], 'code_target':'/skill-code/'+relative,
                    'storage':{'queue':{'resource':'real_shared','target':'/skill-data/'+Path(shared).name}}})
                agents={}
                for name, tools in [('p6',['save']),('p7',['claim','complete','fail']),('main',['query','retry'])]:
                    agent=service.agents.save({'name':name,'skill_ids':[skill['id']]})
                    service.agents.bind({'agent_id':agent['id'],'source':'skill:'+skill['id'],'tools':tools,'job_id':'real-docker-job'})
                    wrapped,mapping=build_tools(service,agent,'real-docker-'+name)
                    agents[name]={item['tool']:next(t for t in wrapped if t.name==key) for key,item in mapping.items()}
                def call(role,name,args):
                    outcome=agents[role][name].invoke(args)
                    self.assertFalse(outcome['isError'],outcome)
                    return outcome['structuredContent']
                record=call('p6','save',{'source_id':'real-source','description':'真实容器工具写入'})['record_id']
                claim=call('p7','claim',{'lease_seconds':60})['claim']
                call('p7','complete',{'claim':claim,'assessment':'真实容器工具提交'})
                self.assertEqual(call('main','query',{'record_id':record})['status'],'completed')
                self.assertTrue((Path(shared)/'P6_P7.sqlite3').exists())
                self.assertTrue(list((Path(shared)/'executions').rglob('*.json')))
            finally:
                service.close()


@unittest.skipUnless(os.environ.get('PLATFORM_SKILL_REAL_MODEL_TEST') == '1', '真实模型回归需显式启用；测试模型响应不代表真实推理')
class RealModelSkillTests(unittest.TestCase):
    """Read existing model configuration without writing live definitions/data.

    Real remote inference + LangGraph; child execution uses PortableChild because
    this test is separate from the independently opt-in real Docker test.
    """
    setUp=fixtures.PlatformTests.setUp
    tearDown=fixtures.PlatformTests.tearDown
    api=fixtures.PlatformTests.api
    setup_skill=SkillRuntimeTests.setup_skill
    tools=SkillToolTests.tools
    call=SkillToolTests.call
    save=SkillToolTests.save
    query=SkillToolTests.query

    def test_real_model_claims_and_completes_via_langchain(self):
        import sqlite3
        live=ROOT/'data/platform.db'
        if not live.exists():self.skipTest('没有现有模型配置')
        with sqlite3.connect('file:'+live.resolve().as_posix()+'?mode=ro',uri=True) as db:
            configured=[]
            for row in db.execute("SELECT data FROM entities WHERE kind='agents'"):
                agent=json.loads(row[0])
                key=db.execute('SELECT value FROM secrets WHERE id=?',(agent.get('secret_ref',''),)).fetchone()
                if agent.get('enabled',True) and agent.get('model') and agent.get('base_url') and key and key[0]:
                    configured.append((agent,key[0]))
        if not configured:self.skipTest('没有已配置模型凭据')
        self.setup_skill()
        original,key=configured[0]
        p7=self.agents['p7'][0]
        self.service.agents.save({'model':original['model'],'base_url':original['base_url'],'api_key':key,'max_tokens':2048,'max_model_calls':6,'model_timeout':30,
            'system_prompt':'你是P7。必须用claim领取唯一pending风险描述，再用complete提交一句简短中文研判。工具名由目录提供。只用真实claim凭证。确认complete成功后最终返回JSON {"status":"completed"}；若失败返回 {"status":"failed"}。不调用其他Agent，不重试失败记录。'},p7['id'])
        main=self.agents['main'][0]
        self.service.tasks.grant({'caller_agent_id':main['id'],'target_agent_id':p7['id'],'permissions':['submit','status','result']})
        with patch.object(self.service.external_cli,'run',side_effect=self.child):
            record=self.save('real-model-example')['record_id']
            parent=self.service.tasks.submit(main['id'],'主Agent明确安排真实P7模型研判')
            self.service.tasks.repo.update(parent,status='running')
            child=self.call('main','submit_task',{'agent_id':p7['id'],'description':'领取并研判一条头盔缺失描述，完成后提交成果。'},parent['id'])
            self.service.scheduler.start()
            final=self.service.tasks.wait(child['task_id'])
            self.assertEqual(final['status'],'succeeded',final.get('error'))
            state=self.query(record)
            self.assertEqual(state['status'],'completed',state)
            self.assertTrue(state['assessment'])
            runs=[r for r in self.service.store.list('tool_runs') if r.get('transport')=='langchain-tool' and r.get('task_id')==child['task_id']]
            self.assertTrue(any(r['tool'].endswith('__claim') for r in runs))
            self.assertTrue(any(r['tool'].endswith('__complete') for r in runs))


if __name__ == '__main__':
    unittest.main()
