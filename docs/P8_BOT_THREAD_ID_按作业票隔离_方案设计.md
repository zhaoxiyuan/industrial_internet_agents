# P8 Bot Thread ID 按作业票隔离 — 方案设计

> 文档版本：v0.1（2026-08-20 起草，待 review）
> 关联代码：
> - [`A7/adapters/chat_reply.py`](../../A7/adapters/chat_reply.py)（`_compute_thread_id` L548-562）
> - [`agents/p8_disposition_agent.py`](../../agents/p8_disposition_agent.py)（`create_disposition_agent` / `run_disposition_agent` / `disposition_demo`）
> - [`A7/storage/p8_working_memory_store.py`](../../A7/storage/p8_working_memory_store.py)（per-job 持久化）
> - [`A7/storage/p8_long_term.py`](../../A7/storage/p8_long_term.py)（P8 长期记忆）
> 关联文档：[`P8_人机协同处置_需求与Demo设计.md`](P8_人机协同处置_需求与Demo设计.md)（蓝图 §7.2.1 thread_id 约定）

## 1. 背景与目标

### 1.1 当前现状

| 调用方 | `thread_id` | `job_id` | `working_memory` 隔离 |
|--------|-------------|----------|----------------------|
| 主流程 `execute_p8` | `f"p8-{job_id}"` | ✅ 显式传入 | ✅ **按 job 隔离** |
| Bot 飞书群（chat_reply） | `chat_id` | ❌ 无 | ❌ **同群多 job 串台** |
| Bot 飞书单聊（chat_reply） | `sender_open_id` | ❌ 无 | ❌ **同用户多 job 串台** |

代码定位：[`A7/adapters/chat_reply.py:548-562`](../../A7/adapters/chat_reply.py#L548-L562)（`_compute_thread_id`）

### 1.2 矛盾点

P8 working_memory 是**结构性敏感**的状态——同一个 P8_job 的 risk_basis / max_level / assignee_role / 处置记录有强上下文语义，**把"作业 A"的工作记忆应用到"作业 B"会引发误处置**。

但飞书 Bot 的使用习惯是：用户直接在群里问"这个作业有什么风险？"，**不会主动声明 `job_id`**。强行要求"调用 P8 前必须传 job_id"会破坏 Bot 体验。

### 1.3 目标

在不破坏飞书 Bot "用户零感知" 使用习惯的前提下，让 P8 working_memory 在 Bot 场景下也按作业票（`job_id`）隔离。

## 2. 问题定义

### 2.1 串台后果示例

```
[t=10:00] 用户在群里问："把作业 JOB-20260813-001 的风险调出来看看"
         → chat_reply 调 P8，thread_id = chat_id = "oc_xxx"
         → P8 working_memory 写入 job A 的 risk_basis

[t=10:05] 用户在群里说："再看看作业 JOB-20260814-002"
         → chat_reply 调 P8，thread_id 仍 = chat_id = "oc_xxx"
         → P8 从 working_memory 读到 job A 的 risk_basis（串台！）
         → LLM 把 job A 的依据当成 job B 的依据
```

### 2.2 设计约束

| 约束 | 说明 |
|------|------|
| C1 | **不破坏 Bot 体验**：用户不应需要主动声明 `job_id` 前缀 |
| C2 | **结构敏感性**：误归属的工作记忆可能引发误处置 |
| C3 | **向下兼容**：现有 `chat_reply` 调用方、web UI 调用方不能破坏 |
| C4 | **可观测**：thread_id 解析路径必须可日志追溯 |
| C5 | **降级优雅**：解析失败时回退到当前 chat_id/open_id，不让对话卡死 |

## 3. 候选方案对比

### 方案 A：消息正文解析 `[job_id=XXX]` 前缀

| 维度 | 评估 |
|------|------|
| 用户体验 | ❌ 用户必须主动声明前缀，违反 C1 |
| 准确率 | ⭐⭐⭐（用户写错就没了） |
| 实现成本 | 低（regex） |
| 适用 | 兜底 |

### 方案 B：会话级"焦点 job"隐式切换

LLM 从对话上下文中识别用户提及的 `job_id`，写入 chat 级焦点缓存，后续消息默认走焦点 job。

| 维度 | 评估 |
|------|------|
| 用户体验 | ✅ 完全无感 |
| 准确率 | ⭐⭐（模糊语义时易误判；焦点过期） |
| 实现成本 | 中（Redis + LLM prompt + set_focus_job 工具） |
| 适用 | 主路径 |

### 方案 C：事件 metadata 携带 job_id

利用 P8 飞书告警卡片 callback 自带的 `p8_job_id`，反查主流程 `job_id`。

| 维度 | 评估 |
|------|------|
| 用户体验 | ✅ 完全无感（用户从卡片点进来） |
| 准确率 | ⭐⭐⭐⭐（事件自带，零歧义） |
| 实现成本 | 低（仅 chat_reply 解析 metadata） |
| 适用 | **P8 卡片 callback 主路径** |

### 方案 D：A + B + C 组合智能焦点（**推荐**）

按优先级链解析 `effective_job_id`，再合成 `thread_id`：

```
effective_job_id = 
  P1. 飞书 event.metadata.job_id  ← 卡片回调 / 系统推送自带
  P2. 消息正文 [job_id=XXX] 前缀   ← 用户显式声明（兜底）
  P3. 当前 chat 焦点缓存          ← LLM 从上下文识别
  P4. None                        ← 闲聊场景

thread_id = f"p8-{effective_job_id}" if effective_job_id else chat_id/open_id
```

| 维度 | 评估 |
|------|------|
| 用户体验 | ✅ 无感 + 可显式覆盖 |
| 准确率 | ⭐⭐⭐⭐⭐（多源融合） |
| 实现成本 | 中高（P0+C 后约 1-2 天；完整 D 含 B 约 3-5 天） |
| 适用 | **生产用（推荐）** |

### 方案 E：thread 仍按 chat，内层 working_memory 按 job 分组

`thread_id` 仍 = `chat_id`，但 working_memory 内部用 `job_id` 做二级索引，由 LLM 主动选 job。

| 维度 | 评估 |
|------|------|
| 用户体验 | ✅ 完全无感 |
| 准确率 | ⭐⭐⭐（LLM 多轮选 job 易混淆） |
| 实现成本 | 中（working_memory schema 升级） |
| 适用 | 单群同时跟踪多 job |

### 方案对比汇总

| 方案 | C1 体验 | C2 结构安全 | C3 兼容 | C4 可观测 | C5 优雅降级 | 总评 |
|------|---------|-----------|---------|-----------|------------|------|
| A | ❌ | ⭐⭐ | ✅ | ⭐⭐ | ⭐⭐ | 兜底 |
| B | ✅ | ⭐⭐ | ✅ | ⭐⭐⭐ | ⭐⭐⭐ | 主路径 |
| C | ✅ | ⭐⭐⭐⭐ | ✅ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | **P0 首选** |
| **D** | ✅ | ⭐⭐⭐⭐⭐ | ✅ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | **推荐** |
| E | ✅ | ⭐⭐⭐ | ⚠️ schema 改动 | ⭐⭐ | ⭐⭐⭐ | 备选 |

## 4. 推荐方案 D 详细设计

### 4.1 优先级解析链

```python
def resolve_effective_job_id(
    event: Dict[str, Any],
    message_text: str,
    chat_id: str,
    focus_cache: FocusCache,           # Redis / 旁路存储
) -> Optional[str]:
    """优先级解析 P1 → P2 → P3 → P4"""

    # P1: 飞书事件 metadata 自带 job_id（卡片回调 / 系统推送）
    job_id = event.get("metadata", {}).get("job_id")
    if job_id:
        logger.info("P8 thread_id resolved via P1 (event.metadata.job_id)")
        return job_id

    # P2: 用户消息正文显式 [job_id=XXX] 前缀
    match = re.match(r"^\s*\[job_id=([A-Z0-9\-]+)\]", message_text)
    if match:
        logger.info("P8 thread_id resolved via P2 (msg prefix)")
        return match.group(1)

    # P3: 当前 chat 焦点缓存
    job_id = focus_cache.get(chat_id)
    if job_id:
        logger.info("P8 thread_id resolved via P3 (focus cache)")
        return job_id

    # P4: None（闲聊场景，不进 P8 working_memory）
    logger.info("P8 thread_id: no job context, fallback to chat_id")
    return None
```

### 4.2 thread_id 合成

```python
def build_p8_thread_id(
    chat_id: str,
    sender_open_id: Optional[str],
    effective_job_id: Optional[str],
) -> str:
    """合成最终 LangGraph thread_id"""

    # 主流程 / 隐式 job 场景：按 job 隔离
    if effective_job_id:
        return f"p8-{effective_job_id}"

    # 闲聊场景：按会话隔离（保留原有行为）
    chat_type = _get_chat_type(event)
    if chat_type in ("group",):
        return chat_id
    return sender_open_id or chat_id
```

### 4.3 焦点缓存设计

#### 4.3.1 数据模型

```python
# Redis 存储
# key:   chat:{chat_id}:focus_job_id
# value: { "job_id": "JOB-20260813-001",
#          "set_at": "2026-08-20T10:00:00+00:00",
#          "p8_job_id": "P8J-20260813-...",
#          "evidence": "用户原话或事件 ID" }
# TTL:   30 分钟（无活动过期）
```

#### 4.3.2 写入路径

```python
# 由 P8 新工具 set_focus_job(job_id, p8_job_id?, evidence) 写入
# LLM 在 system_prompt 指令下识别用户消息中的 job 提及，主动调该工具

class FocusCache:
    def set(self, chat_id: str, job_id: str, evidence: str, ttl: int = 1800) -> None: ...
    def get(self, chat_id: str) -> Optional[Dict[str, str]]: ...
    def delete(self, chat_id: str) -> None: ...
```

#### 4.3.3 失效策略

| 触发 | 行为 |
|------|------|
| 命中 TTL | 自动过期 |
| 用户说"换作业" / "切到 XXX" | LLM 调 `set_focus_job` 覆盖 |
| 用户说"忘了" / 主动清空 | LLM 调 `clear_focus_job` |
| P8_job 终态（completed/rejected） | middleware 触发 `clear_focus_job` |

### 4.4 LLM 识别机制

#### 4.4.1 system_prompt 增补段

```markdown
## 作业焦点识别（v0.1）

当用户消息中提及具体作业（如"作业 JOB-20260813-001"、"今天那个受限空间作业"）时：
1. 从历史消息 / working_memory / 卡片 callback metadata 中找出 `job_id`
2. 调用 `set_focus_job(job_id, evidence)` 写入焦点缓存
3. 后续消息不指定 job 时，默认走焦点 job 的 thread

不要臆造 job_id。如果不确定，**先调用 `recall_jobs(query=...)` 长期记忆搜索**确认。
```

#### 4.4.2 新增工具

| 工具 | 用途 | HITL |
|------|------|------|
| `set_focus_job(job_id, evidence)` | 写入 chat 焦点缓存 | ❌ 自动放行（影响轻） |
| `clear_focus_job()` | 主动清空焦点 | ❌ 自动放行 |
| `get_focus_job()` | 查询当前焦点 | ❌ 只读 |

### 4.5 卡片 callback 反查主流程 job_id

#### 4.5.1 链路

```
P8 LLM 调 notify_feishu(p8_job_id=P8J-XXX, ..., job_id=JOB-YYY)
   ↓ Gateway 推飞书 Card 2.0（含按钮）
   ↓ 用户点按钮 → 飞书 callback event.metadata = { p8_job_id, alert_id }
   ↓ chat_reply 收到 callback event
   ↓ P1: 查 event.metadata.job_id → 若为空，则查 event.metadata.p8_job_id
   ↓ P8 long_term 索引：p8_job_id → job_id（主流程）
   ↓ 合成 thread_id = f"p8-{job_id}"
```

#### 4.5.2 反查接口

```python
# 在 A7/storage/p8_long_term.py 已有索引层接口
# 2026-08-20 重构：长期记忆改为 per-job 真相源（无全局 _long_term/）；
# job_id 已注入 archived dict（save_archived_job 自动写入），
# 反查主流程 job_id 直接读 archived["job_id"] 即可
def lookup_job_id_by_p8_job_id(p8_job_id: str) -> Optional[str]:
    """P8 长期记忆索引层反查主流程 job_id"""
    # 索引条目格式：[<max_level>] <risk_basis>；<decision> by <decider> @ <archived_at>
    # archived dict 自带 job_id 字段（2026-08-20 注入），无需额外映射层
    ...
```

### 4.6 chat_reply 适配

#### 4.6.1 改动点

| 位置 | 改动 |
|------|------|
| `_compute_thread_id` (L548) | 拆分为 `resolve_effective_job_id` + `build_p8_thread_id` 两步 |
| `chat_reply_handler` 主流程 | 在 invoke P8 前调 `resolve_effective_job_id` |
| 日志格式 | 追加 `effective_job_id` + `resolution_path`（P1/P2/P3/P4） |

#### 4.6.2 不改的边界

- ✅ `web/server.py` Gradio 路由（继续用 `default`）
- ✅ `agents/p8_disposition_agent.py` 的 `disposition_demo(message, history)` 签名
- ✅ 主流程 `execute_p8(job_id)` 调用（继续用 `f"p8-{job_id}"`）

## 5. 实施优先级

### P0（最先做，~1-2 天）—— 方案 C 子集

**目标**：覆盖 80% P8 Bot 消息来源（卡片按钮回调）。

| 子任务 | 代码改动点 |
|--------|-----------|
| 卡片 callback event 携带 `job_id` metadata | `notify_feishu` 工具 + Gateway 卡片 payload |
| `chat_reply` 解析 event.metadata | `chat_reply_handler` 注入 `effective_job_id` |
| `p8_long_term` 加 `lookup_job_id_by_p8_job_id` 索引 | `A7/storage/p8_long_term.py` |
| 单元测试：卡片 callback 路径 | `tests/test_p8_callback_thread_isolation.py` |

### P1（~1-2 天）—— 方案 B 子集

**目标**：覆盖纯文本对话中隐含的 job 提及。

| 子任务 | 代码改动点 |
|--------|-----------|
| 焦点缓存后端（Redis / 旁路存储） | `A7/storage/focus_cache.py` |
| `set_focus_job` / `clear_focus_job` / `get_focus_job` 工具 | `agents/p8_disposition_agent.py` |
| system_prompt 增补"作业焦点识别"段 | `agents/system_prompt/P8_DISPOSITION_SYSTEM_PROMPT.md` |
| `chat_reply` 接入焦点解析 | `chat_reply.py` |

### P2（~1 天）—— 方案 A 兜底

**目标**：覆盖边缘场景（用户主动声明 job）。

| 子任务 | 代码改动点 |
|--------|-----------|
| `[job_id=XXX]` 消息前缀解析 | `chat_reply.py` |
| 前缀在 Bot 群里不强制展示（仅作内部 hint） |  |

### P3（~1 天）—— 集成测试 + 文档

| 子任务 | 代码改动点 |
|--------|-----------|
| 端到端测试：4 种解析路径全部覆盖 | `tests/test_p8_thread_isolation_e2e.py` |
| 更新 `docs/agents/P8_DISPOSITION_AGENT.md` / `P8_DISPOSITION_TOOLS.md` | |
| 更新 `docs/architecture.md` 引用 | |

## 6. 待澄清问题（需用户决策）

| # | 问题 | 默认建议 | 影响范围 |
|---|------|---------|---------|
| Q1 | 焦点缓存选哪个后端？ | Redis（已有基础设施，TTL 实现简单） | P1 子任务 |
| Q2 | `p8_job_id → job_id` 反查用哪种方式？ | 走 `p8_long_term` 索引层（零额外存储） | P0 子任务 |
| Q3 | 焦点 TTL 设多久？ | 30 分钟（无活动过期） | 焦点缓存模块 |
| Q4 | `effective_job_id=None` 时是否仍调 P8？ | 调，但 `working_memory` 不写入（闲谈话术走默认 thread_id） | `disposition_demo` |
| Q5 | 焦点切换是否需要 HITL 确认？ | 否（`set_focus_job` 影响轻；用户随时可"换作业"） | HITL 矩阵 |
| Q6 | 主流程的 `f"p8-{job_id}"` thread_id 命名是否要改？ | 不改（向后兼容） | 无 |
| Q7 | `chat_id/open_id` 兜底 thread_id 是否还需要保留？ | 保留（闲聊 / 非 P8 场景） | `_compute_thread_id` |
| Q8 | LLM 误判焦点时如何纠正？ | 用户说"不是这个，换 XXX" → LLM 调 `clear_focus_job` + `set_focus_job` | system_prompt 段 |

## 7. 风险与权衡

### 7.1 主要风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| LLM 焦点误判（特别是模糊语义） | 中 | 焦点 TTL 短 + 用户可纠正 + 日志可追溯 |
| 卡片 callback 链路长（notify → Gateway → 飞书 → callback → 反查） | 中 | P0 阶段加端到端测试 + 超时监控 |
| Redis 依赖（引入新组件） | 低 | 焦点缓存旁路化（也可走 LangGraph MemorySaver） |
| working_memory 跨 job 残留 | 低 | 终态 middleware 已自动清理（`P8ArchiveMiddleware`） |
| 旧 chat_reply 部署回滚 | 低 | 解析失败时回退到 `chat_id`（C5 优雅降级） |

### 7.2 与现有设计的一致性

- ✅ **per-job 持久化**（`A7/storage/p8_working_memory_store.py`）保持不变：`thread_id` 解析后仍是 `f"p8-{job_id}"`，落盘路径不变
- ✅ **P8ArchiveMiddleware** 终态归档逻辑不变
- ✅ **cache key 包含 job_id**（`agents/p8_disposition_agent.py:214`）不变

### 7.3 放弃的方案

- ❌ **方案 E（chat 内层分组）**：实现成本高、LLM 易混淆，放弃
- ❌ **方案 A（必须显式前缀）**：破坏 Bot 体验，仅作兜底

## 8. 后续动作

1. **决策 Q1 / Q2**：决定焦点缓存后端 + 反查路径 → 进入 P0 实施
2. **写 P0 实施 PR**：仅做卡片 callback 反查 thread_id（最小可用版本）
3. **观察运行数据**：P0 上线 1 周后，统计"未带 job 上下文的 P8 invoke"比例，决定 P1 是否值得做
4. **P1 / P2 / P3 按需推进**

## 9. 相关文档

- [`P8_人机协同处置_需求与Demo设计.md`](P8_人机协同处置_需求与Demo设计.md) — 蓝图 §7.2.1 thread_id 原始约定
- [`P8_人机协同处置_文件组织与职责.md`](P8_人机协同处置_文件组织与职责.md) — P8 模块文件组织
- [`docs/agents/P8_DISPOSITION_AGENT.md`](agents/P8_DISPOSITION_AGENT.md) — P8 Agent 文档
- [`docs/agents/P8_DISPOSITION_TOOLS.md`](agents/P8_DISPOSITION_TOOLS.md) — P8 工具 I/O 参考
- [`docs/agents/HUMAN_IN_THE_LOOP.md`](agents/HUMAN_IN_THE_LOOP.md) — HITL 中断机制
- [`docs/architecture.md`](architecture.md) — 系统架构