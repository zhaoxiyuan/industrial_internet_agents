"""
Web 前端 - P1-P10 工作流可视化与执行
集成 HumanInTheLoop 人工确认机制

页面结构:
1. 配置页面: 模型参数 + Agent 系统提示词配置
2. 执行页面: 工作流可视化 + 作业申请 + HITL 弹窗确认
"""
import json
import os
import gradio as gr
from dotenv import load_dotenv
from agents.workflow import (
    run_workflow,
    confirm_and_continue,
    get_workflow_state,
    list_pending_confirmations,
)

# 加载 .env 配置
load_dotenv()


# ============================================================
# 阶段元信息
# ============================================================

STAGE_INFO = {
    "P1": {"name": "作业预约、JSA与作业票", "icon": "📋",
     "description": "接收作业申请，识别作业类型、区域、设备、人员和时间；分析JSA；辅助形成作业票",
     "inputs": ["作业申请表单", "历史JSA", "模板", "人员信息"],
     "outputs": ["结构化任务", "JSA结果", "作业票草稿"],
     "tools": ["permit_submit", "jsa_analyze", "permit_generate_draft"],
     "human_confirm": "作业票审批", "color": "#4CAF50"},
    "P2": {"name": "作业任务获取", "icon": "📌",
     "description": "从作业票系统获取已批准或待执行任务，建立唯一任务实例",
     "inputs": ["作业票", "审批状态", "计划"], "outputs": ["任务实例", "初始状态"],
     "tools": ["task_list", "task_get", "task_instance_create", "task_subscribe"],
     "human_confirm": "是否纳入智能监测", "color": "#2196F3"},
    "P3": {"name": "作业上下文理解", "icon": "🧠",
     "description": "聚合作业类型、区域、设备、介质、风险、措施、人员、时间和关联作业",
     "inputs": ["作业票", "JSA", "场景数据"], "outputs": ["标准作业上下文包"],
     "tools": ["context_build", "context_validate", "context_history"],
     "human_confirm": "上下文缺失确认", "color": "#9C27B0"},
    "P4": {"name": "摄像与数据关联", "icon": "📹",
     "description": "匹配固定/移动摄像、传感器、定位和报警数据",
     "inputs": ["作业区域", "资源台账"], "outputs": ["数据源绑定关系", "监测清单"],
     "tools": ["binding_match", "binding_status", "binding_confirm", "binding_request_manual"],
     "human_confirm": "资源绑定确认", "color": "#FF9800"},
    "P5": {"name": "作业前条件核验", "icon": "✅",
     "description": "核对隔离、警戒、消防、气体检测、人员资质和PPE",
     "inputs": ["措施清单", "视频", "检测数据"], "outputs": ["核验结果", "缺失项", "开工建议"],
     "tools": ["verify_checklist", "verify_execute", "verify_recommendation"],
     "human_confirm": "允许开工或整改", "color": "#F44336"},
    "P6": {"name": "作业过程动态监测", "icon": "📡",
     "description": "持续获取视频、传感器、定位和作业状态，识别违章及条件变化",
     "inputs": ["视频流", "传感器", "定位", "规则"], "outputs": ["候选风险事件", "证据片段"],
     "tools": ["monitor_start", "monitor_stop", "monitor_status", "monitor_events"],
     "human_confirm": None, "color": "#E91E63"},
    "P7": {"name": "风险研判与分级", "icon": "⚠️",
     "description": "融合上下文、模型结果、规则和历史事件，去重并判级",
     "inputs": ["候选事件", "上下文", "规则", "知识"], "outputs": ["风险事件", "等级", "依据和建议"],
     "tools": ["risk_analyze", "risk_grade", "risk_cases", "risk_list"],
     "human_confirm": "高风险判断确认", "color": "#FF5722"},
    "P8": {"name": "人机协同处置", "icon": "🔧",
     "description": "按角色与权限推送责任人，形成整改、暂停、复核或升级建议",
     "inputs": ["风险事件", "处置规则", "组织关系"], "outputs": ["处置任务", "通知", "确认记录"],
     "tools": ["disposition_create", "disposition_confirm", "disposition_status", "disposition_list"],
     "human_confirm": "下发、暂停、恢复", "color": "#795548"},
    "P9": {"name": "闭环跟踪与报告", "icon": "🔄",
     "description": "跟踪整改状态，复核处置结果，汇总全过程记录",
     "inputs": ["处置反馈", "复核证据", "任务日志"], "outputs": ["闭环状态", "作业过程报告"],
     "tools": ["closure_status", "closure_verify", "closure_report", "closure_close"],
     "human_confirm": "关闭事件和作业", "color": "#607D8B"},
    "P10": {"name": "归档与复盘", "icon": "📦",
     "description": "归档票证、视频证据、风险事件、处置记录和报告，形成案例",
     "inputs": ["全过程数据"], "outputs": ["作业档案", "案例", "优化建议"],
     "tools": ["archive_task", "archive_cases", "archive_performance", "archive_suggestions"],
     "human_confirm": "档案确认、规则发布", "color": "#9E9E9E"},
}

ALL_STAGES = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]

# 系统提示词目录
SYSTEM_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "system_prompt")

# .env 文件路径
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


# ============================================================
# 辅助函数 - 配置相关
# ============================================================

def load_env_config():
    """加载 .env 配置"""
    return {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", ""),
        "model": os.getenv("OPENAI_MODEL", ""),
    }


def save_env_config(api_key, base_url, model):
    """保存配置到 .env"""
    lines = [
        "# OpenAI Configuration",
        f"OPENAI_API_KEY={api_key}",
        f"OPENAI_BASE_URL={base_url}",
        f"OPENAI_MODEL={model}",
        "MODEL_PROVIDER=openai",
    ]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return "✅ 配置已保存到 .env"


def load_system_prompt(stage: str) -> str:
    """加载系统提示词"""
    stage_to_file = {
        "P1": "P1_PERMIT_SYSTEM_PROMPT.md",
        "P2": "P2_TASK_SYSTEM_PROMPT.md",
        "P3": "P3_CONTEXT_SYSTEM_PROMPT.md",
        "P4": "P4_BINDING_SYSTEM_PROMPT.md",
        "P5": "P5_VERIFY_SYSTEM_PROMPT.md",
        "P6": "P6_MONITOR_SYSTEM_PROMPT.md",
        "P7": "P7_RISK_SYSTEM_PROMPT.md",
        "P8": "P8_DISPOSITION_SYSTEM_PROMPT.md",
        "P9": "P9_CLOSURE_SYSTEM_PROMPT.md",
        "P10": "P10_ARCHIVE_SYSTEM_PROMPT.md",
    }
    filename = stage_to_file.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    prompt_file = os.path.join(SYSTEM_PROMPT_DIR, filename)
    if os.path.exists(prompt_file):
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_system_prompt(stage: str, content: str) -> str:
    """保存系统提示词"""
    stage_to_file = {
        "P1": "P1_PERMIT_SYSTEM_PROMPT.md",
        "P2": "P2_TASK_SYSTEM_PROMPT.md",
        "P3": "P3_CONTEXT_SYSTEM_PROMPT.md",
        "P4": "P4_BINDING_SYSTEM_PROMPT.md",
        "P5": "P5_VERIFY_SYSTEM_PROMPT.md",
        "P6": "P6_MONITOR_SYSTEM_PROMPT.md",
        "P7": "P7_RISK_SYSTEM_PROMPT.md",
        "P8": "P8_DISPOSITION_SYSTEM_PROMPT.md",
        "P9": "P9_CLOSURE_SYSTEM_PROMPT.md",
        "P10": "P10_ARCHIVE_SYSTEM_PROMPT.md",
    }
    filename = stage_to_file.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    prompt_file = os.path.join(SYSTEM_PROMPT_DIR, filename)
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(content)
    return f"✅ {stage} 系统提示词已保存"


# ============================================================
# 辅助函数 - 工作流相关
# ============================================================

def build_workflow_diagram(completed, current, pending_confirmations=None):
    """构建可视化工作流图"""
    pending_confirmations = pending_confirmations or []
    lines = ['<div style="font-family:Arial,sans-serif;padding:20px;background:#fafafa;border-radius:12px;">']
    lines.append('<div style="display:flex;flex-direction:column;gap:0;">')

    for i, stage in enumerate(ALL_STAGES):
        info = STAGE_INFO[stage]
        is_completed = stage in completed
        is_current = stage == current
        is_pending = stage in pending_confirmations
        has_human_confirm = info.get("human_confirm") is not None

        # 节点状态样式
        if is_completed:
            node_style = f"background:linear-gradient(135deg,{info['color']}22,{info['color']}44);border:2px solid {info['color']};opacity:0.9;"
            status_icon = "✅"
            cursor = "default"
        elif is_pending:
            node_style = f"background:linear-gradient(135deg,#FFF8E1,#FFF3E0);border:3px solid #FFC107;box-shadow:0 0 15px #FFC10755;"
            status_icon = "⏸️"
            cursor = "pointer"
        elif is_current:
            node_style = f"background:linear-gradient(135deg,#FFF3E0,#FFE0B2);border:3px solid #FF9800;box-shadow:0 0 20px #FF980055;"
            status_icon = "⏳"
            cursor = "pointer"
        else:
            node_style = "background:#f5f5f5;border:2px solid #e0e0e0;opacity:0.7;"
            status_icon = "⬜"
            cursor = "pointer"

        # 人工确认标记
        human_tag = ""
        if has_human_confirm:
            human_tag = f'<span style="background:#ff9800;color:white;padding:2px 8px;border-radius:10px;font-size:11px;margin-left:8px;">👤 需确认</span>'

        # 工具标签
        tools_preview = ", ".join(info.get("tools", [])[:2]) + ("..." if len(info.get("tools", [])) > 2 else "")

        # 节点内容 - 点击时调用 JavaScript
        lines.append(f'''
        <div class="stage-node" data-stage="{stage}"
             style="{node_style}border-radius:12px;padding:16px;margin:4px 0;cursor:{cursor};transition:all 0.3s;"
             onclick="window.selectStage('{stage}')" id="node-{stage}">
            <div style="display:flex;align-items:center;gap:12px;">
                <span style="font-size:28px;">{status_icon}</span>
                <div style="flex:1;">
                    <div style="display:flex;align-items:center;">
                        <span style="font-size:20px;font-weight:bold;color:{info['color']};">{stage}</span>
                        <span style="font-size:15px;margin-left:8px;">{info['icon']} {info['name']}</span>
                        {human_tag}
                    </div>
                    <div style="font-size:12px;color:#666;margin-top:4px;">{info['description']}</div>
                    <div style="font-size:11px;color:#999;margin-top:4px;">🔧 {tools_preview}</div>
                </div>
            </div>
        </div>
        ''')

        # 连接箭头
        if i < len(ALL_STAGES) - 1:
            next_has_confirm = STAGE_INFO[ALL_STAGES[i+1]].get("human_confirm") is not None
            arrow_color = info['color'] if is_completed else "#e0e0e0"
            if is_completed and next_has_confirm:
                arrow_style = f"color:{arrow_color};font-size:16px;text-align:center;padding:2px 0;"
                arrow_icon = "↓ 📋 需确认"
            elif is_completed:
                arrow_style = f"color:{arrow_color};font-size:20px;text-align:center;"
                arrow_icon = "▼"
            elif is_pending:
                arrow_style = "color:#FFC107;font-size:20px;text-align:center;"
                arrow_icon = "▼ ⏸️"
            else:
                arrow_style = "color:#e0e0e0;font-size:20px;text-align:center;"
                arrow_icon = "▼"
            lines.append(f'<div style="{arrow_style}">{arrow_icon}</div>')

    lines.append('</div>')
    lines.append('</div>')
    return "\n".join(lines)


def build_stage_detail_panel(stage: str, completed, pending):
    """构建阶段详情面板"""
    if not stage:
        return "<p style='color:#999;text-align:center;padding:40px;'>👈 点击左侧工作流节点查看详情</p>"

    info = STAGE_INFO.get(stage, {})
    is_completed = stage in completed
    is_pending = stage in pending

    lines = ['<div style="font-family:Arial,sans-serif;padding:20px;">']
    lines.append(f'''
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
        <span style="font-size:32px;">{info.get("icon", "📋")}</span>
        <div>
            <span style="font-size:24px;font-weight:bold;color:{info.get("color", "#666")};">{stage}</span>
            <span style="font-size:16px;color:#666;margin-left:8px;">{info.get("name", "")}</span>
        </div>
    </div>
    ''')

    if is_completed:
        lines.append('<span style="background:#4CAF50;color:white;padding:4px 12px;border-radius:12px;font-size:12px;">✅ 已完成</span>')
    elif is_pending:
        lines.append('<span style="background:#FFC107;color:white;padding:4px 12px;border-radius:12px;font-size:12px;">⏸️ 待确认</span>')

    if info.get("human_confirm"):
        lines.append(f'''
        <div style="background:#FFF8E1;border:2px solid #FFC107;border-radius:10px;padding:15px;margin:15px 0;">
            <div style="font-size:14px;font-weight:bold;color:#FF9800;margin-bottom:8px;">👤 人工确认点</div>
            <div style="color:#333;">{info["human_confirm"]}</div>
            <div style="color:#666;font-size:12px;margin-top:5px;">需要人工介入确认后才能继续工作流</div>
        </div>
        ''')

    lines.append(f'<p style="color:#555;line-height:1.6;">{info.get("description", "")}</p>')

    lines.append('''
    <table style="width:100%;border-collapse:collapse;margin:15px 0;font-size:13px;">
        <tr style="background:#f5f5f5;"><th style="padding:10px;border:1px solid #ddd;text-align:left;">类型</th><th style="padding:10px;border:1px solid #ddd;">内容</th></tr>
        <tr><td style="padding:10px;border:1px solid #ddd;"><b>📥 输入</b></td><td style="padding:10px;border:1px solid #ddd;">''' + "<br>".join([f"• {x}" for x in info.get("inputs", [])]) + '''</td></tr>
        <tr><td style="padding:10px;border:1px solid #ddd;"><b>📤 输出</b></td><td style="padding:10px;border:1px solid #ddd;">''' + "<br>".join([f"• {x}" for x in info.get("outputs", [])]) + '''</td></tr>
        <tr><td style="padding:10px;border:1px solid #ddd;"><b>🔧 工具</b></td><td style="padding:10px;border:1px solid #ddd;">''' + "<br>".join([f"`{x}`" for x in info.get("tools", [])]) + '''</td></tr>
    </table>
    ''')

    prompt = load_system_prompt(stage)
    lines.append(f'''
    <div style="margin-top:20px;">
        <div style="font-size:14px;font-weight:bold;margin-bottom:10px;color:#333;">📝 系统提示词</div>
        <div style="background:#1e1e1e;color:#d4d4d4;padding:15px;border-radius:8px;font-size:12px;white-space:pre-wrap;max-height:300px;overflow:auto;">{prompt}</div>
    </div>
    ''')

    lines.append('</div>')
    return "\n".join(lines)


def build_confirm_modal_content(stage: str, pending_data: dict):
    """构建确认弹窗内容"""
    if not stage or not pending_data:
        return "<p style='color:#999;'>无确认信息</p>"

    info = STAGE_INFO.get(stage, {})
    lines = ['<div style="font-family:Arial,sans-serif;padding:20px;">']

    # 标题
    lines.append(f'''
    <div style="text-align:center;margin-bottom:20px;">
        <span style="font-size:48px;">{info.get("icon", "⚠️")}</span>
        <h2 style="color:{info.get("color", "#FF9800")};margin:10px 0;">{stage} - {info.get("name", "")}</h2>
        <span style="background:#FF9800;color:white;padding:4px 12px;border-radius:12px;font-size:12px;">👤 需要人工确认</span>
    </div>
    ''')

    # 确认类型
    confirm_type = pending_data.get("confirm_type", "stage")
    lines.append(f'<p style="font-size:16px;margin-bottom:15px;"><b>确认类型:</b> {confirm_type}</p>')

    # 关键信息
    if pending_data.get("message"):
        lines.append(f'<div style="background:#f5f5f5;padding:15px;border-radius:8px;margin-bottom:15px;"><b>📋 信息:</b><br>{pending_data["message"]}</div>')

    # 证据/依据
    if pending_data.get("evidence"):
        evidence = pending_data["evidence"]
        if isinstance(evidence, list):
            evidence_str = "<br>".join([f"• {e}" for e in evidence])
        else:
            evidence_str = str(evidence)
        lines.append(f'<div style="background:#fff3e0;padding:15px;border-radius:8px;margin-bottom:15px;"><b>📊 证据/依据:</b><br>{evidence_str}</div>')

    # 建议
    if pending_data.get("suggestion"):
        lines.append(f'<div style="background:#e8f5e9;padding:15px;border-radius:8px;margin-bottom:15px;"><b>💡 建议:</b><br>{pending_data["suggestion"]}</div>')

    lines.append('</div>')
    return "\n".join(lines)


def build_workflow_controls(completed, pending, current):
    """构建控制面板"""
    lines = ['<div style="font-family:Arial,sans-serif;padding:15px;">']

    if pending:
        lines.append(f'''
        <div style="background:#FFF8E1;border:2px solid #FFC107;border-radius:10px;padding:15px;margin-bottom:15px;">
            <div style="font-size:14px;font-weight:bold;color:#FF9800;margin-bottom:10px;">⏸️ 等待人工确认</div>
            <div style="font-size:13px;color:#333;">
                {", ".join([f"<b>{p}</b>" for p in pending])} 需要确认后继续
            </div>
        </div>
        ''')
    elif current:
        lines.append(f'''
        <div style="background:#FFF3E0;border:2px solid #FF9800;border-radius:10px;padding:15px;margin-bottom:15px;">
            <div style="font-size:14px;font-weight:bold;color:#FF9800;">⏳ 执行中</div>
            <div style="font-size:13px;color:#333;margin-top:5px;">当前阶段: <b>{current}</b></div>
        </div>
        ''')

    lines.append(f'<div style="font-size:13px;color:#666;">已完成: {len(completed)}/{len(ALL_STAGES)}</div>')
    lines.append('</div>')
    return "\n".join(lines)


# ============================================================
# 状态管理
# ============================================================

_thread_store = {"id": None}


def get_thread_id():
    if not _thread_store["id"]:
        import uuid
        _thread_store["id"] = f"web-{uuid.uuid4().hex[:8]}"
    return _thread_store["id"]


def reset_thread():
    _thread_store["id"] = None


# ============================================================
# Gradio UI
# ============================================================

def start_workflow(application_str):
    """启动工作流"""
    from datetime import datetime

    try:
        app = json.loads(application_str)
    except json.JSONDecodeError:
        ts = datetime.now().strftime("%H:%M:%S")
        return (
            build_workflow_diagram([], "", []),
            "<p style='color:red;'>❌ JSON格式错误</p>",
            f"[{ts}] ❌ JSON格式错误\n",
            0,
            {"status": "error", "pending": [], "confirmed": [], "thread_id": None, "current_stage": ""},
            gr.update(visible=False),
            gr.update(visible=False, value=""),
            gr.update(visible=False, value=""),
            gr.update(visible=False, value=""),
        )

    reset_thread()
    thread_id = get_thread_id()
    result = run_workflow(app, thread_id)

    pending = list_pending_confirmations(thread_id)
    pending_stages = [p.get("stage", "") for p in pending]
    current = result.get("current_stage", "")
    completed = list(result.get("confirmed_stages", {}).keys())

    workflow_html = build_workflow_diagram(completed, current, pending_stages)
    control_html = build_workflow_controls(completed, pending_stages, current)
    detail_html = build_stage_detail_panel(current, completed, pending_stages)

    ts = datetime.now().strftime("%H:%M:%S")
    if pending_stages:
        log = f"[{ts}] ⏳ 工作流暂停，等待: {', '.join(pending_stages)}\n"
        # 弹窗可见
        first_pending = pending[0] if pending else {}
        modal_content = build_confirm_modal_content(pending_stages[0], first_pending)
        modal_update = gr.update(visible=True)
    else:
        log = f"[{ts}] ✅ 工作流执行完成\n"
        modal_update = gr.update(visible=False)

    state = {
        "status": "waiting" if pending_stages else "completed",
        "pending": pending_stages,
        "pending_data": {p.get("stage", ""): p for p in pending},
        "confirmed": completed,
        "thread_id": thread_id,
        "current_stage": current,
    }

    return (
        workflow_html, control_html, detail_html, log, len(completed), state,
        modal_update,
        gr.update(visible=True, value=pending_stages[0] if pending_stages else ""),
        gr.update(visible=True, value=modal_content if pending_stages else ""),
        gr.update(visible=True, value="approve"),
    )


def confirm_stage_fn(stage, decision, state):
    """确认阶段"""
    from datetime import datetime

    thread_id = state.get("thread_id")
    if not thread_id:
        err = "错误: 无有效工作流"
        return (
            build_workflow_diagram([], "", []), err, err, 0, state,
            gr.update(visible=False), gr.update(visible=False, value=""),
            gr.update(visible=False, value=""), gr.update(visible=False, value=""),
        )

    result = confirm_and_continue(thread_id, stage, decision)

    pending = list_pending_confirmations(thread_id)
    pending_stages = [p.get("stage", "") for p in pending]
    current = result.get("current_stage", "")
    completed = list(result.get("confirmed_stages", {}).keys())

    workflow_html = build_workflow_diagram(completed, current, pending_stages)
    control_html = build_workflow_controls(completed, pending_stages, current)
    detail_html = build_stage_detail_panel(stage, completed, pending_stages)

    ts = datetime.now().strftime("%H:%M:%S")
    if pending_stages:
        log = f"[{ts}] ✅ {stage} 已确认({decision})，等待: {', '.join(pending_stages)}\n"
        first_pending = pending[0] if pending else {}
        modal_content = build_confirm_modal_content(pending_stages[0], first_pending)
        modal_update = gr.update(visible=True)
    else:
        log = f"[{ts}] ✅ {stage} 已确认({decision})，工作流完成\n"
        modal_update = gr.update(visible=False)
        modal_content = ""

    new_state = {
        "status": "waiting" if pending_stages else "completed",
        "pending": pending_stages,
        "pending_data": {p.get("stage", ""): p for p in pending},
        "confirmed": completed,
        "thread_id": thread_id,
        "current_stage": current,
    }

    return (
        workflow_html, control_html, detail_html, log, len(completed), new_state,
        modal_update,
        gr.update(visible=True, value=pending_stages[0] if pending_stages else ""),
        gr.update(visible=True, value=modal_content),
        gr.update(visible=True, value="approve"),
    )


def sync_form_to_json(work_type, region, medium, equipment, person_name, person_badge, person_quals, planned_start, planned_end):
    """将表单数据同步到 JSON"""
    equipment_list = [e.strip() for e in equipment.split(",") if e.strip()]
    qualifications_list = [q.strip() for q in person_quals.split(",") if q.strip()]
    application = {
        "work_type": work_type,
        "region": region,
        "equipment": equipment_list,
        "medium": medium,
        "personnel": [{
            "name": person_name,
            "badge_id": person_badge,
            "qualifications": qualifications_list
        }],
        "planned_start": planned_start,
        "planned_end": planned_end
    }
    return json.dumps(application, ensure_ascii=False, indent=2)


def sync_json_to_form(application_str):
    """将 JSON 数据同步到表单"""
    try:
        app = json.loads(application_str)
        personnel = app.get("personnel", [{}])[0] if app.get("personnel") else {}
        return (
            app.get("work_type", ""),
            app.get("region", ""),
            app.get("medium", ""),
            ", ".join(app.get("equipment", [])),
            personnel.get("name", ""),
            personnel.get("badge_id", ""),
            ", ".join(personnel.get("qualifications", [])),
            app.get("planned_start", ""),
            app.get("planned_end", "")
        )
    except:
        return None, None, None, None, None, None, None, None, None


def main():
    # 加载 .env 配置
    env_config = load_env_config()

    # 加载默认作业申请数据
    p1_data_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "input", "P1_data.json")
    if os.path.exists(p1_data_path):
        with open(p1_data_path, "r", encoding="utf-8") as f:
            p1_data = json.load(f)
        default_application = p1_data.get("input", {}).get("application", {})
    else:
        default_application = {
            "work_type": "受限空间作业",
            "region": "炼油厂区01",
            "equipment": ["反应器R-101", "管道P-205"],
            "medium": "原油",
            "personnel": [{"name": "张三", "badge_id": "P-101", "qualifications": ["受限空间作业证", "易燃易爆作业证"]}],
            "planned_start": "2026-08-04T09:00:00Z",
            "planned_end": "2026-08-04T17:00:00Z"
        }
    default_json = json.dumps(default_application, ensure_ascii=False, indent=2)

    # 从 application 中提取表单默认值
    personnel = default_application.get("personnel", [{}])[0] if default_application.get("personnel") else {}
    default_work_type = default_application.get("work_type", "受限空间作业")
    default_region = default_application.get("region", "炼油厂区01")
    default_medium = default_application.get("medium", "原油")
    default_equipment = ", ".join(default_application.get("equipment", []))
    default_person_name = personnel.get("name", "张三")
    default_person_badge = personnel.get("badge_id", "P-101")
    default_person_quals = ", ".join(personnel.get("qualifications", []))
    default_planned_start = default_application.get("planned_start", "2026-08-04T09:00:00Z")
    default_planned_end = default_application.get("planned_end", "2026-08-04T17:00:00Z")

    # JavaScript 全局函数
    page_head = '''
    <script>
    window.selectStage = function(stage) {
        var input = document.getElementById("selected-stage-input");
        if (input) {
            input.value = stage;
            input.dispatchEvent(new Event("change"));
        }
    };
    </script>
    '''

    with gr.Blocks(title="边缘智能作业监测系统", theme=gr.themes.Default(spacing_size="md", text_size="md"), head=page_head) as demo:

        gr.Markdown("## 🔄 边缘智能作业监测系统 - P1-P10 工作流编排")

        with gr.Tabs():
            # ========================================
            # Tab 1: 配置页面
            # ========================================
            with gr.Tab("⚙️ 配置"):
                gr.Markdown("### ⚙️ 配置面板")

                # 左右布局
                with gr.Row():
                    # 左侧菜单 (HTML 实现)
                    with gr.Column(scale=1, min_width=0):
                        menu_input = gr.Textbox(visible=False, value="模型配置", elem_id="menu-input")

                        menu_html = '''
                        <div style="padding:5px 0;text-align:left;">
                            <div style="font-size:12px;color:#666;padding:5px 0;margin-bottom:5px;font-weight:bold;">📋 配置菜单</div>
                            <button id="btn-model" onclick="selectMenu('模型配置')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#FF9800;color:white;font-size:13px;text-align:left;font-weight:bold;">⚙️ 模型配置</button>
                            <div style="border-bottom:1px solid #ddd;margin:10px 0;"></div>
                            <div style="font-size:12px;color:#666;padding:5px 0;margin-bottom:5px;font-weight:bold;">📝 Agent 提示词</div>
                            <button id="btn-P1" onclick="selectMenu('P1')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P1 作业预约</button>
                            <button id="btn-P2" onclick="selectMenu('P2')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P2 作业任务获取</button>
                            <button id="btn-P3" onclick="selectMenu('P3')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P3 作业上下文</button>
                            <button id="btn-P4" onclick="selectMenu('P4')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P4 摄像与数据</button>
                            <button id="btn-P5" onclick="selectMenu('P5')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P5 作业前核验</button>
                            <button id="btn-P6" onclick="selectMenu('P6')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P6 动态监测</button>
                            <button id="btn-P7" onclick="selectMenu('P7')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P7 风险研判</button>
                            <button id="btn-P8" onclick="selectMenu('P8')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P8 人机协同</button>
                            <button id="btn-P9" onclick="selectMenu('P9')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P9 闭环跟踪</button>
                            <button id="btn-P10" onclick="selectMenu('P10')" style="display:block;width:100%;padding:10px 12px;margin:3px 0;border:none;border-radius:6px;background:#f5f5f5;color:#333;font-size:13px;text-align:left;">P10 归档复盘</button>
                        </div>
                        <script>
                        function selectMenu(value) {
                            // 更新按钮高亮
                            document.querySelectorAll('button[id^="btn-"]').forEach(function(b) {
                                b.style.background = '#f5f5f5';
                                b.style.color = '#333';
                            });
                            var btn = document.getElementById('btn-' + value.replace('模型配置', 'model'));
                            if (btn) {
                                btn.style.background = 'linear-gradient(135deg, #FF9800, #FF5722)';
                                btn.style.color = 'white';
                            }
                            // 触发 Gradio
                            var inputs = document.querySelectorAll('input[type="hidden"]');
                            for (var i = 0; i < inputs.length; i++) {
                                inputs[i].value = value;
                                inputs[i].dispatchEvent(new Event('change'));
                            }
                        }
                        </script>
                        '''
                        menu_display = gr.HTML(menu_html)

                    # 右侧内容区
                    with gr.Column(scale=3):
                        # ========== 模型配置面板 ==========
                        with gr.Group(visible=True) as model_config_panel:
                            gr.Markdown("#### 🤖 模型配置")
                            with gr.Row():
                                api_key_input = gr.Textbox(
                                    label="API Key",
                                    value=env_config.get("api_key", ""),
                                    type="password",
                                    scale=2,
                                )
                                base_url_input = gr.Textbox(
                                    label="Base URL",
                                    value=env_config.get("base_url", ""),
                                    scale=2,
                                )
                                model_input = gr.Textbox(
                                    label="Model",
                                    value=env_config.get("model", ""),
                                    scale=1,
                                )
                            with gr.Row():
                                config_save_btn = gr.Button("💾 保存", variant="primary")
                                config_status = gr.Textbox(label="状态", interactive=False)

                        # ========== Agent 提示词配置面板 ==========
                        with gr.Group(visible=False) as agent_prompt_container:
                            gr.Markdown("#### 📝 系统提示词配置")
                            agent_prompt_dropdown = gr.Dropdown(
                                choices=ALL_STAGES,
                                value="P1",
                                label="选择 Agent",
                            )
                            agent_prompt_textarea = gr.Textbox(
                                label="系统提示词",
                                value=load_system_prompt("P1"),
                                lines=20,
                                interactive=True,
                            )
                            with gr.Row():
                                prompt_save_btn = gr.Button("💾 保存提示词", variant="primary")
                                prompt_refresh_btn = gr.Button("🔄 刷新")
                            agent_save_status = gr.Textbox(label="状态", interactive=False)

                # 配置页面事件
                def save_config(api_key, base_url, model):
                    return save_env_config(api_key, base_url, model)

                def on_agent_selected(stage):
                    return load_system_prompt(stage)

                def on_menu_selected(selection):
                    """根据菜单选择显示不同面板"""
                    if selection == "模型配置":
                        return (
                            gr.update(visible=True),    # model_config_panel
                            gr.update(visible=False),   # agent_prompt_container
                            gr.update(),                # agent_prompt_dropdown (保持不变)
                            gr.update(),                # agent_prompt_textarea (保持不变)
                        )
                    else:
                        # 选择 Agent 时，同步更新下拉框并加载提示词
                        prompt = load_system_prompt(selection)
                        return (
                            gr.update(visible=False),   # model_config_panel
                            gr.update(visible=True),    # agent_prompt_container
                            gr.update(value=selection), # agent_prompt_dropdown
                            prompt,                     # agent_prompt_textarea
                        )

                config_save_btn.click(
                    fn=save_config,
                    inputs=[api_key_input, base_url_input, model_input],
                    outputs=[config_status],
                )

                # 菜单选择事件
                menu_input.change(
                    fn=on_menu_selected,
                    inputs=[menu_input],
                    outputs=[model_config_panel, agent_prompt_container, agent_prompt_dropdown, agent_prompt_textarea],
                )

                agent_prompt_dropdown.change(
                    fn=on_agent_selected,
                    inputs=[agent_prompt_dropdown],
                    outputs=[agent_prompt_textarea],
                )

                prompt_save_btn.click(
                    fn=lambda stage, content: save_system_prompt(stage, content),
                    inputs=[agent_prompt_dropdown, agent_prompt_textarea],
                    outputs=[agent_save_status],
                )

                prompt_refresh_btn.click(
                    fn=on_agent_selected,
                    inputs=[agent_prompt_dropdown],
                    outputs=[agent_prompt_textarea],
                )

            # ========================================
            # Tab 2: 执行页面
            # ========================================
            with gr.Tab("🚀 执行"):
                # 隐藏字段用于节点点击
                selected_stage_input = gr.Textbox(elem_id="selected-stage-input", visible=False)

                with gr.Row():
                    # ========== 左侧：工作流图 ==========
                    with gr.Column(scale=2):
                        gr.Markdown("#### 📊 工作流图（点击节点查看详情）")
                        workflow_display = gr.HTML(build_workflow_diagram([], "", []))

                        gr.Markdown("#### 📝 作业申请")
                        with gr.Tab("📋 表单填写"):
                            gr.Markdown("**作业基本信息**")
                            work_type = gr.Dropdown(
                                choices=["受限空间作业", "高空作业", "动火作业", "吊装作业", "临时用电作业"],
                                value=default_work_type,
                                label="作业类型"
                            )
                            region = gr.Textbox(value=default_region, label="作业区域")
                            medium = gr.Textbox(value=default_medium, label="介质")
                            equipment = gr.Textbox(value=default_equipment, label="设备(逗号分隔)")
                            gr.Markdown("**作业人员**")
                            person_name = gr.Textbox(value=default_person_name, label="姓名")
                            person_badge = gr.Textbox(value=default_person_badge, label="工牌号")
                            person_quals = gr.Textbox(value=default_person_quals, label="资质(逗号分隔)")
                            gr.Markdown("**计划时间**")
                            with gr.Row():
                                planned_start = gr.Textbox(value=default_planned_start, label="开始时间")
                                planned_end = gr.Textbox(value=default_planned_end, label="结束时间")

                        with gr.Tab("📄 JSON填写"):
                            application_text = gr.Textbox(
                                value=default_json,
                                placeholder='{"work_type":"...","region":"...","equipment":[...]}',
                                lines=12,
                                label="作业申请 JSON"
                            )

                        with gr.Row():
                            start_btn = gr.Button("🚀 启动", variant="primary")
                            refresh_btn = gr.Button("🔄 刷新")
                            reset_btn = gr.Button("🗑️ 重置")

                        gr.Markdown("#### 📜 日志")
                        log_display = gr.Textbox(lines=6, interactive=False)

                    # ========== 右侧：详情和弹窗 ==========
                    with gr.Column(scale=1):
                        gr.Markdown("#### 🔍 节点详情")
                        detail_display = gr.HTML("<p style='color:#999;text-align:center;'>👈 点击左侧节点查看详情</p>")

                        gr.Markdown("#### ⚙️ 控制面板")
                        control_display = gr.HTML("<p style='color:#999;'>启动工作流后显示状态</p>")

                # ========== HITL 弹窗 ==========
                with gr.Group(visible=False) as hitl_modal:
                    gr.Markdown("### 👤 人工确认")
                    modal_stage = gr.Textbox(label="当前阶段", interactive=False)
                    modal_content = gr.HTML("<p style='color:#999;'>加载中...</p>")
                    decision_radio = gr.Radio(
                        choices=["approve", "reject"],
                        label="决定",
                        value="approve",
                    )
                    with gr.Row():
                        modal_confirm_btn = gr.Button("✅ 确认", variant="primary")
                        modal_cancel_btn = gr.Button("❌ 取消")

                # 状态
                workflow_state = gr.State({
                    "status": "idle",
                    "pending": [],
                    "pending_data": {},
                    "confirmed": [],
                    "thread_id": None,
                    "current_stage": "",
                })

                # 事件
                def reset_all():
                    reset_thread()
                    return (
                        build_workflow_diagram([], "", []),
                        "<p style='color:#999;'>已重置</p>",
                        "<p style='color:#999;'>启动工作流后显示状态</p>",
                        "",
                        0,
                        {"status": "idle", "pending": [], "pending_data": {}, "confirmed": [], "thread_id": None, "current_stage": ""},
                        gr.update(visible=False),
                        gr.update(visible=False, value=""),
                        gr.update(visible=False, value=""),
                        gr.update(visible=False, value=""),
                    )

                def on_stage_click(stage):
                    return build_stage_detail_panel(stage, [], [])

                # 节点点击事件
                selected_stage_input.change(
                    fn=on_stage_click,
                    inputs=[selected_stage_input],
                    outputs=[detail_display],
                )

                start_btn.click(
                    fn=start_workflow,
                    inputs=[application_text],
                    outputs=[
                        workflow_display, control_display, detail_display, log_display,
                        gr.Number(label="已完成"), workflow_state,
                        hitl_modal, modal_stage, modal_content, decision_radio
                    ],
                )

                refresh_btn.click(
                    fn=lambda s: (
                        build_workflow_diagram(s.get("confirmed", []), s.get("current_stage", ""), s.get("pending", [])),
                        build_workflow_controls(s.get("confirmed", []), s.get("pending", []), s.get("current_stage", "")),
                        build_stage_detail_panel(s.get("current_stage", ""), s.get("confirmed", []), s.get("pending", [])),
                        s,
                    ),
                    inputs=[workflow_state],
                    outputs=[workflow_display, control_display, detail_display, workflow_state],
                )

                # 弹窗确认按钮
                modal_confirm_btn.click(
                    fn=confirm_stage_fn,
                    inputs=[modal_stage, decision_radio, workflow_state],
                    outputs=[
                        workflow_display, control_display, detail_display, log_display,
                        gr.Number(label="已完成"), workflow_state,
                        hitl_modal, modal_stage, modal_content, decision_radio
                    ],
                )

                # 弹窗取消按钮 - 简单关闭弹窗
                modal_cancel_btn.click(
                    fn=lambda: (
                        gr.update(visible=False),
                        gr.update(visible=False, value=""),
                        gr.update(visible=False, value=""),
                    ),
                    outputs=[hitl_modal, modal_stage, modal_content],
                )

                reset_btn.click(
                    fn=reset_all,
                    outputs=[
                        workflow_display, detail_display, control_display, log_display,
                        gr.Number(label="已完成"), workflow_state,
                        hitl_modal, modal_stage, modal_content, decision_radio
                    ],
                )

                # 表单字段变化 → 更新 JSON
                form_fields = [work_type, region, medium, equipment, person_name, person_badge, person_quals, planned_start, planned_end]
                for field in form_fields:
                    field.change(
                        fn=sync_form_to_json,
                        inputs=[work_type, region, medium, equipment, person_name, person_badge, person_quals, planned_start, planned_end],
                        outputs=[application_text],
                    )

                # JSON 变化 → 更新表单字段
                application_text.change(
                    fn=sync_json_to_form,
                    inputs=[application_text],
                    outputs=[work_type, region, medium, equipment, person_name, person_badge, person_quals, planned_start, planned_end],
                )

    demo.launch(share=False)


if __name__ == "__main__":
    main()
