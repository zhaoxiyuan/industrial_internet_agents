"""Explicit user-supplied permits; no default, guessed ticket or test fallback."""
import re
from .store import Store,seconds,digest,public
from .acquisition import context_source

REQUIRED=['job_id','permit_id','start_time','person_ids','sensor_ids','basis']


def current_permit(context):
    source,cfg=context_source(context,'main')
    with source.db() as db:active=source.get(db,'active_job','current',False)
    job_id=context.get('job_id') or (active or {}).get('job_id')
    if not job_id:return {'status':'work_permit_required','required_fields':REQUIRED,'message':'请提供作业票及人员/设备和开始时间；不能使用旧测试票。'}
    job=Store(source.root,job_id)
    with job.db() as db:
        config=job.get(db,'config','job')
        if config.get('retired') or job.get(db,'job','state')['status']=='closed':return {'status':'work_permit_required','required_fields':REQUIRED}
    return {'status':'bound','job_id':job_id,'permit_id':config['work_permit']['permit_id']}


def bind_work_permit(arguments,context):
    source,cfg=context_source(context,'main')
    for key in ('job_id','permit_id'):
        if not isinstance(arguments.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',arguments[key]):raise ValueError('WORK_PERMIT_ID_INVALID')
    if arguments['job_id'] != arguments['permit_id']:raise ValueError('WORK_PERMIT_ID_MISMATCH')
    if not arguments.get('basis','').strip():raise ValueError('WORK_PERMIT_BASIS_REQUIRED')
    seconds(arguments['start_time'])
    if arguments.get('end_time') and seconds(arguments['end_time'])<=seconds(arguments['start_time']):raise ValueError('MONITORING_TIME_INVALID')
    if not arguments['person_ids'] and not arguments['sensor_ids']:raise ValueError('MONITORING_SUBJECTS_REQUIRED')
    for key in ('person_ids','sensor_ids'):
        values=arguments.get(key,[])
        if not isinstance(values,list) or any(not isinstance(x,str) or not x.strip() for x in values) or len(set(values))!=len(values):raise ValueError('WORK_PERMIT_SUBJECTS_INVALID')
    from .participants import resolve_policy
    roles={role:resolve_policy(arguments.get(role,[]),context.get('participant_directory')) for role in ('operators','reviewers')}
    rules=[]
    for rule in cfg['rules']:
        subjects=arguments['sensor_ids'] if rule['source']=='sensor' else arguments['person_ids']
        for subject in subjects:
            if rule['subject_id'] in ('*',subject):rules.append({**rule,'subject_id':subject})
    job=Store(source.root,arguments['job_id']);fingerprint=digest(arguments)
    with source.db() as db:
        old=job.get(db,'config','job',False)
        if old and job.get(db,'job','state')['status']=='closed':raise ValueError('JOB_ALREADY_CLOSED')
        if old and (old.get('retired') or old.get('permit_binding_hash')!=fingerprint):raise ValueError('JOB_ALREADY_REGISTERED_DIFFERENT_PERMIT')
        active=source.get(db,'active_job','current',False)
        if active and active.get('job_id') and active['job_id']!=job.job_id:raise ValueError('WORK_PERMIT_ALREADY_BOUND')
        if not old:
            config={'agents':cfg['agents'],'session_id':digest([source.source_id,job.job_id]),'start_time':arguments['start_time'],
                'work_permit':{'job_id':job.job_id,'permit_id':arguments['permit_id'],'basis':arguments['basis']},
                'rules':rules,'observation_policy':{'sample_hold_seconds':1,'recovery_seconds':2},'player_url':cfg['player_url'],'operators':roles['operators'][0],'reviewers':roles['reviewers'][0],
                'participant_policy':{role:rule for role,(_,rule) in roles.items()},
                'test_mode':False,'permit_binding_hash':fingerprint}
            job.put(db,'config','job',config)
            job.put(db,'job','state',{'status':'active','monitoring_closed':False,'p7_through':arguments['start_time']})
            job.put(db,'monitoring_scope','current',{k:arguments.get(k,[] if k in ('person_ids','sensor_ids','area_ids') else None) for k in ['start_time','end_time','person_ids','sensor_ids','area_ids','basis']})
            job.put(db,'acquisition_link','current',{'source_id':source.source_id})
            job.put(db,'time_schedule','current',{'window_seconds':10,'max_parallel':3})
        source.put(db,'config','source',{**cfg,'allowed_job_ids':[job.job_id]})
        source.put(db,'active_job','current',{'job_id':job.job_id,'permit_id':arguments['permit_id']})
        source.audit(db,'bind_user_work_permit',job.job_id,{'actor':context['agent_id'],'run_id':context['run_id'],'basis':arguments['basis']})
    return {'status':'bound','job_id':job.job_id,'permit_id':arguments['permit_id'],'source_id':source.source_id,'mock_unchanged':True}
