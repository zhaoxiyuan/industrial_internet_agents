"""
Edge Monitor CLI - 边缘智能作业监测系统命令行工具

用法:
    python -m agents.cli permit submit <application.json>
    python -m agents.cli task list
    python -m agents.cli monitor start <task-id>
"""
import sys
import json
import click

# 添加项目根目录到 path
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from agents.p1_permit_agent import run_permit_agent
from agents.p2_task_agent import run_task_agent
from agents.p3_context_agent import run_context_agent
from agents.p4_binding_agent import run_binding_agent
from agents.p5_verify_agent import run_verify_agent
from agents.p6_monitor_agent import run_monitor_agent
from agents.p7_risk_agent import run_risk_agent
from agents.p8_disposition_agent import run_disposition_agent
from agents.p9_closure_agent import run_closure_agent
from agents.p10_archive_agent import run_archive_agent


@click.group()
@click.option("--output", type=click.Choice(["json", "text"]), default="json", help="输出格式")
@click.option("--quiet", is_flag=True, help="静默模式")
@click.pass_context
def cli(ctx, output, quiet):
    """边缘智能作业监测系统 CLI (P1-P10)"""
    ctx.ensure_object(dict)
    ctx.obj["output"] = output
    ctx.obj["quiet"] = quiet


def echo_result(result_str: str, output_format: str, quiet: bool):
    """输出结果"""
    if quiet:
        return
    if output_format == "json":
        try:
            data = json.loads(result_str)
            click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            click.echo(result_str)
    else:
        click.echo(result_str)


# ============================================================
# permit (P1)
# ============================================================
@cli.group("permit")
def permit():
    """P1: 作业预约、JSA分析与作业票"""
    pass


@permit.command("submit")
@click.argument("application_file", type=click.File("r"))
@click.pass_context
def permit_submit(ctx, application_file):
    """提交作业申请"""
    try:
        data = json.load(application_file)
        result = run_permit_agent(json.dumps(data))
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@permit.command("analyze-jsa")
@click.argument("task_id")
@click.pass_context
def permit_analyze_jsa(ctx, task_id):
    """分析JSA"""
    try:
        result = run_permit_agent(f"分析JSA，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@permit.command("generate-draft")
@click.argument("task_id")
@click.pass_context
def permit_generate_draft(ctx, task_id):
    """生成作业票草稿"""
    try:
        result = run_permit_agent(f"生成作业票草稿，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@permit.command("check")
@click.argument("permit_id")
@click.pass_context
def permit_check(ctx, permit_id):
    """查询作业票状态"""
    try:
        result = run_permit_agent(f"查询作业票状态，permit_id: {permit_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# task (P2)
# ============================================================
@cli.group("task")
def task():
    """P2: 作业任务获取与实例化"""
    pass


@task.command("list")
@click.option("--region", help="区域代码筛选")
@click.option("--status", type=click.Choice(["pending", "running", "closed"]), help="状态筛选")
@click.pass_context
def task_list(ctx, region, status):
    """列出作业任务"""
    try:
        msg = "列出作业任务"
        if region:
            msg += f"，区域: {region}"
        if status:
            msg += f"，状态: {status}"
        result = run_task_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@task.command("get")
@click.argument("task_id")
@click.pass_context
def task_get(ctx, task_id):
    """获取任务详情"""
    try:
        result = run_task_agent(f"获取任务详情，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@task.command("instance-create")
@click.argument("permit_id")
@click.pass_context
def task_instance_create(ctx, permit_id):
    """创建任务实例（幂等）"""
    try:
        result = run_task_agent(f"创建任务实例，permit_id: {permit_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@task.command("subscribe")
@click.argument("task_id")
@click.pass_context
def task_subscribe(ctx, task_id):
    """订阅任务变更"""
    try:
        result = run_task_agent(f"订阅任务变更，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# context (P3)
# ============================================================
@cli.group("context")
def context():
    """P3: 作业上下文理解与标准化"""
    pass


@context.command("build")
@click.argument("task_id")
@click.pass_context
def context_build(ctx, task_id):
    """构建标准上下文"""
    try:
        result = run_context_agent(f"构建标准上下文，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@context.command("validate")
@click.argument("task_id")
@click.pass_context
def context_validate(ctx, task_id):
    """验证上下文完整性"""
    try:
        result = run_context_agent(f"验证上下文完整性，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@context.command("history")
@click.argument("task_id")
@click.pass_context
def context_history(ctx, task_id):
    """获取上下文变更历史"""
    try:
        result = run_context_agent(f"获取上下文变更历史，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# binding (P4)
# ============================================================
@cli.group("binding")
def binding():
    """P4: 监测资源绑定与数据关联"""
    pass


@binding.command("match")
@click.argument("task_id")
@click.pass_context
def binding_match(ctx, task_id):
    """匹配监测资源"""
    try:
        result = run_binding_agent(f"匹配监测资源，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@binding.command("status")
@click.argument("task_id")
@click.pass_context
def binding_status(ctx, task_id):
    """查看绑定状态"""
    try:
        result = run_binding_agent(f"查看绑定状态，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@binding.command("confirm")
@click.argument("task_id")
@click.pass_context
def binding_confirm(ctx, task_id):
    """人工确认绑定"""
    try:
        result = run_binding_agent(f"人工确认绑定，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@binding.command("request-manual")
@click.argument("task_id")
@click.option("--resource-type", type=click.Choice(["camera", "sensor", "location"]),
              help="资源类型")
@click.pass_context
def binding_request_manual(ctx, task_id, resource_type):
    """请求人工补充资源"""
    try:
        msg = f"请求人工补充资源，task_id: {task_id}"
        if resource_type:
            msg += f"，resource_type: {resource_type}"
        result = run_binding_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# verify (P5)
# ============================================================
@cli.group("verify")
def verify():
    """P5: 作业前条件核验"""
    pass


@verify.command("checklist")
@click.argument("task_id")
@click.pass_context
def verify_checklist(ctx, task_id):
    """生成核验清单"""
    try:
        result = run_verify_agent(f"生成核验清单，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@verify.command("execute")
@click.argument("task_id")
@click.option("--checklist-id", help="核验清单ID")
@click.pass_context
def verify_execute(ctx, task_id, checklist_id):
    """执行核验"""
    try:
        msg = f"执行核验，task_id: {task_id}"
        if checklist_id:
            msg += f"，checklist_id: {checklist_id}"
        result = run_verify_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@verify.command("recommendation")
@click.argument("task_id")
@click.pass_context
def verify_recommendation(ctx, task_id):
    """获取开工建议"""
    try:
        result = run_verify_agent(f"获取开工建议，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# monitor (P6)
# ============================================================
@cli.group("monitor")
def monitor():
    """P6: 作业过程动态监测"""
    pass


@monitor.command("start")
@click.argument("task_id")
@click.pass_context
def monitor_start(ctx, task_id):
    """启动监测（幂等）"""
    try:
        result = run_monitor_agent(f"启动监测，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@monitor.command("stop")
@click.argument("task_id")
@click.pass_context
def monitor_stop(ctx, task_id):
    """停止监测"""
    try:
        result = run_monitor_agent(f"停止监测，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@monitor.command("status")
@click.argument("task_id")
@click.pass_context
def monitor_status(ctx, task_id):
    """查看监测状态"""
    try:
        result = run_monitor_agent(f"查看监测状态，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@monitor.command("events")
@click.argument("task_id")
@click.option("--since", help="ISO8601时间戳")
@click.pass_context
def monitor_events(ctx, task_id, since):
    """获取候选风险事件"""
    try:
        msg = f"获取候选风险事件，task_id: {task_id}"
        if since:
            msg += f"，since: {since}"
        result = run_monitor_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# risk (P7)
# ============================================================
@cli.group("risk")
def risk():
    """P7: 风险研判与分级"""
    pass


@risk.command("analyze")
@click.argument("candidate_event_id")
@click.pass_context
def risk_analyze(ctx, candidate_event_id):
    """综合研判候选事件"""
    try:
        result = run_risk_agent(f"综合研判候选事件，candidate_event_id: {candidate_event_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@risk.command("grade")
@click.argument("risk_event_id")
@click.pass_context
def risk_grade(ctx, risk_event_id):
    """计算风险等级"""
    try:
        result = run_risk_agent(f"计算风险等级，risk_event_id: {risk_event_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@risk.command("cases")
@click.argument("risk_event_id")
@click.option("--limit", type=int, default=5, help="返回数量限制")
@click.pass_context
def risk_cases(ctx, risk_event_id, limit):
    """查询相似案例"""
    try:
        msg = f"查询相似案例，risk_event_id: {risk_event_id}"
        if limit:
            msg += f"，limit: {limit}"
        result = run_risk_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@risk.command("list")
@click.argument("task_id")
@click.pass_context
def risk_list(ctx, task_id):
    """列出任务所有风险事件"""
    try:
        result = run_risk_agent(f"列出风险事件，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# disposition (P8)
# ============================================================
@cli.group("disposition")
def disposition():
    """P8: 人机协同处置"""
    pass


@disposition.command("create")
@click.argument("risk_event_id")
@click.pass_context
def disposition_create(ctx, risk_event_id):
    """创建处置任务"""
    try:
        result = run_disposition_agent(f"创建处置任务，risk_event_id: {risk_event_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@disposition.command("confirm")
@click.argument("disposition_task_id")
@click.option("--action", type=click.Choice(["approve", "reject", "escalate",
                                            "rectify", "pause", "resume"]),
              required=True, help="操作类型")
@click.pass_context
def disposition_confirm(ctx, disposition_task_id, action):
    """确认处置任务"""
    try:
        msg = f"确认处置任务，disposition_task_id: {disposition_task_id}，action: {action}"
        result = run_disposition_agent(msg)
        # 检查是否需要人工确认
        try:
            data = json.loads(result)
            if "error" in data and data["error"].get("code") == "DISPOSITION_HUMAN_CONFIRM_REQUIRED":
                click.echo(json.dumps(data, ensure_ascii=False, indent=2), err=True)
                sys.exit(3)  # 需要人工确认
        except json.JSONDecodeError:
            pass
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@disposition.command("status")
@click.argument("disposition_task_id")
@click.pass_context
def disposition_status(ctx, disposition_task_id):
    """查看处置状态"""
    try:
        result = run_disposition_agent(f"查看处置状态，disposition_task_id: {disposition_task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@disposition.command("list")
@click.argument("task_id")
@click.pass_context
def disposition_list(ctx, task_id):
    """列出任务所有处置任务"""
    try:
        result = run_disposition_agent(f"列出处置任务，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# closure (P9)
# ============================================================
@cli.group("closure")
def closure():
    """P9: 闭环跟踪与报告"""
    pass


@closure.command("status")
@click.argument("task_id")
@click.pass_context
def closure_status(ctx, task_id):
    """跟踪闭环状态"""
    try:
        result = run_closure_agent(f"跟踪闭环状态，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@closure.command("verify")
@click.argument("task_id")
@click.pass_context
def closure_verify(ctx, task_id):
    """执行闭环完整性检查"""
    try:
        result = run_closure_agent(f"执行闭环完整性检查，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@closure.command("report")
@click.argument("task_id")
@click.option("--format", "report_format", type=click.Choice(["json", "markdown", "pdf"]),
              default="markdown", help="报告格式")
@click.pass_context
def closure_report(ctx, task_id, report_format):
    """生成作业过程报告"""
    try:
        msg = f"生成作业过程报告，task_id: {task_id}，format: {report_format}"
        result = run_closure_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@closure.command("close")
@click.argument("task_id")
@click.pass_context
def closure_close(ctx, task_id):
    """关闭事件和作业"""
    try:
        result = run_closure_agent(f"关闭事件和作业，task_id: {task_id}")
        # 检查是否需要人工确认
        try:
            data = json.loads(result)
            if "result" in data and data["result"].get("requires_human_confirm"):
                click.echo(json.dumps(data, ensure_ascii=False, indent=2), err=True)
                sys.exit(3)  # 需要人工确认
        except json.JSONDecodeError:
            pass
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# archive (P10)
# ============================================================
@cli.group("archive")
def archive():
    """P10: 归档与复盘"""
    pass


@archive.command("task")
@click.argument("task_id")
@click.pass_context
def archive_task(ctx, task_id):
    """归档任务数据"""
    try:
        result = run_archive_agent(f"归档任务数据，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@archive.command("cases")
@click.argument("task_id")
@click.option("--type", "case_type",
              type=click.Choice(["misdetection", "missed", "rule_conflict", "all"]),
              default="all", help="案例类型")
@click.pass_context
def archive_cases(ctx, task_id, case_type):
    """挖掘案例"""
    try:
        msg = f"挖掘案例，task_id: {task_id}"
        if case_type:
            msg += f"，type: {case_type}"
        result = run_archive_agent(msg)
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@archive.command("performance")
@click.argument("task_id")
@click.pass_context
def archive_performance(ctx, task_id):
    """分析处置效果"""
    try:
        result = run_archive_agent(f"分析处置效果，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@archive.command("suggestions")
@click.argument("task_id")
@click.pass_context
def archive_suggestions(ctx, task_id):
    """生成规则优化建议"""
    try:
        result = run_archive_agent(f"生成规则优化建议，task_id: {task_id}")
        echo_result(result, ctx.obj["output"], ctx.obj["quiet"])
    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


# ============================================================
# workflow (LangGraph 顺序编排)
# ============================================================
from agents.workflow import run_workflow, run_workflow_stream


@cli.group("workflow")
def workflow():
    """P1-P10 LangGraph 顺序编排工作流"""
    pass


@workflow.command("run")
@click.argument("application_file", type=click.File("r"))
@click.option("--human-confirm", is_flag=True, help="已有人工确认，跳过人工确认节点")
@click.option("--confirm-action",
              type=click.Choice(["approve_permit", "approve_verification", "approve_closure"]),
              help="确认操作类型")
@click.pass_context
def workflow_run(ctx, application_file, human_confirm, confirm_action):
    """运行 P1-P10 完整工作流"""
    try:
        application = json.load(application_file)
        result = run_workflow(
            application=application,
            human_confirm=human_confirm,
            confirm_action=confirm_action
        )

        # 检查是否需要人工确认
        if result.get("requires_human_confirm"):
            stage = result.get("human_confirm_stage", "")
            action = result.get("human_confirm_action", "")
            click.echo(json.dumps({
                "schema_version": "1.0",
                "command": "workflow run",
                "result": {
                    "status": "human_confirm_required",
                    "stage": stage,
                    "action": action,
                    "error": result.get("error"),
                },
                "errors": []
            }, ensure_ascii=False, indent=2), err=True)
            sys.exit(3)

        # 输出结果
        output = {
            "schema_version": "1.0",
            "command": "workflow run",
            "result": {
                "task_id": result.get("task_id"),
                "permit_draft_id": result.get("permit_draft_id"),
                "current_stage": result.get("current_stage"),
                "archive_result": result.get("archive_result"),
                "mined_cases_count": len(result.get("mined_cases", [])),
                "suggestions_count": len(result.get("suggestions", [])),
            },
            "errors": []
        }
        if result.get("error"):
            output["errors"].append({"message": result["error"]})

        echo_result(json.dumps(output, ensure_ascii=False, indent=2),
                    ctx.obj["output"], ctx.obj["quiet"])

    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


@workflow.command("stream")
@click.argument("application_file", type=click.File("r"))
@click.pass_context
def workflow_stream(ctx, application_file):
    """流式运行工作流，逐步输出每个阶段的结果"""
    try:
        application = json.load(application_file)
        for stage_name, stage_state in run_workflow_stream(application):
            if ctx.obj["quiet"]:
                continue
            stage_output = {
                "stage": stage_name if stage_name != "__end__" else "COMPLETED",
                "state": {
                    "current_stage": stage_state.get("current_stage"),
                    "task_id": stage_state.get("task_id"),
                    "error": stage_state.get("error"),
                    "requires_human_confirm": stage_state.get("requires_human_confirm"),
                }
            }
            click.echo(json.dumps(stage_output, ensure_ascii=False, indent=2))

    except Exception as e:
        click.echo(json.dumps({"error": str(e)}, ensure_ascii=False), err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
