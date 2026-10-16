"""Main's business status/recovery/data handoff. Scheduling is a platform grant."""
import re
from .store import context_store, public, digest, atomic, seconds, stamp
from .disposition import closable


def query_pipeline(arguments, context):
    if context.get('source_id'):
        from .work_permits import current_permit
        permit=current_permit(context)
        if permit['status']!='bound':
            source_store,source_cfg=main_source(context)
            return {**permit,'agents':source_cfg['agents'],'queues':{},'acquisition':{'status':'bind_work_permit_before_monitoring'},'p1_p5':{'status':'not_implemented'}}
        context={**context,'job_id':permit['job_id']}
    store, config = context_store(context, 'main')
    with store.db() as db:
        for kind in ('window', 'description', 'report', 'event_notification'):
            store.expire(db, kind)
        result = {'job': public(store.get(db, 'job', 'state')), 'agents': config['agents'], 'p1_p5': {'status': 'not_implemented'}, 'p10_access':{'status':'requires_job_entry_configuration'}, 'queues': {}}
        for kind in ('window', 'description', 'report', 'event_notification', 'event', 'disposition'):
            rows = store.rows(db, kind)
            result['queues'][kind] = [{k: r.get(k) for k in ('id', 'status', 'state', 'publish_state', 'time_range', 'report_id', 'error', 'delivery', 'event_id', 'event_version', 'risk_state', 'consumed_event_version')} for r in rows]
        from .monitoring import current
        result['monitoring_scope'] = current(store, config, db)
        result['participants'] = {k:config.get(k,{ } if k=='participant_policy' else []) for k in ('operators','reviewers','participant_policy')}
        result['closure'] = closable(store, db)
    with store.db() as db:link=store.get(db,'acquisition_link','current',False)
    if link:
        result['acquisition']=list_time_windows({},context)
    return result


def main_source(context):
    if context.get('source_id'):
        from .acquisition import context_source
        return context_source(context,'main')
    store,_=context_store(context,'main')
    with store.db() as db:link=store.get(db,'acquisition_link','current')
    from .acquisition import context_source
    return context_source({**context,'source_id':link['source_id']},'main')


def retry_failed(arguments, context):
    store, config = context_store(context, 'main')
    kind = arguments['kind']
    if kind not in ('description', 'report', 'event_notification'): raise ValueError('INVALID_RETRY_KIND')
    with store.db() as db:
        store.expire(db, kind)
        row = store.get(db, kind, arguments['record_id'])
        if row['status'] != 'failed' or row.get('attempts', 0) >= config.get('max_attempts', 3):
            raise ValueError('RETRY_NOT_ALLOWED')
        store.put(db, kind, row['id'], {**row, 'status': 'pending', 'error': None})
        store.audit(db, 'retry_'+kind, row['id'], {'reason': arguments['reason'], 'attempts': row.get('attempts', 0)})
    return {'status': 'pending', 'record_id': row['id']}


def reopen_monitoring(arguments, context):
    store, _ = context_store(context, 'main')
    reason = arguments.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('MONITORING_REASON_REQUIRED')
    with store.db() as db:
        job = store.get(db, 'job', 'state')
        if job['status'] == 'closed':
            raise ValueError('JOB_ALREADY_CLOSED')
        job = store.put(db, 'job', 'state', {**job, 'monitoring_closed': False, 'monitoring_reopened_at': stamp()})
        store.audit(db, 'reopen_monitoring', 'state', {'reason': reason})
    return public(job)


def prepare_report_data(arguments, context):
    store, config = context_store(context, 'main')
    erp_id = config.get('erp_job_id', store.job_id)
    if not re.fullmatch(r'\d{17}', erp_id):
        raise ValueError('ERP_JOB_ID_REQUIRED')
    with store.db() as db:
        snapshot = {kind: [public(row) for row in store.rows(db, kind)] for kind in ('description', 'event', 'disposition', 'job')}
        if any(r['publish_state'] != 'ready' for r in snapshot['description']):
            raise ValueError('SOURCE_NOT_READY')
        export_id = digest([config['session_id'], snapshot])[:32]
        relative = 'exports/' + export_id
        root = store.root / relative / 'jobs' / erp_id
        permit = dict(config['work_permit'])
        if config.get('test_mode'):
            permit['is_mock'] = True
        if permit.get('job_id', erp_id) != erp_id:
            raise ValueError('PERMIT_JOB_CONFLICT')
        atomic(root / 'work_permit.json', permit)
        for row in snapshot['description']:
            p6 = row['payload']
            events = [{**e, 'wall_time': e['first_seen'], 'person': {'id': e['subject_id']}} for e in p6['events']]
            atomic(root / 'P6' / ('raw_event_' + row['id'] + '.json'), {**p6, 'events': events, 'work_permit': permit})
        for event in snapshot['event']:
            atomic(root / 'P7' / ('a6_' + event['id'] + '.json'), {'a6_event_id': event['id'], 'event_type': event['risk_item'],
               'first_seen': event['first_seen'], 'last_seen': event['last_seen'], 'risk_level': event['risk_level'],
               'risk_basis': event['basis'], 'suggestions': event['advice'], 'involved_persons': [event['subject_id']],
               'reasoning': 'P7真实研判；' + ('模拟来源数据' if config.get('test_mode') else '业务来源数据'), 'timestamp': event.get('closed_at', event['last_seen']),
               'occurrence_state': event['state'], 'source_record_ids': event['record_ids']})
        by_event = {r['event_id']: r for r in snapshot['disposition']}
        closure = {'job_id': erp_id, 'job_status': snapshot['job'][0]['status'], 'events': [],
                   'review': {'last_comment': '逐事件复核见 source-lineage.json；不合并为全作业人工结论。'},
                   'materials': {}, 'job_status_history': [], 'is_mock': bool(config.get('test_mode'))}
        for event in snapshot['event']:
            disposition = by_event.get(event['id'], {})
            status = disposition.get('status', 'open')
            closure['events'].append({'risk_event_id': event['id'], 'event_type': event['risk_item'], 'risk_level': event['risk_level'],
              'event_status': 'disposed' if status == 'closed' else 'pending' if status == 'open' else 'disposing',
              'first_seen': event['first_seen'], 'last_seen': event['last_seen'], 'risk_basis': event['basis'], 'suggestions': event['advice']})
        atomic(root / 'closure_state.json', closure)
        manifest = {'export_id': export_id, 'job_id': store.job_id, 'erp_job_id': erp_id, 'source_versions': snapshot,
                    'contains_mock': bool(config.get('test_mode')), 'scope_gaps': ['P1-P5 not implemented', 'Per-event review details retained in lineage, not flattened into original job review'],
                    'data_root': relative, 'request_id': export_id, 'created_from': 'P6_P7.sqlite3'}
        atomic(root / 'source-lineage.json', manifest)
        receipt = {'export_id': export_id, 'erp_job_id': erp_id, 'data_root': relative, 'request_id': export_id,
                   'contains_mock': bool(config.get('test_mode')), 'scope_gaps': manifest['scope_gaps']}
        store.put(db, 'export', export_id, receipt)
        return receipt


def confirm_job_closed(store, actor, reason, *, human_override=False, participant_directory=None):
    """Called only by authenticated human API; never exposed as a model tool."""
    from .participants import role_ids
    from .acquisition import source_store
    with store.db() as db:
        cfg=store.get(db,'config','job')
        if actor not in role_ids(cfg,'reviewers',participant_directory) or not reason.strip():
            raise ValueError('HUMAN_CONFIRMATION_REQUIRED')
        state=store.get(db,'job','state')
        if state['status']=='closed':return public(state)
        check=closable(store,db)
        if not human_override and not check['closable']:raise ValueError('JOB_NOT_CLOSABLE')
        # Preserve unresolved business facts; invalidate leases and pending delivery only.
        for kind in ('p6_window','description','report','event_notification'):
            for row in store.rows(db,kind):
                if row.get('status')=='processing':store.put(db,kind,row['id'],{**row,'status':'failed','error':'HUMAN_CLOSED_JOB','expiry':0})
        for row in store.rows(db,'card_intent'):
            if row.get('status')=='pending':store.put(db,'card_intent',row['id'],{**row,'status':'cancelled','reason':'HUMAN_CLOSED_JOB'})
        link=store.get(db,'acquisition_link','current',False)
        if link:
            source=source_store_without_init(store,link['source_id'])
            active=source.get(db,'active_job','current',False)
            if active and active.get('job_id')==store.job_id:
                source.put(db,'active_job','current',{'job_id':None,'released_job_id':store.job_id})
                scfg=source.get(db,'config','source');source.put(db,'config','source',{**scfg,'allowed_job_ids':[]})
        result=store.put(db,'job','state',{**state,'status':'closed','monitoring_closed':True,
            'closed_by':actor,'closed_at':stamp(),'close_reason':reason,'closure_mode':'human_override' if human_override else 'normal',
            'unresolved_at_close':check['blockers']})
        store.audit(db,'human_job_close','state',{'actor':actor,'reason':reason,'human_override':human_override,'blockers':check['blockers']})
        return public(result)


def source_store_without_init(store,source_id):
    # Same database transaction: closure and release cannot be separated by a crash.
    from .acquisition import AcquisitionStore
    source=object.__new__(AcquisitionStore)
    source.root,source.path,source.source_id,source.job_id=store.root,store.path,source_id,source_id
    return source


def bind_work_permit(arguments,context):
    from .work_permits import bind_work_permit as bind
    return bind(arguments,context)


def configure_schedule(arguments, context):
    from .timeflow import configure_schedule as implementation
    return implementation(arguments, context)


def list_time_windows(arguments, context):
    from .timeflow import list_time_windows as implementation
    return implementation(arguments, context)


def retry_time_window(arguments, context):
    from .timeflow import retry_time_window as implementation
    return implementation(arguments, context)


def close_time_monitoring(arguments, context):
    from .timeflow import close_time_monitoring as implementation
    return implementation(arguments, context)


def configure_participants(arguments, context):
    from .participants import configure_participants as implementation
    return implementation(arguments, context)
