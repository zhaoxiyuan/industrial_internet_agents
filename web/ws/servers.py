"""
WebSocket 服务器线程启动函数
"""
import threading
import asyncio
import logging
import websockets

from web.ws.handlers import status_websocket_handler, logs_websocket_handler
from web.ws.manager import process_request, WS_STATUS_PORT, WS_LOGS_PORT

logger = logging.getLogger("server")


def run_status_websocket_server():
    """在新线程中运行状态 WebSocket 服务器"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_start_status_websocket_server())


def run_logs_websocket_server():
    """在新线程中运行日志 WebSocket 服务器"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_start_logs_websocket_server())


async def _start_status_websocket_server():
    while True:
        try:
            async with websockets.serve(status_websocket_handler, "127.0.0.1", WS_STATUS_PORT, process_request=process_request):
                logger.info(f"[WS-STATUS] 状态 WebSocket 服务器已启动: ws://localhost:{WS_STATUS_PORT}")
                await asyncio.Future()
        except Exception as e:
            logger.error(f"[WS-STATUS] 服务器错误: {e}")
            await asyncio.sleep(1)


async def _start_logs_websocket_server():
    while True:
        try:
            async with websockets.serve(logs_websocket_handler, "127.0.0.1", WS_LOGS_PORT, process_request=process_request):
                logger.info(f"[WS-LOGS] 日志 WebSocket 服务器已启动: ws://localhost:{WS_LOGS_PORT}")
                await asyncio.Future()
        except Exception as e:
            logger.error(f"[WS-LOGS] 服务器错误: {e}")
            await asyncio.sleep(1)


def start_websocket_threads():
    """启动所有 WebSocket 服务器线程"""
    ws_status_thread = threading.Thread(target=run_status_websocket_server, daemon=True)
    ws_status_thread.start()
    logger.info("[WS-STATUS] 状态 WebSocket 服务器线程已启动")

    ws_logs_thread = threading.Thread(target=run_logs_websocket_server, daemon=True)
    ws_logs_thread.start()
    logger.info("[WS-LOGS] 日志 WebSocket 服务器线程已启动")