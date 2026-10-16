"""Job/role/channel interface; schedule identity, versions and presets stay server-side."""
from .common import Problem, digest
from .agent_targets import resolve_target

SPECS={
 'ensure_heartbeat_channel':{'description':'确保本票指定心跳通道存在或更新间隔/描述。已存在复用，不重复创建；暂停/停止计划不会自动恢复。Main仅通道1，P6通道1–3；平台自动配置防重入、首次延迟、超时、input及幂等。需用户持续运行授权。', 'schema':{'type':'object','properties':{'agent_name':{'type':'string'},'channel':{'type':'integer','minimum':1,'maximum':3},'interval_seconds':{'type':'integer','minimum':1,'maximum':86400},'description':{'type':'string','minLength':1,'maxLength':10000}},'required':['agent_name','channel','description'],'additionalProperties':False}},
 'control_heartbeat_channel':{'description':'按本票执行者名称和通道暂停/恢复/停止心跳。channel=all作用于该执行者的全部通道。只影响未来触发，不取消在途任务；停止后仅显式resume可恢复。', 'schema':{'type':'object','properties':{'agent_name':{'type':'string'},'channel':{'enum':[1,2,3,'all']},'action':{'enum':['pause','resume','stop']}},'required':['agent_name','channel','action'],'additionalProperties':False}},
 'get_heartbeat_channels':{'description':'查询本票自己创建的心跳，返回执行者名称、通道、状态、实际参数及最近运行；channels仅含已映射通道；legacy_schedules是未映射旧计划，严禁按列表顺序给旧计划编通道号。', 'schema':{'type':'object','properties':{},'additionalProperties':False}}}

def scope(service,caller,run_id):
    from .workflow import scope_for_agent
    task=service.tasks.repo.get(run_id)
    if task['executor_agent_id']!=caller or task['status']!='running':raise Problem('TASK_CONTEXT_INVALID','无有效运行上下文',403)
    result=task.get('business_scope') or scope_for_agent(service,caller)
    if not result or not result.get('job_id'):raise Problem('WORK_PERMIT_REQUIRED','先绑定作业',409)
    return result

def same_scope(plan,bound):
    return all((plan.get('business_scope') or {}).get(k)==bound.get(k) for k in ('storage','job_id'))

def summary(service,plan,caller,channel=None):
    detail=service.schedules.detail(plan['id'],caller)
    return {'agent_name':service.store.get('agents',plan['agent_id'])['name'], 'channel':channel,
            **{k:detail.get(k) for k in ('status','interval_seconds','timeout_seconds','overlap_scope','overlap_policy','first_run','description')},
            'recent_runs':[{k:r.get(k) for k in ('status','started','finished','duration_seconds','error')} for r in detail.get('recent_runs',[])[:3]]}

def run(service,caller,name,args,run_id):
    from industrial_skills.store import Store
    with service.tasks.lock:
        bound=scope(service,caller,run_id)
        plans=[p for p in service.schedules.list() if p.get('owner_agent_id')==caller and same_scope(p,bound)]
        if name=='get_heartbeat_channels':
            result=[];legacy=[]
            for p in plans:
                tag=service.store.get('heartbeat_channel_refs',p['id'],required=False)
                item=summary(service,p,caller,tag.get('channel') if tag else None)
                if tag:result.append(item)
                else:
                    item.pop('channel',None);legacy.append(item)
            groups={}
            for item in legacy:
                group=groups.setdefault(item['agent_name'],{'agent_name':item['agent_name'],'count':0,'status_counts':{}})
                group['count']+=1
                group['status_counts'][item['status']]=group['status_counts'].get(item['status'],0)+1
            return {'channels':result,'legacy_schedules':{'count':len(legacy),'by_agent':list(groups.values())},
                    'summary':f'本票已映射通道{len(result)}条，未映射旧计划{len(legacy)}条。旧计划没有通道号，未自动修改或迁移。',
                    'notice':'按summary汇报；channels为空时不得声称通道1/2/3已建立。旧计划明细保留在平台心跳管理页。'}
        target=resolve_target(service.store,caller,'set_heartbeat_schedule',args['agent_name'])
        job=Store(bound['storage'],bound['job_id']);cfg=job.config()
        roles=[r for r in ('main','p6') if target in cfg['agents'].get(r,[])]
        if len(roles)!=1:raise Problem('HEARTBEAT_ROLE_INVALID','仅支持本票明确配置的Main/P6角色',409)
        role=roles[0];channel=args['channel']
        if type(channel) is int and not 1<=channel<=(3 if role=='p6' else 1):raise Problem('HEARTBEAT_CHANNEL_INVALID','Main仅1路，P6最多3路',409)
        if name=='control_heartbeat_channel':
            if args['action']=='resume':
                with job.db() as db:state=job.get(db,'job','state')
                if state['status']=='closed' or state.get('monitoring_closed'):raise Problem('JOB_MONITORING_CLOSED','监测已停，不能恢复心跳',409)
            chosen=[]
            for p in plans:
                if p['agent_id']!=target:continue
                tag=service.store.get('heartbeat_channel_refs',p['id'],required=False)
                if channel=='all' or (tag and tag['channel']==channel):chosen.append(p)
            if channel!='all' and len(chosen)!=1:raise Problem('HEARTBEAT_CHANNEL_NOT_FOUND','通道不存在或为未映射旧计划；先查询',409)
            if args['action']=='resume' and role=='p6':
                with job.db() as db:business_plan=job.get(db,'time_schedule','current',False) or {}
                limit=min(3,business_plan.get('max_parallel',3),service.store.get('agents',target).get('concurrency',1))
                active={p['id'] for p in plans if p['agent_id']==target and p['status']=='active'}
                if len(active | {p['id'] for p in chosen})>limit:raise Problem('HEARTBEAT_CAPACITY_LIMIT','恢复后通道数将超过并发上限',409)
            results=[]
            for p in chosen:
                updated=service.schedules.control(p['id'],args['action'],caller)
                tag=service.store.get('heartbeat_channel_refs',p['id'],required=False)
                results.append(summary(service,updated,caller,tag.get('channel') if tag else None))
            return {'channels':results}
        with job.db() as db:state=job.get(db,'job','state')
        if state['status']=='closed' or state.get('monitoring_closed'):raise Problem('JOB_MONITORING_CLOSED','本票监测已停止，不能创建心跳',409)
        with job.db() as db:plan=job.get(db,'time_schedule','current',False) or {}
        capacity=min(3,plan.get('max_parallel',3),service.store.get('agents',target).get('concurrency',1)) if role=='p6' else 1
        key='channel:'+digest([caller,bound['storage'],bound['job_id'],target,channel])[:40]
        matches=[p for p in plans if p['agent_id']==target and p.get('overlap_key')==key]
        if len(matches)>1:raise Problem('HEARTBEAT_CHANNEL_AMBIGUOUS','通道计划重复，需人工检查',409)
        old=matches[0] if matches else None
        if old and not service.store.get('heartbeat_channel_refs',old['id'],required=False):service.store.put('heartbeat_channel_refs',{'channel':channel,'role':role},old['id'])
        if old is None and any(p['agent_id']==target and p['status'] in ('active','paused') and not service.store.get('heartbeat_channel_refs',p['id'],required=False) for p in plans):
            raise Problem('LEGACY_HEARTBEAT_EXISTS','已有旧计划，先查询并明确停止旧计划再建通道，避免重复',409)
        if old and old['status'] in ('stopped','paused'):return {**summary(service,old,caller,channel),'unchanged':True,'reason':'explicit_resume_required'}
        if old is None and sum(p['agent_id']==target and p['status']=='active' for p in plans)>=capacity:raise Problem('HEARTBEAT_CAPACITY_LIMIT','启用通道数达到业务或Agent并发上限',409)
        config={'agent_id':target,'enabled':True,'interval_seconds':args.get('interval_seconds',old['interval_seconds'] if old else (30 if role=='p6' else 180)),
                'description':args['description'],'input':{},'first_run':'after_interval','overlap_scope':'chain' if role=='p6' else 'self',
                'overlap_policy':'skip','overlap_key':key,'timeout_seconds':180 if role=='p6' else 300}
        if old and all(old.get(k)==v for k,v in config.items() if k not in ('agent_id','enabled')):return summary(service,old,caller,channel)
        if old:config.update(schedule_id=old['id'],version=old['version'])
        config['request_id']='channel:'+digest([key,config,old['version'] if old else 0])
        from .workflow import scope_for_agent
        current=scope_for_agent(service,target) or {}
        if not all(current.get(k)==bound.get(k) for k in ('storage','job_id')):raise Problem('HEARTBEAT_SCOPE_CHANGED','目标作业范围已变化，拒绝跨票创建',409)
        result=service.schedules.save(config,caller,run_id)
        service.store.put('heartbeat_channel_refs',{'channel':channel,'role':role},result['id'])
        return summary(service,result,caller,channel)
