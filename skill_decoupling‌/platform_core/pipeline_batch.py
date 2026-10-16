"""Bounded P6→P7 dispatch; IDs, budgets and dependency wiring stay in code."""
from .common import Problem

SPEC={'description':'仅供用户明确要求的单次批次/窗口验收；持续监测应先建立心跳，不用本工具代替。p6_count=0只派P7，1–3先派P6再按依赖运行P7。固定角色、超时和请求ID；不改票、不建心跳、不发飞书、不关票。每个Main任务仅一批，重复返回同批。返回p7_task_id；仅需等待验收时实际wait_tasks，提交不等于业务完成。',
      'schema':{'type':'object','properties':{'p6_count':{'type':'integer','minimum':0,'maximum':3}},'required':['p6_count'],'additionalProperties':False}}

P6='只处理一个最早窗口：read_current_window({})后按Skill规则判断所有required_observations，实际save_current_description提交summary/descriptions/limitations。只读紧凑观测值，必要时才追溯原始证据。不要用正文模拟调用；保存成功后退出。'
P7='逐份claim_description→complete_assessment→再领取，直到no_work/blocked。严格按required_assessment_keys填写，空列表必须assessments=[]。complete_assessment只传assessments，后台关联当前领取；参数错误修正当前项，不另领。按实际值和阈值核实P6依据，发现矛盾如实说明，不复制错误结论。每次closed_events及时read_event/report_closed_event。一个任务消费多份；不发飞书、不调下游、不关票。'


def run(service,agent_id,args,run_id):
    from .workflow import ensure_open
    from industrial_skills.store import Store
    count=args['p6_count']
    if type(count) is not int or not 0<=count<=3:
        raise Problem('PARAMETER_INVALID','p6_count须为0–3整数')
    with service.tasks.lock:
        parent=service.tasks.repo.get(run_id)
        if parent['executor_agent_id']!=agent_id or parent['status']!='running':
            raise Problem('TASK_FORBIDDEN','只能在当前Main任务内调度',403)
        scope=parent.get('business_scope')
        if not scope or not scope.get('job_id'):
            # A Main started before binding may acquire its scope during this turn.
            from .workflow import scope_for_agent
            scope=scope_for_agent(service,agent_id)
        if not scope or not scope.get('job_id'):
            raise Problem('WORK_PERMIT_REQUIRED','先绑定作业票',409)
        ensure_open(scope)
        job=Store(scope['storage'],scope['job_id']);cfg=job.config()
        if agent_id not in cfg['agents'].get('main',[]):
            raise Problem('TASK_FORBIDDEN','当前Agent不是本票Main',403)
        targets={}
        for role in ('p6','p7'):
            ids=cfg['agents'].get(role,[])
            if len(ids)!=1:raise Problem('PIPELINE_ROLE_AMBIGUOUS','P6/P7各需唯一绑定Agent',409)
            service.tasks.authorize(agent_id,ids[0],'submit')
            targets[role]=service.store.get('agents',ids[0])
            if not targets[role].get('enabled',True):
                raise Problem('AGENT_DISABLED','下游Agent已停用',409)
            if targets[role].get('timeout',0)<(180 if role=='p6' else 600):
                raise Problem('PIPELINE_TIMEOUT_LIMIT','需要P6上限至少180秒、P7至少600秒；本工具不修改配置',409)
        prior=service.store.get('pipeline_batches',run_id,required=False)
        if prior and prior['p6_count']!=count:
            raise Problem('TASK_IDEMPOTENCY_CONFLICT','同一Main任务批次数量不能改变',409)
        if prior and prior.get('result'):return prior['result']
        prefix='pipeline:'+run_id+':'
        active=[t for t in service.tasks.repo.list() if t['status'] in ('queued','running','waiting') and not t.get('request_id','').startswith(prefix) and (t.get('business_scope') or {}).get('job_id')==scope['job_id'] and (t.get('business_scope') or {}).get('storage')==scope['storage']]
        if any(t['executor_agent_id']==targets['p7']['id'] for t in active):
            return {'status':'blocked','reason':'p7_in_flight'}
        with job.db() as db:plan=job.get(db,'time_schedule','current',False) or {'max_parallel':3}
        occupied=sum(t['executor_agent_id']==targets['p6']['id'] for t in active)
        if count+occupied>min(3,plan['max_parallel'],targets['p6']['concurrency']):
            return {'status':'blocked','reason':'p6_concurrency_limit'}
        service.store.put('pipeline_batches',{'p6_count':count,'job_id':scope['job_id']},run_id)
        tasks=[service.tasks.submit(targets['p6']['id'],P6,input={},parent_id=run_id,request_id=prefix+'p6-'+str(i),timeout_seconds=180) for i in range(count)]
        reviewer=service.tasks.submit(targets['p7']['id'],P7,input={},parent_id=run_id,depends_on=[t['id'] for t in tasks],request_id=prefix+'p7',timeout_seconds=600)
        result={'status':'submitted','p6_task_ids':[t['id'] for t in tasks],'p7_task_id':reviewer['id'],'job_id':scope['job_id'],'next_step':'本工具只提交单次批次，未创建心跳。仅用户要求等待验收时实际调用wait_tasks，使用p7_task_id；仅提交则可结束。汇报按真实业务回执，不用计划文字代替调用。'}
        service.store.put('pipeline_batches',{'p6_count':count,'job_id':scope['job_id'],'result':result},run_id)
        return result
