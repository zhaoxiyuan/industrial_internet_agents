"""
Web 服务器 - 纯 HTML 前端后端
http://localhost:8080/index.html
"""
import json
import os
import sys
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.api import config as config_api
from web.api import workflow as workflow_api
from web.api import snapshots as snapshots_api
from A7.notify import feishu_card as feishu_card_api
from web.ws.manager import broadcast_workflow_state, get_logs_broadcast_queue
from web.ws.servers import start_websocket_threads

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("server")

PORT = 8080
WEB_DIR = os.path.dirname(os.path.abspath(__file__))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_POST(self):
        logger.info(f"[POST] 请求进入: path={self.path}")
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            data = self._read_json()
            config_api.handle_config_post(self, data)

        elif path == "/api/test/llm":
            data = self._read_json()
            config_api.handle_test_llm_post(self, data)

        elif path == "/api/config/snapshots/save":
            data = self._read_json()
            snapshots_api.handle_save_post(self, data)

        elif path == "/api/config/snapshots/delete":
            data = self._read_json()
            snapshots_api.handle_delete_post(self, data)

        elif path.startswith("/api/prompt/"):
            stage = path.split("/")[-1]
            data = self._read_json()
            config_api.handle_prompt_post(self, stage, data)

        elif path == "/api/workflow/start":
            app = self._read_json()
            workflow_api.handle_workflow_start(self, app)

        elif path == "/api/workflow/confirm":
            data = self._read_json()
            workflow_api.handle_workflow_confirm(self, data)

        elif path == "/api/feishu/card-callback":
            data = self._read_json()
            feishu_card_api.handle_card_callback(self, data)

        else:
            logger.warning(f"[POST] 路径未找到: path={path}")
            self.send_error(404)

    def do_GET(self):
        logger.info(f"[GET] 请求进入: path={self.path}")
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config/snapshots":
            snapshots_api.handle_list(self)

        elif path == "/api/config":
            config_api.handle_config_get(self)

        elif path.startswith("/api/prompt/"):
            stage = path.split("/")[-1]
            config_api.handle_prompt_get(self, stage)

        elif path == "/data/input/mock_job_content.json":
            mock_file = os.path.join(os.path.dirname(WEB_DIR), "data", "input", "mock_job_content.json")
            if os.path.exists(mock_file):
                with open(mock_file, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"[GET] /data/input/mock_job_content.json 响应: content_length={len(content)}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                body = content.encode("utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                logger.warning(f"[GET] /data/input/mock_job_content.json 文件不存在")
                self.send_error(404)

        elif path == "/api/workflow/state":
            thread_id = parse_qs(parsed.query).get("thread_id", [None])[0]
            workflow_api.handle_workflow_state_get(self, thread_id)

        elif path == "/api/feishu/card-callbacks":
            feishu_card_api.handle_card_callback_list(self)

        else:
            super().do_GET()

    def _read_json(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        return json.loads(body)

    def send_json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    from agents.utils.logging_handler import set_logs_broadcast_queue
    set_logs_broadcast_queue(get_logs_broadcast_queue())
    logger.info("[WS-LOGS] 日志广播队列已设置")

    from agents.main_agent import set_broadcast_callback
    set_broadcast_callback(broadcast_workflow_state)
    logger.info("[BROADCAST] 状态广播回调已设置")

    start_websocket_threads()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Web 服务已启动: http://localhost:{PORT}")
    print(f"打开浏览器访问: http://localhost:{PORT}/index.html")
    print(f"状态 WebSocket: ws://localhost:{PORT + 1}/ws/status/{{job_id}}")
    print(f"日志 WebSocket: ws://localhost:{PORT + 2}/ws/logs/{{job_id}}")
    server.serve_forever()


if __name__ == "__main__":
    main()