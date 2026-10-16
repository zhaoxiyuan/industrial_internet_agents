"""Participant policies. Registered means a trusted Feishu directory, never public access."""
from .store import context_store,public


def resolve_policy(value, directory):
    if value == 'registered':
        if not directory or not directory.get('connection_id') or not directory.get('user_ids'):
            raise ValueError('REGISTERED_DIRECTORY_REQUIRED')
        return sorted(set(directory['user_ids'])), {'mode':'registered','connection_id':directory['connection_id']}
    if not isinstance(value,list) or any(not isinstance(x,str) or not x.strip() or x=='*' for x in value) or len(set(value))!=len(value):
        raise ValueError('PARTICIPANTS_INVALID')
    return value, {'mode':'specified'}


def role_ids(config, role, directory=None):
    policy=config.get('participant_policy',{}).get(role,{'mode':'specified'})
    if policy['mode']=='registered':
        if not directory or directory.get('connection_id')!=policy['connection_id']:
            return []
        return directory.get('user_ids',[])
    return config.get(role,[])


def configure_participants(arguments,context):
    job,_=context_store(context,'main')
    if not arguments.get('reason','').strip():raise ValueError('PARTICIPANT_REASON_REQUIRED')
    roles=[role for role in ('operators','reviewers') if role in arguments]
    if not roles:raise ValueError('PARTICIPANT_ROLE_REQUIRED')
    resolved={role:resolve_policy(arguments[role],context.get('participant_directory')) for role in roles}
    with job.db() as db:
        state=job.get(db,'job','state')
        if state['status']=='closed':raise ValueError('JOB_ALREADY_CLOSED')
        cfg=job.get(db,'config','job');policy=dict(cfg.get('participant_policy',{}))
        before={role:{'ids':cfg.get(role,[]),'policy':policy.get(role,{'mode':'specified'})} for role in roles}
        for role,(ids,rule) in resolved.items():cfg[role]=ids;policy[role]=rule
        cfg['participant_policy']=policy
        job.put(db,'config','job',cfg)
        job.audit(db,'configure_participants','job',{'actor':context['agent_id'],'run_id':context['run_id'],'reason':arguments['reason'],'before':before,'after':{role:{'ids':cfg[role],'policy':policy[role]} for role in roles}})
    return {'job_id':job.job_id,'participant_policy':policy,'operators':cfg.get('operators',[]),'reviewers':cfg.get('reviewers',[]),'scope':'registered is current connection directory; empty explicit list denies all'}
