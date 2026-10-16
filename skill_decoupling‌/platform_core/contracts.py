"""协作命令契约：仅声明参数，不执行任务或访问数据库。

固定 Agent 由管理员配置；这里的提交工具只创建任务，不创建 Agent。
每个协作对象的授权在目录生成和真正执行时都需要检查。
"""

OBJECT = {"type": "object", "properties": {}, "additionalProperties": True}
TASK_TOOLS = {
    "list_tasks": {"description": "查询统一协作任务表：返回本次协作链中获授权任务的关系、依赖、状态和阶段；具有成果权限时同时返回输出文件位置。",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    "submit_task": {"description": "向预定义且获授权的 Agent 提交子任务，返回任务 ID；不会创建 Agent。",
        "schema": {"type": "object", "properties": {
            "timeout_seconds":{"type":"integer","minimum":1,"description":"单次任务执行期限（秒），不超过目标Agent配置上限；超时失败并回收未提交领取。"},
            "agent_id": {"type": "string"}, "description": {"type": "string"},
            "input": OBJECT, "artifact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
            "artifact_inputs": {"type": "object", "additionalProperties": {"type": "string"}},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "request_id": {"type": "string"}}, "required": ["agent_id", "description"], "additionalProperties": False}},
    "get_task_status": {"description": "读取授权范围内的任务状态与阶段，不返回私有上下文。",
        "schema": {"type": "object", "properties": {"task_id": {"type": "string"}},
                   "required": ["task_id"], "additionalProperties": False}},
    "get_task_result": {"description": "读取已完成任务的结构化成果；失败时返回真实错误。",
        "schema": {"type": "object", "properties": {"task_id": {"type": "string"}},
                   "required": ["task_id"], "additionalProperties": False}},
    "wait_tasks": {"description": "等待本次协作中的已提交任务，保存上下文并释放执行资源；完成后自动恢复。",
        "schema": {"type": "object", "properties": {"task_ids": {"type": "array", "items": {"type": "string"}}},
                   "required": ["task_ids"], "additionalProperties": False}},
    "cancel_task": {"description": "请求取消授权任务；不能撤销已经发生的外部副作用。",
        "schema": {"type": "object", "properties": {"task_id": {"type": "string"}},
                   "required": ["task_id"], "additionalProperties": False}},
    "agent_status": {"description": "查看已授权 Agent 的可接受任务情况、并发上限及运行数量。",
        "schema": {"type": "object", "properties": {"agent_id": {"type": "string"}},
                   "required": ["agent_id"], "additionalProperties": False}}
}

# 权限与命令对应关系保持集中，避免前端声明与后端校验出现两套解释。
GRANT_PERMISSIONS = ["submit", "status", "result", "cancel", "overview", "schedule"]

TASK_TOOLS.update({
    'set_heartbeat_schedule': {'description':'创建或更新独立持久心跳计划，返回计划ID；主任务结束后平台继续触发。需要独立schedule授权。更新须提供当前version及新的request_id。',
        'schema':{'type':'object','properties':{
            'agent_id':{'type':'string'}, 'schedule_id':{'type':'string'}, 'version':{'type':'integer','minimum':1},
            'request_id':{'type':'string','minLength':1,'maxLength':128}, 'enabled':{'type':'boolean'},
            'interval_seconds':{'type':'integer','minimum':1,'maximum':86400}, 'description':{'type':'string','minLength':1,'maxLength':10000},
            'input':OBJECT, 'first_run':{'type':'string','enum':['immediate','after_interval']},
            'timeout_seconds':{'type':'integer','minimum':1}, 'overlap_scope':{'type':'string','enum':['self','chain']}, 'overlap_policy':{'type':'string','enum':['skip']}, 'overlap_key':{'type':'string','maxLength':128}},
            'required':['agent_id','request_id','enabled','interval_seconds','description','input'], 'additionalProperties':False}},
    'get_heartbeat_schedule': {'description':'查询自己创建且当前仍有schedule权限的计划及最近执行；读取成果另需result授权。省略计划ID列出自己的计划，不访问其他协作链。',
        'schema':{'type':'object','properties':{'schedule_id':{'type':'string'}},'additionalProperties':False}},
    'control_heartbeat_schedule': {'description':'暂停、恢复或停止自己创建的心跳计划，仅影响未来触发；不会取消在途任务。参数为计划ID，不能传任务ID。',
        'schema':{'type':'object','properties':{'schedule_id':{'type':'string'},'action':{'type':'string','enum':['pause','resume','stop']}},
                  'required':['schedule_id','action'],'additionalProperties':False}}
})


# Model-visible engineering notes; no execution or validation policy is changed here.
ENGINEERING_NOTES = {
    'list_tasks': '参数{}。返回当前协作链及同一作业下自己此前提交的获授权任务，含root/parent/dependencies/status/phase，最多最近100项；只有result授权才返回其他Agent成果位置，不返回私有会话。',
    'submit_task': 'agent_id取业务状态回执或已配置Agent ID；description为本次任务要求，input为目标input_schema认可的对象。P6通常input={}自动领取最早可处理窗口；需要指定窗口时仅传input={start_time:实际窗口开始时间}，窗口大小由Main的configure_schedule设置；不传原始记录ID；P7通常input={}；P8/P9 input={mode:intake}或{mode:review,report_id:真实ID}。需submit有方向授权，目标Agent须启用。depends_on只使用获授权前置任务ID；artifact_ids/artifact_inputs仅传登记且有访问权限的成果引用。request_id可选，省略由本run_id+参数生成稳定幂等键，同键不同内容拒绝；有意新任务使用新键。不传session_id/job_id改变受信业务上下文。返回已提交ID不是成功；需结果调用wait_tasks或终态后get_task_result。独立Agent按各自并发上限执行，提交后不会阻塞整个系统。',
    'get_task_status': 'task_id取真实提交回执，需status权限且属于当前协作链或同票此前自己提交的任务。只返回task_id/status/phase/executor_agent_id/error；queued/running/waiting不是完成。需要等待时优先wait_tasks释放当前执行资源，不用模型反复轮询。',
    'get_task_result': 'task_id取真实回执，需result权限且属于当前协作链或同票此前自己提交的任务。任务未终态报TASK_NOT_FINISHED；终态返回真实result/error/artifacts，failed/cancelled不能写成成功。不能读取其他协作链的历史任务或私有聊天上下文。',
    'wait_tasks': 'task_ids必须非空且为当前协作链已提交任务的真实ID；每个目标同时需status和result权限。未全部终态返waiting并由运行时保存工具调用位置、释放当前执行资源，恢复后从此处继续；不是阻塞全平台或禁止其他Agent运行。全部终态后返回任务结果，逐项核对失败。等待不创建计划、不调度下一阶段、不猜测成果。',
    'cancel_task': 'task_id为当前协作链可访问任务，需cancel权限。仅请求取消，运行中的外部调用/已落库事实/已发卡片不可撤销；核对实际任务状态，不把取消请求回执当副作用回滚。停止未来心跳需control_heartbeat_schedule，不使用本工具替代。',
    'agent_status': 'agent_id取已配置目标，需overview权限。返回可接受任务情况、并发上限、运行/队列概况；只读，不创建Agent，不授予权限，不表示所有Agent只能串行。',
    'set_heartbeat_schedule': '创建需agent_id/request_id/enabled/interval_seconds/description/input，目标需独立schedule授权，启用时校验目标input_schema。interval_seconds是H=1–86400整数秒，P6数据窗口N由Main业务工具configure_schedule设置，P6的input使用{}或start_time。first_run默认after_interval，可immediate；overlap_policy当前仅skip，overlap_scope默认self，只检查目标Agent自身所有在途执行（含手工任务），子Agent不阻塞下一轮；chain才检查同计划/overlap_key的整条协作链。每轮独立对话，作业范围绑定创建时当前票，关闭后停止，不跟随新票。skip跳过触发，不删除未消费业务数据；执行慢于H会产生消费积压，查询业务工具判断。更新必须schedule_id+当前version+新的request_id，目标不可换，所有者不得越权；同request_id不同内容拒绝SCHEDULE_IDEMPOTENCY_CONFLICT。不提供schedule_id是新建，不自动寻找或去重旧计划，先get_heartbeat_schedule检查复用。返回持久计划ID，主任务结束后继续运行；只有用户授权持续运行才enabled=true。',
    'get_heartbeat_schedule': 'schedule_id可省略，省略列出自己创建且仍有schedule权限的计划；提供时取真实计划ID，不传任务ID。返回状态、版本、配置与最近运行，只有result权限才含成果；不会读业务游标，business_backlog未测需查询业务工具。不能查看其他所有者计划，也不自动启用/更新计划。',
    'control_heartbeat_schedule': 'schedule_id是真实计划ID，action为pause/resume/stop。必须是自己的计划且当前仍有schedule权限；pause暂停未来触发，resume重新校验授权与配置后恢复，stop停止未来触发；当前代码允许对已停止计划显式resume并重新校验授权。都不取消在途任务、不关闭监测/风险/作业；业务监测结束另调用close_time_monitoring，明确要取消在途任务另需cancel授权。',
}
PARAMETER_NOTES = {
    'agent_id': '已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。',
    'description': '本次子任务或周期任务的具体工作要求，不将描述文字当作结构化input参数。',
    'input': '目标Agent input_schema认可的结构化对象；P6传{}或start_time，P7通常传{}，P8/P9传mode及review所需report_id。',
    'artifact_ids': '已登记且可访问的成果引用ID数组，最多100项，不传任意主机路径。',
    'artifact_inputs': '目标输入字段名到成果引用ID的映射，供可信成果传递。',
    'depends_on': '前置任务ID数组，取实际回执；依赖未完成不开始后续，不伪造ID。',
    'request_id': '幂等请求标识；同键同内容重放，内容改变或有意创建新任务须新键；心跳创建必填。',
    'task_id': '当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。',
    'task_ids': '非空当前协作链任务ID数组，每项需status和result权限。',
    'schedule_id': '自己创建的持久计划ID；创建时省略，更新/控制取真实回执。',
    'version': '更新计划时提供当前版本；不是事件版本、任务次数或材料版本。',
    'enabled': '是否启用持续计划；没有用户持续运行授权不能自行置true。',
    'interval_seconds': '调度周期H，单位秒，1–86400整数；不是P6消费窗口N。',
    'first_run': 'immediate立即首次触发；after_interval一个H之后首次触发，默认后者。',
    'overlap_scope':'self仅对目标Agent本身防重入，子Agent继续运行不阻塞；chain对整个协作链防重入。每轮独立会话。',
    'timeout_seconds':'单轮执行超时秒数，不超过目标Agent上限；超时失败并回收未提交领取。',
    'overlap_policy': '当前只支持skip，有在途任务时跳过本次触发，不丢弃待消费数据。',
    'overlap_key': '可选共享防重叠分组，最多128字符；留空按目标Agent互斥。',
    'action': 'pause暂停、resume恢复或stop停止计划，仅影响未来触发，不取消在途任务。',
}
for _name, _spec in TASK_TOOLS.items():
    _spec['description'] += ' ' + ENGINEERING_NOTES[_name]
    for _field, _schema in _spec['schema'].get('properties', {}).items():
        if _field in PARAMETER_NOTES:
            _schema['description'] = PARAMETER_NOTES[_field]


def envelope(value=None, error=None):
    """所有能力使用同一 JSON 回执；保留旧结构字段以兼容已有会话记录。

    structuredContent 是普通 JSON 数据字段，不表示内置能力仍通过 MCP 传输。
    外部 MCP 的文本、资源链接等原始回执放在 value 中，避免丢失内容。
    """
    return {"ok": error is None, "isError": error is not None,
            "structuredContent": {"error": error} if error else value,
            **({"error": error} if error else {})}
