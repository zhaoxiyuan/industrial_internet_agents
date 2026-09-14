# P10: 归档与复盘 Agent (p10_archive_agent)

> 文件：[agents/p10_archive_agent.py](../../agents/p10_archive_agent.py)
> 系统提示词：[agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md](../../agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md)
> 在主流程中位于 **P1 → P2 → … → P9 → P10** 链路的最后一站，负责全过程记录归档与案例挖掘。
>
> **文档状态**：v2 — 2026-08-20 新增 4 个规划工具（`read_pipeline_data` / `upload_template` / `update_system_prompt` / `archive_report`），
> 代码侧尚未实现，本节为接口契约，详见各工具的「I/O 详细」与 [§ 待实现说明](#待实现说明)。

## 概述

**归档与复盘专家**，负责把 P1（许可）→ P9（闭环）阶段产生的全部业务记录沉淀为可检索、可分析的知识资产，
并从历史数据中挖掘**误报 / 漏报 / 规则冲突**三类案例，反哺规则与模型迭代。

P10 是工业互联网边缘智能 Agent 工作流的**收尾阶段**，三大职责：

| 职责 | 说明 |
|------|------|
| **数据汇聚** | 一次性读取某个 `task_id` 下 P1–P10 各阶段的全部记录（接口一致性见 [§ read_pipeline_data](#5-read_pipeline_data)） |
| **模板驱动** | 支持上传全局归档模板 + 整段覆盖提示词规则，复盘口径可定制（见 [§ upload_template](#6-upload_template) / [§ update_system_prompt](#7-update_system_prompt)） |
| **报告生成** | 调用 LLM 基于 P1–P10 全量数据 + 当前模板生成 Markdown 归档报告（见 [§ archive_report](#8-archive_report)） |

输出供三个下游系统消费：

| 下游系统 | 数据形态 |
|----------|----------|
| 向量数据库 | 案例摘要 Embedding（语义检索） |
| 规则管理系统 | 规则冲突报告（人工 review） |
| 模型训练平台 | 模型优化建议（CV / 传感器模型） |

## 主要功能

1. **汇聚作业数据**：按 `task_id` 一次性聚合读取 P1–P10 各阶段结果文件（保证接口一致性）
2. **模板与提示词管理**：上传全局归档模板 + 整段覆盖 P10 system_prompt，复盘口径可定制
3. **报告生成**：调用 LLM 基于全量数据 + 当前模板生成 Markdown 归档报告
4. **任务归档**：把作业票证、视频证据、风险事件、处置记录、作业报告打包归档（生成 `archive_id`）
5. **案例挖掘**：识别 `misdetection` / `missed` / `rule_conflict` 三类案例
6. **效果分析**：输出处置性能指标（检出率、误报率、平均响应时长、闭环时长）
7. **优化建议**：生成规则修订（`rule_modify` / `rule_add`）与模型迭代（`model_update`）建议

## 入口函数

| 函数 | 说明 |
|------|------|
| `create_archive_agent()` | 工厂：创建 P10 基础版 Agent（无 HITL） |
| `create_archive_agent_with_hitl()` | 工厂：创建 P10 HITL 版 Agent（生产用） |
| `run_archive_agent(message)` | 入口：运行 P10 Agent（调一次） |
| `archive_demo(message, history)` | 入口：Gradio ChatInterface / chat_reply 兼容（位置参数 `(message, history)`） |

### `create_archive_agent`

**基础版**，无 `HumanInTheLoopMiddleware`。仅供单元测试 / 离线仿真。

```python
def create_archive_agent() -> CompiledStateGraph:
```

| 配置项 | 值 |
|--------|---|
| `model` | `create_chat_model_with_logging("P10")` |
| `tools` | 8 个工具（`read_pipeline_data` / `upload_template` / `update_system_prompt` / `archive_report` / `archive_task` / `archive_cases` / `archive_performance` / `archive_suggestions`） |
| `system_prompt` | `load_system_prompt("P10")`（来自 `agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md`） |

### `create_archive_agent_with_hitl`

**HITL 版**，挂 `HumanInTheLoopMiddleware`，生产场景使用。`_archive_checkpointer = MemorySaver()` 单例支持中断恢复。

```python
def create_archive_agent_with_hitl() -> CompiledStateGraph:
```

| 额外配置 | 值 |
|----------|---|
| `middleware` | `[HumanInTheLoopMiddleware(interrupt_on={...})]` |
| `checkpointer` | `_archive_checkpointer`（`MemorySaver()`） |

### `run_archive_agent`

```python
def run_archive_agent(message: str) -> str:
```

| 入参 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | `str` | ✅ | 用户消息文本 |

返回最后一条 AI 消息内容（`extract_output(result)`）。

### `archive_demo`

Gradio ChatInterface / chat_reply 兼容入口（**位置参数 `(message, history)` 不可破坏**）。

```python
def archive_demo(message: str, history: list = None) -> str:
```

## 工具定义

| 工具 | 触发条件 | 说明 | HITL | 状态 |
|------|----------|------|------|------|
| `read_pipeline_data` | 用户要查看 / 复盘某个作业的全过程数据 | 按 `task_id` 聚合读取 P1–P10 各阶段结果 | ❌ 自动批准 | 🆕 规划中 |
| `upload_template` | 用户上传归档模板 | 保存全局 Markdown / JSON 模板到 `agents/templates/archive/` | ✅ 需要确认 | 🆕 规划中 |
| `update_system_prompt` | 用户修改归档提示词规则 | 整段覆盖 `agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md` | ✅ 需要确认 | 🆕 规划中 |
| `archive_report` | 用户生成归档报告 | LLM 基于 P1–P10 全量数据 + 当前模板生成 Markdown 报告 | ✅ 需要确认 | 🆕 规划中 |
| `archive_task` | 用户归档任务 | 归档任务数据（生成 `archive_id` + 归档清单） | ✅ 需要确认 | ✅ 已实现 |
| `archive_cases` | 用户挖掘案例 | 挖掘误报 / 漏报 / 规则冲突案例 | ❌ 自动批准 | ✅ 已实现 |
| `archive_performance` | 用户分析性能 | 分析处置效果（检出率 / 误报率 / 响应时长 / 闭环时长） | ❌ 自动批准 | ✅ 已实现 |
| `archive_suggestions` | 用户生成建议 | 生成规则与模型优化建议 | ✅ 需要确认 | ✅ 已实现 |

## HITL 中断矩阵

```python
HumanInTheLoopMiddleware(interrupt_on={
    # 🆕 规划中工具（2026-08-20）
    "read_pipeline_data":    False,   # 只读，自动放行
    "upload_template":       True,    # 模板落盘需要确认（全局影响后续所有 P10 调用）
    "update_system_prompt":  True,    # 覆盖系统提示词需要确认（高风险，影响 LLM 行为）
    "archive_report":        True,    # 生成报告需要确认（写入 data/jobs/{job_id}/）
    # ✅ 已实现工具
    "archive_task":          True,    # 写入归档需要确认（不可逆）
    "archive_cases":         False,   # 只读挖掘自动放行
    "archive_performance":   False,   # 只读分析自动放行
    "archive_suggestions":   True,    # 推送规则/模型建议需要确认
})
```

> **设计意图**：P10 只对**写入 / 推送类**工具要求确认（`archive_task` 落盘、`archive_suggestions` 反哺下游），
> **只读类**挖掘与分析自动放行，避免反复打断。
> 新增的 `update_system_prompt` / `upload_template` 同样需要确认，因其影响后续 P10 全局行为。

## 工具 I/O 详细

> 🆕 **本节含 4 个规划工具的接口契约**。代码侧尚未实现，签名 / 入参 / 出参以本节为准，后续实现需保持一致。

---

### 1. `read_pipeline_data`（🆕 规划中）

#### 用途

按 `task_id` 一次性聚合读取 P1–P10 各阶段落盘结果。**保证接口一致性**：所有阶段的读取走统一入口，
每个阶段返回结构化 dict（含 `data` / `available` / `path`），未落盘的阶段返回 `available=False`，**不抛异常**。

#### 签名

```python
def read_pipeline_data(task_id: str) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | ✅ | 主流程作业 ID（如 `JOB-20260813-001`） |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "read_pipeline_data",
  "data": {
    "task_id": "JOB-20260813-001",
    "stages": {
      "P1": {"available": true,  "path": "data/jobs/JOB-20260813-001/p1_result.json",  "data": { /* 完整 p1_result */ }},
      "P2": {"available": true,  "path": "data/jobs/JOB-20260813-001/p2_result.json",  "data": { /* ... */ }},
      "P3": {"available": true,  "path": "data/jobs/JOB-20260813-001/p3_result.json",  "data": { /* ... */ }},
      "P4": {"available": true,  "path": "data/jobs/JOB-20260813-001/p4_result.json",  "data": { /* ... */ }},
      "P5": {"available": true,  "path": "data/jobs/JOB-20260813-001/p5_result.json",  "data": { /* ... */ }},
      "P6": {"available": true,  "path": "data/jobs/JOB-20260813-001/p6_result.json",  "data": { /* ... */ }},
      "P7": {"available": true,  "path": "data/jobs/JOB-20260813-001/p7_result.json",  "data": { /* ... */ }},
      "P8": {"available": true,  "path": "data/jobs/JOB-20260813-001/p8_result.json",  "data": { /* ... */ }},
      "P9": {"available": true,  "path": "data/jobs/JOB-20260813-001/p9_result.json",  "data": { /* ... */ }},
      "P10": {"available": false, "path": "data/jobs/JOB-20260813-001/p10_result.json", "data": null, "note": "P10 尚未执行（首次复盘为预期）"}
    },
    "available_count": 9,
    "total_count": 10
  }
}
```

#### 接口一致性约束

| 约束 | 说明 |
|------|------|
| **统一返回结构** | 每阶段固定返回 `{available, path, data, note?}`；`data` 为 `None` 表示未落盘 |
| **不抛异常** | 文件缺失 / JSON 解析失败 → `available=False` + `note`，**不**中断整个调用 |
| **stage 枚举固定** | 必须包含 `P1` … `P10` 共 10 个 key；新增阶段需在此工具中同步扩展 |
| **路径约定** | `data/jobs/{task_id}/p{n}_result.json`（`p{n}` 小写、单数字不带前导 0） |
| **只读** | 不写盘、不修改任何 state |

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | `task_id` 为空 | False |
| `JOB_DIR_NOT_FOUND` | `data/jobs/{task_id}/` 目录不存在 | False |

> 单个阶段文件缺失 → 走 `available=False` 分支，**不**触发 `JOB_DIR_NOT_FOUND`。

#### 副作用

- ❌ 不修改任何 state / 磁盘
- ✅ 一次调用覆盖全阶段，LLM 推理成本最低

---

### 2. `upload_template`（🆕 规划中）

#### 用途

上传全局归档模板，存到 `agents/templates/archive/`。模板影响后续所有 P10 调用（用于 `archive_report` 拼装报告结构）。

#### 签名

```python
def upload_template(template_name: str, template_content: str, format: str = "markdown") -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `template_name` | `str` | ✅ | 模板名（如 `default` / `high_risk_v1`）；仅允许字母数字下划线连字符 |
| `template_content` | `str` | ✅ | 模板正文（Markdown / JSON 字符串） |
| `format` | `str` | ❌ 默认 `markdown` | `markdown` / `json`；影响落盘文件扩展名与解析方式 |

#### 存储位置

| format | 路径 |
|--------|------|
| `markdown` | `agents/templates/archive/{template_name}.md.tmpl` |
| `json` | `agents/templates/archive/{template_name}.json.tmpl` |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "upload_template",
  "data": {
    "template_name": "high_risk_v1",
    "format": "markdown",
    "path": "agents/templates/archive/high_risk_v1.md.tmpl",
    "size_bytes": 4096,
    "uploaded_at": "2026-08-20T10:30:00+00:00",
    "is_active": false,    // 默认非激活；激活通过 update_system_prompt 关联
    "note": "已上传但未激活，需配合 update_system_prompt 引用才能生效"
  }
}
```

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | `template_name` 含非法字符 / `template_content` 为空 | False |
| `TEMPLATE_EXISTS` | 同名模板已存在（默认拒绝覆盖；如需覆盖显式传 `overwrite=True`） | True |

#### 副作用

- ✅ 写入 `agents/templates/archive/` 目录
- ❌ 不修改 `system_prompt`；激活方式见 `update_system_prompt`

---

### 3. `update_system_prompt`（🆕 规划中）

#### 用途

整段覆盖 P10 system_prompt。**高风险工具**：修改会影响后续所有 P10 调用的 LLM 行为，HITL 必须确认。

#### 签名

```python
def update_system_prompt(new_content: str, template_name: Optional[str] = None) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `new_content` | `str` | ✅ | 完整的新 system_prompt 内容（Markdown 字符串） |
| `template_name` | `Optional[str]` | ❌ | 若提供，**额外**激活 `upload_template` 上传的模板（拼到 system_prompt 末尾） |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "update_system_prompt",
  "data": {
    "path": "agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md",
    "size_bytes": 8192,
    "updated_at": "2026-08-20T10:30:00+00:00",
    "preview_first_line": "你是一个归档与复盘专家...",
    "backup_path": "agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md.bak.20260820T103000",
    "template_attached": "high_risk_v1"   // 若传了 template_name
  }
}
```

#### 行为

| 步骤 | 说明 |
|------|------|
| 1. 备份 | 把当前 `P10_ARCHIVE_SYSTEM_PROMPT.md` 复制到 `*.bak.{timestamp}` |
| 2. 覆盖 | 写入 `new_content` 到 `P10_ARCHIVE_SYSTEM_PROMPT.md` |
| 3. （可选）拼模板 | 若 `template_name` 给出，把对应模板内容追加到 system_prompt 末尾 |
| 4. 通知 | 下次 `create_archive_agent()` 调 `load_system_prompt("P10")` 自动读新版本 |

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | `new_content` 为空 / `template_name` 在 `agents/templates/archive/` 找不到 | False |
| `WRITE_FAILED` | 文件写入失败（权限 / 磁盘满） | True |

#### 副作用

- ✅ 覆盖 `agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md`（**全局生效**）
- ✅ 自动备份上一版本
- ⚠️ **影响所有后续 P10 invoke 调用**（高风险）

---

### 4. `archive_report`（🆕 规划中）

#### 用途

调用 LLM 基于 P1–P10 全量数据 + 当前 system_prompt（含已激活模板）生成 Markdown 归档报告，
存到 `data/jobs/{task_id}/archive_report.md`。

#### 签名

```python
def archive_report(task_id: str, template_name: Optional[str] = None) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | ✅ | 主流程作业 ID |
| `template_name` | `Optional[str]` | ❌ | 临时指定本次报告使用的模板（覆盖 system_prompt 拼接的默认模板） |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "archive_report",
  "data": {
    "task_id": "JOB-20260813-001",
    "report_id": "RPT-20260820103000",
    "path": "data/jobs/JOB-20260813-001/archive_report.md",
    "template_used": "high_risk_v1",
    "size_bytes": 12288,
    "stages_included": ["P1","P2","P3","P4","P5","P6","P7","P8","P9"],
    "generated_at": "2026-08-20T10:30:00+00:00",
    "preview_headings": [
      "# 作业归档报告 — JOB-20260813-001",
      "## 1. 作业概览",
      "## 2. 风险事件时间线",
      "## 3. 处置记录",
      "## 4. 性能指标",
      "## 5. 案例与建议"
    ]
  }
}
```

#### 报告标准章节（默认模板 `default`）

| 章节 | 内容来源 |
|------|----------|
| 1. 作业概览 | P1（许可） + P2（任务） + P3（上下文） |
| 2. 监测与风险 | P6（监测） + P7（风险研判） |
| 3. 处置记录 | P8（人机处置） |
| 4. 闭环报告 | P9 |
| 5. 性能指标 | P10 `archive_performance` |
| 6. 案例与建议 | P10 `archive_cases` + `archive_suggestions` |

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `INVALID_ARGUMENT` | `task_id` 为空 / `template_name` 找不到 | False |
| `NO_STAGE_DATA` | P1–P10 全部未落盘（`available_count=0`） | False |
| `LLM_FAILED` | LLM 调用失败 | True |
| `WRITE_FAILED` | 报告落盘失败 | True |

#### 副作用

- ✅ 写入 `data/jobs/{task_id}/archive_report.md`
- ✅ 调用 LLM（消耗 token）
- ❌ 不修改 `system_prompt` / 不上传模板

#### LLM 输入组装

```
messages:
  [SystemMessage(content=load_system_prompt("P10"))]
  [HumanMessage(content=(
    "# 作业 ID\n{task_id}\n\n"
    "# P1-P10 全量数据\n{read_pipeline_data(task_id) 的 data.stages dict}\n\n"
    "# 模板\n{template_content if template_name else 'default'}\n\n"
    "请按 system_prompt 要求生成归档报告。"
  ))]
```

---

### 5. `archive_task`

#### 用途

归档 P1–P9 全过程记录。生成 `archive_id`（格式 `ARC-YYYYMMDDHHMMSS`）并返回归档清单。

#### 签名

```python
def archive_task(task_id: str) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | ✅ | 任务唯一标识（如 `JOB-20260813-001`） |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "archive_task",
  "data": {
    "task_id": "JOB-20260813-001",
    "archive_id": "ARC-20260820103000",
    "archived_at": "2026-08-20T10:30:00+00:00",
    "contents": [
      "作业票证",
      "视频证据片段",
      "风险事件记录",
      "处置全记录",
      "作业报告"
    ],
    "status": "archived"
  }
}
```

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `TASK_NOT_FOUND` | `task_id` 为空 | False |

#### 归档内容

| 类别 | 形态 |
|------|------|
| 作业票证 | 结构化 JSON + 扫描件 |
| 视频证据片段 | mp4 切片索引 |
| 风险事件记录 | A6 研判结果列表 |
| 处置全记录 | P8 P8_job 列表 |
| 作业报告 | P9 闭环报告 |

---

### 6. `archive_cases`

#### 用途

挖掘**误报 / 漏报 / 规则冲突**三类案例，供规则与模型迭代。

#### 签名

```python
def archive_cases(task_id: str, case_type: Optional[str] = None) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | ✅ | 任务唯一标识 |
| `case_type` | `Optional[str]` | ❌ | `misdetection` / `missed` / `rule_conflict` / `all`（默认 `all`） |

#### 案例类型

| 类型 | 说明 |
|------|------|
| `misdetection` | 检测模型误报（例：CV 模型把管道反光识别为未佩戴安全帽） |
| `missed` | 人工发现的风险事件，传感器未报警 |
| `rule_conflict` | 同场景下 GB 标准与企标规则冲突 |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "archive_cases",
  "data": [
    {
      "case_id": "CASE-20260820-001",
      "type": "misdetection",
      "description": "CV模型误将管道反光识别为未佩戴安全帽",
      "evidence": ["clip_误报001.mp4"],
      "mined_at": "2026-08-20T10:30:00+00:00"
    },
    {
      "case_id": "CASE-20260820-002",
      "type": "missed",
      "description": "人工发现作业区域温度异常但传感器未报警",
      "evidence": ["人工记录表"],
      "mined_at": "2026-08-20T10:30:00+00:00"
    },
    {
      "case_id": "CASE-20260820-003",
      "type": "rule_conflict",
      "description": "GB标准和企标在受限空间作业时间限制上存在冲突",
      "evidence": ["GBXXXX-X", "企标-QHSE-003"],
      "mined_at": "2026-08-20T10:30:00+00:00"
    }
  ]
}
```

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `TASK_NOT_FOUND` | `task_id` 为空 | False |

---

### 7. `archive_performance`

#### 用途

输出处置性能指标，用于看板展示与季度评估。

#### 签名

```python
def archive_performance(task_id: str) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | ✅ | 任务唯一标识 |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "archive performance",
  "data": {
    "task_id": "JOB-20260813-001",
    "metrics": {
      "detection_rate": 0.92,
      "false_positive_rate": 0.08,
      "avg_response_time_seconds": 180.5,
      "closure_time_hours": 4.2
    }
  }
}
```

#### 指标说明

| 指标 | 含义 | 计算口径 |
|------|------|----------|
| `detection_rate` | 检出率 | TP / (TP + FN) |
| `false_positive_rate` | 误报率 | FP / (FP + TN) |
| `avg_response_time_seconds` | 平均响应时长 | 风险事件 → 首次处置动作 |
| `closure_time_hours` | 平均闭环时长 | 风险事件 → P9 闭环 |

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `TASK_NOT_FOUND` | `task_id` 为空 | False |

---

### 8. `archive_suggestions`

#### 用途

生成规则修订与模型迭代建议，反哺**规则管理系统**与**模型训练平台**。

#### 签名

```python
def archive_suggestions(task_id: str) -> str:
```

#### 入参

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | `str` | ✅ | 任务唯一标识 |

#### 建议类型

| 类型 | 说明 | 下游消费方 |
|------|------|----------|
| `rule_modify` | 规则修订建议（如检测频次、阈值） | 规则管理系统 |
| `rule_add` | 规则新增建议 | 规则管理系统 |
| `model_update` | 模型迭代建议（减少误报 / 提升检出） | 模型训练平台 |

#### 出参（成功）

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "tool": "archive_suggestions",
  "data": [
    {
      "rule_id": "R-001",
      "type": "rule_modify",
      "description": "建议增加受限空间作业气体检测频次，从每30分钟一次改为每15分钟一次",
      "priority": "high"
    },
    {
      "rule_id": null,
      "type": "model_update",
      "description": "建议优化CV模型，减少管道反光误报",
      "priority": "medium"
    },
    {
      "rule_id": "R-002",
      "type": "rule_add",
      "description": "建议新增规则：高空作业前必须确认安全带挂点",
      "priority": "medium"
    }
  ]
}
```

#### 错误码

| code | 触发条件 | recoverable |
|------|---------|-------------|
| `TASK_NOT_FOUND` | `task_id` 为空 | False |

## 知识沉淀路径

### 数据汇聚路径（🆕 2026-08-20）

```
[LLM] 用户说"复盘 task_id=XXX"
        │
        ▼
   read_pipeline_data(task_id)
        │
        ▼
   一次性读 P1-P10 各阶段 p{n}_result.json
        │
        ▼
   { P1: {available, path, data},
     P2: {...}, ..., P10: {...} }
        │
        ▼
   [LLM] 拿到全量数据，进入分析 / 报告生成
```

### 模板管理路径（🆕 2026-08-20）

```
   upload_template(name, content)
        │
        ▼
   agents/templates/archive/{name}.md.tmpl   （全局模板存储）
        │
        ▼
   update_system_prompt(new_content, template_name)
        │
        ▼
   agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md
        │
        ├──→ 备份旧版本 → *.bak.{timestamp}
        └──→ 拼装新内容（含激活模板）
        │
        ▼
   下次 load_system_prompt("P10") 自动加载新版本
```

### 报告生成路径（🆕 2026-08-20）

```
   archive_report(task_id, template_name?)
        │
        ├──→ read_pipeline_data(task_id)        （获取 P1-P10 全量数据）
        ├──→ load_system_prompt("P10")          （获取当前激活规则）
        └──→ load_template(template_name)       （获取报告模板）
        │
        ▼
   LLM.invoke(messages=[SystemMessage, HumanMessage(data, template)])
        │
        ▼
   Markdown 报告 ──→ data/jobs/{task_id}/archive_report.md
```

### 复盘输出路径（已实现）

```
P1-P9 全过程数据
       │
       ▼
   archive_task        ──→ 长期存储（结构化 + 索引）
       │
       ├──→ archive_cases       ──→ 案例摘要 Embedding ──→ 向量数据库
       │                          规则冲突报告        ──→ 规则管理系统
       │
       ├──→ archive_performance ──→ 处置效果看板
       │
       └──→ archive_suggestions ──→ 规则修订/新增     ──→ 规则管理系统
                                   模型迭代建议        ──→ 模型训练平台
```

## 在主流程中的位置

```
P1 作业许可 ─→ P2 作业任务 ─→ P3 上下文 ─→ P4 监测绑定 ─→ P5 条件核验
   ↓
P6 动态监测 ─→ P7 风险研判 ─→ P8 人机处置 ─→ P9 闭环报告 ─→ P10 归档复盘
                                                                  ↑
                                                              （当前阶段）
```

## 标准响应 schema

所有工具返回的 JSON 都遵循 [`agents/utils/response_utils.py`](../../agents/utils/response_utils.py)：

```python
SCHEMA_VERSION = "1.0"

def make_response(tool_name: str, data: dict) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": "ok", "tool": tool_name, "data": data}

def make_error(code: str, message: str, recoverable: bool = False) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": "error",
            "error": {"code": code, "message": message, "recoverable": recoverable}}
```

成功响应：
```json
{"schema_version": "1.0", "status": "ok", "tool": "<tool_name>", "data": {...}}
```

错误响应：
```json
{"schema_version": "1.0", "status": "error", "error": {"code": "...", "message": "...", "recoverable": false}}
```

## 文件位置

- Agent 实现：[`agents/p10_archive_agent.py`](../../agents/p10_archive_agent.py)
- 系统提示词：[`agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md`](../../agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md)
- 响应工具：[`agents/utils/response_utils.py`](../../agents/utils/response_utils.py)
- 系统提示词加载器：[`agents/utils/system_prompt.py`](../../agents/utils/system_prompt.py)
- 🆕 模板目录（待创建）：`agents/templates/archive/`（`upload_template` 落盘位置）
- 🆕 报告输出位置：`data/jobs/{task_id}/archive_report.md`（`archive_report` 落盘位置）

## 待实现说明

> 本节记录 4 个规划工具的实现优先级与依赖，便于排期与代码 review。

| 工具 | 实现优先级 | 依赖项 | 预估代码量 |
|------|------------|--------|------------|
| `read_pipeline_data` | P0（最先做） | 各阶段 `p{n}_result.json` 路径约定；`Path` + `json.load` | ~50 行（含错误处理） |
| `upload_template` | P1 | `agents/templates/archive/` 目录创建；`save_system_prompt` 模式参考 | ~40 行 |
| `update_system_prompt` | P1 | `agents/utils/system_prompt.py:save_system_prompt` 已实现，可直接复用 | ~30 行（含备份逻辑） |
| `archive_report` | P2 | 依赖 `read_pipeline_data` + `upload_template` + `update_system_prompt` | ~80 行（LLM 组装 + 落盘） |

### 实现顺序建议

```
1. read_pipeline_data       ← 无外部依赖，先打通数据通路
2. upload_template           ← 创建目录 + 简单写入
3. update_system_prompt      ← 复用 save_system_prompt + 加备份
4. archive_report            ← 串联 1+2+3 + LLM 调用 + Markdown 落盘
```

### 待澄清问题（实现前需确认）

1. **路径约定**：`data/jobs/{task_id}/p{n}_result.json` 是否所有 P 阶段统一？目前已知 P6/P8 有自己的目录结构（如 `data/jobs/{job_id}/P8/archived.json`），需要核对
2. **模板格式校验**：`upload_template` 是否需要 JSON Schema 校验？还是直接信任内容？
3. **LLM 模型选择**：`archive_report` 用同一个 chat_model 还是专门 prompt 调优？
4. **报告文件命名**：固定 `archive_report.md` 还是支持自定义文件名 + 多次生成追加时间戳？
5. **HITL 边界**：模板上传后是否需要二次确认"激活"操作？目前设计是 `upload_template` + `update_system_prompt` 两次确认

## 相关文档

- [P9_CLOSURE_AGENT.md](P9_CLOSURE_AGENT.md) — P10 的上一阶段（闭环报告）
- [P8_DISPOSITION_AGENT.md](P8_DISPOSITION_AGENT.md) — 处置全记录的来源
- [P7_RISK_AGENT.md](P7_RISK_AGENT.md) — 风险事件记录的来源
- [P6_MONITOR_AGENT.md](P6_MONITOR_AGENT.md) — 监测事件记录的来源
- [P8_DISPOSITION_TOOLS.md](P8_DISPOSITION_TOOLS.md) — 标准响应格式与 HITL 矩阵参考模板
- [HUMAN_IN_THE_LOOP.md](HUMAN_IN_THE_LOOP.md) — 两层 HITL 机制总览
- [agents/utils/system_prompt.py](../../agents/utils/system_prompt.py) — `load_system_prompt` / `save_system_prompt` 复用入口