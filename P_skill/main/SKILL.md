---
name: industrial-main
description: 统筹工业作业监测、风险研判、告警处置与总结报告，依据真实处理进度安排各阶段工作。
metadata:
  context-scope: workflow
---

协作工具用agent_name从名称枚举选择实际执行者，不填Agent ID或创建者名称。P6心跳选P6，Main心跳选Main；人员P7/P8/P11不是Agent名称，任务及计划仍用回执ID。
业务操作必须实际调用工具并核对回执。正文里的“将调用”、工具名或JSON都不算执行；下一步仍应执行时不要用计划文字结束。
1. 先query_pipeline({})，实际票号只能原样取回执job.job_id；先报告“目标票号=用户票号；实际票号=回执job.job_id”。不同则唯一结论为“旧票占用，本轮持续监测未启动；须由用户人工关闭旧票后续接”，到此退出，不建议或执行创建心跳。禁止把用户票号替换进查询事实；WORK_PERMIT_ALREADY_BOUND表示冲突，不是新票绑定成功。work_permit_required才绑定，真实人员/设备ID、精确起止、registered授权须完整；姓名不是ID。同票同参数续接，关闭票停止。
2. 先区分业务模式，再调度：
持续：用户要求一小时/持续监测且授权心跳时，先按Skill查重、实际ensure_heartbeat_channel建立Main通道1及P6通道1–3，再get_heartbeat_channels核对目标名称、通道和状态；未建成必须报告“持续监测未启动”及阻塞原因。每轮有限调度后退出，不wait_tasks；不能用run_pipeline_batch代替建立持续计划。
单次：仅用户明确限定批次/窗口时使用run_pipeline_batch({"p6_count":N})，N为0–3，0只消费已有描述。若用户要求等待验收，实际调用wait_tasks，task_ids只取提交回执，不用request_id或猜ID；返回后核对业务进度。仅提交请求则收到回执即可退出。
3. 用configure_schedule/list_time_windows维护窗口、并发和连续进度；已领取后不改窗口长度。P7按队列串行消费，P8P9按授权处理通知/材料；失败先查是否已提交，再有限恢复，不跳过前序。
心跳查询按回执summary汇报，channels为空不能声称已建通道。汇报只写已确认结果；job.status=active只表示票未关闭，不证明心跳运行。只有get_heartbeat_channels返回active的对应通道才能说持续监测已启动。必要操作已完成或存在真实阻塞时才结束，不要求用户为已授权动作再次确认。不发未授权消息，不自动关票。

持续请求：Main每轮查询业务进度、get_heartbeat_channels及任务占用，有限调度后退出，不wait_tasks。ensure_heartbeat_channel只传agent_name、channel、description，可选interval_seconds；Main仅通道1默认180秒，P6通道1–3默认30秒；其余安全参数由平台设置。不传计划ID、version、request_id或overlap_key。更新同一通道复用；暂停/停止后仅显式control_heartbeat_channel(action=resume)恢复。停某角色全部通道用channel=all；先查旧计划，legacy_schedules是没有通道号的旧计划，不能按列表顺序编号，不重复创建，获授权切换时停止旧计划后重建。总并发不超3，不再派普通P6重复抢窗。P7最多一个排队/运行任务，无独立心跳。按source_through、连续进度、积压及recent_runs实际耗时调节通道/间隔，领取后不改窗口长度。
P8P9仅获授权时按待通知队列派intake，不等事件关闭；无在途才派，pending/unknown不当送达、不盲重发。实际材料到达才review。P10仅明确要求才调用。
截止保留精确时刻与不足整窗尾段；P6消化范围后停P6心跳，P7/P8完成自动队列后停Main；仅人工阻塞则暂停Main并列恢复条件。无数据不是完成。用户明确要求提前停止时优先close_time_monitoring(reason)，立即停本票P6及Main心跳，不等待截止或积压清空；保留未完成记录并报告。最终关票由人工入口决定。超时不得超过平台上限。
