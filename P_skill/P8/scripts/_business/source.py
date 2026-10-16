"""Bound mock_player HTTP adapter and evidence-derived observations."""
import json
import operator
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from .store import seconds, iso, digest


def call(config, path, body=None, **query):
    url = config['player_url'].rstrip('/') + path
    if query:
        url += '?' + urlencode(query)
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=15) as response:
        value = json.load(response)
    if value.get('ok') is False:
        raise ValueError('SOURCE_ERROR')
    return value


OPS = {'gt': operator.gt, 'ge': operator.ge, 'lt': operator.lt, 'le': operator.le, 'eq': operator.eq}


def monitoring_channels(window, rules, policy):
    """Enumerate measured channels independently of a risk-rule whitelist."""
    groups = {}
    for source_name in ('ppe', 'sensor', 'position'):
        for item in window.get(source_name, []):
            data = item['data']
            subject = data.get('sensor_id') if source_name == 'sensor' else data.get('person_id')
            fields = sorted(k for k in data if k.endswith('_missing_ratio')) if source_name == 'ppe' else ['value'] if source_name == 'sensor' else ['area_id']
            for field in fields:
                groups.setdefault((source_name, subject, field), []).append(item)
    # Missing entities/channels remain explicit unknown observations.
    for person in window.get('persons', []):
        for field in ('helmet_missing_ratio', 'goggles_missing_ratio', 'suit_missing_ratio'):
            groups.setdefault(('ppe', person['person_id'], field), [])
        groups.setdefault(('position', person['person_id'], 'area_id'), [])
    for sensor in window.get('sensors', []):
        groups.setdefault(('sensor', sensor['sensor_id'], 'value'), [])
    output = []
    ws, we = map(seconds, window['time_range'])
    for (source_name, subject, field), items in sorted(groups.items()):
        hint = next((r for r in rules if r['source']==source_name and r['subject_id']==subject and r['field']==field), None)
        risk = hint['risk_item'] if hint else field
        segments = []
        for item in items:
            data = item['data']
            if source_name == 'ppe': left, right = map(seconds, data['time_range'])
            else:
                left = seconds(data['observed_at'])
                right = left + policy.get('sample_hold_seconds', 0)
            left, right = max(ws,left), min(we,right)
            value=data.get(field)
            segments.append({'time_range':[iso(left),iso(right)],'state':'unassessed' if value is not None else 'unknown','value':value,'evidence_refs':[item['record_ref']]})
        segments.sort(key=lambda x:x['time_range'][0])
        complete=[];cursor=ws
        for segment in segments:
            left,right=map(seconds,segment['time_range'])
            if left>cursor:complete.append({'time_range':[iso(cursor),iso(left)],'state':'unknown','value':None,'evidence_refs':[]})
            complete.append(segment);cursor=max(cursor,right)
        if cursor<we:complete.append({'time_range':[iso(cursor),iso(we)],'state':'unknown','value':None,'evidence_refs':[]})
        output.append({'key':digest([source_name,subject,risk])[:24],'source':source_name,'subject_id':subject,'field':field,'risk_item':risk,
                       'recovery_seconds':(hint or {}).get('recovery_seconds',policy.get('recovery_seconds',2)), 'rule_hint':hint,'segments':complete})
    return output


def classify_channels(observations, decisions):
    """Model classifies every measured channel; tools retain timestamps and evidence."""
    from copy import deepcopy
    if len(decisions)!=len(observations) or {d['observation'] for d in decisions}!=set(range(1,len(observations)+1)):
        raise ValueError('ASSESS_ALL_MONITORING_CHANNELS')
    result=deepcopy(observations)
    for decision in decisions:
        observation=result[decision['observation']-1]
        previous=0
        overrides={}
        for span in decision.get('exceptions',[]):
            left,right=span['from_sample'],span['to_sample']
            if not 1<=left<=right<=len(observation['segments']) or left<=previous:raise ValueError('SAMPLE_RANGE_INVALID')
            previous=right
            overrides.update({i:span['state'] for i in range(left,right+1)})
        for i,segment in enumerate(observation['segments'],1):
            state=overrides.get(i,decision['state'])
            if segment['state']=='unknown':state='unknown'
            segment['state']=state
        observation['model_basis']=decision['explanation']
    return result
