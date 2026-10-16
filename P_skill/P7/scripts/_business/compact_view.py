"""Model views only: retain measured changes; keep original evidence in storage."""
from copy import deepcopy


def observation_view(observation, *, number=None):
    result = {k: deepcopy(v) for k, v in observation.items() if k != 'segments' and (number is None or k != 'key')}
    if number is not None:
        result['observation'] = number
    runs = []
    for index, segment in enumerate(observation['segments'], 1):
        facts = {k: deepcopy(v) for k, v in segment.items() if k not in ('time_range', 'evidence_refs')}
        previous = runs[-1] if runs else None
        same = previous and all(previous.get(k) == v and type(previous.get(k)) is type(v) for k, v in facts.items())
        same = same and set(previous) - {'time_range','from_sample','to_sample'} == set(facts)
        if same and previous['time_range'][1] == segment['time_range'][0]:
            previous['to_sample'] = index
            previous['time_range'][1] = segment['time_range'][1]
        else:
            runs.append({**facts, 'from_sample':index, 'to_sample':index, 'time_range':list(segment['time_range'])})
    result['segments'] = runs
    result['sample_count'] = len(observation['segments'])
    return result


def description_view(record):
    result = deepcopy(record)
    payload = result.get('payload', {})
    if 'observations' in payload:
        payload['observations'] = [observation_view(o) for o in payload['observations']]
    # Evidence remains on the persisted record and is used by all commit logic.
    evidence = payload.pop('evidence', None)
    if evidence is not None:
        payload['evidence_count'] = len(evidence)
    return result
