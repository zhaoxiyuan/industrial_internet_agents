"""Single-permit time scheduling. Run identity, claims and precision stay in tools."""
import time
from .store import Store, context_store, seconds, digest, iso
from .acquisition import context_source
from .monitoring import current, project
from . import source


def resolve(context, role):
    src, cfg = context_source(context, role)
    with src.db() as db:
        active = src.get(db, 'active_job', 'current', False) or {}
    job_id = context.get('job_id') or active.get('job_id')
    if not job_id or job_id != active.get('job_id') or job_id not in cfg['allowed_job_ids']:
        raise ValueError('WORK_PERMIT_REQUIRED')
    job, config = context_store({**context, 'job_id': job_id}, role)
    return job, config


def settings(job, config, db):
    return job.get(db, 'time_schedule', 'current', False) or {'window_seconds': 10, 'max_parallel': 3}


def configure_schedule(arguments, context):
    job, cfg = resolve(context, 'main')
    n, parallel = arguments['window_seconds'], arguments.get('max_parallel', 3)
    if type(n) is not int or not 1 <= n <= 300 or type(parallel) is not int or not 1 <= parallel <= 16:
        raise ValueError('SCHEDULE_PARAMETERS_INVALID')
    with job.db() as db:
        old = settings(job, cfg, db)
        if old['window_seconds'] != n and job.rows(db, 'p6_window'):
            raise ValueError('WINDOW_SIZE_LOCKED_FOR_CURRENT_JOB')
        job.put(db, 'time_schedule', 'current', {'window_seconds': n, 'max_parallel': parallel})
        job.audit(db, 'configure_time_schedule', 'current', arguments)
    return {'window_seconds': n, 'max_parallel': parallel, 'trigger': 'Main chooses one-shot or platform heartbeat; both share progress'}


def catalog(job, cfg):
    with job.db() as db:
        scope, plan = current(job, cfg, db), settings(job, cfg, db)
        state = job.get(db, 'job', 'state')
    query = {'window_seconds': plan['window_seconds'], 'start_time': scope['start_time'], 'limit': 100}
    bounds = [x for x in (scope.get('end_time'), state.get('monitoring_cutoff') if state.get('monitoring_closed') else None) if x]
    if bounds: query['end_time'] = min(bounds, key=seconds)
    items = []
    while True:
        page = source.call(cfg, '/v4/data/windows', **query)
        items.extend(page['items'])
        if not page['has_more']: break
        query['after'] = page['next_start_time']
    return items, page, scope, plan


def window_key(job, interval):
    return digest([job.job_id, interval])[:32]


def expire(job, db):
    for row in job.rows(db, 'p6_window'):
        if row['status'] == 'processing' and row['expiry'] <= time.time():
            job.put(db, 'p6_window', row['id'], {**row, 'status': 'failed', 'error': 'P6_TASK_LEASE_EXPIRED'})


def list_time_windows(arguments, context):
    job, cfg = resolve(context, 'main')
    items, page, scope, plan = catalog(job, cfg)
    limit = arguments.get('limit', 20)
    if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('LIMIT_INVALID')
    start = seconds(arguments['start_time']) if arguments.get('start_time') else None
    result, through, blocked = [], scope['start_time'], False
    with job.db() as db:
        expire(job, db)
        rows = {r['id']: r for r in job.rows(db, 'p6_window')}
        for item in items:
            row = rows.get(window_key(job, item['time_range']), {})
            state = row.get('status', 'pending')
            if state == 'completed' and not blocked: through = item['time_range'][1]
            else: blocked = True
            if start is not None and seconds(item['time_range'][0]) < start: continue
            if arguments.get('state', 'all') != 'all' and state != arguments['state']: continue
            result.append({**item, 'status': state, 'attempts': row.get('attempts', 0), 'error': row.get('error')})
        running = sum(r['status'] == 'processing' for r in rows.values())
    return {'windows': result[:limit], 'has_more': len(result) > limit,
            'next_start_time': result[limit-1]['time_range'][1] if len(result) > limit else None,
            'continuous_completed_through': through, 'source_through': page['source_through'],
            'running': running, 'available_slots': max(0, plan['max_parallel']-running),
            'pending_count': sum(window_key(job, i['time_range']) not in rows for i in items),
            'failed_count': sum(r['status']=='failed' for r in rows.values()), **plan}


def present(row, cfg):
    if row['status'] == 'completed': return row['receipt']
    from .compact_view import observation_view
    return {'status': row['status'], 'next_step':'按规则判断全部观测项后调用save_current_description(summary,descriptions,limitations)。不要只写总结或重复读窗。descriptions每项含observation/state/explanation；仅变化时加exceptions。', 'time_range': row['time_range'], 'work_permit': cfg['work_permit'],
            'observations': [observation_view(o, number=i+1) for i,o in enumerate(row['observations'])],
            'required_observations': list(range(1,len(row['observations'])+1)),
            'monitoring_data': {'format':'channel_runs_v1','note':'全部监测通道见observations；segments合并连续同值，from_sample/to_sample保留原样本序号。原始PPE/传感器/位置/CV证据按需read_current_evidence，不以合并段数组位置代替样本序号。',
                                'record_counts':{k:len(row['snapshot'].get(k,[])) for k in ('ppe','sensor','position')}},
            'classification_policy': cfg.get('observation_policy',{}),
            'coverage': row['snapshot']['completeness'], 'subjects': {k:row['snapshot'].get(k,[]) for k in ('persons','sensors','areas')}}


def read_current_evidence(arguments, context):
    job, cfg = resolve(context, 'p6')
    with job.db() as db: row = bound_row(job, db, context)
    if arguments.get('source'):
        kind=arguments['source']
        if kind!='cv':return {'source':kind,'time_range':row['time_range'],'items':row['snapshot'].get(kind,[])}
        result=source.call(cfg,'/v2/data/records',source='cv',from_time=row['time_range'][0],to_time=row['time_range'][1],limit=arguments.get('limit',100),**({'cursor':arguments['cursor']} if arguments.get('cursor') else {}))
        # Return only people assigned to this immutable snapshot.
        people={p['person_id'] for p in row['snapshot'].get('persons',[])}
        result['items']=[i for i in result['items'] if i['data'].get('person_id') in people and i['data'].get('ingest_seq',0)<=row['snapshot']['cutoff_sequence']]
        return result
    index = arguments['observation']
    if type(index) is not int or not 1 <= index <= len(row['observations']): raise ValueError('OBSERVATION_NUMBER_INVALID')
    observation = row['observations'][index-1]
    refs = {(ref['table'],ref['id']) for s in observation['segments'] for ref in s['evidence_refs']}
    return {'observation': index, 'time_range': row['time_range'], 'items': [i for name in ('ppe','sensor','position') for i in row['snapshot'].get(name,[]) if (i['record_ref']['table'],i['record_ref']['id']) in refs]}


def bound_row(job, db, context):
    binding = job.get(db, 'p6_run', context['run_id'])
    row = job.get(db, 'p6_window', binding['window'])
    if row['run_id'] != context['run_id'] or row['actor'] != context['agent_id']: raise ValueError('P6_RUN_REPLACED')
    return row


def read_current_window(arguments, context):
    job, cfg = resolve(context, 'p6')
    with job.db() as db:
        previous = job.get(db, 'p6_run', context['run_id'], False)
        if previous: return present(bound_row(job, db, context), cfg)
    items, page, scope, plan = catalog(job, cfg)
    requested = seconds(arguments['start_time']) if arguments.get('start_time') else None
    with job.db() as db:
        expire(job, db)
        state = job.get(db, 'job', 'state')
        if state.get('monitoring_closed') or state['status']=='closed': raise ValueError('JOB_MONITORING_CLOSED')
        previous = job.get(db, 'p6_run', context['run_id'], False)
        if previous: return present(bound_row(job, db, context), cfg)
        rows = {r['id']:r for r in job.rows(db, 'p6_window')}
        if sum(r['status']=='processing' for r in rows.values()) >= plan['max_parallel']: return {'status':'blocked','reason':'concurrency_limit'}
        for item in items:
            if requested is not None and seconds(item['time_range'][0]) != requested: continue
            key = window_key(job, item['time_range']); old = rows.get(key)
            if old and old['status'] != 'retry_ready': continue
            # Snapshot and reservation share the business lock: parallel claims cannot overlap.
            snapshot = source.call(cfg, '/v2/data/window', from_time=item['time_range'][0], to_time=item['time_range'][1])
            projected, rules, attribution = project(snapshot, scope, cfg['rules'])
            row = job.put(db, 'p6_window', key, {'status':'processing','time_range':item['time_range'],
                'snapshot':projected,'observations':source.monitoring_channels(projected,rules,cfg.get('observation_policy',{})),'attribution':attribution,
                'run_id':context['run_id'],'actor':context['agent_id'],'expiry':time.time()+1800,'attempts':(old or {}).get('attempts',0)+1})
            job.put(db, 'p6_run', context['run_id'], {'window':key})
            return present(row, cfg)
    return {'status':'no_work','reason':'no_available_window','source_through':page['source_through']}


def save_current_description(arguments, context):
    job, cfg = resolve(context, 'p6')
    fingerprint = digest(arguments)
    with job.db() as db:
        row = bound_row(job, db, context)
        if row['status']=='completed':
            if row['request_hash'] != fingerprint: raise ValueError('IDEMPOTENCY_CONFLICT')
            return row['receipt']
        if row['status']!='processing' or row['expiry']<=time.time(): raise ValueError('P6_WINDOW_NOT_OWNED')
        state = job.get(db,'job','state')
        if state.get('monitoring_closed') or state['status']=='closed': raise ValueError('JOB_MONITORING_CLOSED')
        classified = source.classify_channels(row['observations'], arguments['descriptions'])
        descriptions = {d['observation']:d['explanation'] for d in arguments['descriptions']}
        evidence = {digest(ref):ref for o in row['observations'] for s in o['segments'] for ref in s['evidence_refs']}
        payload = {'schema_version':5,'record_id':row['id'],'job_id':job.job_id,'source':'P6','run_id':context['run_id'],
            'time_range':row['time_range'],'observations':classified,'coverage':row['snapshot']['completeness'],
            'evidence':list(evidence.values()),'summary':arguments['summary'],'limitations':arguments['limitations'],
            'descriptions':[{'key':row['observations'][i-1]['key'],'explanation':text} for i,text in descriptions.items()],
            'attribution':row['attribution']}
        receipt = {'status':'completed','time_range':row['time_range'],'description_saved':True,'job':cfg['work_permit']['permit_id']}
        job.put(db,'window',row['id'],{'status':'completed','publish_state':'ready','time_range':row['time_range'],'receipt':receipt})
        job.put(db,'description',row['id'],{'status':'pending','publish_state':'ready','time_range':row['time_range'],'payload':payload})
        job.put(db,'p6_window',row['id'],{**row,'status':'completed','request_hash':fingerprint,'receipt':receipt})
        job.audit(db,'save_time_description',row['id'],{'run_id':context['run_id'],'time_range':row['time_range']})
    return receipt


def fail_current_window(arguments, context):
    job,cfg=resolve(context,'p6')
    with job.db() as db:
        row=bound_row(job,db,context)
        if row['status'] in ('completed','failed','retry_ready'): return row.get('receipt',{'status':row['status']})
        job.put(db,'p6_window',row['id'],{**row,'status':'failed','error':arguments['reason']})
    return {'status':'failed','time_range':row['time_range']}


def retry_time_window(arguments,context):
    job,cfg=resolve(context,'main')
    with job.db() as db:
        expire(job,db)
        rows=[r for r in job.rows(db,'p6_window') if seconds(r['time_range'][0])==seconds(arguments['start_time'])]
        if len(rows)!=1 or rows[0]['status']!='failed' or rows[0]['attempts']>=3: raise ValueError('TIME_WINDOW_RETRY_NOT_ALLOWED')
        row=rows[0];job.put(db,'p6_window',row['id'],{**row,'status':'retry_ready'})
        job.audit(db,'retry_time_window',row['id'],{'reason':arguments['reason']})
    return {'status':'retry_ready','time_range':row['time_range']}


def close_time_monitoring(arguments,context):
    """Explicit user stop is immediate; unfinished work remains unfinished."""
    job,cfg=resolve(context,'main')
    if not arguments.get('reason','').strip(): raise ValueError('MONITORING_REASON_REQUIRED')
    with job.db() as db:
        state=job.get(db,'job','state')
        if state.get('monitoring_closed'):
            return {'status':'monitoring_closed','cutoff':state.get('monitoring_cutoff'),'business_dispositions_closed':False}
        cutoff=arguments.get('cutoff') or iso(time.time())
        seconds(cutoff)  # Validate explicit timestamps without requiring a window boundary.
        unfinished=[{'id':r['id'],'status':r['status'],'time_range':r['time_range']}
                    for r in job.rows(db,'p6_window') if r['status']!='completed']
        job.put(db,'job','state',{**state,'monitoring_closed':True,'monitoring_cutoff':cutoff,
            'monitoring_close_reason':arguments['reason'],'monitoring_close_mode':'user_stop',
            'unfinished_windows_at_stop':unfinished})
        job.audit(db,'close_time_monitoring','state',{'cutoff':cutoff,'reason':arguments['reason'],
            'mode':'user_stop','unfinished_windows':unfinished})
    return {'status':'monitoring_closed','cutoff':cutoff,'unfinished_window_count':len(unfinished),
            'business_dispositions_closed':False,
            'next_step':'监测已停止，不再领取或提交P6窗口；停用本票P6心跳。未完成记录保留，不代表作业票关闭或风险处置完成。'}
