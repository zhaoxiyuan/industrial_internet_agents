"""独立 HTTP/HTTPS MCP 注册表和 Streamable HTTP 客户端。

Skill 内容不参与地址、凭据或进程管理。注册表仅接入已启动的 HTTP/HTTPS
服务；Docker/K8s 部署由未来独立模块负责，不在此执行本地脚本。HTTP 客户端兼容传统握手与
显式选择的 2026 无会话模式，并保留标准 MCP 内容而不强制结果必须为对象。
"""
import json

from .common import Problem, check_schema, digest, dumps, text, uid, url, validate
from .http_client import request


class Client:
    """Official MCP SDK transport + langchain-mcp-adapters tool conversion.

    Each bounded operation owns a session/event loop; nothing survives its loop.
    """
    def __init__(self,endpoint,token="",protocol="2025-11-25",timeout=15):
        self.endpoint,self.token,self.protocol,self.timeout=url(endpoint),token,protocol,timeout
    def initialize(self):
        return None
    async def operation(self,name=None,arguments=None):
        import asyncio
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools
        headers={"Authorization":"Bearer "+self.token} if self.token else {}
        client=MultiServerMCPClient({"provider":{"transport":"streamable_http","url":self.endpoint,"headers":headers}})
        async with asyncio.timeout(self.timeout):
            async with client.session("provider") as session:
                if name is None:
                    values=[]; cursor=None
                    for _ in range(100):
                        page=await session.list_tools(cursor=cursor)
                        values.extend(t.model_dump(by_alias=True,exclude_none=True) for t in page.tools)
                        cursor=page.nextCursor
                        if not cursor: return values
                    raise Problem("MCP_CATALOG_LIMIT","工具目录页数超限",502)
                tools=await load_mcp_tools(session)
                tool=next((t for t in tools if t.name==name),None)
                if tool is None: raise Problem("TOOL_NOT_FOUND","MCP 工具不存在",404)
                message=await tool.ainvoke({"type":"tool_call","id":uid(),"name":name,"args":arguments})
                value={"content":message.content,"isError":message.status=="error"}
                if message.artifact: value["structuredContent"]=message.artifact["structured_content"]
                return value
    def run(self,*args):
        import asyncio
        try: return asyncio.run(self.operation(*args))
        except Problem: raise
        except Exception: raise Problem("MCP_UNAVAILABLE","官方 MCP 连接或调用失败",502)
    def list_tools(self): return self.run()
    def call(self,name,arguments): return self.run(name,arguments)


class Registry:
    """注册表只拥有接入配置和工具契约，不管理进程或服务部署。"""

    def __init__(self, root, store):
        """恢复接入定义；旧本地启动定义迁为 HTTP 接入，保留地址与绑定 ID。

        移除的入口参数不再执行，历史文件不删除；迁移后要求重新检查服务可达性。
        注册表不持有进程，不主动启动或停止任何基础服务。
        """
        self.store = store
        for provider in store.list("mcps"):
            if provider.get("mode") == "local" or any(key in provider for key in ("entrypoint", "args", "storage_dir")):
                value = {key: item for key, item in provider.items() if key not in ("entrypoint", "args", "storage_dir")}
                value.update(mode="external", status="registered", healthy=False)
                store.put("mcps", value, provider["id"])

    def public(self, provider):
        """前端得到凭据是否存在，不得到认证令牌或私有内部引用。"""
        return {k: v for k, v in provider.items() if k not in ("secret_ref",)} | {
            "token_configured": bool(self.store.secret(provider.get("secret_ref", "")))}

    def save(self, data, id=None, preview=False):
        """登记已运行服务的 HTTP/HTTPS 地址与认证，保存配置不会部署或执行服务。"""
        old = self.store.get("mcps", id) if id else {}
        # 明确拒绝旧启动字段，不能只在页面隐藏而让旧 API 继续执行上传代码。
        if any(key in data for key in ("entrypoint", "script", "args")) or data.get("mode", "external") != "external":
            raise Problem("DEPLOYMENT_NOT_IMPLEMENTED", "仅支持 HTTP/HTTPS 服务接入；Docker/K8s 部署待开发", 501)
        id = id or uid()
        endpoint = url(data.get("endpoint", old.get("endpoint")))
        protocol = data.get("protocol", old.get("protocol", "2025-11-25"))
        if protocol not in ("2025-11-25",):
            raise Problem("PARAMETER_INVALID", "请选择支持的 MCP 协议模式")
        value = {"name": text(data.get("name", old.get("name")), "MCP 名称", 80), "mode": "external",
                 "endpoint": endpoint, "description": str(data.get("description", old.get("description", ""))),
                 "source_url": data.get("source_url", old.get("source_url", "")), "protocol": protocol,
                 "status": "registered", "healthy": False, "tools": [], "revision": old.get("revision", 0)}
        if value["source_url"]:
            url(value["source_url"])
        if preview: return value
        ref = old.get("secret_ref", "mcp:" + id)
        if data.get("token"):
            self.store.secret(ref, data["token"])
        if data.get("clear_token"):
            self.store.secret(ref, "")
        value["secret_ref"] = ref
        return self.public(self.store.put("mcps", value, id))

    def client(self, provider):
        """从平台凭据引用构造客户端；Agent 不参与地址和 token 的选择。"""
        return Client(provider["endpoint"], self.store.secret(provider.get("secret_ref", "")), provider["protocol"])

    def discover(self, id, timeout=15):
        """先生成目录再原子保存修订；不兼容工具保留可见但禁止绑定执行。"""
        provider = self.store.get("mcps", id)
        try:
            client = self.client(provider)
            client.timeout = timeout
            advertised = client.list_tools()
            names, tools = set(), []
            for remote in advertised:
                name = text(remote.get("name"), "工具名称", 120)
                if name in names:
                    raise Problem("MCP_INVALID_RESPONSE", "服务公布了重复工具名", 502)
                names.add(name)
                tool = {"name": name, "description": str(remote.get("description", name)),
                        "inputSchema": remote.get("inputSchema"), "outputSchema": remote.get("outputSchema"), "usable": True}
                try:
                    if not isinstance(tool["inputSchema"], dict) or tool["inputSchema"].get("type") != "object":
                        raise Problem("SCHEMA_INVALID", "工具输入必须是对象 Schema")
                    check_schema(tool["inputSchema"])
                    if tool["outputSchema"]:
                        check_schema(tool["outputSchema"])
                except Problem as e:
                    tool.update(usable=False, schema_error=e.as_dict())
                tools.append(tool)
            revision = provider.get("revision", 0)
            if digest(tools) != digest(provider.get("tools", [])):
                revision += 1
            provider = self.store.put("mcps", {**provider, "healthy": True, "status": "connected",
                                      "tools": tools, "revision": revision, "last_error": None}, id)
            return self.public(provider)
        except Problem as e:
            self.store.put("mcps", {**provider, "healthy": False, "last_error": e.as_dict()}, id)
            raise

    def call(self, id, name, arguments, revision):
        """拒绝陈旧授权修订并校验完整嵌套参数；错误作为工具结果交还运行时。"""
        provider = self.store.get("mcps", id)
        if not provider["healthy"]:
            raise Problem("MCP_UNAVAILABLE", "MCP 未就绪", 409)
        if provider["revision"] != revision:
            raise Problem("SCHEMA_REVISION_CHANGED", "工具契约已改变，请重新确认 Agent 绑定", 409)
        tool = next((t for t in provider["tools"] if t["name"] == name and t["usable"]), None)
        if not tool:
            raise Problem("TOOL_NOT_FOUND", "此工具未开放", 404)
        validate(arguments, tool["inputSchema"])
        result = self.client(provider).call(name, arguments)
        if not result.get("isError") and tool.get("outputSchema") and "structuredContent" in result:
            validate(result["structuredContent"], tool["outputSchema"])
        return result



def result(value, error=False):
    """把内部结构化功能结果转换为标准 MCP ToolResult。"""
    return {"content": [{"type": "text", "text": dumps(value)}], "structuredContent": value, "isError": error}


def rpc_response(payload, tools, call):
    """示例外部 MCP 服务可用的无状态 JSON-RPC 路由；平台内置能力不使用此入口。"""
    if not isinstance(payload, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        return {"jsonrpc": "2.0", "id": id, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in payload:
        return None
    method, params = payload["method"], payload.get("params", {})
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": id, "error": {"code": -32602, "message": "Invalid params"}}
    # 内置服务使用 2025 协议。明确拒绝其他版本，不能把缺少新协议语义的响应伪装为兼容。
    requested = params.get("_meta", {}).get("io.modelcontextprotocol/protocolVersion")
    if requested and requested != "2025-11-25":
        return {"jsonrpc": "2.0", "id": id, "error": {"code": -32022,
                "message": "Unsupported protocol version", "data": {"supported": ["2025-11-25"], "requested": requested}}}
    if method == "initialize":
        value = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "decoupled-platform", "version": "1.0.0"}}
    elif method == "tools/list":
        value = {"tools": tools}
    elif method == "tools/call":
        try:
            value = result(call(params.get("name", ""), params.get("arguments", {})))
            if isinstance(value.get("structuredContent"), dict) and value["structuredContent"].get("status") in ("failed", "unknown"):
                value["isError"] = True
        except Problem as e:
            value = result({"ok": False, "error": e.as_dict()}, True)
    elif method == "ping":
        value = {}
    else:
        return {"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": id, "result": value}
