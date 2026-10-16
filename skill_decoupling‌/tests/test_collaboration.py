"""统一 CLI 与固定 Agent 协作的集成测试。

沿用本地 HTTP 模拟服务，但每个测试有独立平台数据库。模型响应可替换为
确定性任务计划，真实执行仍经过 HTTP、真实 CLI 子进程及 SQLite 状态转换。
不连接真实模型、不发送公网飞书消息，也不生成任何 Agent 定义。
"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_platform as fixtures
from platform_core.common import Problem, digest, dumps, now
from platform_core.task_store import TaskStore


class CollaborationTests(unittest.TestCase):
    """验证真实命令路由、等待释放、授权隔离和故障状态。"""

    # 复用测试服务装配函数，不继承整个回归类，避免重复执行全部旧测试。
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown
    api = fixtures.PlatformTests.api
    connection = fixtures.PlatformTests.connection

    def external_cli_definition(self):
        """创建隔离的真实 CLI 测试程序，不访问用户文件或网络。"""
        script = Path(self.temp.name) / "external_cli_fixture.py"
        script.write_text('import json,sys,time\nmode=sys.argv[1]\n'
                          'if mode=="sleep": time.sleep(3)\n'
                          'if mode=="exit": sys.exit(7)\n'
                          'if mode=="bad": print("not-json"); sys.exit(0)\n'
                          'value=json.load(sys.stdin)\nprint(json.dumps({"text":value["text"]},ensure_ascii=False))\n', encoding="utf-8")
        schema = {"type":"object", "properties":{"text":{"type":"string"}}, "required":["text"], "additionalProperties":False}
        data = {"name":"测试 CLI", "description":"测试 stdin/stdout 契约", "executable":sys.executable,
                "working_directory":self.temp.name, "timeout":1,
                "tools":[{"name":name, "description":name, "arguments":[str(script),name],
                          "input_schema":schema, "output_schema":schema} for name in ["echo","sleep","exit","bad"]]}
        return data

    def test_external_cli_real_execution_authorization_and_revision(self):
        """Docker CLI single Tool, actual gate/revision; container execution mocked separately."""
        data={"name":"Docker fixture","description":"echo","container":"fixture","executable":"python","working_directory":"/app","commands":{"echo":["echo"]}}
        provider=self.service.external_cli.save(data)
        agent=self.agent("P1")
        binding=self.service.agents.bind({"agent_id":agent["id"],"source":"cli:"+provider["id"],"tools":["execute"]})
        _,mapping=self.service.agents.catalog(agent)
        item=next(v for v in mapping.values() if v.get("binding_id")==binding["id"])
        with patch.object(self.service.external_cli,"run",return_value={"text":"中文"}) as run:
            result=self.service.agents.execute(agent["id"],item,{"command":"echo","input":{"text":"中文"}},"test")
            self.assertEqual(result["structuredContent"],{"text":"中文"});run.assert_called_once()
        self.service.external_cli.save({"description":"changed"},provider["id"])
        with self.assertRaises(Problem) as error: self.service.agents.execute(agent["id"],item,{"command":"echo"},"test")
        self.assertEqual(error.exception.code,"BINDING_STALE")

    def test_external_cli_failures_and_remote_boundary(self):
        """Old host execution and remote Docker are both rejected before program launch."""
        with self.assertRaises(Problem): self.service.external_cli.save(self.external_cli_definition())
        for target in ["platform-local","remote","docker-remote"]:
            with self.assertRaises(Problem) as error: self.service.external_cli.save({"execution_target":target})
            self.assertEqual(error.exception.code,"CLI_REMOTE_NOT_IMPLEMENTED")

    def agent(self, name, **extras):
        """管理员预先定义固定 Agent，所有模型请求仅指向模拟器。"""
        return self.service.agents.save({"name": name, "model": name, "api_key": "test-key",
            "base_url": self.upstream.base + "/v1", **extras})

    def grant(self, caller, target, permissions=None):
        """为当前测试明确建立有方向权限，拒绝依赖默认全开放行为。"""
        return self.service.tasks.grant({"caller_agent_id": caller["id"], "target_agent_id": target["id"],
            "permissions": permissions or ["submit", "status", "result", "overview", "cancel"]})

    def tool_call(self, name, arguments, id="call"):
        """生成标准模型工具消息，CLI 参数来自 JSON 而非 shell 字符串。"""
        return {"role": "assistant", "content": "", "tool_calls": [{"id": id, "type": "function",
            "function": {"name": name, "arguments": dumps(arguments)}}]}

    def wait(self, task):
        """有界等待终态，失败保留完整任务以便诊断。"""
        result = self.service.tasks.wait(task["id"], 15)
        self.assertIn(result["status"], {"succeeded", "failed", "cancelled", "interrupted"})
        return result

    def collaboration_model(self, caller, child):
        """主 Agent 提交后等待，子 Agent 延迟完成，模型历史用于恢复后的决策。"""
        def model(payload):
            """通过真实模型 HTTP 请求观察是否重新发送了 submit_task。"""
            if payload["model"] == child["model"]:
                time.sleep(.35)
                return {"role": "assistant", "content": "子任务已经完成"}
            tools = [m for m in payload["messages"] if m["role"] == "tool"]
            if not tools:
                return self.tool_call("submit_task", {"agent_id": child["id"], "description": "查询数据"}, "submit")
            child_task = json.loads(tools[0]["content"])["structuredContent"]["task_id"]
            if len(tools) == 1:
                return self.tool_call("wait_tasks", {"task_ids": [child_task]}, "wait")
            self.assertIn("子任务已经完成", tools[-1]["content"])
            return {"role": "assistant", "content": "主 Agent 汇总完成"}
        self.upstream.model_reply = model

    def test_parent_wait_releases_single_worker_and_resumes(self):
        """只有一个执行槽也能完成父子任务，真实 CLI 等待不占住槽位。"""
        main, child = self.agent("主 Agent"), self.agent("P7")
        self.grant(main, child)
        self.service.scheduler.workers = 1
        self.collaboration_model(main, child)
        _, task = self.api("/api/tasks", {"agent_id": main["id"], "description": "查询并汇总"})
        result = self.wait(task)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(len(self.service.tasks.repo.list()), 2)
        states = [e["status"] for e in self.service.tasks.repo.events(task["id"])]
        self.assertIn("waiting", states)
        self.assertTrue(all(r["transport"] == "langchain-tool" for r in self.service.store.list("tool_runs")))
        self.assertEqual(len(self.service.store.list("agents")), 2)
        artifact = self.api("/api/tasks/" + task["id"] + "/artifacts/0")
        self.assertEqual(artifact[1]["content"], "主 Agent 汇总完成")
        _, detail = self.api("/api/tasks/" + task["id"])
        self.assertNotIn("test-key", dumps(detail))
        self.assertNotIn("secret_ref", dumps(detail))

    def test_dependencies_deliver_results_and_preserve_order(self):
        """后续 P 只能在前置成功后启动，并实际收到前置成果。"""
        main, first, second = self.agent("主"), self.agent("P1"), self.agent("P2")
        self.grant(main, first)
        self.grant(main, second)
        def model(payload):
            """计划两条依赖子任务，检验第二个执行者收到输入成果。"""
            if payload["model"] == first["model"]:
                return {"role": "assistant", "content": "风险数据A"}
            if payload["model"] == second["model"]:
                self.assertIn("风险数据A", dumps(payload["messages"]))
                return {"role": "assistant", "content": "分析完成"}
            tools = [m for m in payload["messages"] if m["role"] == "tool"]
            if not tools:
                return self.tool_call("submit_task", {"agent_id": first["id"], "description": "获取数据"}, "one")
            a = json.loads(tools[0]["content"])["structuredContent"]["id"]
            if len(tools) == 1:
                return self.tool_call("submit_task", {"agent_id": second["id"], "description": "分析", "depends_on": [a]}, "two")
            b = json.loads(tools[1]["content"])["structuredContent"]["id"]
            if len(tools) == 2:
                return self.tool_call("wait_tasks", {"task_ids": [a, b]}, "wait")
            return {"role": "assistant", "content": "汇总"}
        self.upstream.model_reply = model
        task = self.service.tasks.submit(main["id"], "顺序执行")
        self.assertEqual(self.wait(task)["status"], "succeeded")
        children = [t for t in self.service.tasks.repo.list() if t["parent_task_id"] == task["id"]]
        self.assertEqual(len(children), 2)
        self.assertLessEqual(children[0]["finished"], children[1]["started"])

    def test_task_scope_recursion_and_revocation(self):
        """拒绝跨协作链读取、反向递归和运行期间撤销后的调用。"""
        main, child = self.agent("主"), self.agent("P1")
        grant = self.grant(main, child)
        self.grant(child, main)
        # 停止扫描，不让假模型自动推进本测试手动维护的状态。
        self.service.scheduler.stopping.set()
        self.service.scheduler.thread.join(2)
        root = self.service.tasks.submit(main["id"], "总任务")
        root = self.service.tasks.repo.update(root, status="running")
        sub = self.service.tasks.submit(child["id"], "子任务", parent_id=root["id"])
        sub = self.service.tasks.repo.update(sub, status="running")
        with self.assertRaises(Problem) as error:
            self.service.tasks.submit(main["id"], "递归", parent_id=sub["id"])
        self.assertEqual(error.exception.code, "TASK_RECURSION")
        other = self.service.tasks.submit(child["id"], "另一条协作链")
        visible = self.service.tasks.call(main["id"], "list_tasks", {}, root["id"])["tasks"]
        self.assertEqual({t["task_id"] for t in visible}, {root["id"], sub["id"]})
        self.assertTrue(all("result" not in t and "input" not in t for t in visible))
        # 大表查询提供文件引用，但仅状态授权不能读取成果位置。
        sub = self.service.tasks.repo.update(sub, artifacts=[{"path": "artifacts/test/result.json", "purpose": "测试成果"}])
        visible = self.service.tasks.call(main["id"], "list_tasks", {}, root["id"])["tasks"]
        self.assertEqual(next(t for t in visible if t["task_id"] == sub["id"])["artifacts"], sub["artifacts"])
        catalog, _ = self.service.agents.catalog(main)
        submit = next(t for t in catalog if t["function"]["name"] == "submit_task")
        self.assertIn('"input_schema"', submit["function"]["description"])
        self.assertIn(child["id"], submit["function"]["description"])
        self.service.tasks.grant({"permissions": ["submit", "status"]}, grant["id"])
        visible = self.service.tasks.call(main["id"], "list_tasks", {}, root["id"])["tasks"]
        self.assertNotIn("artifacts", next(t for t in visible if t["task_id"] == sub["id"]))
        self.service.tasks.grant({"permissions": ["submit", "status", "result"]}, grant["id"])
        with self.assertRaises(Problem) as error:
            self.service.tasks.view(main["id"], other["id"], "result", root["id"])
        self.assertEqual(error.exception.code, "TASK_FORBIDDEN")
        self.service.tasks.grant({"enabled": False}, grant["id"])
        with self.assertRaises(Problem) as error:
            self.service.tasks.view(main["id"], sub["id"], "status", root["id"])
        self.assertEqual(error.exception.code, "AGENT_FORBIDDEN")

    def test_task_idempotency_and_version_conflict(self):
        """同请求不重复生成任务；并发更新失败而非覆盖新状态。"""
        agent = self.agent("P1")
        self.upstream.model_reply = lambda payload: {"role": "assistant", "content": "完成"}
        first = self.service.tasks.submit(agent["id"], "任务", request_id="stable")
        again = self.service.tasks.submit(agent["id"], "任务", request_id="stable")
        self.assertEqual(first["id"], again["id"])
        with self.assertRaises(Problem) as error:
            self.service.tasks.submit(agent["id"], "不同任务", request_id="stable")
        self.assertEqual(error.exception.code, "TASK_IDEMPOTENCY_CONFLICT")
        final = self.wait(first)
        with self.assertRaises(Problem) as error:
            self.service.tasks.repo.update(first, phase="覆盖旧版本")
        self.assertEqual(error.exception.code, "TASK_CONFLICT")
        self.assertEqual(final["status"], "succeeded")

    def test_cancel_inflight_and_frontend_remain_responsive(self):
        """取消真实慢模型调用期间健康检查仍快速返回，结束不覆盖取消请求。"""
        agent = self.agent("P1")
        started = threading.Event()
        def model(payload):
            """用短延时模拟在途请求，取消不会杀死该 HTTP 服务。"""
            started.set()
            time.sleep(.5)
            return {"role": "assistant", "content": "在途调用结束"}
        self.upstream.model_reply = model
        task = self.service.tasks.submit(agent["id"], "慢任务")
        self.assertTrue(started.wait(10))
        before = time.monotonic()
        self.assertEqual(self.api("/api/health")[0], 200)
        self.assertLess(time.monotonic() - before, .5)
        requested = self.service.tasks.cancel(task["id"])
        self.assertTrue(requested["cancel_requested"])
        self.assertEqual(self.wait(task)["status"], "cancelled")

    def test_restart_marks_pending_tasks_interrupted(self):
        """重启恢复明确中断，不能自动重放可能产生副作用的旧命令。"""
        self.service.scheduler.stopping.set()
        self.service.scheduler.thread.join(2)
        agent = self.agent("P1")
        task = self.service.tasks.submit(agent["id"], "待启动")
        repo = TaskStore(self.service.store)
        self.assertEqual(repo.get(task["id"])["status"], "interrupted")

    def test_output_contract_and_input_validation(self):
        """输入错误提交前拒绝，输出错误不能记为成功。"""
        agent = self.agent("P1", input_schema={"type": "object", "properties": {"job": {"type": "string"}},
                            "required": ["job"], "additionalProperties": False},
                           output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        with self.assertRaises(Problem):
            self.service.tasks.submit(agent["id"], "输入错误", {})
        self.upstream.model_reply = lambda payload: {"role": "assistant", "content": "不是JSON"}
        task = self.service.tasks.submit(agent["id"], "输出错误", {"job": "001"})
        outcome = self.wait(task)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"]["code"], "OUTPUT_INVALID")

    def test_conversation_can_ask_missing_contract_parameters(self):
        """用户可用自然语言追问；被调用 Agent 的结构化接口仍拒绝缺失参数。"""
        agent = self.agent("P7", input_schema={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
                           output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        session = self.service.store.put("sessions", {"agent_id": agent["id"], "name": "自然语言任务"})
        self.upstream.model_reply = lambda payload: {"role": "assistant", "content": "请提供作业编号。"}
        with self.assertRaises(Problem):
            self.service.tasks.submit(agent["id"], "查询风险", {})
        task = self.service.tasks.submit(agent["id"], "查询风险", session_id=session["id"])
        outcome = self.wait(task)
        self.assertEqual(outcome["status"], "succeeded")
        self.assertEqual(outcome["result"]["content"], "请提供作业编号。")

    def test_real_cli_catalog_and_admin_permission(self):
        """独立 CLI 能运行，管理员令牌不替代 Agent 的具体工具绑定。"""
        args = [sys.executable, str(fixtures.ROOT / "cli.py"), "--endpoint", self.base]
        catalog = subprocess.run(args + ["catalog"], capture_output=True, text=True, encoding="utf-8", timeout=5)
        self.assertEqual(json.loads(catalog.stdout)["transport"], "cli")
        agent = self.agent("P1")
        command = args + ["run", "--agent", agent["id"], "--command", "feishu_send"]
        result = subprocess.run(command, input="{}", capture_output=True, text=True, encoding="utf-8", timeout=5,
                                env={**os.environ, "PLATFORM_CLI_TOKEN": self.service.cli_token, "PYTHONUTF8": "1"})
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "TOOL_FORBIDDEN")
        self.assertNotEqual(result.returncode, 0)
        connection = self.connection()
        self.service.agents.bind({"agent_id": agent["id"], "source": "builtin", "connection_ref": connection["id"],
                                  "tools": ["lookup_feishu_directory"]})
        result = subprocess.run(args + ["run", "--agent", agent["id"], "--command", "lookup_feishu_directory"],
            input=dumps({"entity_type": "group"}), capture_output=True, text=True, encoding="utf-8", timeout=5,
            env={**os.environ, "PLATFORM_CLI_TOKEN": self.service.cli_token, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("oc_test", json.loads(result.stdout)["structuredContent"]["entries"])
        self.assertEqual(self.service.store.list("tool_runs")[-1]["audit_source"], "administrator-cli")
        status, _ = self.api("/api/cli/invoke", {"ticket": "made-up", "arguments": {}})
        self.assertEqual(status, 401)

    def test_cli_ticket_is_one_use_and_parameter_bound(self):
        """票据身份与参数不能由模型替换，核销后不可重放。"""
        c = self.connection()
        agent = self.agent("P1")
        self.service.agents.bind({"agent_id": agent["id"], "source": "builtin", "connection_ref": c["id"],
                                  "tools": ["lookup_feishu_directory"]})
        _, mapping = self.service.agents.catalog(agent)
        item = next(iter(mapping.values()))
        args = {"entity_type": "group"}
        def issue(ticket):
            """构建短期测试票据，仅测试 HTTP 核销规则。"""
            self.service.cli.tickets[ticket] = {"agent_id": agent["id"], "item": item, "fingerprint": digest(args),
                                                "run_id": "test", "expires": time.monotonic() + 5}
        issue("mismatch")
        self.assertEqual(self.api("/api/cli/invoke", {"ticket": "mismatch", "arguments": {"entity_type": "user"}})[0], 401)
        issue("valid")
        self.assertEqual(self.api("/api/cli/invoke", {"ticket": "valid", "arguments": args})[0], 200)
        self.assertEqual(self.api("/api/cli/invoke", {"ticket": "valid", "arguments": args})[0], 401)

    def test_cli_external_mcp_and_direct_http_branches(self):
        """两种外部路径都经过真实 CLI，直连调用不产生任何 MCP 握手。"""
        agent = self.agent("P1")
        provider = self.service.registry.save({"name": "MCP", "endpoint": self.upstream.base + "/rpc"})
        self.service.registry.discover(provider["id"])
        self.service.agents.bind({"agent_id": agent["id"], "source": provider["id"], "tools": ["nested"]})
        _, mapping = self.service.agents.catalog(agent)
        args = {"to": {"conversation_id": "oc_demo"}}
        result = self.service.agents.execute(agent["id"], next(iter(mapping.values())), args, "test")
        self.assertTrue(result["ok"])
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        direct = self.service.direct.save({"name": "HTTP", "description": "获取测试令牌", "endpoint": self.upstream.base + "/tenant_access_token/internal", "input_schema": schema})
        self.service.agents.bind({"agent_id": agent["id"], "source": "http:" + direct["id"], "tools": ["call"]})
        _, mapping = self.service.agents.catalog(agent)
        item = next(v for v in mapping.values() if v["tool"] == "call")
        count = len(self.upstream.rpc_requests)
        result = self.service.agents.execute(agent["id"], item, {}, "direct")
        self.assertEqual(result["structuredContent"]["code"], 0)
        self.assertEqual(len(self.upstream.rpc_requests), count)
        self.service.direct.save({"description": "修订"}, direct["id"])
        with self.assertRaises(Problem) as error:
            self.service.agents.execute(agent["id"], item, {}, "direct")
        self.assertEqual(error.exception.code, "BINDING_STALE")

    def test_wait_deadline_and_dependency_failure(self):
        """等待状态也有总期限，前置失败不会启动后续业务动作。"""
        self.service.scheduler.stopping.set()
        self.service.scheduler.thread.join(2)
        main, child = self.agent("主"), self.agent("P1")
        self.grant(main, child)
        root = self.service.tasks.submit(main["id"], "总")
        root = self.service.tasks.repo.update(root, status="running")
        first = self.service.tasks.submit(child["id"], "前置", parent_id=root["id"])
        second = self.service.tasks.submit(child["id"], "后续", parent_id=root["id"], depends_on=[first["id"]])
        self.service.tasks.repo.update(first, status="failed")
        self.service.scheduler.tick()
        self.assertEqual(self.service.tasks.repo.get(second["id"])["error"]["code"], "DEPENDENCY_FAILED")
        cp = self.service.tasks.repo.checkpoint(root["id"])
        cp["wait_ids"] = [first["id"]]
        deadline = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.service.tasks.repo.update(root, cp, status="waiting", deadline=deadline)
        self.service.scheduler.tick()
        self.assertEqual(self.service.tasks.repo.get(root["id"])["error"]["code"], "TASK_TIMEOUT")

    def test_config_snapshot_and_deployment_placeholders(self):
        """已提交任务保留配置版本，分布式部署明确未实现。"""
        self.service.scheduler.stopping.set()
        self.service.scheduler.thread.join(2)
        agent = self.agent("P1", system_prompt="旧职责")
        task = self.service.tasks.submit(agent["id"], "待执行")
        self.service.agents.save({"system_prompt": "新职责"}, agent["id"])
        snapshot = self.service.tasks.repo.checkpoint(task["id"])["agent"]
        self.assertEqual(snapshot["system_prompt"], "旧职责")
        self.assertEqual(snapshot["config_version"], task["config_version"])
        self.assertEqual(self.api("/api/agents/" + agent["id"] + "/deploy", {})[0], 501)
        self.assertIn({"id": "distributed", "supported": False}, self.service.overview()["execution_targets"])

    def test_direct_get_maps_parameters_without_mcp(self):
        """GET 直连通过真实 CLI 传递查询参数，不误发 POST 或携带模型 URL。"""
        agent = self.agent("P1")
        definition = self.service.direct.save({"name": "GET", "description": "查询", "method": "GET",
            "endpoint": self.upstream.base + "/query", "input_schema": {"type": "object", "properties": {
                "job": {"type": "string"}}, "required": ["job"], "additionalProperties": False}})
        self.service.agents.bind({"agent_id": agent["id"], "source": "http:" + definition["id"], "tools": ["call"]})
        _, mapping = self.service.agents.catalog(agent)
        result = self.service.agents.execute(agent["id"], next(iter(mapping.values())), {"job": "作业001"}, "get")
        self.assertEqual(result["structuredContent"]["query"]["job"], ["作业001"])
        self.assertEqual(self.upstream.rpc_requests, [])

    def test_feishu_inbound_optional_agent_trigger_deduplicates(self):
        """验证回调仅在连接明确绑定后触发，重复投递不重复创建任务或发送消息。"""
        agent = self.agent("主 Agent")
        self.upstream.model_reply = lambda payload: {"role": "assistant", "content": "收到入站消息"}
        connection = self.connection()
        self.service.feishu.save({"inbound_agent_id": agent["id"]}, connection["id"])
        payload = {"header": {"event_type": "im.message.receive_v1", "token": "fake-verification", "event_id": "same"},
            "event": {"message": {"message_id": "msg", "chat_id": "oc_test", "content": dumps({"text": "你好"})},
                      "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_test"}}}}
        self.assertEqual(self.api(connection["webhook_path"], payload)[0], 200)
        self.assertEqual(self.api(connection["webhook_path"], payload)[0], 200)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.service.tasks.repo.list():
            time.sleep(.05)
        tasks = self.service.tasks.repo.list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(self.wait(tasks[0])["status"], "succeeded")
        self.assertEqual(self.upstream.sent, [])

    def test_cancel_waiting_session_closes_tool_messages(self):
        """父任务等待时取消，已保存会话中每条工具调用都有失败回执。"""
        main, child = self.agent("主"), self.agent("P1")
        self.grant(main, child)
        def model(payload):
            """子任务延迟，留出取消父任务 waiting 的窗口。"""
            if payload["model"] == child["model"]:
                time.sleep(1)
                return {"role": "assistant", "content": "完成"}
            tools = [m for m in payload["messages"] if m["role"] == "tool"]
            if not tools:
                return self.tool_call("submit_task", {"agent_id": child["id"], "description": "子任务"}, "submit")
            task_id = json.loads(tools[0]["content"])["structuredContent"]["id"]
            return self.tool_call("wait_tasks", {"task_ids": [task_id]}, "wait")
        self.upstream.model_reply = model
        session = self.service.store.put("sessions", {"agent_id": main["id"], "name": "取消测试"})
        task = self.service.tasks.submit(main["id"], "协作", session_id=session["id"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.service.tasks.repo.get(task["id"])["status"] != "waiting":
            time.sleep(.03)
        self.assertEqual(self.service.tasks.repo.get(task["id"])["status"], "waiting")
        self.service.tasks.cancel(task["id"])
        history = self.service.store.messages(session["id"])
        call_ids = {c["id"] for m in history for c in m.get("tool_calls", [])}
        reply_ids = {m["tool_call_id"] for m in history if m["role"] == "tool"}
        self.assertEqual(call_ids, reply_ids)
        self.assertIn("TASK_CANCELLED", history[-1]["content"])
