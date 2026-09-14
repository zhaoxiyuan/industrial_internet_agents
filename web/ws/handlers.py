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


def _message_targets(connections, job_id):
    """返回消息应送达的连接快照；通配日志广播给所有当前连接。"""
    if job_id == "*":
        return [ws for group in connections.values() for ws in tuple(group)]
    return list(connections.get(job_id, ()))


async def _broadcast_dispatcher(message_queue, connections, channel):
    """由一个协程消费队列，再扇出给目标连接，避免多个连接竞争并丢消息。"""
    while True:
        try:
            msg = await asyncio.to_thread(message_queue.get, True, 1.0)
        except queue.Empty:
            continue

        targets = _message_targets(connections, msg.get("job_id"))
        if not targets:
            continue
        payload = json.dumps(msg, ensure_ascii=False)
        results = await asyncio.gather(
            *(ws.send(payload) for ws in targets),
            return_exceptions=True,
        )
        for websocket, result in zip(targets, results):
            if isinstance(result, Exception):
                for group in connections.values():
                    group.discard(websocket)
            else:
                logger.info(
                    f"[WS-{channel.upper()}] 推送消息: "
                    f"job_id={msg.get('job_id')}, type={msg.get('type')}"
                )


async def status_broadcast_dispatcher():
    await _broadcast_dispatcher(status_broadcast_queue, status_connections, "status")


async def logs_broadcast_dispatcher():
    await _broadcast_dispatcher(logs_broadcast_queue, logs_connections, "logs")


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
            "agents": result.get("agents", {}),
        }
        logger.info(f"[WS-STATUS] 发送初始状态: job_id={job_id}")
        await websocket.send(json.dumps({"type": "state_update", "data": state_data}, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"[WS-STATUS] 发送初始状态失败: job_id={job_id}, error={e}")

    try:
        while True:
            await asyncio.sleep(10)
            try:
                await websocket.send(json.dumps({"type": "heartbeat", "channel": "status"}, ensure_ascii=False))
            except Exception:
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
            await asyncio.sleep(10)
            try:
                await websocket.send(json.dumps({"type": "heartbeat", "channel": "logs"}, ensure_ascii=False))
            except Exception:
                break
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"[WS-LOGS] 连接关闭: job_id={job_id}")
    except Exception as e:
        logger.warning(f"[WS-LOGS] 异常: {e}")
    finally:
        logs_connections[job_id].discard(websocket)
        logger.info(f"[WS-LOGS] 客户端断开: job_id={job_id}")
