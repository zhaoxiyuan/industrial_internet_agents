"""
Web 服务器 - 纯 HTML 前端后端
"""
import json
import os
import sys
import threading
import logging
import asyncio
import websockets
import queue
from datetime import datetime
from collections import defaultdict

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from agents.main_agent import run_workflow, confirm_and_continue, list_pending_confirmations, get_workflow_state, set_broadcast_callback
from agents.utils.logging_handler import set_logs_broadcast_queue
from agents.utils.system_prompt import load_system_prompt, save_system_prompt

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("server")


# 配置
PORT = 8080
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(WEB_DIR), ".env")
SYSTEM_PROMPT_DIR = os.path.join(os.path.dirname(WEB_DIR), "agents", "system_prompt")

# WebSocket 端口配置
WS_STATUS_PORT = PORT + 1   # 状态通道端口
WS_LOGS_PORT = PORT + 2     # 日志通道端口


# 线程存储
_thread_store = {"id": None}


# 正在执行的工作流线程
_running_workflows = {}  # job_id -> thread

# ============ 状态通道 WebSocket 连接管理 ============
status_connections = defaultdict(set)  # job_id -> set of websocket clients
status_broadcast_queue = queue.Queue()  # 状态广播队列

# ============ 日志通道 WebSocket 连接管理 ============
logs_connections = defaultdict(set)  # job_id -> set of websocket clients
logs_broadcast_queue = queue.Queue()  # 日志广播队列


def reset_thread():
    _thread_store["id"] = None


async def process_request(ws, request):
    """在握手阶段将路径存储在 ws 对象上"""
    ws.path = request.path
    return None


# ============ 状态通道 WebSocket Handler ============
async def status_websocket_handler(websocket):
    """状态 WebSocket 连接处理器"""
    path = getattr(websocket, 'path', '/')
    logger.info(f"[WS-STATUS] handler 开始: path={path}")
    # 解析 job_id from path: /ws/status/{job_id}
    parts = path.split('/')
    job_id = parts[-1] if parts else None
    logger.info(f"[WS-STATUS] 解析 job_id: {job_id}")

    if not job_id:
        logger.warning(f"[WS-STATUS] 无效的 WebSocket 路径: {path}")
        return

    logger.info(f"[WS-STATUS] 客户端连接: job_id={job_id}")

    # 注册连接
    status_connections[job_id].add(websocket)
    logger.info(f"[WS-STATUS] 已注册连接，当前连接数: {len(status_connections[job_id])}")

    try:
        # 发送当前状态
        result = get_workflow_state(job_id)
        pending = list_pending_confirmations(job_id)
        state_data = {
            "status": result.get("status", "unknown"),
            "pending": [p.get("stage", "") for p in pending],
            "pending_data": {p.get("stage", ""): p for p in pending},
            "confirmed": result.get("confirmed_stages", []),
            "current_stage": result.get("current_stage", ""),
            "thread_id": job_id,
        }
        logger.info(f"[WS-STATUS] 发送初始状态: job_id={job_id}")
        await websocket.send(json.dumps({"type": "state_update", "data": state_data}, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"[WS-STATUS] 发送初始状态失败: job_id={job_id}, error={e}")

    try:
        # 保持连接，监听广播消息
        while True:
            try:
                msg = await asyncio.to_thread(status_broadcast_queue.get, True, 1.0)
                # 只发送给订阅此 job_id 的客户端
                if msg.get("job_id") == job_id:
                    await websocket.send(json.dumps(msg, ensure_ascii=False))
                    logger.info(f"[WS-STATUS] 推送状态: job_id={job_id}, type={msg.get('type')}")
            except queue.Empty:
                # 心跳保活
                try:
                    await websocket.send(json.dumps({"type": "heartbeat", "channel": "status"}, ensure_ascii=False))
                except Exception as e:
                    logger.warning(f"[WS-STATUS] 发送心跳失败: {e}")
                    break
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"[WS-STATUS] 连接关闭: job_id={job_id}")
    except Exception as e:
        logger.warning(f"[WS-STATUS] 异常: {e}")
    finally:
        status_connections[job_id].discard(websocket)
        logger.info(f"[WS-STATUS] 客户端断开: job_id={job_id}")


# ============ 日志通道 WebSocket Handler ============
async def logs_websocket_handler(websocket):
    """日志 WebSocket 连接处理器"""
    path = getattr(websocket, 'path', '/')
    logger.info(f"[WS-LOGS] handler 开始: path={path}")
    # 解析 job_id from path: /ws/logs/{job_id}
    parts = path.split('/')
    job_id = parts[-1] if parts else None
    logger.info(f"[WS-LOGS] 解析 job_id: {job_id}")

    if not job_id:
        logger.warning(f"[WS-LOGS] 无效的 WebSocket 路径: {path}")
        return

    logger.info(f"[WS-LOGS] 客户端连接: job_id={job_id}")

    # 注册连接
    logs_connections[job_id].add(websocket)
    logger.info(f"[WS-LOGS] 已注册连接，当前连接数: {len(logs_connections[job_id])}")

    try:
        # 发送连接成功消息
        await websocket.send(json.dumps({
            "type": "connected",
            "message": f"日志通道已连接: job_id={job_id}",
            "job_id": job_id
        }, ensure_ascii=False))
        logger.info(f"[WS-LOGS] 发送连接确认: job_id={job_id}")
    except Exception as e:
        logger.warning(f"[WS-LOGS] 发送连接确认失败: job_id={job_id}, error={e}")

    try:
        # 保持连接，监听日志消息
        while True:
            try:
                msg = await asyncio.to_thread(logs_broadcast_queue.get, True, 1.0)
                # 只发送给订阅此 job_id 的客户端
                if msg.get("job_id") == job_id or msg.get("job_id") == "*":
                    await websocket.send(json.dumps(msg, ensure_ascii=False))
                    logger.info(f"[WS-LOGS] 推送日志: job_id={msg.get('job_id')}, level={msg.get('level')}")
            except queue.Empty:
                # 心跳保活
                try:
                    await websocket.send(json.dumps({"type": "heartbeat", "channel": "logs"}, ensure_ascii=False))
                except Exception as e:
                    logger.warning(f"[WS-LOGS] 发送心跳失败: {e}")
                    break
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"[WS-LOGS] 连接关闭: job_id={job_id}")
    except Exception as e:
        logger.warning(f"[WS-LOGS] 异常: {e}")
    finally:
        logs_connections[job_id].discard(websocket)
        logger.info(f"[WS-LOGS] 客户端断开: job_id={job_id}")


def broadcast_workflow_state(job_id):
    """从后台线程推送工作流状态（跨线程调用）"""
    try:
        result = get_workflow_state(job_id)
        pending = list_pending_confirmations(job_id)
        state_data = {
            "status": result.get("status", "unknown"),
            "pending": [p.get("stage", "") for p in pending],
            "pending_data": {p.get("stage", ""): p for p in pending},
            "confirmed": result.get("confirmed_stages", []),
            "current_stage": result.get("current_stage", ""),
            "thread_id": job_id,
        }
        # 包装成标准消息格式
        message = {
            "type": "state_update",
            "job_id": job_id,
            "data": state_data
        }
        # 非阻塞放入状态队列（queue.Queue 是线程安全的）
        try:
            status_broadcast_queue.put_nowait(message)
        except queue.Full:
            pass
    except Exception as e:
        logger.warning(f"[WS-STATUS] 广播状态失败: job_id={job_id}, error={e}")


def broadcast_workflow_log(job_id: str, level: str, source: str, message: str, data: dict = None):
    """从后台线程推送工作流日志（跨线程调用）

    Args:
        job_id: 作业ID
        level: 日志级别 (INFO, WARNING, ERROR, DEBUG)
        source: 日志来源 (P1, P2, ..., MAIN, WORKFLOW, TOOL)
        message: 日志消息
        data: 附加数据
    """
    try:
        log_entry = {
            "type": "workflow_log",
            "job_id": job_id,
            "level": level,
            "source": source,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "data": data or {}
        }
        # 非阻塞放入日志队列（queue.Queue 是线程安全的）
        try:
            logs_broadcast_queue.put_nowait(log_entry)
        except queue.Full:
            pass
    except Exception as e:
        logger.warning(f"[WS-LOGS] 广播日志失败: job_id={job_id}, error={e}")


async def start_status_websocket_server():
    """启动状态 WebSocket 服务器"""
    while True:
        try:
            async with websockets.serve(status_websocket_handler, "127.0.0.1", WS_STATUS_PORT, process_request=process_request):
                logger.info(f"[WS-STATUS] 状态 WebSocket 服务器已启动: ws://localhost:{WS_STATUS_PORT}")
                await asyncio.Future()  # 永久运行
        except Exception as e:
            logger.error(f"[WS-STATUS] 服务器错误: {e}")
            await asyncio.sleep(1)


async def start_logs_websocket_server():
    """启动日志 WebSocket 服务器"""
    while True:
        try:
            async with websockets.serve(logs_websocket_handler, "127.0.0.1", WS_LOGS_PORT, process_request=process_request):
                logger.info(f"[WS-LOGS] 日志 WebSocket 服务器已启动: ws://localhost:{WS_LOGS_PORT}")
                await asyncio.Future()  # 永久运行
        except Exception as e:
            logger.error(f"[WS-LOGS] 服务器错误: {e}")
            await asyncio.sleep(1)


def run_status_websocket_server():
    """在新线程中运行状态 WebSocket 服务器"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_status_websocket_server())


def run_logs_websocket_server():
    """在新线程中运行日志 WebSocket 服务器"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_logs_websocket_server())


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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_POST(self):
        logger.info(f"[POST] 请求进入: path={self.path}")
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            logger.info(f"[POST] /api/config 进入: data={ {k:v for k,v in data.items() if k != 'api_key'} }")
            save_env_config(data)
            logger.info(f"[POST] /api/config 响应: status=ok")
            self.send_json({"status": "ok"})

        elif path.startswith("/api/prompt/"):
            stage = path.split("/")[-1]
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            logger.info(f"[POST] /api/prompt/{stage} 进入: content_length={len(body)}")
            save_system_prompt(stage, data.get("content", ""))
            logger.info(f"[POST] /api/prompt/{stage} 响应: status=ok")
            self.send_json({"status": "ok"})

        elif path == "/api/workflow/start":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            app = json.loads(body)

            # 生成作业单编号: yyyyMMddHHMMssXXX (XXX为3位随机数)
            import random
            job_id = datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999):03d}"
            app["job_id"] = job_id

            logger.info(f"[POST] /api/workflow/start 进入: job_id={job_id}")

            # 立即返回，不等待工作流完成
            response_data = {
                "status": "starting",
                "job_id": job_id,
                "message": "工作流启动中..."
            }
            logger.info(f"[POST] /api/workflow/start 响应: {response_data}")
            self.send_json(response_data)

            # 后台线程执行工作流
            def run_workflow_background(job_id, app):
                try:
                    logger.info(f"[WORKFLOW] 工作流开始执行: job_id={job_id}")
                    # 初始状态推送
                    broadcast_workflow_state(job_id)
                    result = run_workflow(app, thread_id=job_id)
                    logger.info(f"[WORKFLOW] 工作流执行完成: job_id={job_id}, result={result.get('status')}")
                    # 最终状态推送
                    broadcast_workflow_state(job_id)
                except Exception as e:
                    logger.exception(f"[WORKFLOW] 工作流执行错误: job_id={job_id}")
                    broadcast_workflow_state(job_id)

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
            async_execute = data.get("async_execute", False)

            logger.info(f"[POST] /api/workflow/confirm 进入: thread_id={thread_id}, stage={stage}, decision={decision}, async={async_execute}")

            if not thread_id:
                logger.warning(f"[POST] /api/workflow/confirm 参数错误: thread_id为空")
                self.send_json({
                    "status": "error",
                    "error": "thread_id 不能为空",
                    "pending": [],
                    "pending_data": {},
                    "confirmed": [],
                    "current_stage": "",
                    "thread_id": None,
                    "job_id": None,
                })
                return

            if not stage:
                logger.warning(f"[POST] /api/workflow/confirm 参数错误: stage为空")
                self.send_json({
                    "status": "error",
                    "error": "stage 不能为空",
                    "pending": [],
                    "pending_data": {},
                    "confirmed": [],
                    "current_stage": "",
                    "thread_id": thread_id,
                    "job_id": thread_id,
                })
                return

            try:
                result = confirm_and_continue(thread_id, stage, decision, async_execute=async_execute)

                if result is None:
                    logger.warning(f"[POST] /api/workflow/confirm 结果为空")
                    self.send_json({
                        "status": "error",
                        "error": "执行结果为空",
                        "pending": [],
                        "pending_data": {},
                        "confirmed": [],
                        "current_stage": stage,
                        "thread_id": thread_id,
                        "job_id": thread_id,
                    })
                    return

                # 异步模式下立即返回，不查询 pending 状态
                if async_execute:
                    logger.info(f"[POST] /api/workflow/confirm 异步响应: status={result.get('status')}")
                    self.send_json(result)
                    return

                pending = list_pending_confirmations(thread_id)
                pending_stages = [p.get("stage", "") for p in pending]
                pending_data = {p.get("stage", ""): p for p in pending}

                response_data = {
                    "status": "waiting" if pending_stages else "completed",
                    "pending": pending_stages,
                    "pending_data": pending_data,
                    "confirmed": result.get("confirmed_stages", []),
                    "current_stage": result.get("current_stage", ""),
                    "thread_id": thread_id,
                    "job_id": thread_id,
                }
                logger.info(f"[POST] /api/workflow/confirm 响应: {response_data}")
                self.send_json(response_data)
                # WebSocket 推送状态更新
                broadcast_workflow_state(thread_id)
            except Exception as e:
                logger.exception(f"[POST] /api/workflow/confirm 异常: thread_id={thread_id}")
                self.send_json({
                    "status": "error",
                    "error": str(e),
                    "pending": [],
                    "pending_data": {},
                    "confirmed": [],
                    "current_stage": "",
                    "thread_id": thread_id,
                    "job_id": thread_id,
                })

        else:
            logger.warning(f"[POST] 路径未找到: path={path}")
            self.send_error(404)

    def do_GET(self):
        logger.info(f"[GET] 请求进入: path={self.path}")
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            config = load_env_config()
            logger.info(f"[GET] /api/config 响应: config={'api_key': '***', 'base_url': config.get('base_url'), 'model': config.get('model')}")
            self.send_json(config)

        elif path.startswith("/api/prompt/"):
            stage = path.split("/")[-1]
            prompt = load_system_prompt(stage)
            logger.info(f"[GET] /api/prompt/{stage} 响应: content_length={len(prompt)}")
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
                logger.info(f"[GET] /data/input/mock_job_content.json 响应: content_length={len(content)}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                body = content.encode("utf-8")
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                logger.warning(f"[GET] /data/input/mock_job_content.json 文件不存在")
                self.send_error(404)

        elif path == "/api/workflow/state":
            thread_id = parse_qs(parsed.query).get("thread_id", [None])[0]
            logger.info(f"[GET] /api/workflow/state 进入: thread_id={thread_id}")
            if thread_id:
                result = get_workflow_state(thread_id)
                pending = list_pending_confirmations(thread_id)
                response_data = {
                    "status": result.get("status", "unknown"),
                    "pending": [p.get("stage", "") for p in pending],
                    "pending_data": {p.get("stage", ""): p for p in pending},
                    "confirmed": result.get("confirmed_stages", []),
                    "current_stage": result.get("current_stage", ""),
                    "thread_id": thread_id,
                }
                logger.info(f"[GET] /api/workflow/state 响应: {response_data}")
                self.send_json(response_data)
            else:
                logger.info(f"[GET] /api/workflow/state 响应: status=idle")
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
    # 设置日志 WebSocket 队列（供 logging_handler 使用）
    set_logs_broadcast_queue(logs_broadcast_queue)
    logger.info("[WS-LOGS] 日志广播队列已设置")

    # 设置状态广播回调（供 main_agent 调用）
    set_broadcast_callback(broadcast_workflow_state)
    logger.info("[BROADCAST] 状态广播回调已设置")

    # 启动状态 WebSocket 服务器（独立线程）
    ws_status_thread = threading.Thread(target=run_status_websocket_server, daemon=True)
    ws_status_thread.start()
    logger.info("[WS-STATUS] 状态 WebSocket 服务器线程已启动")

    # 启动日志 WebSocket 服务器（独立线程）
    ws_logs_thread = threading.Thread(target=run_logs_websocket_server, daemon=True)
    ws_logs_thread.start()
    logger.info("[WS-LOGS] 日志 WebSocket 服务器线程已启动")

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"🌐 Web 服务已启动: http://localhost:{PORT}")
    print(f"📝 打开浏览器访问: http://localhost:{PORT}/index.html")
    print(f"🔌 状态 WebSocket: ws://localhost:{WS_STATUS_PORT}/ws/status/{{job_id}}")
    print(f"📋 日志 WebSocket: ws://localhost:{WS_LOGS_PORT}/ws/logs/{{job_id}}")
    server.serve_forever()


if __name__ == "__main__":
    main()
