"""Administrative monitoring scope; source services have no work-ticket authority."""
from .common import Problem


def handle(service, method, parts, data):
    if len(parts)==3 and parts[:2]==['api','acquisition-sources']:
        return acquisition(service,method,parts[2],data)
    if len(parts) != 3 or parts[:2] != ['api', 'monitoring-scopes']:
        return None
    job_id = parts[2]
    if not any(b.get('job_id') == job_id and b.get('enabled') for b in service.store.list('bindings')):
        raise Problem('JOB_NOT_BOUND', '作业未绑定到平台工具', 404)
    resource = service.store.get('skill_storage', 'p6_p7')
    if not resource['enabled']:
        raise Problem('STORAGE_DISABLED', '业务存储已停用', 409)
    from industrial_skills.store import Store, public
    from industrial_skills.monitoring import configure, current
    store = Store(resource['directory'], job_id)
    if method == 'GET':
        with store.db() as db:
            return 200, {'job': public(store.get(db, 'job', 'state')),
                         'monitoring_scope': current(store, store.get(db, 'config', 'job'), db)}
    if method == 'POST':
        # Serializes administrative updates with task submissions; the domain
        # transaction also blocks updates while a business window is unfinished.
        with service.tasks.lock:
            if any(t['executor_agent_id'] in [b['agent_id'] for b in service.store.list('bindings') if b.get('job_id') == job_id]
                   and (t['status'] not in ('succeeded','failed','cancelled','timed_out','interrupted') or t.get('executor_active'))
                   for t in service.tasks.repo.list()):
                raise Problem('OPERATION_BUSY', '相关 Agent 有在途任务，暂不能修改监测范围', 409)
            try:
                return 200, configure(store, data)
            except (ValueError, KeyError) as error:
                raise Problem('MONITORING_SCOPE_INVALID', str(error), 409)
    raise Problem('METHOD_NOT_ALLOWED', '此接口只支持查询和管理员登记监测范围', 405)


def acquisition(service,method,source_id,data):
    from industrial_skills.acquisition import initialize_source,source_store,source_public
    from industrial_skills.store import Store
    resource=service.store.get('skill_storage','p6_p7')
    if not resource['enabled']:raise Problem('STORAGE_DISABLED','业务存储已停用',409)
    with service.tasks.lock:
        if method=='GET':
            return 200,source_public(source_store(resource['directory'],source_id).config())
        if method!='POST':raise Problem('METHOD_NOT_ALLOWED','只支持源查询与管理员登记',405)
        if any(t['status'] not in ('succeeded','failed','cancelled','interrupted') or t.get('executor_active') for t in service.tasks.repo.list()):
            raise Problem('OPERATION_BUSY','有在途任务，暂不能登记采集源',409)
        for ids in data.get('agents',{}).values():
            for actor in ids:service.store.get('agents',actor)
        try:
            result=initialize_source(resource['directory'],source_id,data)
            for job_id in data['allowed_job_ids']:
                business=Store(resource['directory'],job_id)
                with business.db() as db:
                    business.get(db,'config','job')
                    business.put(db,'acquisition_link','current',{'source_id':source_id})
            return 200,result
        except (ValueError,KeyError) as error:raise Problem('SOURCE_CONFIGURATION_INVALID',str(error),409)
