"""P7 Risk Agent - 空壳，保留 LangChain 工具桩供 main_agent.py 调用"""
import json
import logging
from langchain_core.tools import tool

from .utils import get_stage_logger

# 配置日志
logger = logging.getLogger("p7_risk_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@tool
def risk_analyze(event_id: str) -> str:
    """风险分析工具。供 main_agent P7 阶段调用。"""
    return json.dumps({"status": "ok", "result": {"event_id": event_id, "risk_level": 0}})


@tool
def risk_list(task_id: str) -> str:
    """风险列表工具。供 main_agent P7 阶段调用。"""
    return json.dumps({"status": "ok", "result": {"events": []}})


# 兼容导出
create_risk_agent = None
create_risk_agent_with_hitl = None
run_risk_agent = None
risk_demo = None
risk_grade = None
risk_cases = None


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(job_id: str) -> dict:
    """P7 阶段执行入口：风险研判与分级

    读取 P6 结果中的 candidate_events，逐个进行风险分析
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger, add_job_log

    log = get_stage_logger("P7")
    log.log_enter(job_id)

    result = {
        "job_id": job_id,
        "stage": "P7",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    try:
        # 1. 读取前置阶段结果
        p6_result = read_json_file(get_stage_result_path(job_id, "p6"))
        task_id = p6_result.get("task_id", "")
        events = p6_result.get("candidate_events", [])
        logger.info(f"[P7] task_id={task_id}, events_count={len(events)}")

        # 2. 遍历事件进行风险分析
        risk_events = []
        for event in events:
            event_id = event.get("event_id", "")
            if event_id:
                logger.info(f"[P7] 调用 risk_analyze: event_id={event_id}")
                risk_result = json.loads(risk_analyze.invoke(event_id))
                log.log_tool_call("risk_analyze", {"event_id": event_id}, risk_result)
                if "result" in risk_result:
                    risk_events.append(risk_result["result"])

        # 3. 如果无风险事件，调用备用函数
        if not risk_events:
            logger.info(f"[P7] 无风险事件，调用 risk_list.invoke")
            list_result = json.loads(risk_list.invoke(task_id))
            log.log_tool_call("risk_list", {"task_id": task_id}, list_result)
            risk_events = list_result.get("result", {}).get("events", [])

        result["risk_events"] = risk_events
        result["completed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

        high_risk = [e for e in risk_events if e.get("level") in ("HIGH", "CRITICAL")]
        if high_risk:
            result["pending_confirmation"] = {
                "type": "high_risk",
                "count": len(high_risk),
                "message": f"检测到 {len(high_risk)} 个高风险事件，需要人工确认"
            }

    except Exception as e:
        log.log_error(job_id, e)
        result["error"] = str(e)

    write_json_file(get_stage_result_path(job_id, "p7"), result)
    add_job_log(job_id, {"action": "execute_p7", "result": "success" if result["completed"] else "failed"})
    log.log_exit(job_id, result)
    return result
