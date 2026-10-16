"""P8/P9 business tools. No Feishu transport or model-writable delivery/human votes."""
from .store import context_store, public, digest, stamp, seconds
from .p7 import finish_failure


def claim_risk_report(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        store.expire(db, 'event_notification')
        pending = [r for r in store.rows(db, 'event_notification') if r['status']=='pending']
        if pending:
            return store.claim(db, 'event_notification', pending[0], context['agent_id'], arguments.get('lease_seconds',600))
        store.expire(db, 'report')
        candidates = [r for r in store.rows(db, 'report') if r['status'] == 'pending']
        if not candidates:
            return {'status': 'no_work'}
        return store.claim(db, 'report', candidates[0], context['agent_id'], arguments.get('lease_seconds', 600))


def initialize_disposition(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        kind = arguments['claim']['kind']
        if kind not in ('report','event_notification'):
            raise ValueError('CLAIM_KIND_INVALID')
        claim = store.lease(db, arguments['claim'], context['agent_id'], kind, completed=True)
        event_id = claim['event_id'] if kind=='event_notification' else claim['payload']['event_id']
        report_id = digest([store.job_id,event_id,'final'])[:32]
        existing = store.get(db,'disposition',report_id,False)
        if claim['status']=='completed':
            return public(existing)
        event = store.get(db,'event',event_id)
        report = store.get(db,'report',report_id,False)
        if not report or not report['payload'].get('is_final',True):
            report = store.put(db,'report',report_id,{**(report or {}),'status':'completed',
                'payload':{'report_id':report_id,'job_id':store.job_id,'event_id':event_id,
                    'summary':event['basis'],'event':public(event),'is_final':False}})
        if not existing:
            existing={'report_id':report_id,'event_id':event_id,'status':'open','accepted_by':None,
                'material_version':0,'materials':[],'opinion':None,'human_review':None,
                'delivery':{'status':'pending'},'created_at':stamp()}
        # Updating risk observations must preserve human decisions/materials/delivery.
        if existing.get('event_version') != event['version']:
            row=store.put(db,'disposition',report_id,{**existing,'risk_level':existing['risk_level'] if existing.get('risk_level_override') else event['risk_level'],
                'event_version':event['version'],'event_snapshot':public(event),'risk_state':event['state']})
            store.put(db,'card_intent',report_id,{'status':'pending','disposition_version':row['version'],
                'event_version':event['version']})
        else:
            row=existing
        store.put(db,kind,claim['id'],{**claim,'status':'completed','consumed_event_version':event['version']})
        if report['status']!='completed':
            store.put(db,'report',report_id,{**report,'status':'completed'})
        store.audit(db,'synchronize_event_disposition',report_id,{'run_id':context['run_id'],'event_version':event['version']})
        return public(row)


def read_review_snapshot(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        row = store.get(db, 'disposition', arguments['report_id'])
        if row['status'] != 'materials_in_audit':
            raise ValueError('MATERIALS_NOT_READY')
        snapshot = {'report': store.get(db, 'report', row['id'])['payload'], 'material_version': row['material_version'],
                    'materials': row['materials'], 'disposition_version': row['version']}
        token = digest(snapshot)
        store.put(db, 'review_snapshot', token, {'report_id': row['id'], 'actor': context['agent_id'],
                  'run_id': context['run_id'], 'material_version': row['material_version'], 'snapshot': snapshot})
        return {'snapshot_id': token, **snapshot}


def read_material(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        snap = store.get(db, 'review_snapshot', arguments['snapshot_id'])
        if snap['actor'] != context['agent_id'] or snap['run_id'] != context['run_id']:
            raise ValueError('SNAPSHOT_FORBIDDEN')
        material = next((m for m in snap['snapshot']['materials'] if m['id'] == arguments['material_id']), None)
        if not material:
            raise ValueError('MATERIAL_FORBIDDEN')
    path = (store.root / material['path']).resolve()
    if not path.is_relative_to(store.root):
        raise ValueError('PATH_FORBIDDEN')
    data = path.read_bytes()
    import hashlib
    if hashlib.sha256(data).hexdigest() != material['sha256']:
        raise ValueError('MATERIAL_CHANGED')
    try:
        content = data.decode('utf-8') if material['media_type'] == 'text/plain' and len(data) <= 32000 else None
    except UnicodeError:
        content = None
    with store.db() as db:
        store.put(db, 'material_read', digest([context['run_id'], snap['id'], material['id']]), {'snapshot_id': snap['id'], 'material_id': material['id'], 'run_id': context['run_id'], 'readable': content is not None})
    return {'material_id': material['id'], 'content': content, 'readable': content is not None,
            'limitation': None if content is not None else 'Binary material needs separately bound vision/document reader; do not infer contents.'}


def save_review_opinion(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        snap = store.get(db, 'review_snapshot', arguments['snapshot_id'])
        if snap['actor'] != context['agent_id'] or snap['run_id'] != context['run_id']:
            raise ValueError('SNAPSHOT_FORBIDDEN')
        row = store.get(db, 'disposition', snap['report_id'])
        opinion = {k: arguments[k] for k in ('decision', 'reason', 'limitations')}
        if row.get('opinion') == opinion and row.get('opinion_snapshot') == snap['id']:
            return public(row)
        if row['status'] != 'materials_in_audit' or row['material_version'] != snap['material_version']:
            raise ValueError('MATERIAL_VERSION_CONFLICT')
        read_rows = [r for r in store.rows(db, 'material_read') if r['snapshot_id'] == snap['id'] and r['run_id'] == context['run_id']]
        reads = {r['material_id'] for r in read_rows}
        if reads != {m['id'] for m in row['materials']}:
            raise ValueError('READ_MATERIALS_FIRST')
        if any(not r['readable'] for r in read_rows) and opinion['decision'] != 'unknown':
            raise ValueError('UNREADABLE_MATERIAL_REQUIRES_UNKNOWN')
        row = store.put(db, 'disposition', row['id'], {**row, 'status': 'waiting_human_review', 'opinion': opinion, 'opinion_snapshot': snap['id']})
        store.put(db, 'card_intent', row['id'], {'status': 'pending', 'disposition_version': row['version']})
        store.audit(db, 'model_review_opinion', row['id'], {'snapshot_id': snap['id'], **opinion})
        return public(row)


def read_disposition(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        return public(store.get(db, 'disposition', arguments['report_id']))


def fail_risk_report(arguments, context):
    if not arguments.get('claim'):
        results=[finish_failure(arguments,context,'p8p9',kind) for kind in ('report','event_notification')]
        return {'status':'cleanup_complete','released_count':sum(x['released_count'] for x in results)}
    kind=arguments['claim']['kind']
    if kind not in ('report','event_notification'):raise ValueError('CLAIM_KIND_INVALID')
    return finish_failure(arguments, context, 'p8p9', kind)


def closable(store, db):
    job = store.get(db, 'job', 'state')
    config = store.get(db, 'config', 'job')
    blockers = []
    if not job.get('monitoring_closed', False) or not job.get('monitoring_cutoff') or seconds(job['p7_through']) != seconds(job['monitoring_cutoff']):
        blockers.append('job_monitoring_not_fully_consumed')
    for kind in ('window', 'description', 'report', 'event_notification'):
        if any(r['status'] != 'completed' for r in store.rows(db, kind)):
            blockers.append(kind + '_unfinished')
    for event in store.rows(db, 'event'):
        if event['state'] != 'closed' or not event.get('report_id') or not (store.get(db,'report',event['report_id'],False) or {}).get('payload',{}).get('is_final',False):
            blockers.append('event_not_reported:' + event['id'])
    for row in store.rows(db, 'disposition'):
        delivery = row['delivery']['status']
        accepted = delivery == 'delivered' or (delivery == 'simulated' and config.get('test_mode') is True)
        if row['status'] != 'closed' or not accepted:
            blockers.append('disposition_not_closed_or_delivered:' + row['id'])
    return {'closable': not blockers, 'blockers': blockers, 'job_status': job['status']}


def verify_job_closable(arguments, context):
    store, _ = context_store(context, 'p8p9')
    with store.db() as db:
        return closable(store, db)


def apply_human_action(store, callback_id, actor, report_id, version, action, fields, participant_directory=None):
    """Only called by authenticated platform callbacks/admin fixture, NEVER a model tool."""
    config = store.config()
    with store.db() as db:
        fingerprint = digest([actor, report_id, version, action, fields])
        old = store.get(db, 'human_callback', callback_id, False)
        if old:
            if old['fingerprint'] != fingerprint:
                raise ValueError('CALLBACK_ID_CONFLICT')
            return old['receipt']
        row = store.get(db, 'disposition', report_id)
        if row['version'] != version:
            raise ValueError('CARD_VERSION_CONFLICT')
        from .participants import role_ids
        operators = role_ids(config, 'operators', participant_directory)
        reviewers = role_ids(config, 'reviewers', participant_directory)
        if action == 'acknowledge_disposition':
            if actor not in operators or row['status'] != 'open':
                raise ValueError('ACTION_FORBIDDEN')
            row.update(status='acknowledged', accepted_by=actor)
        elif action == 'relinquish_job':
            if actor != row['accepted_by'] or row['status'] not in ('acknowledged', 'rectifying') or row.get('relinquish_count', 0) >= 2:
                raise ValueError('ACTION_FORBIDDEN')
            if len(fields.get('reason', '').strip()) < 10:
                raise ValueError('COMMENT_TOO_SHORT')
            row.update(status='open', accepted_by=None, relinquish_count=row.get('relinquish_count', 0)+1)
        elif action == 'submit_rectification_materials':
            if actor != row['accepted_by'] or row['status'] not in ('acknowledged', 'rectifying'):
                raise ValueError('ACTION_FORBIDDEN')
            ids = fields.get('material_ids', [])
            if not ids:
                raise ValueError('MATERIAL_REQUIRED')
            materials = [store.get(db, 'upload', mid) for mid in ids]
            if any(m['actor'] != actor or m['report_id'] != report_id or m['version_at_issue'] != version or m.get('used') for m in materials):
                raise ValueError('UPLOAD_TOKEN_INVALID')
            for m in materials:
                store.put(db, 'upload', m['id'], {**m, 'used': True})
            row.update(status='materials_in_audit', materials=materials, review_text=fields.get('review_text', ''), material_version=row['material_version']+1, opinion=None, human_review=None)
        elif action == 'record_closure_review':
            if actor not in reviewers or row['status'] != 'waiting_human_review':
                raise ValueError('ACTION_FORBIDDEN')
            approved = fields.get('approved') is True
            comment = fields.get('approved_comment' if approved else 'rejection_reason', '').strip()
            if len(comment) < 10:
                raise ValueError('COMMENT_TOO_SHORT')
            approved = approved and '驳回' not in comment
            row.update(status='closed' if approved else 'rectifying', human_review={'actor': actor, 'approved': approved,
                        'comment': comment, 'material_version': row['material_version'], 'at': stamp()})
        elif action in ('escalate_risk', 'downgrade_risk'):
            if actor not in reviewers or row['status'] == 'closed' or len(fields.get('reason', '').strip()) < 10:
                raise ValueError('ACTION_FORBIDDEN')
            level = fields.get('level')
            if type(level) is not int or not 0 <= level <= 5 or (action == 'escalate_risk' and level <= row['risk_level']) or (action == 'downgrade_risk' and level >= row['risk_level']):
                raise ValueError('RISK_LEVEL_INVALID')
            row['risk_level'] = level
            row['risk_level_override'] = True
        else:
            raise ValueError('ACTION_UNSUPPORTED')
        row = store.put(db, 'disposition', report_id, row, expected=version)
        import time
        store.put(db, 'card_intent', row['id'], {'status': 'pending', 'disposition_version': row['version'], 'not_before': time.time()+.5})
        receipt = {'status': row['status'], 'report_id': report_id, 'version': row['version']}
        store.put(db, 'human_callback', callback_id, {'fingerprint': fingerprint, 'receipt': receipt})
        store.audit(db, 'human_'+action, report_id, {'actor': actor, 'fields': fields})
        return receipt
