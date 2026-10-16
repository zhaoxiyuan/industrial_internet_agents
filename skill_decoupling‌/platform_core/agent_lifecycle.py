"""Administrator Agent deletion: atomic definition cleanup, retained execution history."""
import json
import time

from .common import Problem, dumps, now
from .task_store import TERMINAL


def _plan(service, db, agent_id):
    def rows(kind):
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM entities WHERE kind=?', (kind,))]
    agent=next((a for a in rows('agents') if a['id']==agent_id),None)
    if not agent:raise Problem('NOT_FOUND','Agent 不存在',404)
    tasks=[json.loads(r[0]) for r in db.execute('SELECT payload FROM tasks')]
    related=[t for t in tasks if agent_id in (t.get('executor_agent_id'),t.get('caller_agent_id'))]
    task_ids={t['id'] for t in related}
    busy=[t['id'] for t in related if t['status'] not in TERMINAL or t.get('executor_active') or t.get('executor_recovery_until',0)>time.time()]
    holds=[h for h in rows('execution_holds') if h.get('run_id') in task_ids and h.get('state')!='exited']
    plans=[json.loads(r[0]) for r in db.execute('SELECT payload FROM schedules')]
    plans=[p for p in plans if agent_id in (p['agent_id'],p.get('owner_agent_id')) and p['status']!='stopped']
    operations=[o for o in rows('operations') if o.get('resource')=='agent:'+agent_id and o.get('status') in ('queued','running')]
    inbound=[c for c in rows('connections') if c.get('inbound_agent_id')==agent_id]
    scopes=[s for s in rows('risk_card_scopes') if agent_id in s.get('agents',[])]
    blockers=[]
    if busy or holds:blockers.append({'code':'AGENT_BUSY','message':'有未结束任务或未确认退出的执行器，请先等待或处理任务。'})
    if operations:blockers.append({'code':'AGENT_OPERATION_BUSY','message':'模型测试等后台操作尚未结束。'})
    if plans:blockers.append({'code':'AGENT_SCHEDULE_IN_USE','message':'请先停止以此 Agent 为执行者或所有者的心跳计划；暂停不等于停止。'})
    if inbound:blockers.append({'code':'AGENT_INBOUND_IN_USE','message':'请先修改飞书入站连接的默认 Agent。'})
    if scopes:blockers.append({'code':'AGENT_CARD_SCOPE_IN_USE','message':'此 Agent 仍被飞书风险卡片业务范围引用，请先调整范围映射。'})
    bindings=[b for b in rows('bindings') if b['agent_id']==agent_id]
    grants=[g for g in rows('agent_grants') if agent_id in (g['caller_agent_id'],g['target_agent_id'])]
    return agent,bindings,grants,{'agent_id':agent_id,'name':agent['name'],'can_delete':not blockers,'blockers':blockers,
        'bindings':len(bindings),'grants':len(grants),'retained_tasks':len(related),
        'retained_sessions':sum(s['agent_id']==agent_id for s in rows('sessions')),
        'note':'删除 Agent 定义、专属模型密钥及关联绑定/授权；保留历史任务、会话、成果、Skill 和外部服务。'}


def deletion_preview(service, agent_id):
    with service.store.db() as db:
        db.execute('BEGIN')
        return _plan(service,db,agent_id)[3]


def remove_agent(service, agent_id):
    # Same lock order as task submission and schedule tick; recheck inside the write transaction.
    with service.tasks.lock,service.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        agent,bindings,grants,plan=_plan(service,db,agent_id)
        if plan['blockers']:
            first=plan['blockers'][0]
            raise Problem(first['code'],first['message'],409)
        deleted_at=now()
        tombstone={'id':agent_id,'name':agent['name'],'deleted_at':deleted_at,
                   'removed_bindings':len(bindings),'removed_grants':len(grants)}
        db.execute('INSERT INTO entities VALUES(?,?,?,?,?)',('deleted_agents',agent_id,dumps(tombstone),deleted_at,deleted_at))
        for kind,items in [('bindings',bindings),('agent_grants',grants)]:
            db.executemany('DELETE FROM entities WHERE kind=? AND id=?',[(kind,r['id']) for r in items])
        db.execute('DELETE FROM secrets WHERE id=?',(agent['secret_ref'],))
        db.execute("DELETE FROM entities WHERE kind='agents' AND id=?",(agent_id,))
        return {'ok':True,**plan,'deleted_at':deleted_at}
