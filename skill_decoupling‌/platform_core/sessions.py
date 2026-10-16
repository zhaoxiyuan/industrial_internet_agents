"""Conversation metadata and permanent deletion; task journals remain intact."""
from .common import Problem, text
from .task_store import TERMINAL


class Sessions:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def active(self, id):
        session = self.store.get('sessions', id)
        if session.get('deleted_at'):
            raise Problem('SESSION_DELETED', '会话已删除', 410)
        return session

    def create(self, data):
        self.store.get('agents', data.get('agent_id'))
        name = text(data.get('name', '新会话'), '对话名称', 80)
        return self.store.put('sessions', {'agent_id':data['agent_id'], 'name':name})

    def rename(self, id, data):
        if set(data) != {'name'}:
            raise Problem('PARAMETER_INVALID', '重命名只接受name字段')
        with self.service.tasks.lock:
            session = self.active(id)
            return self.store.put('sessions', {**session, 'name':text(data['name'], '对话名称', 80)}, id)

    def delete(self, id):
        with self.service.tasks.lock:
            self.store.get('sessions', id)
            if any(t.get('session_id') == id and (t['status'] not in TERMINAL or t.get('executor_active'))
                   for t in self.service.tasks.repo.list()):
                raise Problem('OPERATION_BUSY', '会话仍在执行，请等待结束后删除', 409)
            with self.store.db() as db:
                db.execute('DELETE FROM messages WHERE session_id=?', (id,))
                db.execute("DELETE FROM entities WHERE kind='sessions' AND id=?", (id,))
            return {'ok':True, 'note':'对话及消息已永久删除，任务及成果仍保留'}
