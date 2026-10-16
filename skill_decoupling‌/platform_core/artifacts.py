"""Immutable task artifact declarations; bytes remain owned by business providers."""
import json
from pathlib import PurePosixPath

from .common import Problem, digest, dumps, now

TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 500}
ARTIFACT_TOOLS = {
    'register_artifact': {'description': '登记本任务已由业务工具保存的成果引用；保存不可变声明，不读取业务文件。路径必须是相对路径。',
        'schema': {'type': 'object', 'properties': {**{k: TEXT for k in ('provider', 'record_ref', 'relative_path', 'content_type', 'sha256', 'title')},
            'version': {'type': 'integer', 'minimum': 1}},
            'required': ['provider', 'record_ref', 'relative_path', 'content_type', 'sha256', 'version'], 'additionalProperties': False}},
    'get_artifact': {'description': '读取本任务、授权后代或上游显式传入的成果声明；业务内容通过声明对应的已授权工具读取。',
        'schema': {'type': 'object', 'properties': {'artifact_id': TEXT}, 'required': ['artifact_id'], 'additionalProperties': False}}
}

class Artifacts:
    def __init__(self, service):
        self.service = service

    def context(self, caller, task_id):
        task = self.service.tasks.repo.get(task_id)
        if task['executor_agent_id'] != caller or task['status'] != 'running' or task.get('cancel_requested'):
            raise Problem('TASK_CONTEXT_INVALID', '成果工具需要当前有效任务', 403)
        return task

    def accessible(self, caller, task_id, artifact_id):
        task = self.service.tasks.repo.get(task_id)
        value = self.service.store.get('artifact_refs', artifact_id)
        if value['task_id'] == task_id:
            return value
        if value['task_id'] in task.get('depends_on', []):
            parent=self.service.tasks.repo.get(task['parent_task_id'])
            source=self.service.tasks.repo.get(value['task_id'])
            self.service.tasks.authorize(parent['executor_agent_id'],caller,'submit')
            self.service.tasks.authorize(parent['executor_agent_id'],source['executor_agent_id'],'result')
            return value
        if artifact_id in task.get('artifact_ids', []):
            parent = self.service.tasks.repo.get(task['parent_task_id'])
            self.service.tasks.authorize(parent['executor_agent_id'], caller, 'submit')
            # Recheck any result permission needed when the parent forwarded another Agent's output.
            self.accessible(parent['executor_agent_id'], parent['id'], artifact_id)
            return value
        self.service.tasks.view(caller, value['task_id'], 'result', task_id)
        return value

    def call(self, caller, name, args, task_id):
        self.context(caller, task_id)
        if name == 'get_artifact':
            return self.accessible(caller, task_id, args['artifact_id'])
        path = PurePosixPath(args['relative_path'])
        import re
        if path.is_absolute() or '..' in path.parts or '\\' in args['relative_path'] or ':' in args['relative_path']:
            raise Problem('PATH_INVALID', '成果路径须为存储内相对路径')
        if not re.fullmatch('[0-9a-f]{64}', args['sha256']):
            raise Problem('PARAMETER_INVALID', 'sha256 须为64位小写十六进制')
        identity = digest([task_id, args['provider'], args['record_ref'], args['version']])
        with self.service.tasks.lock:
            old = self.service.store.get('artifact_refs', identity, required=False)
            declaration = dict(args, task_id=task_id, agent_id=caller)
            if old and any(old.get(k) != v for k, v in declaration.items()):
                raise Problem('ARTIFACT_CONFLICT', '同一成果版本已登记其他内容', 409)
            value = old or self.service.store.put('artifact_refs', declaration, identity)
            directory = self.service.data / 'artifacts' / task_id / 'references'
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (identity + '.json')
            if not target.exists():
                temporary = target.with_suffix('.tmp')
                temporary.write_text(dumps(value), encoding='utf-8')
                temporary.replace(target)
            task = self.service.tasks.repo.get(task_id)
            if not any(a.get('artifact_id') == identity for a in task['artifacts']):
                entry = {**value, 'artifact_id': identity,
                    'path': target.relative_to(self.service.root).as_posix(), 'purpose': '业务成果引用声明'}
                self.service.tasks.repo.update(task, artifacts=[*task['artifacts'], entry])
            return value | {'artifact_id': identity}

    def transfer(self, caller, parent_id, ids):
        if len(ids) > 100 or len(set(ids)) != len(ids):
            raise Problem('PARAMETER_INVALID', '成果引用最多100条且不得重复')
        return [self.accessible(caller, parent_id, id) | {'artifact_id': id} for id in ids]
