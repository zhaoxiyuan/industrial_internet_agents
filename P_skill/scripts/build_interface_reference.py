from pathlib import Path
import ast,json,runpy,hashlib
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'P_skill'
PLATFORM=next(ROOT.glob('skill_decoupling*'))
OUT=BASE/'SKILL提示词与工具接口说明.md'
meaning={
'job_id':'业务作业标识；新绑定时与作业票号码相同，用于隔离业务描述、事件和处置。','permit_id':'作业票号码；新绑定必须与job_id相同。','start_time':'监测归属起点，使用带时区时间。','end_time':'可选归属终点，不填写则没有时间上限；不代表自动关闭作业。','person_ids':'归属该作业的人员ID；可为空，与sensor_ids至少一类非空。','sensor_ids':'归属该作业的传感器/设备ID；可为空，与person_ids至少一类非空。','area_ids':'可选区域ID范围。','operators':'可选处置人员配置。','reviewers':'可选审核人员配置。','basis':'事实或业务归属依据；绑定时为用户确认的归属说明。','window_seconds':'P6本次领取的数据窗口长度N，与心跳间隔H分开。','claim':'原样使用领取回执中的任务身份、租约等字段，不能自造。','lease_seconds':'申请或续租的有效秒数。','record_id':'来源事实/业务描述记录标识，使用实际回执。','summary':'本次事实或处理结果摘要。','descriptions':'P6使用本次观察序号及事实说明；其他阶段按自身工具Schema。','limitations':'缺测、证据或审核限制。','evidence_refs':'用于核对的证据引用。','reason':'失败、重试或控制操作的实际原因。','cutoff':'结束监测的明确截止时刻，不能用播放结束代替。','kind':'操作对象类型，按枚举选择。','assessments':'P7逐风险key的研判结果。','event_id':'P7持久风险事件ID。','report_id':'风险报告/处置记录标识，持续追踪同一风险。','snapshot_id':'不可变审核快照标识。','material_id':'整改材料标识。','decision':'审核意见内容；不替代人工关闭决定。','agent_id':'目标已配置Agent ID，不创建Agent。','task_id':'已提交任务的ID。','task_ids':'需要等待的本次协作任务ID集合。','description':'发给目标Agent的任务描述。','input':'结构化任务输入；P6传{}或start_time，窗口长度由Main配置。','artifact_ids':'转交给子任务的已登记成果ID列表。','artifact_inputs':'子任务输入字段与成果ID的对应映射。','depends_on':'前置任务ID；依赖完成后才可推进。','request_id':'调用幂等标识；相同请求不得换参数重复使用。','schedule_id':'持久心跳计划ID，不能用任务ID替代。','version':'更新计划时提供当前版本，用于并发冲突校验。','enabled':'计划是否启用。','interval_seconds':'触发间隔H，单位秒，不是数据窗口N。','first_run':'首次立即触发或一个周期后触发。','overlap_policy':'当前只支持skip：目标仍在运行时跳过本次触发。','overlap_key':'可选共享防重叠分组键；具体互斥行为由调度器实施。','action':'计划控制动作pause/resume/stop；不取消在途任务。','offset':'分页起始偏移。','limit':'本页最大记录数量。','risk_level':'风险等级过滤；null为全部。','template_id':'报告模板标识。','export_id':'Main导出的不可变报告数据快照标识。'}
lines=['# Skill 提示词、模型工具目录与参数说明','', '整理日期：2026-10-15。本文对应当前本地源码；不是实时平台注册/授权清单。重新生成：`python P_skill/scripts/build_interface_reference.py`。','',
'## 1. 模型实际看到什么','',
'平台先注入Agent自己的system_prompt、平台执行规则和已启用Skill的名称/描述。Skill正文按需读取，而非所有文件一次性全部注入。模型工具目录由有效绑定和有方向协作授权生成，声明某工具不代表每个Agent都有权调用。运行时再次校验权限与输入。',
'', 'Skill业务工具在模型目录中的名称为 `cap_<绑定ID前12位>__<工具原名>`，描述为tools.json中的description加“已授权 Skill 工具：<Skill名>”。下文使用原工具名便于维护。平台协作工具保留原名。动态ID、密钥、模型配置和共享目录由平台注入，不由模型填写。',
'', 'Main、P6、P7、P8/P9完整ZIP包含本地业务实现；共享SQLite目录由平台提供，不需要单独的数据库Docker。mock仍是外部采集服务，飞书卡片发送/更新/回调属于平台。P10保持原实现及外部报告入口。',
'', '## 2. 参数阅读规则','', '“必填”表示JSON Schema required；数组字段必填不等于必须非空。参数Schema已有description时逐字展示，模型直接可见；无description的字段，下面“业务含义”是维护注释，不宣称模型原Schema自带这段文字。完整Schema逐项附后，包含嵌套字段、枚举、数值边界与additionalProperties；最终还需通过业务代码校验。', '',
'当前作业绑定：一个票号同时传job_id/permit_id；start_time必填；person_ids、sensor_ids均传数组且至少一类非空；basis非空；end_time、area_ids、operators、reviewers可选。结构化规则为参考，P6仍读取所有授权监测通道；绑定不得覆盖其他活动作业。常规关闭监测需reason及进度校验，cutoff可省略；可信人工决定关闭可越过业务阻塞并释放票绑定。', '']
def tool_doc(name,description,schema):
 lines.extend(['### `'+name+'`','', '**模型工具描述原文：** '+description,'','| 参数 | 类型 | 必填 | 业务含义/原字段描述 |','|---|---|---|---|'])
 for key,value in schema.get('properties',{}).items():
  desc=value.get('description') or meaning.get(key,'见下方完整Schema及对应工具实现。')
  limits={k:v for k,v in value.items() if k not in ('description','properties','items','type')}
  if limits:desc+='；约束：`'+json.dumps(limits,ensure_ascii=False)+'`'
  lines.append('| `'+key+'` | '+str(value.get('type','按Schema'))+' | '+('是' if key in schema.get('required',[]) else '否')+' | '+desc.replace('|','\\|').replace('\n',' ')+' |')
 if not schema.get('properties'):lines.append('| 无参数 | object | — | 传 `{}` |')
 lines.extend(['','**模型参数 Schema 原文：**','','```json',json.dumps(schema,ensure_ascii=False,indent=2),'```',''])
for stage in ['main','P6','P7','P8']:
 folder=BASE/stage
 lines.extend(['## '+stage+('（P8/P9合并Agent）' if stage=='P8' else ''),'','来源：`'+folder.relative_to(ROOT).as_posix()+'/SKILL.md` 与 `tools.json`。','','### Skill 提示词完整原文','',*['> '+line if line else '>' for line in folder.joinpath('SKILL.md').read_text(encoding='utf-8').strip().splitlines()],''])
 for t in json.loads(folder.joinpath('tools.json').read_text(encoding='utf-8'))['tools']:tool_doc(t['name'],t['description'],t['input_schema'])
lines.extend(['## P10（复用原报告Skill）','','来源：`a_mac_skill/P10/SKILL.md`、`a_mac_skill/P10/tools.py`。P_skill/P10仅记录接入方案，没有新开发一份SKILL.md。','','### Skill提示词完整原文','',*['> '+line if line else '>' for line in (ROOT/'a_mac_skill/P10/SKILL.md').read_text(encoding='utf-8').strip().splitlines()],'', '下面四个工具属于原P10报告生成模型，在原报告服务内部构建；不等同于平台P10 Agent直接拥有四个同名工具。平台侧通过已绑定HTTP提供者的动态cap_*__call工具提交/查询export_id，确切目录取决于当前绑定。HTTP /submit与/status均接受且只接受export_id，管理员负责对应真实作业入口及鉴权。',''])
tree=ast.parse((ROOT/'a_mac_skill/P10/tools.py').read_text(encoding='utf-8'))
for n in ast.walk(tree):
 if not isinstance(n,ast.FunctionDef) or not n.name.startswith('p10_'):continue
 props={};required=[];start=len(n.args.args)-len(n.args.defaults)
 for i,arg in enumerate(n.args.args):
  typ=ast.unparse(arg.annotation);v={'type':'integer' if typ.startswith('int') else 'string'}
  if 'None' in typ:v['type']=['integer','null']
  if i>=start:v['default']=ast.literal_eval(n.args.defaults[i-start])
  else:required.append(arg.arg)
  if arg.arg=='offset':v['minimum']=0
  if arg.arg=='limit':v.update(minimum=1,maximum=500)
  if arg.arg=='risk_level':v.update(minimum=0,maximum=5)
  props[arg.arg]=v
 tool_doc(n.name,ast.get_docstring(n),{'type':'object','properties':props,'required':required})
lines.extend(['上述P10参数Schema依据函数签名整理，不是声称从当前线上模型请求抓取。代码另校验job_id必须等于快照绑定作业、模板必须等于本次绑定模板。','', '## 平台多智能体协作工具','', '来源：`platform_core/contracts.py`。有方向权限：submit/status/result/cancel/overview/schedule。wait_tasks需要可用status与result授权；schedule独立授权，不由submit隐式授予。并发是按Agent配置；wait_tasks持久化并释放当前执行资源，其他Agent可继续运行。模型单次请求parallel_tool_calls=False不代表整个平台串行。',''])
for name,t in runpy.run_path(str(PLATFORM/'platform_core/contracts.py'))['TASK_TOOLS'].items():tool_doc(name,t['description'],t['schema'])
lines.extend(['## 平台飞书能力（包含单风险卡片）','','来源：platform_core/feishu.py。根据当前连接绑定生成目录，账号/收件范围/模板由平台控制，Skill不声明发送实现。',''])
feishu_tree=ast.parse((PLATFORM/'platform_core/feishu.py').read_text(encoding='utf-8'))
constants=[]
for node in feishu_tree.body:
 if isinstance(node,ast.ClassDef):break
 if isinstance(node,ast.Assign) or isinstance(node,ast.FunctionDef) and node.name=='obj':constants.append(node)
namespace={}
exec(compile(ast.Module(body=constants,type_ignores=[]),'<feishu declarations>','exec'),namespace)
for name,schema in namespace['SCHEMAS'].items():tool_doc(name,namespace['DESCRIPTIONS'][name],schema)
lines.extend(['## 平台成果引用工具','','来源：platform_core/artifacts.py；不用于读取任意业务文件。',''])
artifact_tree=ast.parse((PLATFORM/'platform_core/artifacts.py').read_text(encoding='utf-8'))
assigns=[node for node in artifact_tree.body if isinstance(node,ast.Assign)]
namespace={}
exec(compile(ast.Module(body=assigns,type_ignores=[]),'<artifact schema>','exec'),namespace)
for name,t in namespace['ARTIFACT_TOOLS'].items():tool_doc(name,t['description'],t['schema'])
lines.extend(['## 补充：Skill读取、平台成果与运行边界','', '`read_skill`：描述原文“按任务需要读取已绑定 Skill 的正文或参考资料，不执行脚本。”参数skill_id必填且限定当前Agent已启用Skill ID；path可选，省略为SKILL.md，只能使用包内相对路径。官方Skill middleware另有受控read_file能力；不可据此读取其他Agent私有Skill或任意主机文件。','', '平台成果工具支持登记与读取授权成果引用，便于子任务传递，不代表任意文件访问。平台通用执行提示要求通过wait_tasks等待子任务、不得猜测结果或反复查询。会话属于各自Agent，子任务成果共享遵循协作权限，不共享完整私有聊天上下文。','', '本轮SKILL.md改为业务正文，工程约束进入工具描述/参数description；完整维护分工与限制见PROMPT_TOOL_SEPARATION.md。本说明不是操作授权，不启心跳、不绑定新作业、不发送真实飞书消息，也不重写P10。','', '## 源码核对索引',''])
for path in [BASE/'main/SKILL.md',BASE/'main/tools.json',BASE/'runtime/industrial_skills/work_permits.py',PLATFORM/'platform_core/contracts.py',PLATFORM/'platform_core/agents.py',ROOT/'a_mac_skill/P10/tools.py']:
 lines.append('- `'+path.relative_to(ROOT).as_posix()+'`：SHA256 `'+hashlib.sha256(path.read_bytes()).hexdigest()+'`')
OUT.write_text(('\n'.join(lines)+'\n').replace('业务监测结束另调用close_monitoring','业务监测结束使用当前Skill的close_time_monitoring'),encoding='utf-8')
print(str(OUT),len(lines),'lines')
