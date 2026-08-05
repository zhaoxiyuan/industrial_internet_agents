"""
Web 前端 - P1-P10 工作流可视化与执行
集成 HumanInTheLoop 人工确认机制

工作流样式展示，每个节点可点击查看详情和系统提示词
"""
import json
import os
import gradio as gr
from agents.workflow import (
    run_workflow,
    confirm_and_continue,
    get_workflow_state,
    list_pending_confirmations,
)


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


# ============================================================
# 辅助函数
# ============================================================

def load_system_prompt(stage: str) -> str:
    """加载系统提示词"""
    # 文件名映射
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
    return f"系统提示词文件未找到: {prompt_file}"


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

        # 节点内容
        lines.append(f'''
        <div class="stage-node" data-stage="{stage}"
             style="{node_style}border-radius:12px;padding:16px;margin:4px 0;cursor:{cursor};transition:all 0.3s;
                    onclick="selectStage('{stage}')" id="node-{stage}">
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

        # 连接箭头（除最后一个）
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

    # 标题
    lines.append(f'''
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
        <span style="font-size:32px;">{info.get("icon", "📋")}</span>
        <div>
            <span style="font-size:24px;font-weight:bold;color:{info.get("color", "#666")};">{stage}</span>
            <span style="font-size:16px;color:#666;margin-left:8px;">{info.get("name", "")}</span>
        </div>
    </div>
    ''')

    # 状态标签
    if is_completed:
        lines.append('<span style="background:#4CAF50;color:white;padding:4px 12px;border-radius:12px;font-size:12px;">✅ 已完成</span>')
    elif is_pending:
        lines.append('<span style="background:#FFC107;color:white;padding:4px 12px;border-radius:12px;font-size:12px;">⏸️ 待确认</span>')

    # 人工确认信息
    if info.get("human_confirm"):
        lines.append(f'''
        <div style="background:#FFF8E1;border:2px solid #FFC107;border-radius:10px;padding:15px;margin:15px 0;">
            <div style="font-size:14px;font-weight:bold;color:#FF9800;margin-bottom:8px;">👤 人工确认点</div>
            <div style="color:#333;">{info["human_confirm"]}</div>
            <div style="color:#666;font-size:12px;margin-top:5px;">需要人工介入确认后才能继续工作流</div>
        </div>
        ''')

    # 描述
    lines.append(f'<p style="color:#555;line-height:1.6;">{info.get("description", "")}</p>')

    # 输入输出
    lines.append('''
    <table style="width:100%;border-collapse:collapse;margin:15px 0;font-size:13px;">
        <tr style="background:#f5f5f5;"><th style="padding:10px;border:1px solid #ddd;text-align:left;">类型</th><th style="padding:10px;border:1px solid #ddd;">内容</th></tr>
        <tr><td style="padding:10px;border:1px solid #ddd;"><b>📥 输入</b></td><td style="padding:10px;border:1px solid #ddd;">''' + "<br>".join([f"• {x}" for x in info.get("inputs", [])]) + '''</td></tr>
        <tr><td style="padding:10px;border:1px solid #ddd;"><b>📤 输出</b></td><td style="padding:10px;border:1px solid #ddd;">''' + "<br>".join([f"• {x}" for x in info.get("outputs", [])]) + '''</td></tr>
        <tr><td style="padding:10px;border:1px solid #ddd;"><b>🔧 工具</b></td><td style="padding:10px;border:1px solid #ddd;">''' + "<br>".join([f"`{x}`" for x in info.get("tools", [])]) + '''</td></tr>
    </table>
    ''')

    # 系统提示词
    prompt = load_system_prompt(stage)
    lines.append(f'''
    <div style="margin-top:20px;">
        <div style="font-size:14px;font-weight:bold;margin-bottom:10px;color:#333;">📝 系统提示词</div>
        <div style="background:#1e1e1e;color:#d4d4d4;padding:15px;border-radius:8px;font-size:12px;white-space:pre-wrap;max-height:300px;overflow:auto;">{prompt}</div>
    </div>
    ''')

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
            gr.update(choices=[], value=None),
            [],
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
        dropdown_update = gr.update(choices=pending_stages, value=pending_stages[0])
    else:
        log = f"[{ts}] ✅ 工作流执行完成\n"
        dropdown_update = gr.update(choices=[], value=None)

    state = {
        "status": "waiting" if pending_stages else "completed",
        "pending": pending_stages,
        "confirmed": completed,
        "thread_id": thread_id,
        "current_stage": current,
    }

    return workflow_html, control_html, detail_html, log, len(completed), state, dropdown_update, [[p, "待确认"] for p in pending_stages] if pending_stages else []


def confirm_stage_fn(stage, decision, state):
    """确认阶段"""
    from datetime import datetime

    thread_id = state.get("thread_id")
    if not thread_id:
        return "错误: 无有效工作流", state, gr.update(choices=[], value=None), []

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
        dropdown_update = gr.update(choices=pending_stages, value=pending_stages[0])
    else:
        log = f"[{ts}] ✅ {stage} 已确认({decision})，工作流完成\n"
        dropdown_update = gr.update(choices=[], value=None)

    new_state = {
        "status": "waiting" if pending_stages else "completed",
        "pending": pending_stages,
        "confirmed": completed,
        "thread_id": thread_id,
        "current_stage": current,
    }

    return workflow_html, control_html, detail_html, log, len(completed), new_state, dropdown_update, [[p, "待确认"] for p in pending_stages] if pending_stages else []


def on_stage_selected(stage, state):
    """节点被选中时更新详情面板"""
    completed = state.get("confirmed", [])
    pending = state.get("pending", [])
    detail_html = build_stage_detail_panel(stage, completed, pending)
    return detail_html


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
    # 加载默认作业申请数据 (从 P1_data.json)
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

    with gr.Blocks(title="P1-P10 工作流编排") as demo:

        gr.Markdown("## 🔄 边缘智能作业监测系统 - P1-P10 工作流编排")
        gr.Markdown("### 👤 支持 HumanInTheLoop - 虚线框节点需要人工确认")

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

                # 同步表单和JSON的隐藏状态
                form_data_state = gr.JSON(value=default_application, visible=False)

                with gr.Row():
                    start_btn = gr.Button("🚀 启动", variant="primary")
                    refresh_btn = gr.Button("🔄 刷新")
                    reset_btn = gr.Button("🗑️ 重置")

                gr.Markdown("#### 📜 日志")
                log_display = gr.Textbox(lines=6, interactive=False)

            # ========== 右侧：详情和确认 ==========
            with gr.Column(scale=1):
                gr.Markdown("#### 🔍 节点详情")
                detail_display = gr.HTML("<p style='color:#999;text-align:center;'>👈 点击左侧节点查看详情</p>")

                gr.Markdown("#### ⚙️ 控制面板")
                control_display = gr.HTML("<p style='color:#999;'>启动工作流后显示状态</p>")

                gr.Markdown("#### 👤 人工确认")
                confirm_stage_dropdown = gr.Dropdown(
                    choices=ALL_STAGES,
                    label="选择阶段",
                    value=None,
                )
                decision_radio = gr.Radio(
                    choices=["approve", "reject"],
                    label="决定",
                    value="approve",
                )
                confirm_btn = gr.Button("✅ 确认", variant="primary")

                pending_display = gr.DataFrame(
                    headers=["阶段", "确认状态"],
                    label="待确认列表",
                    interactive=False,
                )

        # 状态
        workflow_state = gr.State({
            "status": "idle",
            "pending": [],
            "confirmed": [],
            "thread_id": None,
            "current_stage": "",
        })

        # 事件
        def update_pending_table(state):
            pending = state.get("pending", [])
            if pending:
                return [[p, "待确认"] for p in pending]
            return []

        def reset_all():
            reset_thread()
            return (
                build_workflow_diagram([], "", []),
                "<p style='color:#999;'>已重置</p>",
                "<p style='color:#999;'>启动工作流后显示状态</p>",
                "",
                0,
                {"status": "idle", "pending": [], "confirmed": [], "thread_id": None, "current_stage": ""},
                gr.update(choices=[], value=None),
                [],
            )

        def on_stage_click(stage):
            """节点被点击"""
            return build_stage_detail_panel(stage, [], [])

        # 节点点击事件 - 使用 HTML 组件的 click 方法
        workflow_display.click(
            fn=on_stage_click,
            js="(evt) => { const node = evt.target.closest('.stage-node'); return node ? node.dataset.stage : ''; }",
            inputs=[],
            outputs=[detail_display],
        )

        start_btn.click(
            fn=start_workflow,
            inputs=[application_text],
            outputs=[workflow_display, control_display, detail_display, log_display, gr.Number(label="已完成"), workflow_state, confirm_stage_dropdown, pending_display],
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

        confirm_btn.click(
            fn=confirm_stage_fn,
            inputs=[confirm_stage_dropdown, decision_radio, workflow_state],
            outputs=[workflow_display, control_display, detail_display, log_display, gr.Number(label="已完成"), workflow_state, confirm_stage_dropdown, pending_display],
        )

        reset_btn.click(fn=reset_all, outputs=[workflow_display, detail_display, control_display, log_display, gr.Number(label="已完成"), workflow_state, confirm_stage_dropdown, pending_display])

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

    demo.launch(theme=gr.themes.Default(spacing_size="md", text_size="md"))


if __name__ == "__main__":
    main()
