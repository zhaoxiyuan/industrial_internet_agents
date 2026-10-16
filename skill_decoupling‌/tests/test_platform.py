"""独立平台的本地集成与回归测试，完全不需要飞书或模型账号。

测试启动真实 HTTP 平台、模拟飞书/模型/MCP 上游，以及真实 HTTP MCP 调用。
所有文件落入本项目 tests/.tmp，退出时删除本次临时目录，不访问原项目配置。
模拟服务只用于核对参数和错误传播；通过测试不等于公网飞书权限已通过验证。
运行：python -m unittest discover -s tests -v
"""
import base64
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from unittest.mock import patch

from app import ROOT, make_server
from platform_core.common import Problem, check_schema, dumps, validate
from platform_core.crypto import decrypt_cbc, decrypt_feishu
from platform_core.mcp import Client, rpc_response, result
from platform_core.service import Service
from platform_core.contracts import envelope


class BuiltinBusinessClient:
    """飞书业务测试适配器；真实 CLI 与任务协作在独立测试文件验证。"""
    def __init__(self, service):
        """保留隔离平台对象，不模拟已删除的内部 MCP。"""
        self.service = service

    def call(self, name, args):
        """保持旧业务回归断言，网络请求仅指向本地模拟飞书。"""
        try:
            return envelope(self.service.builtin_call(name, args))
        except Problem as error:
            return envelope(error=error.as_dict())


class Upstream:
    """同时模拟飞书 API、模型 API 和外部 MCP；保留请求用于断言真实网络链路。"""

    def __init__(self):
        """模拟数据每个测试独立；delay 用于复现慢任务期间的管理页可用性。"""
        self.sent, self.model_requests, self.rpc_requests = [], [], []
        self.delay, self.fail_send, self.model_step = 0, False, 0
        self.tools = [{"name": "nested", "description": "嵌套参数回声", "inputSchema": {
            "type": "object", "properties": {"to": {"type": "object", "properties": {
                "conversation_id": {"type": "string"}}, "required": ["conversation_id"], "additionalProperties": False}},
            "required": ["to"], "additionalProperties": False}}]
        parent = self

        class Handler(BaseHTTPRequestHandler):
            """返回有限长度 JSON；不向任何真实飞书地址转发。"""

            def do_GET(self):
                """直连 GET 测试只返回真实收到的查询参数，不提供业务写入。"""
                from urllib.parse import parse_qs, urlsplit
                raw = dumps({"query": parse_qs(urlsplit(self.path).query)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                """按路径区分上游协议，并记录发送、认证和模型调用的实际参数。"""
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                status = 200
                if self.path.endswith("tenant_access_token/internal"):
                    time.sleep(parent.delay)
                    value = {"code": 0, "tenant_access_token": "fake-tenant-token", "expire": 7200}
                elif self.path.startswith("/open-apis/im/v1/messages"):
                    parent.sent.append((self.path, body, self.headers.get("Authorization")))
                    value = {"code": 230002} if parent.fail_send else {"code": 0, "data": {"message_id": "om_" + str(len(parent.sent))}}
                elif self.path == "/v1/chat/completions":
                    parent.model_requests.append(body)
                    value = {"choices": [{"message": parent.model_reply(body)}]}
                elif self.path == "/rpc":
                    parent.rpc_requests.append(body)
                    value = rpc_response(body, parent.tools, parent.mcp_call)
                    status = 202 if value is None else 200
                elif self.path == "/modern":
                    parent.rpc_requests.append(body)
                    meta = body["params"].get("_meta", {})
                    valid = meta.get("io.modelcontextprotocol/protocolVersion") == "2026-07-28" and "io.modelcontextprotocol/clientCapabilities" in meta
                    value = {"jsonrpc": "2.0", "id": body["id"], "result": {"resultType": "complete", "tools": parent.tools}} if valid else {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32602}}
                else:
                    status, value = 404, {"error": "not found"}
                if self.path == '/v1/chat/completions' and body.get('stream'):
                    message=value['choices'][0]['message']
                    delta=dict(message)
                    for i,call in enumerate(delta.get('tool_calls',[])): call['index']=i
                    chunk={'id':'test','object':'chat.completion.chunk','model':'test','choices':[{'index':0,'delta':delta,'finish_reason':None}]}
                    end={'id':'test','object':'chat.completion.chunk','model':'test','choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if delta.get('tool_calls') else 'stop'}]}
                    raw=('data: '+dumps(chunk)+'\n\ndata: '+dumps(end)+'\n\ndata: [DONE]\n\n').encode()
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                raw = dumps(value).encode() if value is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format, *args):
                """模拟服务不输出请求日志，避免测试输出混入敏感测试文本。"""
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def mcp_call(self, name, arguments):
        """外部工具返回结构化内容；本测试工具为回声而非真实写操作。"""
        return {"echo": arguments}

    def model_reply(self, payload):
        """根据历史工具回执选择下一步，覆盖 Skill 读取→查询→发送→最终回复。"""
        tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
        names = [t["function"]["name"] for t in payload.get("tools", [])]
        if not [n for n in names if n not in ('register_artifact','get_artifact')]:
            return {"role": "assistant", "content": "连接成功"}
        if len(tool_messages) == 0:
            name, args = "read_skill", {"skill_id": next(t["function"]["parameters"]["properties"]["skill_id"]["enum"][0] for t in payload["tools"] if t["function"]["name"] == "read_skill")}
        elif len(tool_messages) == 1:
            name = next(n for n in names if n.endswith("lookup_feishu_directory"))
            args = {"entity_type": "group", "name": "动火作业群"}
        elif len(tool_messages) in (2, 3):
            # 模拟模型连续重复同一次发送；运行时应复用结果，真实上游只能收到一次。
            name = next(n for n in names if n.endswith("feishu_send"))
            args = {"target": {"type": "group", "name": "动火作业群"}, "text": "本地集成测试"}
        else:
            return {"role": "assistant", "content": "<think>测试推理文本</think>消息已发送，回执见记录。"}
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_" + str(len(tool_messages)),
            "type": "function", "function": {"name": name, "arguments": dumps(args)}}]}

    def close(self):
        """结束模拟服务器并等待线程退出，释放本机随机端口。"""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class PlatformTests(unittest.TestCase):
    """每个测试使用新的平台数据库和端口，防止缓存与进程相互影响。"""

    def setUp(self):
        """创建隔离数据目录、上游模拟器和真实多线程平台。"""
        temporary = ROOT / "tests" / ".tmp"
        temporary.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary)
        self.service = Service(ROOT, Path(self.temp.name) / "data")
        self.upstream = Upstream()
        self.server = make_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        """先等待后台操作完成，再关闭 HTTP，最后删除本次临时数据。"""
        self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.upstream.close()
        self.temp.cleanup()

    def api(self, path, body=None, method=None, headers=None):
        """通过真实 HTTP 调用平台并保留 HTTP 状态，错误也解析为 JSON。"""
        request = Request(self.base + path, data=dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})}, method=method or ("POST" if body is not None else "GET"))
        try:
            reply = self.opener.open(request, timeout=5)
        except HTTPError as e:
            reply = e
        with reply:
            raw = reply.read()
            return reply.code, json.loads(raw) if raw else None

    def wait_operation(self, operation):
        """轮询有界后台任务；失败时保留完整错误便于断言，不把失败当作测试超时。"""
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            _, row = self.api(("/api/tasks/" if operation.get("task_id") else "/api/operations/") + operation["id"])
            if row["status"] not in ("queued", "running", "waiting"):
                return row
            time.sleep(0.03)
        self.fail("后台操作未在测试时间内结束")

    def connection(self):
        """创建含测试凭据和已知群 ID 的连接；凭据只能用于本地模拟器。"""
        status, row = self.api("/api/connections", {"name": "本地飞书", "app_id": "cli_fake",
            "app_secret": "fake-secret", "verification_token": "fake-verification", "api_base": self.upstream.base,
            "groups": {"oc_test": {"name": "动火作业群"}}, "users": {"ou_test": {"name": "负责人"}}})
        self.assertEqual(status, 201)
        return row

    def skill(self):
        """从独立项目自带的标准示例导入 Skill，不引用原 a_mac_skill。"""
        status, row = self.api("/api/skills", {"source": "examples/skills/feishu-communication"})
        self.assertEqual(status, 201)
        return row

    def inbound(self, connection, event_id="ev_test", chat_id="oc_test"):
        """构造飞书 v2 用户文本事件，通过公开 Webhook 走真实校验路径。"""
        payload = {"schema": "2.0", "header": {"event_id": event_id, "token": "fake-verification", "event_type": "im.message.receive_v1"},
            "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_test"}},
                "message": {"message_id": "om_inbound", "chat_id": chat_id, "message_type": "text", "content": dumps({"text": "收到请回复"})}}}
        return self.api(connection["webhook_path"], payload)

    def test_skill_is_independent_and_deduplicated(self):
        """导入仅保存快照，重复导入复用记录且不创建任何服务或连接。"""
        first, second = self.skill(), self.skill()
        self.assertEqual(first["id"], second["id"])
        _, overview = self.api("/api/overview")
        self.assertEqual(overview["connections"], [])
        self.assertEqual(overview["mcps"], [])
        status, _ = self.api("/api/skills", {"source": "../a_mac_skill"})
        self.assertEqual(status, 400)

    def test_zip_import_and_traversal(self):
        """合法 ZIP 可导入，../ 文件即使放在 ZIP 内也必须拒绝。"""
        for unsafe in (False, True):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as z:
                z.writestr("demo/SKILL.md", "---\nname: demo\ndescription: 示例\n---\n正文")
                if unsafe:
                    z.writestr("../escape.txt", "不能写出项目")
            status, _ = self.api("/api/skills", {"archive": base64.b64encode(stream.getvalue()).decode()})
            self.assertEqual(status, 400 if unsafe else 201)

    def test_env_save_preserves_secret_and_unknown_lines(self):
        """前端保存写 env；编辑留空保留密钥、旧注释及未知变量并产生备份。"""
        c = self.connection()
        path = self.service.feishu.env_path(c["id"])
        with path.open("a", encoding="utf-8") as file:
            file.write("# 自定义说明\nEXTRA=value\n")
        status, row = self.api("/api/connections/" + c["id"], {"app_secret": "", "name": "修改名称"}, "PATCH")
        self.assertEqual(status, 200)
        self.assertNotIn("app_secret", row)
        self.assertTrue(row["app_secret_configured"])
        self.assertEqual(self.service.feishu.config(c["id"])["app_secret"], "fake-secret")
        self.assertIn("EXTRA=value", path.read_text(encoding="utf-8"))
        self.assertTrue(path.with_name(".env.bak").exists())
        self.assertNotIn("fake-secret", dumps(self.service.overview()))

    def test_removed_internal_mcp_and_nested_schema_validation(self):
        """旧内置 MCP 入口已删除，嵌套目标字段错误返回 isError 且不会发消息。"""
        status, _ = self.api("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 404)
        c = self.connection()
        value = BuiltinBusinessClient(self.service).call("feishu_send", {
            "connection_ref": c["id"], "request_id": "r1", "target": {"type": "group", "bad": "oc_test"}, "text": "测试"})
        self.assertTrue(value["isError"])
        self.assertEqual(self.upstream.sent, [])

    def test_send_idempotency_and_api_receipt(self):
        """相同请求只发送一次，账号字段不由 Agent 填写，成功有真实模拟 API 回执。"""
        c = self.connection()
        args = {"connection_ref": c["id"], "request_id": "stable-request", "target": {"type": "group", "name": "动火作业群"}, "text": "测试"}
        client = BuiltinBusinessClient(self.service)
        first = client.call("feishu_send", args)["structuredContent"]
        again = client.call("feishu_send", args)["structuredContent"]
        self.assertEqual(first["status"], "sent")
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.upstream.sent), 1)
        self.assertEqual(self.upstream.sent[0][1]["receive_id"], "oc_test")
        self.assertNotIn("account_id", self.upstream.sent[0][1])
        conflict = client.call("feishu_send", {**args, "text": "不同内容"})
        self.assertTrue(conflict["isError"])

    def test_send_failure_and_unknown_never_report_success(self):
        """飞书拒绝和网络不确定的结果都不是 sent；再次同 ID 不重复请求。"""
        c = self.connection()
        self.upstream.fail_send = True
        args = (c["id"], {"type": "group", "id": "oc_test"}, "测试", "failed-request")
        self.assertEqual(self.service.feishu.send(*args)["status"], "failed")
        self.service.feishu.token(c["id"])
        with patch("platform_core.feishu.request", side_effect=Problem("RESULT_UNKNOWN", "模拟超时", 504)):
            args = (*args[:3], "unknown-request")
            self.assertEqual(self.service.feishu.send(*args)["status"], "unknown")
        self.assertTrue(self.service.feishu.send(*args)["replayed"])
        self.assertEqual(len(self.upstream.sent), 1)

    def test_receive_duplicate_reply_and_ack(self):
        """收件去重、读取不 ACK，成功回复后才允许确认 processed。"""
        c = self.connection()
        self.assertEqual(self.inbound(c)[0], 200)
        self.assertEqual(self.inbound(c)[0], 200)
        self.assertEqual(len(self.service.store.events(c["id"])), 1)
        client = BuiltinBusinessClient(self.service)
        ack = {"connection_ref": c["id"], "event_id": "ev_test", "status": "processed"}
        self.assertTrue(client.call("feishu_ack", ack)["isError"])
        reply = client.call("feishu_reply", {"connection_ref": c["id"], "event_id": "ev_test", "text": "收到", "request_id": "reply1"})
        self.assertEqual(reply["structuredContent"]["status"], "sent")
        self.assertIn("/om_inbound/reply", self.upstream.sent[0][0])
        self.assertFalse(client.call("feishu_ack", ack)["isError"])

    def test_events_page_returns_latest_messages_after_fifty(self):
        """超过一页历史消息后前端仍返回最新消息；MCP 正序游标读取保持原契约。"""
        c = self.connection()
        for index in range(55):
            self.service.store.add_event(c["id"], f"ev_{index}", {"event_id": f"ev_{index}",
                "conversation_id": "oc_test", "sender_id": "ou_test", "content": {"text": str(index)}})
        status, value = self.api("/api/connections/" + c["id"] + "/events")
        self.assertEqual(status, 200)
        self.assertEqual(len(value["events"]), 50)
        self.assertEqual(value["events"][0]["event_id"], "ev_54")
        self.assertEqual(self.service.store.events(c["id"])[0]["event_id"], "ev_0")

    def test_callback_signature_and_token(self):
        """错误 Token 或加密连接缺少签名时拒绝，不把未验证消息写入事件表。"""
        c = self.connection()
        status, _ = self.api(c["webhook_path"], {"header": {"token": "wrong"}})
        self.assertEqual(status, 401)
        self.api("/api/connections/" + c["id"], {"encrypt_key": "test-encrypt"}, "PATCH")
        self.assertEqual(self.inbound(c)[0], 401)
        payload = {"type": "url_verification", "token": "fake-verification", "challenge": "challenge-text"}
        stamp, nonce = str(int(time.time())), "test-nonce"
        signature = hashlib.sha256((stamp + nonce + "test-encrypt").encode() + dumps(payload).encode()).hexdigest()
        status, value = self.api(c["webhook_path"], payload, headers={"X-Lark-Request-Timestamp": stamp,
            "X-Lark-Request-Nonce": nonce, "X-Lark-Signature": signature})
        self.assertEqual((status, value), (200, {"challenge": "challenge-text"}))

    def test_callback_named_p8_accepts_public_host_and_saves_message(self):
        """复现 ngrok 的公网 Host 与 P8 路径：认证后入库，重复投递保持单条记录。"""
        c = self.connection()
        _, c = self.api("/api/connections/" + c["id"], {"name": "P8"}, "PATCH")
        self.assertEqual(c["webhook_path"], "/webhooks/feishu/P8")
        payload = {"header": {"event_id": "ev_public_p8", "token": "fake-verification", "event_type": "im.message.receive_v1"},
            "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_public"}},
                "message": {"message_id": "om_public", "chat_id": "oc_public", "message_type": "text", "content": dumps({"text": "你好"})}}}
        for _ in range(2):
            status, value = self.api("/webhooks/feishu/P8", payload, headers={"Host": "test.ngrok-free.dev"})
            self.assertEqual(status, 200, value)
        _, value = self.api("/api/connections/" + c["id"] + "/events")
        self.assertEqual(len(value["events"]), 1)
        self.assertEqual(value["events"][0]["content"]["text"], "你好")
        self.assertEqual(self.api("/api/overview", headers={"Host": "test.ngrok-free.dev"})[0], 403)

    def test_callback_public_scope_still_requires_token(self):
        """公网例外仅限精确 POST Webhook；错误 Token、GET 或越界路径仍拒绝。"""
        c = self.connection()
        public = {"Host": "test.ngrok-free.dev"}
        status, value = self.api(c["webhook_path"], {"header": {"token": "wrong"}}, headers=public)
        self.assertEqual((status, value["error"]["code"]), (401, "CALLBACK_TOKEN_INVALID"))
        self.assertEqual(self.api(c["webhook_path"], headers=public)[0], 403)
        self.assertEqual(self.api(c["webhook_path"] + "/extra", {}, headers=public)[0], 403)
        status, _ = self.api(c["webhook_path"], {"type": "url_verification", "challenge": "unverified"}, headers=public)
        self.assertEqual(status, 401)
        self.assertEqual(self.service.store.events(c["id"]), [])

    def test_callback_names_unique_and_rename_preserves_identity(self):
        """名称唯一，中文/空格可编码，改名不会改变数据身份，旧名称和 UUID 不再路由。"""
        c = self.connection()
        status, _ = self.api("/api/connections", {"name": c["name"], "app_id": "cli_other"})
        self.assertEqual(status, 409)
        status, renamed = self.api("/api/connections/" + c["id"], {"name": "P8 中文"}, "PATCH")
        self.assertEqual(status, 200)
        self.assertEqual(renamed["id"], c["id"])
        self.assertEqual(renamed["webhook_path"], "/webhooks/feishu/P8%20%E4%B8%AD%E6%96%87")
        self.assertEqual(self.inbound(renamed)[0], 200)
        self.assertEqual(self.api(c["webhook_path"], {})[0], 404)
        self.assertEqual(self.api("/webhooks/feishu/" + c["id"], {})[0], 404)
        self.assertEqual(self.api("/api/connections/" + c["id"], {"name": "P8/other"}, "PATCH")[0], 400)

    def test_background_operation_keeps_page_responsive(self):
        """慢认证运行时 health/overview 仍及时返回，同一连接的并行动作报 409。"""
        c = self.connection()
        self.upstream.delay = 1
        start = time.monotonic()
        status, operation = self.api("/api/connections/" + c["id"] + "/test", {})
        self.assertEqual(status, 202)
        self.assertLess(time.monotonic() - start, 0.6)
        status, _ = self.api("/api/connections/" + c["id"] + "/test", {})
        self.assertEqual(status, 409)
        start = time.monotonic()
        self.assertEqual(self.api("/api/overview")[0], 200)
        self.assertEqual(self.api("/api/health")[0], 200)
        self.assertLess(time.monotonic() - start, 0.6)
        self.assertEqual(self.wait_operation(operation)["status"], "succeeded")

    def test_agent_can_create_before_model(self):
        """无模型凭据也能先创建 Agent，模型测试给出可修复错误而不是循环依赖。"""
        status, agent = self.api("/api/agents", {"name": "空配置 Agent"})
        self.assertEqual(status, 201)
        _, operation = self.api("/api/agents/" + agent["id"] + "/test", {})
        row = self.wait_operation(operation)
        self.assertEqual(row["error"]["code"], "MODEL_NOT_CONFIGURED")
        status, _ = self.api("/api/agents/" + agent["id"] + "/deploy", {})
        self.assertEqual(status, 501)

    def test_agent_skill_cli_feishu_end_to_end(self):
        """真实平台 HTTP→模拟模型→真实内部 MCP→模拟飞书，完整运行含发送去重。"""
        c, skill = self.connection(), self.skill()
        _, agent = self.api("/api/agents", {"name": "飞书助手", "model": "mock-model", "api_key": "fake-model-key",
            "base_url": self.upstream.base + "/v1", "skill_ids": [skill["id"]]})
        status, binding = self.api("/api/bindings", {"agent_id": agent["id"], "source": "builtin", "connection_ref": c["id"],
            "tools": ["lookup_feishu_directory", "feishu_send"], "allowed_targets": ["oc_test"]})
        self.assertEqual(status, 201)
        _, session = self.api("/api/sessions", {"agent_id": agent["id"]})
        _, operation = self.api("/api/sessions/" + session["id"] + "/messages", {"content": "给动火作业群发测试消息"})
        row = self.wait_operation(operation)
        self.assertEqual(row["status"], "succeeded", row)
        self.assertNotIn("think", row["result"]["content"])
        self.assertEqual(len(self.upstream.sent), 1)
        self.assertEqual(len(self.service.store.list("tool_runs")), 4)
        for request in self.upstream.model_requests:
            self.assertNotIn("fake-secret", dumps(request))
            for tool in request["tools"]:
                properties = tool["function"]["parameters"]["properties"]
                self.assertNotIn("connection_ref", properties)
                self.assertNotIn("request_id", properties)
        self.assertEqual(self.api("/api/skills/" + skill["id"], method="DELETE")[0], 409)
        self.assertEqual(self.api("/api/connections/" + c["id"], method="DELETE")[0], 409)
        self.api("/api/bindings/" + binding["id"], method="DELETE")
        self.assertTrue(self.service.store.get("connections", c["id"])["enabled"])

    def test_binding_revocation_and_target_scope(self):
        """目录生成后仍重新检查授权，目标白名单和停用绑定可立即阻止执行。"""
        c = self.connection()
        agent = self.service.agents.save({"name": "授权测试"})
        b = self.service.agents.bind({"agent_id": agent["id"], "source": "builtin", "connection_ref": c["id"],
            "tools": ["feishu_send"], "allowed_targets": ["oc_other"]})
        _, mapping = self.service.agents.catalog(agent)
        item = next(iter(mapping.values()))
        args = {"target": {"type": "group", "id": "oc_test"}, "text": "拒绝发送"}
        with self.assertRaises(Problem) as error:
            self.service.agents.execute(agent["id"], item, args, "run1")
        self.assertEqual(error.exception.code, "TARGET_FORBIDDEN")
        self.service.agents.bind({"enabled": False}, b["id"])
        with self.assertRaises(Problem) as error:
            self.service.agents.execute(agent["id"], item, args, "run1")
        self.assertEqual(error.exception.code, "BINDING_DISABLED")

    def test_target_scope_filters_reads_and_blocks_ack(self):
        """飞书目标范围同时限制目录、事件和确认操作，不能绕过发送授权读取别的群。"""
        c = self.connection()
        self.inbound(c, "ev_allowed", "oc_test")
        self.inbound(c, "ev_hidden", "oc_other")
        agent = self.service.agents.save({"name": "范围过滤"})
        self.service.agents.bind({"agent_id": agent["id"], "source": "builtin", "connection_ref": c["id"],
            "tools": ["feishu_events", "lookup_feishu_directory", "feishu_ack"], "allowed_targets": ["oc_test"]})
        _, mapping = self.service.agents.catalog(agent)
        items = {item["tool"]: item for item in mapping.values()}
        events = self.service.agents.execute(agent["id"], items["feishu_events"], {}, "run1")["structuredContent"]
        self.assertEqual(len(events["events"]), 1)
        self.assertGreater(events["next_cursor"], events["events"][0]["seq"])
        directory = self.service.agents.execute(agent["id"], items["lookup_feishu_directory"], {"entity_type": "group"}, "run1")
        self.assertEqual(list(directory["structuredContent"]["entries"]), ["oc_test"])
        with self.assertRaises(Problem) as error:
            self.service.agents.execute(agent["id"], items["feishu_ack"], {"event_id": "ev_hidden", "status": "ignored"}, "run1")
        self.assertEqual(error.exception.code, "TARGET_FORBIDDEN")

    def test_external_mcp_discovery_revision_and_nested_contract(self):
        """外部 MCP 目录修订变化后旧绑定不能执行，完整嵌套字段在平台侧检查。"""
        provider = self.service.registry.save({"name": "外部模拟 MCP", "endpoint": self.upstream.base + "/rpc"})
        self.service.registry.discover(provider["id"])
        agent = self.service.agents.save({"name": "外部工具 Agent"})
        b = self.service.agents.bind({"agent_id": agent["id"], "source": provider["id"], "tools": ["nested"]})
        result = self.service.registry.call(provider["id"], "nested", {"to": {"conversation_id": "oc_test"}}, b["revision"])
        self.assertFalse(result["isError"])
        with self.assertRaises(Problem):
            self.service.registry.call(provider["id"], "nested", {"to": {}}, b["revision"])
        self.upstream.tools[0]["description"] = "修改后的契约说明"
        self.service.registry.discover(provider["id"])
        with self.assertRaises(Problem) as error:
            self.service.registry.call(provider["id"], "nested", {"to": {"conversation_id": "oc_test"}}, b["revision"])
        self.assertEqual(error.exception.code, "SCHEMA_REVISION_CHANGED")
        self.assertEqual(self.api("/api/mcps/" + provider["id"] + "/stop", {}, "POST")[0], 404)

    def test_mcp_rejects_removed_script_deployment_fields(self):
        """旧启动字段不能通过 API 继续上传或执行；合法本机地址仍可按 HTTP 接入。"""
        for data in ({"mode": "local"}, {"entrypoint": "examples/echo_mcp.py"}, {"script": "cHJpbnQoMSk="}, {"args": []}):
            status, value = self.api("/api/mcps", {"name": "拒绝脚本部署", "endpoint": self.upstream.base + "/rpc", **data})
            self.assertEqual(status, 501)
            self.assertEqual(value["error"]["code"], "DEPLOYMENT_NOT_IMPLEMENTED")
        provider = self.service.registry.save({"name": "本机 HTTP 接入", "endpoint": self.upstream.base + "/rpc"})
        self.assertTrue(self.service.registry.discover(provider["id"])["healthy"])
        self.assertEqual(self.api("/api/mcps/" + provider["id"] + "/start", {}, "POST")[0], 404)

    def test_legacy_local_definition_migrates_without_running_code(self):
        """旧定义移除进程字段并保留身份/端点，重新发现后仍能正常调用。"""
        from platform_core.mcp import Registry
        old = self.service.store.put("mcps", {"name": "旧样例", "mode": "local", "endpoint": self.upstream.base + "/rpc",
            "protocol": "2025-11-25", "entrypoint": "never_execute.py", "args": [], "storage_dir": "old-data",
            "status": "running", "healthy": True, "tools": [], "revision": 0})
        registry = Registry(ROOT, self.service.store)
        migrated = self.service.store.get("mcps", old["id"])
        self.assertEqual(migrated["mode"], "external")
        self.assertFalse(migrated["healthy"])
        self.assertNotIn("entrypoint", migrated)
        self.assertNotIn("args", migrated)
        self.assertTrue(registry.discover(old["id"])["healthy"])

    def test_modern_mcp_metadata(self):
        """Unsupported protocol is rejected instead of silently bypassing official SDK negotiation."""
        with self.assertRaises(Problem) as error:
            self.service.registry.save({"name":"unsupported","endpoint":self.upstream.base+"/modern","protocol":"2026-07-28"})
        self.assertEqual(error.exception.code,"PARAMETER_INVALID")

    def test_host_origin_and_private_static_files(self):
        """Management requests remain same-origin; databases are never static assets."""
        self.assertEqual(self.api('/api/overview',headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.api('/api/overview',headers={'Host':'other.example'})[0],403)
        self.assertEqual(self.api('/data/platform.db')[0],404)
        self.assertEqual(self.api('/mcp')[0],404)


class ContractTests(unittest.TestCase):
    """无 HTTP 的算法与契约测试，核对外部标准而不复制业务实现。"""

    def test_aes256_cbc_nist_vector(self):
        """使用 NIST SP800-38A AES256 CBC 的四块公开向量，核对块顺序和混列。"""
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        ciphertext = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd69cfc4e967edb808d679f777bc6702c7d39f23369a9d9bacfa530e26304231461b2eb05e2c39be9fcda6c19078c6a9d1b")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e5130c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710")
        self.assertEqual(decrypt_cbc(key, iv, ciphertext), plaintext)

    def test_invalid_encrypted_envelope(self):
        """无效信封返回明确解密失败；异常内容不含配置密钥。"""
        with self.assertRaises(Problem) as error:
            decrypt_feishu("private-encrypt-key", "not base64")
        self.assertEqual(error.exception.code, "CALLBACK_DECRYPT_FAILED")
        self.assertNotIn("private-encrypt-key", dumps(error.exception.as_dict()))

    def test_schema_nested_refs_and_rejection(self):
        """本地引用和嵌套数组正确校验；未知断言不能被静默忽略。"""
        schema = {"type": "object", "$defs": {"target": {"type": "string", "pattern": "^oc_"}},
            "properties": {"ids": {"type": "array", "items": {"$ref": "#/$defs/target"}}}, "required": ["ids"]}
        check_schema(schema)
        validate({"ids": ["oc_one"]}, schema)
        with self.assertRaises(Problem):
            validate({"ids": ["wrong"]}, schema)
        with self.assertRaises(Problem):
            check_schema({"type": "object", "unevaluatedProperties": False})

    def test_schema_boolean_is_not_number_and_external_ref_rejected(self):
        """枚举不混淆布尔值和数字；外部引用在发现阶段即拒绝，避免生成伪可用工具。"""
        with self.assertRaises(Problem):
            validate(True, {"enum": [1]})
        validate(1.0, {"const": 1})
        with self.assertRaises(Problem):
            check_schema({"$ref": "https://example.invalid/schema"})

    def test_json_rpc_malformed_params(self):
        """错误请求形状返回协议错误，不触发 AttributeError 或平台 500。"""
        self.assertEqual(rpc_response([], [], lambda *args: {})["error"]["code"], -32600)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}
        self.assertEqual(rpc_response(payload, [], lambda *args: {})["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
