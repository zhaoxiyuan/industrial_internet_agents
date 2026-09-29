import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from skill.test_package_validation import fixture
from skill.package_validation import write_lock
import yaml


class ApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["SKILL_DB_PATH"] = str(Path(cls.temp.name) / "test.db")
        spec = importlib.util.spec_from_file_location("skill_app", Path(__file__).with_name("app.py"))
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.app.ROOT = Path(cls.temp.name)/'skill'
        fixture(Path(cls.temp.name)/'demo')
        cls.app.init_db()
        cls.control_patch = patch.object(cls.app, 'control', return_value={'healthy': True})
        cls.discover_patch = patch.object(cls.app, 'discover', return_value=([({'name':'sample'}, object())], []))
        cls.control_patch.start()
        cls.discover_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.control_patch.stop()
        cls.discover_patch.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path, method="GET", data=None):
        payload = json.dumps(data).encode() if data is not None else None
        req = Request(self.base + path, data=payload, method=method,
                      headers={"Content-Type": "application/json"})
        with urlopen(req) as response:
            return json.load(response)

    def test_create_agent_with_model_config_in_one_request(self):
        created = self.request('/api/agents', 'POST', {
            'name': '页面内创建测试', 'node': 'local', 'model': 'test-model',
            'base_url': 'https://example.invalid/v1', 'api_key': 'test-key-not-a-real-secret',
            'system_prompt': '测试职责', 'temperature': 0.5, 'max_tokens': 1024})
        saved = self.app.get('agents', created['id'])
        self.assertEqual(saved['model'], 'test-model')
        self.assertEqual(saved['api_key'], 'test-key-not-a-real-secret')
        self.assertEqual(saved['system_prompt'], '测试职责')
        overview = self.request('/api/overview')
        self.assertNotIn('test-key-not-a-real-secret', json.dumps(overview))

    def test_create_bind_chat_and_schedule(self):
        skill = self.request("/api/skills", "POST", {"path":"demo", "configuration":{}})
        self.request(f"/api/skills/{skill['id']}", "PATCH", {"enabled":True,"configuration":{}})
        agent = self.request("/api/agents", "POST", {"name": "值班助手", "node": "local"})
        self.request("/api/bindings", "POST", {"agent_id": agent["id"], "skill_id": skill["id"]})
        self.request(f"/api/agents/{agent['id']}", "PATCH", {
            "model": "test-model", "api_key": "test-only-not-a-real-secret", "system_prompt": "测试助手"})
        overview = self.request("/api/overview")
        public_agent = next(a for a in overview["agents"] if a["id"] == agent["id"])
        self.assertTrue(public_agent["api_key_configured"])
        self.assertNotIn("api_key", public_agent)
        self.assertNotIn("test-only-not-a-real-secret", json.dumps(overview))
        self.request(f"/api/agents/{agent['id']}", "PATCH", {"api_key": "", "temperature": 0.5})
        self.assertEqual(self.app.get("agents", agent["id"])["api_key"], "test-only-not-a-real-secret")
        with patch.object(self.app, "invoke_agent", return_value="连接成功"):
            probe = self.request(f"/api/agents/{agent['id']}/test", "POST", {})
        self.assertEqual(probe["answer"], "连接成功")
        session = self.request("/api/sessions", "POST", {"agent_id": agent["id"]})
        with patch.object(self.app, "invoke_agent", return_value="已收到"):
            self.request(f"/api/sessions/{session['id']}/messages", "POST", {"content": "检查现场"})
        messages = self.request(f"/api/sessions/{session['id']}/messages")
        self.assertEqual(messages[0]["content"], "检查现场")
        self.assertEqual(messages[1]["content"], "已收到")
        schedule = self.request("/api/schedules", "POST", {
            "agent_id": agent["id"], "name": "巡检", "interval_seconds": 3600})
        first = self.request(f"/api/schedules/{schedule['id']}/run", "POST", {})
        second = self.request(f"/api/schedules/{schedule['id']}/run", "POST", {})
        self.assertNotEqual(first["thread_id"], second["thread_id"])
        self.assertNotEqual(first["thread_id"], session["thread_id"])

    def test_delete_skill_requires_unbind_and_keeps_source(self):
        source = Path(self.temp.name) / "removable"
        manifest = fixture(source)
        manifest["metadata"]["name"] = "removable-skill"
        (source / "skill-package.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        (source / "SKILL.md").write_text("---\nname: removable-skill\ndescription: Removal test\n---\nInstructions.\n", encoding="utf-8")
        write_lock(source)
        skill = self.request("/api/skills", "POST", {"path": "removable", "configuration": {}})
        self.request(f"/api/skills/{skill['id']}", "PATCH", {"enabled": True, "configuration": {}})
        agent = self.request("/api/agents", "POST", {"name": "删除保护测试"})
        binding = self.request("/api/bindings", "POST", {"agent_id": agent["id"], "skill_id": skill["id"]})
        with self.assertRaises(HTTPError) as rejected:
            self.request(f"/api/skills/{skill['id']}", "DELETE")
        self.assertEqual(rejected.exception.code, 409)
        self.request(f"/api/bindings/{binding['id']}", "DELETE")
        with self.assertRaises(HTTPError) as still_enabled:
            self.request(f"/api/skills/{skill['id']}", "DELETE")
        self.assertEqual(still_enabled.exception.code, 409)
        self.request(f"/api/skills/{skill['id']}", "PATCH", {"enabled": False})
        self.request(f"/api/skills/{skill['id']}", "DELETE")
        self.assertTrue((source / "SKILL.md").exists())
        self.assertFalse(any(s["id"] == skill["id"] for s in self.request("/api/overview")["skills"]))

    def test_mcp_token_is_not_accepted_from_frontend(self):
        source = Path(self.temp.name) / 'token-fixture'
        manifest = fixture(source)
        manifest['metadata']['name'] = 'token-fixture'
        (source / 'skill-package.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
        (source / 'SKILL.md').write_text('---\nname: token-fixture\ndescription: Token test\n---\nInstructions.\n', encoding='utf-8')
        write_lock(source)
        secret = 'test-mcp-token-at-least-24-characters'
        skill = self.request('/api/skills', 'POST', {'path':'token-fixture', 'mcp_token':secret})
        saved = self.app.get('skills', skill['id'])
        self.assertEqual(saved['mcp_token'], '')
        overview = self.request('/api/overview')
        public = next(s for s in overview['skills'] if s['id'] == skill['id'])
        self.assertTrue(public['mcp_token_configured'])
        self.assertNotIn('mcp_token', public)
        self.assertNotIn(secret, json.dumps(overview))
        self.assertNotIn(manifest['spec']['tools']['mcp']['auth']['token'], json.dumps(overview))


if __name__ == "__main__":
    unittest.main()
