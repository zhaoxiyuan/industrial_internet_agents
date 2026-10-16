"""Declarative Skill providers. Code runs only in an administrator's Docker container.

The container is the security boundary: reviewed Skill code is trusted inside it.
Never give this container the platform DB, credentials, Docker socket or other jobs.
"""
import ast
import json
import re
from pathlib import Path, PurePosixPath

from .common import Problem, check_schema, digest, safe_path, validate


def declarations(source):
    path = source / 'tools.json'
    if not path.exists():
        return []
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise Problem('SKILL_TOOLS_INVALID', 'tools.json 必须是 UTF-8 JSON')
    if not isinstance(manifest, dict) or set(manifest) != {'version', 'tools'} or type(manifest['version']) is not int or manifest['version'] != 1 or not isinstance(manifest['tools'], list) or not 1 <= len(manifest['tools']) <= 100:
        raise Problem('SKILL_TOOLS_INVALID', '工具声明版本或数量无效')
    names = set()
    for tool in manifest['tools']:
        required = {'name', 'description', 'input_schema', 'output_schema', 'entry', 'dependencies', 'storage', 'timeout'}
        if not isinstance(tool, dict) or set(tool) - (required | {'claim_failure'}) or not required <= set(tool):
            raise Problem('SKILL_TOOLS_INVALID', '工具声明字段不完整或未知')
        name = tool['name']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) or name in names:
            raise Problem('TOOL_NAME_COLLISION', '工具名无效或重复')
        names.add(name)
        if not isinstance(tool['description'], str) or not tool['description'].strip():
            raise Problem('SKILL_TOOLS_INVALID', '工具用途不能为空')
        check_schema(tool['input_schema']); check_schema(tool['output_schema'])
        if not isinstance(tool['input_schema'], dict) or tool['input_schema'].get('type') != 'object' or tool['input_schema'].get('additionalProperties') is not False:
            raise Problem('SKILL_TOOLS_INVALID', '输入须为封闭 object 契约')
        if type(tool['timeout']) is not int or not 1 <= tool['timeout'] <= 120:
            raise Problem('SKILL_TOOLS_INVALID', '工具超时须为1–120秒')
        for key in ('dependencies', 'storage'):
            values = tool[key]
            if not isinstance(values, list) or any(not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', v) for v in values) or len(set(values)) != len(values):
                raise Problem('SKILL_TOOLS_INVALID', key + '须为唯一逻辑名数组')
        entry = tool['entry']
        if not isinstance(entry, str) or entry.count(':') != 1:
            raise Problem('SKILL_ENTRY_INVALID', '入口格式为 scripts/file.py:function')
        relative, function = entry.split(':')
        file = safe_path(source, relative)
        if PurePosixPath(relative).parts[0] not in ('scripts', 'references') or file.suffix != '.py' or not file.is_file():
            raise Problem('SKILL_ENTRY_INVALID', '只允许显式引用 scripts/references Python 文件')
        try:
            tree = ast.parse(file.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeError):
            raise Problem('SKILL_ENTRY_INVALID', '入口 Python 语法无效')
        if not any(isinstance(n, ast.FunctionDef) and n.name == function and len(n.args.args) == 2 and not n.args.posonlyargs and not n.args.kwonlyargs and not n.args.vararg and not n.args.kwarg and not n.decorator_list for n in tree.body):
            raise Problem('SKILL_ENTRY_INVALID', '入口须为顶层同步函数 (arguments, context)')
    for tool in manifest['tools']:
        if tool.get('claim_failure'):
            failure = next((t for t in manifest['tools'] if t['name'] == tool['claim_failure']), None)
            if not failure or failure.get('claim_failure') or not set(failure['storage']) <= set(tool['storage']):
                raise Problem('SKILL_TOOLS_INVALID', 'claim_failure 须引用同包失败工具且不能扩大存储范围')
    return manifest['tools']


LOADER = r'''
import hashlib,importlib.util,json,pathlib,sys
p=json.load(sys.stdin)
for name in p['dependencies']:
 if importlib.util.find_spec(name) is None:
  print(json.dumps({'__skill_error':'SKILL_DEPENDENCY_MISSING'}));sys.exit(0)
root=pathlib.Path(p['root']).resolve()
for relative,expected in p['hashes'].items():
 file=(root/relative).resolve()
 if not file.is_relative_to(root) or hashlib.sha256(file.read_bytes()).hexdigest()!=expected:
  print(json.dumps({'__skill_error':'SKILL_CODE_CHANGED'}));sys.exit(0)
file,function=p['entry'].split(':')
spec=importlib.util.spec_from_file_location('skill_entry',root/file)
module=importlib.util.module_from_spec(spec)
exec(compile((root/file).read_bytes(),str(root/file),'exec'),module.__dict__)
try: value=getattr(module,function)(p['arguments'],p['context'])
except Exception as error:
 print(json.dumps({'__skill_error':str(error) if str(error).isupper() and len(str(error))<80 else 'SKILL_EXECUTION_ERROR'}));sys.exit(0)
print(json.dumps(value,ensure_ascii=False))
'''


class SkillTools:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def configure_storage(self, name, data):
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', name):
            raise Problem('PARAMETER_INVALID', '逻辑存储名无效')
        path = Path(data.get('directory', '')).resolve()
        # Administrator API only; directories must already exist, never created from model input.
        if not path.is_dir() or path.is_relative_to(self.service.skills.snapshots.resolve()) or path.is_relative_to(self.service.data) or self.service.data.is_relative_to(path):
            raise Problem('SKILL_STORAGE_INVALID', '持久化目录须已存在且与代码及平台私有数据分离')
        return self.store.put('skill_storage', {'directory': str(path), 'enabled': bool(data.get('enabled', True))}, name)

    def configure(self, sid, data):
        skill = self.store.get('skills', sid)
        old = self.store.get('skill_runtimes', sid, required=False) or {}
        # Reuse CLI registration validation; it never executes or starts services.
        row = self.service.external_cli.save(dict(name=skill['name'], container=data.get('container'), executable=data.get('python', 'python3'),
            working_directory=data.get('code_target'), commands={'execute': []}, timeout=30), preview=True)
        target = row['working_directory']
        if target == '/' or ':' in target or '\\' in target:
            raise Problem('SKILL_STORAGE_INVALID', '代码挂载点无效')
        mappings = data.get('storage', {})
        if not isinstance(mappings, dict):
            raise Problem('SKILL_STORAGE_INVALID', 'storage 须为逻辑资源映射')
        for logical, mapping in mappings.items():
            if not isinstance(mapping, dict) or set(mapping) != {'resource', 'target'}:
                raise Problem('SKILL_STORAGE_INVALID', '存储映射须为 resource/target')
            self.store.get('skill_storage', mapping['resource'])
            dest = mapping['target']
            if not isinstance(dest, str) or not dest.startswith('/') or '..' in dest.split('/') or ':' in dest or '\\' in dest:
                raise Problem('SKILL_STORAGE_INVALID', '容器存储位置无效')
        destinations = [target, *[m['target'] for m in mappings.values()]]
        for i, a in enumerate(destinations):
            for b in destinations[i+1:]:
                if PurePosixPath(a).is_relative_to(b) or PurePosixPath(b).is_relative_to(a):
                    raise Problem('SKILL_STORAGE_INVALID', '代码及存储挂载不能重叠')
        return self.store.put('skill_runtimes', {'container': row['container'], 'python': row['executable'], 'code_target': target,
            'storage': mappings, 'revision': old.get('revision', 0)+1, 'enabled': bool(data.get('enabled', True))}, sid)

    def revision(self, skill):
        runtime = self.store.get('skill_runtimes', skill['id'])
        return digest([skill['digest'], runtime, [self.store.get('skill_storage', m['resource']) for m in runtime['storage'].values()]])

    def provider(self, sid, agent):
        skill = self.store.get('skills', sid)
        runtime = self.store.get('skill_runtimes', sid)
        if sid not in agent['skill_ids'] or not skill['enabled'] or not runtime['enabled']:
            raise Problem('BINDING_DISABLED', 'Skill 未绑定或已停用', 409)
        return skill, runtime

    def call(self, binding, name, arguments, run_id):
        agent = self.store.get('agents', binding['agent_id'])
        skill, runtime = self.provider(binding['source'][6:], agent)
        if self.revision(skill) != binding['revision']:
            raise Problem('BINDING_STALE', 'Skill 内容、执行配置或存储映射已变更，需重新授权', 409)
        tool = next(t for t in skill['tools'] if t['name'] == name)
        validate(arguments, tool['input_schema'])
        try:
            result = self.run(skill, runtime, tool, arguments, binding, run_id)
            try:
                validate(result, tool['output_schema'])
            except Problem:
                raise Problem('SKILL_OUTPUT_INVALID', '工具输出不满足声明契约', 502)
            if tool.get('claim_failure') and result.get('claim'):
                failure = next(t for t in skill['tools'] if t['name'] == tool['claim_failure'])
                key = digest([run_id, binding['id'], result['claim']])
                self.store.put('skill_claims', {'run_id': run_id, 'binding': binding, 'skill': skill, 'runtime': runtime,
                    'failure': failure, 'claim': result['claim'], 'status': 'processing'}, key)
            if name=='bind_work_permit':
                try:
                    task=self.service.tasks.repo.get(run_id)
                    from .workflow import scope_for_agent
                    self.service.tasks.repo.update(task,business_scope=scope_for_agent(self.service,binding['agent_id']))
                except Problem as error:
                    if error.code!='TASK_NOT_FOUND':raise
            return result
        except Problem as error:
            if error.code in ('CLI_TIMEOUT', 'CLI_RESULT_UNKNOWN', 'CLI_EXIT_ERROR', 'CLI_TERMINATION_FAILED', 'SKILL_EXECUTION_ERROR'):
                self.finish_run(run_id, 'tool_timeout' if error.code == 'CLI_TIMEOUT' else 'tool_failed')
            raise

    def run(self, skill, runtime, tool, arguments, binding, run_id, cleanup_context=None):
        code = self.service.root / skill['path']
        mounts = [{'source': str(code.resolve()), 'target': runtime['code_target'], 'read_only': True}]
        resources = {}
        for logical in tool['storage']:
            mapping = runtime['storage'].get(logical)
            if not mapping:
                raise Problem('SKILL_STORAGE_MISSING', '未配置所需逻辑存储', 409)
            resource = self.store.get('skill_storage', mapping['resource'])
            if not resource['enabled']:
                raise Problem('SKILL_STORAGE_DISABLED', '存储已停用', 409)
            resources[logical] = mapping['target']
            mounts.append({'source': resource['directory'], 'target': mapping['target'], 'read_only': False})
        payload = {'root': runtime['code_target'], 'entry': tool['entry'], 'dependencies': tool['dependencies'],
            'hashes': skill['file_hashes'],
            'arguments': arguments, 'context': {'agent_id': binding['agent_id'], 'job_id': binding['job_id'], 'storage': resources, 'run_id': run_id}}
        if binding.get('source_id'):
            payload['context'].pop('job_id')
            payload['context']['source_id']=binding['source_id']
            if True:
                from industrial_skills.acquisition import source_store
                resource=self.store.get('skill_storage',runtime['storage']['p6_p7']['resource'])
                business=source_store(resource['directory'],binding['source_id'])
                with business.db() as db:
                    active=business.get(db,'active_job','current',False)
                root_run=run_id
                try:
                    task=self.service.tasks.repo.get(run_id)
                    root_run=task.get('root_task_id',run_id)
                except Problem:
                    pass
                key=digest([binding['source_id'],root_run])
                pinned=self.store.get('workflow_contexts',key,required=False)
                task_scope={}
                try:task_scope=self.service.tasks.repo.get(run_id).get('business_scope') or {}
                except Problem:pass
                job_id=(task_scope if task_scope.get('job_id') else pinned or active or {}).get('job_id','')
                if job_id and not pinned:
                    self.store.put('workflow_contexts',{'source_id':binding['source_id'],'root_run_id':root_run,'job_id':job_id},key)
                payload['context']['job_id']=job_id

        if tool['name'] in ('bind_work_permit','configure_participants') and binding.get('source_id'):
            from industrial_skills.acquisition import source_store
            resource=self.store.get('skill_storage',runtime['storage']['p6_p7']['resource'])
            cfg=source_store(resource['directory'],binding['source_id']).config()
            actors=cfg.get('agents',{}).get('p8p9',[])
            candidates=[b for b in self.store.list('bindings') if b.get('enabled') and b['agent_id'] in actors and b['source']=='builtin' and 'feishu_sync_risk_card' in b.get('tools',[]) and b.get('connection_ref')]
            if len(candidates)==1:
                connection=candidates[0]['connection_ref']
                directory=self.service.feishu.require(connection).get('users',{})
                payload['context']['participant_directory']={'connection_id':connection,'user_ids':list(directory)}

        if cleanup_context is not None:
            payload['context']={**cleanup_context,'_cleanup':True}
        else:
            try:task=self.service.tasks.repo.get(run_id)
            except Problem:task=None
            if task:
                self.service.runtime.guard(task)
            # Persist cleanup intent BEFORE acquisition: a lost reply must not strand a lease.
            if tool.get('claim_failure'):
                failure=next(t for t in skill['tools'] if t['name']==tool['claim_failure'])
                if 'claim' not in failure['input_schema'].get('required',[]):
                    key=digest([run_id,binding['id'],'run-bound'])
                    self.store.put('skill_claims',{'run_id':run_id,'binding':binding,'skill':skill,'runtime':runtime,
                        'failure':failure,'claim':None,'cleanup_context':payload['context'],'status':'processing'},key)

        row = {'name': skill['name'], 'container': runtime['container'], 'runner_python': runtime['python'], 'executable': runtime['python'], 'mount_subpaths': True,
            'arguments': ['-I', '-X', 'utf8', '-c', LOADER], 'commands': {'execute': []}, 'working_directory': runtime['code_target'], 'timeout': tool['timeout'], 'mounts': mounts}
        result = self.service.external_cli.run(row, 'execute', payload, run_id)
        if isinstance(result, dict) and result.get('__skill_error'):
            raise Problem(result['__skill_error'], 'Skill 工具执行失败', 502)
        self.store.put('tool_runs', {'agent_id': binding['agent_id'], 'run_id': run_id, 'transport': 'skill-docker', 'tool': tool['name'], 'skill_digest': skill['digest'], 'ok': True})
        return result

    def cleanup_context(self,claim):
        binding=claim['binding'];runtime=claim['runtime']
        context={'agent_id':binding['agent_id'],'run_id':claim['run_id'],'storage':{k:v['target'] for k,v in runtime['storage'].items()}}
        try:scope=self.service.tasks.repo.get(claim['run_id']).get('business_scope') or {}
        except Problem:scope={}
        context['job_id']=scope.get('job_id') or binding.get('job_id','')
        if binding.get('source_id'):context['source_id']=binding['source_id']
        return context

    def finish_run(self, run_id, reason):
        """Compensate previously acquired leases without asking a timed-out model.

        Internal cleanup may use the original approved snapshot after revocation;
        it can only fail its own recorded lease, never acquire or retry work.
        """
        for claim in self.store.list('skill_claims'):
            if claim['run_id'] != run_id or claim['status'] != 'processing':
                continue
            try:
                args = {'reason':reason}
                if claim['claim'] is not None:args['claim']=claim['claim']
                validate(args, claim['failure']['input_schema'])
                result = self.run(claim['skill'], claim['runtime'], claim['failure'], args, claim['binding'], run_id, cleanup_context=claim.get('cleanup_context') or self.cleanup_context(claim))
                validate(result, claim['failure']['output_schema'])
                self.store.put('skill_claims', {**claim, 'status': 'closed'}, claim['id'])
            except Problem as error:
                if error.code in ('JOB_ALREADY_CLOSED','WORK_PERMIT_REQUIRED','RECORD_NOT_FOUND_OR_FORBIDDEN','LEASE_INVALID'):
                    self.store.put('skill_claims',{**claim,'status':'closed'},claim['id']);continue
                self.store.put('tool_runs', {'run_id': run_id, 'transport': 'skill-cleanup', 'tool': claim['failure']['name'], 'error': error.as_dict()})
                # Retain the receipt for operator recovery; lease expiry is the fallback.
