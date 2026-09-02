"""
WebSocket 连接管理 + 广播队列 + 广播函数
"""
import json
import asyncio
import queue
from collections import defaultdict
from datetime import datetime

PORT = 8080
WS_STATUS_PORT = PORT + 1   # 状态通道端口
WS_LOGS_PORT = PORT + 2     # 日志通道端口

# ============ 状态通道 WebSocket 连接管理 ============
status_connections = defaultdict(set)
status_broadcast_queue = queue.Queue()

# ============ 日志通道 WebSocket 连接管理 ============
logs_connections = defaultdict(set)
logs_broadcast_queue = queue.Queue()


async def process_request(ws, request):
    """在握手阶段将路径存储在 ws 对象上"""
    ws.path = request.path
    return None


def broadcast_workflow_state(job_id):
    """从后台线程推送工作流状态（跨线程调用）"""
    import logging
    logger = logging.getLogger("server")
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
        message = {
            "type": "state_update",
            "job_id": job_id,
            "data": state_data
        }
        try:
            status_broadcast_queue.put_nowait(message)
        except queue.Full:
            pass
    except Exception as e:
        logger.warning(f"[WS-STATUS] 广播状态失败: job_id={job_id}, error={e}")


def broadcast_workflow_log(job_id: str, level: str, source: str, message: str, data: dict = None):
    """从后台线程推送工作流日志（跨线程调用）"""
    import logging
    logger = logging.getLogger("server")
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
        try:
            logs_broadcast_queue.put_nowait(log_entry)
        except queue.Full:
            pass
    except Exception as e:
        logger.warning(f"[WS-LOGS] 广播日志失败: job_id={job_id}, error={e}")


def get_status_broadcast_queue():
    return status_broadcast_queue


def get_logs_broadcast_queue():
    return logs_broadcast_queue