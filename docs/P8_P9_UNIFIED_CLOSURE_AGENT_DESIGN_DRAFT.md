# P8/P9 处置闭环智能体开发设计草案（待开发）

> 状态：设计草案，尚未实现。
>
> 本文用于讨论 P8/P9 后续改造，不替代或修改现有 `P8.md`、`P9.md`、`FEISHU_API.md` 等当前实现文档。本文中的接口、状态和路径均为建议方案，开发完成并验证后才能升级为正式契约。

## 1. 背景与设计结论

当前 P8 负责风险通知、人工决策和处置任务状态，P9 负责闭环检查、报告及关闭确认。实际用户处理风险时，整改提交、材料补充、复核驳回和再次整改是一段连续对话，若拆成两个相互独立的智能体，会产生上下文交接、状态冲突和重复询问。

本方案采用：

- 一个面向用户的“处置闭环智能体”。
- P8、P9 仍保留为两个业务子流程和两个审计阶段。
- P8 负责处置任务执行，最远只能推进到“整改材料已提交”。
- P9 负责材料充分性、整改合规性、人工复核、事件关闭、作业报告和 `job_id` 关闭。
- 用户只看到一个助手和一条连续进度，不感知智能体切换。
- 状态变更由确定性状态机、规则和权限控制；大模型只负责理解、归纳、解释和引导，不能自行绕过关闭条件。

目标流程：

```text
P7 风险事件
  → P8 创建处置任务并通知
  → 责任人接收、整改、提交材料
  → P9 检查材料充分性与整改合规性
      ├─ 材料不足 → 返回 P8 补充材料
      ├─ 整改不合规 → 返回 P8 重新整改
      ├─ 无法自动判断 → 等待人工复核
      └─ 满足条件 → 等待事件关闭确认
  → 关闭全部风险事件
  → 生成作业过程报告
  → 校验 job 关闭条件
  → 有权限人员确认关闭 job_id
  → P10 归档与复盘
```

## 2. 范围与职责边界

### 2.1 P8 处置执行子流程

P8 负责：

- 将 P7 风险事件转换为处置任务。
- 确定责任角色、通知渠道、处置时限和处置要求。
- 接收任务知悉、任务接收、暂停建议、升级、延期申请。
- 接收整改说明、图片、视频、检测记录和其他材料。
- 记录材料版本并发起 P9 检查。
- 收到 P9 驳回后恢复整改或材料补充状态。

P8 不负责：

- 不判断风险事件已经闭环。
- 不关闭风险事件。
- 不关闭整个 `job_id`。
- 不把“处置材料已提交”命名为 `completed` 或业务意义上的 `archived`。

### 2.2 P9 闭环复核子流程

P9 负责：

- 检查整改材料是否齐全、有效、与风险和措施对应。
- 运行确定性闭环规则并解释阻断原因。
- 发起人工复核，接收通过或驳回意见。
- 驳回时生成结构化补充清单并返回 P8。
- 经授权关闭单个风险事件。
- 汇总全部事件、证据、处置和审计记录，生成作业报告。
- 检查整个 `job_id` 是否满足关闭条件。
- 经最终人工确认关闭 `job_id`，随后触发 P10。

### 2.3 关闭语义

必须区分以下概念：

| 概念 | 含义 | 责任阶段 |
|---|---|---|
| 材料已提交 | 责任人完成一次提交，尚未证明整改合规 | P8 |
| 材料检查通过 | 必填材料齐全且格式、时效、关联关系有效 | P9 |
| 整改复核通过 | 规则检查及人工复核通过 | P9 |
| 风险事件关闭 | 单个 `risk_event_id` 已满足关闭条件并获确认 | P9 |
| 作业关闭 | 当前 `job_id` 下所有强制条件满足并获最终确认 | P9 |
| 归档完成 | 已关闭作业进入 P10 并完成档案固化 | P10 |

## 3. 用户交互设计

### 3.1 交互原则

- 用户每次只处理一个明确动作，不要求理解 P8/P9 技术边界。
- 系统应保存已提交材料；补充时只提示缺项，不要求全部重传。
- “材料完整”和“整改合规”分开展示。
- 不满足条件时，“关闭事件/关闭作业”按钮不可用，并提供可读的阻断原因。
- 智能体给出建议，规则服务给出硬性校验结果，有权限人员作最终决定。
- 每次按钮、上传、规则检查、人工意见和状态变化都必须审计。

### 3.2 推荐交互过程

```text
1. 飞书收到风险处置卡片
2. 用户点击“立即处理”
3. 卡片原位更新为“整改中”，展示处置要求和截止时间
4. 用户点击“提交整改材料”进入 Web 详情页
5. 用户上传材料并提交
6. 统一智能体调用 P9 检查工具
7. 若不足：卡片原位显示缺项摘要，并提供“补充材料”链接
8. 若合规：卡片原位显示“待复核”，向复核人发送确认按钮
9. 复核驳回：回到整改/补充材料；保留驳回原因和历史版本
10. 复核通过：进入“待事件关闭确认”
11. 事件关闭后，检查同一 job 下是否还有开放事件
12. 全部满足后生成报告，并向关闭审批人展示“确认关闭作业”
13. 关闭成功后卡片显示只读摘要和“查看报告”链接
```

### 3.3 不充分材料的回复格式

智能体回复必须来自结构化检查结果，建议统一为：

```json
{
  "summary": "当前材料尚不足以发起复核",
  "resolved_items": ["已提供整改后现场全景照片"],
  "missing_items": [
    {
      "code": "GAS_TEST_TIME_MISSING",
      "title": "气体检测时间缺失",
      "required_material": "带检测时间的气体检测记录",
      "reason": "无法判断检测结果是否处于本次作业有效时间内",
      "blocking": true
    }
  ],
  "next_action": "supplement_materials"
}
```

自然语言可以由大模型生成，但 `code`、`blocking` 和下一状态必须由确定性检查结果决定。

## 4. 领域对象与标识

统一使用以下标识，禁止通过字符串格式互相推断：

| 标识 | 作用 |
|---|---|
| `job_id` | 全流程作业实例 |
| `risk_event_id` | P7 形成的正式风险事件 |
| `disposition_task_id` | P8 的处置任务；建议逐步替代对外暴露的 `p8_job_id` 名称 |
| `submission_id` | 一次整改材料提交版本 |
| `evidence_id` | 一份证据或材料 |
| `review_id` | 一次自动或人工复核 |
| `confirmation_id` | 一次敏感操作确认记录 |
| `report_id` | P9 作业过程报告 |

关系为：

```text
job_id
  └─ risk_event_id (1..n)
       └─ disposition_task_id (1..n)
            └─ submission_id (1..n)
                 └─ evidence_id (1..n)
       └─ review_id (0..n)
```

## 5. 状态设计

不建议继续用一个 `status` 同时表达通知、整改、复核和关闭。建议至少拆成事件状态、处置状态、复核状态和作业状态，或者在一个聚合对象内保存四个明确字段。

### 5.1 风险事件状态 `event_status`

```text
open
→ disposition_in_progress
→ pending_review
→ ready_to_close
→ waiting_close_confirmation
→ closed
```

异常/回退：

```text
pending_review → disposition_in_progress   # 材料不足或复核驳回
ready_to_close → disposition_in_progress   # 新证据推翻原结论
任意未关闭状态 → suspended                # 人工接管或数据冲突
```

### 5.2 处置任务状态 `disposition_status`

```text
created
→ notified
→ acknowledged
→ rectifying
→ materials_submitted
```

补充状态：

- `materials_incomplete`：材料缺失，允许补充后再次提交。
- `rectification_rejected`：整改不合规，需要重新整改。
- `extension_requested`：申请延期，等待有权限人员决定。
- `escalated`：已升级给更高层级责任人。
- `cancelled`：经授权取消，不等价于风险关闭。

`materials_submitted` 是 P8 的正常交接状态，不是闭环终态。

### 5.3 复核状态 `review_status`

```text
not_started
→ checking_completeness
→ checking_compliance
→ waiting_human_review
→ approved
```

分支状态：

- `need_more_materials`：材料不充分，返回 P8。
- `rejected`：整改不符合要求，返回 P8。
- `rule_conflict`：规则冲突，等待人工裁决。
- `inconclusive`：模型或证据无法确定，等待人工复核。

### 5.4 作业状态 `job_closure_status`

```text
active
→ closure_checking
→ ready_to_close
→ waiting_close_confirmation
→ closed
→ archive_pending
```

关闭检查不通过时保持 `active`，同时维护 `closure_blockers`，不增加一个含义模糊的 `failed` 状态。

### 5.5 状态变更约束

- 只有状态机服务可以持久化业务状态；LLM Tool 请求只是状态变更命令。
- 每次命令必须包含 `expected_version`，用乐观锁防止飞书/Web 并发覆盖。
- 状态变更必须校验操作者身份、角色、数据范围和动作权限。
- 重复回调必须通过 `idempotency_key` 返回原结果，不能重复执行。
- `event_status=closed` 后普通用户不可继续补充；如需重开必须走专门的授权流程。
- `job_closure_status=closed` 后 P8/P9 仅允许查询，写入转入补充档案或重开流程。

## 6. 新增与调整 Tool

Tool 分为查询、材料、检查、人工协同和关闭五类。Tool 返回结构化结果，不允许只返回自然语言。

### 6.1 查询类

| Tool | 作用 | 是否写入 |
|---|---|---|
| `get_closure_context(job_id, risk_event_id?)` | 聚合作业、风险、处置、材料、复核和权限上下文 | 否 |
| `list_open_risk_events(job_id)` | 查询作业下未关闭事件 | 否 |
| `get_disposition_task(disposition_task_id)` | 查询当前处置状态、要求和截止时间 | 否 |
| `get_closure_progress(job_id)` | 返回事件关闭数量、缺项和下一步 | 否 |

### 6.2 P8 处置与材料类

| Tool | 作用 | 关键约束 |
|---|---|---|
| `create_disposition_task(risk_event_id, assignee, requirements, due_at)` | 创建处置任务 | 对 `risk_event_id` 幂等 |
| `acknowledge_disposition(disposition_task_id)` | 责任人知悉/接收任务 | 校验责任人或代理权限 |
| `start_rectification(disposition_task_id)` | 标记开始整改 | 不代表已完成 |
| `create_material_upload_session(disposition_task_id)` | 创建短时上传会话 | 返回上传约束，不在 LLM 上下文传二进制 |
| `submit_rectification_materials(disposition_task_id, evidence_ids, statement)` | 生成材料提交版本 | 至少一项说明或证据；保留历史版本 |
| `request_extension(disposition_task_id, requested_due_at, reason)` | 申请延期 | 只创建申请，不直接修改截止时间 |
| `escalate_disposition(disposition_task_id, reason)` | 升级处置 | 高风险操作需确认 |
| `return_for_supplement(disposition_task_id, missing_items)` | P9 退回补充材料 | 只能由检查结果触发 |
| `return_for_rectification(disposition_task_id, findings)` | P9 退回重新整改 | 保存驳回依据 |

### 6.3 P9 检查类

| Tool | 作用 | 结果 |
|---|---|---|
| `validate_material_manifest(submission_id)` | 检查必填类型、文件存在性、格式、时间和来源 | 缺项及无效项 |
| `verify_evidence_relevance(submission_id, risk_event_id)` | 判断证据是否支持对应整改要求 | 支持/不支持/无法判断及依据 |
| `evaluate_closure_rules(risk_event_id, submission_id)` | 执行确定性闭环规则 | 规则命中、阻断项、规则版本 |
| `build_review_packet(risk_event_id)` | 生成给复核人的摘要、前后证据对比和规则结论 | `review_packet_id` |
| `record_human_review(review_id, decision, comment)` | 保存人工复核 | 身份、权限、签名和时间 |
| `verify_event_closable(risk_event_id)` | 汇总材料、规则、复核和审计条件 | `closable`、`blockers` |
| `verify_job_closable(job_id)` | 检查所有事件、报告和作业条件 | `closable`、`blockers` |
| `generate_closure_report(job_id, format)` | 生成版本化报告和证据索引 | `report_id`、路径、版本 |

`verify_evidence_relevance` 可以使用视觉/语言模型，但结果只能作为复核输入；硬性数值、时间、权限和状态条件必须由 `evaluate_closure_rules` 等确定性工具判断。

### 6.4 关闭类

| Tool | 作用 | 要求 |
|---|---|---|
| `request_event_close_confirmation(risk_event_id)` | 创建关闭确认请求 | 先调用 `verify_event_closable` |
| `close_risk_event(risk_event_id, confirmation_id, expected_version)` | 关闭单个事件 | 确认令牌、权限、幂等、审计 |
| `request_job_close_confirmation(job_id)` | 创建作业关闭确认请求 | 先生成报告并调用 `verify_job_closable` |
| `close_job(job_id, confirmation_id, expected_version)` | 正式关闭作业 | 所有阻断项为空；关闭后触发 P10 |

关闭 Tool 内部必须重新校验条件，不能相信智能体上一步保存的结论，防止检查完成后现场状态发生变化。

### 6.5 现有 Tool 的处理建议

- `update_job`：逐步缩小为内部兼容入口，禁止任意字符串直接修改状态。
- `hitl_decide`：拆为明确的确认请求与确认落库 Tool。
- `notify_feishu`：保留为通道工具，但卡片内容必须根据状态模板生成。
- `closure_status`：由 `get_closure_progress` 替代或作为兼容别名。
- `closure_verify`：拆为材料清单、证据相关性、事件可关闭、作业可关闭四类 Tool。
- `closure_close`：拆为 `close_risk_event` 与 `close_job`，禁止二者语义混用。

## 7. 是否使用 LangGraph 状态管理

### 7.1 结论

建议使用 LangGraph 管理“对话与流程编排状态”，但不能把 LangGraph Checkpointer 当作唯一业务数据库。

原因：

- P8/P9 存在多轮对话、多次材料补充、人工等待和驳回回退，适合用图表达循环。
- 飞书按钮、Web 上传和后台规则检查可能在不同时间、不同进程发生，需要可恢复中断。
- 关闭属于敏感操作，需要在确认点暂停并从原检查点恢复。
- 业务状态还要被 Web、审计、报表和 P10 稳定查询，因此必须独立持久化。

### 7.2 推荐图结构

```text
load_context
  → route_by_state
      ├─ notify_disposition
      ├─ wait_for_materials ───────────────┐
      ├─ check_materials                  │
      │    ├─ incomplete → explain_gaps ──┘
      │    └─ complete → check_compliance
      │         ├─ rejected → explain_findings → wait_for_materials
      │         ├─ inconclusive → wait_human_review
      │         └─ passed → verify_event_closable
      ├─ wait_event_close_confirmation
      ├─ close_event
      ├─ verify_job_closable
      ├─ generate_report
      ├─ wait_job_close_confirmation
      └─ close_job → trigger_p10
```

等待外部输入时应结束当前执行或使用 LangGraph interrupt/checkpoint，不允许进程内阻塞等待。

### 7.3 建议的 LangGraph State

```python
class ClosureAgentState(TypedDict, total=False):
    messages: list
    job_id: str
    active_risk_event_id: str | None
    active_disposition_task_id: str | None
    active_submission_id: str | None
    user_context: dict
    route: str
    material_check: dict | None
    compliance_check: dict | None
    closure_blockers: list[dict]
    pending_confirmation: dict | None
    last_error: dict | None
```

`messages`、当前路由和临时检查结果可以存在 Checkpointer；以下数据必须写入业务存储：

- 风险事件、处置任务及其状态。
- 材料、证据元数据和提交版本。
- 规则检查结果及规则版本。
- 人工复核、确认和签字。
- 事件关闭、作业关闭和审计日志。

### 7.4 Checkpointer 与线程键

- 开发测试可以继续使用 `MemorySaver`。
- 联调和生产应换成支持进程重启的持久化 Checkpointer，例如 SQLite/PostgreSQL 对应实现。
- 主业务线程建议使用 `thread_id="closure:{job_id}"`，保证同一作业 P8/P9 共用上下文。
- 飞书 `chat_id/open_id` 只作为会话来源，不再作为业务状态主键。
- 一条飞书消息必须先解析并校验 `job_id`，再路由到 `closure:{job_id}`。
- 若用户同时处理多个事件，必须由卡片携带 `risk_event_id`；不能依赖“上一轮谈的是哪个事件”。

### 7.5 并发和一致性

- LangGraph 负责步骤编排，业务存储负责最终一致性。
- Web 上传完成后发布 `materials_submitted` 领域事件，唤醒对应 `closure:{job_id}`。
- 飞书回调、Web 操作和后台任务统一进入命令处理层。
- 命令包含 `command_id`、`idempotency_key` 和 `expected_version`。
- 状态写入成功后再更新飞书卡片；卡片更新失败只重试展示，不回滚已经提交的业务状态。

## 8. 飞书卡片设计

### 8.1 两类按钮

飞书卡片应区分两类交互：

1. 回调按钮：适合“知悉、接收、确认、驳回、升级”等原子操作。
2. 链接按钮：适合上传材料、填写说明、查看完整证据和报告等复杂页面。

不要在飞书回调 toast 中完成文件上传或复杂表单，也不要让大模型根据按钮文字猜测业务对象。

### 8.2 卡片公共字段

每张卡片至少显示：

- 风险等级、作业名称和区域。
- 风险事实与处置要求。
- 当前状态、责任角色和截止时间。
- 材料完整性、整改合规性和闭环阻断项摘要。
- 最近操作者和更新时间。
- 状态对应的操作按钮。

卡片回调 `value` 建议升级为版本化载荷：

```json
{
  "schema_version": "2.0",
  "action": "acknowledge_disposition",
  "job_id": "20260914123000123",
  "risk_event_id": "RISK-001",
  "disposition_task_id": "DSP-001",
  "expected_version": 7,
  "nonce": "opaque-random-value"
}
```

回调服务必须从飞书事件本身读取操作者 `open_id`，不得信任按钮 `value` 中自报的用户身份。

### 8.3 各状态推荐按钮

| 当前状态 | 主按钮 | 次按钮 |
|---|---|---|
| `notified` | 接收任务（回调） | 查看详情（链接） |
| `rectifying` | 提交整改材料（链接） | 申请延期（链接）、升级（回调/确认） |
| `materials_incomplete` | 补充材料（链接） | 查看缺项（链接） |
| `pending_review` | 查看复核包（链接） | 无普通责任人操作 |
| `waiting_human_review` | 进入复核（链接） | 查看复核包（链接） |
| `waiting_close_confirmation` | 进入关闭确认（链接） | 查看事件详情（链接） |
| job `waiting_close_confirmation` | 确认关闭作业（主工作流确认） | 查看报告（链接） |
| `closed` | 查看闭环报告（链接） | 无写操作按钮 |

“关闭事件”和“关闭作业”必须使用不同 action，且卡片标题明确区分，避免误操作。

### 8.4 链接 URL 设计

建议新增统一 Web 路由：

```text
GET /closure/entry/{link_id}
```

其中 `link_id` 是服务端生成的随机、不透明、短时有效标识。服务端根据它解析：

```json
{
  "job_id": "...",
  "risk_event_id": "...",
  "disposition_task_id": "...",
  "target": "materials|review|report|detail",
  "expires_at": "...",
  "allowed_user_or_role": "..."
}
```

推荐最终外链形式：

```text
http://127.0.0.1:8080/closure/entry/<opaque-link-id>
```

不推荐：

```text
http://127.0.0.1:8080/closure?job_id=...&action=close&role=admin
```

原因是明文参数可被修改、转发或进入访问日志。URL 中不能直接携带权限结论、关闭动作、用户角色、文件路径或长期有效 token。

### 8.5 链接跳转流程

```text
用户点击飞书链接按钮
  → 浏览器打开 /closure/entry/{link_id}
  → 服务端检查 link_id 是否存在、过期、已撤销
  → 以短时 link_id 校验其授权的 job、事件和 target
  → 解析 target
      ├─ materials → 整改材料上传页
      ├─ review    → 复核详情页
      ├─ report    → 闭环报告只读页
      └─ detail    → 风险处置详情页
  → 页面通过后端 API 读取最新状态
  → 用户提交后写业务状态并发布领域事件
  → 后台唤醒 Closure Agent 继续检查
  → 原位更新原飞书卡片
```

材料提交链接不使用企业身份认证，直接指向本地服务；短时 link_id 是仅限材料操作的 bearer capability，仍需：

- `link_id` 使用高强度随机值。
- 默认 15～30 分钟失效。
- 只能上传、提交和检查指定风险事件的材料。
- 复核和关闭不能仅凭“持有材料链接”执行。
- 链接被转发给无权限用户时返回无权限页面，不泄露作业详情。

### 8.6 回调跳转与卡片更新

回调按钮继续使用现有：

```http
POST /api/feishu/card-callback
```

建议处理顺序：

1. 验证飞书请求签名/事件来源。
2. 解码并校验 `schema_version`、`action` 和业务 ID。

### 8.7 Web 端写状态后自动刷新卡片（2026-09-14 已落地）

材料提交、复核、事件关闭等操作均可在 Web 端独立完成（不依赖 LLM Agent）。
状态机落盘成功后，必须原位刷新飞书群里的事件卡片，避免按钮与实际状态脱节。

实现要点：

- `send_to_group_card` 在发送成功后，调用 `feishu_card.register_card` 时**额外持久化**
  `chat_id` 与 `chat_type="group"`（[feishu_card.py:register_card](../openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_card.py)）。
  老索引条目无 `chat_id`，刷新时静默跳过。
- 新增 `agents.p8_p9_closure_agent.send_event_card_for_web(job_id, risk_event_id, actor)`：
  - 按 `alert_id` (= risk_event_id) 从 `feishu_card_index.json` 反查原 `chat_id/account_id`。
  - 用最新状态重新构造卡片（`_resolve_link_target` 根据
    `event_status`/`review.status` 自动选 `materials/review/detail/report`）。
  - 调 `send_to_group_card`（已有 card_id 时走 CardKit 原位 PATCH，不重发）。
  - **fire-and-forget**：内部用 `threading.Thread(daemon=True)` 异步执行；
    任何异常（含网络、签名、Schema）只记日志，不抛给 Web 主响应。
- `A6_A7/web_api.py:handle_post` 在以下分支后调度刷新（不阻塞、不影响 HTTP 响应）：
  - `/events/{rid}/acknowledge` `/start-rectification` `/submissions` `/check`
    `/review` `/close-confirmations` `/close`
  - `/initialize` `/report` `/close-confirmations`(job) `/close`(job)
    ——这些是 job 级别变更，需刷新 job 下所有事件卡片。
  - 单个 `/evidence`（只上传文件、不改变状态机）**不刷新**，避免抖动。

刷新失败不影响 Web 写操作的成功语义：用户先看到"已提交"返回，
卡片后台异步重试或等下次操作时一并更新。
3. 从事件解析真实操作者身份并执行权限检查。
4. 以 `nonce/action/operator` 构造幂等键。
5. 调用确定性命令服务执行状态变更；不要先让 LLM 决定任意目标状态。
6. 同步快速返回“处理中”或明确错误 toast。
7. 异步唤醒 LangGraph 继续后续节点。
8. 状态稳定后用 CardKit 原位更新同一张卡片。

重复点击、旧版本卡片或状态已经变化时，应返回：

> 当前任务状态已更新，请以卡片最新内容为准。

并触发一次卡片刷新，不重复执行状态变更。

## 9. Web 页面与 API 草案

### 9.1 页面

| 页面 | 用途 |
|---|---|
| `/closure/entry/{link_id}` | 飞书统一入口和安全跳转 |
| `/closure/jobs/{job_id}` | 作业闭环总览 |
| `/closure/jobs/{job_id}/events/{risk_event_id}` | 单个风险事件详情 |
| `/closure/tasks/{disposition_task_id}/materials` | 材料上传、历史版本和缺项 |
| `/closure/reviews/{review_id}` | 复核包、通过或驳回 |
| `/closure/jobs/{job_id}/report` | 闭环报告和证据索引 |

飞书卡片只保存 `/closure/entry/{link_id}`。材料页不要求企业身份认证，由短时、不透明的 `link_id` 限定到指定作业、风险事件和 `materials` 操作；复核与关闭页仍执行角色校验。

### 9.2 API

```http
GET  /api/closure/jobs/{job_id}
POST /api/closure/jobs/{job_id}/initialize
POST /api/closure/jobs/{job_id}/links
POST /api/closure/jobs/{job_id}/events/{risk_event_id}/evidence
POST /api/closure/jobs/{job_id}/events/{risk_event_id}/submissions
POST /api/closure/jobs/{job_id}/events/{risk_event_id}/check
POST /api/closure/jobs/{job_id}/events/{risk_event_id}/review
POST /api/closure/jobs/{job_id}/events/{risk_event_id}/close-confirmations
POST /api/closure/jobs/{job_id}/events/{risk_event_id}/close
POST /api/closure/jobs/{job_id}/close-confirmations
POST /api/closure/jobs/{job_id}/close
POST /api/closure/jobs/{job_id}/report
```

除材料链接授权的写接口外，所有写接口需要：

- 用户身份和角色。
- `Idempotency-Key`。
- `expected_version` 或 `If-Match`。
- 操作理由；驳回、延期、升级和关闭不得为空。

材料上传、材料提交与充分性检查由 `/closure/entry/{link_id}` 使用同一个短时 bearer link 调用，不额外要求企业账号登录；后端必须逐次校验 `link_id` 的有效期、目标类型、`job_id` 与 `risk_event_id`，并禁止其调用复核或关闭接口。

## 10. 持久化建议

设计阶段可以继续沿用 per-job 文件，但建议把结构扩展为：

```text
data/jobs/{job_id}/
  P8_P9/
    closure_state.json
    disposition_tasks.json
    submissions.json
    evidence_manifest.json
    reviews.json
    confirmations.json
    audit.jsonl
    report/
      report-v1.md
      evidence-index-v1.json
```

正式部署建议迁移到事务数据库和对象存储：

- 数据库保存状态、版本、权限、确认和审计索引。
- 对象存储保存图片、视频、文档等证据。
- 数据库只保存对象 ID、校验和、来源、时间和访问策略。
- LangGraph Checkpointer 保存对话编排状态，不代替上述业务存储。

## 11. 权限与安全要求

- 责任人可以接收任务、提交材料和申请延期，不能复核自己的高风险整改。
- 复核人可以查看证据、通过或驳回，但是否可关闭事件由权限矩阵决定。
- 作业关闭需要独立权限；高风险作业建议双人或指定角色确认。
- 智能体不得从自然语言中推断复核人或关闭审批人身份；材料链接本身仅作为限定范围的提交主体记录。
- 上传材料需要类型、大小、恶意内容和敏感信息检查。
- 报告和证据链接应按用户和 job 数据范围授权。
- 所有敏感动作记录操作者、来源通道、前后状态、规则版本和确认记录。

## 12. 异常与降级

| 异常 | 行为 |
|---|---|
| 模型不可用 | 仍执行确定性清单和规则检查；语义证据转人工复核 |
| 规则冲突 | 禁止自动关闭，列出冲突规则并转人工裁决 |
| 飞书卡片更新失败 | 保留业务结果，重试卡片展示；不回滚业务状态 |
| Web 上传成功但 Agent 未响应 | 材料已持久化，后台任务可重新唤醒检查 |
| 网络中断 | 本地暂存提交和审计；恢复后先对账再继续 |
| 重复回调 | 返回原处理结果或提示状态已变化 |
| 关闭前出现新风险 | 撤销 `ready_to_close`，重新进入 `active/disposition_in_progress` |

## 13. 分阶段开发计划

### 第一阶段：纠正状态语义

- 引入 `disposition_status`、`review_status`、`event_status`、`job_closure_status`。
- P8 的正常终点改为 `materials_submitted`。
- 停止把 `completed/archived` 当作风险闭环。
- 建立 `job_id → risk_event_id → disposition_task_id` 显式映射。

### 第二阶段：材料提交与闭环检查

- 建设材料上传页面和材料版本模型。
- 实现材料清单、证据相关性和规则检查 Tool。
- 实现缺项循环和驳回回到整改状态。
- 生成结构化 `closure_blockers`。

### 第三阶段：事件与作业关闭

- 实现人工复核包。
- 实现事件关闭确认和 `close_risk_event`。
- 实现报告生成、作业关闭检查和 `close_job`。
- `close_job` 成功后才触发 P10。

### 第四阶段：LangGraph 持久化与飞书体验

- 将 P8/P9 统一到 `closure:{job_id}` 图线程。
- 使用持久化 Checkpointer 替换生产环境 `MemorySaver`。
- 建设状态化卡片模板、短时材料链接和本地服务跳转。
- 完成并发、重复点击、进程重启和断网恢复测试。

## 14. MVP 验收用例

1. 用户只上传一张模糊照片，系统指出缺少哪些材料且不允许关闭。
2. 用户分三次补充材料，系统保留已有材料，只提示剩余缺项。
3. 材料齐全但检测值超限，完整性通过、合规性不通过。
4. 模型无法判断现场警戒范围时转人工复核，不自动通过。
5. 复核人驳回后，任务返回整改状态并保留驳回原因。
6. 单个事件关闭时，其他事件仍开放，作业关闭按钮保持不可用。
7. 全部事件关闭且报告生成后，有权限人员才可关闭 `job_id`。
8. 无权限用户转发并打开飞书链接时不能查看或操作。
9. 用户重复点击关闭按钮，只产生一次业务状态变更。
10. 飞书卡片更新失败时，数据库状态仍正确，恢复后能够刷新卡片。
11. 服务重启后，可从持久化 Checkpointer 和业务存储继续原闭环。
12. `job_id` 关闭成功后才触发 P10，P8/P9 转为只读。

## 15. 待确认事项

开发前还需要业务方确认：

- MVP 作业类型及每类风险的必填材料清单。
- 哪些角色可以复核、关闭事件和关闭作业。
- 高风险事件是否要求双人确认。
- 本地闭环 Web 服务在飞书客户端所在终端的可访问地址。
- Web 服务正式可访问域名及生产网访问边界。
- 图片、视频和检测记录的保存期限、大小限制及脱敏要求。
- P8 当前 `archived.json` 是否仅保留为历史兼容，还是迁移为新的处置任务历史表。
