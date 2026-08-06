"""
Web 服务器 - 纯 HTML 前端后端
"""
import json
import os
import sys
import threading
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from agents.main_agent import run_workflow, confirm_and_continue, list_pending_confirmations, get_workflow_state


# 配置
PORT = 8080
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(WEB_DIR), ".env")
SYSTEM_PROMPT_DIR = os.path.join(os.path.dirname(WEB_DIR), "system_prompt")


# 线程存储
_thread_store = {"id": None}


# 正在执行的工作流线程
_running_workflows = {}  # job_id -> thread


def reset_thread():
    _thread_store["id"] = None


def load_env_config():
    config = {"api_key": "", "base_url": "", "model": ""}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        key, val = line.split("=", 1)
                        if key == "OPENAI_API_KEY":
                            config["api_key"] = val
                        elif key == "OPENAI_BASE_URL":
                            config["base_url"] = val
                        elif key == "OPENAI_MODEL":
                            config["model"] = val
    return config


def save_env_config(data):
    lines = [
        "# OpenAI Configuration",
        f"OPENAI_API_KEY={data.get('api_key', '')}",
        f"OPENAI_BASE_URL={data.get('base_url', '')}",
        f"OPENAI_MODEL={data.get('model', '')}",
        "MODEL_PROVIDER=openai",
    ]
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines))


def load_system_prompt(stage: str) -> str:
    stage_to_file = {
        "MAIN": "MAIN_AGENT_SYSTEM_PROMPT.md",
        "P1": "P1_PERMIT_SYSTEM_PROMPT.md",
        "P2": "P2_TASK_SYSTEM_PROMPT.md",
        "P3": "P3_CONTEXT_SYSTEM_PROMPT.md",
        "P4": "P4_BINDING_SYSTEM_PROMPT.md",
        "P5": "P5_VERIFY_SYSTEM_PROMPT.md",
        "P6": "P6_MONITOR_SYSTEM_PROMPT.md",
        "P7": "P7_RISK_SYSTEM_PROMPT.md",
        "P8": "P8_DISPOSITION_SYSTEM_PROMPT.md",
        "P9": "P9_CLOSURE_SYSTEM_PROMPT.md",
        "P10": "P10_ARCHIVE_SYSTEM_PROMPT.md",
    }
    filename = stage_to_file.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    filepath = os.path.join(SYSTEM_PROMPT_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_system_prompt(stage: str, content: str):
    stage_to_file = {
        "MAIN": "MAIN_AGENT_SYSTEM_PROMPT.md",
        "P1": "P1_PERMIT_SYSTEM_PROMPT.md",
        "P2": "P2_TASK_SYSTEM_PROMPT.md",
        "P3": "P3_CONTEXT_SYSTEM_PROMPT.md",
        "P4": "P4_BINDING_SYSTEM_PROMPT.md",
        "P5": "P5_VERIFY_SYSTEM_PROMPT.md",
        "P6": "P6_MONITOR_SYSTEM_PROMPT.md",
        "P7": "P7_RISK_SYSTEM_PROMPT.md",
        "P8": "P8_DISPOSITION_SYSTEM_PROMPT.md",
        "P9": "P9_CLOSURE_SYSTEM_PROMPT.md",
        "P10": "P10_ARCHIVE_SYSTEM_PROMPT.md",
    }
    filename = stage_to_file.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    filepath = os.path.join(SYSTEM_PROMPT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            save_env_config(data)
            self.send_json({"status": "ok"})

        elif path.startswith("/api/prompt/"):
            stage = path.split("/")[-1]
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            save_system_prompt(stage, data.get("content", ""))
            self.send_json({"status": "ok"})

        elif path == "/api/workflow/start":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            app = json.loads(body)

            # 生成作业单编号: yyyyMMddHHMMssXXX (XXX为3位随机数)
            import random
            job_id = datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999):03d}"
            app["job_id"] = job_id

            # 立即返回，不等待工作流完成
            self.send_json({
                "status": "starting",
                "job_id": job_id,
                "message": "工作流启动中..."
            })

            # 后台线程执行工作流
            def run_workflow_background(job_id, app):
                try:
                    result = run_workflow(app, thread_id=job_id)
                    # 工作流执行完成，记录最终状态
                except Exception as e:
                    print(f"工作流执行错误: {e}")

            t = threading.Thread(target=run_workflow_background, args=(job_id, app))
            t.daemon = True
            t.start()
            _running_workflows[job_id] = t

        elif path == "/api/workflow/confirm":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)

            thread_id = data.get("thread_id")
            stage = data.get("stage")
            decision = data.get("decision")

            result = confirm_and_continue(thread_id, stage, decision)
            pending = list_pending_confirmations(thread_id)
            pending_stages = [p.get("stage", "") for p in pending]
            pending_data = {p.get("stage", ""): p for p in pending}

            self.send_json({
                "status": "waiting" if pending_stages else "completed",
                "pending": pending_stages,
                "pending_data": pending_data,
                "confirmed": list(result.get("confirmed_stages", {}).keys()),
                "current_stage": result.get("current_stage", ""),
                "thread_id": thread_id,
                "job_id": thread_id,
            })

        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            config = load_env_config()
            self.send_json(config)

        elif path.startswith("/api/prompt/"):
            stage = path.split("/")[-1]
            prompt = load_system_prompt(stage)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            body = prompt.encode("utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        elif path == "/data/input/mock_job_content.json":
            mock_file = os.path.join(os.path.dirname(WEB_DIR), "data", "input", "mock_job_content.json")
            if os.path.exists(mock_file):
                with open(mock_file, "r", encoding="utf-8") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                body = content.encode("utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self.send_error(404)

        elif path == "/api/workflow/state":
            thread_id = parse_qs(parsed.query).get("thread_id", [None])[0]
            if thread_id:
                result = get_workflow_state(thread_id)
                pending = list_pending_confirmations(thread_id)
                self.send_json({
                    "status": result.get("status", "unknown"),
                    "pending": [p.get("stage", "") for p in pending],
                    "pending_data": {p.get("stage", ""): p for p in pending},
                    "confirmed": list(result.get("confirmed_stages", {}).keys()),
                    "current_stage": result.get("current_stage", ""),
                    "thread_id": thread_id,
                })
            else:
                self.send_json({"status": "idle", "pending": [], "confirmed": [], "current_stage": ""})

        else:
            super().do_GET()

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"🌐 Web 服务已启动: http://localhost:{PORT}")
    print(f"📝 打开浏览器访问: http://localhost:{PORT}/index.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
