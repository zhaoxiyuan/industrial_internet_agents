"""LangChain Tool 目录、绑定与公共业务授权门禁。"""
import re

from .common import Problem, digest, text, uid, url, validate, normalize_tool_values
from .feishu import SCHEMAS, DESCRIPTIONS
from .contracts import TASK_TOOLS, envelope


class Agents:
    """把 Agent 内容配置和获授权的能力目录组合为一次可审计运行。"""

    def __init__(self, service):
        """保留平台对象引用；业务文件、MCP 和凭据均通过各自模块访问。"""
        self.service, self.store = service, service.store

    def public(self, record):
        """只返回模型密钥是否已设置；不把 secret_ref 或密钥发送给前端。"""
        return {k: v for k, v in record.items() if k != "secret_ref"} | {
            "key_configured": bool(self.store.secret(record.get("secret_ref", ""))),
            "heartbeat": self.service.schedules.default(record['id'])}

    def save(self, data, id=None):
        # Serialize definition changes with task submission/deletion in this platform process.
        with self.service.tasks.lock:
            return self._save(data, id)

    def _save(self, data, id=None):
        """允许先创建无模型的 Agent，避免配置页陷入必须先保存才可创建的循环。"""
        old = self.store.get("agents", id) if id else {}
        id = id or uid()
        target = data.get("execution_target", old.get("execution_target", "platform-local"))
        if target != "platform-local":
            raise Problem("EDGE_NOT_IMPLEMENTED", "边缘 Agent 部署仅保留接口", 501)
        skills = data.get("skill_ids", old.get("skill_ids", []))
        if not isinstance(skills, list) or len(set(skills)) != len(skills):
            raise Problem("PARAMETER_INVALID", "Skill 绑定必须为无重复 ID 数组")
        for sid in skills:
            self.store.get("skills", sid)
        base = data.get("base_url", old.get("base_url", ""))
        if base:
            url(base)
        value = {"name": text(data.get("name", old.get("name")), "Agent 名称", 80),
                 "model": data.get("model", old.get("model", "")), "base_url": base,
                 "system_prompt": data.get("system_prompt", old.get("system_prompt", "你是一个助手。工具失败时说明真实结果，不得声称执行成功。")),
                 "skill_ids": skills, "execution_target": target, "secret_ref": "model:" + id,
                 "max_tokens": int(data.get("max_tokens", old.get("max_tokens", 2048)))}
        value.update(model_timeout=int(data.get("model_timeout", old.get("model_timeout", 30))),
                     max_model_calls=int(data.get("max_model_calls", old.get("max_model_calls", 12))))
        if not 10 <= value["model_timeout"] <= 300 or not 1 <= value["max_model_calls"] <= 64:
            raise Problem("PARAMETER_INVALID", "模型超时须10–300秒，调用上限须1–64")
        # 基础配置定义执行契约；仅显式心跳配置会改变计划，修订用于任务快照审计。
        from .common import check_schema
        value.update(description=str(data.get("description", old.get("description", ""))),
                     enabled=bool(data.get("enabled", old.get("enabled", True))),
                     concurrency=int(data.get("concurrency", old.get("concurrency", 2))),
                     timeout=int(data.get("timeout", old.get("timeout", 180))),
                     contract_version=int(data.get("contract_version", old.get("contract_version", 1))),
                     config_version=old.get("config_version", 0) + 1,
                     input_schema=data.get("input_schema", old.get("input_schema", {"type": "object", "properties": {}, "additionalProperties": True})),
                     output_schema=data.get("output_schema", old.get("output_schema")))
        if not 1 <= value["concurrency"] <= 4 or not 10 <= value["timeout"] <= 1800 or value["contract_version"] < 1:
            raise Problem("PARAMETER_INVALID", "并发需为 1–4，超时需为 10–1800 秒，版本为正整数")
        check_schema(value["input_schema"])
        if value["output_schema"] is not None:
            check_schema(value["output_schema"])
        field=data.get('artifact_output_field',old.get('artifact_output_field',''))
        if field and (not isinstance(value['output_schema'],dict) or field not in value['output_schema'].get('properties',{})):
            raise Problem('PARAMETER_INVALID','成果输出绑定字段必须出现在输出契约中')
        value['artifact_output_field']=field
        if not 1 <= value["max_tokens"] <= 32000:
            raise Problem("PARAMETER_INVALID", "max_tokens 超出允许范围")
        heartbeat = None
        if 'heartbeat' in data:
            heartbeat = self.service.schedules.configuration(data['heartbeat'], value)
        if data.get("api_key"):
            self.store.secret(value["secret_ref"], data["api_key"])
        if data.get("clear_key"):
            self.store.secret(value["secret_ref"], "")
        saved = self.store.put('agents', value, id)
        if heartbeat is not None:
            self.service.schedules.save_default(id, heartbeat)
        return self.public(saved)

    def bind(self, data, id=None):
        with self.service.tasks.lock:
            return self._bind(data, id)

    def _bind(self, data, id=None):
        """绑定的是能力实例，绝不启动服务，也不修改某个 Skill 的加载状态。"""
        old = self.store.get("bindings", id) if id else {}
        value = {**old, **data}
        self.store.get("agents", value.get("agent_id"))
        source = value.get("source")
        if not isinstance(source, str):
            raise Problem("PARAMETER_INVALID", "能力来源必须为字符串")
        selected = value.get("tools", [])
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
            raise Problem("PARAMETER_INVALID", "请选择一个或多个不重复的工具")
        if source == "builtin":
            connection = self.store.get("connections", value.get("connection_ref"))
            available = set(SCHEMAS)
            revision = 1
            if not connection["enabled"]:
                raise Problem("CONNECTION_DISABLED", "连接已停用", 409)
        elif source.startswith("skill:"):
            agent = self.store.get('agents', value['agent_id'])
            provider, runtime = self.service.skill_tools.provider(source[6:], agent)
            available = {t['name'] for t in provider.get('tools', [])}
            revision = self.service.skill_tools.revision(provider)
            source_scoped = provider.get('metadata',{}).get('metadata',{}).get('context-scope') in ('source','workflow')
            job_id = '' if source_scoped else text(value.get('job_id'), '可信作业ID', 128)
            source_id = text(value.get('source_id'), '可信采集源ID', 80) if source_scoped else ''
            for tool in provider.get('tools', []):
                if tool['name'] in selected and tool.get('claim_failure') not in (None, *selected):
                    raise Problem('PARAMETER_INVALID', '领取工具须同时授权配套失败工具')
                if tool['name'] in selected:
                    for logical in tool['storage']:
                        if logical not in runtime['storage']:
                            raise Problem('SKILL_STORAGE_MISSING', '未配置所需逻辑存储', 409)
                        if not self.store.get('skill_storage', runtime['storage'][logical]['resource'])['enabled']:
                            raise Problem('SKILL_STORAGE_DISABLED', '共享存储已停用', 409)
        elif source.startswith("cli:"):
            provider = self.store.get("cli_services", source[4:])
            if not provider["enabled"]:
                raise Problem("CLI_DISABLED", "外部 CLI 已停用", 409)
            available, revision = {t["name"] for t in provider["tools"]}, provider["revision"]
        elif source.startswith("http:"):
            provider = self.store.get("http_services", source[5:])
            if not provider["enabled"]:
                raise Problem("HTTP_DISABLED", "直连能力已停用", 409)
            available, revision = {"call"}, provider["revision"]
        else:
            provider = self.store.get("mcps", source)
            if not provider["healthy"]:
                raise Problem("MCP_UNAVAILABLE", "先发现工具并验证 MCP", 409)
            available = {t["name"] for t in provider["tools"] if t["usable"]}
            revision = provider["revision"]
        if not set(selected).issubset(available):
            raise Problem("TOOL_NOT_FOUND", "所选工具不在可用目录中")
        allowed = value.get("allowed_targets", [])
        if not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed):
            raise Problem("PARAMETER_INVALID", "目标白名单需为 ID 字符串数组")
        return self.store.put("bindings", {"agent_id": value["agent_id"], "source": source, "tools": selected,
            "connection_ref": value.get("connection_ref", "") if source == "builtin" else "",
            "allowed_targets": allowed, "revision": revision, "job_id": job_id if source.startswith('skill:') else '',
            "source_id": source_id if source.startswith('skill:') else '', "enabled": bool(value.get("enabled", True))}, id)

    def catalog(self, agent):
        """模型只看到业务参数；连接账号、授权修订和请求 ID 保留在运行映射中。"""
        catalog, mapping = [], {}
        for binding in self.store.list("bindings"):
            if binding["agent_id"] != agent["id"] or not binding["enabled"]:
                continue
            if binding["source"] == "builtin":
                connection = self.store.get("connections", binding["connection_ref"])
                if not connection["enabled"]:
                    continue
                remote = [{"name": n, "description": DESCRIPTIONS[n], "inputSchema": SCHEMAS[n]} for n in binding["tools"]]
                suffix = "使用连接：" + connection["name"] + "。账号由平台注入。"
            elif binding['source'].startswith('skill:'):
                try:
                    provider, runtime = self.service.skill_tools.provider(binding['source'][6:], agent)
                    if self.service.skill_tools.revision(provider) != binding['revision']:
                        continue
                except Problem:
                    continue
                remote = [{'name': t['name'], 'description': t['description'], 'inputSchema': t['input_schema']}
                          for t in provider.get('tools', []) if t['name'] in binding['tools']]
                suffix = '已授权 Skill 工具：' + provider['name']
            elif binding["source"].startswith("cli:"):
                provider = self.store.get("cli_services", binding["source"][4:])
                if not provider["enabled"] or provider["revision"] != binding["revision"]:
                    continue
                remote = [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
                          for t in provider["tools"] if t["name"] in binding["tools"]]
                suffix = "本机 Docker CLI：" + provider["name"]
            elif binding["source"].startswith("http:"):
                provider = self.store.get("http_services", binding["source"][5:])
                if not provider["enabled"] or provider["revision"] != binding["revision"]:
                    continue
                remote = [{"name": "call", "description": provider["description"], "inputSchema": provider["input_schema"]}]
                suffix = "HTTP 直连能力：" + provider["name"]
            else:
                provider = self.store.get("mcps", binding["source"])
                if not provider["healthy"] or provider["revision"] != binding["revision"]:
                    continue
                remote = [t for t in provider["tools"] if t["name"] in binding["tools"] and t["usable"]]
                suffix = "服务：" + provider["name"]
            for tool in remote:
                name = "cap_" + binding["id"][:12] + "__" + re.sub(r"[^a-zA-Z0-9_-]", "_", tool["name"])[:40]
                if name in mapping:
                    raise Problem("TOOL_NAME_COLLISION", "工具名称冲突，请修改服务公布的名称", 409)
                catalog.append({"type": "function", "function": {"name": name, "description": tool["description"] + " " + suffix,
                                                                    "parameters": tool["inputSchema"]}})
                mapping[name] = {"binding_id": binding["id"], "tool": tool["name"], "schema": tool["inputSchema"],
                                 "binding_revision": binding["revision"], "binding_source": binding["source"]}
                mapping[name]['binding_fingerprint'] = digest(binding)
        # Skill 兼容读取工具；官方 middleware 使用只读 read_file；目录只注入名称和描述，资源脚本不会自动执行。
        active_skills = [sid for sid in agent["skill_ids"] if self.store.get("skills", sid)["enabled"]]
        if active_skills:
            schema = {"type": "object", "properties": {"skill_id": {"type": "string", "enum": active_skills},
                "path": {"type": "string", "description": "省略读取 SKILL.md；引用资源使用 Skill 内相对路径"}},
                "required": ["skill_id"], "additionalProperties": False}
            catalog.append({"type": "function", "function": {"name": "read_skill", "description": "按任务需要读取已绑定 Skill 的正文或参考资料，不执行脚本。", "parameters": schema}})
            mapping["read_skill"] = {"tool": "read_skill", "schema": schema}
        from .artifacts import ARTIFACT_TOOLS
        for name, spec in ARTIFACT_TOOLS.items():
            catalog.append({"type": "function", "function": {"name": name, "description": spec["description"], "parameters": spec["schema"]}})
            mapping[name] = {"tool": name, "schema": spec["schema"], "source": "artifacts"}
        # 协作目录根据当前有方向授权生成；运行时继续检查对象和数据范围。
        grants = [g for g in self.store.list("agent_grants") if g["enabled"] and g["caller_agent_id"] == agent["id"]]
        if grants:
            allowed = {p for g in grants for p in g["permissions"]}
            required = {"submit_task": "submit", "get_task_status": "status", "get_task_result": "result",
                        "cancel_task": "cancel", "agent_status": "overview", "wait_tasks": "status", "list_tasks": "status"}
            required.update({n:'schedule' for n in ('set_heartbeat_schedule','get_heartbeat_schedule','control_heartbeat_schedule')})
            # 调用者需要提前知道目标职责与必填参数；后端仍按当前契约再次校验。
            from .common import dumps
            channel_mode = any(self.store.get('skills',sid)['name']=='industrial-main' for sid in agent['skill_ids'])
            for name, spec in TASK_TOOLS.items():
                if channel_mode and name in ('set_heartbeat_schedule','get_heartbeat_schedule','control_heartbeat_schedule'):continue
                if required[name] not in allowed or (name == "wait_tasks" and not any("status" in g["permissions"] and "result" in g["permissions"] for g in grants)):
                    continue
                from .agent_targets import PERMISSIONS, target_schema
                schema = spec['schema']
                description = spec['description']
                if name in PERMISSIONS:
                    candidates = [self.store.get('agents', g['target_agent_id']) for g in grants
                                  if PERMISSIONS[name] in g['permissions']]
                    schema = target_schema(schema, candidates)
                    if name == 'submit_task':
                        description += ' 目标输入契约：' + dumps([{'name':t['name'],'description':t.get('description',''),'input_schema':t.get('input_schema',{})} for t in candidates])
                    description = description.replace('agent_id', 'agent_name').replace('已配置Agent ID', '已配置Agent名称').replace('已配置目标', '已配置目标名称')
                    description = 'agent_name是被唤醒/执行任务者，不是发起创建者；从枚举选名称，平台自动解析ID。' + description
                catalog.append({"type": "function", "function": {"name": name,
                    "description": description, "parameters": schema}})
                mapping[name] = {"tool": name, "schema": schema, "source": "tasks"}
            if channel_mode and 'schedule' in allowed:
                from .heartbeat_channels import SPECS
                from copy import deepcopy
                names=sorted({self.store.get('agents',g['target_agent_id'])['name'] for g in grants if 'schedule' in g['permissions']})
                for name,spec in SPECS.items():
                    schema=deepcopy(spec['schema'])
                    if 'agent_name' in schema['properties']:schema['properties']['agent_name']['enum']=names
                    catalog.append({'type':'function','function':{'name':name,'description':spec['description'],'parameters':schema}})
                    mapping[name]={'tool':name,'schema':schema,'source':'heartbeat_channels'}
            if 'submit' in allowed and any(self.store.get('skills',sid)['name']=='industrial-main' for sid in agent['skill_ids']):
                from .pipeline_batch import SPEC
                catalog.append({'type':'function','function':{'name':'run_pipeline_batch','description':SPEC['description'],'parameters':SPEC['schema']}})
                mapping['run_pipeline_batch']={'tool':'run_pipeline_batch','schema':SPEC['schema'],'source':'pipeline_batch'}
        return catalog, mapping

    def execute(self, agent_id, item, arguments, run_id):
        """LangChain Tool 入口：参数预检后直接进入公共授权门禁，不经 CLI 中转。"""
        if item.get('tool') == 'set_heartbeat_schedule':
            # Persistent scheduling uses strict types, unlike legacy numeric coercion.
            validate(arguments, item['schema'])
        arguments=normalize_tool_values(arguments,item['schema'])
        # Report the selected command's concrete error instead of an opaque oneOf mismatch.
        for branch in item['schema'].get('oneOf',[]):
            props=branch.get('properties',{})
            if props.get('command',{}).get('const') == arguments.get('command') and 'input' in arguments:
                arguments=dict(arguments,input=normalize_tool_values(arguments['input'],props['input']))
                validate(arguments['input'],props['input'],path='$.input')
                break
        validate(arguments, item["schema"])
        return self.dispatch(agent_id, item, arguments, run_id)

    def dispatch(self, agent_id, item, arguments, run_id):
        """公共业务入口：重新核对授权后路由内置、Docker CLI、MCP 或 HTTP。"""
        agent = self.store.get("agents", agent_id)
        if not agent.get("enabled", True):
            raise Problem("AGENT_DISABLED", "Agent 已停用", 409)
        # 在命令 HTTP 入口再检查任务取消，缩小 Runtime 检查后排队执行的窗口。
        try:
            task = self.service.tasks.repo.get(run_id)
        except Problem as error:
            if error.code != "TASK_NOT_FOUND":
                raise
            task = None  # 管理员独立命令没有协作任务，只使用自己的 Agent 绑定。
        if task and (task["status"] != "running" or task.get("cancel_requested")):
            raise Problem("TASK_CANCELLED", "当前任务已停止或请求取消，命令未启动", 409)
        validate(arguments, item["schema"])
        if item.get("source") == "artifacts":
            return envelope(self.service.artifacts.call(agent_id, item["tool"], arguments, run_id))
        if item.get("source") == "tasks":
            if 'agent_name' in arguments:
                from .agent_targets import resolve_target
                arguments = dict(arguments)
                target_name = arguments.pop('agent_name')
                arguments['agent_id'] = resolve_target(self.store, agent_id, item['tool'], target_name)
                validate(arguments, TASK_TOOLS[item['tool']]['schema'])
            return envelope(self.service.tasks.call(agent_id, item["tool"], arguments, run_id))
        if item.get('source') == 'heartbeat_channels':
            from .heartbeat_channels import run
            return envelope(run(self.service,agent_id,item['tool'],arguments,run_id))
        if item.get('source') == 'pipeline_batch':
            from .pipeline_batch import run
            return envelope(run(self.service,agent_id,arguments,run_id))
        if item["tool"] == "read_skill":
            sid = arguments["skill_id"]
            if sid not in agent["skill_ids"] or not self.store.get("skills", sid)["enabled"]:
                raise Problem("BINDING_DISABLED", "Skill 已解除绑定或停用", 409)
            return envelope(self.service.builtin_call("read_skill", arguments))
        binding = self.store.get("bindings", item["binding_id"])
        if item.get('binding_revision',binding['revision']) != binding['revision'] or item.get('binding_source',binding['source']) != binding['source']:
            raise Problem('BINDING_STALE','运行期间工具绑定已修订，请重新提交任务',409)
        if not binding["enabled"] or binding["agent_id"] != agent_id or item["tool"] not in binding["tools"]:
            raise Problem("BINDING_DISABLED", "能力绑定已停用", 409)
        if item.get('binding_fingerprint', digest(binding)) != digest(binding):
            raise Problem('BINDING_STALE', '运行期间授权参数已变化，请重新提交任务', 409)
        if binding["source"] == "builtin":
            if binding["allowed_targets"] and item["tool"] in ("feishu_send", "lookup_feishu_directory", "feishu_reply", "feishu_ack"):
                if item["tool"] in ("feishu_reply", "feishu_ack"):
                    target_id = self.store.event(binding["connection_ref"], arguments["event_id"])["conversation_id"]
                elif item["tool"] == "lookup_feishu_directory" and not arguments.get("name") and not arguments.get("id"):
                    target_id = None  # 全目录查询在 MCP 返回后过滤，不要求查询指定目标。
                else:
                    target = arguments.get("target") or {"type": arguments.get("entity_type"), **{k: arguments[k] for k in ("name", "id") if arguments.get(k)}}
                    target_id = self.service.feishu.resolve(binding["connection_ref"], target)["id"]
                if target_id is not None and target_id not in binding["allowed_targets"]:
                    raise Problem("TARGET_FORBIDDEN", "此 Agent 未获授权访问该目标", 403)
            if item['tool'] == 'feishu_sync_risk_card':
                return envelope(self.service.feishu.cards.sync(binding['connection_ref'], agent_id, arguments['report_id'], binding['allowed_targets']))
            args = dict(arguments, connection_ref=binding["connection_ref"])
            if item["tool"] in ("feishu_send", "feishu_reply"):
                args["request_id"] = digest([run_id, binding["id"], item["tool"], arguments])[:40]
            outcome = envelope(self.service.builtin_call(item["tool"], args))
            # 有白名单时读取也需要隔离；先保留服务器的真实游标，再过滤未授权的事件。
            if binding["allowed_targets"] and not outcome.get("isError"):
                value = outcome.get("structuredContent", {})
                if item["tool"] == "feishu_events":
                    events = value.get("events", [])
                    value = {"events": [e for e in events if e["conversation_id"] in binding["allowed_targets"]],
                             "next_cursor": max([arguments.get("after", 0), *[e["seq"] for e in events]])}
                    outcome = envelope(value)
                elif item["tool"] == "lookup_feishu_directory" and "entries" in value:
                    outcome = envelope({**value, "entries": {key: meta for key, meta in value["entries"].items()
                                     if key in binding["allowed_targets"]}})
            return outcome
        if binding['source'].startswith('skill:'):
            try:
                return envelope(self.service.skill_tools.call(binding, item['tool'], arguments, run_id))
            except Problem as error:
                self.store.put('tool_runs', {'agent_id': agent_id, 'run_id': run_id, 'tool': item['tool'], 'transport': 'skill-docker', 'error': error.as_dict()})
                raise
        if binding["source"].startswith("cli:"):
            value = self.service.external_cli.call(binding["source"][4:], item["tool"], arguments, binding["revision"], run_id)
            if isinstance(value, dict) and value.get("ok") is False and value.get("error"):
                return envelope(error=value["error"])
            return envelope(value)
        if binding["source"].startswith("http:"):
            return envelope(self.service.direct.call(binding["source"][5:], arguments, binding["revision"], run_id))
        outcome = self.service.registry.call(binding["source"], item["tool"], arguments, binding["revision"])
        return {**outcome, "ok": not outcome.get("isError", False)}

    def model(self, agent, messages, tools=None):
        """兼容管理页模型测试；模型网络适配实际由独立 Runtime 负责。"""
        return self.service.runtime.model(agent, messages, tools)

    def chat(self, session_id, content):
        """兼容同步 Python 调用；HTTP 会话接口使用非阻塞任务提交。

        此方法不在任务调度线程中使用，避免父任务占用子任务执行资源。
        """
        session = self.store.get("sessions", session_id)
        task = self.service.tasks.submit(session["agent_id"], content, session_id=session_id)
        self.service.scheduler.start()
        outcome = self.service.tasks.wait(task["id"])
        if outcome["status"] != "succeeded":
            error = outcome.get("error") or {"code": "TASK_FAILED", "message": "任务未成功"}
            raise Problem(error["code"], error["message"], 409)
        return outcome["result"]
