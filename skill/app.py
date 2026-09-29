"""本地 Skill 管理平台原型。

非 Docker 启动：
  - 在仓库根目录运行 ``python skill/app.py``；
  - 已进入 skill 目录时运行 ``python app.py``。
浏览器访问：http://127.0.0.1:8765
停止服务：在运行服务的终端按 Ctrl+C。
默认数据位置：skill/data/skill.db（SQLite）。
管理页面使用 Python 标准库；实际 Agent 对话需安装 requirements-agent.txt。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import threading
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
try:
    from skill.package_store import inspect_source
    from skill.package_validation import validate_package, SCHEMA_PATH
    from skill.mcp_runtime import discover, langchain_tools
    from skill.service_runtime import control, profiles
except ImportError:
    from package_store import inspect_source
    from package_validation import validate_package, SCHEMA_PATH
    from mcp_runtime import discover, langchain_tools
    from service_runtime import control, profiles

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SKILL_DB_PATH", ROOT / "data" / "skill.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
SESSION_LOCKS = {}


def session_lock(session_id):
    with LOCK:
        return SESSION_LOCKS.setdefault(session_id, threading.RLock())


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def public_report(report):
    public = json.loads(json.dumps(report, ensure_ascii=False))
    if isinstance(public.get('manifest'), dict):
        public['manifest'].get('spec', {}).get('tools', {}).pop('mcp', None)
    return public


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
          description TEXT NOT NULL, manifest TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL,
          UNIQUE(name, version));
        CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
          node TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bindings(id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, skill_id TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, UNIQUE(agent_id, skill_id));
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, title TEXT NOT NULL,
          thread_id TEXT NOT NULL UNIQUE, summary TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
          content TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, name TEXT NOT NULL,
          instruction TEXT NOT NULL, interval_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
          last_run TEXT, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          status TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL);
        """)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(agents)")}
        for column, definition in {
            "model": "TEXT NOT NULL DEFAULT ''", "base_url": "TEXT NOT NULL DEFAULT ''",
            "api_key_env": "TEXT NOT NULL DEFAULT ''", "api_key": "TEXT NOT NULL DEFAULT ''",
            "system_prompt": "TEXT NOT NULL DEFAULT ''",
            "temperature": "REAL NOT NULL DEFAULT 0.7", "max_tokens": "INTEGER NOT NULL DEFAULT 2048",
        }.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE agents ADD COLUMN {column} {definition}")
        skill_columns = {r[1] for r in conn.execute("PRAGMA table_info(skills)")}
        for column in ('package_path', 'digest', 'validation_report', 'runtime_config', 'mcp_token'):
            if column not in skill_columns:
                conn.execute(f"ALTER TABLE skills ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE skills SET enabled=0 WHERE package_path=''")


def rows(table):
    with db() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY created DESC")]


def get(table, item_id):
    with db() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None


def validate_manifest(m):
    if not isinstance(m, dict):
        raise ValueError("manifest 必须是 JSON 对象")
    name, version = m.get("name"), m.get("version")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", name):
        raise ValueError("name 需为小写字母、数字和连字符")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version 需为 x.y.z")
    services = m.get("services")
    if not isinstance(services, list):
        raise ValueError("services 必须显式声明为数组，可为空")
    ids = set()
    for s in services:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or s["id"] in ids:
            raise ValueError("服务 id 缺失或重复")
        ids.add(s["id"])
        if not s.get("image") or not s.get("digest") or not s.get("target"):
            raise ValueError(f"服务 {s['id']} 缺少 image、digest 或 target")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", s["digest"]):
            raise ValueError(f"服务 {s['id']} digest 格式错误")
    return name, version


def validate_agent_config(data):
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Agent 名称不能为空")
    model = str(data.get("model", "")).strip()
    base_url = str(data.get("base_url", "")).strip()
    api_key_env = str(data.get("api_key_env", "")).strip()
    if api_key_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", api_key_env):
        raise ValueError("密钥环境变量名格式错误")
    if base_url and not (base_url.startswith("https://") or base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")):
        raise ValueError("模型接口须使用 HTTPS；本机地址可使用 HTTP")
    temperature = float(data.get("temperature", 0.7))
    max_tokens = int(data.get("max_tokens", 2048))
    if not 0 <= temperature <= 2 or not 1 <= max_tokens <= 32768:
        raise ValueError("温度或最大输出 token 超出范围")
    return (name, str(data.get("description", "")), str(data.get("node", "local")),
            model, base_url, api_key_env, str(data.get("system_prompt", "")), temperature, max_tokens)


def agent_status(agent):
    if not agent["model"]:
        return "unconfigured"
    if not agent.get("api_key") and not os.environ.get(agent["api_key_env"]):
        return "missing-secret"
    return "ready"


def save_agent_key(conn, agent_id, data):
    value = data.get("api_key")
    if value is None or value == "":
        return  # Empty input preserves the saved key during ordinary edits.
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise ValueError("API Key 格式错误")
    conn.execute("UPDATE agents SET api_key=? WHERE id=?", (value.strip(), agent_id))


def invoke_agent(agent, session, content):
    """Use a real LangChain agent. Persisted message rows provide restart-safe history."""
    if agent_status(agent) != "ready":
        raise ValueError("Agent 模型或密钥未配置；请先完成 Agent API 设置")
    try:
        from langchain.agents import create_agent
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ValueError("缺少 LangChain 依赖；请安装 skill/requirements-agent.txt") from exc
    with db() as conn:
        history = [dict(r) for r in conn.execute(
            "SELECT role,content FROM messages WHERE session_id=? ORDER BY created,id", (session["id"],))]
        skills = [dict(r) for r in conn.execute("""
            SELECT s.*,b.id AS binding_id FROM skills s
            JOIN bindings b ON b.skill_id=s.id WHERE b.agent_id=? AND b.enabled=1 AND s.enabled=1
            ORDER BY s.name
        """, (agent["id"],))]
    instructions = [agent["system_prompt"] or agent["description"] or "你是一个工业场景助手。"]
    if skills:
        instructions.append("已绑定的 Skill 能力说明：")
        for skill in skills:
            instructions.append(f"- {skill['name']} {skill['version']}: {skill['description']}")
    tools = []
    for skill in skills:
        if skill['manifest'] and json.loads(skill['manifest'])['spec']['tools'].get('mcp'):
            def active(binding_id):
                binding = get('bindings', binding_id)
                return bool(binding and binding['enabled'] and get('skills', binding['skill_id'])['enabled'])
            loaded, unavailable = langchain_tools(skill, skill['binding_id'], active)
            tools.extend(loaded)
            if unavailable:
                instructions.append('未就绪工具：' + ', '.join(x['name'] for x in unavailable))
        else:
            instructions.append(f"{skill['name']} 尚未配置 MCP 工具入口，不得声称已执行其业务功能。")
    model_args = {"model": agent["model"], "api_key": agent.get("api_key") or os.environ[agent["api_key_env"]],
                  "temperature": agent["temperature"], "max_tokens": agent["max_tokens"], "timeout": 60}
    if agent["base_url"]:
        model_args["base_url"] = agent["base_url"]
    model = ChatOpenAI(**model_args)
    runtime = create_agent(model=model, tools=tools, system_prompt="\n".join(instructions))
    messages = [{"role": row["role"], "content": row["content"]} for row in history]
    messages.append({"role": "user", "content": content})
    result = runtime.invoke({"messages": messages}, config={"configurable": {"thread_id": session["thread_id"]}})
    response = result["messages"][-1]
    return str(response.content) if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 70 * 1024 * 1024:
            raise ValueError("请求过大")
        data = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是对象")
        return data

    def route(self, method):
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        if path == "/":
            content = (ROOT / "web" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if parts[:1] != ["api"]:
            return self.send(404, {"error": "未找到"})
        if method == 'GET' and parts == ['api','skills','schema']:
            return self.send(200, json.loads(SCHEMA_PATH.read_text(encoding='utf-8')))
        if method == "GET" and parts == ["api", "overview"]:
            agents = rows("agents")
            for agent in agents:
                agent["runtime_status"] = agent_status(agent)
                agent["api_key_configured"] = bool(agent.pop("api_key", "") or os.environ.get(agent["api_key_env"]))
                agent.pop("api_key_env", None)
            skills = rows('skills')
            for skill in skills:
                skill.pop('mcp_token', None)
                public_manifest = json.loads(skill['manifest'])
                mcp = public_manifest['spec']['tools'].pop('mcp', None)
                skill['mcp_token_configured'] = bool(mcp and (os.environ.get(mcp['auth']['secretEnv']) or mcp['auth'].get('token')))
                skill['manifest'] = json.dumps(public_manifest, ensure_ascii=False)
                if skill.get('validation_report'):
                    skill['validation_report'] = json.dumps(public_report(json.loads(skill['validation_report'])), ensure_ascii=False)
            return self.send(200, {"skills": skills, "agents": agents,
                                   "bindings": rows("bindings"), "sessions": rows("sessions"),
                                   "schedules": rows("schedules"), "runs": rows("runs")})
        if method == "GET" and parts == ["api", "docker"]:
            p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=5)
            return self.send(200, {"available": p.returncode == 0, "version": p.stdout.strip(), "error": p.stderr.strip()})
        if method == "GET" and len(parts) == 4 and parts[1] == "sessions" and parts[3] == "messages":
            with db() as conn:
                records = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created", (parts[2],))]
            return self.send(200, records)
        if method == "GET" and len(parts) == 4 and parts[1] == "skills" and parts[3] == "tools":
            skill = get('skills', parts[2])
            if not skill:
                return self.send(404, {'error': 'Skill 不存在'})
            if not json.loads(skill['manifest'])['spec']['tools'].get('mcp'):
                return self.send(200, {'available': [], 'unavailable': [], 'protocol': '未声明 MCP'})
            matched, unavailable = discover(skill)
            return self.send(200, {'available': [tool['name'] for tool, _ in matched],
                                   'unavailable': unavailable, 'protocol': 'mcp'})
        if method == 'GET' and len(parts) == 4 and parts[1] == 'skills' and parts[3] == 'status':
            skill = get('skills', parts[2])
            if not skill:
                return self.send(404, {'error': 'Skill 不存在'})
            profile_id = json.loads(skill.get('runtime_config') or '{}').get('deploymentProfile') or profiles(skill)[0]['id']
            services = control(skill, profile_id, 'status')
            matched, unavailable = discover(skill) if services['healthy'] else ([], [])
            return self.send(200, {'services': services, 'mcpHealthy': bool(matched),
                                   'availableTools': [t['name'] for t, _ in matched], 'unavailableTools': unavailable})
        data = self.body() if method in ("POST", "PATCH") else {}
        if method == 'POST' and parts == ['api','skills','validate']:
            report, _ = inspect_source(data, ROOT.parent, DB_PATH.parent/'packages')
            return self.send(200, public_report(report))
        if method == 'POST' and len(parts) == 4 and parts[1] == 'skills' and parts[3] == 'validate':
            item = get('skills', parts[2])
            if not item or not item['package_path']:
                raise ValueError('旧版记录没有实际包文件，请重新导入完整 Skill 包')
            report = validate_package(item['package_path'], data.get('configuration'))
            with db() as conn:
                conn.execute('UPDATE skills SET validation_report=?,enabled=0 WHERE id=?', (json.dumps(report,ensure_ascii=False),parts[2]))
            return self.send(200, public_report(report))
        if method == "POST" and parts == ["api", "skills"]:
            report, package_path = inspect_source(data, ROOT.parent, DB_PATH.parent/'packages', install=True)
            if not report['valid']:
                return self.send(422, {'error':'Skill 包校验失败', 'report':public_report(report)})
            manifest = report['manifest']
            config = data.get('configuration') or {}
            runtime_config = {}
            token = ''
            item = (uid(), report['name'], report['version'], report['description'], json.dumps(manifest), now(),package_path,report['digest'],json.dumps(report,ensure_ascii=False),json.dumps(runtime_config),token)
            with db() as conn:
                old = conn.execute('SELECT digest FROM skills WHERE name=? AND version=?',(report['name'],report['version'])).fetchone()
                if old:
                    raise ValueError('同版本包已导入' if old['digest']==report['digest'] else '同版本内容不一致，请提升版本号')
                conn.execute("INSERT INTO skills(id,name,version,description,manifest,created,package_path,digest,validation_report,runtime_config,mcp_token,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)", item)
            return self.send(201, {"id": item[0], 'report':public_report(report)})
        if method == "POST" and parts == ["api", "agents"]:
            fields = validate_agent_config(data)
            item = (uid(), fields[0], fields[1], fields[2], now(), *fields[3:])
            with db() as conn:
                conn.execute("""INSERT INTO agents(id,name,description,node,created,model,base_url,api_key_env,
                  system_prompt,temperature,max_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", item)
                save_agent_key(conn, item[0], data)
            return self.send(201, {"id": item[0]})
        if method == "PATCH" and len(parts) == 3 and parts[1] == "agents":
            old = get("agents", parts[2])
            if not old:
                raise ValueError("Agent 不存在")
            merged = {**old, **data}
            fields = validate_agent_config(merged)
            with db() as conn:
                conn.execute("""UPDATE agents SET name=?,description=?,node=?,model=?,base_url=?,api_key_env=?,
                    system_prompt=?,temperature=?,max_tokens=? WHERE id=?""", (*fields, parts[2]))
                save_agent_key(conn, parts[2], data)
            return self.send(200, {"ok": True, "runtime_status": agent_status(get("agents", parts[2]))})
        if method == "POST" and len(parts) == 4 and parts[1] == "agents" and parts[3] == "test":
            agent = get("agents", parts[2])
            if not agent:
                raise ValueError("Agent 不存在")
            probe = {"id": uid(), "thread_id": uid()}
            answer = invoke_agent(agent, probe, str(data.get("prompt", "请简短回复：连接成功")))
            return self.send(200, {"ok": True, "answer": answer})
        if method == "POST" and parts == ["api", "bindings"]:
            if not get("agents", data.get("agent_id")) or not get("skills", data.get("skill_id")):
                raise ValueError("Agent 或 Skill 不存在")
            skill = get('skills',data['skill_id'])
            if not skill['enabled']:
                raise ValueError('Skill 尚未通过部署依赖校验并启用')
            item = (uid(), data["agent_id"], data["skill_id"], 1, now())
            with db() as conn:
                conn.execute("INSERT INTO bindings VALUES(?,?,?,?,?)", item)
            return self.send(201, {"id": item[0]})
        if method == "POST" and parts == ["api", "sessions"]:
            if not get("agents", data.get("agent_id")):
                raise ValueError("Agent 不存在")
            item = (uid(), data["agent_id"], str(data.get("title", "新会话")), uid(), "", now())
            with db() as conn:
                conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", item)
            return self.send(201, {"id": item[0], "thread_id": item[3]})
        if method == "POST" and len(parts) == 4 and parts[1] == "sessions" and parts[3] == "messages":
            with session_lock(parts[2]):
                session = get("sessions", parts[2])
                if not session:
                    raise ValueError("会话不存在")
                content = str(data.get("content", "")).strip()
                if not content:
                    raise ValueError("内容不能为空")
                agent = get("agents", session["agent_id"])
                answer = invoke_agent(agent, session, content)
                item = (uid(), parts[2], "user", content, now())
                reply = (uid(), parts[2], "assistant", answer, now())
                with db() as conn:
                    conn.execute("INSERT INTO messages VALUES(?,?,?,?,?)", item)
                    conn.execute("INSERT INTO messages VALUES(?,?,?,?,?)", reply)
                return self.send(201, {"id": reply[0], "content": answer, "thread_id": session["thread_id"]})
        if method == "POST" and len(parts) == 4 and parts[1] == "sessions" and parts[3] == "compact":
            if not get("sessions", parts[2]):
                raise ValueError("会话不存在")
            return self.send(501, {"error": "尚未连接 LangGraph checkpointer，不能安全压缩上下文"})
        if method == "POST" and parts == ["api", "schedules"]:
            if not get("agents", data.get("agent_id")):
                raise ValueError("Agent 不存在")
            interval = int(data.get("interval_seconds", 0))
            if interval < 60:
                raise ValueError("最小间隔为 60 秒")
            item = (uid(), data["agent_id"], str(data.get("name", "定时任务")), str(data.get("instruction", "")), interval, 0, None, now())
            with db() as conn:
                conn.execute("INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?)", item)
            return self.send(201, {"id": item[0]})
        if method == "POST" and len(parts) == 4 and parts[1] == "schedules" and parts[3] == "run":
            schedule = get("schedules", parts[2])
            if not schedule:
                raise ValueError("计划不存在")
            run = (uid(), parts[2], uid(), "awaiting-agent-runtime", "", now())
            with db() as conn:
                conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", run)
            return self.send(202, {"run_id": run[0], "thread_id": run[2], "status": run[3]})
        if method == "PATCH" and len(parts) == 3 and parts[1] in ("skills", "bindings", "schedules"):
            table = parts[1]
            if not get(table, parts[2]):
                raise ValueError("对象不存在")
            if data.get('enabled') and table in ('skills','bindings'):
                target = get(table, parts[2])
                skill = target if table=='skills' else get('skills',target['skill_id'])
                if not skill['package_path']:
                    raise ValueError('旧版记录必须重新导入完整 Skill 包')
                report = validate_package(skill['package_path'], data.get('configuration'))
                if not report['valid']:
                    return self.send(409, {'error':'Skill 包结构校验失败', 'report':report})
                if table == 'skills':
                    profile_id = data.get('deployment_profile') or profiles(skill)[0]['id']
                    control(skill, profile_id, 'start')
                    with db() as conn:
                        conn.execute('UPDATE skills SET runtime_config=? WHERE id=?',
                                     (json.dumps({'deploymentProfile': profile_id}), parts[2]))
                if json.loads(skill['manifest'])['spec']['tools'].get('mcp'):
                    try:
                        matched, unavailable = discover(skill)
                    except Exception as exc:
                        if table == 'skills':
                            control(skill, profile_id, 'stop')
                        return self.send(409, {'error':'MCP 服务不可用：' + str(exc)})
                    if not matched:
                        if table == 'skills':
                            control(skill, profile_id, 'stop')
                        return self.send(409, {'error':'MCP 服务没有与 Skill 声明匹配的工具', 'unavailable':unavailable})
                    if table == 'skills':
                        with db() as conn:
                            conn.execute('UPDATE skills SET validation_report=? WHERE id=?',
                                         (json.dumps(report,ensure_ascii=False),parts[2]))
            if table == 'skills' and data.get('enabled') is False:
                skill = get('skills', parts[2])
                profile_id = json.loads(skill.get('runtime_config') or '{}').get('deploymentProfile')
                if profile_id:
                    control(skill, profile_id, 'stop')
            with db() as conn:
                conn.execute(f"UPDATE {table} SET enabled=? WHERE id=?", (int(bool(data.get("enabled"))), parts[2]))
            return self.send(200, {"ok": True})
        if method == "DELETE" and len(parts) == 3 and parts[1] == "bindings":
            with db() as conn:
                cursor = conn.execute("DELETE FROM bindings WHERE id=?", (parts[2],))
            return self.send(200 if cursor.rowcount else 404, {"ok": bool(cursor.rowcount)})
        if method == "DELETE" and len(parts) == 3 and parts[1] == "skills":
            skill = get("skills", parts[2])
            if not skill:
                return self.send(404, {"error": "Skill 记录不存在"})
            if skill["enabled"]:
                return self.send(409, {"error": "请先停用 Skill，再删除记录"})
            with db() as conn:
                binding_count = conn.execute("SELECT COUNT(*) FROM bindings WHERE skill_id=?", (parts[2],)).fetchone()[0]
                if binding_count:
                    return self.send(409, {"error": f"此 Skill 仍被 {binding_count} 个 Agent 绑定，请先在 Agent 管理中解除绑定"})
                conn.execute("DELETE FROM skills WHERE id=?", (parts[2],))
            return self.send(200, {"ok": True, "note": "已删除平台记录；项目源目录与包快照保留"})
        if method == "DELETE" and len(parts) == 3 and parts[1] == "sessions":
            with session_lock(parts[2]):
                with db() as conn:
                    conn.execute("DELETE FROM messages WHERE session_id=?", (parts[2],))
                    conn.execute("DELETE FROM sessions WHERE id=?", (parts[2],))
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "未找到"})

    def handle_request(self, method):
        try:
            self.route(method)
        except (ValueError, sqlite3.IntegrityError) as e:
            self.send(400, {"error": str(e)})
        except FileNotFoundError as e:
            if urlparse(self.path).path == '/api/docker':
                self.send(200, {"available": False, "error": "Docker CLI 未安装"})
            else:
                self.send(400, {"error": f"所需文件不存在：{e.filename or str(e)}"})
        except Exception as e:
            traceback.print_exc()
            path = urlparse(self.path).path
            if path in ('/api/skills', '/api/skills/validate'):
                self.send(500, {"error": f"Skill 包处理失败（{type(e).__name__}）：{e}"})
            elif path.startswith('/api/agents/') and path.endswith('/test'):
                kind = type(e).__name__
                if kind == 'APIConnectionError':
                    self.send(503, {"error": "无法连接模型接口；请检查平台进程的网络权限、代理和 Base URL"})
                elif getattr(e, 'status_code', None):
                    self.send(502, {"error": f"模型接口返回 HTTP {e.status_code}（{kind}）；请检查模型名称、API Key 和 Base URL"})
                else:
                    self.send(500, {"error": f"模型测试失败（{kind}）；请查看服务端日志"})
            else:
                self.send(500, {"error": "服务执行失败，请检查服务端日志"})

    def do_GET(self): self.handle_request("GET")
    def do_POST(self): self.handle_request("POST")
    def do_PATCH(self): self.handle_request("PATCH")
    def do_DELETE(self): self.handle_request("DELETE")


if __name__ == "__main__":
    init_db()
    host = os.environ.get("SKILL_HOST", "127.0.0.1")
    port = int(os.environ.get("SKILL_PORT", "8765"))
    print(f"Skill platform: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
