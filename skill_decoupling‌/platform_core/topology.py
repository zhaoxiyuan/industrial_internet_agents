"""Read-only collaboration projection: grants define edges; persisted tasks define activity.

No model calls, endpoint probes, task creation, credentials or checkpoints in this API.
Configuration enablement and current execution are deliberately independent fields.
"""
from collections import Counter, defaultdict

from .common import now
from .task_store import TERMINAL


def snapshot(service):
    agents = service.store.list('agents')
    ids = {a['id'] for a in agents}
    tasks = service.tasks.repo.list()
    by_agent, by_pair = defaultdict(list), defaultdict(list)
    for task in tasks:
        by_agent[task['executor_agent_id']].append(task)
        by_pair[task.get('caller_agent_id'), task['executor_agent_id']].append(task)
    skills = {s['id']: s for s in service.store.list('skills')}
    bindings = defaultdict(list)
    for binding in service.store.list('bindings'):
        bindings[binding['agent_id']].append({k: binding.get(k) for k in ('id', 'source', 'tools', 'enabled')})
    schedules = defaultdict(list)
    for plan in service.schedules.list():
        schedules[plan['agent_id']].append({k: plan.get(k) for k in ('id', 'status', 'interval_seconds', 'next_run_at', 'description', 'overlap_scope', 'timeout_seconds', 'business_scope', 'version', 'source')})
    nodes = []
    for agent in agents:
        rows = by_agent[agent['id']]
        counts = Counter(t['status'] for t in rows)
        draining = sum(bool(t.get('executor_active')) and t['status'] in TERMINAL for t in rows)
        enabled = agent.get('enabled', True)
        configured = bool(agent.get('model') and agent.get('base_url') and service.store.secret(agent['secret_ref']))
        status = ('running' if counts['running'] else 'draining' if draining else
                  'waiting' if counts['waiting'] else 'queued' if counts['queued'] else
                  'disabled' if not enabled else 'unconfigured' if not configured else 'idle')
        recent = sorted(rows, key=lambda t: (t.get('updated', ''), t.get('created', ''), t['id']), reverse=True)[:5]
        nodes.append({
            'id': agent['id'], 'name': agent['name'], 'description': agent.get('description', ''),
            'model': agent.get('model', ''), 'enabled': enabled, 'configured': configured,
            'status': status, 'active': bool(counts['running'] or counts['waiting'] or draining),
            'counts': {k: counts[k] for k in ('running', 'waiting', 'queued', 'succeeded', 'failed', 'interrupted', 'cancelled')},
            'draining': draining, 'concurrency': agent.get('concurrency', 2),
            'accepting': enabled and configured and counts['running'] < agent.get('concurrency', 2),
            'execution_target': agent.get('execution_target', 'platform-local'),
            'skills': [{'id': sid, 'name': skills.get(sid, {}).get('name', sid),
                        'enabled': skills.get(sid, {}).get('enabled', False)} for sid in agent.get('skill_ids', [])],
            'bindings': bindings[agent['id']], 'schedules': schedules[agent['id']],
            'recent_tasks': [{k: t.get(k) for k in ('id', 'description', 'status', 'phase', 'created', 'updated', 'parent_task_id', 'caller_agent_id')} for t in recent],
            'last_activity': recent[0].get('updated') if recent else None,
        })
    edges = []
    for grant in service.store.list('agent_grants'):
        source, target = grant['caller_agent_id'], grant['target_agent_id']
        if source not in ids or target not in ids:
            continue
        rows = by_pair[source, target]
        edges.append({'id': grant['id'], 'source': source, 'target': target,
                      'enabled': grant['enabled'], 'permissions': grant['permissions'],
                      'can_submit': 'submit' in grant['permissions'],
                      'active_tasks': sum(t['status'] in ('running', 'waiting') or bool(t.get('executor_active')) for t in rows),
                      'queued_tasks': sum(t['status'] == 'queued' for t in rows),
                      'total_tasks': len(rows)})
    return {'generated_at': now(), 'nodes': nodes, 'edges': edges,
            'summary': {'agents': len(nodes), 'enabled': sum(n['enabled'] for n in nodes),
                        'active': sum(n['active'] for n in nodes), 'queued': sum(n['counts']['queued'] for n in nodes),
                        'grants': len(edges), 'enabled_grants': sum(e['enabled'] for e in edges)}}
