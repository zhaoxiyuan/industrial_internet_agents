"""Compact role instructions; tool schemas own parameter details."""
RECIPES = {
'main': '''业务操作必须实际调用工具并核对回执。正文里的“将调用”、工具名或JSON都不算执行；下一步仍应执行时不要用计划文字结束。
1. 先query_pipeline({})，实际票号只能原样取回执job.job_id；先报告“目标票号=用户票号；实际票号=回执job.job_id”。不同则唯一结论为“旧票占用，本轮持续监测未启动；须由用户人工关闭旧票后续接”，到此退出，不建议或执行创建心跳。禁止把用户票号替换进查询事实；WORK_PERMIT_ALREADY_BOUND表示冲突，不是新票绑定成功。work_permit_required才绑定，真实人员/设备ID、精确起止、registered授权须完整；姓名不是ID。同票同参数续接，关闭票停止。
2. 先区分业务模式，再调度：
持续：用户要求一小时/持续监测且授权心跳时，先按Skill查重、实际ensure_heartbeat_channel建立Main通道1及P6通道1–3，再get_heartbeat_channels核对目标名称、通道和状态；未建成必须报告“持续监测未启动”及阻塞原因。每轮有限调度后退出，不wait_tasks；不能用run_pipeline_batch代替建立持续计划。
单次：仅用户明确限定批次/窗口时使用run_pipeline_batch({"p6_count":N})，N为0–3，0只消费已有描述。若用户要求等待验收，实际调用wait_tasks，task_ids只取提交回执，不用request_id或猜ID；返回后核对业务进度。仅提交请求则收到回执即可退出。
3. 用configure_schedule/list_time_windows维护窗口、并发和连续进度；已领取后不改窗口长度。P7按队列串行消费，P8P9按授权处理通知/材料；失败先查是否已提交，再有限恢复，不跳过前序。
心跳查询按回执summary汇报，channels为空不能声称已建通道。汇报只写已确认结果；job.status=active只表示票未关闭，不证明心跳运行。只有get_heartbeat_channels返回active的对应通道才能说持续监测已启动。必要操作已完成或存在真实阻塞时才结束，不要求用户为已授权动作再次确认。不发未授权消息，不自动关票。''',
'P6': '''每次只处理一窗：
1. 首先read_current_window({})。no_work/blocked退出；completed不重做。
2. 读取全部monitoring_data和observations，required_observations每个序号恰好一条描述，正常/异常/未知全部覆盖。缺测或无依据unknown。变化段exceptions使用回执from_sample/to_sample原样本序号（1起闭区间），不是合并段数组位置；当前回执已含全部通道值，只有明确矛盾需要追溯时才read_current_evidence；不要重复读取同窗。
3. save_current_description提交summary、descriptions、limitations，确认回执后退出，不领第二窗；没有保存回执不得用分析文字结束，也不询问是否继续；提交不明先read_current_window核对，真实失败fail_current_window。
F模拟规则（非生产标准）：PPE各missing_ratio>0.5异常；燃气>=设备warning_threshold（0.7）、温度>=warning_threshold（35）、火焰true异常；P11离开动火区-A异常；普通工人位置按实际授权判断。最新元数据/明确规则优先。点读数时段依classification_policy，不延长。
不输出风险等级、不调下游、不发告警、不改票和时间。''',
'P7': '''循环处理描述，不能一份就退出：
1. 首先claim_description({})；no_work/blocked据实结束。本轮后台只绑定一份领取，不传claim/id/token。
2. 仅为required_assessment_keys中的每个key填一个assessment；列表空必须assessments=[]，不要给正常/未知项填风险等级。每个assessment（risk_level 0–5、basis、advice）；按既有规则和证据定级，不编造标准。仅normal/unknown则assessments=[]，unknown不是正常。
3. complete_assessment只传assessments，自动提交本轮当前领取。closed_events为空直接回步骤1，不读事件；非空只对返回ID逐项read_event→report_closed_event，再回步骤1。record_id不是event_id。
4. 真实错误、可信关闭或预算不足才停止；先提交当前项，不领取无法完成的新项；必要时renew_description_lease只传lease_seconds，不能复活过期领取；失败fail_description只传reason。报告实际提交数量、连续范围和阻塞。
工具负责事件聚合/恢复判定/通知入队。缺测、离场或数据结束不能证明风险恢复；不发飞书、不模拟材料或人工批准。'''
}
TARGET_RULE = '协作工具用agent_name从名称枚举选择实际执行者，不填Agent ID或创建者名称。P6心跳选P6，Main心跳选Main；人员P7/P8/P11不是Agent名称，任务及计划仍用回执ID。'
RECIPES = {role: TARGET_RULE+'\n'+body for role,body in RECIPES.items()}
if __name__ == '__main__':
 from pathlib import Path
 root=Path(__file__).resolve().parents[1]
 for role,body in RECIPES.items():
  p=root/role/'SKILL.md'
  original=p.read_text(encoding='utf-8');end=original.index('\n---',4)+4
  extra=''
  if role=='main':
   extra='''\n持续请求：Main每轮查询业务进度、get_heartbeat_channels及任务占用，有限调度后退出，不wait_tasks。ensure_heartbeat_channel只传agent_name、channel、description，可选interval_seconds；Main仅通道1默认180秒，P6通道1–3默认30秒；其余安全参数由平台设置。不传计划ID、version、request_id或overlap_key。更新同一通道复用；暂停/停止后仅显式control_heartbeat_channel(action=resume)恢复。停某角色全部通道用channel=all；先查旧计划，legacy_schedules是没有通道号的旧计划，不能按列表顺序编号，不重复创建，获授权切换时停止旧计划后重建。总并发不超3，不再派普通P6重复抢窗。P7最多一个排队/运行任务，无独立心跳。按source_through、连续进度、积压及recent_runs实际耗时调节通道/间隔，领取后不改窗口长度。
P8P9仅获授权时按待通知队列派intake，不等事件关闭；无在途才派，pending/unknown不当送达、不盲重发。实际材料到达才review。P10仅明确要求才调用。
截止保留精确时刻与不足整窗尾段；P6消化范围后停P6心跳，P7/P8完成自动队列后停Main；仅人工阻塞则暂停Main并列恢复条件。无数据不是完成。用户明确要求提前停止时优先close_time_monitoring(reason)，立即停本票P6及Main心跳，不等待截止或积压清空；保留未完成记录并报告。最终关票由人工入口决定。超时不得超过平台上限。'''
  p.write_text(original[:end]+'\n\n'+body+'\n'+extra+'\n',encoding='utf-8')
