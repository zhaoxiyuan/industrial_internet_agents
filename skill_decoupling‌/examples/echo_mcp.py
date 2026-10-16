"""可独立启动的本地 MCP 样例，仅引用新项目内的协议模块和标准库。

由开发者在终端配置 MCP_PORT/MCP_DATA_DIR 并独立启动；样例支持 echo 和将文本写入自己数据
目录的 save_note，用于验证 HTTP 工具发现和独立存储，不由平台启动或停止。
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# 直接运行脚本时 examples/ 是搜索根；只添加本独立项目目录，不引用旧仓库的代码。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_core.common import Problem, dumps, uid, validate
from platform_core.mcp import rpc_response

TOOLS = [
    {"name": "echo", "description": "原样返回文本，用于验证 MCP 协议调用。", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
    {"name": "save_note", "description": "将文本保存在本服务自己的 MCP_DATA_DIR，返回路径信息。", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
]


def call(name, arguments):
    """文件名由服务生成；模型无法指定主机任意路径。"""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if not tool:
        raise Problem("TOOL_NOT_FOUND", "工具不存在", 404)
    validate(arguments, tool["inputSchema"])
    if name == "echo":
        return {"text": arguments["text"]}
    path = Path(os.environ["MCP_DATA_DIR"]) / (uid() + ".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(arguments["text"], encoding="utf-8")
    return {"saved": True, "service_file": path.name, "storage": "MCP_DATA_DIR", "note": "这是服务文件，平台不会读取任意返回的远端路径"}


class Handler(BaseHTTPRequestHandler):
    """样例提供 JSON Streamable HTTP，平台配置地址可用 /rpc 而非固定 /mcp。"""

    def do_POST(self):
        """可选 Bearer 验证；按标准区分通知空响应和带 id 的 RPC 响应。"""
        token = os.environ.get("MCP_BEARER_TOKEN", "")
        # 本地 MCP 与管理 API 一样检查 Origin，防止任意外站通过浏览器调用本机服务。
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.send_error(403)
            return
        if token and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
            self.send_error(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 1024 * 1024:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            value = rpc_response(payload, TOOLS, call)
        except (ValueError, TypeError):
            self.send_error(400)
            return
        raw = dumps(value).encode() if value is not None else b""
        self.send_response(200 if value is not None else 202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        """样例不发布独立 SSE 流，按 Streamable HTTP 约定返回 405。"""
        self.send_error(405)

    def log_message(self, format, *args):
        """样例日志只含 HTTP 状态，不记录 token 或业务文本。"""
        super().log_message(format, *args)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("MCP_PORT", "8771"))), Handler).serve_forever()
