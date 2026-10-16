"""Model-facing names; internal identity and authorization remain ID based."""
from copy import deepcopy
from .common import Problem

PERMISSIONS = {'submit_task':'submit', 'agent_status':'overview', 'set_heartbeat_schedule':'schedule'}

def target_schema(schema, targets):
    result=deepcopy(schema)
    result['properties'].pop('agent_id')
    result['properties']['agent_name']={'type':'string','enum':sorted(set(t['name'] for t in targets)),
        'description':'实际执行任务或心跳的目标Agent名称，严格从枚举选择；不是创建者名称。'}
    result['required']=['agent_name' if n=='agent_id' else n for n in result.get('required',[])]
    return result

def resolve_target(store, caller, tool, name):
    permission=PERMISSIONS[tool]
    ids={g['target_agent_id'] for g in store.list('agent_grants')
         if g['enabled'] and g['caller_agent_id']==caller and permission in g['permissions']}
    matches=[a for a in store.list('agents') if a['id'] in ids and a['name']==name]
    if not matches: raise Problem('AGENT_NAME_NOT_AUTHORIZED','目标名称不存在、已改名或当前无此操作权限',403)
    if len(matches)!=1: raise Problem('AGENT_NAME_AMBIGUOUS','获授权的Agent名称重复，请管理员修改名称后重试',409)
    return matches[0]['id']
