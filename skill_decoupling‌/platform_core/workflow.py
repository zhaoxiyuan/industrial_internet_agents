"""Trusted business scope, terminal fencing and human job closure."""
from .common import Problem, digest


def scope_for_agent(service,agent_id):
    bindings=[b for b in service.store.list('bindings') if b.get('enabled') and b['agent_id']==agent_id and b.get('source_id') and b['source'].startswith('skill:')]
    scopes=[]
    from industrial_skills.acquisition import source_store
    for b in bindings:
        runtime=service.store.get('skill_runtimes',b['source'][6:])
        mapping=runtime['storage'].get('p6_p7')
        if not mapping:continue
        root=service.store.get('skill_storage',mapping['resource'])['directory']
        source=source_store(root,b['source_id'])
        with source.db() as db:active=source.get(db,'active_job','current',False) or {}
        scope={'source_id':b['source_id'],'storage':root,'job_id':active.get('job_id')}
        if scope not in scopes:scopes.append(scope)
    if len(scopes)>1:raise Problem('WORKFLOW_SCOPE_AMBIGUOUS','存在多个作业来源，无法确定任务范围',409)
    return scopes[0] if scopes else None


def ensure_open(scope):
    if not scope or not scope.get('job_id'):return
    from industrial_skills.store import Store
    job=Store(scope['storage'],scope['job_id'])
    with job.db() as db:state=job.get(db,'job','state')
    if state['status']=='closed':raise Problem('JOB_ALREADY_CLOSED','人工已关闭本作业，停止后续执行',409)


def fence_run(service,task,reason):
    scope=task.get('business_scope')
    if not scope or not scope.get('job_id'):return
    from industrial_skills.store import Store
    job=Store(scope['storage'],scope['job_id'])
    with job.db() as db:
        if job.get(db,'job','state')['status']=='closed':return
        job.put(db,'run_fence',task['id'],{'reason':reason})


def close_job(service,connection,data):
    # This route is administrator/human-only, never registered as an Agent tool.
    from industrial_skills.store import Store
    from industrial_skills.main import confirm_job_closed
    raw=service.store.get('risk_card_scopes',connection)
    requested=data.get('job_id')
    if requested:
        business=Store(raw['storage'],requested)
        with business.db() as db:
            link=business.get(db,'acquisition_link','current')
            if link['source_id']!=raw.get('source_id'):raise Problem('JOB_SCOPE_INVALID','作业不属于此连接',403)
    else:_,business=service.feishu.cards.scope(connection)
    directory={'connection_id':connection,'user_ids':list(service.feishu.require(connection).get('users',{}))}
    if data.get('mode','normal') not in ('normal','human_override'):raise Problem('PARAMETER_INVALID','关闭模式无效')
    with service.tasks.lock:
        try:result=confirm_job_closed(business,data['actor'],data['reason'],human_override=data.get('mode')=='human_override',participant_directory=directory)
        except ValueError as error:
            code=str(error)
            raise Problem(code if code.isupper() else 'JOB_CLOSE_FAILED','常规闭环尚未完成；明确人工关闭可使用human_override' if code=='JOB_NOT_CLOSABLE' else '人工身份或关闭参数无效',409)
        stopped=[]
        for plan in service.schedules.list():
            scope=plan.get('business_scope') or {}
            if scope.get('job_id')==business.job_id and scope.get('storage')==str(business.root):
                service.schedules.control(plan['id'],'stop');stopped.append(plan['id'])
        for task in service.tasks.repo.list():
            scope=task.get('business_scope') or {}
            if scope.get('job_id')==business.job_id and scope.get('storage')==str(business.root) and task['status'] not in ('succeeded','failed','cancelled','interrupted'):
                service.tasks.cancel(task['id'])
    return {**result,'stopped_schedules':stopped,'active_binding_released':True}
