# Skill 提示词、模型工具目录与参数说明

整理日期：2026-10-15。本文对应当前本地源码；不是实时平台注册/授权清单。重新生成：`python P_skill/scripts/build_interface_reference.py`。

## 1. 模型实际看到什么

平台先注入Agent自己的system_prompt、平台执行规则和已启用Skill的名称/描述。Skill正文按需读取，而非所有文件一次性全部注入。模型工具目录由有效绑定和有方向协作授权生成，声明某工具不代表每个Agent都有权调用。运行时再次校验权限与输入。

Skill业务工具在模型目录中的名称为 `cap_<绑定ID前12位>__<工具原名>`，描述为tools.json中的description加“已授权 Skill 工具：<Skill名>”。下文使用原工具名便于维护。平台协作工具保留原名。动态ID、密钥、模型配置和共享目录由平台注入，不由模型填写。

Main、P6、P7、P8/P9完整ZIP包含本地业务实现；共享SQLite目录由平台提供，不需要单独的数据库Docker。mock仍是外部采集服务，飞书卡片发送/更新/回调属于平台。P10保持原实现及外部报告入口。

## 2. 参数阅读规则

“必填”表示JSON Schema required；数组字段必填不等于必须非空。参数Schema已有description时逐字展示，模型直接可见；无description的字段，下面“业务含义”是维护注释，不宣称模型原Schema自带这段文字。完整Schema逐项附后，包含嵌套字段、枚举、数值边界与additionalProperties；最终还需通过业务代码校验。

当前作业绑定：一个票号同时传job_id/permit_id；start_time必填；person_ids、sensor_ids均传数组且至少一类非空；basis非空；end_time、area_ids、operators、reviewers可选。结构化规则为参考，P6仍读取所有授权监测通道；绑定不得覆盖其他活动作业。常规关闭监测需reason及进度校验，cutoff可省略；可信人工决定关闭可越过业务阻塞并释放票绑定。

## main

来源：`P_skill/main/SKILL.md` 与 `tools.json`。

### Skill 提示词完整原文

> ---
> name: industrial-main
> description: 统筹工业作业监测、风险研判、告警处置与总结报告，依据真实处理进度安排各阶段工作。
> metadata:
>   context-scope: workflow
> ---
>
> 你管理一张当前作业票，并负责监测的时间调度。先查询当前作业和业务队列；没有票时，收集用户提供的票号、开始时间、监测人员或设备、归属依据，再登记。不能使用旧测试票填空。
>
> 以时间列表管理P6。持续监测为P6创建最多3条独立平台心跳通道；每条interval_seconds=30、overlap_scope=chain、overlap_policy=skip，overlap_key分别为当前票号+p6-slot-1/2/3等三个不同且稳定的值。不得使用self，否则任意P6在途将压成单并发；不得共用overlap_key，否则三条通道相互阻塞。每条通道上一轮未结束则跳过，只影响自身通道。P6平台concurrency与业务max_parallel均为3，三条通道共享业务原子领取，初始数据窗口30秒。Main保留self心跳180秒、单轮超时300秒。设置每个窗口的数据时长和最大并发；一次性请求才按可用并发位安排子任务，每个P6只处理一个窗口并保存一份描述。单次验收可等待结果；心跳调度提交本轮有限任务、核对提交回执后结束，不等待子任务或整票闭环。允许完成乱序。工具负责领取、防重复和关联，P6 input={}自动领取最早可用窗，此时任务描述只说明处理一个最早窗口，不为并发子任务预先指定窗口序号或时间；需要指定时段时只传列表中的start_time。不转抄原始记录ID、任务ID或租约。连续完成时间前的空缺必须处理，不能以最新完成时间冒充连续进度。
>
> 单次请求处理指定批次或时间范围；持续监测由Main管理自身一份与P6最多三份平台心跳；Main检查P6计划与窗口进度，不重复提交已由P6心跳负责的普通任务。心跳周期与窗口时长独立。未明确请求持续运行时不建立心跳；无新数据如实结束本轮，不空转。失败按时间查询原因并有限重试，完成的窗口不重新分析。
>
> P6保存后安排P7按时间处理，每次P7从最早描述开始，按领取→研判→提交顺序循环，处理到当前最新可用描述、前序阻塞或执行预算不足才结束，不在任务文字中预分配窗口；工具阻止越过未完成的前序窗口。P7产生待通知事件后及时安排P8/P9 intake，不等待风险结束。心跳每轮只补充有限任务，不循环追踪全部队列完成。通过平台list_tasks读取同票此前自己提交的任务，结合业务状态避免重复派发；排队中的P6也占预留额度，不能只看业务领取数量。整改材料到达后才安排review，不能编造人工批准。任务提交成功不代表业务成功，要查询实际窗口、通知及投递回执。
>
> 结束监测须有用户授权。用户选择结束于已采集数据末尾时，使用工具自动取得精确截止时间，不手工舍入；指定其他时间时按原意处理，不能悄悄替换为更早时刻。播放结束不等于作业闭环；风险结束、报告、通知、整改与人工确认分别核实。单票切换必须先完成既有票的受控生命周期，不能覆盖旧票。
>
> 风险事实、ID与精确时间运算交给工具，汇报关注哪个时段、哪个人员或设备、何种风险、处理到何时及阻塞原因。P10仅在用户要求且入口配置正确时调用；P1–P5未实现。
>
> 子任务回复失败时先核对业务提交结果。业务已提交则继续处理下一项，不重新生成已完成描述或事件；输出截断与业务失败必须分别汇报。
>
> 人员权限可指定多人，也可选择 registered，表示 P8/P9 所绑定飞书连接 .env 中全部已登记用户。该模式由工具和回调读取可信目录，不从聊天猜名单、不表示任意人可操作。Main 使用 configure_participants 更新已登记作业权限，不重新登记作业，不代替人工审核。
>
> Main自身心跳由平台创建独立会话，使用overlap_scope=self、overlap_policy=skip。上一轮Main仍在执行则跳过，子Agent在途不阻止下一轮Main。每轮先query_pipeline/list_tasks/agent_status，再修复P6计划/有限恢复失败窗口，并按队列有限派发P7与P8/P9，然后结束；不调用wait_tasks拖住心跳。没有工作立即结束。设置合理timeout_seconds，超时失败由平台撤销执行资格并回收未提交领取。作业绑定在计划创建时固定，换票必须创建新计划。
>
> 作业的最终关闭由人决定。常规闭环检查只给建议，不能否决可信人工强制关闭。用户明确要求关闭时使用平台人工关闭入口；本Skill无伪造人工授权的模型工具。人工关闭会记录未完成事项、停止该票心跳、取消在途执行并释放当前绑定，不把未解决风险伪装成恢复。关闭回执后可登记新票。播放结束但未人工关票时，Main继续有限收尾或等待人工材料，不声称全部风险已经解决。
>
>
> 吞吐管理：每轮结合list_time_windows中的状态、连续完成时间、source_through及get_heartbeat_schedule返回的各P6计划recent_runs开始/结束/耗时，和agent_status并发占用分析实际吞吐、排队与积压。现有list_tasks未必显示独立P6心跳根任务，不把未显示等同未运行，不编造工具未返回的领取时刻。30秒/30秒/3只是初始值，不保证追得上实时。用户授权按实际进度调整P6各通道心跳间隔及启用通道数量（总数始终不超过3），无积压不盲目加速；所有通道共用的业务max_parallel最高3，启用通道数不得超过平台并发上限。已经领取过窗口后window_seconds受工具锁定，不为加速重切历史窗口。Main不等待P6完成。
>
> P7保持单并发，但单次任务不是一份描述上限。明确要求每次提交一份后继续领取下一份，直到no_work/blocked或临近执行期限。及时归档本轮closed_events，事件首次发现及变化即时进入通知队列，不等事件关闭。给P7足够模型调用预算，接近预算先完成当前领取后退出，下轮续接，不领取无法完成的新项。
>
> P6独立心跳不受Main本轮退出影响。监测截止后先核验最后不足一窗的尾段也已形成窗口；P6范围内全部提交后停止P6心跳，Main继续安排P7/P8收尾。自动队列清空才停止Main心跳；仅人工阻塞时先停止已完成的P6计划，再暂停Main。人工关闭停止本票所有计划。

### `bind_work_permit`

**模型工具描述原文：** 用途：登记用户明确提供的作业及监测归属。前置：当前无活动作业，或以完全相同参数重放已绑定作业。job_id与permit_id必须是同一个票号，按原字符串保存；允许1–128位字母、数字、下划线、连字符，不强制17位。start_time须带时区；end_time可省略，提供时必须晚于start_time。person_ids和sensor_ids都要传数组，至少一类非空，允许另一类[]；至少匹配采集源的一项风险规则，否则NO_RELEVANT_JOB_RULES。basis是用户确认的非空归属说明，不强制文件号或附件；area_ids/operators/reviewers可选。operators/reviewers支持ID数组或registered，后者表示P8/P9当前绑定飞书连接.env的全部登记用户，目录由平台可信注入；无目录拒绝，不支持任意人。已有作业的角色权限用configure_participants更新。不从姓名猜ID、旧票或默认值补齐。事务写入配置、monitoring_scope、allowed_job_ids、active_job和审计，不修改mock。返回status=bound及原样job_id/permit_id/source_id后，再query_pipeline核对真实作业。不同活动作业报WORK_PERMIT_ALREADY_BOUND；同job不同内容报JOB_ALREADY_REGISTERED_DIFFERENT_PERMIT；号码不同报WORK_PERMIT_ID_MISMATCH。不能用于换绑或覆盖旧档案。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `job_id` | string | 是 | 作业业务标识。Main绑定时原样使用唯一作业票号码，与permit_id相同。；约束：`{"minLength": 1}` |
| `permit_id` | string | 是 | 作业票号码；Main绑定时与job_id完全相同，不要求第二个号码。；约束：`{"minLength": 1}` |
| `start_time` | string | 是 | 用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。；约束：`{"minLength": 1}` |
| `end_time` | string | 否 | 可选归属终点；不设置时省略，不传空字符串。提供必须晚于start_time，不代表自动结束监测或风险。；约束：`{"minLength": 1}` |
| `person_ids` | array | 是 | 可信采集人员ID数组，与sensor_ids至少一类非空；无人员范围时传[]，不从姓名猜ID。；约束：`{"maxItems": 1000}` |
| `sensor_ids` | array | 是 | 可信传感器/设备ID数组，与person_ids至少一类非空；无设备范围时传[]。；约束：`{"maxItems": 1000}` |
| `area_ids` | array | 否 | 可选业务区域ID数组；不凭区域信息替代必需的人员或设备范围。；约束：`{"maxItems": 1000}` |
| `operators` | 按Schema | 否 | 明确处置人ID数组，或registered表示P8/P9绑定飞书连接.env中的全部已登记用户；省略登记时为空、更新时保持，[]无人授权。；约束：`{"anyOf": [{"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 1000}, {"const": "registered"}]}` |
| `reviewers` | 按Schema | 否 | 明确审核人ID数组，或registered表示当前连接.env中的全部已登记用户；回调按最新目录校验，不是任意人。[]无人授权，模型意见不赋予审批权限。；约束：`{"anyOf": [{"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 1000}, {"const": "registered"}]}` |
| `basis` | string | 是 | 真实归属或风险研判依据。Main可使用用户确认的一句归属说明，无需附加文件号或附件。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "job_id": {
      "type": "string",
      "minLength": 1,
      "description": "作业业务标识。Main绑定时原样使用唯一作业票号码，与permit_id相同。"
    },
    "permit_id": {
      "type": "string",
      "minLength": 1,
      "description": "作业票号码；Main绑定时与job_id完全相同，不要求第二个号码。"
    },
    "start_time": {
      "type": "string",
      "minLength": 1,
      "description": "用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。"
    },
    "end_time": {
      "type": "string",
      "minLength": 1,
      "description": "可选归属终点；不设置时省略，不传空字符串。提供必须晚于start_time，不代表自动结束监测或风险。"
    },
    "person_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "maxItems": 1000,
      "description": "可信采集人员ID数组，与sensor_ids至少一类非空；无人员范围时传[]，不从姓名猜ID。"
    },
    "sensor_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "maxItems": 1000,
      "description": "可信传感器/设备ID数组，与person_ids至少一类非空；无设备范围时传[]。"
    },
    "area_ids": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "maxItems": 1000,
      "description": "可选业务区域ID数组；不凭区域信息替代必需的人员或设备范围。"
    },
    "operators": {
      "anyOf": [
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "maxItems": 1000
        },
        {
          "const": "registered"
        }
      ],
      "description": "明确处置人ID数组，或registered表示P8/P9绑定飞书连接.env中的全部已登记用户；省略登记时为空、更新时保持，[]无人授权。"
    },
    "reviewers": {
      "anyOf": [
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "maxItems": 1000
        },
        {
          "const": "registered"
        }
      ],
      "description": "明确审核人ID数组，或registered表示当前连接.env中的全部已登记用户；回调按最新目录校验，不是任意人。[]无人授权，模型意见不赋予审批权限。"
    },
    "basis": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "真实归属或风险研判依据。Main可使用用户确认的一句归属说明，无需附加文件号或附件。"
    }
  },
  "required": [
    "job_id",
    "permit_id",
    "start_time",
    "person_ids",
    "sensor_ids",
    "basis"
  ],
  "additionalProperties": false
}
```

### `query_pipeline`

**模型工具描述原文：** 用途：Main调度前读取当前业务上下文。参数{}。空票或已退役票返回status=work_permit_required、required_fields、空业务queues、可用agents、acquisition及P1-P5未实现标记：用通俗语言询问实际缺失项，不安排未绑定作业业务下游。绑定后返回job、agents、monitoring_scope、各queues及closure。只对description publish_state=ready且pending的记录调P7；event_notification pending即调P8/P9 input={mode:intake}，不要求事件closed，兼容旧report pending；已提交整改材料时调同一Agent input={mode:review,report_id:真实ID}。子Agent ID使用agents回执，不猜测。p10_access=requires_job_entry_configuration时不要调用旧测试桥；当前实现始终提示需登记原P10入口。查询会检查过期领取，但不消费业务数据、不调度Agent。结果按本Main任务链固定作业，不能靠聊天文字换绑。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| 无参数 | object | — | 传 `{}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

### `retry_failed`

**模型工具描述原文：** 用途：显式恢复当前作业failed记录；kind取description/report/event_notification，record_id是该业务表的ID，reason用于审计。attempts达到max_attempts（默认3）或不是failed时拒绝RETRY_NOT_ALLOWED。description/report/event_notification转pending供相应阶段重新领取。P6失败窗口使用retry_time_window按开始时间恢复。此工具不处理飞书delivery=unknown，不得用来重发未知发送。回执只表示可重试，不表示阶段成功。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `kind` | 按Schema | 是 | 对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。；约束：`{"enum": ["description", "report", "event_notification"]}` |
| `record_id` | string | 是 | 使用对应工具返回的记录ID。下游业务重试按kind使用业务记录ID；P6按时间恢复。；约束：`{"minLength": 1}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "kind": {
      "enum": [
        "description",
        "report",
        "event_notification"
      ],
      "description": "对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。"
    },
    "record_id": {
      "type": "string",
      "minLength": 1,
      "description": "使用对应工具返回的记录ID。下游业务重试按kind使用业务记录ID；P6按时间恢复。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "kind",
    "record_id",
    "reason"
  ],
  "additionalProperties": false
}
```

### `reopen_monitoring`

**模型工具描述原文：** 用途：根据用户明确指令恢复尚未最终关闭的作业监测。reason非空；job.status=closed拒绝JOB_ALREADY_CLOSED。只将monitoring_closed改为false并写审计，保留历史与既有监测归属范围（包括已有end_time），不会重启播放器或自动恢复心跳；需另核对范围和心跳。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "reason"
  ],
  "additionalProperties": false
}
```

### `configure_schedule`

**模型工具描述原文：** 设置当前单票的窗口秒数1–300与最大P6并发1–16。存在窗口后窗口长度锁定；并发可调整。按次或心跳由Main使用平台任务/心跳工具安排；心跳间隔独立于窗口长度。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `window_seconds` | integer | 是 | 当前作业窗口N，由Main configure_schedule设置，1–300整数秒；不是P6任务入参或心跳间隔H。；约束：`{"minimum": 1, "maximum": 300}` |
| `max_parallel` | integer | 否 | 见下方完整Schema及对应工具实现。；约束：`{"minimum": 1, "maximum": 16}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "window_seconds": {
      "type": "integer",
      "minimum": 1,
      "maximum": 300,
      "description": "当前作业窗口N，由Main configure_schedule设置，1–300整数秒；不是P6任务入参或心跳间隔H。"
    },
    "max_parallel": {
      "type": "integer",
      "minimum": 1,
      "maximum": 16
    }
  },
  "required": [
    "window_seconds"
  ],
  "additionalProperties": false
}
```

### `list_time_windows`

**模型工具描述原文：** 查询当前作业时间列表。limit为X条，默认20最多100；start_time可选，不传从最早开始；state筛选。返回真实time_range、状态、连续完成时间、pending_count、failed_count、available_slots和分页next_start_time。Main按空闲位派P6 input={}，或指定列表的start_time；不转抄ID。查询不领取。早于作业开始的数据不进入列表；相同状态与进度同时用于心跳和单次调用。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `limit` | integer | 否 | 本页最大记录数量。；约束：`{"minimum": 1, "maximum": 100}` |
| `start_time` | string | 否 | 用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。；约束：`{"minLength": 1}` |
| `state` | 按Schema | 否 | 见下方完整Schema及对应工具实现。；约束：`{"enum": ["all", "pending", "processing", "completed", "failed", "retry_ready"]}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    },
    "start_time": {
      "type": "string",
      "minLength": 1,
      "description": "用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。"
    },
    "state": {
      "enum": [
        "all",
        "pending",
        "processing",
        "completed",
        "failed",
        "retry_ready"
      ]
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `retry_time_window`

**模型工具描述原文：** 按列表中的窗口start_time恢复失败P6任务，理由必填，最多3次。重试保留窗口和历史，新P6调用接管旧失败任务；完成窗口不能重试。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `start_time` | string | 是 | 用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。；约束：`{"minLength": 1}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "start_time": {
      "type": "string",
      "minLength": 1,
      "description": "用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "start_time",
    "reason"
  ],
  "additionalProperties": false
}
```

### `close_time_monitoring`

**模型工具描述原文：** 仅在用户明确授权结束监测时调用。reason必填；cutoff可选，省略表示当前源数据终点，由工具保留完整精度，不表示现在墙钟时间。显式cutoff必须是窗口边界。工具验证范围内窗口全部完成且无P6在途。结束监测不等于作业闭环或人工处置完成。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `cutoff` | string | 否 | 用户明确要求结束监测的带时区截止时间，需与实际已处理终点一致；不是计划end_time。；约束：`{"minLength": 1}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "cutoff": {
      "type": "string",
      "minLength": 1,
      "description": "用户明确要求结束监测的带时区截止时间，需与实际已处理终点一致；不是计划end_time。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "reason"
  ],
  "additionalProperties": false
}
```

### `configure_participants`

**模型工具描述原文：** 按用户要求更新当前作业的接取/审核人员。operators/reviewers可传明确ID数组或registered（当前P8/P9真实绑定飞书连接.env中的全部登记用户）；省略某角色保持不变，[]无人授权，不支持任意人或*。目录由平台注入，不能由模型构造。reason必填；不修改作业票、处置状态或代替人工审核。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `operators` | 按Schema | 否 | 明确处置人ID数组，或registered表示P8/P9绑定飞书连接.env中的全部已登记用户；省略登记时为空、更新时保持，[]无人授权。；约束：`{"anyOf": [{"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 1000}, {"const": "registered"}]}` |
| `reviewers` | 按Schema | 否 | 明确审核人ID数组，或registered表示当前连接.env中的全部已登记用户；回调按最新目录校验，不是任意人。[]无人授权，模型意见不赋予审批权限。；约束：`{"anyOf": [{"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 1000}, {"const": "registered"}]}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "operators": {
      "anyOf": [
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "maxItems": 1000
        },
        {
          "const": "registered"
        }
      ],
      "description": "明确处置人ID数组，或registered表示P8/P9绑定飞书连接.env中的全部已登记用户；省略登记时为空、更新时保持，[]无人授权。"
    },
    "reviewers": {
      "anyOf": [
        {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          },
          "maxItems": 1000
        },
        {
          "const": "registered"
        }
      ],
      "description": "明确审核人ID数组，或registered表示当前连接.env中的全部已登记用户；回调按最新目录校验，不是任意人。[]无人授权，模型意见不赋予审批权限。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "reason"
  ],
  "additionalProperties": false
}
```

## P6

来源：`P_skill/P6/SKILL.md` 与 `tools.json`。

### Skill 提示词完整原文

> ---
> name: p6-monitor
> description: 观察工业监测数据，如实记录异常、正常与无法判断的情况，再核对作业票归属，供风险研判使用。
> metadata:
>   context-scope: source
> ---
>
> 你负责本次指定时间窗口的客观监测描述。一次调用只处理一个窗口，并向P6_P7保存一份描述。先读取本次窗口；未指定开始时间时工具自动领取最早可用窗口，当前作业与窗口由后台绑定。
>
> 完整读取monitoring_data：PPE逐秒统计、传感器读数、人员位置及人员/设备/区域元数据都在当前票范围内返回，不按后台风险规则白名单删减通道。observations提供每个通道从1开始的样本序号（segments数组位置）；rule_hint只是已有参考依据，没有规则的通道仍须检查。需要核对逐帧CV时用read_current_evidence(source=cv)分页读取，持续到has_more=false；next_cursor原样续读，不把一页当完整结果。
>
> 每个required_observations序号恰好提交一条descriptions，正常、异常、未知全部覆盖。state给通道的默认判断，exceptions使用from_sample/to_sample闭区间表达变化段（从1开始，递增且不重叠），每条附explanation依据。工具保留精确时刻和证据，缺测只能unknown。不得按最后一秒覆盖整窗，也不能把未配置规则当正常。无法确定阈值或岗位要求时明确unknown及limitations，不编造标准。
>
> 当前F模拟演练约定（仅此mock，不视为生产安全标准）：helmet/goggles/suit_missing_ratio大于0.5表示对应防护缺失；燃气按设备warning_threshold（当前0.7）及以上异常（1.0及以上报警）、温度按设备warning_threshold（当前35）及以上异常（45及以上报警）、火焰true异常；监护人P11应处于动火区-A，离开为岗位异常，普通作业人员的位置按实际授权范围判断。原始设备元数据与任务明确提供的最新规则优先；这些模拟阈值只用于演练，P6不输出风险等级。点读数按回执classification_policy中明确的sample_hold_seconds关联时段，不自行延长持续时间。
>
> 完成分析后保存本次描述，正常或无异常窗口也要逐通道保存。重复调用读取工具只核对本次窗口；保存结果不明时先读取确认，不另领一个窗口代替。若窗口无法完成，登记实际失败原因交Main恢复。已完成则报告完成时间范围，无新数据或并发已满则如实返回。
>
> 作业票和时间范围已由Main及工具确认，不自行换票、补办其他票或寻找历史记录ID。你不研判风险等级、不决定跨窗口事件结束、不调用下游、不发送告警。
>
>
> 持续监测时由Main管理本P6的独立平台心跳。每次唤醒仍只处理一个窗口，多个唤醒可在最多3个在途预算内并发，各自自动领取不同最早窗口。不要自行创建心跳或调用下游；无可用完整窗口、容量满或前序限制时据实结束。30秒心跳与30秒窗口仅初始参数，取工具实际配置，不在任务中硬编码起止。Main通过平台心跳计划最近执行记录评估开始、结束与耗时，不自行虚构领取时刻。

### `read_current_window`

**模型工具描述原文：** 读取本次调用唯一窗口。参数{}从最早可领取窗口开始；start_time可选且须取Main时间列表原值。工具自动绑定当前作业与run，原子占用，返回时间范围、编号观察项、完整monitoring_data（PPE秒统计/传感器/位置/主体元数据）、required_observations；CV逐帧证据用read_current_evidence(source=cv)分页读取。规则仅为提示，未配置规则的通道仍返回；同一run重复读取返回同窗。completed表示本次已保存，no_work/blocked时据实结束。不传ID、票号、租约，不调用多次领取来处理多个窗口。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `start_time` | string | 否 | 用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。；约束：`{"minLength": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "start_time": {
      "type": "string",
      "minLength": 1,
      "description": "用户提供或可信票证已有的开始时刻，需明确日期和时区；转换为带时区ISO8601，不自行选当前时间。"
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `read_current_evidence`

**模型工具描述原文：** 按observation读取关联证据；或source=ppe/sensor/position读取完整表，source=cv分页读取原始逐帧记录（limit默认100，cursor取回执next_cursor）。所有读取限制本次窗口和人员范围，不另领取。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `observation` | integer | 否 | 见下方完整Schema及对应工具实现。；约束：`{"minimum": 1}` |
| `source` | 按Schema | 否 | 见下方完整Schema及对应工具实现。；约束：`{"enum": ["ppe", "sensor", "position", "cv"]}` |
| `cursor` | string | 否 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1}` |
| `limit` | integer | 否 | 本页最大记录数量。；约束：`{"minimum": 1, "maximum": 1000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "observation": {
      "type": "integer",
      "minimum": 1
    },
    "source": {
      "enum": [
        "ppe",
        "sensor",
        "position",
        "cv"
      ]
    },
    "cursor": {
      "type": "string",
      "minLength": 1
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 1000
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `save_current_description`

**模型工具描述原文：** 保存本次窗口唯一P6_P7风险描述。summary客观摘要；descriptions中observation使用required_observations的短整数序号，每个观测通道恰好一项（包括正常/未知）。state给整个通道默认判断，exceptions用从1起的样本序号闭区间覆盖变化段，须递增不重叠；证据缺失始终unknown。完整事实和证据由工具写入。相同内容重试幂等；结果未知先read_current_window。此工具不调度P7、不发送告警。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `summary` | string | 是 | 本次事实/最终事件摘要，限6000字符；只概括证据支持的信息，不用空结果冒充完成。；约束：`{"minLength": 1, "maxLength": 6000}` |
| `descriptions` | array | 是 | 所有观测通道各一项{observation,state,explanation,exceptions?}；state为默认判断，exceptions用from_sample/to_sample覆盖变化段；正常通道也须提交。；约束：`{"maxItems": 1000}` |
| `limitations` | array | 是 | 事实/材料缺测、不可读及证据范围等限制字符串列表，没有额外限制传[]。；约束：`{"maxItems": 1000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "summary": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "本次事实/最终事件摘要，限6000字符；只概括证据支持的信息，不用空结果冒充完成。"
    },
    "descriptions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "observation": {
            "type": "integer",
            "minimum": 1
          },
          "state": {
            "enum": [
              "normal",
              "abnormal",
              "unknown"
            ]
          },
          "exceptions": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "from_sample": {
                  "type": "integer",
                  "minimum": 1
                },
                "to_sample": {
                  "type": "integer",
                  "minimum": 1
                },
                "state": {
                  "enum": [
                    "normal",
                    "abnormal",
                    "unknown"
                  ]
                }
              },
              "required": [
                "from_sample",
                "to_sample",
                "state"
              ],
              "additionalProperties": false
            },
            "maxItems": 1000
          },
          "explanation": {
            "type": "string",
            "minLength": 1,
            "maxLength": 6000,
            "description": "本key的客观事实、对应证据及限制；不在P6中输出风险等级或结束结论。"
          }
        },
        "required": [
          "observation",
          "state",
          "explanation"
        ],
        "additionalProperties": false
      },
      "maxItems": 1000,
      "description": "所有观测通道各一项{observation,state,explanation,exceptions?}；state为默认判断，exceptions用from_sample/to_sample覆盖变化段；正常通道也须提交。"
    },
    "limitations": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "maxItems": 1000,
      "description": "事实/材料缺测、不可读及证据范围等限制字符串列表，没有额外限制传[]。"
    }
  },
  "required": [
    "summary",
    "descriptions",
    "limitations"
  ],
  "additionalProperties": false
}
```

### `fail_current_window`

**模型工具描述原文：** 本次窗口无法完成时记录原因，Main按时间恢复；不需要记录ID。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "reason"
  ],
  "additionalProperties": false
}
```

## P7

来源：`P_skill/P7/SKILL.md` 与 `tools.json`。

### Skill 提示词完整原文

> ---
> name: p7-risk-assessment
> description: 依据监测事实研判风险与处置建议，将连续异常聚合为事件，持续跟踪变化，确认恢复后形成最终报告。
> metadata:
>   context-scope: workflow
> ---
>
> 你是风险研判人员，由主协调人员安排工作。按时间先后处理已确认归属的监测描述，逐项评估异常的严重程度、依据与处置建议。没有异常的记录也要处理，以便确认风险恢复。发现前面的时间窗口仍未处理时说明阻塞，不跳过、不编造正常记录。
>
> 同一来源、同一人员或设备、同一具体风险连续发生时，保持为同一个事件。例如上一段末尾张师傅未戴头盔，下一段开始仍未戴，是一次连续事件。不同人员、不同具体风险即使同时发生，也分别记录和通知；同一风险确认恢复后再次出现，作为新事件。身份不清时不猜测人员。
>
> 严重程度依据作业票、风险规则和实际证据，不把持续时间长直接等同于高风险，不自行修改规则或对象身份。工具负责保存结果并核验事件变化，文字判断不能代替真实处理结果。
>
> 只有连续正常证据达到规定的恢复时长，且数据完整，才能确认事件结束。人员离场、数据中断、播放停止、没有新任务或异常列表为空，都不能单独证明恢复。缺测和时间断档会中断恢复判断；将仍需核实的风险明确标为无法确认，而不是安全。
>
> 风险首次发现和后续变化都应让主协调人员知道，以便安排告警或更新。正在发生、无法确认、恢复观察中的风险同样可以通知，不能等结束才首次告警。同一事件持续跟踪，不拆成多张卡片。你不自行发送卡片或安排处置人员。
>
> 确认结束后，核对该事件的完整事实，再形成最终报告，说明起止、严重度、依据和建议。未结束风险可以持续通报，但不形成最终归档结论。结束时说明处理结果、发生变化的事件、已结束事件和报告；一段监测处理完成不等于风险已经结束。
>
> 当前上游由Main按时间调度，P6每次保存一份窗口描述；依据工具返回的时间顺序和业务状态推进。
>
>
> 当前平台支持可信人工决定关闭作业，闭环检查仅是建议，人工强制关闭不受阻塞项否决；关闭不代表风险恢复。收到JOB_ALREADY_CLOSED或TASK_EXECUTION_REVOKED后停止写入并据实结束。平台超时会回收未提交领取，不重试已成功提交的结果。
>
>
> 一次任务连续消费：从claim_description最早项开始，完成研判和complete_assessment后再次claim_description，持续追到当前最新可用P6_P7描述。不要处理一份就退出，不把一次工具领取误认为一次Agent任务上限。每次仍只领取和提交一份，严格串行，禁止同时持有多个领取或越序。遇no_work/blocked、可信关闭/撤销、无法恢复的错误或接近任务时限/模型调用预算时结束，报告连续处理范围、数量、事件变化和阻塞。预算不足时先提交当前项再退出，不领取下一项；对每次返回的closed_events及时report_closed_event，不等本次全部循环完成。

### `claim_description`

**模型工具描述原文：** 用途：按当前授权作业时间顺序领取P6 ready业务描述；lease_seconds可选30–1800，默认600。前序P6时间窗口未完成时返blocked/reason=earlier_p6_window_unfinished/time_range，由Main按时间恢复或等待，不能跳过。其他blocked返record_id/p7_through；no_work的unreported列出已closed未最终报告事件，可read_event核对后补报。claimed返claim、record.payload.observations、active_events和rules。真正时间断档会持久unknown区间，重置normal_since并产生更新通知；不把断档当恢复。一次处理一个描述，领取后异常fail_description，长分析renew_description_lease。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `lease_seconds` | integer | 否 | 领取或续租秒数，30–1800整数；可选领取参数默认600。；约束：`{"minimum": 30, "maximum": 1800}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "lease_seconds": {
      "type": "integer",
      "minimum": 30,
      "maximum": 1800,
      "description": "领取或续租秒数，30–1800整数；可选领取参数默认600。"
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `complete_assessment`

**模型工具描述原文：** 用途：提交本描述全部异常key的研判与跨窗事件变化。claim原样来自claim_description；assessments=[{key,risk_level,basis,advice}]，无重复且精确覆盖observations含abnormal的key，risk_level为0–5整数，basis说明证据和规则，advice为具体建议字符串数组。全正常/未知传[]仍须提交。工具检查前序p7_through等于窗口起点；按会话/来源/主体/具体风险key保持单活动事件；同风险closed后再次异常新建事件。abnormal延续open、清空恢复计时并保留历史最大等级；unknown置uncertain并重置计时；连续normal置closing，达到该observation.recovery_seconds且本窗口coverage.complete才closed。模型不能指定关闭字段、改变对象/规则或以空异常闭合。事务写事件、描述completed、p7_through、event_notification最新版本，返回record_id/changed_events/closed_events/artifact_ref。同claim+相同assessments幂等，改内容IDEMPOTENCY_CONFLICT。通知保留在途/failed状态，不自动调P8/写卡片意图。closed_events逐个read_event后report_closed_event；开放变化返回Main调P8，不等待最终报告。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `claim` | object | 是 | 原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。；约束：`{"required": ["kind", "id", "token"], "additionalProperties": false}` |
| `assessments` | array | 是 | 每个含异常key一项{key,risk_level,basis,advice}；全正常或未知传[]仍提交。；约束：`{"maxItems": 1000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "claim": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "description": "对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。"
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "description": "领取记录标识，必须来自领取回执；证据引用内id是正整数数据库主键。"
        },
        "token": {
          "type": "string",
          "minLength": 1,
          "description": "有效领取凭证，原样传回，不猜测、不外传。"
        }
      },
      "required": [
        "kind",
        "id",
        "token"
      ],
      "additionalProperties": false,
      "description": "原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。"
    },
    "assessments": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "key": {
            "type": "string",
            "minLength": 1,
            "description": "事实回执中的风险key，原样使用，不能自行创造、合并主体或省略异常。"
          },
          "risk_level": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5,
            "description": "0–5整数，依据可信规则和证据；事件持续更新保留历史最大研判等级，人工处置等级另有受控覆盖。"
          },
          "basis": {
            "type": "string",
            "minLength": 1,
            "maxLength": 6000,
            "description": "真实归属或风险研判依据。Main可使用用户确认的一句归属说明，无需附加文件号或附件。"
          },
          "advice": {
            "type": "array",
            "items": {
              "type": "string",
              "minLength": 1
            },
            "maxItems": 1000,
            "description": "处置建议字符串数组，不能声称建议已执行。"
          }
        },
        "required": [
          "key",
          "risk_level",
          "basis",
          "advice"
        ],
        "additionalProperties": false
      },
      "maxItems": 1000,
      "description": "每个含异常key一项{key,risk_level,basis,advice}；全正常或未知传[]仍提交。"
    }
  },
  "required": [
    "claim",
    "assessments"
  ],
  "additionalProperties": false
}
```

### `report_closed_event`

**模型工具描述原文：** 用途：生成已结束事件的不可变最终归档。event_id取closed_events或no_work.unreported，先read_event核对；summary概括起止、严重度、依据和建议。要求event.state=closed且closure_reason=continuous_normal_evidence，否则EVENT_NOT_CLOSED。稳定report_id与开放阶段处置使用同一标识，payload.is_final=true；同摘要幂等，不同摘要拒绝IDEMPOTENCY_CONFLICT。更新事件report_id与event_notification；已有处置则报告status=completed，否则pending供兼容领取。不是发送回执，不调P8、不发飞书；未结束事件用事件通知持续更新，不调用本工具生成最终报告。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `event_id` | string | 是 | 持久P7事件ID，来自changed_events/closed_events/unreported或实际队列。；约束：`{"minLength": 1}` |
| `summary` | string | 是 | 本次事实/最终事件摘要，限6000字符；只概括证据支持的信息，不用空结果冒充完成。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "event_id": {
      "type": "string",
      "minLength": 1,
      "description": "持久P7事件ID，来自changed_events/closed_events/unreported或实际队列。"
    },
    "summary": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "本次事实/最终事件摘要，限6000字符；只概括证据支持的信息，不用空结果冒充完成。"
    }
  },
  "required": [
    "event_id",
    "summary"
  ],
  "additionalProperties": false
}
```

### `read_event`

**模型工具描述原文：** 用途：按当前作业event_id读取持久事件、证据区间、历史记录引用、状态与报告关联。用于最终报告核对和解释变化，不能读其他作业；查询无副作用，不会确认风险恢复。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `event_id` | string | 是 | 持久P7事件ID，来自changed_events/closed_events/unreported或实际队列。；约束：`{"minLength": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "event_id": {
      "type": "string",
      "minLength": 1,
      "description": "持久P7事件ID，来自changed_events/closed_events/unreported或实际队列。"
    }
  },
  "required": [
    "event_id"
  ],
  "additionalProperties": false
}
```

### `renew_description_lease`

**模型工具描述原文：** 用途：长分析期间续租当前P7 description有效领取，claim原样、lease_seconds30–1800；返回status/expiry。不推进p7_through、不改变事件，不允许他人或已失效领取。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `claim` | object | 是 | 原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。；约束：`{"required": ["kind", "id", "token"], "additionalProperties": false}` |
| `lease_seconds` | integer | 是 | 领取或续租秒数，30–1800整数；可选领取参数默认600。；约束：`{"minimum": 30, "maximum": 1800}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "claim": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "description": "对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。"
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "description": "领取记录标识，必须来自领取回执；证据引用内id是正整数数据库主键。"
        },
        "token": {
          "type": "string",
          "minLength": 1,
          "description": "有效领取凭证，原样传回，不猜测、不外传。"
        }
      },
      "required": [
        "kind",
        "id",
        "token"
      ],
      "additionalProperties": false,
      "description": "原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。"
    },
    "lease_seconds": {
      "type": "integer",
      "minimum": 30,
      "maximum": 1800,
      "description": "领取或续租秒数，30–1800整数；可选领取参数默认600。"
    }
  },
  "required": [
    "claim",
    "lease_seconds"
  ],
  "additionalProperties": false
}
```

### `fail_description`

**模型工具描述原文：** 用途：记录当前P7 description执行失败；claim可原样传；省略时仅回收本run领取，reason为真实原因。本行completed则返completed而不回退；只回收仍归本run或有效token的processing记录，写failed与审计，阻塞后序，等待Main显式retry_failed(kind=description)。失败不能跳过前序或自动无限重试。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `claim` | object | 否 | 原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。；约束：`{"required": ["kind", "id", "token"], "additionalProperties": false}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "claim": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "description": "对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。"
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "description": "领取记录标识，必须来自领取回执；证据引用内id是正整数数据库主键。"
        },
        "token": {
          "type": "string",
          "minLength": 1,
          "description": "有效领取凭证，原样传回，不猜测、不外传。"
        }
      },
      "required": [
        "kind",
        "id",
        "token"
      ],
      "additionalProperties": false,
      "description": "原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "reason"
  ],
  "additionalProperties": false
}
```

## P8（P8/P9合并Agent）

来源：`P_skill/P8/SKILL.md` 与 `tools.json`。

### Skill 提示词完整原文

> ---
> name: p8p9-disposition
> description: 接收风险变化并持续维护告警与处置记录，复核整改材料，提供参考意见，等待授权人员作出最终决定。
> metadata:
>   context-scope: workflow
> ---
>
> 你承担风险告警维护和整改材料复核两项职责，由主协调人员指定本次工作。风险是否仍在发生，与整改处置是否完成，是两个独立状态，始终分开说明。
>
> 接到风险通知时，核对最新风险事实，建立或更新对应处置记录，并通过平台飞书能力同步告警。首次发现即可通知，不必等待风险结束。同一个风险事件始终维护同一张卡片；持续变化、恢复观察和最终结束都更新原卡片，不重复建立告警。
>
> 更新风险信息时保留人员接取、整改材料、已有意见和处置进度，不能因新观察将人工工作清空。缺测时明确表示无法确认恢复。处理记录已保存不等于飞书已送达；根据实际投递回执说明成功、模拟或尚未确认，结果不明时交由平台核对，不盲目重发。
>
> 接到整改复核任务时，先读取本次提交的完整材料，再结合原风险、整改说明和证据判断是否足够，给出理由与限制。材料发生变更时重新复核；不能沿用旧材料结论。无法读取的材料不能仅凭文件名推断内容，明确表示无法判断。保存参考意见后同步卡片。
>
> 模型意见供人工参考，不能代替人员接取、材料提交、审核批准或作业关闭。人员操作以平台已验证的记录为准，不编造用户动作。人工通过整改审核只完成该风险的处置；常规作业闭环检查监测、风险、通知与整改；授权人工明确决定关闭可越过业务阻塞，保留未解决事项。
>
> 结束时说明本次通知或复核结果、对应事件的风险状态、处置进度、实际投递情况及尚待处理的事项。
>
> 当前上游由Main按时间调度，P6每次保存一份窗口描述；依据工具返回的时间顺序和业务状态推进。
>
> 人员权限可指定多人，也可选择 registered，表示 P8/P9 所绑定飞书连接 .env 中全部已登记用户。该模式由工具和回调读取可信目录，不从聊天猜名单、不表示任意人可操作。Main 使用 configure_participants 更新已登记作业权限，不重新登记作业，不代替人工审核。
>
>
> 当前平台支持可信人工决定关闭作业，闭环检查仅是建议，人工强制关闭不受阻塞项否决；关闭不代表风险恢复。收到JOB_ALREADY_CLOSED或TASK_EXECUTION_REVOKED后停止写入并据实结束。平台超时会回收未提交领取，不重试已成功提交的结果。

### `claim_risk_report`

**模型工具描述原文：** 用途：Main安排intake时领取P7最新待处理通知；名称保留兼容，并非只允许closed报告。优先event_notification pending（open/uncertain/closing/closed皆可），无通知再取旧report pending。lease_seconds可选30–1800，默认600；返claim.kind=event_notification或report，均原样传initialize_disposition；no_work如实结束。不会发送飞书、不创建新Agent。P7持续更新同事件会合并到最新版本，processing/failed不被自动改成pending。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `lease_seconds` | integer | 否 | 领取或续租秒数，30–1800整数；可选领取参数默认600。；约束：`{"minimum": 30, "maximum": 1800}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "lease_seconds": {
      "type": "integer",
      "minimum": 30,
      "maximum": 1800,
      "description": "领取或续租秒数，30–1800整数；可选领取参数默认600。"
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `initialize_disposition`

**模型工具描述原文：** 用途：把领取的最新风险同步到单事件处置记录。claim.kind只允许event_notification/report且需当前有效领取；原样使用id/token。事务读取事件当前最新version，稳定report_id与该事件最终报告相同；没有最终报告时建立is_final=false临时视图，不冒充最终归档。创建或更新disposition.event_snapshot/event_version/risk_state，写card_intent pending，保留人工接取、材料版本、模型意见、人工审核、投递回执和人工risk_level_override。同已完成领取返回原记录，重复版本不重复建卡；成功消费通知并返回真实report_id/status/version/delivery。这里status只表示处置存储状态，非发送成功；再用平台feishu_sync_risk_card(report_id)核对真实或模拟回执。不会自行改变人工状态或调度Agent。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `claim` | object | 是 | 原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。；约束：`{"required": ["kind", "id", "token"], "additionalProperties": false}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "claim": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "description": "对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。"
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "description": "领取记录标识，必须来自领取回执；证据引用内id是正整数数据库主键。"
        },
        "token": {
          "type": "string",
          "minLength": 1,
          "description": "有效领取凭证，原样传回，不猜测、不外传。"
        }
      },
      "required": [
        "kind",
        "id",
        "token"
      ],
      "additionalProperties": false,
      "description": "原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。"
    }
  },
  "required": [
    "claim"
  ],
  "additionalProperties": false
}
```

### `read_disposition`

**模型工具描述原文：** 用途：读取当前作业report_id对应处置、最新风险快照、材料/人工状态与平台delivery。report_id必须来自initialize_disposition/真实队列，不猜测。只读，delivery pending/unknown不能称送达，simulated只测试。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `report_id` | string | 是 | 稳定事件处置标识，来自初始化/队列真实回执，同事件开放和最终报告使用同一值。；约束：`{"minLength": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "report_id": {
      "type": "string",
      "minLength": 1,
      "description": "稳定事件处置标识，来自初始化/队列真实回执，同事件开放和最终报告使用同一值。"
    }
  },
  "required": [
    "report_id"
  ],
  "additionalProperties": false
}
```

### `read_review_snapshot`

**模型工具描述原文：** 用途：Main安排review并提供report_id后获取当前整改材料快照。要求disposition.status=materials_in_audit，否则MATERIALS_NOT_READY；返回snapshot_id、material_version、materials、disposition_version和report。快照绑定当前agent_id/run_id，同一次任务依次read_material所有materials，再save_review_opinion；材料更新须重新取快照，不跨任务复用snapshot_id。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `report_id` | string | 是 | 稳定事件处置标识，来自初始化/队列真实回执，同事件开放和最终报告使用同一值。；约束：`{"minLength": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "report_id": {
      "type": "string",
      "minLength": 1,
      "description": "稳定事件处置标识，来自初始化/队列真实回执，同事件开放和最终报告使用同一值。"
    }
  },
  "required": [
    "report_id"
  ],
  "additionalProperties": false
}
```

### `read_material`

**模型工具描述原文：** 用途：读取本任务review_snapshot包含的material_id，校验snapshot.actor/run_id、材料属于快照、路径在共享根目录与SHA256一致；越界/改动拒绝。当前仅text/plain且UTF-8可解码、大小≤32000字节返回content/readable=true，其他返content=null/readable=false和limitation。工具登记本次材料读取；不能把文件名/上传成功当整改证据。当前save_review_opinion遇到任何不可读材料只允许unknown；外部视觉读取器的结果尚不能替代此读取标记，需受控接入后再扩展。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `snapshot_id` | string | 是 | 本Agent本任务生成的审核快照标识，不跨任务复用。；约束：`{"minLength": 1}` |
| `material_id` | string | 是 | 该审核快照materials中的材料ID。；约束：`{"minLength": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "snapshot_id": {
      "type": "string",
      "minLength": 1,
      "description": "本Agent本任务生成的审核快照标识，不跨任务复用。"
    },
    "material_id": {
      "type": "string",
      "minLength": 1,
      "description": "该审核快照materials中的材料ID。"
    }
  },
  "required": [
    "snapshot_id",
    "material_id"
  ],
  "additionalProperties": false
}
```

### `save_review_opinion`

**模型工具描述原文：** 用途：提交本任务当前材料快照的模型参考意见。snapshot_id来自read_review_snapshot；decision=sufficient/insufficient/unknown，reason写逐项依据，limitations传限制列表。要求快照同Agent/任务、处置仍materials_in_audit且material_version一致、所有材料已read_material；未读报READ_MATERIALS_FIRST，版本改变报MATERIAL_VERSION_CONFLICT，任何readable=false而非unknown报UNREADABLE_MATERIAL_REQUIRES_UNKNOWN。相同意见+快照幂等，保存后status=waiting_human_review并写card_intent pending，再平台同步卡片。sufficient不是批准，不写human_review、不关闭事件/作业；人工审批经平台已验证回调。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `snapshot_id` | string | 是 | 本Agent本任务生成的审核快照标识，不跨任务复用。；约束：`{"minLength": 1}` |
| `decision` | 按Schema | 是 | sufficient/insufficient/unknown，表示模型参考意见；不可读材料只能unknown，不表示人工批准。；约束：`{"enum": ["sufficient", "insufficient", "unknown"]}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |
| `limitations` | array | 是 | 事实/材料缺测、不可读及证据范围等限制字符串列表，没有额外限制传[]。；约束：`{"maxItems": 1000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "snapshot_id": {
      "type": "string",
      "minLength": 1,
      "description": "本Agent本任务生成的审核快照标识，不跨任务复用。"
    },
    "decision": {
      "enum": [
        "sufficient",
        "insufficient",
        "unknown"
      ],
      "description": "sufficient/insufficient/unknown，表示模型参考意见；不可读材料只能unknown，不表示人工批准。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    },
    "limitations": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      },
      "maxItems": 1000,
      "description": "事实/材料缺测、不可读及证据范围等限制字符串列表，没有额外限制传[]。"
    }
  },
  "required": [
    "snapshot_id",
    "decision",
    "reason",
    "limitations"
  ],
  "additionalProperties": false
}
```

### `verify_job_closable`

**模型工具描述原文：** 用途：只读检查当前作业闭环屏障，参数{}。要求monitoring_closed且cutoff=p7_through、所有window/description/report/event_notification completed、所有事件closed并有is_final=true报告、所有处置closed并delivered（simulated仅test_mode=true可接受）。返回closable/blockers/job_status。closable=true也只是可供人工确认，不执行关闭、不绕过授权审核；播放结束或模型sufficient不能满足全作业关闭。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| 无参数 | object | — | 传 `{}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

### `fail_risk_report`

**模型工具描述原文：** 用途：登记intake领取失败，claim可省略按本run回收；提供时kind只允许report/event_notification，reason为真实错误。completed不回退；有效领取写failed+审计待Main显式retry_failed对应kind，不自动无限重试，不重置已保存处置，不处理unknown飞书发送。 工程上下文：agent_id、run_id、source_id/job_id与共享SQLite路径由平台可信绑定注入，不是模型参数；只能操作当前授权范围。业务工具不会自行创建Agent或调度下一阶段，租约凭证不得向用户输出。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `claim` | object | 否 | 原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。；约束：`{"required": ["kind", "id", "token"], "additionalProperties": false}` |
| `reason` | string | 是 | 实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。；约束：`{"minLength": 1, "maxLength": 6000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "claim": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "minLength": 1,
          "description": "对象类型；在claim中必须原样使用领取类型，在retry_failed中按工具枚举选择。"
        },
        "id": {
          "type": "string",
          "minLength": 1,
          "description": "领取记录标识，必须来自领取回执；证据引用内id是正整数数据库主键。"
        },
        "token": {
          "type": "string",
          "minLength": 1,
          "description": "有效领取凭证，原样传回，不猜测、不外传。"
        }
      },
      "required": [
        "kind",
        "id",
        "token"
      ],
      "additionalProperties": false,
      "description": "原样使用领取工具返回的kind/id/token；平台和业务侧校验角色、归属、期限，不复制到用户回复。"
    },
    "reason": {
      "type": "string",
      "minLength": 1,
      "maxLength": 6000,
      "description": "实际失败、恢复、监测结束或复核理由；操作审计字段，不填写推测成功或敏感凭据。"
    }
  },
  "required": [
    "reason"
  ],
  "additionalProperties": false
}
```

## P10（复用原报告Skill）

来源：`a_mac_skill/P10/SKILL.md`、`a_mac_skill/P10/tools.py`。P_skill/P10仅记录接入方案，没有新开发一份SKILL.md。

### Skill提示词完整原文

> ---
> name: work-ticket-report
> description: 根据当前作业票与监测、研判和整改记录生成可追溯的作业总结，供负责人了解进展、风险和未完成事项。
> ---
>
> 你是作业总结撰写人员。只总结本次绑定的作业票与提供的事实快照，核对票证、流程状态和报告模板要求。没有真实票证时说明缺失，不能拿示例或其他作业代替。不启动监测、不改变作业状态，也不发送消息。
>
> 监测发现、风险研判和处置记录分别表达，不能将不同阶段的记录数量直接混为同一件事。统计使用服务已经核算的结果，不自行计算检出率、响应时长等未核验指标。建议、人工决定和实际执行结果分别说明，不能把建议写成已落实。
>
> 记录中没有可用证据时写明无法核验，不把缺失解释为无风险；尚在进行的风险和未完成的整改应如实保留。前期阶段尚未提供时不虚构章节；模拟数据明确标注。只有规则判断而没有模型研判时，也要说明这一限制。
>
> 报告面向作业负责人。用姓名、职位和日常业务表达描述接取、整改、提交、审核和关闭过程；姓名、职位未记录时不猜测。中文时间使用北京时间，时间来源不明时说明限制。简洁概括已记录事实，不重复表格，也不扩展证据之外的因果结论。
>
> 每项摘要和建议都应能追溯到本次资料，使用工具提供的真实来源，不自造引用。这里提供的是监测记录，不是视频观看结果；不能声称已检查视频。依据报告工具要求交付内容，文档生成和落盘由原报告服务完成，报告生成与人工验收分别说明。

下面四个工具属于原P10报告生成模型，在原报告服务内部构建；不等同于平台P10 Agent直接拥有四个同名工具。平台侧通过已绑定HTTP提供者的动态cap_*__call工具提交/查询export_id，确切目录取决于当前绑定。HTTP /submit与/status均接受且只接受export_id，管理员负责对应真实作业入口及鉴权。

### `p10_query_monitor_events`

**模型工具描述原文：** 查询绑定作业的P6监测记录。job_id必须逐字等于本次snapshot.job_id，否则INVALID_JOB_ID；offset默认0且>=0，limit默认100且1–500。返回items/source_status/total/next_offset/warnings，各项source_ref只可原样引用；next_offset为null结束。统计使用服务计算，不通过抽样重新算总数；没有记录不等于没有风险。本工具只读、不启动采集、不获取媒体，不能声称已观看视频。缺测、模拟或规则降级要保留。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `job_id` | string | 是 | 业务作业标识；新绑定时与作业票号码相同，用于隔离业务描述、事件和处置。 |
| `offset` | integer | 否 | 分页起始偏移。；约束：`{"default": 0, "minimum": 0}` |
| `limit` | integer | 否 | 本页最大记录数量。；约束：`{"default": 100, "minimum": 1, "maximum": 500}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "job_id": {
      "type": "string"
    },
    "offset": {
      "type": "integer",
      "default": 0,
      "minimum": 0
    },
    "limit": {
      "type": "integer",
      "default": 100,
      "minimum": 1,
      "maximum": 500
    }
  },
  "required": [
    "job_id"
  ]
}
```

### `p10_query_risk_assessments`

**模型工具描述原文：** 查询本次快照P7研判记录。job_id必须等于snapshot.job_id；risk_level省略或null返回全部（含0级），否则仅0–5整数；offset默认0且>=0，limit默认100且1–500。返回items/total/next_offset/source_status与真实来源。P6候选/P7研判/P8事件不是同一集合，不推断数量或ID映射；reasoning若声明LLM不可用必须说明规则降级。风险建议不是已执行处置，分页按next_offset直到null。不得越过本次不可变快照。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `job_id` | string | 是 | 业务作业标识；新绑定时与作业票号码相同，用于隔离业务描述、事件和处置。 |
| `risk_level` | ['integer', 'null'] | 否 | 风险等级过滤；null为全部。；约束：`{"default": null, "minimum": 0, "maximum": 5}` |
| `offset` | integer | 否 | 分页起始偏移。；约束：`{"default": 0, "minimum": 0}` |
| `limit` | integer | 否 | 本页最大记录数量。；约束：`{"default": 100, "minimum": 1, "maximum": 500}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "job_id": {
      "type": "string"
    },
    "risk_level": {
      "type": [
        "integer",
        "null"
      ],
      "default": null,
      "minimum": 0,
      "maximum": 5
    },
    "offset": {
      "type": "integer",
      "default": 0,
      "minimum": 0
    },
    "limit": {
      "type": "integer",
      "default": 100,
      "minimum": 1,
      "maximum": 500
    }
  },
  "required": [
    "job_id"
  ]
}
```

### `p10_query_closure_state`

**模型工具描述原文：** 查询绑定job_id的真实work_permit/permit_source、P8处置、P9意见与人工决定，只读。job_id必须等于本次快照。state是投影视图，不是原文件逐字段映射；只能逐字复制allowed_source_refs，不得凭汇总字段拼接JSON指针。汇总可引用closure_state.json#，缺失state只能说明无法核验。submission_count等不等于原文件字段。意见、人工决定、实际关闭状态分别表达，不能将模型sufficient视作批准；作业仍进行中保留未完成项，不从播放器结束推断闭环。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `job_id` | string | 是 | 业务作业标识；新绑定时与作业票号码相同，用于隔离业务描述、事件和处置。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "job_id": {
      "type": "string"
    }
  },
  "required": [
    "job_id"
  ]
}
```

### `p10_get_template_contract`

**模型工具描述原文：** 查询本次绑定Word模板契约。template_id默认work-ticket-report-v1且必须等于template_contract.template_id，否则TEMPLATE_NOT_FOUND。返回版本、章节、必填与空值规则；Word统计/渲染/校验/落盘由原服务完成，不重写工具。模型最终输出严格NarrativeContent JSON对象，不带Markdown围栏：overview_summary、monitor_summary、risk_summary、disposition_summary、closure_summary、overall_summary均为{text,source_refs}，recommendations为[{text,basis_refs}]最多15项，不允许额外字段；摘要业务约定每项220字以内（schema上限500字符），建议schema上限1500字符。引用逐字选自本次allowed_refs/allowed_source_refs/source_ref/permit_source，不自造路径。未给P1-P5不生成其章节；模拟标记保留；不自行计算百分比/帧密度/响应时长。输出业务语言、真实姓名/职位（未知则说明）、北京时间（时区未知则说明限制），不写人员代码或内部状态码。返回本地报告路径而非下载链接，不额外要求人工确认生成，但生成不等于人工验收。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `template_id` | string | 否 | 报告模板标识。；约束：`{"default": "work-ticket-report-v1"}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "template_id": {
      "type": "string",
      "default": "work-ticket-report-v1"
    }
  },
  "required": []
}
```

上述P10参数Schema依据函数签名整理，不是声称从当前线上模型请求抓取。代码另校验job_id必须等于快照绑定作业、模板必须等于本次绑定模板。

## 平台多智能体协作工具

来源：`platform_core/contracts.py`。有方向权限：submit/status/result/cancel/overview/schedule。wait_tasks需要可用status与result授权；schedule独立授权，不由submit隐式授予。并发是按Agent配置；wait_tasks持久化并释放当前执行资源，其他Agent可继续运行。模型单次请求parallel_tool_calls=False不代表整个平台串行。

### `list_tasks`

**模型工具描述原文：** 查询统一协作任务表：返回本次协作链中获授权任务的关系、依赖、状态和阶段；具有成果权限时同时返回输出文件位置。 参数{}。返回当前协作链及同一作业下自己此前提交的获授权任务，含root/parent/dependencies/status/phase，最多最近100项；只有result授权才返回其他Agent成果位置，不返回私有会话。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| 无参数 | object | — | 传 `{}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

### `submit_task`

**模型工具描述原文：** 向预定义且获授权的 Agent 提交子任务，返回任务 ID；不会创建 Agent。 agent_id取业务状态回执或已配置Agent ID；description为本次任务要求，input为目标input_schema认可的对象。P6通常input={}自动领取最早可处理窗口；需要指定窗口时仅传input={start_time:实际窗口开始时间}，窗口大小由Main的configure_schedule设置；不传原始记录ID；P7通常input={}；P8/P9 input={mode:intake}或{mode:review,report_id:真实ID}。需submit有方向授权，目标Agent须启用。depends_on只使用获授权前置任务ID；artifact_ids/artifact_inputs仅传登记且有访问权限的成果引用。request_id可选，省略由本run_id+参数生成稳定幂等键，同键不同内容拒绝；有意新任务使用新键。不传session_id/job_id改变受信业务上下文。返回已提交ID不是成功；需结果调用wait_tasks或终态后get_task_result。独立Agent按各自并发上限执行，提交后不会阻塞整个系统。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `timeout_seconds` | integer | 否 | 单轮执行超时秒数，不超过目标Agent上限；超时失败并回收未提交领取。；约束：`{"minimum": 1}` |
| `agent_id` | string | 是 | 已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。 |
| `description` | string | 是 | 本次子任务或周期任务的具体工作要求，不将描述文字当作结构化input参数。 |
| `input` | object | 否 | 目标Agent input_schema认可的结构化对象；P6传{}或start_time，P7通常传{}，P8/P9传mode及review所需report_id。；约束：`{"additionalProperties": true}` |
| `artifact_ids` | array | 否 | 已登记且可访问的成果引用ID数组，最多100项，不传任意主机路径。；约束：`{"maxItems": 100}` |
| `artifact_inputs` | object | 否 | 目标输入字段名到成果引用ID的映射，供可信成果传递。；约束：`{"additionalProperties": {"type": "string"}}` |
| `depends_on` | array | 否 | 前置任务ID数组，取实际回执；依赖未完成不开始后续，不伪造ID。 |
| `request_id` | string | 否 | 幂等请求标识；同键同内容重放，内容改变或有意创建新任务须新键；心跳创建必填。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "timeout_seconds": {
      "type": "integer",
      "minimum": 1,
      "description": "单轮执行超时秒数，不超过目标Agent上限；超时失败并回收未提交领取。"
    },
    "agent_id": {
      "type": "string",
      "description": "已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。"
    },
    "description": {
      "type": "string",
      "description": "本次子任务或周期任务的具体工作要求，不将描述文字当作结构化input参数。"
    },
    "input": {
      "type": "object",
      "properties": {},
      "additionalProperties": true,
      "description": "目标Agent input_schema认可的结构化对象；P6传{}或start_time，P7通常传{}，P8/P9传mode及review所需report_id。"
    },
    "artifact_ids": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "maxItems": 100,
      "description": "已登记且可访问的成果引用ID数组，最多100项，不传任意主机路径。"
    },
    "artifact_inputs": {
      "type": "object",
      "additionalProperties": {
        "type": "string"
      },
      "description": "目标输入字段名到成果引用ID的映射，供可信成果传递。"
    },
    "depends_on": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "前置任务ID数组，取实际回执；依赖未完成不开始后续，不伪造ID。"
    },
    "request_id": {
      "type": "string",
      "description": "幂等请求标识；同键同内容重放，内容改变或有意创建新任务须新键；心跳创建必填。"
    }
  },
  "required": [
    "agent_id",
    "description"
  ],
  "additionalProperties": false
}
```

### `get_task_status`

**模型工具描述原文：** 读取授权范围内的任务状态与阶段，不返回私有上下文。 task_id取真实提交回执，需status权限且属于当前协作链或同票此前自己提交的任务。只返回task_id/status/phase/executor_agent_id/error；queued/running/waiting不是完成。需要等待时优先wait_tasks释放当前执行资源，不用模型反复轮询。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `task_id` | string | 是 | 当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "description": "当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。"
    }
  },
  "required": [
    "task_id"
  ],
  "additionalProperties": false
}
```

### `get_task_result`

**模型工具描述原文：** 读取已完成任务的结构化成果；失败时返回真实错误。 task_id取真实回执，需result权限且属于当前协作链或同票此前自己提交的任务。任务未终态报TASK_NOT_FINISHED；终态返回真实result/error/artifacts，failed/cancelled不能写成成功。不能读取其他协作链的历史任务或私有聊天上下文。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `task_id` | string | 是 | 当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "description": "当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。"
    }
  },
  "required": [
    "task_id"
  ],
  "additionalProperties": false
}
```

### `wait_tasks`

**模型工具描述原文：** 等待本次协作中的已提交任务，保存上下文并释放执行资源；完成后自动恢复。 task_ids必须非空且为当前协作链已提交任务的真实ID；每个目标同时需status和result权限。未全部终态返waiting并由运行时保存工具调用位置、释放当前执行资源，恢复后从此处继续；不是阻塞全平台或禁止其他Agent运行。全部终态后返回任务结果，逐项核对失败。等待不创建计划、不调度下一阶段、不猜测成果。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `task_ids` | array | 是 | 非空当前协作链任务ID数组，每项需status和result权限。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "task_ids": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "非空当前协作链任务ID数组，每项需status和result权限。"
    }
  },
  "required": [
    "task_ids"
  ],
  "additionalProperties": false
}
```

### `cancel_task`

**模型工具描述原文：** 请求取消授权任务；不能撤销已经发生的外部副作用。 task_id为当前协作链可访问任务，需cancel权限。仅请求取消，运行中的外部调用/已落库事实/已发卡片不可撤销；核对实际任务状态，不把取消请求回执当副作用回滚。停止未来心跳需control_heartbeat_schedule，不使用本工具替代。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `task_id` | string | 是 | 当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "task_id": {
      "type": "string",
      "description": "当前协作链真实平台任务ID，非mock整数source_task_id，非schedule_id。"
    }
  },
  "required": [
    "task_id"
  ],
  "additionalProperties": false
}
```

### `agent_status`

**模型工具描述原文：** 查看已授权 Agent 的可接受任务情况、并发上限及运行数量。 agent_id取已配置目标，需overview权限。返回可接受任务情况、并发上限、运行/队列概况；只读，不创建Agent，不授予权限，不表示所有Agent只能串行。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `agent_id` | string | 是 | 已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "agent_id": {
      "type": "string",
      "description": "已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。"
    }
  },
  "required": [
    "agent_id"
  ],
  "additionalProperties": false
}
```

### `set_heartbeat_schedule`

**模型工具描述原文：** 创建或更新独立持久心跳计划，返回计划ID；主任务结束后平台继续触发。需要独立schedule授权。更新须提供当前version及新的request_id。 创建需agent_id/request_id/enabled/interval_seconds/description/input，目标需独立schedule授权，启用时校验目标input_schema。interval_seconds是H=1–86400整数秒，P6数据窗口N由Main业务工具configure_schedule设置，P6的input使用{}或start_time。first_run默认after_interval，可immediate；overlap_policy当前仅skip，overlap_scope默认self，只检查目标Agent自身所有在途执行（含手工任务），子Agent不阻塞下一轮；chain才检查同计划/overlap_key的整条协作链。每轮独立对话，作业范围绑定创建时当前票，关闭后停止，不跟随新票。skip跳过触发，不删除未消费业务数据；执行慢于H会产生消费积压，查询业务工具判断。更新必须schedule_id+当前version+新的request_id，目标不可换，所有者不得越权；同request_id不同内容拒绝SCHEDULE_IDEMPOTENCY_CONFLICT。不提供schedule_id是新建，不自动寻找或去重旧计划，先get_heartbeat_schedule检查复用。返回持久计划ID，主任务结束后继续运行；只有用户授权持续运行才enabled=true。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `agent_id` | string | 是 | 已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。 |
| `schedule_id` | string | 否 | 自己创建的持久计划ID；创建时省略，更新/控制取真实回执。 |
| `version` | integer | 否 | 更新计划时提供当前版本；不是事件版本、任务次数或材料版本。；约束：`{"minimum": 1}` |
| `request_id` | string | 是 | 幂等请求标识；同键同内容重放，内容改变或有意创建新任务须新键；心跳创建必填。；约束：`{"minLength": 1, "maxLength": 128}` |
| `enabled` | boolean | 是 | 是否启用持续计划；没有用户持续运行授权不能自行置true。 |
| `interval_seconds` | integer | 是 | 调度周期H，单位秒，1–86400整数；不是P6消费窗口N。；约束：`{"minimum": 1, "maximum": 86400}` |
| `description` | string | 是 | 本次子任务或周期任务的具体工作要求，不将描述文字当作结构化input参数。；约束：`{"minLength": 1, "maxLength": 10000}` |
| `input` | object | 是 | 目标Agent input_schema认可的结构化对象；P6传{}或start_time，P7通常传{}，P8/P9传mode及review所需report_id。；约束：`{"additionalProperties": true}` |
| `first_run` | string | 否 | immediate立即首次触发；after_interval一个H之后首次触发，默认后者。；约束：`{"enum": ["immediate", "after_interval"]}` |
| `timeout_seconds` | integer | 否 | 单轮执行超时秒数，不超过目标Agent上限；超时失败并回收未提交领取。；约束：`{"minimum": 1}` |
| `overlap_scope` | string | 否 | self仅对目标Agent本身防重入，子Agent继续运行不阻塞；chain对整个协作链防重入。每轮独立会话。；约束：`{"enum": ["self", "chain"]}` |
| `overlap_policy` | string | 否 | 当前只支持skip，有在途任务时跳过本次触发，不丢弃待消费数据。；约束：`{"enum": ["skip"]}` |
| `overlap_key` | string | 否 | 可选共享防重叠分组，最多128字符；留空按目标Agent互斥。；约束：`{"maxLength": 128}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "agent_id": {
      "type": "string",
      "description": "已配置、获当前有方向授权的目标Agent ID，不是名称，不创建Agent。"
    },
    "schedule_id": {
      "type": "string",
      "description": "自己创建的持久计划ID；创建时省略，更新/控制取真实回执。"
    },
    "version": {
      "type": "integer",
      "minimum": 1,
      "description": "更新计划时提供当前版本；不是事件版本、任务次数或材料版本。"
    },
    "request_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 128,
      "description": "幂等请求标识；同键同内容重放，内容改变或有意创建新任务须新键；心跳创建必填。"
    },
    "enabled": {
      "type": "boolean",
      "description": "是否启用持续计划；没有用户持续运行授权不能自行置true。"
    },
    "interval_seconds": {
      "type": "integer",
      "minimum": 1,
      "maximum": 86400,
      "description": "调度周期H，单位秒，1–86400整数；不是P6消费窗口N。"
    },
    "description": {
      "type": "string",
      "minLength": 1,
      "maxLength": 10000,
      "description": "本次子任务或周期任务的具体工作要求，不将描述文字当作结构化input参数。"
    },
    "input": {
      "type": "object",
      "properties": {},
      "additionalProperties": true,
      "description": "目标Agent input_schema认可的结构化对象；P6传{}或start_time，P7通常传{}，P8/P9传mode及review所需report_id。"
    },
    "first_run": {
      "type": "string",
      "enum": [
        "immediate",
        "after_interval"
      ],
      "description": "immediate立即首次触发；after_interval一个H之后首次触发，默认后者。"
    },
    "timeout_seconds": {
      "type": "integer",
      "minimum": 1,
      "description": "单轮执行超时秒数，不超过目标Agent上限；超时失败并回收未提交领取。"
    },
    "overlap_scope": {
      "type": "string",
      "enum": [
        "self",
        "chain"
      ],
      "description": "self仅对目标Agent本身防重入，子Agent继续运行不阻塞；chain对整个协作链防重入。每轮独立会话。"
    },
    "overlap_policy": {
      "type": "string",
      "enum": [
        "skip"
      ],
      "description": "当前只支持skip，有在途任务时跳过本次触发，不丢弃待消费数据。"
    },
    "overlap_key": {
      "type": "string",
      "maxLength": 128,
      "description": "可选共享防重叠分组，最多128字符；留空按目标Agent互斥。"
    }
  },
  "required": [
    "agent_id",
    "request_id",
    "enabled",
    "interval_seconds",
    "description",
    "input"
  ],
  "additionalProperties": false
}
```

### `get_heartbeat_schedule`

**模型工具描述原文：** 查询自己创建且当前仍有schedule权限的计划及最近执行；读取成果另需result授权。省略计划ID列出自己的计划，不访问其他协作链。 schedule_id可省略，省略列出自己创建且仍有schedule权限的计划；提供时取真实计划ID，不传任务ID。返回状态、版本、配置与最近运行，只有result权限才含成果；不会读业务游标，business_backlog未测需查询业务工具。不能查看其他所有者计划，也不自动启用/更新计划。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `schedule_id` | string | 否 | 自己创建的持久计划ID；创建时省略，更新/控制取真实回执。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "schedule_id": {
      "type": "string",
      "description": "自己创建的持久计划ID；创建时省略，更新/控制取真实回执。"
    }
  },
  "additionalProperties": false
}
```

### `control_heartbeat_schedule`

**模型工具描述原文：** 暂停、恢复或停止自己创建的心跳计划，仅影响未来触发；不会取消在途任务。参数为计划ID，不能传任务ID。 schedule_id是真实计划ID，action为pause/resume/stop。必须是自己的计划且当前仍有schedule权限；pause暂停未来触发，resume重新校验授权与配置后恢复，stop停止未来触发；当前代码允许对已停止计划显式resume并重新校验授权。都不取消在途任务、不关闭监测/风险/作业；业务监测结束另调用close_time_monitoring，明确要取消在途任务另需cancel授权。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `schedule_id` | string | 是 | 自己创建的持久计划ID；创建时省略，更新/控制取真实回执。 |
| `action` | string | 是 | pause暂停、resume恢复或stop停止计划，仅影响未来触发，不取消在途任务。；约束：`{"enum": ["pause", "resume", "stop"]}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "schedule_id": {
      "type": "string",
      "description": "自己创建的持久计划ID；创建时省略，更新/控制取真实回执。"
    },
    "action": {
      "type": "string",
      "enum": [
        "pause",
        "resume",
        "stop"
      ],
      "description": "pause暂停、resume恢复或stop停止计划，仅影响未来触发，不取消在途任务。"
    }
  },
  "required": [
    "schedule_id",
    "action"
  ],
  "additionalProperties": false
}
```

## 平台飞书能力（包含单风险卡片）

来源：platform_core/feishu.py。根据当前连接绑定生成目录，账号/收件范围/模板由平台控制，Skill不声明发送实现。

### `lookup_feishu_directory`

**模型工具描述原文：** 在当前飞书连接配置的群/用户目录中查询名称或 ID，不发送消息。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `entity_type` | 按Schema | 是 | 见下方完整Schema及对应工具实现。；约束：`{"enum": ["group", "user"]}` |
| `name` | string | 否 | 见下方完整Schema及对应工具实现。 |
| `id` | string | 否 | 见下方完整Schema及对应工具实现。 |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "entity_type": {
      "enum": [
        "group",
        "user"
      ]
    },
    "name": {
      "type": "string"
    },
    "id": {
      "type": "string"
    }
  },
  "required": [
    "entity_type"
  ],
  "additionalProperties": false
}
```

### `feishu_send`

**模型工具描述原文：** 发送一条飞书文本消息。target.type 为 group/user，填写 name 或 id；账号由平台绑定，不填机器人 App ID。失败或结果未知时不要盲目重发。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `target` | object | 是 | 见下方完整Schema及对应工具实现。；约束：`{"required": ["type"], "additionalProperties": false}` |
| `text` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 10000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "target": {
      "type": "object",
      "properties": {
        "type": {
          "enum": [
            "group",
            "user"
          ]
        },
        "name": {
          "type": "string",
          "minLength": 1
        },
        "id": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "type"
      ],
      "additionalProperties": false
    },
    "text": {
      "type": "string",
      "minLength": 1,
      "maxLength": 10000
    }
  },
  "required": [
    "target",
    "text"
  ],
  "additionalProperties": false
}
```

### `feishu_events`

**模型工具描述原文：** 读取当前飞书连接的已验证入站事件，after 为序号游标，不自动确认处理。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `after` | integer | 否 | 见下方完整Schema及对应工具实现。；约束：`{"minimum": 0}` |
| `limit` | integer | 否 | 本页最大记录数量。；约束：`{"minimum": 1, "maximum": 100}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "after": {
      "type": "integer",
      "minimum": 0
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `feishu_reply`

**模型工具描述原文：** 回复当前连接中的入站消息。使用事件 event_id；成功返回实际消息回执后再 ACK。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `event_id` | string | 是 | P7持久风险事件ID。；约束：`{"minLength": 1}` |
| `text` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 10000}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "event_id": {
      "type": "string",
      "minLength": 1
    },
    "text": {
      "type": "string",
      "minLength": 1,
      "maxLength": 10000
    }
  },
  "required": [
    "event_id",
    "text"
  ],
  "additionalProperties": false
}
```

### `feishu_ack`

**模型工具描述原文：** 确认入站事件已处理或忽略。processed 需要该事件已有成功回复回执。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `event_id` | string | 是 | P7持久风险事件ID。；约束：`{"minLength": 1}` |
| `status` | 按Schema | 是 | 见下方完整Schema及对应工具实现。；约束：`{"enum": ["processed", "ignored"]}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "event_id": {
      "type": "string",
      "minLength": 1
    },
    "status": {
      "enum": [
        "processed",
        "ignored"
      ]
    }
  },
  "required": [
    "event_id",
    "status"
  ],
  "additionalProperties": false
}
```

### `feishu_health`

**模型工具描述原文：** 只读检查当前飞书连接的凭据和飞书 API 连通性，不发送测试消息。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| 无参数 | object | — | 传 `{}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

### `feishu_sync_risk_card`

**模型工具描述原文：** 平台飞书能力：只同步已落库单风险处置的最新卡片，不允许模型构造卡片JSON或选择机器人/收件人。report_id必须来自initialize_disposition或真实队列；首次创建，后续同report_id更新同一CardKit实体/消息，开放、未知、恢复中、结束均可更新。作业范围、收件目标、模板、uuid/sequence、消息关联、签名回调及附件页由平台控制，保持已验证协议；业务Skill不实现发送工具。delivery=delivered才是真实成功，simulated仅测试，pending不是送达；unknown需平台核对，不能强制重发初始告警或改ID绕过。无有效平台绑定/连接/业务范围则拒绝，不回退文本告警。风险状态与处置状态独立显示；更新保留人工操作、材料与审核，不代表人工批准或作业关闭。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `report_id` | string | 是 | 同一事件的稳定处置report_id，来自初始化/实际队列；不传event_id、任务ID或自行编造的新ID。；约束：`{"minLength": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "report_id": {
      "type": "string",
      "minLength": 1,
      "description": "同一事件的稳定处置report_id，来自初始化/实际队列；不传event_id、任务ID或自行编造的新ID。"
    }
  },
  "required": [
    "report_id"
  ],
  "additionalProperties": false
}
```

## 平台成果引用工具

来源：platform_core/artifacts.py；不用于读取任意业务文件。

### `register_artifact`

**模型工具描述原文：** 登记本任务已由业务工具保存的成果引用；保存不可变声明，不读取业务文件。路径必须是相对路径。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `provider` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |
| `record_ref` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |
| `relative_path` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |
| `content_type` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |
| `sha256` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |
| `title` | string | 否 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |
| `version` | integer | 是 | 更新计划时提供当前版本，用于并发冲突校验。；约束：`{"minimum": 1}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "provider": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "record_ref": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "relative_path": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "content_type": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "sha256": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "title": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    },
    "version": {
      "type": "integer",
      "minimum": 1
    }
  },
  "required": [
    "provider",
    "record_ref",
    "relative_path",
    "content_type",
    "sha256",
    "version"
  ],
  "additionalProperties": false
}
```

### `get_artifact`

**模型工具描述原文：** 读取本任务、授权后代或上游显式传入的成果声明；业务内容通过声明对应的已授权工具读取。

| 参数 | 类型 | 必填 | 业务含义/原字段描述 |
|---|---|---|---|
| `artifact_id` | string | 是 | 见下方完整Schema及对应工具实现。；约束：`{"minLength": 1, "maxLength": 500}` |

**模型参数 Schema 原文：**

```json
{
  "type": "object",
  "properties": {
    "artifact_id": {
      "type": "string",
      "minLength": 1,
      "maxLength": 500
    }
  },
  "required": [
    "artifact_id"
  ],
  "additionalProperties": false
}
```

## 补充：Skill读取、平台成果与运行边界

`read_skill`：描述原文“按任务需要读取已绑定 Skill 的正文或参考资料，不执行脚本。”参数skill_id必填且限定当前Agent已启用Skill ID；path可选，省略为SKILL.md，只能使用包内相对路径。官方Skill middleware另有受控read_file能力；不可据此读取其他Agent私有Skill或任意主机文件。

平台成果工具支持登记与读取授权成果引用，便于子任务传递，不代表任意文件访问。平台通用执行提示要求通过wait_tasks等待子任务、不得猜测结果或反复查询。会话属于各自Agent，子任务成果共享遵循协作权限，不共享完整私有聊天上下文。

本轮SKILL.md改为业务正文，工程约束进入工具描述/参数description；完整维护分工与限制见PROMPT_TOOL_SEPARATION.md。本说明不是操作授权，不启心跳、不绑定新作业、不发送真实飞书消息，也不重写P10。

## 源码核对索引

- `P_skill/main/SKILL.md`：SHA256 `deb6dc5a3734145d084e300abe1f99ba85f3d8a6ee27fc3ec9d1492ec9e28e20`
- `P_skill/main/tools.json`：SHA256 `22ae405fb088b4e2609274f56d67fdf24ae07d5dbe295a17ba9c050c223f650f`
- `P_skill/runtime/industrial_skills/work_permits.py`：SHA256 `aa4766da9a5f36f720882bede78d7323f7d176bff2c6c6a72e15a22a307ed002`
- `skill_decoupling‌/platform_core/contracts.py`：SHA256 `6d39c7c54bf4d2a6ee3e4fd55b2e9bf91e75cc221b5a40c3d99468b939109685`
- `skill_decoupling‌/platform_core/agents.py`：SHA256 `69bd2414bec1addd309f622408249900dea902ee232dc5655b37d49c2ada0184`
- `a_mac_skill/P10/tools.py`：SHA256 `4da056e59434022a0eadc0827c4863ef4c21eb01b70f88c826ec51f5bb875b63`
