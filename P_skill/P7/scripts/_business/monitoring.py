"""Platform-owned job attribution. Acquisition services never receive work permits."""
from .store import seconds, digest, public


def current(store, config, db):
    value = store.get(db, 'monitoring_scope', 'current', False)
    if value:
        return {k:v for k,v in value.items() if k!='consumer_id'}
    # Read-only compatibility for existing administrator-provisioned jobs.
    return {'start_time': config['start_time'], 'end_time': None,
            'person_ids': sorted({r['subject_id'] for r in config['rules'] if r['source'] != 'sensor'}),
            'sensor_ids': sorted({r['subject_id'] for r in config['rules'] if r['source'] == 'sensor'}),
            'area_ids': [],
            'basis': 'legacy_admin_rule_configuration', 'version': 0}


def configure(store, value):
    allowed = {'start_time', 'end_time', 'person_ids', 'sensor_ids', 'area_ids', 'basis'}
    if set(value) - allowed or not isinstance(value.get('basis'), str) or not value['basis'].strip():
        raise ValueError('MONITORING_SCOPE_BASIS_REQUIRED')
    if not isinstance(value.get('start_time'), str) or (value.get('end_time') is not None and not isinstance(value['end_time'], str)):
        raise ValueError('MONITORING_TIME_INVALID')
    seconds(value['start_time'])
    if value.get('end_time') and seconds(value['end_time']) <= seconds(value['start_time']):
        raise ValueError('MONITORING_TIME_INVALID')
    for field in ('person_ids', 'sensor_ids', 'area_ids'):
        if not isinstance(value.get(field, []), list) or any(not isinstance(x, str) or not x for x in value.get(field, [])):
            raise ValueError('MONITORING_SCOPE_INVALID')
    if not value.get('person_ids') and not value.get('sensor_ids'):
        raise ValueError('MONITORING_SUBJECTS_REQUIRED')
    with store.db() as db:
        job = store.get(db, 'job', 'state')
        if job['status'] == 'closed':
            raise ValueError('JOB_ALREADY_CLOSED')
        if any(r['status'] != 'completed' for r in store.rows(db, 'window')):
            raise ValueError('MONITORING_WINDOWS_UNFINISHED')
        config = store.get(db, 'config', 'job')
        old = store.get(db, 'monitoring_scope', 'current', False)
        row = {**value, 'end_time': value.get('end_time')}
        if old and {k: old.get(k) for k in row} != row:
            raise ValueError('MONITORING_SCOPE_IMMUTABLE_CREATE_NEW_JOB')
        if not old:
            row = store.put(db, 'monitoring_scope', 'current', row)
        else:
            row = old
        # Old source_closed sealed a simulated playback, not the physical source.
        if 'monitoring_closed' not in job:
            legacy = {k: job.get(k) for k in ('source_closed', 'source_cutoff', 'source_end_receipt') if k in job}
            job = {k: v for k, v in job.items() if k not in legacy}
            store.put(db, 'job', 'state', {**job, 'monitoring_closed': False,
                                          'legacy_source_seal': legacy})
            store.audit(db, 'migrate_source_seal', 'state', {'basis': value['basis']})
        store.audit(db, 'configure_monitoring_scope', 'current', {'basis': value['basis'], 'scope_version': row['version']})
        return public(row)


def project(window, scope, rules):
    """Apply administrator attribution to a source snapshot; preserve unknown gaps."""
    start = max(seconds(window['time_range'][0]), seconds(scope['start_time']))
    end = min(seconds(window['time_range'][1]), seconds(scope['end_time'])) if scope.get('end_time') else seconds(window['time_range'][1])
    if start >= end:
        raise ValueError('WINDOW_OUTSIDE_MONITORING_SCOPE')
    from .store import iso
    result = {**window, 'time_range': [iso(start), iso(end)]}
    people, sensors, areas = (set(scope.get(k, [])) for k in ('person_ids', 'sensor_ids', 'area_ids'))
    positions = [x for x in window.get('position', []) if x['data']['person_id'] in people
                 and start <= seconds(x['data']['observed_at']) < end]
    result['position'] = positions
    result['sensor'] = [x for x in window.get('sensor', []) if x['data']['sensor_id'] in sensors
                        and start <= seconds(x['data']['observed_at']) < end]
    result['ppe'] = [x for x in window.get('ppe', []) if x['data']['person_id'] in people
                     and seconds(x['data']['time_range'][1]) > start and seconds(x['data']['time_range'][0]) < end]
    result['persons'] = [x for x in window.get('persons', []) if x['person_id'] in people]
    result['sensors'] = [x for x in window.get('sensors', []) if x['sensor_id'] in sensors]
    result['areas'] = window.get('areas', [])
    selected_rules = [r for r in rules if r['subject_id'] in (sensors if r['source'] == 'sensor' else people)]
    attribution = {k: scope.get(k) for k in ('start_time', 'end_time', 'person_ids', 'sensor_ids', 'area_ids', 'basis', 'version')}
    attribution['decided_by'] = 'platform_business_scope'
    attribution['area_match_policy'] = 'observe_all_positions_of_assigned_people' 
    if areas:
        attribution['limitations'] = ['Position points support interval association, not continuous location coverage']
    return result, selected_rules, attribution
