"""Ordered consumption and persisted occurrence aggregation, separate from disposition."""
import time
from .store import context_store, public, digest, seconds


def notify_event(store, db, event):
    """Latest-version outbox only; never creates a card intent or schedules P8."""
    old = store.get(db, 'event_notification', event['id'], False) or {}
    return store.put(db, 'event_notification', event['id'], {**old,
        'event_id':event['id'], 'event_version':event['version'],
        'status':old['status'] if old.get('status') in ('processing','failed') else 'pending'})


def claim_description(arguments, context):
    store, config = context_store(context, 'p7')
    with store.db() as db:
        time_plan = store.get(db, 'time_schedule', 'current', False)
    time_items = None
    if time_plan:
        from .timeflow import catalog, window_key
        time_items = catalog(store, config)[0]
    with store.db() as db:
        store.expire(db, 'description')
        through = store.get(db, 'job', 'state')['p7_through']
        rows = sorted((r for r in store.rows(db, 'description') if r['status'] != 'completed'), key=lambda r: seconds(r['time_range'][0]))
        windows = sorted((r for r in store.rows(db, 'window') if seconds(r['time_range'][1]) > seconds(through)), key=lambda r: seconds(r['time_range'][0]))
        if time_items is not None:
            future = [i for i in time_items if seconds(i['time_range'][1]) > seconds(through)]
            if future:
                first_time = future[0]
                scheduled = store.get(db, 'p6_window', window_key(store, first_time['time_range']), False)
                if not scheduled or scheduled['status'] != 'completed':
                    return {'status':'blocked','reason':'earlier_p6_window_unfinished','time_range':first_time['time_range'],'p7_through':through}
        if not rows:
            return {'status': 'no_work', 'unreported': [e['id'] for e in store.rows(db, 'event') if e['state'] == 'closed' and not e.get('report_id')]}
        first = rows[0]
        if (first['status'] != 'pending' and not (first['status']=='processing' and first.get('owner_run_id')==context['run_id'])) or seconds(first['time_range'][0]) < seconds(through) or not windows or windows[0]['id'] != first['id'] or windows[0].get('publish_state') != 'ready':
            return {'status': 'blocked', 'record_id': first['id'], 'p7_through': through}
        if seconds(first['time_range'][0]) > seconds(through):
            gap = [through, first['time_range'][0]]
            for event in store.rows(db, 'event'):
                if event['state'] != 'closed':
                    updated = store.put(db, 'event', event['id'], {**event, 'state': 'uncertain', 'normal_since': None,
                              'unknown_intervals': [*event.get('unknown_intervals', []), gap]})
                    notify_event(store, db, updated)
            store.put(db, 'monitoring_gap', digest(gap)[:32], {'time_range': gap, 'state': 'unknown',
                      'reason': 'no_attributed_window', 'next_record_id': first['id']})
            job = store.get(db, 'job', 'state')
            store.put(db, 'job', 'state', {**job, 'p7_through': first['time_range'][0]})
            store.audit(db, 'monitoring_gap', first['id'], {'time_range': gap, 'state': 'unknown'})
        if first['status']=='processing':
            current_claim(store,db,context)
            result={'status':'processing','record':public(first)}
        else:
            result = store.claim(db, 'description', first, context['agent_id'], arguments.get('lease_seconds', 600))
            store.put(db,'p7_current_claim',context['run_id'],{'actor':context['agent_id'],'claim':result.pop('claim')})
        from .compact_view import description_view
        result['record'] = description_view(result['record'])
        result['required_assessment_keys'] = [o['key'] for o in first['payload']['observations'] if any(s['state']=='abnormal' for s in o['segments'])]
        result['next_step'] = '调用complete_assessment，不传claim，工具自动关联本轮当前领取。assessments仅包含required_assessment_keys，每key恰好一项；列表为空就传[]，不添加正常/未知条目。提交成功再领取；参数错误修正本项，不另领或猜事件ID。'
        return {**result, 'current_claim':True, 'active_events': [public(e) for e in store.rows(db, 'event') if e['state'] != 'closed'], 'rules': config['rules']}


def current_claim(store, db, context, completed=False):
    binding=store.get(db,'p7_current_claim',context['run_id'],False)
    if not binding or binding['actor']!=context['agent_id']: raise ValueError('NO_CURRENT_P7_CLAIM')
    row=store.lease(db,binding['claim'],context['agent_id'],'description',completed=completed)
    if row.get('owner_run_id')!=context['run_id']: raise ValueError('P7_CLAIM_RUN_MISMATCH')
    return row


def complete_assessment(arguments, context):
    store, config = context_store(context, 'p7')
    assessment_hash = digest(arguments['assessments'])
    with store.db() as db:
        row = current_claim(store, db, context, completed=True)
        if row['status'] == 'completed':
            if row['assessment_hash'] != assessment_hash:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return row['receipt']
        facts = row['payload']['observations']
        expected = {o['key'] for o in facts if any(s['state'] == 'abnormal' for s in o['segments'])}
        assessments = {a['key']: a for a in arguments['assessments']}
        if set(assessments) != expected or len(assessments) != len(arguments['assessments']):
            raise ValueError('ASSESS_ALL_ABNORMAL_KEYS')
        job = store.get(db, 'job', 'state')
        if seconds(job['p7_through']) != seconds(row['time_range'][0]):
            raise ValueError('PREDECESSOR_NOT_COMPLETE')
        changed = {}
        closed = []
        for observation in facts:
            key = observation['key']
            active = [e for e in store.rows(db, 'event') if e['key'] == key and e['state'] != 'closed']
            if len(active) > 1:
                raise ValueError('MULTIPLE_ACTIVE_OCCURRENCES')
            event = active[0] if active else None
            for segment in observation['segments']:
                left, right = segment['time_range']
                state = segment['state']
                if state == 'abnormal':
                    opinion = assessments[key]
                    if not event:
                        event_id = digest([config['session_id'], key, row['id'], left])[:32]
                        event = {'id': event_id, 'key': key, 'subject_id': observation['subject_id'], 'risk_item': observation['risk_item'],
                                 'source': observation['source'], 'state': 'open', 'first_seen': left,
                                 'risk_level': opinion['risk_level'], 'evidence': [], 'record_ids': []}
                    event.update(state='open', last_seen=right, normal_since=None,
                                 risk_level=max(event['risk_level'], opinion['risk_level']), basis=opinion['basis'], advice=opinion['advice'])
                elif event and state == 'unknown':
                    event.update(state='uncertain', normal_since=None)
                elif event and state == 'normal':
                    # Any unexplained gap breaks consecutive recovery, including missing entire windows.
                    prior = event.get('observed_through')
                    normal_since = event.get('normal_since') if prior and seconds(prior) == seconds(left) else None
                    event['normal_since'] = normal_since or left
                    event['state'] = 'closing'
                    if right != left and seconds(right)-seconds(event['normal_since']) >= observation['recovery_seconds'] and row['payload']['coverage']['complete']:
                        event.update(state='closed', ended_at=event['normal_since'], closed_at=right,
                                     closure_reason='continuous_normal_evidence', recovery_seconds=observation['recovery_seconds'])
                if event:
                    event['observed_through'] = right
                    if row['id'] not in event['record_ids']:
                        event['record_ids'].append(row['id'])
                    event['evidence'].append({'record_id': row['id'], **segment})
                    changed[event['id']] = event.copy()
                    if event['state'] == 'closed':
                        closed.append(event['id'])
                        event = None
        archive = {'record_id': row['id'], 'payload': row['payload'], 'assessments': arguments['assessments'],
                   'changes': list(changed.values()), 'run_id': context['run_id']}
        path = store.archive('P7', [row['id'], assessment_hash], archive)
        for key, event in changed.items():
            updated = store.put(db, 'event', key, event)
            notify_event(store, db, updated)
        receipt = {'status': 'completed', 'record_id': row['id'], 'closed_events': closed, 'changed_events': list(changed), 'artifact_ref': path,
                   'next_step': '仅对closed_events返回的ID逐项read_event→report_closed_event，然后claim_description({})。' if closed else 'closed_events为空，不调用read_event/report_closed_event；下一步立即claim_description({})。'}
        store.put(db, 'description', row['id'], {**row, 'status': 'completed', 'assessment_hash': assessment_hash, 'receipt': receipt})
        store.put(db, 'job', 'state', {**job, 'p7_through': row['time_range'][1]})
        store.audit(db, 'complete_assessment', row['id'], receipt)
        return receipt


def report_closed_event(arguments, context):
    store, _ = context_store(context, 'p7')
    with store.db() as db:
        event = store.get(db, 'event', arguments['event_id'])
        if event['state'] != 'closed' or event.get('closure_reason') != 'continuous_normal_evidence':
            raise ValueError('EVENT_NOT_CLOSED')
        report_id = digest([store.job_id, event['id'], 'final'])[:32]
        old = store.get(db, 'report', report_id, False)
        if old and old['payload'].get('is_final', True):
            if old['payload']['summary'] != arguments['summary']:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return public(old)
        payload = {'report_id': report_id, 'event_id': event['id'], 'job_id': store.job_id,
                   'summary': arguments['summary'], 'event': public(event), 'is_final': True}
        path = store.archive('reports/P7', report_id, payload)
        report = store.put(db, 'report', report_id, {**(old or {}), 'status': 'completed' if store.get(db, 'disposition', report_id, False) else 'pending', 'payload': payload, 'artifact_ref': path})
        updated = store.put(db, 'event', event['id'], {**event, 'report_id': report_id})
        notify_event(store, db, updated)
        store.audit(db, 'report_closed_event', event['id'], {'report_id': report_id})
        return public(report)


def read_event(arguments, context):
    store, _ = context_store(context, 'p7')
    with store.db() as db:
        return public(store.get(db, 'event', arguments['event_id']))


def fail_description(arguments, context):
    return finish_failure(arguments, context, 'p7', 'description')


def finish_failure(arguments, context, role, kind):
    store, _ = context_store(context, role)
    with store.db() as db:
        claim=arguments.get('claim')
        if claim:
            row=store.get(db,kind,claim['id'])
            if row.get('actor')!=context['agent_id'] or row.get('token')!=claim.get('token'):
                raise ValueError('LEASE_INVALID')
            rows=[row]
        else:
            rows=[r for r in store.rows(db,kind) if r.get('owner_run_id')==context['run_id'] and r.get('actor')==context['agent_id']]
        failed=[]
        for row in rows:
            if row['status']!='processing':continue
            store.put(db,kind,row['id'],{**row,'status':'failed','error':arguments['reason'],'expiry':0})
            store.audit(db,'fail_'+kind,row['id'],{'reason':arguments['reason'],'run_id':context['run_id']})
            failed.append(row['id'])
        return {'status':'failed' if failed else 'no_uncommitted_claim','released_count':len(failed)}


def renew_description_lease(arguments, context):
    store, _ = context_store(context, 'p7')
    with store.db() as db:
        row = current_claim(store, db, context)
        row = store.put(db, 'description', row['id'], {**row, 'expiry': time.time()+arguments['lease_seconds']})
        return {'status': row['status'], 'expiry': row['expiry']}
