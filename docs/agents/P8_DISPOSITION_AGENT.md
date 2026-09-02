# P8: 人机协同处置 Agent (p8_disposition_agent)

## 概述

**人机协同处置专家**，负责创建和跟踪处置任务，按角色与权限推送责任人。

> **工具 I/O 详细参考**：每个工具的入参 / 出参 / 错误码 / 副作用 / 默认值，
> 见 [`P8_DISPOSITION_TOOLS.md`](P8_DISPOSITION_TOOLS.md)（2026-08-19 新增）。
> 本文件侧重整体架构 + 飞书集成 + 长期记忆；具体函数签名请翻 [工具参考](P8_DISPOSITION_TOOLS.md)。

## 主要功能

1. 创建处置任务
2. 按风险等级推送（消息/短信/电话）
3. 形成整改、暂停、复核或升级建议
4. 跟踪处置状态

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_disposition_agent(message)` | 运行 P8 Agent |
| `disposition_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `update_job` | 用户创建/更新处置任务 | 创建或更新 P8_job（reducer 按 pid upsert to working_memory） | ✅ 需要确认 |
| `hitl_decide` | 用户进入 HITL 决策 | 强制 channel=HITL + status=waiting_decision | ✅ 需要确认 |
| `read_p7_events` | 用户查看 P7 风险事件 | 读 `data/jobs/{job_id}/p7_result.json` | ❌ 自动批准 |
| `notify_feishu` | channel=PUSH 决策完成后推送 | **飞书交互式卡片推送（2026-08-19 重构走 feishu_sender 封装）** | ✅ 需要确认 |
| `list_active_p8_jobs` | 用户列出当前 in-progress P8_job | 读 working_memory（仅占位，REST 走 `/api/jobs/{job_id}/working-memory`） | ❌ 自动批准 |
| `recall_jobs` | 用户明确查询历史（"昨天那个事件最后怎么处理的"） | **长期记忆查询（罗盘长期记忆 LLM 入口）**：索引层子串搜索 + 数据层精确查询；两步走检索模式 | ❌ 自动批准（只读） |

> **罗盘长期记忆入口**：`recall_jobs` 是 P8 长期记忆的 **LLM 唯一入口**，
> 数据源 [A7/storage/p8_long_term.py](../../A7/storage/p8_long_term.py) 顶部"长期记忆接口（罗盘长期记忆）"注释块。
> 内部实现：索引层（轻量；一句话描述）+ 数据层（按需精确加载）；
> "两步走"检索：`recall_jobs(query)` 拿到索引 → `recall_jobs(detail_p8_job_id=...)` 拿完整归档。

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "disposition_create": True,    # 创建处置任务需要确认
        "disposition_confirm": True,    # 确认处置需要确认
        "disposition_status": False,     # 查询状态自动批准
        "disposition_list": False,      # 查询列表自动批准
        "recall_jobs": False,            # 长期记忆只读 — 自动批准（不阻塞）
    }
)
```

## 推送策略

| 风险等级 | 推送方式 | 处理时限 |
|----------|----------|----------|
| `LOW` | 消息推送 | 24h |
| `MEDIUM` | 短信+消息 | 4h |
| `HIGH` | 电话+短信+消息 | 1h |
| `CRITICAL` | 电话直达 | 实时 |

## 操作类型

| 操作 | 说明 |
|------|------|
| `approve` | 批准 |
| `reject` | 否决 |
| `escalate` | 升级 |
| `rectify` | 整改 |
| `pause` | 暂停作业 |
| `resume` | 恢复作业 |

## 人工控制点

- 高风险事件：必须人工确认
- 写入操作：必须人工确认
- 暂停作业：必须人工确认
- 恢复作业：必须人工确认

## 文件位置

`agents/p8_disposition_agent.py`

## 飞书回复格式（2026-08-19）

P8 Agent 在飞书侧的普通对话回复走 `A7/adapters/chat_reply.py` 的**纯文本模式**（`msg_type=text`），
飞书按纯文本渲染 markdown 加粗 / 列表 / 表格 / 代码块。

LLM 输出受 `agents/system_prompt/P8_DISPOSITION_SYSTEM_PROMPT.md` 的「飞书回复格式约束」段约束：

- **不要**用 `#`/`##`/`###` 任何级别的 Markdown 标题 → 改用 emoji + 加粗（`**📋 标题**`）
- **不要**对"提问类对话"输出 Markdown 表格 → 优先列表 + emoji；表格仅用于"数据汇报"
- **不要**用 `__` 双下划线（飞书 text 模式不识别）；加粗用 `**...**`
- **不要**输出超过 4000 字符的整段（飞书单消息上限；`chat_reply._truncate_for_feishu` 会自动截断）
- 鼓励 emoji、加粗列表；数字/状态/ID 必须加粗

代码链路：

```
LLM 回复 → _strip_think_tags → _truncate_for_feishu → reply_to_event(text=response_text)
  → Gateway /v1/messages/reply (msg_type=text) → Feishu /open-apis/im/v1/messages/{id}/reply
```

> **chat_reply 不再包卡片**：曾尝试把普通回消息也包成 Card 2.0 interactive 卡片
> （2026-08-19 中段），但用户反馈视觉冗余（红 header + 边框 + emoji 标题），要求
> "普通回消息不要走飞书 sender 调卡片格式"。故 chat_reply 退回纯文本模式，
> 卡片仅用于告警/处置推送（即 `notify_feishu` 工具）。

## notify_feishu 推送通道（2026-08-19 重构）

`update_job` 创建 P8_job 后若 channel=PUSH，LLM 会调 `notify_feishu` 推飞书卡片。重构后**走 `feishu_sender` 封装**（[`openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py`](../../openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py)），不再直调 `channel_gateway_client.send_message`。

### 重构原因（旧实现 3 个 bug）

| # | Bug | 旧值 | 应传 |
|---|-----|------|------|
| 1 | 缺 `conversation_id` | （未传） | chat_id / open_id（解析后的真实 ID） |
| 2 | 缺 `receive_id_type` | （未传） | `"chat_id"` 或 `"open_id"` |
| 3 | `account_id=assignee_role` 语义错乱 | `"属地责任人 + 班组长"`（岗位描述） | `"P8"` / `"alert-bot"`（机器人身份选择器） |

Gateway 校验 `conversation_id` 必填 → 报 `VALIDATION_ERROR / FEISHU_PUSH_FAILED`。

### 新签名（12 参数；2026-08-19 收紧）

```python
def notify_feishu(
    p8_job_id: str,
    title: str,
    body: str,
    risk_level: str,
    assignee_role: str,        # 责任岗位描述（仅记录/审计，**不是**收件人 ID）
    job_id: str,
    a6_event_ids: list[str],
    options: list[str],        # ★ 必填：["label:action", ...]（与 CLI --option 同构）
    *,
    # === 收件人（互斥必填其一；仅支持群发）===
    chat_id:    Optional[str] = None,  # 飞书群 ID（"oc_xxx"）直传
    group_name: Optional[str] = None,  # 按 FEISHU_GROUP_MAP.name 反查 chat_id
    # === 可选 ===
    alert_id:   Optional[str] = None,  # 业务告警 ID（callback 异步更新卡片；默认=p8_job_id）
    account_id: Optional[str] = None,  # Gateway 账号 ID（多账号 bot）
) -> str:
```

### 设计边界（与 chat_reply.py 分工）

| 场景 | 走哪个 | 格式 |
|------|--------|------|
| 普通对话 / 提问 / 任务状态汇报 | `chat_reply.py` 自动 | msg_type=text（纯文本） |
| 告警 / 处置推送（HITL 按钮需求） | `notify_feishu` 工具 | msg_type=interactive + Card 2.0 + 必带 buttons |

### 关键约束（2026-08-19 收紧）

1. **必传 `options`** —— 至少 1 个按钮，格式 `["label:action", ...]`。空列表 → `INVALID_ARGUMENT`。
2. **只支持群发** —— 移除 `name` / `open_id` 参数（单聊 DM 暂不支持 interactive 卡片）。
3. **必须有按钮** —— 不接受"无按钮的纯展示卡片"。卡片若无可交互内容，chat_reply 文本模式更轻量。

### 收件人选择决策（LLM 推理规则）

| 场景 | 推荐参数 | 解析路径 |
|------|----------|----------|
| 已知飞书群 ID | `chat_id="oc_xxx"` | 直传 |
| 不知道群 ID，记群名 | `group_name="应急响应群"` | FEISHU_GROUP_MAP 反查 |
| 已知用户 open_id | （不支持；用 group 发群里 @user） | — |
| 不知道 open_id，记姓名 | （不支持；用 group 发群里 @user） | — |

### 发送链路

```
LLM → notify_feishu(p8_job_id, title, body, ..., options=["已知悉:ack", "立即处理:handle"], group_name="应急响应群")
  → parse_options → [("已知悉","ack"), ("立即处理","handle")]
  → build_feishu_card(text=body, options=[("已知悉","ack"), ("立即处理","handle")], title=title, alert_id=alert_id)
  → feishu_sender.send_to_group_card(card, group_name="应急响应群", alert_id=alert_id, idempotency_key=p8_job_id)
    → channel_gateway_client.send_message(
        conversation_id=<chat_id from group_name>,
        receive_id_type="chat_id",
        msg_type="interactive",
        content=<Card 2.0 JSON>,
        ...
      )
  → Gateway :8787/v1/messages/send → 飞书 im/v1/messages
  → 用户点击按钮 → 飞书 callback → Gateway /webhooks/feishu → web /api/feishu/card-callback
  → P8 Agent 自动继续（action → hitl_decide）
```

### 仿真/模拟场景

直接调 `notify_feishu`（不通过 webhook）若不传 `chat_id/group_name` 或 `options=[]`，工具返回
`INVALID_ARGUMENT` 错误。这是**预期行为**，不是 bug：仿真必须显式指定收件人 ID
（`group_name="应急响应群"`）和至少 1 个按钮（`options=["已知悉:ack", "立即处理:handle"]`）。

## 长期记忆后端（罗盘长期记忆）

数据源：[`A7/storage/p8_long_term.py`](../../A7/storage/p8_long_term.py)

- **双层 JSON 持久化**：索引层（`p8_archive.index.json`，轻量）+ 数据层（`p8_archive.json`，完整）
- **仓库级全局**：路径 `data/jobs/_long_term/`；跨 job_id 共享
- **9 个公共函数**（详见源码顶部注释块）：
  - 写入（仅 P8ArchiveMiddleware）：`save_archived_job`
  - 数据层：`get_archived_job` / `search_archived_jobs` / `load_all_archived_jobs`
  - 索引层：`get_index_entry` / `search_archived_descriptions` / `load_all_index_entries`
  - 维护：`reset_archive`
- **索引条目格式**：`[<max_level>] <risk_basis 前 30 字>；<decision> by <decider> @ <archived_at 截 YYYY-MM-DD HH:MM>`
- **线程安全**：模块级 `threading.Lock` 包裹所有 IO
- **进程重启可恢复**：模块导入时自动 `_init()` 从 JSON 加载到内存
