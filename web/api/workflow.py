"""
工作流 API
"""
import threading
import logging
import random
import os
import re
from datetime import datetime

logger = logging.getLogger("server")

_running_workflows = {}  # job_id -> thread


def _valid_job_id(job_id):
    return bool(re.fullmatch(r"\d{17}", str(job_id or "")))


def _read_job_files(job_id):
    """读取历史作业的持久化数据，不修改工作流状态。"""
    from agents.workflow import get_job_dir, read_json_file

    job_dir = get_job_dir(job_id)
    if not os.path.isdir(job_dir):
        return None

    def read(name):
        return read_json_file(os.path.join(job_dir, name))

    application_file = read("application.json")
    workflow_status = read("workflow_status.json")
    execution_status = read("execution_status.json")
    p1_result = read("p1_result.json")
    permit = read("permit.json")
    logs = read("logs.json")
    if not isinstance(logs, list):
        logs = []

    application = application_file.get("application", {}) if isinstance(application_file, dict) else {}
    main_status = workflow_status.get("main_agent", {}) if isinstance(workflow_status, dict) else {}
    status = main_status.get("status")
    current_stage = main_status.get("current_stage")

    if not status and isinstance(execution_status, dict):
        current_stage = execution_status.get("current_stage")
        stages = execution_status.get("stages", {})
        failed = next((name for name, item in stages.items() if item.get("status") == "failed"), None)
        waiting = next((name for name, item in stages.items() if item.get("status") == "waiting"), None)
        if failed:
            status, current_stage = "error", failed
        elif waiting:
            status, current_stage = "waiting", waiting
        elif current_stage == "completed":
            status = "completed"
        else:
            status = "running"

    status = status or "unknown"
    current_stage = current_stage or ""
    agents = workflow_status.get("agents", {}) if isinstance(workflow_status, dict) else {}
    pending = [stage for stage, item in agents.items() if item.get("status") == "waiting"]
    if (
        not pending
        and status != "completed"
        and isinstance(p1_result, dict)
        and p1_result.get("pending_confirmation")
    ):
        pending = ["P1"]

    updated_at = ""
    for source in (workflow_status, execution_status, p1_result, application_file):
        if isinstance(source, dict):
            updated_at = source.get("updated_at") or source.get("completed_at") or source.get("saved_at") or updated_at
            if updated_at:
                break

    return {
        "job_id": job_id,
        "thread_id": job_id,
        "status": status,
        "current_stage": current_stage,
        "pending": pending,
        "confirmed": [stage for stage, item in agents.items() if item.get("status") == "completed"],
        "agents": agents,
        "application": application,
        "permit": permit if isinstance(permit, dict) else {},
        "p1_result": p1_result if isinstance(p1_result, dict) else {},
        "execution_status": execution_status if isinstance(execution_status, dict) else {},
        "logs": logs[-200:],
        "created_at": (
            workflow_status.get("created_at", "") if isinstance(workflow_status, dict) else ""
        ) or (application_file.get("saved_at", "") if isinstance(application_file, dict) else ""),
        "updated_at": updated_at,
    }


def _job_summary(snapshot):
    application = snapshot.get("application", {})
    personnel = application.get("personnel") or []
    applicant = personnel[0].get("name", "") if personnel and isinstance(personnel[0], dict) else ""
    return {
        "job_id": snapshot["job_id"],
        "status": snapshot["status"],
        "current_stage": snapshot["current_stage"],
        "created_at": snapshot["created_at"],
        "updated_at": snapshot["updated_at"],
        "job_content": application.get("job_content", ""),
        "job_type": application.get("job_type") or application.get("permit_type") or "",
        "region": application.get("region") or application.get("work_location") or "",
        "applicant": application.get("applicant") or applicant,
        "can_continue": snapshot["status"] not in {"completed", "idle", "unknown"},
    }


def handle_workflow_history(handler, limit=50):
    """GET /api/workflow/history - 按时间倒序返回历史作业。"""
    from agents.workflow import get_jobs_dir

    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        limit = 50

    jobs_dir = get_jobs_dir()
    items = []
    if os.path.isdir(jobs_dir):
        for job_id in sorted(os.listdir(jobs_dir), reverse=True):
            if not _valid_job_id(job_id):
                continue
            snapshot = _read_job_files(job_id)
            if snapshot:
                items.append(_job_summary(snapshot))
            if len(items) >= limit:
                break
    handler.send_json({"status": "ok", "jobs": items, "count": len(items)})


def handle_workflow_job_detail(handler, job_id):
    """GET /api/workflow/job-detail - 返回查看和 P1 审批需要的完整数据。"""
    if not _valid_job_id(job_id):
        handler.send_json({"status": "error", "error": "job_id 必须是 17 位数字"}, status=400)
        return
    snapshot = _read_job_files(job_id)
    if not snapshot:
        handler.send_json({"status": "error", "error": "作业不存在"}, status=404)
        return

    pending_data = {}
    for stage in snapshot["pending"]:
        pending_info = snapshot["p1_result"].get("pending_confirmation", {}) if stage == "P1" else {}
        pending_data[stage] = {
            "stage": stage,
            "pending": pending_info,
            "application": snapshot["application"],
            "permit": snapshot["permit"],
            "p1_result": snapshot["p1_result"],
        }
    snapshot["pending_data"] = pending_data
    handler.send_json(snapshot)


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
        finally:
            _running_workflows.pop(job_id, None)

    t = threading.Thread(target=run_workflow_background, args=(job_id, app))
    t.daemon = True
    _running_workflows[job_id] = t
    t.start()


def handle_workflow_confirm(handler, data):
    """POST /api/workflow/confirm"""
    from agents.main_agent import confirm_and_continue, list_pending_confirmations
    from web.ws.manager import broadcast_workflow_state

    thread_id = data.get("thread_id")
    stage = data.get("stage")
    decision = data.get("decision")
    notes = data.get("notes", "")
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

    if decision not in {"approve", "reject"}:
        handler.send_json({
            "status": "error", "error": "decision 必须是 approve 或 reject",
            "pending": [], "pending_data": {}, "confirmed": [],
            "current_stage": stage, "thread_id": thread_id, "job_id": thread_id,
        }, status=400)
        return

    try:
        result = confirm_and_continue(
            thread_id, stage, decision, notes=notes, async_execute=async_execute
        )

        if result is None:
            logger.warning(f"[POST] /api/workflow/confirm 结果为空")
            handler.send_json({
                "status": "error", "error": "执行结果为空",
                "pending": [], "pending_data": {}, "confirmed": [],
                "current_stage": stage, "thread_id": thread_id, "job_id": thread_id,
            })
            return

        if result.get("status") == "error" or result.get("rejected"):
            handler.send_json(result)
            broadcast_workflow_state(thread_id)
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


def handle_latest_incomplete_workflow(handler):
    """GET /api/workflow/latest-incomplete - 找回最近一条未完成作业。"""
    from agents.main_agent import get_workflow_state, list_pending_confirmations
    from agents.workflow import get_jobs_dir

    jobs_dir = get_jobs_dir()
    if not os.path.isdir(jobs_dir):
        handler.send_json({"status": "none", "job_id": None})
        return

    for job_id in sorted(os.listdir(jobs_dir), reverse=True):
        if not re.fullmatch(r"\d{17}", job_id):
            continue
        result = get_workflow_state(job_id)
        workflow_status = result.get("status", "unknown")
        if workflow_status in {"completed", "idle", "unknown"}:
            continue
        pending = list_pending_confirmations(job_id)
        handler.send_json({
            "status": workflow_status,
            "job_id": job_id,
            "thread_id": job_id,
            "pending": [item.get("stage", "") for item in pending],
            "pending_data": {item.get("stage", ""): item for item in pending},
            "confirmed": result.get("confirmed_stages", []),
            "current_stage": result.get("current_stage", ""),
            "agents": result.get("agents", {}),
        })
        return

    handler.send_json({"status": "none", "job_id": None})

def handle_workflow_resume(handler, data):
    """POST /api/workflow/resume - 从失败阶段恢复执行

    请求体:
    {
        "job_id": "20260908093711563",
        "stage": "P2",  // 可选，不指定则从失败阶段开始
        "force": false   // 是否强制重试（忽略重试次数限制）
    }
    """
    job_id = str(data.get("job_id") or "")
    stage = data.get("stage")
    force = data.get("force") is True

    logger.info(f"[POST] /api/workflow/resume 进入: job_id={job_id}, stage={stage}, force={force}")

    if not job_id:
        handler.send_json({"status": "error", "error": "job_id 不能为空"}, status=400)
        return
    if not re.fullmatch(r"\d{17}", job_id):
        handler.send_json({"status": "error", "error": "job_id 必须是 17 位数字"}, status=400)
        return
    running = _running_workflows.get(job_id)
    if running and running.is_alive():
        handler.send_json({"status": "error", "error": "该作业正在执行，请勿重复恢复"}, status=409)
        return

    try:
        from agents.workflow import (
            get_execution_status, get_failed_stage, can_retry_stage,
            read_json_file, get_job_dir,
        )
        from agents.main_agent import run_workflow, STAGE_EXECUTORS

        job_dir = get_job_dir(job_id)
        if not os.path.exists(job_dir):
            handler.send_json({"status": "error", "error": "作业不存在"}, status=404)
            return

        # 获取执行状态
        get_execution_status(job_id)

        # 确定要恢复的阶段
        if not stage:
            stage = get_failed_stage(job_id)
            if not stage:
                handler.send_json({
                    "status": "error",
                    "error": "没有找到失败的阶段"
                }, status=400)
                return
        if not isinstance(stage, str):
            handler.send_json({"status": "error", "error": "stage 必须是字符串"}, status=400)
            return
        stage = stage.upper()
        if stage not in STAGE_EXECUTORS:
            handler.send_json({
                "status": "error",
                "error": f"无效阶段: {stage}"
            }, status=400)
            return

        # 检查是否可以重试
        if not force and not can_retry_stage(job_id, stage):
            handler.send_json({
                "status": "error",
                "error": f"阶段 {stage} 当前不可重试；如需重新执行请使用 force=true"
            }, status=400)
            return

        # 读取作业申请
        app_file = job_dir + "/application.json"
        application_file = read_json_file(app_file)
        application = application_file.get("application")

        if not application:
            handler.send_json({
                "status": "error",
                "error": "找不到作业申请数据"
            }, status=404)
            return

        # 后台执行恢复
        def run_resume_background():
            try:
                logger.info(f"[RESUME] 开始恢复执行: job_id={job_id}, stage={stage}")
                from web.ws.manager import broadcast_workflow_state
                broadcast_workflow_state(job_id)

                # 从指定阶段开始执行
                result = run_workflow(
                    application,
                    thread_id=job_id,
                    start_stage=stage,
                    resume=True,
                    force=force,
                )

                logger.info(f"[RESUME] 恢复执行完成: job_id={job_id}, result={result.get('status')}")
                broadcast_workflow_state(job_id)
            except Exception as e:
                logger.exception(f"[RESUME] 恢复执行失败: job_id={job_id}")
                broadcast_workflow_state(job_id)
            finally:
                _running_workflows.pop(job_id, None)

        t = threading.Thread(target=run_resume_background)
        t.daemon = True
        _running_workflows[job_id] = t
        t.start()

        response_data = {
            "status": "resuming",
            "job_id": job_id,
            "stage": stage,
            "message": f"从阶段 {stage} 恢复执行中..."
        }
        logger.info(f"[POST] /api/workflow/resume 响应: {response_data}")
        handler.send_json(response_data)

    except Exception as e:
        logger.exception(f"[POST] /api/workflow/resume 异常: {e}")
        handler.send_json({
            "status": "error",
            "error": str(e)
        }, status=500)


def handle_execution_status(handler, job_id):
    """GET /api/workflow/execution-status?job_id=xxx - 查询执行状态"""
    logger.info(f"[GET] /api/workflow/execution-status 进入: job_id={job_id}")

    if not job_id:
        handler.send_json({"status": "error", "error": "job_id 不能为空"}, status=400)
        return
    if not re.fullmatch(r"\d{17}", str(job_id)):
        handler.send_json({"status": "error", "error": "job_id 必须是 17 位数字"}, status=400)
        return

    try:
        from agents.workflow import get_execution_status, get_job_dir, STAGE_CONFIG

        if not os.path.exists(get_job_dir(job_id)):
            handler.send_json({"status": "error", "error": "作业不存在"}, status=404)
            return

        execution_status = get_execution_status(job_id)

        # 构建响应
        response_data = {
            "job_id": job_id,
            "current_stage": execution_status.get("current_stage"),
            "created_at": execution_status.get("created_at"),
            "updated_at": execution_status.get("updated_at"),
            "stages": {}
        }

        # 添加每个阶段的摘要信息
        for stage, info in execution_status.get("stages", {}).items():
            response_data["stages"][stage] = {
                "status": info.get("status"),
                "attempts": info.get("attempts", 0),
                "last_attempt_at": info.get("last_attempt_at"),
                "completed_at": info.get("completed_at"),
                "last_error": info.get("last_error"),
                "last_error_type": info.get("last_error_type"),
                "history": info.get("history", []),
                "config": STAGE_CONFIG.get(stage, {})
            }

        logger.info(f"[GET] /api/workflow/execution-status 响应: job_id={job_id}")
        handler.send_json(response_data)

    except Exception as e:
        logger.exception(f"[GET] /api/workflow/execution-status 异常: {e}")
        handler.send_json({
            "status": "error",
            "error": str(e)
        }, status=500)
