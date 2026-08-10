"""
P1: 作业预约、JSA分析与作业票
Permit Agent - 处理作业申请、JSA分析和作业票生成
支持 HumanInTheLoop - Agent 层级中断
"""
import logging
from typing import Any, TypedDict, Dict
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

from .model.chat_model import create_chat_model
from .utils.agent_utils import extract_output
from .utils.logging_handler import AgentLoggingCallback, get_logging_callback, push_websocket_log
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.system_prompt import load_system_prompt

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

    push_websocket_log("*", "INFO", "TOOL", f">>> permit_submit 工具入口", {"application": application[:200] + "..." if len(application) > 200 else application})
    logger.info(f"[permit_submit] >>> 工具入口: application={application[:200]}...")
    try:
        data = json.loads(application)
    except json.JSONDecodeError:
        logger.warning(f"[permit_submit] !!! JSON 解析失败")
        push_websocket_log("*", "ERROR", "TOOL", "!!! permit_submit JSON解析失败")
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
    push_websocket_log("*", "INFO", "TOOL", f"<<< permit_submit 工具出口", {"task_id": task_id, "permit_draft_id": permit_draft_id, "missing_fields_count": len(missing_fields)})
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

    push_websocket_log("*", "INFO", "TOOL", f">>> jsa_analyze 工具入口", {"task_id": task_id})
    logger.info(f"[jsa_analyze] >>> 工具入口: task_id={task_id}")
    if not task_id:
        logger.warning(f"[jsa_analyze] !!! task_id 为空")
        push_websocket_log("*", "ERROR", "TOOL", "!!! jsa_analyze task_id为空")
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    # JSA 分析过程日志
    push_websocket_log("*", "INFO", "TOOL", f"JSA 分析开始: task_id={task_id}", {"step": "start"})

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
        push_websocket_log("*", "INFO", "TOOL", f"识别危害因素: [{hazard['id']}] {hazard['description']}", {
            "hazard_id": hazard["id"],
            "severity": hazard["severity"],
            "measures_count": len(hazard["measures"])
        })
        for measure in hazard["measures"]:
            push_websocket_log("*", "DEBUG", "TOOL", f"  -> 措施: {measure}", {"hazard_id": hazard["id"], "measure": measure})

    # 计算完整性得分
    completeness_score = 0.85
    missing_items = ["建议补充应急救援预案"]

    push_websocket_log("*", "WARNING", "TOOL", f"JSA 分析完成: 识别到 {len(hazards)} 个危害因素, 完整性得分: {completeness_score}", {
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

    push_websocket_log("*", "INFO", "TOOL", f">>> permit_generate_draft 工具入口", {"task_id": task_id})
    logger.info(f"[permit_generate_draft] >>> 工具入口: task_id={task_id}")
    if not task_id:
        logger.warning(f"[permit_generate_draft] !!! task_id 为空")
        push_websocket_log("*", "ERROR", "TOOL", "!!! permit_generate_draft task_id为空")
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
    push_websocket_log("*", "INFO", "TOOL", f"<<< permit_generate_draft 工具出口", {"permit_draft_id": result['permit_draft_id'], "missing_fields_count": len(result['missing_fields'])})
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

    push_websocket_log("*", "INFO", "TOOL", f">>> permit_check 工具入口", {"permit_id": permit_id})
    logger.info(f"[permit_check] >>> 工具入口: permit_id={permit_id}")
    if not permit_id:
        logger.warning(f"[permit_check] !!! permit_id 为空")
        push_websocket_log("*", "ERROR", "TOOL", "!!! permit_check permit_id为空")
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
    push_websocket_log("*", "INFO", "TOOL", f"<<< permit_check 工具出口", {"permit_id": permit_id, "status": result['status']})
    return json.dumps(make_response("permit check", result), ensure_ascii=False)


# ============================================================
# Agent 工厂 (HITL Enabled)
# ============================================================

# Agent 层级 Checkpointer - 用于 Agent 内部中断
_permit_checkpointer = MemorySaver()

# 全局 Agent 注册表 - 按 thread_id 缓存 Agent 实例，支持中断恢复
_agent_registry: Dict[str, Any] = {}


def create_permit_agent():
    """创建 P1 作业许可 Agent（基础版本，无 HITL）"""
    logger.info("[create_permit_agent] 创建 P1 Permit Agent（无 HITL）")
    llm = create_chat_model()
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P1"))


def create_permit_agent_with_hitl(thread_id: str = "default"):
    """创建 P1 作业许可 Agent - 支持 HumanInTheLoop

    使用 HumanInTheLoopMiddleware 使所有工具调用前都暂停等待人工确认

    Args:
        thread_id: 线程ID，用于注册表管理
    """
    # 复用注册表中的 Agent（支持同一 thread_id 的中断恢复）
    if thread_id in _agent_registry:
        logger.info(f"[create_permit_agent_with_hitl] 复用已有 Agent: thread_id={thread_id}")
        return _agent_registry[thread_id]

    logger.info(f"[create_permit_agent_with_hitl] 创建新 Agent: thread_id={thread_id}")
    llm = create_chat_model()
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]

    # 创建 HITL Middleware - 所有工具都需要人工确认
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={
            "permit_submit": True,      # 作业申请需要确认
            "jsa_analyze": True,       # JSA分析需要确认
            "permit_generate_draft": True,  # 生成作业票需要确认
            "permit_check": True,       # 查询状态需要确认
        }
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P1"),
        middleware=[hitl_middleware],
        checkpointer=_permit_checkpointer,
    )

    # 注册到全局表
    _agent_registry[thread_id] = agent
    logger.info(f"[create_permit_agent_with_hitl] Agent 已注册: thread_id={thread_id}")
    return agent


def run_permit_agent_with_hitl(message: str, thread_id: str = "default", resume: bool = False) -> dict:
    """运行 P1 作业许可 Agent（支持 HITL 中断）

    Args:
        message: 输入消息
        thread_id: 线程ID（用于 checkpoint 恢复）
        resume: 是否从中断点恢复（True=清除checkpoint重新执行）

    Returns:
        包含 {"result": ..., "interrupted": bool, "next": list}
    """
    import os
    push_websocket_log(thread_id, "INFO", "AGENT", f">>> P1 Agent 入口", {"message": message[:100] + "..." if message and len(message) > 100 else message, "resume": resume})
    logger.info(f"[run_permit_agent_with_hitl] >>> Agent 入口: thread_id={thread_id}, message={message[:100] if message else 'None'}, resume={resume}")

    # 首次执行时：先用非 HITL agent 执行一次获取日志（工具完整执行）
    if not resume and message and not is_agent_interrupted(thread_id):
        push_websocket_log(thread_id, "INFO", "AGENT", f"[P1] 第一阶段：执行 JSA 分析并输出日志")
        logger.info(f"[run_permit_agent_with_hitl] 第一阶段：非 HITL 执行获取日志")
        # 创建非 HITL agent 完整执行（用于输出日志）
        llm = create_chat_model()
        tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
        logging_agent = create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P1"))
        # 非 HITL 执行，工具会完整执行并输出日志
        logging_result = logging_agent.invoke({"messages": [HumanMessage(content=message)]})
        push_websocket_log(thread_id, "INFO", "AGENT", f"[P1] JSA 分析完成，开始等待人工确认")
        logger.info(f"[run_permit_agent_with_hitl] 第一阶段完成，继续 HITL 执行")

    # 恢复执行时：从文件读取原始消息，并清除checkpoint重新执行
    if resume and not message:
        # 读取保存的原始消息
        jobs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jobs")
        msg_file = os.path.join(jobs_dir, thread_id, "p1_original_message.txt")
        if os.path.exists(msg_file):
            with open(msg_file, "r", encoding="utf-8") as f:
                message = f.read()
            logger.info(f"[run_permit_agent_with_hitl] 从文件恢复原始消息: length={len(message)}")

        # 清除该 thread_id 的 checkpoint，重新执行
        config = {"configurable": {"thread_id": thread_id}}

        # 获取 agent 并清除 checkpoint
        if thread_id in _agent_registry:
            agent = _agent_registry[thread_id]
            # 清除 checkpoint：通过写入空状态
            try:
                agent.delete_state(config)
                logger.info(f"[run_permit_agent_with_hitl] checkpoint 已清除: thread_id={thread_id}")
            except Exception as e:
                logger.warning(f"[run_permit_agent_with_hitl] 清除 checkpoint 失败: {e}")

        # 创建新的非 HITL agent 来执行（避免再次中断）
        # 不设置 checkpointer，因为不需要中断恢复
        logger.info(f"[run_permit_agent_with_hitl] 创建非 HITL Agent 重新执行")
        push_websocket_log(thread_id, "INFO", "AGENT", f"创建非 HITL Agent 重新执行")
        llm = create_chat_model()
        tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
        fresh_agent = create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P1"))
        result = fresh_agent.invoke({"messages": [HumanMessage(content=message)]}, config)

        # 非 HITL agent 不会中断，直接返回结果
        logger.info(f"[run_permit_agent_with_hitl] <<< 非 HITL Agent 执行完成")
        push_websocket_log(thread_id, "INFO", "AGENT", f"<<< 非 HITL Agent 执行完成")
        return {
            "result": extract_output(result),
            "interrupted": False,
            "next": []
        }

    # 正常首次执行或新消息执行
    agent = create_permit_agent_with_hitl(thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    # 保存原始消息到文件（用于恢复）
    if message:
        jobs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jobs")
        os.makedirs(os.path.join(jobs_dir, thread_id), exist_ok=True)
        msg_file = os.path.join(jobs_dir, thread_id, "p1_original_message.txt")
        with open(msg_file, "w", encoding="utf-8") as f:
            f.write(message)
        logger.info(f"[run_permit_agent_with_hitl] 原始消息已保存: {msg_file}")

    # 检查是否有中断点可恢复
    state = agent.get_state(config)
    if state and state.next:
        logger.info(f"[run_permit_agent_with_hitl] 从中断点恢复执行: next={list(state.next)}")
        push_websocket_log(thread_id, "INFO", "AGENT", f"从中断点恢复执行", {"next": list(state.next)})
        result = agent.invoke(None, config)
    else:
        logger.info(f"[run_permit_agent_with_hitl] 正常执行新消息")
        push_websocket_log(thread_id, "INFO", "AGENT", f"正常执行新消息")
        result = agent.invoke({"messages": [HumanMessage(content=message)]}, config)

    # 检查是否中断
    final_state = agent.get_state(config)
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


def run_permit_agent(message: str) -> str:
    """运行 P1 作业许可 Agent"""
    agent = create_permit_agent()
    result = agent.invoke({"messages": [HumanMessage(content=message)]})
    return extract_output(result)


def permit_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_permit_agent(message)
