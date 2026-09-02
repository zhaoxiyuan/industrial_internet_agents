"""
工作流 API
"""
import threading
import logging
import random
from datetime import datetime

logger = logging.getLogger("server")

_running_workflows = {}  # job_id -> thread


def handle_workflow_start(handler, app):
    """POST /api/workflow/start"""
    job_id = datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999):03d}"
    app["job_id"] = job_id

    logger.info(f"[POST] /api/workflow/start 进入: job_id={job_id}")

    response_data = {
        "status": "starting",
        "job_id": job_id,
        "message": "工作流启动中..."
    }
    logger.info(f"[POST] /api/workflow/start 响应: {response_data}")
    handler.send_json(response_data)

    def run_workflow_background(job_id, app):
        try:
            logger.info(f"[WORKFLOW] 工作流开始执行: job_id={job_id}")
            from web.ws.manager import broadcast_workflow_state
            broadcast_workflow_state(job_id)
            from agents.main_agent import run_workflow
            result = run_workflow(app, thread_id=job_id)
            logger.info(f"[WORKFLOW] 工作流执行完成: job_id={job_id}, result={result.get('status')}")
            broadcast_workflow_state(job_id)
        except Exception as e:
            logger.exception(f"[WORKFLOW] 工作流执行错误: job_id={job_id}")
            from web.ws.manager import broadcast_workflow_state
            broadcast_workflow_state(job_id)

    t = threading.Thread(target=run_workflow_background, args=(job_id, app))
    t.daemon = True
    t.start()
    _running_workflows[job_id] = t


def handle_workflow_confirm(handler, data):
    """POST /api/workflow/confirm"""
    from agents.main_agent import confirm_and_continue, list_pending_confirmations
    from web.ws.manager import broadcast_workflow_state

    thread_id = data.get("thread_id")
    stage = data.get("stage")
    decision = data.get("decision")
    async_execute = data.get("async_execute", False)

    logger.info(f"[POST] /api/workflow/confirm 进入: thread_id={thread_id}, stage={stage}, decision={decision}, async={async_execute}")

    if not thread_id:
        logger.warning(f"[POST] /api/workflow/confirm 参数错误: thread_id为空")
        handler.send_json({
            "status": "error", "error": "thread_id 不能为空",
            "pending": [], "pending_data": {}, "confirmed": [],
            "current_stage": "", "thread_id": None, "job_id": None,
        })
        return

    if not stage:
        logger.warning(f"[POST] /api/workflow/confirm 参数错误: stage为空")
        handler.send_json({
            "status": "error", "error": "stage 不能为空",
            "pending": [], "pending_data": {}, "confirmed": [],
            "current_stage": "", "thread_id": thread_id, "job_id": thread_id,
        })
        return

    try:
        result = confirm_and_continue(thread_id, stage, decision, async_execute=async_execute)

        if result is None:
            logger.warning(f"[POST] /api/workflow/confirm 结果为空")
            handler.send_json({
                "status": "error", "error": "执行结果为空",
                "pending": [], "pending_data": {}, "confirmed": [],
                "current_stage": stage, "thread_id": thread_id, "job_id": thread_id,
            })
            return

        if async_execute:
            logger.info(f"[POST] /api/workflow/confirm 异步响应: status={result.get('status')}")
            handler.send_json(result)
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
        handler.send_json(response_data)
        broadcast_workflow_state(thread_id)
    except Exception as e:
        logger.exception(f"[POST] /api/workflow/confirm 异常: thread_id={thread_id}")
        handler.send_json({
            "status": "error", "error": str(e),
            "pending": [], "pending_data": {}, "confirmed": [],
            "current_stage": "", "thread_id": thread_id, "job_id": thread_id,
        })


def handle_workflow_state_get(handler, thread_id):
    """GET /api/workflow/state"""
    from agents.main_agent import get_workflow_state, list_pending_confirmations

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
        handler.send_json(response_data)
    else:
        logger.info(f"[GET] /api/workflow/state 响应: status=idle")
        handler.send_json({"status": "idle", "pending": [], "confirmed": [], "current_stage": ""})