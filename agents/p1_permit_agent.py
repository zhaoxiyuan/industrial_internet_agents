"""
P1: 作业预约、JSA分析与作业票
Permit Agent - 处理作业申请、JSA分析和作业票生成
支持 HumanInTheLoop - P1 阶段末统一审批
"""
import json
import logging
from contextvars import ContextVar
from typing import Any, TypedDict, Dict
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .model.chat_model import create_chat_model_with_logging
from .model.config import get_llm_params
from .utils.agent_utils import extract_output
from .utils.logging_handler import AgentLoggingCallback, get_logging_callback, push_websocket_log, get_agent_config
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.system_prompt import load_system_prompt
from .utils import add_job_log

# 配置日志
logger = logging.getLogger("p1_permit_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


_p1_job_context = ContextVar("p1_job_id", default="*")


JSA_PROFILES = {
    "动火作业": [
        ("火灾、爆炸", "高", ["隔离可燃介质和能量", "清除动火点5米内可燃物", "配备消防器材并设专人监护"]),
        ("可燃气体超限", "高", ["作业前30分钟内完成有代表性的气体检测", "便携式仪器检测值不大于10%LEL", "作业期间按要求复测"]),
        ("火花飞溅和灼烫", "中", ["设置防火花飞溅隔离", "佩戴防护面罩、手套和阻燃防护用品"]),
    ],
    "受限空间作业": [
        ("缺氧、富氧或有毒有害气体", "高", ["作业前和作业中检测氧气、可燃及有毒气体", "持续通风", "超限立即停止并撤离"]),
        ("人员被困或盲目施救", "高", ["出入口外全程专人监护", "登记并清点人员和工器具", "配备救援器材并明确联络方式"]),
        ("意外启动或介质突入", "高", ["管线加盲板或物理断开", "电气断电、上锁挂牌", "禁止以关闭阀门代替隔离"]),
    ],
    "高处作业": [
        ("高处坠落", "高", ["验收作业平台、脚手架和防坠落设施", "安全带高挂低用", "无可靠挂点时设置生命线"]),
        ("物体打击", "中", ["工具和零件放入工具袋", "设置警戒区", "禁止上下抛掷物品"]),
        ("恶劣天气影响", "高", ["五级及以上风或暴雨、浓雾时停止露天高处作业", "雨雪天采取防滑防寒措施"]),
    ],
    "吊装作业": [
        ("吊物坠落或起重机失稳", "高", ["核验起重量、额定能力和地基承载力", "检查吊具、索具及安全装置", "正式起吊前试吊"]),
        ("人员进入吊装警戒区", "高", ["设置警戒区并专人监护", "吊物和起重臂移动区域下方禁止人员停留"]),
    ],
    "临时用电作业": [
        ("触电", "高", ["由合格电工接线", "配置漏电保护器和独立开关", "停送电执行上锁挂牌"]),
        ("电气火灾或爆炸", "高", ["爆炸危险区域使用相应防爆等级设备", "动力和照明线路分路设置"]),
    ],
}


def _infer_job_type(application: dict) -> str:
    explicit = application.get("job_type") or application.get("permit_type")
    if explicit:
        return str(explicit)
    text = str(application.get("job_content", ""))
    aliases = (("受限空间", "受限空间作业"), ("高空", "高处作业"), ("高处", "高处作业"), ("动火", "动火作业"), ("吊装", "吊装作业"), ("临时用电", "临时用电作业"), ("管线打开", "管线打开作业"))
    return next((name for keyword, name in aliases if keyword in text), "非常规作业")


def _build_fallback_permit(application: dict, job_id: str) -> tuple:
    """将已持久化的实际申请整理为审批可读的作业票，避免使用工具内的固定示例值。"""
    job_type = _infer_job_type(application)
    profile = JSA_PROFILES.get(job_type, [
        ("作业环境和条件变化", "中", ["作业前开展JSA和安全技术交底", "设置专人监护", "条件变化时立即停止并重新评估"])
    ])
    hazards = [
        {"id": f"H-{index:03d}", "description": description, "severity": severity, "measures": measures}
        for index, (description, severity, measures) in enumerate(profile, 1)
    ]
    missing = []
    for field in ("job_content", "region", "planned_start", "planned_end"):
        if not application.get(field):
            missing.append(field)
    if not application.get("personnel"):
        missing.append("personnel")
    if not application.get("equipment"):
        missing.append("equipment")
    if not application.get("job_level"):
        missing.append("job_level")
    for person in application.get("personnel") or []:
        if isinstance(person, dict) and not (person.get("qualifications") or person.get("qualification")):
            missing.append(f"personnel_{person.get('name') or 'unknown'}_qualifications")

    jsa_result = {
        "task_id": f"TASK-{job_id}",
        "hazards": hazards,
        "completeness_score": round(max(0.0, 1 - len(missing) * 0.08), 2),
        "missing_items": missing,
        "source": "application_rule_fallback",
    }
    permit_content = {
        "job_type": job_type,
        "job_level": application.get("job_level", ""),
        "job_content": application.get("job_content", ""),
        "region": application.get("region", ""),
        "work_unit": application.get("work_unit") or application.get("applicant_unit", ""),
        "territorial_unit": application.get("territorial_unit", ""),
        "work_location": application.get("work_location") or application.get("region", ""),
        "equipment": application.get("equipment", []),
        "medium": application.get("medium", ""),
        "personnel": application.get("personnel", []),
        "planned_start": application.get("planned_start", ""),
        "planned_end": application.get("planned_end", ""),
        "related_permits": application.get("related_permits", []),
        "attachments": application.get("attachments", []),
        "gas_detection": application.get("gas_detection", []),
        "hazards": hazards,
        "measures": [measure for hazard in hazards for measure in hazard["measures"]],
        "missing_fields": missing,
    }
    return jsa_result, permit_content, missing


def _push_p1_tool_log(level: str, message: str, data: dict = None):
    """将 P1 工具日志绑定到当前作业，避免通配日志串到其他作业窗口。"""
    push_websocket_log(_p1_job_context.get(), level, "TOOL", message, data)


# ============================================================
# Agent State Schema
# ============================================================

class PermitAgentState(TypedDict, total=False):
    """P1 Agent 内部状态"""
    messages: list



# ============================================================
# 工具定义
# ============================================================

@tool(description="提交作业申请，返回作业票草稿。当用户提供作业申请信息时触发。")
def permit_submit(application: str) -> str:
    """
    提交作业申请，返回作业票草稿。

    参数:
        application: 作业申请 JSON 字符串，包含 job_content, region, equipment,
                    personnel, planned_start, planned_end 等字段
    返回:
        标准 JSON 响应，包含 task_id, permit_draft_id, status, missing_fields
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> permit_submit 工具入口", {"application": application[:200] + "..." if len(application) > 200 else application})
    logger.info(f"[permit_submit] >>> 工具入口: application={application[:200]}...")
    try:
        data = json.loads(application)
    except json.JSONDecodeError:
        logger.warning(f"[permit_submit] !!! JSON 解析失败")
        _push_p1_tool_log("ERROR", "!!! permit_submit JSON解析失败")
        return json.dumps(make_error(
            code="PERMIT_INVALID",
            message="无效的 JSON 格式",
            recoverable=False
        ), ensure_ascii=False)

    # 提取基本信息
    job_content = data.get("job_content", "")
    region = data.get("region", "")
    equipment = data.get("equipment", [])
    personnel = data.get("personnel", [])

    # 生成 task_id
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_id = f"TASK-{job_content[:4].upper()}-{region[:2]}-{timestamp}"

    # 生成 permit_draft_id
    permit_draft_id = f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # 检查缺失字段
    missing_fields = []
    if not job_content:
        missing_fields.append("job_content")
    if not region:
        missing_fields.append("region")
    if not equipment:
        missing_fields.append("equipment")
    if not personnel:
        missing_fields.append("personnel")

    # 检查人员资质
    for p in personnel:
        quals = p.get("qualifications", [])
        if not quals:
            missing_fields.append(f"personnel_{p.get('name', 'unknown')}_qualifications")

    result = {
        "task_id": task_id,
        "permit_draft_id": permit_draft_id,
        "status": "draft",
        "missing_fields": missing_fields,
        "jsa_complete": len(missing_fields) == 0,
    }

    logger.info(f"[permit_submit] <<< 工具出口: task_id={task_id}, permit_draft_id={permit_draft_id}, missing_fields_count={len(missing_fields)}")
    _push_p1_tool_log("INFO", f"<<< permit_submit 工具出口", {"task_id": task_id, "permit_draft_id": permit_draft_id, "missing_fields_count": len(missing_fields)})
    return json.dumps(make_response("permit submit", result), ensure_ascii=False)


@tool(description="分析JSA，识别危害因素和对应措施。当用户请求分析JSA时触发。")
def jsa_analyze(task_id: str) -> str:
    """
    分析 JSA（作业安全分析），识别危害因素和对应措施。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 hazards 分析结果
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> jsa_analyze 工具入口", {"task_id": task_id})
    logger.info(f"[jsa_analyze] >>> 工具入口: task_id={task_id}")
    if not task_id:
        logger.warning(f"[jsa_analyze] !!! task_id 为空")
        _push_p1_tool_log("ERROR", "!!! jsa_analyze task_id为空")
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # JSA 分析过程日志
    _push_p1_tool_log("INFO", f"JSA 分析开始: task_id={task_id}", {"step": "start"})

    # 危害因素识别过程
    hazards = [
        {
            "id": "H-001",
            "description": "受限空间内存在有毒有害气体",
            "severity": "高",
            "measures": ["气体检测", "强制通风", "佩戴呼吸器"]
        },
        {
            "id": "H-002",
            "description": "高温设备烫伤风险",
            "severity": "中",
            "measures": ["设备降温", "佩戴防护手套", "设置警戒区域"]
        },
        {
            "id": "H-003",
            "description": "人员误入风险区域",
            "severity": "中",
            "measures": ["设置警戒标识", "专人监护", "门禁管理"]
        }
    ]

    # 记录每个危害因素的识别
    for hazard in hazards:
        _push_p1_tool_log("INFO", f"识别危害因素: [{hazard['id']}] {hazard['description']}", {
            "hazard_id": hazard["id"],
            "severity": hazard["severity"],
            "measures_count": len(hazard["measures"])
        })
        for measure in hazard["measures"]:
            _push_p1_tool_log("DEBUG", f"  -> 措施: {measure}", {"hazard_id": hazard["id"], "measure": measure})

    # 计算完整性得分
    completeness_score = 0.85
    missing_items = ["建议补充应急救援预案"]

    _push_p1_tool_log("WARNING", f"JSA 分析完成: 识别到 {len(hazards)} 个危害因素, 完整性得分: {completeness_score}", {
        "hazards_count": len(hazards),
        "completeness_score": completeness_score,
        "missing_items": missing_items
    })

    # 模拟 JSA 分析结果
    result = {
        "task_id": task_id,
        "hazards": hazards,
        "completeness_score": completeness_score,
        "missing_items": missing_items
    }
    return json.dumps(make_response("permit analyze-jsa", result), ensure_ascii=False)


@tool(description="生成作业票草稿，包含缺失项提示。当用户请求生成作业票草稿时触发。")
def permit_generate_draft(task_id: str) -> str:
    """
    生成作业票草稿。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 permit_draft_id, content, missing_fields
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> permit_generate_draft 工具入口", {"task_id": task_id})
    logger.info(f"[permit_generate_draft] >>> 工具入口: task_id={task_id}")
    if not task_id:
        logger.warning(f"[permit_generate_draft] !!! task_id 为空")
        _push_p1_tool_log("ERROR", "!!! permit_generate_draft task_id为空")
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "permit_draft_id": f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "content": {
            "task_id": task_id,
            "job_content": "受限空间作业",
            "region": "炼油厂区01",
            "equipment": ["反应器R-101", "管道P-205"],
            "medium": "原油",
            "personnel": [{"name": "张三", "badge_id": "P-101"}],
            "hazards": ["有毒有害气体", "高温烫伤", "人员误入"],
            "measures": ["气体检测", "通风", "警戒标识"]
        },
        "missing_fields": ["personnel_qualifications", "emergency_plan"],
        "requires_approval": True
    }

    logger.info(f"[permit_generate_draft] <<< 工具出口: permit_draft_id={result['permit_draft_id']}, missing_fields_count={len(result['missing_fields'])}")
    _push_p1_tool_log("INFO", f"<<< permit_generate_draft 工具出口", {"permit_draft_id": result['permit_draft_id'], "missing_fields_count": len(result['missing_fields'])})
    return json.dumps(make_response("permit generate-draft", result), ensure_ascii=False)


@tool(description="查询作业票状态。当用户查询作业票状态时触发。")
def permit_check(permit_id: str) -> str:
    """
    查询作业票状态。

    参数:
        permit_id: 作业票ID
    返回:
        标准 JSON 响应，包含 permit_id, status, task_id 等
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> permit_check 工具入口", {"permit_id": permit_id})
    logger.info(f"[permit_check] >>> 工具入口: permit_id={permit_id}")
    if not permit_id:
        logger.warning(f"[permit_check] !!! permit_id 为空")
        _push_p1_tool_log("ERROR", "!!! permit_check permit_id为空")
        return json.dumps(make_error(
            code="PERMIT_NOT_FOUND",
            message="permit_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "permit_id": permit_id,
        "status": "draft",
        "task_id": f"TASK-{permit_id.replace('PD-', '')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": None,
        "approved_at": None
    }

    logger.info(f"[permit_check] <<< 工具出口: permit_id={permit_id}, status={result['status']}")
    _push_p1_tool_log("INFO", f"<<< permit_check 工具出口", {"permit_id": permit_id, "status": result['status']})
    return json.dumps(make_response("permit check", result), ensure_ascii=False)


# ============================================================
# Agent 工厂 (HITL Enabled)
# ============================================================

# Agent 层级 Checkpointer - 用于 Agent 内部中断
_permit_checkpointer = MemorySaver()

# 全局 Agent 注册表 - 按 thread_id 缓存 Agent 实例，支持中断恢复
_agent_registry: Dict[str, Any] = {}


def create_permit_agent(job_id: str = "*"):
    """创建 P1 作业许可 Agent（基础版本，无 HITL）"""
    logger.info("[create_permit_agent] 创建 P1 Permit Agent（无 HITL）")
    llm = create_chat_model_with_logging("P1", job_id)
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P1"))


def create_permit_agent_with_hitl(thread_id: str = "default"):
    """创建 P1 作业许可 Agent。

    工具调用阶段不再逐个中断；P1 全部准备工作完成后，由
    ``_process_p1_result`` 统一产生一次最终人工审批。

    Args:
        thread_id: 线程ID，用于注册表管理
    """
    # 复用注册表中的 Agent（支持同一 thread_id 的中断恢复）
    if thread_id in _agent_registry:
        logger.info(f"[create_permit_agent_with_hitl] 复用已有 Agent: thread_id={thread_id}")
        return _agent_registry[thread_id]

    logger.info(f"[create_permit_agent_with_hitl] 创建新 Agent: thread_id={thread_id}")
    llm = create_chat_model_with_logging("P1", thread_id)
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P1"),
        checkpointer=_permit_checkpointer,
    )

    # 注册到全局表
    _agent_registry[thread_id] = agent
    logger.info(f"[create_permit_agent_with_hitl] Agent 已注册: thread_id={thread_id}")
    return agent


def run_permit_agent_with_hitl(
    message: str | None,
    thread_id: str = "default",
    resume: bool = False,
    decision: str = "approve",
    notes: str = "",
) -> dict:
    """运行 P1 作业许可 Agent。

    新作业不做逐工具中断；resume 分支仅用于兼容已经产生的旧 checkpoint。

    Args:
        message: 输入消息
        thread_id: 线程ID（用于 checkpoint 恢复）
        resume: 是否从中断点恢复
        decision: 人工决定（approve/reject）
        notes: 人工决定备注

    Returns:
        包含 {"result": ..., "interrupted": bool, "next": list}
    """
    push_websocket_log(thread_id, "INFO", "AGENT", f">>> P1 Agent 入口", {"message": message[:100] + "..." if message and len(message) > 100 else message, "resume": resume})
    logger.info(f"[run_permit_agent_with_hitl] >>> Agent 入口: thread_id={thread_id}, message={message[:100] if message else 'None'}, resume={resume}")

    # 首次和恢复始终复用同一个 HITL Agent/checkpoint，工具不会预执行或重跑。
    agent = create_permit_agent_with_hitl(thread_id)
    hitl_config = get_agent_config(thread_id, "P1-HITL", get_llm_params())

    if resume:
        state = agent.get_state(hitl_config)
        if not state or not state.next:
            raise RuntimeError("P1 HITL checkpoint 不存在或已失效，无法从中断点继续")

        # 一个模型响应可能同时提出多个工具调用，阶段级确认对本批请求统一处理。
        action_count = 1
        interrupts = getattr(state, "interrupts", ()) or ()
        if interrupts:
            interrupt_value = getattr(interrupts[-1], "value", {}) or {}
            action_count = max(1, len(interrupt_value.get("action_requests", [])))
        decision_type = decision if decision in {"approve", "reject"} else "approve"
        decisions = [
            {"type": decision_type, **({"message": notes} if notes else {})}
            for _ in range(action_count)
        ]
        logger.info(
            f"[run_permit_agent_with_hitl] 从 checkpoint 继续: "
            f"thread_id={thread_id}, decision={decision_type}, actions={action_count}"
        )
        push_websocket_log(
            thread_id,
            "INFO",
            "AGENT",
            "P1 人工确认完成，从中断点继续",
            {"decision": decision_type, "actions": action_count},
        )
        context_token = _p1_job_context.set(thread_id)
        try:
            result = agent.invoke(Command(resume={"decisions": decisions}), hitl_config)
        finally:
            _p1_job_context.reset(context_token)
    else:
        if not message:
            raise ValueError("P1 首次执行时 message 不能为空")
        logger.info(f"[run_permit_agent_with_hitl] 正常执行新消息")
        push_websocket_log(thread_id, "INFO", "AGENT", f"正常执行新消息")
        context_token = _p1_job_context.set(thread_id)
        try:
            result = agent.invoke({"messages": [HumanMessage(content=message)]}, hitl_config)
        finally:
            _p1_job_context.reset(context_token)

    # 检查是否中断
    final_state = agent.get_state(hitl_config)
    interrupted = bool(final_state.next)

    if interrupted:
        logger.info(f"[run_permit_agent_with_hitl] !!! Agent 被中断: next={list(final_state.next)}")
        push_websocket_log(thread_id, "WARNING", "AGENT", f"!!! Agent 被中断", {"next_tools": list(final_state.next)})
    else:
        logger.info(f"[run_permit_agent_with_hitl] <<< Agent 执行完成")
        push_websocket_log(thread_id, "INFO", "AGENT", f"<<< Agent 执行完成")

    return {
        "result": extract_output(result) if not interrupted else None,
        "interrupted": interrupted,
        "next": list(final_state.next) if final_state.next else []
    }


def is_agent_interrupted(thread_id: str) -> bool:
    """检查指定 thread_id 的 Agent 是否处于中断状态"""
    if thread_id not in _agent_registry:
        return False
    agent = _agent_registry[thread_id]
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    return bool(state and state.next)


def get_agent_next_tools(thread_id: str) -> list:
    """获取 Agent 下一个待执行工具"""
    if thread_id not in _agent_registry:
        return []
    agent = _agent_registry[thread_id]
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    return list(state.next) if state and state.next else []


def clear_agent_registry(thread_id: str = None):
    """清除 Agent 注册表"""
    global _agent_registry
    if thread_id:
        _agent_registry.pop(thread_id, None)
    else:
        _agent_registry = {}


def reset_permit_execution(thread_id: str):
    """丢弃被否决的 P1 中断点，使下一次重试成为一次全新的执行。"""
    clear_agent_registry(thread_id)
    _permit_checkpointer.delete_thread(thread_id)


def run_permit_agent(message: str) -> str:
    """运行 P1 作业许可 Agent"""
    agent = create_permit_agent()
    agent_config = get_agent_config("default", "P1", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def permit_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_permit_agent(message)


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(
    job_id: str,
    resume: bool = False,
    decision: str = "approve",
    notes: str = "",
) -> dict:
    """P1 阶段执行入口：作业预约、JSA分析与作业票

    支持 HumanInTheLoop 中断恢复
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_job_dir, get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger

    log = get_stage_logger("P1")
    log.log_enter(job_id, {"resume": resume})
    result_file = get_stage_result_path(job_id, "p1")
    existing_result = read_json_file(result_file)

    result = {
        "job_id": job_id,
        "stage": "P1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    # 检查是否需要恢复执行
    if resume or is_agent_interrupted(job_id):
        log.log_hitl_interrupt(job_id, get_agent_next_tools(job_id))
        hitl_result = run_permit_agent_with_hitl(
            None,
            job_id,
            resume=True,
            decision=decision,
            notes=notes,
        )
        if not hitl_result:
            logger.error(f"[execute_stage P1] hitl_result 为空: job_id={job_id}, resume={resume}")
            return {"job_id": job_id, "stage": "P1", "completed": False, "error": "恢复执行失败：hitl_result 为空"}
        if hitl_result["interrupted"]:
            next_tools = hitl_result.get("next", [])
            result["pending_confirmation"] = {
                "type": "hitl_recover",
                "message": "P1 Agent 工具调用等待确认",
                "next_tools": next_tools,
            }
            log.log_exit(job_id, result)
            return result
        result_text = hitl_result.get("result", "{}")
        try:
            result_data = json.loads(result_text)
        except:
            result_data = {"result": result_text}
        result = _process_p1_result(job_id, result_data, existing_result)
        log.log_exit(job_id, result)
        return result

    # 首次执行
    app_file = get_job_dir(job_id) + "/application.json"
    application = read_json_file(app_file).get("application", {})

    if not application:
        logger.warning(f"[P1] !!! 作业申请为空: job_id={job_id}")
        result = {"error": "No application found", "completed": False}
        log.log_exit(job_id, result)
        return result

    try:
        app_str = json.dumps(application, ensure_ascii=False)
        message = f"""请处理以下作业申请：

作业申请内容：{app_str}

请依次执行：
1. 调用 permit_submit 工具提交作业申请
2. 调用 jsa_analyze 工具进行JSA分析
3. 调用 permit_generate_draft 工具生成作业票草稿"""

        logger.info(f"[P1] 调用 run_permit_agent_with_hitl: job_id={job_id}")
        push_websocket_log(job_id, "INFO", "AGENT", f"[P1] 开始执行作业许可流程")
        hitl_result = run_permit_agent_with_hitl(message, job_id)

        if hitl_result["interrupted"]:
            next_tools = hitl_result.get("next", [])
            log.log_hitl_interrupt(job_id, next_tools)
            add_job_log(job_id, {
                "action": "execute_p1_hitl_interrupt",
                "next_tools": next_tools
            })
            result["pending_confirmation"] = {
                "type": "hitl_tool_call",
                "message": "P1 Agent 工具调用需要人工确认",
                "next_tools": next_tools,
            }
            write_json_file(result_file, result)
            log.log_exit(job_id, result)
            return result

        result_text = hitl_result.get("result", "{}")
        try:
            result_data = json.loads(result_text)
        except:
            result_data = {"result": result_text}

        result = _process_p1_result(job_id, result_data, result)
        log.log_exit(job_id, result)
        return result

    except Exception as e:
        log.log_error(job_id, e)
        result["error"] = str(e)
        write_json_file(result_file, result)
        log.log_exit(job_id, result)
        return result


def _process_p1_result(job_id: str, result_data: dict, existing_result: dict) -> dict:
    """处理 P1 执行结果，提取并保存作业票数据"""
    from datetime import datetime, timezone
    from .workflow import get_job_dir, get_stage_result_path, read_json_file, write_json_file

    result = existing_result.copy() if existing_result else {}
    for stale_key in ("rejected", "decision", "error"):
        result.pop(stale_key, None)
    result["job_id"] = job_id
    result["stage"] = "P1"
    result["completed"] = True
    result["completed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        submit_data = None

        if isinstance(result_data, dict):
            if "result" in result_data and isinstance(result_data["result"], dict):
                submit_data = result_data["result"]
            elif "task_id" in result_data or "permit_draft_id" in result_data:
                submit_data = result_data
            elif "schema_version" in result_data:
                submit_data = result_data
        elif isinstance(result_data, str):
            try:
                parsed = json.loads(result_data)
                if isinstance(parsed, dict):
                    if "result" in parsed and isinstance(parsed["result"], dict):
                        submit_data = parsed["result"]
                    else:
                        submit_data = parsed
            except json.JSONDecodeError:
                logger.warning(f"[_process_p1_result] 无法解析 JSON，尝试从文本提取: {result_data[:200]}")
                submit_data = {}

        if submit_data:
            result["task_id"] = submit_data.get("task_id", "")
            result["permit_draft_id"] = submit_data.get("permit_draft_id", "")
            result["jsa_result"] = submit_data.get("jsa_result", {})
            result["permit_content"] = submit_data.get("permit_content", {})
            result["missing_fields"] = submit_data.get("missing_fields", [])
            logger.info(f"[_process_p1_result] 成功提取数据: task_id={result.get('task_id')}, permit_draft_id={result.get('permit_draft_id')}")
        else:
            logger.warning(f"[_process_p1_result] 无法提取 submit 数据，result_data={result_data}")

        app_file = get_job_dir(job_id) + "/application.json"
        application = read_json_file(app_file).get("application", {})
        fallback_jsa, fallback_content, fallback_missing = _build_fallback_permit(application, job_id)
        result["task_id"] = result.get("task_id") or f"TASK-{job_id}"
        result["permit_draft_id"] = result.get("permit_draft_id") or f"PD-{job_id}"
        result["jsa_result"] = result.get("jsa_result") or fallback_jsa
        result["permit_content"] = result.get("permit_content") or fallback_content
        result["missing_fields"] = result.get("missing_fields") or fallback_missing

        permit_data = {
            "task_id": result.get("task_id"),
            "permit_draft_id": result.get("permit_draft_id"),
            "application": application,
            "jsa_result": result.get("jsa_result"),
            "permit_content": result.get("permit_content"),
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        output_path = get_job_dir(job_id) + "/permit.json"
        write_json_file(output_path, permit_data)
        result["permit_file"] = output_path

    except Exception as e:
        result["error"] = str(e)

    # P1 只保留这一处人工控制点：所有工具和分析完成后统一审批一次。
    if not result.get("error"):
        result["pending_confirmation"] = {
            "type": "permit_final_approval",
            "fields": result.get("missing_fields", []),
            "message": "P1 作业申请、JSA 与作业票草稿已生成，请进行最终审批",
        }

    write_json_file(get_stage_result_path(job_id, "p1"), result)
    add_job_log(job_id, {
        "action": "execute_p1",
        "result": "success" if result["completed"] else "failed"
    })

    return result
