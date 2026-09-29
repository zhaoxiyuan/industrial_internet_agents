"""
a/web/server.py — 精简 Web 服务(端口 8080,只服务 P6-P9 业务)

【拆分说明 - 2026-09-18】
从根目录 web/server.py 拆分而来。原版服务 P1-P10 全流程 Web 入口,
但其核心依赖 `agents.main_agent`(P1-P10 编排),a/ 子项目刻意不搬 P1-P5
主流程。所以原版不能整体搬过来。

【本服务保留的 endpoint】
- POST /api/feishu/card-callback       飞书卡片按钮回调 → 转发 P8P9
- GET  /api/jobs/{job_id}/working-memory  P8 工作记忆查询
- GET  /api/feishu/card-callbacks      已注册飞书卡片 ID 列表

【不服务的 endpoint】
- /api/config*、/api/prompt/*、/api/workflow/*、/api/test/llm、/api/workflow/parse-docx
  这些原 web/server.py 里的 endpoint 强依赖 P1-P5 主流程,不在 a/ 范围内。

【与 P8P9 闭环】
P8P9 闭环的关键链路:
    飞书用户点击卡片按钮
        ↓
    飞书服务器 → Node.js Channel Gateway (a/gateway :8787)
        ↓ HTTP POST
    本服务 /api/feishu/card-callback (a/web :8080)        ← 本文件
        ↓ urllib 转发
    P8P9 web_server /feishu/card/callback (a/P8P9 :8089)
        ↓ 状态机写入 + 卡片更新
    用户在飞书看到卡片刷新

如果本服务或 P8P9 web_server 任何一环缺失,飞书侧按钮点击会失败。

【HTTP 端口】
8080(同根目录 webui 端口,保持一致)

【复用 a/ 内部的模块】
- feishu_gateway_cli.feishu_card  : 已注册卡片 ID 索引
- A7.api.p8_working_memory_ctrl    : P8 工作记忆
- P8P9.web_server (跨进程 HTTP)    : P8P9 业务核心
"""

import json
import sys
import os
import logging
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 把 a/ 加入 sys.path(让 web 目录下的其他模块可以正常 import a/ 的内容)
# 注:本服务以 `python -m web.server` 启动时 cwd=a/,sys.path[0]="" 已解析为 a/,
# 这行是为了以 `python a/web/server.py` 启动时也能正确解析 a/ 内部模块。
A_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if A_ROOT not in sys.path:
    sys.path.insert(0, A_ROOT)

# 静态资源目录: 跟 server.py 同级的 .html 文件
WEB_DIR = os.path.dirname(os.path.abspath(__file__))


# ── 日志 ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("a.web.server")

# P8P9 业务端地址(状态机 + 卡片回调主入口)
P8P9_BASE_URL = os.environ.get("A_P8P9_BASE_URL", "http://127.0.0.1:8089")


# ── Handler ──────────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    """精简版 Handler — 只暴露 P6-P9 业务 endpoint。

    静态文件走父类 SimpleHTTPRequestHandler(WEB_DIR = a/web/)。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    # ── POST ─────────────────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        logger.info(f"[POST] 请求进入: path={path}")

        if path == "/api/feishu/card-callback":
            return self._handle_feishu_card_callback()
        else:
            logger.warning(f"[POST] 路径未找到或不在 P6-P9 范围内: path={path}")
            self.send_json(
                {
                    "status": "error",
                    "error": (
                        "a/web/server.py 仅服务 P6-P9 业务。" 
                        f"该 endpoint ({path}) 不在 P6-P9 范围内。"
                        "完整 P1-P10 Web 服务请用根目录 web/server.py。"
                    ),
                },
                status=404,
            )

    # ── GET ──────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        logger.info(f"[GET] 请求进入: path={path}")
        try:
            return self._do_GET_inner(path, parsed)
        except Exception as exc:
            # 兜底任何 handler 未捕获的异常(例如 feishu_card._load_index 内部 dotenv 缺失),
            # 避免 HTTPServer.serve_forever 因单个请求崩溃导致整个服务挂掉。
            logger.exception(f"[GET] 未捕获异常: path={path}")
            try:
                self.send_json(
                    {"status": "error", "error": f"internal: {exc}"[:200]},
                    status=500,
                )
            except Exception:
                logger.exception("[GET] send_json 兜底失败")

    def _do_GET_inner(self, path, parsed):
        """do_GET 实际逻辑(被 do_GET 用 try/except 包裹)。"""

        # ── 健康检查(Docker / Nginx upstream 用)───────────────────
        # 2026-09-20:为 Docker 部署新增;在业务 endpoint 之前判断
        #   避免父类 SimpleHTTPRequestHandler 把它当静态文件处理
        if path == "/api/health":
            self.send_json({"ok": True})
            return

        # ── P6-P9 业务 endpoint ──────────────────────────────────────
        if path.startswith("/api/jobs/") and path.endswith("/working-memory"):
            parts = path.strip("/").split("/")
            # 期望 ["api","jobs","{job_id}","working-memory"] → len==4
            if len(parts) != 4 or not parts[2]:
                logger.warning(f"[GET] /api/jobs/.../working-memory 路径非法: path={path}")
                self.send_json(
                    {
                        "status": "error",
                        "error": "路径必须是 /api/jobs/{job_id}/working-memory",
                    },
                    status=400,
                )
                return
            job_id = parts[2]
            return self._handle_working_memory(job_id)

        if path == "/api/feishu/card-callbacks":
            return self._handle_card_callbacks_list()

        # ── 静态资源(父类默认走 SimpleHTTPRequestHandler)────────────
        # 父类会根据 path 找 a/web/{path},找不到 404。✓
        return super().do_GET()

    # ── endpoint 实现 ───────────────────────────────────────────────

    def _handle_feishu_card_callback(self):
        """POST /api/feishu/card-callback → 转发到 P8P9 web_server。

        飞书 Node Gateway → 本 endpoint → 转发到 P8P9 业务核心。
        这是飞书交互闭环的关键 — 任何一环挂掉,用户按钮点不动。

        失败兜底:必须回 200 + toast(飞书对 4xx/5xx 会重试轰炸)。
        """
        try:
            payload = self._read_json()
            business_reply = _proxy_to_p8p9_card_callback(payload)
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
                self.send_json(
                    {
                        "toast": {
                            "type": "error",
                            "content": "服务暂时不可用,请稍后重试",
                        }
                    },
                    status=200,
                )
            except Exception:
                logger.exception("[POST] send_json 兜底失败")

    def _handle_working_memory(self, job_id: str):
        """GET /api/jobs/{job_id}/working-memory → A7 P8 工作记忆快照。

        蓝图 § 8.1:P8 工作记忆快照查询。
        """
        logger.info(f"[GET] /api/jobs/{job_id}/working-memory 进入: job_id={job_id}")
        try:
            from A7.api.p8_working_memory_ctrl import get_working_memory_snapshot
            result = get_working_memory_snapshot(job_id)
            logger.info(
                f"[GET] /api/jobs/{job_id}/working-memory 响应: "
                f"status={result.get('status')}, "
                f"working_count={len(result.get('working_memory') or [])}, "
                f"archived_count={len(result.get('archived_recent') or [])}"
            )
            self.send_json(result)
        except ValueError as exc:
            logger.warning(
                f"[GET] /api/jobs/{job_id}/working-memory 参数非法: {exc}"
            )
            self.send_json({"status": "error", "error": str(exc)[:200]}, status=400)
        except Exception as exc:
            logger.exception(
                f"[GET] /api/jobs/{job_id}/working-memory 异常: {exc}"
            )
            self.send_json(
                {"status": "error", "error": f"internal: {exc}"[:200]},
                status=500,
            )

    def _handle_card_callbacks_list(self):
        """GET /api/feishu/card-callbacks → 已注册飞书卡片 ID 列表(排查用)。

        注:根目录原版 web/server.py 也调 feishu_card.handle_card_callback_list,
        但 feishu_gateway_cli.feishu_card 模块里其实没有该函数(原版是个 bug)。
        这里直接复用 feishu_gateway_cli.feishu_card 内部的 _load_index,功能等价。
        """
        try:
            from feishu_gateway_cli.feishu_card import _load_index
            index = _load_index()
            callbacks = [
                {"alert_id": k, **v} for k, v in index.items()
            ]
            logger.info(
                f"[GET] /api/feishu/card-callbacks 响应: count={len(callbacks)}"
            )
            self.send_json({"status": "ok", "callbacks": callbacks, "count": len(callbacks)})
        except Exception as exc:
            logger.exception(f"[GET] /api/feishu/card-callbacks 异常: {exc}")
            self.send_json(
                {"status": "error", "error": f"internal: {exc}"[:200]},
                status=500,
            )

    # ── helpers ───────────────────────────────────────────────────────

    def _read_json(self):
        """读 POST body 并解析为 dict。容错 UTF-8 → GBK → latin-1(同根目录原版)。"""
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

    def log_message(self, format, *args):
        # 抑制父类默认 stderr 输出,统一走 logger。
        logger.debug(f"HTTP: {format % args}")


# ── 转发 helper ─────────────────────────────────────────────────────

def _proxy_to_p8p9_card_callback(payload) -> dict:
    """POST /api/feishu/card/callback → 转发到 P8P9 web_server。

    流程:
        1. json.dumps(payload, ensure_ascii=False) 编码(保持中文)
        2. urllib POST http://127.0.0.1:8089/feishu/card/callback(5s 超时)
        3. 业务端返回 dict(由 P8P9 web_server.feishu_card_callback 封装)

    P8P9 端 route_card_callback 自身有 action 路由表 + operator.open_id 校验;
    本函数不做业务校验,仅透传。失败抛异常 → 外层 try/except 兜底回 200 + toast。
    """
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=f"{P8P9_BASE_URL}/feishu/card/callback",
        data=body_bytes,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp_body = resp.read().decode("utf-8")
        return json.loads(resp_body)


# ── main ─────────────────────────────────────────────────────────────

PORT = int(os.environ.get("A_WEBUI_PORT", "8080"))
HOST = os.environ.get("A_WEBUI_HOST", "127.0.0.1")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    server = HTTPServer((HOST, PORT), Handler)

    print(f"a/web 服务已启动: http://{HOST}:{PORT}")
    print(f"  飞书卡片回调入口:POST /api/feishu/card-callback → 转发 P8P9 {P8P9_BASE_URL}")
    print(f"  P8 工作记忆查询:GET  /api/jobs/{{job_id}}/working-memory")
    print(f"  飞书卡片索引:GET    /api/feishu/card-callbacks")
    print(f"  静态首页:GET        /")
    print(f"  P8 详情:GET         /p8_detail.html")
    print(f"  说明:本服务只覆盖 P6-P9 业务,P1-P5 主流程请用根目录 web/server.py")
    server.serve_forever()


if __name__ == "__main__":
    main()
