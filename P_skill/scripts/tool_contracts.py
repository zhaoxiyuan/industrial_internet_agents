"""Model-visible engineering contracts; implementation remains in packaged business modules."""
from copy import deepcopy

COMMON = ' 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。'
DESCRIPTIONS = {'main': {'bind_work_permit': '用途：登记用户明确提供的作业及监测归属。前置：当前无活动作业，或以完全相同参数重放已绑定作业。job_id与permit_id必须是同一个票号，按原字符串保存；允许1–128位字母、数字、下划线、连字符，不强制17位。start_time须带时区；end_time可省略，提供时必须晚于start_time。person_ids和sensor_ids都要传数组，至少一类非空，允许另一类[]；至少匹配采集源的一项风险规则，否则NO_RELEVANT_JOB_RULES。basis是用户确认的非空归属说明，不强制文件号或附件；area_ids/operators/reviewers可选。operators/reviewers支持ID数组或registered，后者表示P8/P9当前绑定飞书连接.env的全部登记用户，目录由平台可信注入；无目录拒绝，不支持任意人。已有作业的角色权限用configure_participants更新。不从姓名猜ID、旧票或默认值补齐。事务写入配置、monitoring_scope、allowed_job_ids、active_job和审计，不修改mock。返回status=bound及原样job_id/permit_id/source_id后，再query_pipeline核对真实作业。不同活动作业报WORK_PERMIT_ALREADY_BOUND；同job不同内容报JOB_ALREADY_REGISTERED_DIFFERENT_PERMIT；号码不同报WORK_PERMIT_ID_MISMATCH。不能用于换绑或覆盖旧档案。', 'query_pipeline': '用途：Main调度前读取当前业务上下文。参数{}。空票或已退役票返回status=work_permit_required、required_fields、空业务queues、可用agents、acquisition及P1-P5未实现标记：用通俗语言询问实际缺失项，不安排未绑定作业业务下游。绑定后返回job、agents、monitoring_scope、各queues及closure。只对description publish_state=ready且pending的记录调P7；event_notification pending即调P8/P9 input={mode:intake}，不要求事件closed，兼容旧report pending；已提交整改材料时调同一Agent input={mode:review,report_id:真实ID}。子Agent ID使用agents回执，不猜测。p10_access=requires_job_entry_configuration时不要调用旧测试桥；当前实现始终提示需登记原P10入口。查询会检查过期领取，但不消费业务数据、不调度Agent。结果按本Main任务链固定作业，不能靠聊天文字换绑。', 'retry_failed': '用途：显式恢复当前作业failed记录；kind取description/report/event_notification，record_id是该业务表的ID，reason用于审计。attempts达到max_attempts（默认3）或不是failed时拒绝RETRY_NOT_ALLOWED。description/report/event_notification转pending供相应阶段重新领取。P6失败窗口使用retry_time_window按开始时间恢复。此工具不处理飞书delivery=unknown，不得用来重发未知发送。回执只表示可重试，不表示阶段成功。', 'reopen_monitoring': '用途：根据用户明确指令恢复尚未最终关闭的作业监测。reason非空；job.status=closed拒绝JOB_ALREADY_CLOSED。只将monitoring_closed改为false并写审计，保留历史与既有监测归属范围（包括已有end_time），不会重启播放器或自动恢复心跳；需另核对范围和心跳。'}, 'P6': {}, 'P7': {'claim_description': '用途：按当前授权作业时间顺序领取P6 ready业务描述；lease_seconds可选30–1800，默认600。前序P6时间窗口未完成时返blocked/reason=earlier_p6_window_unfinished/time_range，由Main按时间恢复或等待，不能跳过。其他blocked返record_id/p7_through；no_work的unreported列出已closed未最终报告事件，可read_event核对后补报。claimed返claim、record.payload.observations、active_events和rules。真正时间断档会持久unknown区间，重置normal_since并产生更新通知；不把断档当恢复。一次处理一个描述，领取后异常fail_description，长分析renew_description_lease。', 'complete_assessment': '用途：提交本描述全部异常key的研判与跨窗事件变化。不传claim，自动关联本轮当前领取；assessments=[{key,risk_level,basis,advice}]，无重复且精确覆盖observations含abnormal的key，risk_level为0–5整数，basis说明证据和规则，advice为具体建议字符串数组。全正常/未知传[]仍须提交。工具检查前序p7_through等于窗口起点；按会话/来源/主体/具体风险key保持单活动事件；同风险closed后再次异常新建事件。abnormal延续open、清空恢复计时并保留历史最大等级；unknown置uncertain并重置计时；连续normal置closing，达到该observation.recovery_seconds且本窗口coverage.complete才closed。模型不能指定关闭字段、改变对象/规则或以空异常闭合。事务写事件、描述completed、p7_through、event_notification最新版本，返回record_id/changed_events/closed_events/artifact_ref。同claim+相同assessments幂等，改内容IDEMPOTENCY_CONFLICT。通知保留在途/failed状态，不自动调P8/写卡片意图。closed_events逐个read_event后report_closed_event；开放变化返回Main调P8，不等待最终报告。', 'report_closed_event': '用途：生成已结束事件的不可变最终归档。event_id取closed_events或no_work.unreported，先read_event核对；summary概括起止、严重度、依据和建议。要求event.state=closed且closure_reason=continuous_normal_evidence，否则EVENT_NOT_CLOSED。稳定report_id与开放阶段处置使用同一标识，payload.is_final=true；同摘要幂等，不同摘要拒绝IDEMPOTENCY_CONFLICT。更新事件report_id与event_notification；已有处置则报告status=completed，否则pending供兼容领取。不是发送回执，不调P8、不发飞书；未结束事件用事件通知持续更新，不调用本工具生成最终报告。', 'read_event': '用途：按当前作业event_id读取持久事件、证据区间、历史记录引用、状态与报告关联。用于最终报告核对和解释变化，不能读其他作业；查询无副作用，不会确认风险恢复。', 'renew_description_lease': '用途：长分析期间续租当前P7 description有效领取，不传claim，自动关联本轮当前领取；lease_seconds30–1800；返回status/expiry。不推进p7_through、不改变事件，不允许他人或已失效领取。', 'fail_description': '用途：记录当前P7 description执行失败；claim可原样传；省略时仅回收本run领取，reason为真实原因。本行completed则返completed而不回退；只回收仍归本run或有效token的processing记录，写failed与审计，阻塞后序，等待Main显式retry_failed(kind=description)。失败不能跳过前序或自动无限重试。'}, 'P8': {'claim_risk_report': '用途：Main安排intake时领取P7最新待处理通知；名称保留兼容，并非只允许closed报告。优先event_notification pending（open/uncertain/closing/closed皆可），无通知再取旧report pending。lease_seconds可选30–1800，默认600；返claim.kind=event_notification或report，均原样传initialize_disposition；no_work如实结束。不会发送飞书、不创建新Agent。P7持续更新同事件会合并到最新版本，processing/failed不被自动改成pending。', 'initialize_disposition': '用途：把领取的最新风险同步到单事件处置记录。claim.kind只允许event_notification/report且需当前有效领取；原样使用id/token。事务读取事件当前最新version，稳定report_id与该事件最终报告相同；没有最终报告时建立is_final=false临时视图，不冒充最终归档。创建或更新disposition.event_snapshot/event_version/risk_state，写card_intent pending，保留人工接取、材料版本、模型意见、人工审核、投递回执和人工risk_level_override。同已完成领取返回原记录，重复版本不重复建卡；成功消费通知并返回真实report_id/status/version/delivery。这里status只表示处置存储状态，非发送成功；再用平台feishu_sync_risk_card(report_id)核对真实或模拟回执。不会自行改变人工状态或调度Agent。', 'read_disposition': '用途：读取当前作业report_id对应处置、最新风险快照、材料/人工状态与平台delivery。report_id必须来自initialize_disposition/真实队列，不猜测。只读，delivery pending/unknown不能称送达，simulated只测试。', 'read_review_snapshot': '用途：Main安排review并提供report_id后获取当前整改材料快照。要求disposition.status=materials_in_audit，否则MATERIALS_NOT_READY；返回snapshot_id、material_version、materials、disposition_version和report。快照绑定当前agent_id/run_id，同一次任务依次read_material所有materials，再save_review_opinion；材料更新须重新取快照，不跨任务复用snapshot_id。', 'read_material': '用途：读取本任务review_snapshot包含的material_id，校验snapshot.actor/run_id、材料属于快照、路径在共享根目录与SHA256一致；越界/改动拒绝。当前仅text/plain且UTF-8可解码、大小≤32000字节返回content/readable=true，其他返content=null/readable=false和limitation。工具登记本次材料读取；不能把文件名/上传成功当整改证据。当前save_review_opinion遇到任何不可读材料只允许unknown；外部视觉读取器的结果尚不能替代此读取标记，需受控接入后再扩展。', 'save_review_opinion': '用途：提交本任务当前材料快照的模型参考意见。snapshot_id来自read_review_snapshot；decision=sufficient/insufficient/unknown，reason写逐项依据，limitations传限制列表。要求快照同Agent/任务、处置仍materials_in_audit且material_version一致、所有材料已read_material；未读报READ_MATERIALS_FIRST，版本改变报MATERIAL_VERSION_CONFLICT，任何readable=false而非unknown报UNREADABLE_MATERIAL_REQUIRES_UNKNOWN。相同意见+快照幂等，保存后status=waiting_human_review并写card_intent pending，再平台同步卡片。sufficient不是批准，不写human_review、不关闭事件/作业；人工审批经平台已验证回调。', 'verify_job_closable': '用途：只读检查当前作业闭环屏障，参数{}。要求monitoring_closed且cutoff=p7_through、所有window/description/report/event_notification completed、所有事件closed并有is_final=true报告、所有处置closed并delivered（simulated仅test_mode=true可接受）。返回closable/blockers/job_status。closable=true也只是可供人工确认，不执行关闭、不绕过授权审核；播放结束或模型sufficient不能满足全作业关闭。', 'fail_risk_report': '用途：登记intake领取失败，claim可省略按本run回收；提供时kind只允许report/event_notification，reason为真实错误。completed不回退；有效领取写failed+审计待Main显式retry_failed对应kind，不自动无限重试，不重置已保存处置，不处理unknown飞书发送。'}}
FIELDS = {
'job_id':'作业业务标识。Main绑定时原样使用唯一作业票号码，与permit_id相同。',
'permit_id':'作业票号码；Main绑定时与job_id完全相同，不要求第二个号码。',
'start_time':'用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。',
'end_time':'可选归属终点；不设置时省略，不传空字符串。提供必须晚于start_time，不代表自动结束监测或风险。',
'person_ids':'可信采集人员ID数组，与sensor_ids至少一类非空；无人员范围时传[]，不从姓名猜ID。',
'sensor_ids':'可信传感器/设备ID数组，与person_ids至少一类非空；无设备范围时传[]。',
'area_ids':'可选业务区域ID数组；不凭区域信息替代必需的人员或设备范围。',
'operators':'明确处置人ID数组，或registered表示P8/P9绑定飞书连接.env中的全部已登记用户；省略登记时为空、更新时保持，[]无人授权。',
'reviewers':'明确审核人ID数组，或registered表示当前连接.env中的全部已登记用户；回调按最新目录校验，不是任意人。[]无人授权，模型意见不赋予审批权限。',
'basis':'真实归属或风险研判依据。Main可使用用户确认的一句归属说明，无需附加文件号或附件。',
'window_seconds':'当前作业窗口N，由Main configure_schedule设置，1–300整数秒；不是P6任务入参或心跳间隔H。',
'claim':'原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。',
'kind':'对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。',
'id':'领取记录标识，必须来自领取回执；证据引用内id是正整数数据库主键。',
'token':'有效领取凭证，原样传回，不猜测、不外传。',
'lease_seconds':'领取或续租秒数，30–1800整数；可选领取参数默认600。',
'evidence_refs':'快照内部证据引用列表，每项table/id均来自工具事实，不编造引用或查询越界数据。',
'table':'证据原记录表名，必须使用快照返回的record_ref.table。',
'record_id':'使用对应工具返回的记录ID。下游业务重试按kind使用业务记录ID；P6按时间恢复。',
'summary':'本次事实/最终事件摘要，限6000字符；只概括证据支持的信息，不用空结果冒充完成。',
'descriptions':'所有观测通道各一项{observation,state,explanation,exceptions?}；state为默认判断，exceptions用from_sample/to_sample覆盖变化段；正常通道也须提交。',
'key':'事实回执中的风险key，原样使用，不能自行创造、合并主体或省略异常。',
'explanation':'本key的客观事实、对应证据及限制；不在P6中输出风险等级或结束结论。',
'limitations':'事实/材料缺测、不可读及证据范围等限制字符串列表，没有额外限制传[]。',
'assessments':'每个含异常key一项{key,risk_level,basis,advice}；全正常或未知传[]仍提交。',
'risk_level':'0–5整数，依据可信规则和证据；事件持续更新保留历史最大研判等级，人工处置等级另有受控覆盖。',
'advice':'处置建议字符串数组，不能声称建议已执行。',
'event_id':'持久P7事件ID，来自changed_events/closed_events/unreported或实际队列。',
'report_id':'稳定事件处置标识，来自初始化/队列真实回执，同事件开放和最终报告使用同一值。',
'snapshot_id':'本Agent本任务生成的审核快照标识，不跨任务复用。',
'material_id':'该审核快照materials中的材料ID。',
'decision':'sufficient/insufficient/unknown，表示模型参考意见；不可读材料只能unknown，不表示人工批准。',
'reason':'实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。',
'cutoff':'用户明确要求结束监测的带时区截止时间，需与实际已处理终点一致；不是计划end_time。',
}
OVERRIDES = {}
EXECUTION_STEPS = {
 'query_pipeline':'Main第一步。用此工具判断作业票是否存在；list_tasks中的平台任务ID不是作业票。返回work_permit_required就按用户资料bind_work_permit，不能get_task_result查询作业。',
 'configure_schedule':'Main绑定后调用：仅配置业务窗口长度与并发，不创建平台心跳。随后list_time_windows，再按用户范围调度P6。',
 'read_current_window':'P6第一步，通常传{}。领取一个最早可用窗口后必须save_current_description；无工作/容量满则退出。',
 'save_current_description':'P6最后一步：逐一覆盖required_observations，成功后退出本次P6，不再领取第二窗。',
 'claim_description':'P7循环入口：领取→complete_assessment→再次领取，直到no_work/blocked；一次Agent任务可处理多份，但同时只持有一份。',
 'complete_assessment':'正常或未知描述也必须提交assessments=[]。成功后处理closed_events归档，然后再次claim_description，不能一份就退出。',
}
CONCISE = {
 'bind_work_permit':'无活动票时登记；job_id=permit_id=用户票号，精确开始时间必填；人员/设备至少一类非空，basis为用户归属确认。用户指定registered须传operators/reviewers。不同票占用禁止覆盖，同票须参数一致。成功返回bound后查query_pipeline。',
 'query_pipeline':'Main首步传{}。当前票号只取回执job.job_id，与用户目标逐字比较，不同则停止调度并报告冲突。active只表示票未关，不证明心跳运行；心跳须另查get_heartbeat_channels。work_permit_required才绑定。平台task_id不是票号。',
 'configure_schedule':'绑定后设置业务window_seconds和max_parallel，不创建心跳；窗口已领取则时长锁定。随后list_time_windows查进度。',
 'list_time_windows':'查询时间窗与连续完成进度，不领取。默认{}，可limit/start_time/state筛选。返回windows、continuous_completed_through、source_through、pending_count/failed_count/available_slots；无数据不要空转。',
 'retry_time_window':'按list_time_windows原始start_time恢复失败窗，reason必填，最多3次。成功窗不重做，保留历史，不跳过前序。',
 'retry_failed':'恢复失败description/report/event_notification，传原record_id及reason；先核对未成功落库，不重发结果未知的飞书通知。',
 'reopen_monitoring':'用户明确要求时恢复尚未关闭作业的监测，reason必填；保留历史与归属。',
 'close_time_monitoring':'用户明确要求停止监测即调用，不等截止或窗口完成。reason必填；cutoff省略取工具当前时间，可非窗口边界。保留未完成记录并停用本票P6心跳；不等于人工关票。',
 'configure_participants':'更新当前票operators/reviewers：ID数组或registered（真实绑定连接登记名单）。省略保持，[]清空，reason必填；不代替人工审核。',
 'read_current_window':'P6首步传{}，自动领取最早一窗；同run重读仍同窗。返回monitoring_data、observations、required_observations。no_work/blocked退出；completed不重做；否则分析后save_current_description。',
 'read_current_evidence':'核实当前窗证据，不另领取。observation为观测序号；source可ppe/sensor/position/cv。CV按next_cursor翻页直到has_more=false。',
 'save_current_description':'提交一窗描述：summary、descriptions、limitations必填。每个required_observations序号恰好一项，含observation/state/explanation；变化用exceptions（1起闭区间，递增不重叠）。缺测unknown。成功后退出P6；结果不明先读当前窗核对。',
 'fail_current_window':'本窗确实无法提交时保存reason，交Main恢复；不需ID，不覆盖已完成结果。',
 'claim_description':'P7领取最早描述，逐份串行；no_work/blocked退出。当前领取由后台绑定，complete_assessment不传claim，提交后继续领取，不能一份就退出。',
 'complete_assessment':'只传assessments，后台关联本轮当前领取，不传claim/id/token。每个含异常段的key恰好一条assessment；正常/未知描述传assessments=[]。成功后closed_events逐项read_event→report_closed_event，再claim_description。工具负责事件聚合/恢复与通知入队。',
 'read_event':'读取当前授权事件的完整事实、证据和状态，归档前核实。',
 'report_closed_event':'对已结束事件生成一次最终报告，event_id与summary必填。未结束不归档，不代表人工处置关闭。',
 'renew_description_lease':'只传lease_seconds，自动续租本轮当前领取；不能复活过期领取，不延长Agent执行超时。',
 'fail_description':'真实失败且未提交时记录reason，claim可省略按当前run回收；已完成不回退，交Main有限恢复。',
}

def enrich(stage,name,description,schema):
    value=deepcopy(schema)
    def walk(node,path=()):
        if 'properties' in node:
            for key,child in node['properties'].items():
                child=deepcopy(child)
                node['properties'][key]=child
                note=OVERRIDES.get((stage,name,key)) or FIELDS.get(key)
                if note:child['description']=note
                if path and key=='id':child['description']='证据正整数主键，使用快照record_ref.id。' if path[-1]=='evidence_refs' else FIELDS['id']
                walk(child,path+(key,))
        if isinstance(node.get('items'),dict):walk(node['items'],path)
    walk(value)
    if stage in ('main','P6','P7') and name in CONCISE:
        return CONCISE[name],value
    return EXECUTION_STEPS.get(name,'')+(description if name in ('read_current_window','read_current_evidence','save_current_description','fail_current_window','configure_schedule','list_time_windows','retry_time_window','close_time_monitoring') else DESCRIPTIONS.get(stage,{}).get(name,description))+COMMON,value
