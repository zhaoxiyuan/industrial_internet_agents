"""平台应用服务：将独立对象组合成 API 操作，不把部署逻辑放回 Skill。"""
import secrets
from pathlib import Path

from .agents import Agents
from .common import Problem, digest, dumps, uid
from .feishu import Feishu, SCHEMAS
from .mcp import Registry
from .operations import Operations
from .skills import Skills
from .storage import Store
from .cli_bridge import CliBridge
from .contracts import TASK_TOOLS, envelope
from .direct_http import DirectHttp
from .external_cli import ExternalCli
from .runtime import Runtime
from .tool_registry import ToolProviders
from .tasks import Tasks
from .artifacts import ARTIFACT_TOOLS
from .scheduler import Scheduler


class Service:
    """持有模块实例与 CLI 调用桥接，不持有旧项目代码或路径。"""

    def __init__(self, root, data=None):
        """运行数据必须落在新项目内部；默认 data 可复制备份而不会依赖原仓库。"""
        self.root = Path(root).resolve()
        self.data = Path(data or self.root / "data").resolve()
        if not self.data.is_relative_to(self.root) or self.data == self.root:
            raise Problem("PATH_INVALID", "数据目录必须位于独立项目内")
        self.store = Store(self.data)
        self.skills = Skills(self.root, self.store)
        self.feishu = Feishu(self.store)
        self.registry = Registry(self.root, self.store)
        self.direct = DirectHttp(self.store)
        self.external_cli = ExternalCli(self.store)
        from .skill_tools import SkillTools
        self.skill_tools = SkillTools(self)
        self.tool_providers = ToolProviders(self)
        self.operations = Operations(self.store)
        self.agents = Agents(self)
        self.cli = CliBridge(self)
        self.runtime = Runtime(self)
        self.tasks = Tasks(self)
        from .sessions import Sessions
        self.sessions = Sessions(self)
        from .schedules import Schedules
        self.schedules = Schedules(self)
        from .artifacts import Artifacts
        self.artifacts = Artifacts(self)
        self.scheduler = Scheduler(self)
        self.cli_token = self.store.secret("cli-admin") or secrets.token_urlsafe(32)
        self.store.secret("cli-admin", self.cli_token)
        # 管理员 CLI 的本机凭据文件不进入前端、模型或版本控制。
        import os
        token_file = self.data / ".cli-admin-token"
        token_file.write_text(self.cli_token, encoding="utf-8")
        os.chmod(token_file, 0o600)

    def overview(self, compact=False):
        """管理页一次读取当前对象；不在 overview 中探测远程服务而拖慢页面。"""
        from .topology import snapshot
        value = {"runtime": "langchain-langgraph", "tool_providers": self.tool_providers.list(), "skills": self.store.list("skills"), "connections": [self.feishu.public(c["id"]) for c in self.store.list("connections")],
                "mcps": [self.registry.public(m) for m in self.store.list("mcps")],
                "agents": [self.agents.public(a) for a in self.store.list("agents")],
                "deleted_agents": self.store.list('deleted_agents'),
                "bindings": self.store.list("bindings"), "sessions": [s for s in self.store.list("sessions") if not s.get('deleted_at')],
                "skill_storage": self.store.list('skill_storage'), "skill_runtimes": self.store.list('skill_runtimes'),
                "operations": self.store.list("operations")[-50:], "tool_runs": self.store.list("tool_runs")[-30:],
                "cli_commands": [{"name": t["name"], "description": t["description"]} for t in self.builtin_tools()] + [{"name": n, "description": t["description"]} for n, t in {**TASK_TOOLS, **ARTIFACT_TOOLS}.items()],
                "http_services": [self.direct.public(h) for h in self.store.list("http_services")],
                "cli_services": self.store.list("cli_services"),
                "agent_grants": self.store.list("agent_grants"), "agent_topology": snapshot(self), "tasks": self.tasks.repo.list()[-100:],
                "schedules": [self.schedules.detail(p['id']) for p in self.schedules.list()],
                "agent_statuses": [self.tasks.overview(a["id"]) for a in self.store.list("agents")],
                "scheduler": {"alive": bool(self.scheduler.thread and self.scheduler.thread.is_alive()),
                              "error": self.scheduler.last_error, "workers": self.scheduler.workers},
                "functions": [{"id": "feishu.messaging.v1", "name": "飞书收发消息", "state": "implemented", "tools": list(SCHEMAS)},
                              {"id": "artifacts.v1", "name": "成果引用登记与交接", "state": "implemented", "tools": list(ARTIFACT_TOOLS)},
                              {"id": "files.v1", "name": "通用文件内容访问", "state": "reserved", "tools": []}],
                "tool_execution_backends": [{"id":"docker-local","supported":True},{"id":"docker-remote","supported":False}],
                "deployment_backends": [{"id": "docker", "supported": False}, {"id": "kubernetes", "supported": False}],
                "execution_targets": [{"id": "platform-local", "supported": True}, {"id": "distributed", "supported": False}, {"id": "edge", "supported": False}]}

        if compact:
            # The UI polls summaries; model catalogs and explicit details stay complete.
            for key in ('skills','tool_providers','mcps','cli_services','http_services'):
                rows=[]
                for original in value[key]:
                    row={k:v for k,v in original.items() if k not in ('body','files','input_schema','output_schema')}
                    if 'tools' in row:
                        row['tool_count']=len(row['tools'])
                        row['tools']=[{k:t[k] for k in ('name','usable') if k in t} for t in row['tools']]
                    rows.append(row)
                value[key]=rows
            value['cli_commands']=[{'name':t['name']} for t in value['cli_commands']]
            for key in ('operations','tool_runs'):
                value[key]=[{k:v for k,v in row.items() if k not in ('result','input','arguments')} for row in value[key]]
            task_fields=('id','task_id','root_task_id','parent_task_id','session_id','caller_agent_id','executor_agent_id',
                         'description','status','phase','error','created','updated','version','artifacts','executor_active',
                         'schedule_id','schedule_owner_agent_id','context_tokens','context_estimated')
            value['tasks']=[{k:row[k] for k in task_fields if k in row} for row in value['tasks']]
            value['details_deferred']=True
        return value

    def builtin_tools(self):
        """平台内置 CLI 契约目录；名称保留以兼容内部业务 Schema 定义。"""
        return self.feishu.tools() + [{"name": "read_skill", "description": "读取标准 Skill 的正文或资源，不执行文件。",
            "inputSchema": {"type": "object", "properties": {"skill_id": {"type": "string"}, "path": {"type": "string"}},
                            "required": ["skill_id"], "additionalProperties": False}}]

    def builtin_call(self, name, args):
        """调用内置业务实现；Agent 必须经 CLI 票据及绑定校验才能进入此处。"""
        if name != "read_skill":
            return self.feishu.call(name, args)
        from .common import validate
        validate(args, self.builtin_tools()[-1]["inputSchema"])
        skill = self.store.get("skills", args["skill_id"])
        if not skill["enabled"]:
            raise Problem("SKILL_DISABLED", "Skill 已停用", 409)
        path = self.skills.resource(skill["id"], args.get("path", "SKILL.md"))
        if path.stat().st_size > 128 * 1024:
            raise Problem("RESOURCE_LIMIT", "文本资源过大，请从资源接口下载", 413)
        try:
            return {"name": skill["name"], "resource": args.get("path", "SKILL.md"), "text": path.read_text(encoding="utf-8")}
        except UnicodeDecodeError:
            raise Problem("RESOURCE_BINARY", "此资源为二进制文件，请使用下载接口")

    def function_operation(self, id, tool, args):
        """管理 REST 复用内置业务层，无需内部 MCP；发送仍有持久幂等回执。"""
        values = dict(args, connection_ref=id)
        if tool in ("feishu_send", "feishu_reply"):
            values["request_id"] = values.get("request_id") or uid()
        def work():
            """后台线程调用内置业务，错误作为 operation 状态供前端查询。"""
            outcome = envelope(self.builtin_call(tool, values))
            if outcome.get("isError"):
                structured = outcome.get("structuredContent", {})
                error = structured.get("error", {})
                raise Problem(error.get("code", "FUNCTION_FAILED"), error.get("message", "功能调用失败"), 409, structured)
            return outcome
        return self.operations.submit(tool, "connection:" + id, work)

    def remove_mcp(self, id):
        """删除注册信息前核对使用者；不随删除清理业务文件或日志。"""
        provider = self.store.get("mcps", id)
        if any(b["source"] == id for b in self.store.list("bindings")):
            raise Problem("IN_USE", "请先解除所有 Agent 的能力绑定", 409)
        self.store.delete("mcps", id)

    def trigger_inbound(self, connection_id, receipt):
        """已验证事件可选异步提交总任务，Webhook 不运行模型或自动回复。

        重复事件只提交一次；提交失败留在后台操作记录供管理员核对。
        触发授权来自连接配置，工具发送仍需该 Agent 自己的能力绑定。
        """
        connection = self.store.get("connections", connection_id)
        if not connection.get("inbound_agent_id") or not receipt.get("seq"):
            return
        events = self.store.events(connection_id, receipt["seq"] - 1, 1)
        if not events:
            return
        event = events[0]
        request_id = "feishu:" + digest([connection_id, event["event_id"]])
        with self.tasks.lock:
            if self.store.get("inbound_runs", request_id, required=False):
                return
            self.store.put("inbound_runs", {"event_id": event["event_id"], "connection_id": connection_id}, request_id)
            def work():
                """子任务提交保持事件输入；不会确认事件或声称已回复。"""
                description = "处理飞书入站消息：" + dumps(event.get("content", {}))
                row = self.tasks.submit(connection["inbound_agent_id"], description,
                    {"connection_id": connection_id, "event": event}, request_id=request_id)
                self.store.put("inbound_runs", {"event_id": event["event_id"], "connection_id": connection_id,
                                                "task_id": row["id"]}, request_id)
                return {"task_id": row["id"]}
            self.operations.submit("feishu-inbound-task", request_id, work)

    def deploy_edge(self, agent_id):
        """明确返回未实现，不创建伪成功部署记录，也不执行 SSH 或远程安装。"""
        self.store.get("agents", agent_id)
        raise Problem("EDGE_NOT_IMPLEMENTED", "边缘 Agent 部署仅预留接口，本期未实现", 501)

    def close(self):
        """等待已受理操作完成；服务进程由独立部署系统管理。"""
        self.feishu.cards.close()
        self.operations.close()
        self.scheduler.close()
