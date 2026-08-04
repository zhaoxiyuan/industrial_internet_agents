"""
Web 前端 - P1-P10 工作流可视化与执行
使用 Gradio 展示工作流执行进度和每个节点的产物
"""
import json
import time
import gradio as gr
from agents.p1_permit_agent import permit_submit
from agents.p2_task_agent import task_instance_create
from agents.p3_context_agent import context_build
from agents.p4_binding_agent import binding_match, binding_confirm
from agents.p5_verify_agent import verify_execute, verify_recommendation
from agents.p6_monitor_agent import monitor_start, monitor_events
from agents.p7_risk_agent import risk_list
from agents.p8_disposition_agent import disposition_create
from agents.p9_closure_agent import closure_status, closure_verify, closure_report
from agents.p10_archive_agent import archive_task, archive_cases, archive_performance, archive_suggestions


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
     "tools": ["task_instance_create"], "human_confirm": "是否纳入智能监测", "color": "#2196F3"},
    "P3": {"name": "作业上下文理解", "icon": "🧠",
     "description": "聚合作业类型、区域、设备、介质、风险、措施、人员、时间和关联作业",
     "inputs": ["作业票", "JSA", "场景数据"], "outputs": ["标准作业上下文包"],
     "tools": ["context_build", "context_validate"],
     "human_confirm": "上下文缺失确认", "color": "#9C27B0"},
    "P4": {"name": "摄像与数据关联", "icon": "📹",
     "description": "匹配固定/移动摄像、传感器、定位和报警数据",
     "inputs": ["作业区域", "资源台账"], "outputs": ["数据源绑定关系", "监测清单"],
     "tools": ["binding_match", "binding_confirm", "binding_request_manual"],
     "human_confirm": "资源绑定确认", "color": "#FF9800"},
    "P5": {"name": "作业前条件核验", "icon": "✅",
     "description": "核对隔离、警戒、消防、气体检测、人员资质和PPE",
     "inputs": ["措施清单", "视频", "检测数据"], "outputs": ["核验结果", "缺失项", "开工建议"],
     "tools": ["verify_checklist", "verify_execute", "verify_recommendation"],
     "human_confirm": "允许开工或整改", "color": "#F44336"},
    "P6": {"name": "作业过程动态监测", "icon": "📡",
     "description": "持续获取视频、传感器、定位和作业状态，识别违章及条件变化",
     "inputs": ["视频流", "传感器", "定位", "规则"], "outputs": ["候选风险事件", "证据片段"],
     "tools": ["monitor_start", "monitor_stop", "monitor_events"],
     "human_confirm": None, "color": "#E91E63"},
    "P7": {"name": "风险研判与分级", "icon": "⚠️",
     "description": "融合上下文、模型结果、规则和历史事件，去重并判级",
     "inputs": ["候选事件", "上下文", "规则", "知识"], "outputs": ["风险事件", "等级", "依据和建议"],
     "tools": ["risk_analyze", "risk_grade", "risk_cases"],
     "human_confirm": "高风险判断确认", "color": "#FF5722"},
    "P8": {"name": "人机协同处置", "icon": "🔧",
     "description": "按角色与权限推送责任人，形成整改、暂停、复核或升级建议",
     "inputs": ["风险事件", "处置规则", "组织关系"], "outputs": ["处置任务", "通知", "确认记录"],
     "tools": ["disposition_create", "disposition_confirm"],
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


# ============================================================
# 工具函数
# ============================================================

def build_pipeline_html(completed, current, failed):
    lines = ['<div style="font-family:Arial,sans-serif;padding:10px;">']
    for i, stage in enumerate(ALL_STAGES):
        info = STAGE_INFO[stage]
        is_completed = stage in completed
        is_current = stage == current
        is_failed = stage in failed
        is_pending = stage not in completed and stage != current and stage not in failed

        if is_completed:
            icon, bg, border = "✅", "#E8F5E9", "#4CAF50"
        elif is_current:
            icon, bg, border = "⏳", "#FFF3E0", "#FF9800"
        elif is_failed:
            icon, bg, border = "❌", "#FFEBEE", "#F44336"
        else:
            icon, bg, border = "⬜", "#F5F5F5", "#E0E0E0"

        if i > 0:
            prev = ALL_STAGES[i - 1]
            lc = "#4CAF50" if (prev in completed or prev == current) else "#E0E0E0"
            lines.append(f'<div style="text-align:center;color:{lc};font-size:20px;">▼</div>')

        lines.append(f'''<div style="background:{bg};border:2px solid {border};border-radius:8px;
            padding:12px;margin:4px 0;{'opacity:0.6;' if is_pending else ''}">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:24px;">{icon}</span>
                <span style="font-size:18px;font-weight:bold;">{stage}</span>
                <span style="font-size:16px;">{info["icon"]} {info["name"]}</span>
            </div>
            <div style="margin-top:8px;font-size:13px;color:#555;">{info["description"]}</div>
            <div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;">
                <span style="background:#e3f2fd;padding:2px 8px;border-radius:4px;font-size:12px;">📥 {", ".join(info["inputs"][:2])}</span>
                <span style="background:#e8f5e9;padding:2px 8px;border-radius:4px;font-size:12px;">📤 {", ".join(info["outputs"][:2])}</span>
            </div>
            {f'<div style="margin-top:6px;color:#F44336;font-size:12px;">⚠️ 需人工确认: {info["human_confirm"]}</div>' if info["human_confirm"] and not is_completed else ''}
        </div>''')
    lines.append('</div>')
    return "\n".join(lines)


def build_stage_detail_html(stage, result_data=None):
    info = STAGE_INFO[stage]
    lines = [f'<div style="font-family:Arial,sans-serif;padding:15px;">']
    lines.append(f'<h3>{info["icon"]} {stage} - {info["name"]}</h3>')
    lines.append(f'<p style="color:#666;">{info["description"]}</p>')
    lines.append('''<table style="width:100%;border-collapse:collapse;margin:10px 0;">
        <tr style="background:#f5f5f5;"><th style="padding:8px;border:1px solid #ddd;text-align:left;">类型</th><th style="padding:8px;border:1px solid #ddd;text-align:left;">内容</th></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>主要输入</b></td><td style="padding:8px;border:1px solid #ddd;">''' + "<br>".join([f"• {x}" for x in info["inputs"]]) + '''</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>主要产物</b></td><td style="padding:8px;border:1px solid #ddd;">''' + "<br>".join([f"• {x}" for x in info["outputs"]]) + '''</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>调用工具</b></td><td style="padding:8px;border:1px solid #ddd;">''' + "<br>".join([f"`{x}`" for x in info["tools"]]) + '''</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>人工控制点</b></td><td style="padding:8px;border:1px solid #ddd;">''' + (info["human_confirm"] or "无") + '''</td></tr>
    </table>''')
    if result_data:
        lines.append('<h4>📤 执行产物</h4>')
        lines.append('<pre style="background:#1e1e1e;color:#d4d4d4;padding:15px;border-radius:8px;overflow:auto;max-height:400px;font-size:13px;">')
        try:
            lines.append(json.dumps(result_data, ensure_ascii=False, indent=2, default=str))
        except Exception:
            lines.append(str(result_data))
        lines.append('</pre>')
    lines.append('</div>')
    return "\n".join(lines)


def execute_stage(stage, task_id, application):
    """执行单个阶段"""
    try:
        if stage == "P1":
            app_str = json.dumps(application, ensure_ascii=False)
            result_str = permit_submit.invoke(app_str)
            result = json.loads(result_str)
            return result

        elif stage == "P2":
            result_str = task_instance_create.invoke(application.get("permit_draft_id", "PD-TEMP"))
            return json.loads(result_str)

        elif stage == "P3":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            result_str = context_build.invoke(tid)
            return json.loads(result_str)

        elif stage == "P4":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            match_str = binding_match.invoke(tid)
            match_result = json.loads(match_str)
            confirm_str = binding_confirm.invoke(tid)
            confirm_result = json.loads(confirm_str)
            return {"result": {"binding": match_result.get("result", {}), "confirm": confirm_result.get("result", {})}}

        elif stage == "P5":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            exec_str = verify_execute.invoke(tid)
            exec_result = json.loads(exec_str)
            rec_str = verify_recommendation.invoke(tid)
            rec_result = json.loads(rec_str)
            return {"result": {"verification": exec_result.get("result", {}), "recommendation": rec_result.get("result", {})}}

        elif stage == "P6":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            start_str = monitor_start.invoke(tid)
            start_result = json.loads(start_str)
            events_str = monitor_events.invoke(tid)
            events = []
            for line in events_str.strip().split("\n"):
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return {"result": {"session": start_result.get("result", {}), "events": events}}

        elif stage == "P7":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            list_str = risk_list.invoke(tid)
            return json.loads(list_str)

        elif stage == "P8":
            events = application.get("risk_events", [])
            if not events:
                events = [{"event_id": f"RE-20260804-001"}]
            disp_results = []
            for event in events[:3]:
                event_id = event.get("event_id", "")
                if event_id:
                    disp_str = disposition_create.invoke(event_id)
                    disp_result = json.loads(disp_str)
                    if "result" in disp_result:
                        disp_results.append(disp_result["result"])
            return {"result": {"dispositions": disp_results}}

        elif stage == "P9":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            status_str = closure_status.invoke(tid)
            status_result = json.loads(status_str)
            verify_str = closure_verify.invoke(tid)
            verify_result = json.loads(verify_str)
            report_str = closure_report.invoke(tid)
            report_result = json.loads(report_str)
            return {"result": {"status": status_result.get("result", {}), "verify": verify_result.get("result", {}), "report": report_result.get("result", {})}}

        elif stage == "P10":
            tid = task_id or application.get("task_id", "TASK-UNKNOWN")
            archive_str = archive_task.invoke(tid)
            archive_result = json.loads(archive_str)
            cases_str = archive_cases.invoke(tid)
            cases_result = json.loads(cases_str)
            perf_str = archive_performance.invoke(tid)
            perf_result = json.loads(perf_str)
            sugg_str = archive_suggestions.invoke(tid)
            sugg_result = json.loads(sugg_str)
            return {"result": {"archive": archive_result.get("result", {}), "cases": cases_result.get("result", []),
                       "performance": perf_result.get("result", {}), "suggestions": sugg_result.get("result", [])}}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Gradio UI
# ============================================================

def run_workflow(application_str):
    """同步运行完整工作流，返回所有输出组件的tuple"""
    from datetime import datetime

    try:
        app = json.loads(application_str)
    except json.JSONDecodeError:
        ts = datetime.now().strftime("%H:%M:%S")
        return (
            build_pipeline_html([], "", []),  # pipeline
            "<p style='color:red;'>❌ JSON格式错误</p>",  # detail
            f"[{ts}] ❌ JSON格式错误，请检查输入\n",  # log
            0,  # progress
            {  # state
                "completed": [],
                "stage_results": {},
                "task_id": "",
                "log_lines": [],
            },
        )

    completed = []
    failed = []
    stage_results = {}
    task_id = ""
    log_lines = []
    ctx = dict(app)

    for stage in ALL_STAGES:
        ts = datetime.now().strftime("%H:%M:%S")
        log_lines.append(f"[{ts}] ⏳ 执行 {stage}...\n")

        result = execute_stage(stage, task_id, ctx)
        stage_results[stage] = result

        res = result.get("result", result)
        if isinstance(res, dict):
            if "task_id" in res:
                task_id = res["task_id"]
            if "context" in res:
                ctx["context"] = res["context"]
            if "binding" in res:
                ctx["monitoring_resources"] = res["binding"]
            if "verification" in res:
                ctx["verification"] = res["verification"]
            if "session" in res:
                ctx["monitoring_session"] = res["session"]
            if "events" in res:
                ctx["candidate_events"] = res["events"]
            if "dispositions" in res:
                ctx["dispositions"] = res["dispositions"]

        ts2 = datetime.now().strftime("%H:%M:%S")
        if "error" not in result:
            completed.append(stage)
            log_lines.append(f"[{ts2}] ✅ {stage} 完成\n")
        else:
            failed.append(stage)
            log_lines.append(f"[{ts2}] ❌ {stage} 失败: {result.get('error', {})}\n")

    ts3 = datetime.now().strftime("%H:%M:%S")
    log_lines.append(f"[{ts3}] 🎉 工作流执行完成！共 {len(completed)}/{len(ALL_STAGES)} 阶段成功\n")

    final_stage = completed[-1] if completed else "P1"
    final_result = stage_results.get(final_stage)
    pipeline_html = build_pipeline_html(completed, "", failed)
    detail_html = build_stage_detail_html(final_stage, final_result)

    state = {
        "completed": completed,
        "failed": failed,
        "stage_results": stage_results,
        "task_id": task_id,
        "log_lines": log_lines,
    }

    return (
        pipeline_html,
        detail_html,
        "".join(log_lines),
        len(completed),
        state,
    )


def show_stage_detail(stage, state):
    """选中阶段后显示其详情和产物"""
    if not stage:
        return (
            "<p style='color:#999;padding:20px;'>请选择一个阶段</p>",
            state,
        )
    result_data = state.get("stage_results", {}).get(stage)
    return (
        build_stage_detail_html(stage, result_data),
        state,
    )


def clear_all():
    return (
        build_pipeline_html([], "", []),
        "<p style='color:#999;padding:20px;'>已清空</p>",
        "",
        0,
        {"completed": [], "failed": [], "stage_results": {}, "task_id": "", "log_lines": []},
    )


def main():
    with gr.Blocks(
        title="P1-P10 工作流可视化",
        theme=gr.themes.Default(spacing_size="md", text_size="md"),
    ) as demo:

        gr.Markdown("## 🔄 边缘智能作业监测系统 - P1-P10 工作流编排")
        gr.Markdown("### 填写作业申请，点击启动工作流，观察每个阶段的执行与产物")

        with gr.Row():
            # ---------- 左侧 ----------
            with gr.Column(scale=3):
                gr.Markdown("#### 📋 工作流进度")
                pipeline_display = gr.HTML(build_pipeline_html([], "", []))
                gr.Markdown("#### 📝 作业申请 (JSON)")
                application_text = gr.Textbox(
                    label="作业申请",
                    placeholder='{"work_type":"受限空间作业","region":"炼油厂区01","equipment":["反应器R-101"],"medium":"原油","personnel":[{"name":"张三","badge_id":"P-101","qualifications":["受限空间作业证"]}],"planned_start":"2026-08-04T09:00:00Z","planned_end":"2026-08-04T17:00:00Z"}',
                    lines=8,
                )
                with gr.Row():
                    start_btn = gr.Button("🚀 启动工作流", variant="primary")
                    clear_btn  = gr.Button("🗑️ 清空")
                progress_bar = gr.Number(label="完成阶段数", interactive=False)

            # ---------- 右侧 ----------
            with gr.Column(scale=2):
                gr.Markdown("#### 🔍 阶段详情与产物")
                detail_display = gr.HTML("<p style='color:#999;padding:20px;'>请启动工作流查看详情</p>")
                stage_selector = gr.Dropdown(
                    choices=ALL_STAGES,
                    label="快速跳转查看阶段产物",
                    value=None,
                )

        gr.Markdown("#### 📜 执行日志")
        log_display = gr.Textbox(lines=12, interactive=False)

        workflow_state = gr.State({
            "completed": [],
            "failed": [],
            "stage_results": {},
            "task_id": "",
            "log_lines": [],
        })

        # ---------- 事件 ----------
        start_btn.click(
            fn=run_workflow,
            inputs=[application_text],
            outputs=[pipeline_display, detail_display, log_display, progress_bar, workflow_state],
        )

        clear_btn.click(
            fn=clear_all,
            outputs=[pipeline_display, detail_display, log_display, progress_bar, workflow_state],
        )

        stage_selector.select(
            fn=show_stage_detail,
            inputs=[stage_selector, workflow_state],
            outputs=[detail_display, workflow_state],
        )

    demo.launch()


if __name__ == "__main__":
    main()
