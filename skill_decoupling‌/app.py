"""独立 Skill 平台的本地 HTTP 入口。

运行：python app.py --port 8770。服务器固定绑定 loopback，管理 API 仅接受
同源 JSON 请求；依赖项目虚拟环境；外部 CLI 需要本机 Docker。后台任务和 MCP 入口使用
多线程处理，因此MCP 连接检查、模型调用等不会堵住管理页。
"""
import argparse
import json
import mimetypes
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from platform_core.common import Problem, dumps, safe_path, text
from platform_core.service import Service
from platform_core.collaboration_api import handle as collaboration_route
from platform_core.risk_card_routes import PUBLIC_PATHS, handle as risk_card_route

ROOT = Path(__file__).resolve().parent


def feishu_callback_name(path):
    """识别唯一可接受公网请求的路径，返回 URL 解码后的连接名称。

    只允许 /webhooks/feishu/<名称> 这一个 POST 路由跳过管理 Host/Origin 限制。
    不接受额外层级、空名称或编码后的斜线，避免放宽到管理 API 或其他路径。
    名称只用于数据库查找，不参与文件路径拼接；实际数据仍按内部 ID 隔离。
    """
    parts = path.split("/")
    if len(parts) != 4 or parts[:3] != ["", "webhooks", "feishu"] or not parts[3]:
        return None
    name = unquote(parts[3], encoding="utf-8", errors="strict")
    if "/" in name or "\\" in name:
        return None
    return name


def make_server(service, port=0):
    """创建可嵌入测试的服务器，并回填平台内部 MCP 的实际监听地址。"""
    class Handler(BaseHTTPRequestHandler):
        """所有响应添加禁缓存头；不把应用数据目录当成静态文件目录暴露。"""

        def send_json(self, status, value=None):
            """MCP 通知返回 202 空体；其他接口统一返回 UTF-8 JSON。"""
            body = dumps(value).encode("utf-8") if value is not None else b""
            self.send_bytes(status, body, "application/json; charset=utf-8")

        def send_bytes(self, status, body, content_type):
            """按明确长度结束响应，防止浏览器把无结束标记误认为接口卡住。"""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def body(self):
            """拒绝错误 Content-Type、过大请求和非对象 JSON；Webhook 保存原始字节用于验签。"""
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise Problem("CONTENT_TYPE_INVALID", "请求需要 application/json", 415)
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                raise Problem("REQUEST_INVALID", "无效 Content-Length")
            if length < 0 or length > 30 * 1024 * 1024:
                raise Problem("UPLOAD_LIMIT", "请求过大", 413)
            raw = self.rfile.read(length)
            self._body_consumed = True
            try:
                value = json.loads(raw or b"{}")
            except (ValueError, UnicodeDecodeError):
                raise Problem("REQUEST_INVALID", "无效 JSON")
            if not isinstance(value, dict):
                raise Problem("REQUEST_INVALID", "JSON 请求必须为对象")
            return value, raw

        def discard_rejected_body(self):
            """拒绝请求时丢弃已声明的小请求体，避免 Windows 关闭含未读数据的连接时复位。

            不解析或保存内容；只允许最多 1MB、最多等 1 秒，防止非法请求拖住线程。
            正常路由已经读取的 body 不重复读取，原业务错误码保持不变。
            """
            if getattr(self, "_body_consumed", False):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if not 0 < length <= 1024 * 1024:
                return
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(1)
                self.rfile.read(length)
            except OSError:
                pass
            finally:
                self.connection.settimeout(previous_timeout)
                self._body_consumed = True

        def route(self):
            """固定路由表明确分开内容、能力和服务操作；未知资源统一返回 404。"""
            parsed = urlsplit(self.path)
            path = parsed.path
            parts = [p for p in path.split("/") if p]
            method = self.command
            if path in PUBLIC_PATHS:
                return risk_card_route(self, service, method, parsed)
            if method == "GET" and path in ("/", "/web/app.js", "/web/style.css", "/web/collaboration.js", "/web/schema-form.js", "/web/binding-controls.js", "/web/external-access.js", "/web/topology.js", "/web/topology.css", "/web/chat-content.js"):
                file = ROOT / "web" / ("index.html" if path == "/" else path.rsplit("/", 1)[1])
                return self.send_bytes(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "text/plain")
            # 内部 MCP 已移除；外部 MCP 客户端独立保留在 Registry 中。
            if path == "/mcp":
                raise Problem("NOT_FOUND", "平台内置能力已迁移至 LangChain Tool", 404)
            callback = feishu_callback_name(path)
            if callback is not None and method == "POST":
                data, raw = self.body()
                connection_id = service.feishu.callback_connection(callback)
                result = service.feishu.inbound(connection_id, data, {k.lower(): v for k, v in self.headers.items()}, raw)
                service.trigger_inbound(connection_id, result)
                return self.send_json(200, result)
            if not parts or parts[0] != "api":
                raise Problem("NOT_FOUND", "没有此接口", 404)
            if method == "GET" and parts == ["api", "health"]:
                return self.send_json(200, {"healthy": True, "mode": "local", "docker_required": False, "runtime": "langchain-langgraph", "cli_execution": "docker-local"})
            if method == "GET" and parts == ["api", "overview"]:
                return self.send_json(200, service.overview(compact=True))
            if method == "GET" and len(parts) == 3 and parts[1] == "tool-runs":
                return self.send_json(200, service.store.get('tool_runs', parts[2]))
            if method == "GET" and len(parts) == 3 and parts[1] == "operations":
                return self.send_json(200, service.store.get("operations", parts[2]))
            if method == "GET" and len(parts) == 3 and parts[1] == "skills":
                return self.send_json(200, service.store.get('skills', parts[2]))
            if method == "GET" and len(parts) == 4 and parts[1] == "skills" and parts[3] == "resource":
                relative = parse_qs(parsed.query).get("path", ["SKILL.md"])[0]
                file = service.skills.resource(parts[2], relative)
                return self.send_bytes(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            if method == "GET" and len(parts) == 3 and parts[1] == "connections":
                return self.send_json(200, service.feishu.public(parts[2]))
            if method == "GET" and len(parts) == 4 and parts[1] == "connections" and parts[3] == "events":
                service.store.get("connections", parts[2])
                return self.send_json(200, {"events": service.store.latest_events(parts[2])})
            if method == "GET" and len(parts)==4 and parts[1]=='tasks' and parts[3]=='stream':
                import time
                task_id=parts[2]; service.tasks.repo.get(task_id)
                after=int(self.headers.get('Last-Event-ID') or parse_qs(parsed.query).get('after',['0'])[0])
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Connection','close')
                self.end_headers()
                self.close_connection=True
                deadline=time.monotonic()+55
                try:
                    while time.monotonic()<deadline:
                        events=service.runtime.events(task_id,after)
                        for event in events:
                            after=event['id']
                            self.wfile.write(('id: '+str(after)+'\ndata: '+dumps(event)+'\n\n').encode('utf-8'))
                        self.wfile.flush()
                        if len(events)<200 and service.tasks.repo.get(task_id)['status'] in ('succeeded','failed','cancelled','interrupted'):
                            self.wfile.write(b'event: finished\ndata: {}\n\n');self.wfile.flush();break
                        time.sleep(.2)
                except (BrokenPipeError,ConnectionResetError): pass
                return
            if method == "GET" and len(parts) == 4 and parts[1] == "sessions" and parts[3] == "messages":
                service.sessions.active(parts[2])
                return self.send_json(200, service.store.messages(parts[2]))
            if method == "GET" and len(parts) == 5 and parts[:2] == ["api", "tasks"] and parts[3] == "artifacts":
                task = service.tasks.repo.get(parts[2])
                index = int(parts[4])
                if not 0 <= index < len(task["artifacts"]):
                    raise Problem("NOT_FOUND", "没有此成果", 404)
                file = safe_path(service.root, task["artifacts"][index]["path"])
                if not file.is_relative_to(service.data / "artifacts" / task["id"]):
                    raise Problem("PATH_INVALID", "成果位置不属于此任务", 403)
                return self.send_bytes(200, file.read_bytes(), "application/json; charset=utf-8")
            data, _ = self.body() if (method in ("POST", "PATCH") or (method == "DELETE" and self.headers.get("Content-Length", "0") != "0")) else ({}, b"")
            if method == "GET" and parts == ["api", "cli", "catalog"]:
                data = {"agent_id": parse_qs(parsed.query).get("agent_id", [""])[0]}
            response = collaboration_route(service, method, parts, data, self.headers)
            if response is not None:
                return self.send_json(*response)
            from platform_core.monitoring import handle as monitoring_route
            response = monitoring_route(service, method, parts, data)
            if response is not None:
                return self.send_json(*response)
            if method == "POST" and parts == ["api", "skills"]:
                return self.send_json(201, service.skills.import_skill(data))
            if method == 'POST' and len(parts) == 4 and parts[1] == 'skills' and parts[3] == 'runtime':
                return self.send_json(200, service.skill_tools.configure(parts[2], data))
            if method == 'POST' and len(parts) == 3 and parts[1] == 'skill-storage':
                return self.send_json(200, service.skill_tools.configure_storage(parts[2], data))
            if method == "PATCH" and len(parts) == 3 and parts[1] == "skills":
                row = service.store.get("skills", parts[2])
                if not isinstance(data.get("enabled"), bool):
                    raise Problem("PARAMETER_INVALID", "enabled 必须为布尔值")
                return self.send_json(200, service.store.put("skills", {**row, "enabled": data["enabled"]}, row["id"]))
            if method == "DELETE" and len(parts) == 3 and parts[1] == "skills":
                service.skills.remove(parts[2])
                return self.send_json(200, {"ok": True, "note": "服务和业务数据保留"})
            if method == "POST" and parts == ["api", "connections"]:
                return self.send_json(201, service.feishu.save(data))
            if method == 'POST' and len(parts) == 4 and parts[1] == 'connections' and parts[3] == 'risk-cards':
                return self.send_json(200, service.feishu.cards.configure(parts[2], data))
            if method == 'POST' and len(parts) == 4 and parts[1] == 'connections' and parts[3] == 'confirm-job-closed':
                from platform_core.workflow import close_job
                return self.send_json(200, close_job(service, parts[2], data))
            if method == "PATCH" and len(parts) == 3 and parts[1] == "connections":
                return self.send_json(200, service.feishu.save(data, parts[2]))
            if method == "DELETE" and len(parts) == 3 and parts[1] == "connections":
                if any(b.get("connection_ref") == parts[2] for b in service.store.list("bindings")):
                    raise Problem("IN_USE", "先解除连接的 Agent 绑定", 409)
                service.store.delete("connections", parts[2])
                return self.send_json(200, {"ok": True, "note": "配置文件保留"})
            if method == "POST" and len(parts) == 4 and parts[1] == "connections":
                service.store.get("connections", parts[2])
                tool = {"test": "feishu_health", "lookup": "lookup_feishu_directory", "send": "feishu_send",
                        "reply": "feishu_reply", "ack": "feishu_ack"}.get(parts[3])
                if tool:
                    return self.send_json(202, service.function_operation(parts[2], tool, data))
            if method == "POST" and parts == ["api", "mcps"]:
                return self.send_json(201, service.registry.save(data))
            if method == "PATCH" and len(parts) == 3 and parts[1] == "mcps":
                return self.send_json(200, service.registry.save(data, parts[2]))
            if method == "DELETE" and len(parts) == 3 and parts[1] == "mcps":
                service.remove_mcp(parts[2])
                return self.send_json(200, {"ok": True, "note": "服务数据保留"})
            if method == "POST" and len(parts) == 4 and parts[1] == "mcps" and parts[3] == "discover":
                id = parts[2]
                service.store.get("mcps", id)
                return self.send_json(202, service.operations.submit("mcp-discover", "mcp:" + id,
                    lambda: service.registry.discover(id)))
            if method == "POST" and parts == ["api", "agents"]:
                return self.send_json(201, service.agents.save(data))
            if method == "PATCH" and len(parts) == 3 and parts[1] == "agents":
                return self.send_json(200, service.agents.save(data, parts[2]))
            if method == 'GET' and len(parts) == 4 and parts[1] == 'agents' and parts[3] == 'deletion':
                from platform_core.agent_lifecycle import deletion_preview
                return self.send_json(200, deletion_preview(service, parts[2]))
            if method == 'DELETE' and len(parts) == 3 and parts[1] == 'agents':
                from platform_core.agent_lifecycle import remove_agent
                return self.send_json(200, remove_agent(service, parts[2]))
            if method == "POST" and len(parts) == 4 and parts[1] == "agents" and parts[3] == "test":
                with service.tasks.lock:
                    agent = service.store.get("agents", parts[2])
                    return self.send_json(202, service.operations.submit("model-test", "agent:" + parts[2],
                        lambda: service.agents.model(agent, [{"role": "user", "content": "只回复：连接成功"}])))
            if method == "POST" and len(parts) == 4 and parts[1] == "agents" and parts[3] == "deploy":
                service.deploy_edge(parts[2])
            if method == "POST" and parts == ["api", "bindings"]:
                return self.send_json(201, service.agents.bind(data))
            if method == "PATCH" and len(parts) == 3 and parts[1] == "bindings":
                return self.send_json(200, service.agents.bind(data, parts[2]))
            if method == "DELETE" and len(parts) == 3 and parts[1] == "bindings":
                service.store.delete("bindings", parts[2])
                return self.send_json(200, {"ok": True, "note": "MCP 服务与连接保持独立"})
            if method == "POST" and parts == ["api", "sessions"]:
                return self.send_json(201, service.sessions.create(data))
            if method == 'PATCH' and len(parts) == 3 and parts[1] == 'sessions':
                return self.send_json(200, service.sessions.rename(parts[2], data))
            if method == "POST" and len(parts) == 4 and parts[1] == "sessions" and parts[3] == "messages":
                id, content = parts[2], text(data.get("content"), "消息", 10000)
                service.store.get("sessions", id)
                session = service.store.get("sessions", id)
                return self.send_json(202, service.tasks.submit(session["agent_id"], content, session_id=id))
            if method == "DELETE" and len(parts) == 3 and parts[1] == "sessions":
                return self.send_json(200, service.sessions.delete(parts[2]))
            raise Problem("NOT_FOUND", "没有此接口", 404)

        def handle_method(self):
            """管理 API 检查本机 Host/Origin，公网 Webhook 由飞书 Token/签名认证。

            ngrok 保留公网 Host 是正常行为，只对精确的 POST 回调入口例外。
            不信任 X-Forwarded-Host，也不放宽管理页面、配置 API 或 MCP 的访问限制。
            """
            try:
                host = self.headers.get("Host", "")
                parsed = urlsplit("http://" + host)
                public_callback = (self.command == "POST" and feishu_callback_name(urlsplit(self.path).path) is not None) or urlsplit(self.path).path in PUBLIC_PATHS
                if not public_callback and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
                    raise Problem("HOST_FORBIDDEN", "仅接受本机管理请求", 403)
                origin = self.headers.get("Origin")
                if not public_callback and origin and urlsplit(origin).netloc != host:
                    raise Problem("ORIGIN_FORBIDDEN", "管理接口不接受跨源请求", 403)
                self.route()
            except Problem as e:
                self.discard_rejected_body()
                self.send_json(e.status, {"error": e.as_dict()})
            except (ValueError, TypeError, KeyError):
                self.send_json(400, {"error": {"code": "PARAMETER_INVALID", "message": "参数格式不正确"}})
            except OSError:
                self.send_json(500, {"error": {"code": "IO_ERROR", "message": "文件或进程操作失败"}})
            except Exception:
                self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "平台发生内部错误"}})

        def do_GET(self):
            """读取操作不会等待后台任务或远程模型。"""
            self.handle_method()

        def do_POST(self):
            """创建与动作接口统一使用 JSON 请求。"""
            self.handle_method()

        def do_PATCH(self):
            """对象配置修改保留已有未显式替换的密钥。"""
            self.handle_method()

        def do_DELETE(self):
            """删除注册和绑定与实际业务数据清理分开。"""
            self.handle_method()

        def log_message(self, format, *args):
            """记录状态行而不记录请求内容，避免消息与凭据进入访问日志。"""
            if urlsplit(self.path).path not in PUBLIC_PATHS and self.path not in ("/api/overview", "/api/health") and not self.path.startswith("/api/operations/"):
                super().log_message(format, *args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    service.cli.endpoint = f"http://127.0.0.1:{server.server_port}"
    service.scheduler.start()
    service.feishu.cards.start()
    return server


def main():
    """启动独立项目并安装可平滑退出的信号处理器。"""
    parser = argparse.ArgumentParser(description="独立 Skill 平台：标准库、本地服务、无 Docker")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--data", default="data", help="项目内相对数据目录")
    args = parser.parse_args()
    data = safe_path(ROOT, args.data)
    service = Service(ROOT, data)
    server = make_server(service, args.port)
    def stop(signum, frame):
        """保持 HTTP 桥接可用直到执行线程退出，避免关闭时 CLI 回执丢失。"""
        def finish():
            """停止领取并等待有界在途请求，然后再关闭 HTTP 主循环。"""
            service.close()
            server.shutdown()
        threading.Thread(target=finish, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Skill 平台：http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
