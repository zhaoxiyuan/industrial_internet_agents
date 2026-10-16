"""Generate platform declarations/wrappers from one checked contract; never runtime data."""
import json
import ast
import hashlib
import zipfile
from pathlib import Path
from tool_contracts import enrich

ROOT = Path(__file__).resolve().parents[1]
S = {'type': 'string', 'minLength': 1}
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 6000}
def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required, 'additionalProperties': False}
def arr(item):
    return {'type': 'array', 'items': item, 'maxItems': 1000}
CLAIM = obj({'kind': S, 'id': S, 'token': S})
LEASE = {'type': 'integer', 'minimum': 30, 'maximum': 1800}
REF = obj({'table': S, 'id': {'type': 'integer', 'minimum': 1}})
FAIL = obj({'claim': CLAIM, 'reason': TEXT},['reason'])
TOOLS = {
 'P6': ('p6', []),
 'P7': ('p7', [
  ('claim_description', '按时间顺序领取待处理描述，禁止跳过前序。', obj({'lease_seconds': LEASE}, []), 'fail_description'),
  ('complete_assessment', '研判全部异常 key 并事务提交；工具以实际正常证据执行跨窗闭合，返回 closed_events。', obj({'assessments': arr(obj({'key': S, 'risk_level': {'type': 'integer', 'minimum': 0, 'maximum': 5}, 'basis': TEXT, 'advice': arr(S)}))})),
  ('report_closed_event', '仅对已结束事件生成不可变最终报告和 P8 outbox；一个事件一个报告。', obj({'event_id': S, 'summary': TEXT})),
  ('read_event', '读取授权事件事实、历次证据和状态；补报前核实。', obj({'event_id': S})),
  ('renew_description_lease', '续租当前 P7 描述。', obj({'lease_seconds': LEASE})),
  ('fail_description', '保存 P7 失败，等待 Main 显式重试。', FAIL)]),
 'P8': ('disposition', [
  ('claim_risk_report', '领取 P7 最新事件通知（开放、未知、恢复中、结束均可），兼容旧最终报告；由Main调度。', obj({'lease_seconds': LEASE}, []), 'fail_risk_report'),
  ('initialize_disposition', '按最新P7快照创建或更新同一事件处置及卡片意图，保留人工状态与材料；不是发送回执。', obj({'claim': CLAIM})),
  ('read_disposition', '读取处置和平台写入的真实/模拟投递回执。', obj({'report_id': S})),
  ('read_review_snapshot', 'P9 模式读取当前材料版本快照。', obj({'report_id': S})),
  ('read_material', '读取当前快照材料文本；二进制不可读时明确限制。', obj({'snapshot_id': S, 'material_id': S})),
  ('save_review_opinion', '保存模型建议，不是人工审批；版本变更拒绝，未读材料拒绝。', obj({'snapshot_id': S, 'decision': {'enum': ['sufficient', 'insufficient', 'unknown']}, 'reason': TEXT, 'limitations': arr(S)})),
  ('verify_job_closable', '只读检查作业结束屏障，不代表人工关闭。', obj({})),
  ('fail_risk_report', '记录 P8 领取失败。', FAIL)]),
 'main': ('main', [
  ('bind_work_permit','绑定用户作业票；job_id与permit_id必须为同一票号。start_time必填；人员/设备至少一类非空，另一类传[]；end_time可选；basis为用户确认的归属说明。无默认票、不修改mock。', obj({'job_id':S,'permit_id':S,'start_time':S,'end_time':S,'person_ids':arr(S),'sensor_ids':arr(S),'area_ids':arr(S),'operators':{'anyOf':[arr(S),{'const':'registered'}]},'reviewers':{'anyOf':[arr(S),{'const':'registered'}]},'basis':TEXT},['job_id','permit_id','start_time','person_ids','sensor_ids','basis'])),
  ('query_pipeline', '读取队列、子 Agent ID、事件和作业屏障；P1-P5 仅接口。', obj({})),
  ('retry_failed', '显式重试失败项，保留审计；不得重试未知飞书发送。', obj({'kind': {'enum': ['description', 'report', 'event_notification']}, 'record_id': S, 'reason': TEXT})),
  ('reopen_monitoring', '明确恢复尚未关闭作业的监测；保留全部历史和归属范围。', obj({'reason': TEXT})),
 ])}

# Time-oriented v4 contracts replace model-facing source-task bookkeeping.
TOOLS['P6'] = ('p6', [
 ('read_current_window', '读取本次调用唯一窗口。参数{}从最早可领取窗口开始；start_time可选且须取Main时间列表原值。工具自动绑定当前作业与run，原子占用，返回时间范围、编号观察项、完整monitoring_data（PPE秒统计/传感器/位置/主体元数据）、required_observations；CV逐帧证据用read_current_evidence(source=cv)分页读取。规则仅为提示，未配置规则的通道仍返回；同一run重复读取返回同窗。completed表示本次已保存，no_work/blocked时据实结束。不传ID、票号、租约，不调用多次领取来处理多个窗口。', obj({'start_time':S}, []), 'fail_current_window'),
 ('read_current_evidence','按observation读取关联证据；或source=ppe/sensor/position读取完整表，source=cv分页读取原始逐帧记录（limit默认100，cursor取回执next_cursor）。所有读取限制本次窗口和人员范围，不另领取。',obj({'observation':{'type':'integer','minimum':1},'source':{'enum':['ppe','sensor','position','cv']},'cursor':S,'limit':{'type':'integer','minimum':1,'maximum':1000}},[])),
 ('save_current_description', '保存本次窗口唯一P6_P7风险描述。summary客观摘要；descriptions中observation使用required_observations的短整数序号，每个观测通道恰好一项（包括正常/未知）。state给整个通道默认判断，exceptions用从1起的样本序号闭区间覆盖变化段，须递增不重叠；证据缺失始终unknown。完整事实和证据由工具写入。相同内容重试幂等；结果未知先read_current_window。此工具不调度P7、不发送告警。', obj({'summary':TEXT,'descriptions':arr(obj({'observation':{'type':'integer','minimum':1},'state':{'enum':['normal','abnormal','unknown']},'exceptions':arr(obj({'from_sample':{'type':'integer','minimum':1},'to_sample':{'type':'integer','minimum':1},'state':{'enum':['normal','abnormal','unknown']}})),'explanation':TEXT},['observation','state','explanation'])),'limitations':arr(S)})),
 ('fail_current_window', '本次窗口无法完成时记录原因，Main按时间恢复；不需要记录ID。',obj({'reason':TEXT}))])
TOOLS['main'] = ('main', [e for e in TOOLS['main'][1] if e[0] in ('bind_work_permit','query_pipeline','retry_failed','reopen_monitoring')] + [
 ('configure_schedule','设置当前单票的窗口秒数1–300与最大P6并发1–16。存在窗口后窗口长度锁定；并发可调整。按次或心跳由Main使用平台任务/心跳工具安排；心跳间隔独立于窗口长度。',obj({'window_seconds':{'type':'integer','minimum':1,'maximum':300},'max_parallel':{'type':'integer','minimum':1,'maximum':16}},['window_seconds'])),
 ('list_time_windows','查询当前作业时间列表。limit为X条，默认20最多100；start_time可选，不传从最早开始；state筛选。返回真实time_range、状态、连续完成时间、pending_count、failed_count、available_slots和分页next_start_time。Main按空闲位派P6 input={}，或指定列表的start_time；不转抄ID。查询不领取。早于作业开始的数据不进入列表；相同状态与进度同时用于心跳和单次调用。',obj({'limit':{'type':'integer','minimum':1,'maximum':100},'start_time':S,'state':{'enum':['all','pending','processing','completed','failed','retry_ready']}},[])),
 ('retry_time_window','按列表中的窗口start_time恢复失败P6任务，理由必填，最多3次。重试保留窗口和历史，新P6调用接管旧失败任务；完成窗口不能重试。',obj({'start_time':S,'reason':TEXT})),
 ('close_time_monitoring','用户明确要求停止监测时立即调用，不等待截止时间、未完成窗口或在途P6。reason必填；cutoff可选，省略取工具当前时间，不要求窗口边界。保留未完成记录，停止后停用本票P6心跳；不等于人工关票或风险处置完成。',obj({'cutoff':S,'reason':TEXT},['reason']))])

TOOLS['main'][1].append(('configure_participants','按用户要求更新当前作业的接取/审核人员。operators/reviewers可传明确ID数组或registered（当前P8/P9真实绑定飞书连接.env中的全部登记用户）；省略某角色保持不变，[]无人授权，不支持任意人或*。目录由平台注入，不能由模型构造。reason必填；不修改作业票、处置状态或代替人工审核。',obj({'operators':{'anyOf':[arr(S),{'const':'registered'}]},'reviewers':{'anyOf':[arr(S),{'const':'registered'}]},'reason':TEXT},['reason'])))

def module_closure(module):
    """Include every transitive relative import, including imports inside tools."""
    source = ROOT/'runtime/industrial_skills'
    pending, found = [module], set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        tree = ast.parse((source/(name+'.py')).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                pending.extend([node.module.split('.')[0]] if node.module else [a.name for a in node.names])
    return sorted(found | {'__init__'})


def build():
    distributions = ROOT/'dist'
    distributions.mkdir(exist_ok=True)
    for directory, (module, entries) in TOOLS.items():
        root = ROOT / directory
        (root/'scripts').mkdir(exist_ok=True)
        library = root/'scripts/_business'
        library.mkdir(exist_ok=True)
        names = module_closure(module)
        # Only generated Python files in this exact directory may be removed.
        for old in library.glob('*.py'):
            if old.stem not in names:
                old.unlink()
        hashes = {}
        for name in names:
            data = (ROOT/'runtime/industrial_skills'/(name+'.py')).read_bytes()
            (library/(name+'.py')).write_bytes(data)
            hashes[name+'.py'] = hashlib.sha256(data).hexdigest()
        wrappers = ['"""Complete package-local tools; no preinstalled business library."""\n'
                    'import sys\nfrom pathlib import Path\n'
                    'sys.path.insert(0, str(Path(__file__).resolve().parent))\n']
        manifest = []
        for entry in entries:
            name, description, schema, *failure = entry
            description, schema = enrich(directory, name, description, schema)
            wrappers.append(f'def {name}(arguments, context):\n    from _business.{module} import {name} as implementation\n    return implementation(arguments, context)\n')
            item = {'name': name, 'description': description, 'input_schema': schema, 'output_schema': {'type':'object'},
                    'entry': 'scripts/tools.py:'+name, 'dependencies': [], 'storage': ['p6_p7'], 'timeout': 90}
            if failure:
                item['claim_failure'] = failure[0]
            manifest.append(item)
        (root/'scripts/tools.py').write_text('\n\n'.join(wrappers), encoding='utf-8')
        (root/'tools.json').write_text(json.dumps({'version':1,'tools':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (root/'references').mkdir(exist_ok=True)
        (root/'references/bundle.json').write_text(json.dumps({'bundle_version':1,
            'database_contract':'p6-p7-timeflow-v5', 'module_sha256':hashes},indent=2)+'\n',encoding='utf-8')
        package_name = next(line.split(':',1)[1].strip() for line in (root/'SKILL.md').read_text(encoding='utf-8').splitlines() if line.startswith('name:'))
        with zipfile.ZipFile(distributions/(package_name+'.zip'),'w',zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(root.rglob('*')):
                relative=file.relative_to(root)
                if not file.is_file() or '__pycache__' in relative.parts or file.suffix in ('.pyc','.pyo'):
                    continue
                if relative.as_posix() not in ('SKILL.md','tools.json') and relative.parts[0] not in ('scripts','references','assets'):
                    continue
                archive.write(file, package_name+'/'+relative.as_posix())

if __name__ == '__main__':
    build()
