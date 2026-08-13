"""
WebSocket 处理器
"""
import json
import asyncio
import queue
import logging
import websockets

from web.ws.manager import (
    status_connections,
    logs_connections,
    status_broadcast_queue,
    logs_broadcast_queue,
    process_request,
    WS_STATUS_PORT,
    WS_LOGS_PORT,
)

logger = logging.getLogger("server")


async def status_websocket_handler(websocket):
    """状态 WebSocket 连接处理器"""
    path = getattr(websocket, 'path', '/')
    logger.info(f"[WS-STATUS] handler 开始: path={path}")
    parts = path.split('/')
    job_id = parts[-1] if parts else None
    logger.info(f"[WS-STATUS] 解析 job_id: {job_id}")

    if not job_id:
        logger.warning(f"[WS-STATUS] 无效的 WebSocket 路径: {path}")
        return

    logger.info(f"[WS-STATUS] 客户端连接: job_id={job_id}")
    status_connections[job_id].add(websocket)
    logger.info(f"[WS-STATUS] 已注册连接，当前连接数: {len(status_connections[job_id])}")

    try:
        from agents.main_agent import get_workflow_state, list_pending_confirmations
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
        while True:
            try:
                msg = await asyncio.to_thread(status_broadcast_queue.get, True, 1.0)
                if msg.get("job_id") == job_id:
                    await websocket.send(json.dumps(msg, ensure_ascii=False))
                    logger.info(f"[WS-STATUS] 推送状态: job_id={job_id}, type={msg.get('type')}")
            except queue.Empty:
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


async def logs_websocket_handler(websocket):
    """日志 WebSocket 连接处理器"""
    path = getattr(websocket, 'path', '/')
    logger.info(f"[WS-LOGS] handler 开始: path={path}")
    parts = path.split('/')
    job_id = parts[-1] if parts else None
    logger.info(f"[WS-LOGS] 解析 job_id: {job_id}")

    if not job_id:
        logger.warning(f"[WS-LOGS] 无效的 WebSocket 路径: {path}")
        return

    logger.info(f"[WS-LOGS] 客户端连接: job_id={job_id}")
    logs_connections[job_id].add(websocket)
    logger.info(f"[WS-LOGS] 已注册连接，当前连接数: {len(logs_connections[job_id])}")

    try:
        await websocket.send(json.dumps({
            "type": "connected",
            "message": f"日志通道已连接: job_id={job_id}",
            "job_id": job_id
        }, ensure_ascii=False))
        logger.info(f"[WS-LOGS] 发送连接确认: job_id={job_id}")
    except Exception as e:
        logger.warning(f"[WS-LOGS] 发送连接确认失败: job_id={job_id}, error={e}")

    try:
        while True:
            try:
                msg = await asyncio.to_thread(logs_broadcast_queue.get, True, 1.0)
                if msg.get("job_id") == job_id or msg.get("job_id") == "*":
                    await websocket.send(json.dumps(msg, ensure_ascii=False))
                    logger.info(f"[WS-LOGS] 推送日志: job_id={msg.get('job_id')}, level={msg.get('level')}")
            except queue.Empty:
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