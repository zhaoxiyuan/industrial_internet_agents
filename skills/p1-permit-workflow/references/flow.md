# P1 完整运行流程（按时间顺序，含文件行号）

行号为 2026-10-13 实测，改动后可能漂移，但**函数名与文件稳定**。排查时优先按函数名搜。

## 0. 总览

```
[浏览器 app.js]
   │ fetch POST /api/workflow/start
   ▼
[web/server.py:69]  路由分发
   ▼
[web/api/workflow.py:387 handle_workflow_start]
   │ 生成 job_id，起后台线程 :440
   ▼
[agents/main_agent.py:1174 run_workflow]
   │ 落盘申请/初始化状态 :1191~1200
   │ 阶段循环 :1215 → :1227 execute_stage_with_retry(:1025)
   ▼
[agents/p1_permit_agent.py:1196 execute_stage]  ← P1 全部业务在这里
   │
   ├── 未批准 → :1263 _build_jsa_result(:122)  ……【模型调用 1：JSA】
   │              └─ jsa_analyze.py + jsa_report.py
   │           → :1271 挂起，等 jsa_approval
   │
   └── 已批准 → :1251 选 builder
                ├─ _build_manual_permit_result(:703)   ← 手动填报
                └─ _build_docx_permit_result(:277)     ← 上传 DOCX
                     └─ :537 _decide_safety_measures ……【模型调用 2：措施判定】
                          └─ article_knowledge.py（知识库对齐 + 条文依据）
                     └─ permit_template.py（票面行 + 渲染）
                → :1291 _process_p1_result 落盘
                → :1380 挂起，等 permit_final_approval
   ▼
[人工审批] web/api/workflow.py:451 → main_agent.py:1313 confirm_and_continue
   ▼
批准后 P1 标记 completed，run_workflow 循环进入 P2
```

**两次模型调用**是全流程唯一的外部**网络**依赖；其余全是纯 Python（规则 + 文件读写）。但失败不只有模型——模板读取、文件读写、配置错误同样会中断流程。

## 1. 请求入口（Web 层）

| 步骤 | 文件:行 | 做什么 |
|---|---|---|
| 前端发起启动 | `web/app.js:1546` | `fetch POST /api/workflow/start` |
| HTTP 路由 | `web/server.py:69` | `path == "/api/workflow/start"` → `workflow_api.handle_workflow_start` |
| 启动处理 | `web/api/workflow.py:387 handle_workflow_start` | 生成 job_id、落盘申请、返回 `starting` |
| 后台执行 | `web/api/workflow.py:417 run_workflow_background` | 后台线程调 `agents.main_agent.run_workflow`（:423-424） |
| 审批路由 | `web/server.py:73` | `/api/workflow/confirm` → `handle_workflow_confirm`（:451） |
| 下载路由 | `web/server.py` | `/api/workflow/permit-docx` → `handle_workflow_permit_docx`（:272，支持 `artifact=jsa`） |

## 2. 工作流调度（`agents/main_agent.py`）

| 行 | 内容 |
|---|---|
| `:762` | `STAGE_EXECUTORS = {"P1": p1_execute_stage, ...}` —— **阶段名 → 执行器**的映射，这就是"哪个阶段调哪个文件"的总入口 |
| `:759` | `execute_p1 = p1_execute_stage`（别名，来自 `p1_permit_agent.execute_stage`） |
| `:1025` | `execute_stage_with_retry()` —— 重试、attempt 计数、中断恢复预算（`MAX_INTERRUPTED_RECOVERIES`） |
| `:1174` | `run_workflow()` —— 主流程 |
| `:1191~1200` | `save_job_application` / `init_workflow_status` / `init_execution_status` / `add_job_log` |
| `:1215` | 按 `STAGE_EXECUTORS` 顺序循环阶段 |
| `:1227` | 调 `execute_stage_with_retry(job_id, stage, executor, config)` |
| `:1235` | 结果含 `pending_confirmation` → 置 `waiting` 并 **return（工作流暂停）** |
| `:1255` | 结果 `completed=False` → 置 `failed`/`error` |
| `:1313` | `confirm_and_continue()` —— 审批后续跑 |
| `:1718` | `_confirm_and_continue_async()` —— 异步模式的后台线程体 |

> `run_workflow` 是"**跑到底、遇审批就停**"，不是"每次只跑一个阶段"。恢复靠 `confirm_and_continue` 重新进入。

## 3. P1 第一遍：JSA（`agents/p1_permit_agent.py:1196 execute_stage`）

| 行 | 内容 |
|---|---|
| `:1214` | `get_stage_result_path(job_id, "p1")` → `data/jobs/<工单号>/p1_result.json` |
| `:1224` | 读 `application.json`（申请数据） |
| `:1238` | 算 `application` 的 sha256 指纹 |
| `:1240` | `approved` = 同一申请 **且** `jsa_approval.decision == "approve"` 且未被否决 |
| `:1258` | `get_permit_type()` 识别作业类型（`permit_template.py`） |
| `:1261` | `_jsa_values()`（:95）合并申请与表单值为 JSA 上下文 |
| `:1262` | `_related_permit_types()`（:676）取关联作业类型（供专项章节） |
| `:1263` | **`_build_jsa_result()`（:122）→ 模型调用 1** |
| `:1265` | `jsa_report.build_info_lines()`（报告"作业基本信息"章节） |
| `:1271` | 写入 `pending_confirmation = {type: "jsa_approval", approval_id: uuid4}` |
| `:1277` | 写 `permit.json`（此时只有 `application` / `jsa_result` / `p1_phase`，**不建票面**） |
| `:1280` | 写 `p1_result.json` |
| `:1283` | `return` → 交给 `run_workflow` 挂起 |

### 3.1 JSA 生成与渲染

| 步骤 | 文件:行 | 做什么 |
|---|---|---|
| 入口 | `p1_permit_agent.py:122 _build_jsa_result` | 组装上下文、调模型、渲染报告 |
| 章节规则 | `workflow/jsa_analyze.py:53 special_section_titles` | **专项章节由规则决定**（介质含硫 → H₂S；关联类型 → 各自专项；高处+吊装合一） |
| 上下文 | `jsa_analyze.py:95 build_jsa_context` | 作业基本信息 + 该类型措施清单 + 待输出章节 |
| 调模型 | `jsa_analyze.py:210 generate_jsa` | `create_chat_model_with_logging("P1-JSA", ...)`，**temperature=0.2** |
| 提示词 | `agents/system_prompt/JSA_SYSTEM_PROMPT.md` | 经 `load_system_prompt("JSA")` 加载 |
| 严格解析 | `jsa_analyze.py:137 parse_jsa_payload` | level 只三档、steps≥4、章节必须一致；**任何失败 → `source="unavailable"` 且不产出内容** |
| 报告渲染 | `workflow/jsa_report.py`（`render_jsa_report`） | 用模板块当原型**重建整份正文**，输出 `data/jobs/<工单号>/jsa_report.docx` |
| 安全渲染 | `p1_permit_agent.py:170 _render_jsa_report_safely` | **渲染失败只记 warning，不影响出票** |

## 4. 第一次审批（JSA 审批）

| 行 | 内容 |
|---|---|
| `web/api/workflow.py:451 handle_workflow_confirm` | 收前端审批请求 |
| `main_agent.py:1345` | `get_job_lock(job_id)` —— 校验与占位同锁，防两个页面同时通过 |
| `main_agent.py:1351` | `is_waiting` 判定（以 `agents.<stage>.status` 为准） |
| `main_agent.py:1367` | **`approval_id` 校验**，不符 → `already_processed`（防误批上一轮） |
| `main_agent.py:1370-1375` | P1 的 JSA 审批：**JSA 未成功生成则拒绝批准出票** |
| `main_agent.py:1398` | `save_confirmation(...)` → `workflow/job_persistence.py:53` |
| `main_agent.py:1399-1403` | 写 `jsa_approval` + `p1_phase="permit_build"` 到 `p1_result.json` |
| `main_agent.py:1527` | 异步模式 → 后台线程 `_confirm_and_continue_async`（:1718） |
| `main_agent.py:1553-1571` | 同步模式：`execute_stage_with_retry(..., execute_p1(resume=True), continuation=True)` → **重新进入 `execute_stage`** |

> `main_agent.py` 的 `decision == "reject"` 分支在 `:1407` 和 `:1456` **逐行重复出现了两次**，后者不可达（前者已 return），约 45 行死代码。改这块时留意，别改错那一份。

## 5. P1 第二遍：措施判定 + 出票

回到 `p1_permit_agent.py:1196 execute_stage`，走 `approved` 分支：

| 行 | 内容 |
|---|---|
| `:1242-1250` | 复用已批准的 `jsa_result`（**不重跑模型**） |
| `:1251` | `builder = _build_manual_permit_result`（`input_source=="manual"`）或 `_build_docx_permit_result` |
| `:1253` | `_process_p1_result()`（:1291）落盘 |

### 5.1 手动填报路径 `_build_manual_permit_result`（:703）

| 行 | 内容 |
|---|---|
| `:703` | 入口：合并申请与表单值 |
| `:192` | `_merge_jsa_risks()` —— JSA 的主要风险回填票面 `risks`，**用户勾选优先 + 去重补充** |
| `:650` | `_manual_measures_context()` —— 构造判定上下文（与票面同源 `resolve_fields`） |
| `:537` | **`_decide_safety_measures()` → 模型调用 2** |
| — | `build_permit_rows()`（`permit_template.py`）生成票面行 |
| — | 写 `permit.json`：`permit_content`（含 `measure_decisions` / `permit_values` / `related_permit_types`） |
| — | `pending_confirmation = {type: "permit_final_approval"}` |

### 5.2 措施判定内部（**知识库就在这一环**）

| 行 | 内容 |
|---|---|
| `:517 _knowledge_items` | 调 `workflow/article_knowledge.py: align_measures()` —— 逐条与附录 1 对齐；**对不齐返回 `None`** |
| `:185 _jsa_measures_context` | 把**已批准 JSA 的 steps** 并入措施判定上下文（在 `_manual_measures_context` / `_docx_measures_context` 之后再套一层；用在 `:303` / `:732`） |
| `:537 _decide_safety_measures` | `grounded = kb_items is not None`；`mode = "knowledge"` / `"full"` |
| `:419 _measures_user_message` | 拼用户消息，**`:431` 逐条附 `〖依据·第N条〗` 条文原文** |
| `:470 _basis_excerpt` | 决定从条文里截哪 320 字（**原文优先，逐字返回**） |
| 提示词 | `agents/system_prompt/P1_MEASURES_SYSTEM_PROMPT.md`（`load_system_prompt("P1_MEASURES")`） |
| 模型 | `create_chat_model_with_logging("P1-MEASURES", job_id, temperature=0.0, reasoning_effort="low")` |
| `:615 _parse_measure_decisions` | 严格解析；失败 → `source="unavailable"`、`items=[]`（不写任何勾，退回人工） |
| 知识库访问层 | `workflow/article_knowledge.py`（`load_knowledge` / `align_measures` / `basis_texts`） |
| 知识库数据 | `data/knowledge/permit_articles.json` |

### 5.3 上传 DOCX 路径 `_build_docx_permit_result`（:277）

| 行 | 内容 |
|---|---|
| `:208 _catalog_to_measures` | 把上传票面的措施目录转成待判清单 |
| `:249 _docx_measures_context` | 判定上下文 |
| `:229 _annotate_catalog` | 把判定结果注回目录 |

> 该路径的措施来自**上传文件自带的清单**，与附录 1 **通常**对不齐（对齐要求：同类型 + 条数一致 + 逐条相似度 ≥0.85）→ 判定**通常**退回 `mode="full"`（无条文依据）。**这是对齐结果，不是"DOCX 路径不支持知识库"**——两条路径共用同一个 `_decide_safety_measures`。

### 5.4 票面与渲染（`workflow/permit_template.py`）

| 函数 | 作用 |
|---|---|
| `load_config` / `get_permit_type` / `resolve_fields` | 读 `data/permit_templates/permit_templates.json`，把 `common_fields` + 各类型 `extra_fields` 解析成字段清单 |
| `list_safety_measures` | 取该类型票面的安全措施清单（票面固定文字） |
| `build_permit_rows` | 按字段 `mode`（见 mechanisms.md）把值写进表格行 |
| `render_permit_docx` | 套模板 `data/permit_templates/*.docx` 渲染，并把 `measure_decisions` 的 √/× 写进票面 |
| 模板机制 | 纯 `zipfile` + 正则改写 `document.xml`，**不引入 python-docx** |

## 6. 第二次审批与收尾

| 行 | 内容 |
|---|---|
| `main_agent.py:1503-1504` | 清 waiting；`jsa_review` 时置 `running`，否则 `completed` |
| `main_agent.py:1507-1514` | 删 `pending_confirmation`，写回 `p1_result.json` |
| 批准 → 阶段 completed | `run_workflow` 循环继续 **P2**（`STAGE_EXECUTORS` 下一项） |
| 否决 → `:1407` | 置 `failed`；**P1 会 `reset_permit_execution(job_id)`**（:1422），下次重跑并再次等审批 |

## 7. 数据落盘（全部在 `data/jobs/<17位工单号>/`）

| 文件 | 写入者 | 内容 |
|---|---|---|
| `application.json` | `job_persistence.py:15 save_job_application` | 作业申请（`permit_type` / `permit_fields` / `input_source`） |
| `workflow_status.json` | `workflow_state.py:21/45` | 各阶段状态、`pending_confirmations` |
| `execution_status.json` | `execution_status.py` | 阶段 attempts / interrupted 计数 |
| `p1_result.json` | `p1_permit_agent.py:1280 / :1503` | P1 结果、`jsa_approval`、`p1_phase`、`jsa_result` |
| `permit.json` | `p1_permit_agent.py:1277`、出票时 | `application` / `jsa_result` / `permit_content`（含 `measure_decisions`） |
| `jsa_report.docx` | `jsa_report.py` | JSA 报告（下载用） |
| `permit.docx` | `web/api/workflow.py:360` | 许可证（下载时按需生成） |
| `logs.json` / `confirmations.json` | `job_persistence.py:28/:53` | 日志、审批记录（含 `approval_type` / `approval_id`） |

**文件工具与并发**（`workflow/file_utils.py`）：`get_job_dir:41`、`get_job_lock:17`（每工单 RLock）、`get_stage_result_path:53`、`read_json_file:58` / `write_json_file:66`（原子写）。

**页面占用租约**：`workflow/job_lease.py`（`/api/workflow/claim|heartbeat|release`）。**执行占用**：`workflow/execution_guard.py:12 claim_job_execution`。

## 8. 三个容易踩的点

**① `run_workflow` 是"跑到底、遇审批就停"**，不是"每次只跑一个阶段"。恢复靠 `confirm_and_continue` 重新进入。

**② P1 会被进入两次**（`execute_stage` 调两次），靠 `jsa_application_fingerprint` + `jsa_approval` 区分——改了申请内容，指纹变，会重新跑 JSA 并要求重新审批。

**③ 两次模型调用是主要的耗时来源**（但不是唯一失败来源）：JSA（temperature 0.2）与措施判定（temperature 0.0 + `reasoning_effort="low"`）。两者失败都降级为 `source="unavailable"`，**不产出编造内容**。实测耗时见 `constraints.md`（已标注实测日期）。
