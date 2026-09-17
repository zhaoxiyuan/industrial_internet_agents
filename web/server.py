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
from web.api import docx_permit as docx_permit_api
from web.api import snapshots as snapshots_api
from feishu_gateway_cli import feishu_card as feishu_card_api
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

        elif path == "/api/workflow/resume":
            data = self._read_json()
            workflow_api.handle_workflow_resume(self, data)

        elif path == "/api/workflow/parse-docx":
            data = self._read_json()
            docx_permit_api.handle_parse_docx(self, data)

        elif path == "/api/feishu/card-callback":
            # 2026-09-17：转 P8P9 6 态状态机。原 feishu_card_api.handle_card_callback
            # 走老 P8 CardActionAgent + LLM 异步线程，从未接 P8P9 状态机
            # (grep "P8P9|business_actions|callback_router" feishu_card.py → No matches)。
            # 改为：直接 HTTP POST 到 P8P9 web_server.py
            #   (http://127.0.0.1:8089/feishu/card/callback)
            # 由 P8P9 内部 route_card_callback 解析 action 路由表 + 写状态机 +
            # 刷新飞书卡片。Gateway feishu.js 的 path 不用改（仍是
            # /api/feishu/card-callback），飞书侧 URI 也不用改。
            #
            # 安全性：P8P9 端 route_card_callback 自身有 action 路由表 +
            # operator.open_id 校验。
            # 失败兜底：业务端挂了也要回 200 + toast（飞书会重试轰炸 4xx/5xx）。
            try:
                data = self._read_json()
                business_reply = _proxy_to_p8p9_card_callback(data, logger)
                reply_summary = (
                    list(business_reply.keys())[:6]
                    if isinstance(business_reply, dict)
                    else type(business_reply).__name__
                )
                logger.info(
                    f"[POST] /api/feishu/card-callback 响应: keys={reply_summary}"
                )
                self.send_json(business_reply)
            except Exception as exc:
                logger.exception("[POST] /api/feishu/card-callback 异常")
                try:
                    # 飞书会重试轰炸 4xx/5xx，统一回 200 + toast
                    self.send_json(
                        {"toast": {"type": "error", "content": "服务暂时不可用，请稍后重试"}},
                        status=200,
                    )
                except Exception:
                    logger.exception("[POST] send_json 兜底失败")

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

        elif path == "/api/workflow/latest-incomplete":
            workflow_api.handle_latest_incomplete_workflow(self)

        elif path == "/api/workflow/history":
            limit = parse_qs(parsed.query).get("limit", [50])[0]
            workflow_api.handle_workflow_history(self, limit)

        elif path == "/api/workflow/job-detail":
            job_id = parse_qs(parsed.query).get("job_id", [None])[0]
            workflow_api.handle_workflow_job_detail(self, job_id)

        elif path == "/api/workflow/state":
            thread_id = parse_qs(parsed.query).get("thread_id", [None])[0]
            workflow_api.handle_workflow_state_get(self, thread_id)

        elif path == "/api/workflow/execution-status":
            job_id = parse_qs(parsed.query).get("job_id", [None])[0]
            workflow_api.handle_execution_status(self, job_id)

        elif path == "/api/feishu/card-callbacks":
            feishu_card_api.handle_card_callback_list(self)

        elif path.startswith("/api/jobs/") and path.endswith("/working-memory"):
            # 蓝图 § 8.1：P8 工作记忆快照查询
            # 路径格式：/api/jobs/{job_id}/working-memory
            parts = path.strip("/").split("/")
            # 期望 ["api","jobs","{job_id}","working-memory"] → len==4
            if len(parts) != 4 or not parts[2]:
                logger.warning(
                    f"[GET] /api/jobs/.../working-memory 路径非法: path={path}"
                )
                self.send_json(
                    {
                        "status": "error",
                        "error": "路径必须是 /api/jobs/{job_id}/working-memory",
                    },
                    status=400,
                )
            else:
                job_id = parts[2]
                logger.info(
                    f"[GET] /api/jobs/{job_id}/working-memory 进入: job_id={job_id}"
                )
                try:
                    from A7.api.p8_working_memory_ctrl import (
                        get_working_memory_snapshot,
                    )
                    result = get_working_memory_snapshot(job_id)
                    logger.info(
                        f"[GET] /api/jobs/{job_id}/working-memory 响应: "
                        f"status={result.get('status')}, "
                        f"working_count={len(result.get('working_memory') or [])}, "
                        f"archived_count={len(result.get('archived_recent') or [])}"
                    )
                    self.send_json(result)
                except ValueError as exc:
                    # job_id 为空（控制器兜底，正常不会到这里）
                    logger.warning(
                        f"[GET] /api/jobs/{job_id}/working-memory 参数非法: {exc}"
                    )
                    self.send_json(
                        {"status": "error", "error": str(exc)[:200]}, status=400
                    )
                except Exception as exc:
                    logger.exception(
                        f"[GET] /api/jobs/{job_id}/working-memory 异常: {exc}"
                    )
                    self.send_json(
                        {
                            "status": "error",
                            "error": f"internal: {exc}"[:200],
                        },
                        status=500,
                    )

        else:
            super().do_GET()

    def _read_json(self):
        """读 POST body 并解析为 dict。

        2026-08-18 修复：飞书某些 SDK / 测试工具在 Windows GBK locale 下会
        用 GBK 编码 body（含 0xd5 等字节），直接 .decode("utf-8") 抛
        UnicodeDecodeError → do_POST 异常 → Empty reply → Gateway 兜底
        toast.error → 飞书侧报错。容错策略：
        1. 先尝试 UTF-8（飞书官方规范）
        2. 失败回退 GBK（飞书 SDK / Git Bash curl 的 GBK locale）
        3. 最后 latin-1（永不抛，解码结果可能含乱码但 json.loads 仍可能成功）
        """
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length)
        text = None
        for codec in ("utf-8", "gbk"):
            try:
                text = raw.decode(codec)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("latin-1")
        return json.loads(text)

    def send_json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ─── 飞书 Card 按钮回调 → P8P9 业务端转发 helper ───────────────────────────

def _proxy_to_p8p9_card_callback(payload, logger) -> dict:
    """POST /api/feishu/card/callback → 转发到 P8P9 web_server (127.0.0.1:8089)。

    流程：
        1. json.dumps(payload, ensure_ascii=False) 编码（保持中文）
        2. urllib POST http://127.0.0.1:8089/feishu/card/callback（5s 超时）
        3. 业务端返回 dict（由 P8P9 web_server.feishu_card_callback 封装）

    P8P9 端 route_card_callback 自身有 action 路由表 + operator.open_id 校验；
    本函数不做业务校验，仅透传。失败抛异常 → 外层 try/except 兜底回 200 + toast
    （飞书对 4xx/5xx 会重试轰炸，业务端暂不可用也要回 200）。
    """
    import urllib.request

    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url="http://127.0.0.1:8089/feishu/card/callback",
        data=body_bytes,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp_body = resp.read().decode("utf-8")
        return json.loads(resp_body)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    from agents.utils.logging_handler import set_logs_broadcast_queue
    set_logs_broadcast_queue(get_logs_broadcast_queue())
    logger.info("[WS-LOGS] 日志广播队列已设置")

    from agents.main_agent import set_broadcast_callback
    set_broadcast_callback(broadcast_workflow_state)
    logger.info("[BROADCAST] 状态广播回调已设置")

    # 先占用 HTTP 端口，避免误启动第二个服务进程时把第一个进程正在执行的
    # 工单错误标记为“服务中断”。端口占用时会在恢复扫描前直接启动失败。
    server = HTTPServer(("127.0.0.1", PORT), Handler)

    recovered = workflow_api.recover_interrupted_workflows_on_startup()
    if recovered:
        logger.warning("[STARTUP-RECOVERY] 已标记 %s 个异常中断作业为可恢复", len(recovered))

    start_websocket_threads()

    print(f"Web 服务已启动: http://localhost:{PORT}")
    print(f"打开浏览器访问: http://localhost:{PORT}/index.html")
    print(f"状态 WebSocket: ws://localhost:{PORT + 1}/ws/status/{{job_id}}")
    print(f"日志 WebSocket: ws://localhost:{PORT + 2}/ws/logs/{{job_id}}")
    server.serve_forever()


if __name__ == "__main__":
    main()
