# P8 人机协同处置：需求梳理与 Demo 设计

> 本文档描述 P8 的**职责边界**与**应当演示的能力**，是"需求视角"的设计文档。
> 与 [docs/agents/P8_DISPOSITION_AGENT.md](agents/P8_DISPOSITION_AGENT.md)（"工具视角"的接口规格）**并存互补**。
>
> 关联代码：[agents/p8_disposition_agent.py](../agents/p8_disposition_agent.py)（待实现） ·
> [agents/channel_gateway_client.py](../agents/channel_gateway_client.py)（飞书通道） ·
> [agents/main_agent.py:509-553](../agents/main_agent.py#L509) execute_p8 ·
> [docs/spec/CLI-SPEC/contract.yaml](spec/CLI-SPEC/contract.yaml) 工具契约
>
> **v2 架构重构**（2026-08-13）：核心思路转变 — P8 agent 通过**工作记忆 + LLM 自主决策**维护状态，
> HITL 与 P8_job 一一绑定，长期记忆按需调取。废弃外部单例事件总线与 watcher 推送机制。

---

## 1. 一句话定位

**P8 是 P7 风险研判的"执行调度器"**：通过**工作记忆**承载当前所有 in-progress P8_job，
由 **LLM 自主决策**调用工具推进 P8_job 状态；每个 P8_job 决定走**飞书推送**还是 **HITL 中断**；
已完成的 P8_job 归档到**长期记忆**，用户明确要求才调取。

| 概念 | 定义 |
|------|------|
| **a6_event_id** | A6 阶段对"一个违规生命周期"分配的标识符（`A6-YYYYMMDD-HHMMSS-millis`）；由 P7 透传给 P8 |
| **risk_event** | P7 输出 `p7_result.json.risk_events[*]`，每一项 = 一个 a6_event_id + 归一化字段 |
| **P8_job**（p8_job_id） | **N 个同时间的 risk_event**（N≥1）+ 处理状态 + 通道决策（HITL / PUSH）的处理单元；与 risk_event 是 **N:1 映射** |
| **风险叠加** | 同一时刻多个 risk_event 合并为 1 个 P8_job；由 LLM 自主判断（不写死 if/else） |
| **P8_job 队列** | 工作记忆中的 P8_job 列表（LangGraph state），LLM 自主维护入队 / 出队 / 阻塞 / 聚合 |
| **工作记忆** | 当前 in-progress P8_job 的结构化状态；每次 invoke 自动注入 context |
| **长期记忆** | 已完成 P8_job 的历史；**不直接进 context**，用户明确查询时通过 `recall_jobs` 调取 |
| **HITL 中断** | 与单个 P8_job 绑定：阻塞该 P8_job 等人工决策；决策到达后唤醒 LLM 继续处理 |

---

## 2. P8 在 P1–P10 工作流中的位置

```
P1 许可证 → P2 任务 → P3 上下文 → P4 绑定 → P5 核验
                                            ↓
                                       【作业开始】
                                            ↓
P6 监测（发现问题） ──→ P7 研判（判断风险等级）
                                ↓
                          【P8 人机协同处置】   ← 本文档
                                ↓
                          P9 闭环（跟踪处置完成）
                                ↓
                          P10 归档
```

### 职责边界（避免越界）

| 阶段 | 职责 | 不做什么 |
|------|------|----------|
| P6 监测 | 发现异常、写日志 | 不下决策 |
| P7 研判 | 判断风险等级、给建议 | 不通知人 |
| **P8 处置** | **匹配责任岗位、推送整改/暂停/升级/恢复指令** | **不重新评估风险** |
| P9 闭环 | 跟踪处置是否完成 | 不下新指令 |

---

## 3. 核心职责（基于关键词）

| # | 能力 | 关键词映射 | 触发条件 |
|---|------|-----------|---------|
| 1 | **组织整改** | "组织整改" | LOW/MEDIUM 风险 |
| 2 | **暂停作业** | "暂停"、"下发暂停" | HIGH/CRITICAL 风险 / 紧急情况 |
| 3 | **升级处置** | "升级" | 当前责任人无法处理 / 风险持续 |
| 4 | **下发处置要求** | "推送处置要求"、"下发" | 所有处置 P8_job 创建后 |
| 5 | **恢复作业** | "恢复" | 风险解除、整改完成 |

---

## 4. P8 Agent 内部架构 ⭐（核心：双层记忆 + LLM 自主决策）

### 4.1 双层记忆模型

| 层级 | 范围 | 加载方式 | 在 P8 决策中的作用 |
|------|------|---------|------------------|
| **工作记忆** | 当前 in-progress P8_job + P7 输出事件 + 实时状态 | **始终在 context**（每次 invoke 自动注入） | LLM 自主决策依据 |
| **长期记忆** | 已完成 P8_job + 历史处置 | **按需调取**（用户明确要求时通过 `recall_jobs` 工具） | 历史追溯；不污染当前决策 |

**为什么分两层？**

- **工作记忆常驻**：LLM 不丢失当前进度；每次 invoke 都能看到所有 in-progress P8_job 的最新状态
- **长期记忆不常驻**：已完成 P8_job 不污染 context；按需精确查询（避免 token 浪费与决策干扰）

### 4.2 P8_job 概念

**P8_job = N 个同时间的 risk_event + 1 个处理状态 + 1 个通道决策**（N ≥ 1）。

**支持风险叠加**：同一时刻出现的多个 risk_event 可由 LLM 聚合为 1 个 P8_job，刻画"多个事件并发"的复合风险；N=1 即退化为单事件处置。

工作记忆中的 P8_job 结构：

```jsonc
{
  "p8_job_id": "P8J-20260813-180000-001",  // P8 自生成（不复用 a6_event_id，因为可能聚合多个）
  "a6_event_ids": [                          // 该 P8_job 关联的所有 risk_event 源（A6 输出 ID）
    "A6-20260813-094525-230",
    "A6-20260813-094525-450",
    "A6-20260813-094525-680"
  ],
  "risk_basis": "3 人同时段 PPE 缺失 + 噪声超标 + 进入受限区域（风险叠加）",
  "max_level": "HIGH",                       // 多个 risk_event 的最高等级（用于决策）
  "level_distribution": { "HIGH": 1, "MEDIUM": 2 },  // 等级分布（可选，便于审计）
  "urgency_emoji": "🟠",
  "assignee_role": "车间主任",
  "status": "notified",          // pending / notified / waiting_decision / completed / rejected / escalated / resumed
  "channel": "HITL",             // HITL / PUSH — 决定走哪种决策通道
  "decision": null,              // approve / reject / escalate / rectify / pause / resume（决策到达后填入）
  "note": "",                    // 自由文本备注（决策者、原因等）
  "due_at": "2026-08-13T19:00:00Z",
  "created_at": "2026-08-13T18:00:00Z"
}
```

**关键字段**：

| 字段 | 含义 |
|------|------|
| `p8_job_id` | P8 自生成的全局唯一 ID，格式 `P8J-YYYYMMDD-HHMMSS-NNN` |
| `a6_event_ids` | 该 P8_job 关联的所有 A6 输出 ID（**数组**；N=1 时只有一个元素） |
| `max_level` | 多个 risk_event 风险等级的最高值（用于通道决策 / due_at 计算） |
| `level_distribution` | 等级分布（可选，便于追溯叠加构成） |

> **N:1 关系的实际意义**：
> - 一个 P8_job 内部的 HITL 决策**一次性**处理所有 risk_event
> - 飞书推送的"风险事件 ID"字段列出多个 a6_event_id（用逗号分隔）
> - 归档时 P8_job 整体归档，但 `a6_event_ids[]` 仍保留追溯链

**P8_job 生命周期**：

```
pending  ──update_job(notified)──►  notified
                                       │
                                       ├── channel=HITL + hitl_decide()──► waiting_decision
                                       │                                       │
                                       │                                       │ 决策到达（框架自动唤醒 LLM）
                                       │                                       ├── approve/rectify ──► completed  ─┐
                                       │                                       ├── reject          ──► rejected   ─┤
                                       │                                       ├── escalate        ──► escalated  ─┼─► P8ArchiveMiddleware
                                       │                                       └── resume          ──► resumed    ─┘   (自动归档到长期记忆)
                                       │
                                       └── channel=PUSH + notify_feishu()  ──► completed  ──► P8ArchiveMiddleware
                                                                                       (自动归档到长期记忆)
```

### 4.3 HITL 与 P8_job 绑定

**每个 HITL 中断 = 一个 P8_job 阻塞**。

- LLM 调 `hitl_decide(p8_job_id, options)` → 框架中断 agent 执行
- 当前 P8_job 状态 = `waiting_decision`
- 决策到达（CLI / Chat / API 任一通道）→ 框架**自动唤醒** agent
- 唤醒后 LLM 看到 working memory 里 `decision` 字段已更新 → 调 `update_job` 推进状态

> HITL 中断与 P8_job 一一对应，废弃多通道决策源 + 单例事件总线 + watcher 推送机制。

### 4.4 LLM 自主决策权

**关键原则**：**不写死 if/else**。每次 invoke，LLM 看到 working memory + 工具 description，**自主决定**下一步。

典型决策路径（LLM 推理过程）：

| LLM 看到的上下文 | LLM 推理 | 调用的工具 |
|----------------|---------|-----------|
| working memory 为空 | "需要先读 P7 建立初始 P8_job 列表" | `read_p7_events(job_id)` |
| 看到 3 个同时间 first_seen / last_seen 重叠的 risk_event | "3 人同时段 PPE+噪声+受限区，**风险叠加** → 合并为 1 个 P8_job" | `update_job(a6_event_ids=[id1,id2,id3], status="notified")`（聚合入队） |
| 看到 1 个 event level=HIGH | "HIGH 风险需 HITL 中断决策" | `update_job(a6_event_ids=[id], status="notified")` + `update_job(channel="HITL")` + `hitl_decide(p8_job_id, options)` |
| 看到 1 个 event level=LOW | "LOW 风险走 PUSH 直达即可" | `update_job(a6_event_ids=[id], status="notified", channel="PUSH")` + `notify_feishu(p8_job_id, message)` |
| 看到聚合 P8_job max_level=HIGH | "聚合后等级升级为 HITL 阈值，需统一决策" | `update_job(channel="HITL")` + `hitl_decide(p8_job_id, options=["rectify", "escalate"])` |
| 看到 working memory 中 `decision=approve` | "决策到了，推进 P8_job 状态（含聚合时所有 a6_event_id 一次性处理）" | `update_job(p8_job_id, status="completed")`（**归档由 P8ArchiveMiddleware 自动触发**，LLM 不调 archive_job） |
| 看到所有 P8_job 都 completed / archived | "本轮所有 P8_job 处理完" | （不调工具，输出完成总结） |
| 用户问"昨天那个事件最后怎么处理的" | "需要查长期记忆" | `recall_jobs(query="昨天 可燃气体")` |

---

## 5. 提示词架构（两类 + 动态注入区）

> **设计原则**：P8 提示词**不再保留"极简版"**——所有内容通过 markdown 文件加载，避免代码内常量与文档脱节。
>
> 提示词分**两类静态内容** + 一个**动态注入区**，按"面向开发 / 面向业务"职责分离。

| 类别 | 面向 | 文件位置 | 内容范围 | 修改频率 | 维护者 |
|------|------|---------|---------|---------|--------|
| **系统提示词**（System Prompt） | 开发人员 | `agents/system_prompt/P8_SYSTEM_PROMPT.md` | **原始 JSON 结构说明**：A6 输出 schema / P7 `risk_event` schema / P8 工作记忆 schema / P8_job 字段含义 / 工具签名 / 状态机 | 低频（随代码 schema 变化） | **开发**（与代码同步） |
| **规则提示词**（Rules Prompt） | 业务人员 | `agents/rules/P8_RULES.md`（或 `config/p8_rules.yaml`） | **任务执行规则**：风险叠加判定规则 / 责任岗位映射 / 推送策略表 / `due_at` 计算 / HITL vs PUSH 阈值 / 业务边界 / 行业合规 | 高频（随业务调整） | **业务**（可独立修改，无需发版） |
| **动态注入区** | 框架 | LangGraph state 自动追加 | `walltime` + 工作记忆快照（P8_job 列表）+ 长期记忆调取标记 | 每次 invoke | 框架 |

### 5.1 两类提示词的职责边界

```
┌──────────────────────────────────────────────────────────────┐
│                       最终 system message                     │
│                                                              │
│  ┌────────────────────────────────────┐                      │
│  │  [1] 系统提示词（面向开发）         │  ← 代码侧：JSON 结构 │
│  │  - A6 输出 schema 是什么样子       │     / 工具签名 / 状态机│
│  │  - P7 risk_event 字段含义           │                      │
│  │  - P8_job 工作记忆 schema           │                      │
│  │  - 6 个工具的入参出参               │                      │
│  │  - P8_job 状态机                    │                      │
│  └────────────────────────────────────┘                      │
│  ┌────────────────────────────────────┐                      │
│  │  [2] 规则提示词（面向业务）         │  ← 业务侧：决策规则 │
│  │  - 哪些 risk_event 应该聚合？       │                      │
│  │  - HIGH/LOW 风险走 HITL 还是 PUSH？ │                      │
│  │  - 责任岗位怎么映射？               │                      │
│  │  - due_at 怎么算？                  │                      │
│  │  - 哪些操作需要人工 gate？          │                      │
│  │  - 行业合规要求                     │                      │
│  └────────────────────────────────────┘                      │
│  ┌────────────────────────────────────┐                      │
│  │  [3] 动态注入区（框架侧）           │  ← 运行时上下文     │
│  │  - walltime（ISO8601）              │                      │
│  │  - 工作记忆快照（in-progress P8_job）│                     │
│  │  - 长期记忆调取标记                 │                      │
│  └────────────────────────────────────┘                      │
└──────────────────────────────────────────────────────────────┘
```

**为什么这样分？**

| 解耦点 | 收益 |
|--------|------|
| **schema 与规则解耦** | 业务规则调整（"把 MEDIUM 改成走 PUSH 直达"）不需要改代码、不需要开发介入、不需要发版 |
| **开发与业务解耦** | 开发只维护 system prompt（与代码 schema 同步）；业务只维护 rules prompt（按场景调整） |
| **演进隔离** | JSON 字段新增（如 P8_job 新增 `escalation_count`）只影响 system prompt；规则调整（如"叠加等级阈值"）只影响 rules prompt |
| **可审计** | 业务规则的变更走 git diff，业务侧独立 review，不污染代码评审 |
| **可热加载** | 规则提示词未来可走配置中心（如 Nacos / Consul）实现运行时热更新 |

### 5.2 系统提示词模板（[1] 面向开发）

**文件**：`agents/system_prompt/P8_SYSTEM_PROMPT.md`

**职责**：告诉 LLM **数据长什么样、工具怎么调、状态怎么变**。

```markdown
# P8 Agent 系统提示词（面向开发）

## 角色
你是人机协同处置 Agent，负责把 P7 输出的 risk_event 转化为可执行的处置任务。

## 数据结构（你需要理解的三类 JSON）

### A. A6 原始输出（参考，来自 `A6/logs/assessments/YYYYMMDD/a6_*.json`）
```jsonc
{
  "a6_event_id": "A6-20260813-094525-230",
  "event_type": "PPE缺失",
  "first_seen": "2026-08-13T09:44:51.700",
  "last_seen": "2026-08-13T09:44:59.700",
  "risk_level": 1,
  "risk_level_name": "轻微",
  "risk_basis": "...",
  "suggestions": [...],
  "evidence": {...}
}
```
- **唯一标识**：`a6_event_id`，格式 `A6-YYYYMMDD-HHMMSS-millis`
- **时间字段**：`first_seen` / `last_seen` 用于风险叠加判定

### B. P7 输出 risk_event（来自 `p7_result.json.risk_events[*]`）
```jsonc
{
  "event_id": "A6-20260813-094525-230",   // = a6_event_id
  "a6_event_id": "A6-20260813-094525-230",
  "level": "LOW",                          // LOW/MEDIUM/HIGH/CRITICAL
  "risk_level": 1,
  "risk_basis": "...",
  "suggestions": [...],
  "first_seen": "2026-08-13T09:44:51.700",
  "last_seen": "2026-08-13T09:44:59.700"
}
```

### C. P8 工作记忆 P8_job（你维护的对象）
```jsonc
{
  "p8_job_id": "P8J-20260813-180000-001",   // 你生成；格式 P8J-YYYYMMDD-HHMMSS-NNN
  "a6_event_ids": ["A6-...", "A6-..."],      // 数组，N≥1，支持风险叠加
  "max_level": "HIGH",                       // 数组中最高等级
  "level_distribution": {"HIGH": 1, "MEDIUM": 2},
  "status": "notified",
  "channel": "HITL",
  "decision": null,
  "due_at": "2026-08-13T19:00:00Z",
  "created_at": "2026-08-13T18:00:00Z"
}
```

## 工具签名（5 个）
- `read_p7_events(job_id)` → 读 P7 输出写入工作记忆
- `update_job(a6_event_ids, p8_job_id=None, status=None, channel=None, note=None)` → 新建/更新 P8_job
- `hitl_decide(p8_job_id, options)` → HITL 中断等人工决策
- `notify_feishu(p8_job_id, message)` → 飞书推送（不阻塞）
- `recall_jobs(query)` → 长期记忆查询（按需）

> ❌ **`archive_job` 不在工具集中**。归档是框架机制（`P8ArchiveMiddleware`），在 status 进入终态时自动触发；LLM 不应、也不需要调此动作。

## P8_job 状态机
```
pending → notified → waiting_decision → {completed | rejected | escalated | resumed} ┐
                                  ↘ (channel=PUSH 直达) notified → completed       ┴─► P8ArchiveMiddleware (自动归档)
```

## 双层记忆
- **工作记忆**：当前 in-progress P8_job（始终在 context）
- **长期记忆**：已完成 P8_job（不直接进 context；用户明确查询时调 `recall_jobs`）
- **归档是框架机制，不是 LLM 工具**：status 进入终态时由 `P8ArchiveMiddleware` 自动 working → long_term + 从 working_memory 移除（详见 § 6.4）

## 边界（不要越界）
- ❌ 不重新评估风险（P7 的事）
- ❌ 不做实时监测（P6 的事）
- ❌ 不验证整改结果（P9 的事）
- 越界时直接告知用户并转交对应阶段
```

### 5.3 规则提示词模板（[2] 面向业务）

**文件**：`agents/rules/P8_RULES.md`（或 `config/p8_rules.yaml`）

**职责**：告诉 LLM **在什么场景下做什么决策**。

```markdown
# P8 业务规则提示词（面向业务）

## 1. 风险叠加判定规则（哪些 risk_event 合并为 1 个 P8_job）

满足以下任一条件，**应**聚合为 1 个 P8_job：
- **时间窗口重叠**：多个 risk_event 的 `first_seen` / `last_seen` 在同一窗口内（默认 60s）
- **空间/人员重叠**：同一作业区域或同一人员的多类型违规
- **风险类型互补**：PPE + 噪声 + 受限区 = 协同违规场景
- **严重度提升**：合并后 `max_level` 触发更高决策通道阈值

不满足上述条件的 risk_event **保持独立**（N=1）。

## 2. 责任岗位映射

| `max_level` | 默认责任岗位 | 飞书消息前缀 |
|------------|------------|------------|
| `LOW`      | 作业负责人   | 🟢 `[一般]` |
| `MEDIUM`   | 班组长       | 🟡 `[重要]` |
| `HIGH`     | 车间主任     | 🟠 `[紧急]` |
| `CRITICAL` | 厂长 / HSE 经理 | 🔴 `[🚨危急]` |

实际责任岗位可由用户在前端手动指定；LLM 看到用户指令时按指令执行。

## 3. 通道决策（HITL vs PUSH）

| 场景 | 默认 channel | 说明 |
|------|-------------|------|
| 单 event max_level ≥ HIGH | HITL | 高风险必须人工确认 |
| 单 event max_level ≤ MEDIUM | PUSH | 低风险消息直达即可 |
| 聚合 P8_job（任意 N>1） | HITL | 多事件合并需综合决策 |
| 用户明确说"直接推送" | PUSH | 覆盖默认 |
| 用户明确说"等决策" | HITL | 覆盖默认 |

## 4. due_at 计算

按 `max_level` 对应的 `RISK_PUSH_STRATEGY`：

| `max_level` | due_at 距 walltime |
|------------|-------------------|
| `LOW`      | 24h |
| `MEDIUM`   | 4h  |
| `HIGH`     | 1h  |
| `CRITICAL` | 0h（实时） |

## 5. 哪些操作必须走 HITL（人工 gate）

- 创建 P8_job（避免误创建）
- 所有状态变更（防止误推进）
- 归档到长期记忆（防止误删）

## 6. 行业合规

- 所有 P8_job 必须保留 `a6_event_ids[]` 数组至少 90 天（审计要求）
- 聚合 P8_job 必须记录"聚合依据"到 `note` 字段（追溯要求）
- 所有 HITL 决策必须记录决策者 ID（`note` 字段）

## 7. 何时调 `read_p7_events`（拉取 P7 数据）

> P8 工作期间 P6/A6/P7 是持续运行的，`p7_result.json.risk_events[]` 会被持续追加。
> LLM 应按触发模式区分对待（详见 § 7.8）。

| 触发模式 | 调 `read_p7_events`？ | 时机 |
|---------|---------------------|------|
| **用户正常对话** | ✅ **按需** | 仅当用户消息隐含或明确需要 P7 数据时（见下） |
| **主流程自动调用** | ⚠️ **不归 LLM 管** | `execute_p8` 后台线程负责；LLM 不调此工具 |
| **用户明确要求** | ✅ **立即调** | 用户问"刚才还有新的吗？"时立即拉取 |

### 7.1 用户正常对话 — 调 `read_p7_events` 的判定

✅ **应该调**（隐含需要 P7 数据）：

- 用户问"现在有哪些 P8_job？" → 调（看 P7 是否有未入队的 risk_event）
- 用户说"为 X 创建 P8_job" → 调（X 是 a6_event_id，需要确认已在 P7）
- 用户问"刚才那批噪声超标处理了吗？" → 调（找噪声相关 risk_event 状态）
- 用户问"现在还有未处置的吗？" → 调（看 P7 是否有遗漏的）

❌ **不应该调**（用户已有上下文）：

- 用户在确认/推进某个已知 P8_job（"RE-001 批准"）
- 用户查询某个 P8_job 状态（"RE-002 进展如何？"）
- 用户在说无关的事（"今天天气怎么样"）
- 用户在做反思/总结（"今天哪些高风险？")

### 7.2 主流程自动调用 — 不调 `read_p7_events`

- LLM **不调** `read_p7_events`，由 `execute_p8` 后台线程负责
- LLM 通过 HumanMessage 接收"【P7 增量】"通知，自主决定是否入队
- 若 LLM 主动调 `read_p7_events`，工具会返回"主流程模式下请等待后台注入"提示（避免与后台线程冲突）

### 7.3 用户明确要求 — 立即调

- 用户消息包含"新的"、"最新"、"现在"、"刚才"、"增量"等关键词 → LLM 立即调
- 用户消息包含"全部"、"所有"、"列表" → LLM 立即调
```

### 5.4 动态注入区（[3] 框架侧）

**位置**：每次 `agent.invoke()` 前由框架自动追加。

**内容示例**：

```
【当前上下文】
- walltime: 2026-08-13T18:00:00Z    ← 工厂调用时注入，用于 due_at 计算
                                       job_id 不注入 prompt；它由 HumanMessage 内容传入，
                                       LLM 自行从消息中解析或从 read_p7_events(job_id=...) 反推

【工作记忆】（in-progress P8_jobs，LangGraph state 自动注入）
- 每条 = 1 个 P8_job（可能聚合 N 个 risk_event）；标识字段为 p8_job_id
- P8J-20260813-180000-001 [HIGH] 🟠 车间主任    ← 单事件 P8_job（N=1）
    a6_event_ids: ["A6-20260813-094525-230"]
    status: waiting_decision | channel: HITL | decision: null
    risk_basis: 可燃气体浓度超标（CH4 4.8%）
- P8J-20260813-180000-002 [HIGH] 🟠 车间主任    ← 聚合 P8_job（N=3，风险叠加）
    a6_event_ids: ["A6-...-450", "A6-...-680", "A6-...-910"]
    status: notified | channel: HITL | decision: null
    risk_basis: 3 人同时段 PPE 缺失 + 噪声超标 + 进入受限区域

【长期记忆】未调取（按需）
```

### 5.5 加载与组合顺序

```python
# agents/p8_disposition_agent.py
def build_system_prompt(walltime: str | None = None) -> str:
    """组合三类提示词 + 动态注入区。"""
    system_prompt = load_system_prompt("P8")        # [1] 面向开发（代码侧 schema）
    rules_prompt = load_rules_prompt("P8")          # [2] 面向业务（业务侧规则）
    dynamic_section = build_dynamic_section(walltime)  # [3] 框架侧上下文

    return "\n\n".join([system_prompt, rules_prompt, dynamic_section])
```

**拼装顺序**：[1] 系统 → [2] 规则 → [3] 动态；保证 LLM 先理解数据，再理解业务，最后看当前上下文。

---

## 6. 工具集（5 个 `@tool`）

### 6.1 工具总览

按"**读 / 写 / 中断 / 推送 / 回忆**"五维分类：**归档是框架机制，不是 LLM 工具**。

| # | 工具 | 签名 | HITL | 职责 |
|---|------|------|------|------|
| 1 | `read_p7_events` | `(job_id: str)` → `str` | ❌ | 读 P7 输出写入 working memory（job_id = 主流程作业 ID） |
| 2 | `update_job` | `(a6_event_ids: list[str], p8_job_id=None, status=None, channel=None, note=None)` → `str` | ❌ | 新建 / 更新工作记忆中某 P8_job 的字段；`a6_event_ids` 是关联的 A6 输出 ID 数组（支持聚合） |
| 3 | `hitl_decide` | `(p8_job_id, options: list[str])` → `str` | ✅ | HITL 中断等人工决策 |
| 4 | `notify_feishu` | `(p8_job_id, message)` → `str` | ❌ | 飞书单通道推送 |
| 5 | `recall_jobs` | `(query: str)` → `str` | ❌ | 从长期记忆查询历史 P8_job |

> **❌ `archive_job` 不再作为 LLM 工具**
>
> "P8_job 进入终态 → 归档到长期记忆"是**确定性副作用**，由 LangGraph middleware 在状态变更时**自动触发**；LLM 不应、也不需要显式调此动作（详见 § 6.4 归档机制）。

> **参数命名严格区分**：
> - `job_id`（仅 `read_p7_events` 使用）= 主流程作业 ID，**用于读取筛选**
> - `p8_job_id`（其余 4 个工具使用）= P8 内部 P8_job 的标识符（即一个 risk_event 的 ID）
>
> 查询类（"查看 D-001 的状态"）走 REST API，不下沉到工具层——它们是给人看的，不是给 agent 用的。

### 6.2 各工具 description（提示 LLM 何时调用）

```python
@tool(description=(
    "读 P7 输出 risk_events 写入工作记忆。"
    "启动时必调一次建立初始 P8_job 列表；后续若需要刷新 P7 数据也可调用。"
    "参数 job_id 是主流程的作业 ID（用于定位 P7 输出文件），不是 P8_job ID。"
    "返回每个 risk_event 含 event_id(=a6_event_id)/first_seen/last_seen/risk_basis/level，"
    "LLM 据此自主决定是否聚合多个同时间 risk_event 为 1 个 P8_job。"
))
def read_p7_events(job_id: str) -> str: ...


@tool(description=(
    "新建或更新工作记忆中某 P8_job 的字段。"
    "p8_job_id: 已有 P8_job 标识符（更新时用）；新建时省略，工具内部按 P8J-YYYYMMDD-HHMMSS-NNN 自生成。"
    "a6_event_ids: 该 P8_job 关联的 A6 输出 ID 列表（数组，N≥1）。"
    "  - N=1：单事件 P8_job，对应一个 risk_event。"
    "  - N>1：聚合 P8_job（风险叠加），对应多个同时间的 risk_event。"
    "status 可选: pending/notified/waiting_decision/completed/rejected/escalated/resumed。"
    "channel 可选: HITL/PUSH（决定走哪种决策通道；聚合 P8_job 通常走 HITL）。"
    "note: 自由文本备注（决策者、原因、聚合依据等）。"
))
def update_job(
    a6_event_ids: list[str],                     # 关联 A6 输出 ID 数组
    p8_job_id: str | None = None,                # None → 新建；已有 → 更新
    status: str | None = None,
    channel: str | None = None,
    note: str | None = None,
) -> str: ...


@tool(description=(
    "HITL 中断：阻塞当前 P8_job 等人工决策。"
    "options: 候选 action 列表（approve/reject/escalate/rectify/pause/resume）。"
    "聚合 P8_job 的 HITL 决策一次性作用于 a6_event_ids[] 内的全部 risk_event。"
    "决策到达后 LLM 会自动被唤醒，working memory 中 decision 字段会自动填入。"
))
def hitl_decide(p8_job_id: str, options: list[str]) -> str: ...


@tool(description=(
    "通过飞书单通道推送处置通知（系统自动，不阻塞 agent）。"
    "channel=PUSH 时调用此工具；channel=HITL 时改调 hitl_decide。"
    "聚合 P8_job 推送时，message 中应列出全部 a6_event_ids（详见 8.2 节聚合模板）。"
))
def notify_feishu(p8_job_id: str, message: str) -> str: ...


@tool(description=(
    "从长期记忆查询历史 P8_job。仅在用户明确要求时调用。"
    "query: 关键词（如'昨天可燃气体'或 p8_job_id）。结果作为临时返回，不写入工作记忆。"
    "可同时返回 P8_job 关联的 a6_event_ids[] 数组。"
))
def recall_jobs(query: str) -> str: ...
```

> LLM 通过这 5 个 `description` + 用户自然语言 + 工作记忆，**自主决定调用哪个工具**——不写死 if/else。
>
> **归档（archive_job）不在工具集中**——它是框架机制，见 § 6.4。

### 6.4 归档机制（框架层而非 LLM 工具）

```
                ┌─────────────────────────────────────┐
                │      LangGraph State                │
                │  ┌─────────────────────────────┐   │
                │  │  working_memory             │   │
                │  │  = [P8_job, P8_job, ...]    │ ◄─┐ 自动注入 context
                │  └─────────────────────────────┘   │
                │  ┌─────────────────────────────┐   │
                │  │  long_term_memory           │ ◄─┼─ recall_jobs（按需）
                │  │  = [archived P8_job, ...]   │   │
                │  └─────────────────────────────┘   │
                └─────────────────────────────────────┘
                                ▲    ▲    ▲    ▲
                                │    │    │    │
        ┌───────────────────────┼────┼────┼────┼──────────────────────┐
        │ 工具调用（5 个）：                                        │
        │   read_p7_events  ───► 写入 working_memory               │
        │   update_job      ───► 改 working_memory                 │
        │   hitl_decide     ───► 中断 → 决策到达 → 唤醒 LLM       │
        │   notify_feishu   ───► 推飞书（不阻塞）                 │
        │   recall_jobs     ───► long_term → 临时返回（不污染）   │
        └──────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────────────────┐
        │ 框架机制（非 LLM 工具）：                                 │
        │   _p8_archive_middleware ──► 监听到 P8_job 进入终态 →  │
        │                            自动 working → long_term     │
        │                            + 从 working_memory 移除     │
        └──────────────────────────────────────────────────────────┘
```

### 6.4 归档机制（框架层而非 LLM 工具）

> **核心结论**：`archive_job` 不再是 LLM 工具；它是**框架层 middleware**，在 P8_job 状态进入终态时自动触发。
>
> 原因：归档是**确定性副作用**（status 进终态 → 必归档），不应让 LLM 决策"何时归档"。

#### 6.4.1 触发机制

```
P8_job 状态变更
   │
   │ update_job(status="completed"|"rejected"|"escalated"|"resumed")
   ▼
_p8_archive_middleware（LangGraph middleware）
   │
   │ 1. 检测 working_memory 中所有 status ∈ _TERMINAL_STATUSES 的 P8_job
   │ 2. 自动汇总 summary（从 decision / note / confirmed_by 拼装）
   │ 3. 写入 long_term_memory[p8_job_id]
   │ 4. 从 working_memory 移除（context 不再加载）
   │
   ▼
state 更新完成，继续后续 invoke
```

#### 6.4.2 终态集合

```python
_TERMINAL_STATUSES = {"completed", "rejected", "escalated", "resumed"}
```

> 注释：`escalated` 是"升级"语义 —— 进入 escalated 状态的 P8_job 也会被自动归档到长期记忆；但同时会有新的 P8_job 创建（继承原 P8_job 的 `a6_event_ids[]`）进入循环。这是 § 4.2 状态机里 `escalated ─► update_job 重新进入循环` 的具体含义。

#### 6.4.3 middleware 实现示例

```python
# agents/p8_disposition_agent.py
from datetime import datetime, timezone

_TERMINAL_STATUSES = {"completed", "rejected", "escalated", "resumed"}


def _summarize_p8_job(job: dict) -> str:
    """从 P8_job 字段自动汇总 summary。"""
    parts = [
        f"max_level={job.get('max_level')}",
        f"decision={job.get('decision') or 'N/A'}",
        f"by={job.get('note', '').split('by ')[-1] if 'by ' in job.get('note', '') else 'system'}",
    ]
    if job.get("a6_event_ids") and len(job["a6_event_ids"]) > 1:
        parts.append(f"aggregated {len(job['a6_event_ids'])} events")
    return " | ".join(parts)


def _p8_archive_middleware(state):
    """LangGraph middleware：P8_job 终态 → 自动归档。"""
    working = state.get("working_memory", [])
    long_term = state.get("long_term_memory", {})

    new_working = []
    for job in working:
        if job.get("status") in _TERMINAL_STATUSES:
            archived_job = job | {
                "summary": _summarize_p8_job(job),
                "archived_at": datetime.now(timezone.utc).isoformat(),
            }
            long_term[job["p8_job_id"]] = archived_job
        else:
            new_working.append(job)

    state["working_memory"] = new_working
    state["long_term_memory"] = long_term
    return state
```

#### 6.4.4 工厂接入 middleware

```python
from langchain.agents.middleware import AgentMiddleware

class P8ArchiveMiddleware(AgentMiddleware):
    """包装 _p8_archive_middleware 为 LangGraph middleware。"""
    def after_agent(self, state, runtime):
        return _p8_archive_middleware(state)


def create_disposition_agent(walltime: str | None = None) -> CompiledGraph:
    return create_agent(
        model=create_chat_model(),
        tools=[read_p7_events, update_job, hitl_decide, notify_feishu, recall_jobs],
        system_prompt=build_system_prompt(walltime),
        middleware=[
            HumanInTheLoopMiddleware(interrupt_on={"hitl_decide": True}),
            P8ArchiveMiddleware(),    # ← 新增：自动归档
        ],
        checkpointer=MemorySaver(),
    )
```

#### 6.4.5 旧 `archive_job` 工具如何保留（开发者手动归档）

虽然 `archive_job` 不再是 LLM 工具，但**仍保留作为开发者手动归档接口**，用于 CLI / REST API 调试、批量运维、P10 归档阶段：

```python
# agents/p8_disposition_agent.py（不下发给 LLM）
def archive_job(p8_job_id: str, final_status: str, summary: str) -> dict:
    """开发者手动归档接口（CLI / REST API）。**不**作为 LLM 工具。

    LLM 不应调此函数；框架已通过 P8ArchiveMiddleware 自动处理。
    """
    return _force_archive(p8_job_id, final_status, summary)
```

> **关键边界**：开发者工具 ≠ LLM 工具。同名函数 `archive_job` 在两个层面共存，但 LLM 工具注册表（`@tool` 装饰器）不再包含它。

#### 6.4.6 改造前后对比

| 维度 | 旧设计（LLM 调 archive_job） | 新设计（框架 middleware） |
|------|--------------------------|------------------------|
| 触发方 | LLM 显式调 | 框架自动 |
| LLM 工具数 | 6 | **5** |
| 归档时机 | LLM 决策（可能忘） | 状态变更即归档（确定） |
| summary 来源 | LLM 填 | 框架从字段汇总 |
| 重复调风险 | 可能 | 无（middleware 只跑一次） |
| 调试入口 | LLM 工具 | 开发者手动函数 `archive_job()` |

---

## 7. 激活流程与执行流程

### 7.1 两种激活入口

| 入口 | 触发方 | 启动函数 | 用途 |
|------|-------|---------|------|
| **主流程自动模式** | `main_agent.execute_p8(job_id)` | `create_disposition_agent(walltime)` | 端到端流水线，无人值守批量处理 |
| **独立对话模式** | 用户在前端 / CLI 发起消息 | `disposition_demo(message, history)` | 人工补创建、调试、演示 |

### 7.2 注入数据清单（启动函数注入）

| 字段 | 来源 | 在 P8 决策中的作用 |
|------|------|------------------|
| `walltime` | `datetime.now(timezone.utc)` | 计算 `due_at`；状态机时间戳；注入 system prompt |
| `p7_result.risk_events[*]` | `p7_result.json`（由 `read_p7_events` 工具读入） | **核心数据源**：每个 risk_event 对应一个 P8_job |
| `job_id` | **消息内容**（非工厂参数） | 通过 HumanMessage 传入；LLM 调 `read_p7_events(job_id=...)` 时使用 |

> **两个易混的 ID 必须区分清楚：**
>
> | 层次 | 命名 | 含义 | 来源 |
> |------|------|------|------|
> | 主流程作业 | `job_id` | 一个完整作业的标识（如 `JOB-20260813-001`，覆盖 P1→P10） | `main_agent.execute_p8(job_id)` |
> | P8 内部工作单元 | `P8_job` / `p8_job_id` | N 个 risk_event 聚合后的处理单元（如 `P8J-20260813-180000-001`），格式 `P8J-YYYYMMDD-HHMMSS-NNN` | P8 内部 / LLM 工作记忆 |
>
> 主流程的 `job_id` **不作为工厂参数**——避免与内部 P8_job 概念命名冲突；
> 它通过 HumanMessage 内容传入，LLM 再调 `read_p7_events(job_id=...)` 把它当作"读取筛选条件"。

> **P7 输出不预先注入系统 prompt**，而是通过 `read_p7_events` 工具按需读入工作记忆。
> 这样保持架构一致性：所有外部数据都通过工具进入，LLM 拥有完整的工具调用自主权。

### 7.3 启动函数签名

```python
# agents/p8_disposition_agent.py

def create_disposition_agent(
    walltime: str | None = None,   # 当前时间（ISO8601），注入 system prompt 用于 due_at 计算
    # ❌ 不接受 job_id —— 主流程的作业 ID 不应进入 P8 内部状态
    # ❌ 不接受 thread_id —— 由调用方通过 invoke(config={...}) 自行指定
) -> CompiledGraph:
    """构造 P8 agent。

    工作记忆（P8_job 列表）由 LLM 通过工具自行维护：
      - read_p7_events(job_id) 把 P7 风险事件写入 working memory
      - update_job(p8_job_id, ...) 推进 P8_job 状态
      - P8ArchiveMiddleware 自动监听终态 → 归档（详见 § 6.4）
    """
    return create_agent(
        model=create_chat_model(),
        tools=[read_p7_events, update_job, hitl_decide, notify_feishu, recall_jobs],
        system_prompt=build_system_prompt(walltime),
        middleware=[
            HumanInTheLoopMiddleware(interrupt_on={"hitl_decide": True}),
            P8ArchiveMiddleware(),  # 框架层自动归档（详见 § 6.4）
        ],
        checkpointer=MemorySaver(),  # 跨 invoke 保持 working memory
    )


def disposition_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 入口（独立对话模式）。

    job_id 不通过工厂参数传入，而是嵌入到 message 中：
    message = f"[job_id={current_job_id}] {user_message}"
    LLM 收到后通过 read_p7_events(job_id=...) 工具读取。
    """
    return run_disposition_agent(message)
```

### 7.4 主流程自动模式时序（P7 → P8 → 批量处理）

```
P7 阶段完成，p7_result.json 写入 risk_events
  ↓
main_agent.execute_p8(job_id) 被自动调用
  ↓
构造 walltime = datetime.now(timezone.utc).isoformat()
  ↓
create_disposition_agent(walltime)               ← 仅传 walltime
  ↓
agent 系统 prompt 注入：walltime + "【工作记忆】空"
  ↓
agent.invoke(
    {"messages": [HumanMessage(content=f"处理作业 {job_id} 的 P7 风险事件")]},  ← job_id 通过消息内容传入
    config={"configurable": {"thread_id": f"p8-{job_id}"}},                     ← thread_id 用于 checkpointer
)
  ↓
LLM 看到 working memory 空 → 调 read_p7_events(job_id)                          ← job_id 从消息内容提取
  ↓
工具读 p7_result.json.risk_events → 写入 working memory
  ↓
LLM 遍历每个 event：
  ├─ update_job(p8_job_id, status="notified")
  ├─ 查 RISK_PUSH_STRATEGY → 决定 channel
  ├─ 若 channel=HITL → hitl_decide(p8_job_id, options=["approve","reject","escalate","rectify"])
  │     └─ 框架中断；状态 = waiting_decision
  └─ 若 channel=PUSH → notify_feishu(p8_job_id, message)
        └─ 推飞书后 update_job(status="completed")  ← 归档由 P8ArchiveMiddleware 自动触发
  ↓
agent 返回任务摘要
  ↓
execute_p8 写 p8_result.json.disposition_tasks（仅写"快照"；运行时状态在工作记忆）
  ↓
返回 main_agent，触发 pending_confirmation
```

### 7.5 独立对话模式时序

```
用户在前端 / CLI 输入："为 A6-20260813-094525-230 创建处置任务"
  ↓
disposition_demo(message, history) 被前端调起
  ↓
后端：解析 current_job_id（从前端会话上下文获取）；walltime = now()
  ↓
create_disposition_agent(walltime)               ← 仅传 walltime
  ↓
构造实际消息：HumanMessage(content=f"[job_id={current_job_id}] {user_message}")
  ↓
agent.invoke({"messages": [HumanMessage(content=...)]})
  ↓
LLM 看到工作记忆为空 → 调 read_p7_events(job_id=current_job_id)  ← 从消息中解析出 job_id
  ↓
工具读 P7 → 写入工作记忆（含 a6_event_id / first_seen / last_seen）
  ↓
LLM 判断为单事件处置（N=1，level=HIGH）：
  ├─ update_job(a6_event_ids=["A6-20260813-094525-230"], status="notified", channel="HITL")
  │   （工具内部生成 p8_job_id="P8J-..."）
  └─ hitl_decide(p8_job_id, options=["rectify","escalate"])
  ↓
agent 输出："已为 A6-20260813-094525-230 创建处置 P8_job (P8J-...)，等待人工决策"
  ↓
agent 空闲；等待 HITL 决策到达 → 框架自动唤醒
```

### 7.6 HITL 决策到达后的执行流程

```
HITL 决策到达（CLI / Chat / API 任一通道）
  ↓
框架自动写入 working memory：P8_job.decision = "approve"
  ↓
框架自动唤醒 agent.invoke()（同 thread_id）
  ↓
LLM 看到 working memory 中 decision 已更新
  ↓
LLM 推理："决策到了，推进 P8_job 状态"
  ↓
LLM 调：
  └─ update_job(p8_job_id, status="completed", note="by frontend:zhang")
  ↓
P8ArchiveMiddleware 自动监听 status="completed" → 归档到长期记忆 + 从 working_memory 移除
  ↓
LLM 检查其他 P8_job → 继续处理或输出完成总结
```

### 7.7 完整生命周期一览

| 阶段 | 主流程自动模式 | 独立对话模式 |
|------|--------------|------------|
| 触发 | `execute_p8(job_id)` | `disposition_demo(message)` |
| 工厂调用 | `create_disposition_agent(walltime)` | `create_disposition_agent(walltime)` |
| job_id 传入 | HumanMessage 内容（如 `"处理作业 {job_id} 的 P7 风险事件"`） | HumanMessage 内容（如 `"[job_id={...}] {user_msg}"`） |
| thread_id | `invoke(config={"configurable": {"thread_id": f"p8-{job_id}"}})` | `invoke(config={"configurable": {"thread_id": session_id}})` |
| 初始读取 | LLM → `read_p7_events(job_id=...)` | LLM → `read_p7_events(job_id=...)` |
| 决策循环 | LLM 遍历 events → `update_job` + `hitl_decide` / `notify_feishu` | LLM 按用户指令处理单个/多个 event |
| 决策响应 | 框架自动唤醒 → LLM → `update_job` → `P8ArchiveMiddleware` 自动归档 | 同左 |
| 终态 | 写 p8_result.json + 工作记忆清空（已归档） | agent 输出文本确认 |

### 7.8 何时关注新的 a6_event（事件关注策略）

> P8 工作期间 P6 监测 → A6 评估 → P7 研判是**持续运行**的，`p7_result.json.risk_events[]` 会被**持续追加**。
> P8 何时主动拉取新增 a6_event，按触发模式区分。

| 触发模式 | 关注新 a6_event？ | 实现机制 | 理由 |
|---------|-----------------|---------|------|
| **用户正常对话** | ❌ **不主动关注** | LLM 不主动调 `read_p7_events` 拉增量；只处理用户消息中提到的 P8_job / a6_event | 用户是来**操作**的（创建 / 确认 / 查询），不是来**监控**的；监控是 P6/P7 的事；新事件会污染工作记忆，分散用户当前对话注意力 |
| **主流程自动调用**（`execute_p8(job_id)`） | ✅ **全量关注** | 启动时**一次性** `read_p7_events(job_id)` 拉全量；处理期间由 `execute_p8` **周期性快照**触发增量拉取（默认 30s，可由 `p8_runtime.yaml` 配置） | 流程触发的批量处理有 SLA 要求（HIGH 风险 1h 内必须响应）；不关注会遗漏；启动全量 + 周期增量的组合保证初始 + 增量全覆盖 |
| **用户明确要求**（"刚才还有新的吗？"） | ✅ **按需关注** | 用户消息触发 LLM 调 `read_p7_events(job_id)`；LLM 通过对比工作记忆中已有的 `a6_event_ids[]` 与最新 `risk_events[*]` 发现增量 | 用户主动询问 → LLM 被动响应；不预先订阅、不轮询 |

**实现细节**：

#### A. 用户正常对话（不关注）

```python
# users/services/disposition_demo(message, history)
def disposition_demo(message: str, history: list = None) -> str:
    """独立对话入口。

    ❌ 不在入口拉取 P7 数据。
    ❌ 不启动任何轮询线程。
    ✅ 只把用户消息原样传给 LLM，由 LLM 自主决定是否调 read_p7_events。
    """
    return run_disposition_agent(message)
```

- LLM 在 prompt 中被规则提示词告知："用户正常对话场景下不要主动订阅新事件；用户明确询问时才拉取"
- 工作记忆不包含"订阅"或"轮询"状态

#### B. 主流程自动调用（全量关注）

```python
# agents/main_agent.py
def execute_p8(job_id: str) -> dict:
    """主流程 P8 入口。

    ✅ 启动时全量读 P7（一次性）。
    ✅ 处理期间周期快照 + 增量拉取（增量去重：跳过已在 a6_event_ids[] 里的）。
    ✅ 工作记忆持续更新；新 event 触发 LLM 再次决策。
    """
    walltime = datetime.now(timezone.utc).isoformat()
    agent = create_disposition_agent(walltime=walltime)
    # 触发 LLM 启动 + 全量读 P7
    initial_msg = HumanMessage(content=f"处理作业 {job_id} 的 P7 风险事件")
    config = {"configurable": {"thread_id": f"p8-{job_id}"}}

    result = agent.invoke({"messages": [initial_msg]}, config=config)

    # ⚠️ 增量快照周期由 execute_p8 启动后台线程；不在 LLM 内决策
    # （避免 LLM 工具集膨胀；轮询是运行时关注点，不是 LLM 关注点）
    return result
```

**为什么主流程轮询在 `execute_p8` 而不在 LLM 工具里？**

- **职责分离**：LLM 决策"如何处置"；轮询是"何时把新数据塞进来"
- **避免 LLM 工具集膨胀**：不增加 `poll_p7_events` 工具
- **确定性 SLA**：轮询周期由配置控制（30s / 1min / 5min），不依赖 LLM 自主决定
- **幂等去重**：后台线程维护"已读 a6_event_id 集合"，新 event 增量写入工作记忆时跳过已读

**主流程增量推送机制**（`execute_p8` 内部启动的后台线程）：

```python
# agents/main_agent.py
import threading, time, json
from pathlib import Path

def _p8_poll_worker(job_id, agent, config, seen_event_ids, interval_sec=30):
    """后台线程：每 interval_sec 秒扫描 p7_result.json 增量。"""
    p7_path = Path(f"data/jobs/{job_id}/p7_result.json")
    while True:
        time.sleep(interval_sec)
        if not p7_path.exists():
            continue
        events = json.loads(p7_path.read_text()).get("risk_events", [])
        new_events = [e for e in events if e["a6_event_id"] not in seen_event_ids]
        if not new_events:
            continue
        # 把新事件注入到 LLM context（通过增量 HumanMessage）
        msg = HumanMessage(content=(
            f"【P7 增量】检测到 {len(new_events)} 个新 risk_event：\n"
            + "\n".join(f"- {e['a6_event_id']} ({e['level']})" for e in new_events)
        ))
        seen_event_ids.update(e["a6_event_id"] for e in new_events)
        # 在主 thread 调 agent.invoke（按 LangGraph 约定）
        agent.invoke({"messages": [msg]}, config=config)
```

#### C. 用户明确要求（按需关注）

LLM 规则提示词明确：

```markdown
## 何时调 `read_p7_events`（独立对话模式）

- ✅ 用户明确询问新事件时（"刚才还有新的吗？"、"P7 那边有新数据吗？"）
- ✅ 用户要求刷新 P8_job 列表时（"看看现在有哪些事件"）
- ✅ 用户要求创建新 P8_job 时（隐含：需要最新 P7 数据决定是否已有 P8_job）
- ❌ 用户在确认/查询/推进某个已知 P8_job 时（不主动订阅）
```

#### 三种模式的对比矩阵

| 维度 | 用户正常对话 | 主流程自动调用 | 用户明确要求 |
|------|------------|--------------|------------|
| 启动拉取 | ❌ | ✅ 全量 | ❌（等用户问） |
| 期间拉取 | ❌ | ✅ 周期增量 | ❌ |
| 触发拉取的主体 | LLM（用户消息驱动） | `execute_p8` 后台线程 | LLM（用户消息驱动） |
| 拉取范围 | 全量（按用户指令） | 启动全量 + 期间增量 | 全量（按用户指令） |
| 去重机制 | 工作记忆 `a6_event_ids[]` | 后台线程 `seen_event_ids` 集合 | 工作记忆 `a6_event_ids[]` |
| 工作记忆影响 | 用户问时合并 | 持续更新 | 用户问时合并 |
| 适用场景 | 调试、问答、操作 | 流程 SLA | 临时查询 |

---

## 8. 责任岗位与紧急程度映射

### 8.1 紧急程度映射表

| 风险等级 | 默认责任岗位 | 飞书消息前缀 | 处理时限 |
|---------|------------|------------|---------|
| `LOW` | 作业负责人 | 🟢 `[一般]` A6 风险研判 | 24h |
| `MEDIUM` | 班组长 | 🟡 `[重要]` A6 风险研判 | 4h |
| `HIGH` | 车间主任 | 🟠 `[紧急]` A6 风险研判 | 1h |
| `CRITICAL` | 厂长 / HSE 经理 | 🔴 `[🚨危急]` A6 风险研判 | 实时 |

> LLM 调 `update_job` 时按此表查 `channel` 默认值（HIGH/CRITICAL → HITL，LOW/MEDIUM → PUSH）；
> 实际 channel 由 LLM 自主决定，可结合用户指令调整。

### 8.2 飞书消息正文结构（Demo 模板）

**单事件 P8_job（N=1）模板**：

```
[🟠 紧急] A6 风险研判 HIGH 级
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
风险事件 ID:    A6-20260813-094525-230
风险描述:       可燃气体浓度超标（CH4 4.8% / 阈值 1%）
建议处置动作:   rectify (组织整改)
建议责任岗位:   车间主任
处理时限:       1 小时内
关联任务 ID:    J-JOB-20260813-001
处置 P8_job ID: P8J-20260813-180000-001   ← channel=PUSH 时直接推送
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
请相关人员及时处置。
```

**聚合 P8_job（N>1，风险叠加）模板**：

```
[🟠 紧急] A6 风险研判 风险叠加处置 HIGH 级
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
处置 P8_job ID: P8J-20260813-180000-001
风险叠加事件:
  • A6-20260813-094525-230  HIGH  PPE 缺失（单人 P7）
  • A6-20260813-094525-450  MEDIUM 噪声超标（85 dB）
  • A6-20260813-094525-680  MEDIUM 进入受限区域
聚合等级:       HIGH (max)
聚合描述:       3 人同时段 PPE 缺失 + 噪声超标 + 进入受限区域
建议处置动作:   escalate (升级至 HSE 经理统一处置)
建议责任岗位:   车间主任
处理时限:       1 小时内
关联任务 ID:    J-JOB-20260813-001
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
该 P8_job 合并多个并发风险事件，请一次性决策处置。
```

> **channel=PUSH 时**：直接调 `notify_feishu` 推送上述消息，P8_job 状态 = `completed`。
> **channel=HITL 时**：调 `hitl_decide` 中断等决策，不推消息（避免重复打扰）。

### 8.3 单通道推送的实现要点

| 项 | 说明 |
|----|------|
| 调用入口 | `agents/channel_gateway_client.py:send_message()` |
| 通道 | 固定 `channel="feishu"` |
| 接收方 | `conversation_id` 来自 `RISK_PUSH_STRATEGY[risk_level]` 对应的岗位群组 |
| 幂等键 | `idempotency_key = p8_job_id`（同一 P8_job 重复发送只产生一条） |
| 失败回执 | 飞书推送失败 → 仅记日志，**不阻塞** P8_job 推进；LLM 据此决定是否重试 |

---

## 9. 状态机

### 9.1 P8_job 状态转换图

```
pending（初始）
   │
   │ update_job(status="notified", a6_event_ids=[id1,...])  ← N≥1
   ▼
notified
   │
   ├── channel=HITL + hitl_decide()  ──►  waiting_decision
   │                                          │
   │                                          │ 决策到达（框架自动唤醒 LLM）
   │                                          │ 决策一次性作用于 a6_event_ids[] 内全部 risk_event
   │                                          ├── approve/rectify ──► completed  ─┐
   │                                          ├── reject          ──► rejected   ─┤
   │                                          ├── escalate        ──► escalated  ─┼─► P8ArchiveMiddleware
   │                                          └── resume          ──► resumed    ─┘   (自动归档到长期记忆)
   │
   └── channel=PUSH + notify_feishu()  ──►  completed  ──► P8ArchiveMiddleware
        └─ 推送 message 中列出全部 a6_event_ids（聚合模板，参见 8.2）
```

**叠加场景（N>1）的状态变化要点**：

- 聚合 P8_job 在 `pending → notified` 一步时，`a6_event_ids[]` 一次性写入；后续状态推进不再修改该数组
- HITL 决策到达后，`P8ArchiveMiddleware` 自动归档，N 个 risk_event 一次性写入长期记忆
- 若 `update_job` 后续又把同一 a6_event_id 加到另一个 P8_job → 工具内部去重检查返回错误（防重复）
- 聚合 P8_job 不可拆解（一旦入队就视为整体）；如需拆分必须新建 P8_job 并 link 到原 job

### 9.2 字段更新规则

| 字段 | 更新条件 |
|------|---------|
| `status` | LLM 通过 `update_job` 推进；框架在决策到达后填 `decision` 时 LLM 自动更新 |
| `channel` | LLM 在 `update_job` 时决定（HITL / PUSH） |
| `decision` | 框架在 HITL 中断恢复时自动填入 |
| `note` | LLM 自由填入（决策者、原因等） |
| `due_at` | 创建时按 `RISK_PUSH_STRATEGY` 计算 |

### 9.3 工作记忆的物理实现

```python
# agents/p8_disposition_agent.py
from langgraph.checkpoint.memory import MemorySaver
from langchain.agents.middleware import HumanInTheLoopMiddleware

_checkpointer = MemorySaver()

def create_disposition_agent(walltime: str | None = None) -> CompiledGraph:
    """构造 P8 agent。P8_job 列表由 LLM 通过工具自行维护。

    工厂函数只接收 walltime（用于 due_at 计算）；
    job_id 由调用方通过 HumanMessage 内容传入；
    thread_id 由调用方通过 invoke(config=...) 指定。
    """
    return create_agent(
        model=create_chat_model(),
        tools=[read_p7_events, update_job, hitl_decide, notify_feishu, recall_jobs],
        system_prompt=build_system_prompt(walltime),
        middleware=[
            HumanInTheLoopMiddleware(interrupt_on={"hitl_decide": True}),
            P8ArchiveMiddleware(),  # 框架层自动归档（详见 § 6.4）
        ],
        checkpointer=_checkpointer,   # 跨 invoke 保持 working memory
    )

# 主流程调用示例
agent = create_disposition_agent(walltime=now_iso())
agent.invoke(
    {"messages": [HumanMessage(content=f"处理作业 {job_id} 的 P7 风险事件")]},
    config={"configurable": {"thread_id": f"p8-{job_id}"}},
)
```

> working memory 作为 LangGraph state 字段实现（除 `messages` 外）；
> 每次 invoke 前由框架自动注入到 LLM context。
> HITL 决策到达后框架自动调 `agent.invoke(None, config={"configurable": {"thread_id": ...}})` 唤醒。

---

## 10. Demo 应当实现的功能（6 个场景）

### Demo 1：自动创建处置 P8_job（核心场景）

| 项 | 说明 |
|----|------|
| **演示准备** | 确保 `data/jobs/JOB-XXX/p7_result.json` 里有 risk_events；调用 `execute_p8(job_id)` 时通过 HumanMessage 内容传入 `job_id`（不是工厂参数） |
| **LLM 自动行为** | ① 启动时工作记忆为空 → 调 `read_p7_events` 读 P7 ② 遍历每个 event ③ 查 `RISK_PUSH_STRATEGY` 决定 channel ④ `update_job` + `hitl_decide` 或 `notify_feishu` |
| **HITL** | HIGH/CRITICAL → `hitl_decide` 中断等决策；LOW/MEDIUM → `notify_feishu` 直达 |
| **价值** | 演示"工作记忆 + LLM 自主决策"的全自动 P8_job 推进路径 |

### Demo 2：暂停作业（应急场景）

| 项 | 说明 |
|----|------|
| **用户输入** | "A5 监测到 CRITICAL 风险，立即暂停当前作业" |
| **LLM 自动行为** | ① 调 `read_p7_events` 读最新 P7 数据 ② 创建 P8J-...-099 的 P8_job（`a6_event_ids=["A6-..."]` 带 pause 语义） ③ `update_job(channel="HITL", status="notified")` ④ 调 `hitl_decide` 中断 |
| **HITL** | ✅ 暂停需人工二次确认（高风险动作） |
| **价值** | 演示 LLM 自主组合多工具完成复杂决策 |

### Demo 3：升级处置（链路场景）

| 项 | 说明 |
|----|------|
| **用户输入** | "P8J-20260813-180000-001 整改超时，升级给厂长" |
| **LLM 自动行为** | ① `recall_jobs(query="P8J-20260813-180000-001")` 查长期记忆 ② 创建新 P8_job（`a6_event_ids=["A6-..."]`）带 escalate 语义 ③ `update_job(channel="HITL")` ④ `hitl_decide(options=["escalate","其他"])` |
| **HITL** | ✅ 升级需人工确认 |
| **价值** | 演示 working ↔ long_term memory 跨层调用 |

### Demo 4：恢复作业（闭环场景）

| 项 | 说明 |
|----|------|
| **用户输入** | "P8J-20260813-180000-001 风险已解除，恢复作业" |
| **LLM 自动行为** | ① `recall_jobs` 查上一个 pause P8_job ② 创建新 P8_job（`a6_event_ids=["A6-..."]`，resume 语义） ③ `update_job(channel="PUSH", status="notified")` ④ `notify_feishu` 直达 |
| **HITL** | ❌ 走 PUSH 通道，不阻塞 |
| **价值** | 演示 PUSH 通道直达，无需人工 gate |

### Demo 5：查询历史 P8_job（长期记忆场景）

| 项 | 说明 |
|----|------|
| **用户输入** | "昨天那个可燃气体超标事件最后怎么处理的？" |
| **LLM 自动行为** | ❌ **不读工作记忆**——目标不在 in-progress P8_jobs。直接调 `recall_jobs(query="可燃气体")` 查长期记忆 |
| **HITL** | ❌ 不涉及 |
| **价值** | 演示"长期记忆按需调取"原则；不污染当前工作记忆 |

### Demo 6：当前作业状态查询（走 REST API）

| 项 | 说明 |
|----|------|
| **用户输入** | "查看当前所有进行中的 P8_job" |
| **LLM 自动行为** | ❌ **不调工具**——查询走 REST API。引导用户："请在右侧面板查看（前端已自动调 `GET /api/jobs/:job_id/working-memory`）" |
| **前端自动行为** | Web 组件自动调 REST API 拉工作记忆快照，按 status / channel 分组展示 |
| **HITL** | ❌ 不涉及 |
| **价值** | 演示"工具 = 动作 / REST API = 查询"的职责分层 |

---

## 11. 接口契约（精简版）

### 11.0 a6_event_id 与 P8_job 队列（关键前置概念）

#### 11.0.1 `a6_event_id` 的定义与唯一性

**a6_event_id** 是 A6 阶段对"一个违规生命周期"分配的标识符，格式：

```
A6-YYYYMMDD-HHMMSS-millis
```

例：`A6-20260813-094525-230`（日期=20260813，时间=09:45:25，毫秒=230）

**生成逻辑**（`A6/agent/tools.py:OutputTools._generate_a6_event_id`）：

```python
def _generate_a6_event_id(self) -> str:
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")        # 20260813
    time_str = now.strftime("%H%M%S")        # 094525
    seq = now.microsecond // 1000             # 毫秒级 000-999
    return f"A6-{date_str}-{time_str}-{seq:03d}"
```

**唯一性范围**：

| 场景 | 唯一性 | 机制 |
|------|--------|------|
| **进程内同时刻** | ✅ 唯一 | `_locks: Dict[str, asyncio.Lock]` 串行化（避免同毫秒并发冲突） |
| **进程内不同时刻** | ✅ 唯一 | 时间戳递增 |
| **跨进程** | ⚠️ **不保证** | 当前基于本地时钟；多进程部署应替换为 UUID / DB 自增 |
| **聚合场景** | ✅ 不冲突 | 聚合时**复用已有** `a6_event_id`，不重新生成 |

#### 11.0.2 A6 输出路径（按天分目录）

```
A6/logs/
└── assessments/
    └── YYYYMMDD/                          ← 按天分子目录
        └── a6_<safe_timestamp>.json        ← 文件名由 timestamp 安全化生成
```

实际例：`A6/logs/assessments/20260813/a6_2026-08-13T09-45-25_230312.json`

> **`a6_event_id` vs 文件名**：两者**不是一一对应关系**——
> - `a6_event_id` 是**逻辑标识符**，跨日期仍然唯一（用于 P7/P8 引用）
> - 文件名是**物理路径**，按生成时刻分目录存放
> - 一个 `a6_event_id` 对应**一个文件**；聚合时**覆盖更新**该文件（不创建新文件）
>
> 通过 `A6/agent/tools.py:load_assessment_with_path(a6_event_id)` 反查文件路径（遍历所有日期目录）。

#### 11.0.3 `a6_event_id` 与 P8_job 的 N:1 关系（风险叠加）

| 概念 | 归属 | 含义 |
|------|------|------|
| **a6_event_id** | A6 维护 | 一个违规生命周期（person + violation_type 持续期间） |
| **risk_event** | P7 构造 | `p7_result.json.risk_events[*]` 一项 = 一个 a6_event_id + 归一化字段 |
| **p8_job_id** | P8 维护 | 一个处置任务（可聚合 N 个同时间的 risk_event） |

**关键约定**：**p8_job_id ≠ a6_event_id**。p8_job_id 由 P8 自生成（`P8J-YYYYMMDD-HHMMSS-NNN`），不复用 a6_event_id。

**N:1 关系示意**：

```
p7_result.json.risk_events:        A6 输出文件:                 P8 工作记忆:
┌──────────────────────────┐       ┌────────────────────┐       ┌──────────────────────────────┐
│ risk_event #1 (HIGH)     │ ───►  │ A6-...-230.json    │ ─┐    │ P8J-...-001                  │
│ risk_event #2 (MEDIUM)   │ ───►  │ A6-...-450.json    │ ─┼──► │   a6_event_ids: [...230, ...450, ...680] │
│ risk_event #3 (MEDIUM)   │ ───►  │ A6-...-680.json    │ ─┘    │   max_level: HIGH            │
└──────────────────────────┘       └────────────────────┘       │   channel: HITL               │
                                                                 └──────────────────────────────┘
        N 个 risk_event                          多个 A6 文件             1 个 P8_job（聚合）
```

**为什么 N:1（而不是 1:1）？**

| 维度 | 1:1（旧设计） | N:1（新设计） |
|------|--------------|--------------|
| 风险刻画 | 每个事件独立处置 | **同一时刻多事件叠加**作为一个整体处置（如 3 人同时 PPE 缺失 + 噪声 + 进入受限区） |
| HITL 决策 | 一个决策只针对 1 个事件 | **一个决策一次性处理多个事件**（减少决策疲劳） |
| 飞书推送 | 多次推送（重复打扰） | **合并推送**（"3 个并发风险，建议统一处置"） |
| 责任归属 | 每个事件单独分派 | **聚合后统一分派**给能综合决策的角色（如 HSE 经理） |
| 追溯链 | a6_event_id ↔ p8_job_id 一对一 | p8_job_id → `a6_event_ids[]` 数组 → 多个 A6 文件 |

**LLM 聚合决策的依据**（由 LLM 自主判断，不写死 if/else）：

- **时间窗口重叠**：多个 risk_event 的 `first_seen` / `last_seen` 在同一窗口内
- **空间/人员重叠**：同一区域 / 同一人员的多类型违规
- **风险类型互补**：PPE + 噪声 + 受限区 = 协同违规场景
- **严重度提升**：合并后 `max_level` 触发 HITL 阈值

> LLM 通过 `read_p7_events` 读到所有 risk_event 后，自主决定：
> - **不聚合**（N=1）：每个 risk_event 单独建 P8_job
> - **部分聚合**：同时间窗的合并、不同时间窗的分开
> - **全聚合**：所有 risk_event 合并为一个 P8_job

**P7 工具返回字段**（`agents/p7_risk_agent.py:risk_analyze`，单条）：

```jsonc
{
  "status": "ok",
  "result": {
    "event_id": "A6-20260813-094525-230",   // = a6_event_id（字段名是 event_id 因为 P8 tool 入参归一化）
    "a6_event_id": "A6-20260813-094525-230", // A6 原生字段（冗余）
    "level": "LOW",                          // LOW/MEDIUM/HIGH/CRITICAL（由 risk_level 映射）
    "risk_level": 1,                         // 原始 1-5
    "risk_level_name": "轻微",
    "risk_basis": "...",
    "suggestions": [...],
    "first_seen": "2026-08-13T09:44:51.700",  // ← LLM 聚合依据之一
    "last_seen": "2026-08-13T09:44:59.700"
  }
}
```

> **字段命名注意**：`event_id` 和 `a6_event_id` 在 P7 返回里**值相同**。LLM 拿到的是 `risk_events[]` 列表，每项都含这两个字段 + `first_seen` / `last_seen` 用于聚合判断。

#### 11.0.4 P8_job 队列（工作记忆的物理形态）

P8 内部工作记忆 = **P8_job 队列**，结构（LangGraph state.working_memory）：

```jsonc
[
  {
    "p8_job_id": "P8J-20260813-180000-001",     // P8 自生成
    "a6_event_ids": [                            // 关联的 risk_event 源（数组，N≥1）
      "A6-20260813-094525-230",
      "A6-20260813-094525-450",
      "A6-20260813-094525-680"
    ],
    "risk_basis": "3 人同时段 PPE 缺失 + 噪声超标 + 进入受限区域（风险叠加）",
    "max_level": "HIGH",
    "level_distribution": { "HIGH": 1, "MEDIUM": 2 },
    "urgency_emoji": "🟠",
    "assignee_role": "车间主任",
    "status": "notified",
    "channel": "HITL",
    "decision": null,
    "note": "",
    "due_at": "2026-08-13T19:00:00Z",
    "created_at": "2026-08-13T18:00:00Z"
  },
  ...
]
```

**队列操作**（入队/推进/阻塞/推送 = LLM 工具调用；出队/归档 = 框架机制）：

| 操作 | 触发工具 | 队列变化 |
|------|---------|---------|
| **入队（单）** | `update_job(p8_job_id=<NEW>, a6_event_ids=[id_1], status="notified")` | 从 P7 risk_events 拉 1 个 a6_event_id 转为 P8_job（N=1） |
| **入队（聚合）** | `update_job(p8_job_id=<NEW>, a6_event_ids=[id_1, id_2, id_3], status="notified")` | 从 P7 risk_events 拉 N 个同时间 a6_event_id 合并为 1 个 P8_job（N>1） |
| **推进** | `update_job(p8_job_id=..., status="...")` | 改 status / channel / note；P8_job 仍在队列 |
| **阻塞** | `hitl_decide(p8_job_id=..., options=...)` | 状态 → `waiting_decision`；P8_job 仍在队列，但 LLM 不能继续推进该 job |
| **推送** | `notify_feishu(p8_job_id=..., message=...)` | channel=PUSH 路径；消息中列出全部 a6_event_ids；推送后状态 → `completed` |
| **出队（归档）** | **`P8ArchiveMiddleware`（框架自动）** | 监听 `update_job` 进入终态 → 自动 working → long_term + 从 working_memory 移除；LLM 不调任何工具 |

**聚合入队的实现要点**：

- LLM 调 `update_job` 时一次性传入**整个数组** `a6_event_ids=[..., ..., ...]`
- 工具内部检查所有 a6_event_id 都来自同一 risk_event 集合（防误传）
- 自动计算 `max_level` = 数组中所有 risk_event 等级的最高值
- 自动计算 `due_at` 按 `max_level` 对应的 `RISK_PUSH_STRATEGY`
- 已入队的 a6_event_id 不允许再次入队（防重复）

**并发约束**：

- **同一 p8_job_id 在同一时刻只能被一个 LLM invoke 操作**（避免并发写冲突；由 LangGraph state 序列化保证）
- **不同 p8_job_id 之间可并行处理**（独立 HITL 中断、独立状态推进）
- **HITL 中断粒度 = 单 p8_job_id**（一个 P8_job 阻塞不影响其他 P8_job）

### 11.1 P8 接收（P7 输出 → `p7_result.json.risk_events`）

字段：`event_id` (= a6_event_id) / `a6_event_id` / `level` / `risk_level` / `risk_basis` / `suggestions`。
LLM 通过 `read_p7_events(job_id)` 工具读入。

### 11.2 P8 输出（写 `p8_result.json`，供 P9 消费）

> P8 的"输出"主要是**工作记忆快照 + 长期记忆归档**；`p8_result.json` 仅作为持久化备份。

```jsonc
{
  "job_id": "JOB-20260813-001",
  "stage": "P8",
  "snapshot_at": "2026-08-13T18:00:30Z",
  "working_memory": [
    {
      "p8_job_id": "P8J-20260813-180000-001",
      "a6_event_ids": [                           // ← N:1 关键字段（数组）
        "A6-20260813-094525-230",
        "A6-20260813-094525-450",
        "A6-20260813-094525-680"
      ],
      "max_level": "HIGH",
      "level_distribution": { "HIGH": 1, "MEDIUM": 2 },
      "status": "waiting_decision",
      "channel": "HITL",
      "decision": null,
      "due_at": "2026-08-13T19:00:00Z"
    },
    {
      "p8_job_id": "P8J-20260813-180000-002",   // ← 单事件 P8_job（N=1）
      "a6_event_ids": ["A6-20260813-100000-100"],
      "max_level": "LOW",
      "level_distribution": { "LOW": 1 },
      "status": "completed",
      "channel": "PUSH",
      "decision": null,
      "due_at": "2026-08-14T18:00:00Z"
    }
  ],
  "archived_jobs": [
    {
      "p8_job_id": "P8J-20260813-180000-002",
      "a6_event_ids": ["A6-20260813-100000-100"],
      "final_status": "completed",
      "decided_by": "feishu:ou_xxx",
      "archived_at": "2026-08-13T18:00:25Z"
    }
  ]
}
```

> 工作记忆实时在 LangGraph state；本文件由 `execute_p8` 周期性快照，供 P9 / 重启恢复使用。
>
> **`a6_event_ids` 数组始终非空**（N≥1）；N=1 即单事件 P8_job，N>1 即聚合 P8_job（风险叠加）。
> P9 消费时按 `a6_event_ids[]` 反查 A6 文件，**不会丢失任何 risk_event 的追溯链**。

### 11.3 长期记忆（`_MOCK_ARCHIVE` 模块级 dict）

```python
_MOCK_ARCHIVE: dict[str, dict] = {}  # p8_job_id → archived P8_job 完整记录
```

- 进程重启时由 `p8_result.json.archived_jobs` 加载
- 真实生产可替换为 SQLite / 文件存储

---

## 12. 边界与不做的事（避免范围蔓延）

| 不做 | 原因 |
|------|------|
| ❌ 不做风险评估 | P7 的事 |
| ❌ 不做实时监测 | P6 的事 |
| ❌ 不做整改结果验证 | P9 的事 |
| ❌ 不直接控制作业系统 | "暂停"只是创建 P8_job + 标记状态 |
| ❌ 不做归档 | P10 的事 |
| ❌ 不实现真实多通道推送 | Demo 阶段只走飞书 |
| ❌ 不主动预加载 P7 输出到 system prompt | 保持"所有外部数据通过工具读入"的一致架构 |
| ❌ 不让 LLM 主动轮询决策 | HITL 决策由框架事件自动唤醒 LLM |
| ❌ 不在 context 直接放长期记忆 | 按需 `recall_jobs` 调取，避免污染 |
| ❌ 跨阶段问题不擅自处理 | 提示词明确：风险评估/监测/验证转交 P6/P7/P9 |

---

## 13. 关键决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 记忆分层 | 工作记忆（in-context）+ 长期记忆（按需） | 当前进度不丢；历史不污染 context |
| P8_job 概念 | N 个 risk_event = 1 个 P8_job（N:1，风险叠加） | 支持同一时刻多事件合并处置；HITL 中断与 P8_job 一一绑定；一个决策处理多个 risk_event |
| 状态归属 | 状态完全在工作记忆（LangGraph state），无外部单例 dict | LLM 拥有完整决策权；简化并发模型 |
| HITL 中断粒度 | 单 P8_job 级（不全局） | 不同 P8_job 可并行处理；互不阻塞 |
| 决策唤醒 | 框架自动调 `agent.invoke(None, thread_id=...)` | 无需 watcher 进程；无外部事件总线 |
| 通道决策 | LLM 自主决定 HITL / PUSH（基于 `level` + 用户指令） | 灵活；不写死 if/else |
| P7 输出加载时机 | 启动时不预注入；LLM 通过 `read_p7_events` 工具按需读 | 架构一致性：所有外部数据通过工具进入 |
| 工具数量 | 6 个（读/写/中断/推送/归档/回忆） | 按职责分维度；LLM 工具选择负担可控 |
| 查询走 REST API | 状态查询走 `GET /api/jobs/:job_id/working-memory`，不下沉到工具 | 工具 = 动作；REST API = 查询 |
| 存储后端 | `_MOCK_ARCHIVE` 模块级 dict | 与 P2/P4 既有约定一致；进程重启由 `execute_p8` 写 `p8_result.json` 兜底 |
| 推送通道 | Demo 阶段只走飞书单通道 | 当前只做 demo，不实现真实短信 / 电话 |
| 工作记忆的物理实现 | LangGraph state 字段 + `MemorySaver` checkpointer | 跨 invoke 自动保持；HITL 唤醒天然兼容 |
| **工厂签名** | `create_disposition_agent(walltime=None)`，**不接收 job_id** | 主流程 `job_id` 与 P8 内部 `P8_job` 是两个不同层次的概念；混用会导致命名冲突与职责越界。job_id 由调用方通过 HumanMessage 内容传入；thread_id 由 `invoke(config={...})` 指定 |
| **废弃极简版提示词** | 删除代码内 `SYSTEM_PROMPT` 常量；所有提示词通过 markdown 文件加载 | 避免代码常量与文档脱节；统一从 `system_prompt/` 和 `rules/` 加载，便于审计与版本控制 |
| **提示词分两类（系统 / 规则）** | 系统提示词面向开发（schema / 工具 / 状态机），规则提示词面向业务（叠加规则 / 责任岗位 / 通道决策 / due_at / 合规） | schema 与规则解耦；业务规则调整无需开发介入；演进隔离；可审计；未来可走配置中心热加载 |
| **新 a6_event 关注策略** | 三种触发模式区分对待：用户正常对话❌不关注；主流程自动调用✅全量+周期增量；用户明确要求✅按需关注 | 主流程有 SLA 必须覆盖；用户操作模式不应被新事件打扰；查询模式被动响应。详见 § 7.8 |
| **主流程轮询职责归属** | 增量拉取由 `execute_p8` 后台线程执行，**不**作为 LLM 工具 | 职责分离：LLM 决策"如何处置"，轮询是"何时塞新数据"；避免 LLM 工具集膨胀；周期由配置（`p8_runtime.yaml`）控制而非 LLM 自主 |
| **`archive_job` 机制化** | 从 LLM 工具集**移除**，改为 LangGraph middleware `P8ArchiveMiddleware` 监听状态变更自动触发 | "完成 → 归档"是确定性副作用，不应由 LLM 决策。改造后：LLM 工具数从 6 → 5；归档时机确定；summary 框架自动汇总；不会出现"忘记归档"或"重复归档"。详见 § 6.4 |
| **walltime 注入方式** | 工厂调用时取一次 ISO8601 字符串，写入 system prompt | 比 `now()` 工具更轻量（每 P8_job 决策无需多调一次）；适合 due_at 这种全局基准时间 |
| **主流程 job_id 传递路径** | HumanMessage 内容（LLM 自取） + invoke config thread_id | 解耦：内容层携带业务标识，配置层携带 LangGraph 状态标识 |
| **P8_job 命名空间** | P8 内部工作单元统称 `P8_job`，字段名 `p8_job_id` | 与主流程 `job_id` 严格区分；文档、代码、LLM prompt 三层一致 |
| **a6_event_id 来源** | 由 A6 阶段生成（`A6-YYYYMMDD-HHMMSS-millis`），P8 不重新生成 | 唯一性靠 A6 进程内 asyncio.Lock 串行化保证；跨进程不保证（未来替换 UUID/DB 自增） |
| **p8_job_id ≠ a6_event_id（独立 ID 体系）** | P8 自生成 `P8J-YYYYMMDD-HHMMSS-NNN`，不复用 a6_event_id | 因为 P8_job 可聚合多个 risk_event（风险叠加），单一 a6_event_id 无法表示一个 P8_job 的完整语义；引入独立 ID 是 N:1 设计的必然结果 |
| **p8_job_id ↔ a6_event_ids 映射** | 工作记忆中每个 P8_job 持有 `a6_event_ids[]` 数组（长度 N≥1） | 保留完整追溯链：p8_job_id → a6_event_ids[] → 多个 A6 文件 |
| **P8_job 与 risk_event 关系** | **N:1（风险叠加）**，不是 1:1 | 同一时刻多个 risk_event 合并为 1 个 P8_job；HITL 决策一次性处理多个事件；飞书推送合并；由 LLM 自主判断聚合 |
| **聚合决策权** | LLM 自主决定 N=1 / 部分聚合 / 全聚合（基于 first_seen/last_seen/区域/人员） | 不写死 if/else；保留 LLM 对场景的灵活判断 |
| **P8_job 队列存储** | LangGraph state.working_memory（in-context） | LLM 自主决策入队/推进/阻塞/出队；并发由 LangGraph state 序列化保证 |

---

## 14. 与现有文档的关系

| 文档 | 视角 | 用途 |
|------|------|------|
| **本文档**（P8_人机协同处置_需求与Demo设计.md） | **需求视角** | 职责边界 / **Agent 内部架构（双层记忆 + P8_job + LLM 自主决策 + N:1 风险叠加）** / 工具集 / 激活流程 / Demo 场景 / 边界 / **a6_event_id 与 P8_job 队列契约（11.0 节）** / **两类提示词架构（§ 5）** |
| [docs/agents/P8_DISPOSITION_AGENT.md](agents/P8_DISPOSITION_AGENT.md) | 工具视角 | **6 工具接口**（含 `update_job(a6_event_ids)` 数组参数） / HITL 中断策略 / 推送策略表 |
| `agents/system_prompt/P8_SYSTEM_PROMPT.md` | 系统提示词（面向开发） | 数据 schema / 工具签名 / 状态机；与代码同步 |
| `agents/rules/P8_RULES.md`（或 `config/p8_rules.yaml`） | 规则提示词（面向业务） | 风险叠加规则 / 责任岗位 / 通道决策阈值 / due_at 计算 / 行业合规；业务可独立修改 |
| [docs/spec/CLI-SPEC/contract.yaml](spec/CLI-SPEC/contract.yaml) | 契约视角 | 工具输入输出 schema 权威定义 |
| [docs/spec/SDD.md](spec/SDD.md) | 设计视角 | DispositionService 详细设计 |
| [docs/CHANNEL_GATEWAY_CLIENT.md](CHANNEL_GATEWAY_CLIENT.md) | 通信通道 | `send_message` 等飞书接口 |
| [A6/agent/a6_agent.py](../A6/agent/a6_agent.py) | A6 实现 | `_generate_a6_event_id()` 生成 / `_update_existing_assessment` 聚合 / `_locks` 串行化 |
| [A6/agent/tools.py](../A6/agent/tools.py) | A6 工具 | `OutputTools.save_assessment` 写盘 / `load_assessment_with_path` 按 ID 反查 |
| [agents/p7_risk_agent.py](../agents/p7_risk_agent.py) | P7 透传 | `risk_analyze` / `risk_list` 把 a6_event_id + `first_seen/last_seen` 透传到 `p7_result.json.risk_events` |
| [agents/main_agent.py:509-553](../agents/main_agent.py#L509) | 主流程 | `execute_p8` 读 p7_result，遍历 a6_event_id 触发 P8 |