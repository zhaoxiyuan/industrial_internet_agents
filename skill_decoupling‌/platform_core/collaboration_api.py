"""协作与能力管理 REST 路由；返回短请求结果，不在 HTTP 中等待模型。

HTTP 解析、同源限制归 app.py，任务规则归 Tasks，直连契约归 DirectHttp。
CLI 票据绑定 Runtime 身份；管理员命令需要独立令牌且仍受 Agent 绑定限制。
"""
import hmac

from .common import Problem, uid
from .contracts import TASK_TOOLS, envelope
from .artifacts import ARTIFACT_TOOLS


def handle(service, method, parts, data, headers):
    """命中新增路由返回 (HTTP状态, JSON)，否则交给原管理路由处理。"""
    if parts == ['api', 'agent-topology'] and method == 'GET':
        from .topology import snapshot
        return 200, snapshot(service)
    if parts == ['api','schedules']:
        if method == 'GET': return 200, {'schedules':[service.schedules.detail(p['id']) for p in service.schedules.list()]}
        if method == 'POST': return 201, service.schedules.save(data)
    if len(parts) == 3 and parts[:2] == ['api','schedules']:
        if method == 'GET': return 200, service.schedules.detail(parts[2])
        if method == 'PATCH': return 200, service.schedules.save({**data,'schedule_id':parts[2]})
        if method == 'DELETE': return 200, service.schedules.delete(parts[2], data.get('version'))
    if len(parts) == 4 and parts[:2] == ['api','schedules'] and method == 'POST':
        if parts[3] == 'acknowledge-exit':
            return 200, service.schedules.acknowledge_exit(parts[2],data.get('task_id'),data.get('confirmed'))
        return 200, service.schedules.control(parts[2],parts[3])
    if parts == ["api", "docker", "containers"] and method == "GET":
        return 200, {"containers":service.external_cli.containers()}
    if parts == ["api", "tool-providers"]:
        if method == "GET": return 200, {"providers":service.tool_providers.list()}
        if method == "POST": return 201, service.tool_providers.save(data)
    if parts == ['api','tool-providers','openapi'] and method=='POST':
        return 200, service.tool_providers.openapi(data)
    if parts == ["api", "tool-providers", "import"] and method == "POST":
        return 200, service.tool_providers.preview(data)
    if len(parts) >= 3 and parts[:2] == ["api", "tool-providers"]:
        id=parts[2]
        if len(parts)==3:
            if method=="GET": return 200, service.tool_providers.get(id)
            if method=="PATCH": return 200, service.tool_providers.save(data,id)
        if len(parts)==4:
            if parts[3]=="export" and method=="GET": return 200, service.tool_providers.export(id)
            if parts[3]=="discover" and method=="POST":
                return 202, service.operations.submit('tool-discovery',id,lambda:service.tool_providers.discover(id))
            if parts[3]=="confirm" and method=="POST": return 200, service.tool_providers.confirm(id,data)
    if parts == ["api", "cli", "catalog"] and method == "GET":
        if data.get("agent_id"):
            agent = service.store.get("agents", data["agent_id"])
            tools, _ = service.agents.catalog(agent)
            return 200, {"ok": True, "transport": "cli", "commands": [t["function"] for t in tools]}
        commands = service.builtin_tools() + [{"name": n, "description": t["description"],
                                                "inputSchema": t["schema"]} for n, t in {**TASK_TOOLS, **ARTIFACT_TOOLS}.items()]
        return 200, {"ok": True, "transport": "cli", "commands": commands}
    if parts == ["api", "cli", "invoke"] and method == "POST":
        try:
            return 200, service.cli.consume(data.get("ticket", ""), data.get("arguments", {}))
        except Problem as error:
            return error.status, envelope(error=error.as_dict())
    if parts == ["api", "cli", "run"] and method == "POST":
        if not hmac.compare_digest(headers.get("Authorization", ""), "Bearer " + service.cli_token):
            raise Problem("CLI_AUTH_FAILED", "管理员 CLI 需要有效令牌", 401)
        agent = service.store.get("agents", data.get("agent_id"))
        _, mapping = service.agents.catalog(agent)
        item = mapping.get(data.get("command"))
        if item is None:
            candidates = [v for v in mapping.values() if v["tool"] == data.get("command")]
            if len(candidates) == 1:
                item = candidates[0]
        if item is None:
            raise Problem("TOOL_FORBIDDEN", "命令未授权或存在多连接歧义，请使用完整目录名称", 403)
        if item.get("source") in ("tasks", "artifacts"):
            raise Problem("TASK_CONTEXT_REQUIRED", "协作命令须在真实任务 Runtime 中执行", 409)
        run_id = uid()
        try:
            outcome = service.agents.dispatch(agent["id"], item, data.get("arguments", {}), run_id)
        except Problem as error:
            # 手动 CLI 也有审计，不依赖模型 Runtime 才记录业务调用结果。
            outcome = envelope(error=error.as_dict())
        service.store.put("tool_runs", {"agent_id": agent["id"], "run_id": run_id,
            "tool": data.get("command"), "transport": "cli", "audit_source": "administrator-cli", "result": outcome})
        return 200, outcome
    if parts == ["api", "agent-grants"] and method == "POST":
        return 201, service.tasks.grant(data)
    if len(parts) == 3 and parts[:2] == ["api", "agent-grants"]:
        if method == "PATCH":
            return 200, service.tasks.grant(data, parts[2])
        if method == "DELETE":
            service.store.delete("agent_grants", parts[2])
            return 200, {"ok": True}
    if parts == ["api", "http-services"] and method == "POST":
        return 201, service.direct.save(data)
    if parts == ["api", "cli-services"] and method == "POST":
        return 201, service.external_cli.save(data)
    if len(parts) == 3 and parts[:2] == ["api", "cli-services"]:
        if method == "PATCH":
            return 200, service.external_cli.save(data, parts[2])
        if method == "DELETE":
            if any(b["source"] == "cli:" + parts[2] for b in service.store.list("bindings")):
                raise Problem("IN_USE", "请先解除 CLI 能力绑定", 409)
            service.store.delete("cli_services", parts[2])
            return 200, {"ok": True}
    if len(parts) == 3 and parts[:2] == ["api", "http-services"]:
        if method == "PATCH":
            return 200, service.direct.save(data, parts[2])
        if method == "DELETE":
            if any(b["source"] == "http:" + parts[2] for b in service.store.list("bindings")):
                raise Problem("IN_USE", "请先解除直连能力绑定", 409)
            service.store.delete("http_services", parts[2])
            return 200, {"ok": True}
    if parts == ["api", "tasks"]:
        if method == "GET":
            return 200, {"tasks": service.tasks.repo.list()[-100:]}
        if method == "POST":
            task = service.tasks.submit(data.get("agent_id"), data.get("description"), data.get("input"),
                                        request_id=data.get("request_id", ""), timeout_seconds=data.get("timeout_seconds"))
            return 202, task
    if len(parts) == 3 and parts[:2] == ["api", "tasks"] and method == "GET":
        task = service.tasks.repo.get(parts[2])
        return 200, {**task, "events": service.tasks.repo.events(task["id"])}
    if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "cancel" and method == "POST":
        return 200, service.tasks.cancel(parts[2])
    return None
