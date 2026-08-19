# P8: 人机协同处置 Agent (p8_disposition_agent)

## 概述

**人机协同处置专家**，负责创建和跟踪处置任务，按角色与权限推送责任人。

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

P8 Agent 在飞书侧的回复由 `A7/adapters/chat_reply.py` 包成飞书 **interactive 卡片**
（`msg_type="interactive"` + `lark_md` 标签），markdown 标题/表格/列表可正常渲染。

LLM 输出受 `agents/system_prompt/P8_DISPOSITION_SYSTEM_PROMPT.md` 的「飞书回复格式约束」段约束：

- **不要**用 `#`/`##`/`###` 任何级别的 Markdown 标题 → 改用 emoji + 加粗（`**📋 标题**`）
- **不要**对"提问类对话"输出 Markdown 表格 → 优先列表 + emoji；表格仅用于"数据汇报"
- **不要**用 `__` 双下划线（`lark_md` 不识别）；加粗用 `**...**`
- **不要**输出超过 4000 字符的整段（飞书单消息上限；`chat_reply._truncate_for_feishu` 会自动截断）
- 鼓励 emoji、加粗列表；数字/状态/ID 必须加粗

代码链路：

```
LLM 回复 → _strip_think_tags → _truncate_for_feishu → _build_feishu_card
  → reply_to_event(msg_type="interactive", content=<card JSON>)
  → Gateway /v1/messages/reply → Feishu /open-apis/im/v1/messages/{id}/reply
```

## notify_feishu 推送通道（2026-08-19 重构）

`update_job` 创建 P8_job 后若 channel=PUSH，LLM 会调 `notify_feishu` 推飞书卡片。重构后**走 `feishu_sender` 封装**（[`openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py`](../../openclaw-channel-gateway-standalone/feishu_gateway_cli/feishu_sender.py)），不再直调 `channel_gateway_client.send_message`。

### 重构原因（旧实现 3 个 bug）

| # | Bug | 旧值 | 应传 |
|---|-----|------|------|
| 1 | 缺 `conversation_id` | （未传） | chat_id / open_id（解析后的真实 ID） |
| 2 | 缺 `receive_id_type` | （未传） | `"chat_id"` 或 `"open_id"` |
| 3 | `account_id=assignee_role` 语义错乱 | `"属地责任人 + 班组长"`（岗位描述） | `"P8"` / `"alert-bot"`（机器人身份选择器） |

Gateway 校验 `conversation_id` 必填 → 报 `VALIDATION_ERROR / FEISHU_PUSH_FAILED`。

### 新签名（13 参数）

```python
def notify_feishu(
    p8_job_id: str,
    title: str,
    body: str,
    risk_level: str,
    assignee_role: str,        # 责任岗位描述（仅记录/审计，**不是**收件人 ID）
    job_id: str,
    a6_event_ids: list[str],
    *,
    # === 收件人（互斥必填其一）===
    chat_id:    Optional[str] = None,  # 飞书群 ID（"oc_xxx"）直传
    group_name: Optional[str] = None,  # 按 FEISHU_GROUP_MAP.name 反查 chat_id
    name:       Optional[str] = None,  # 按 FEISHU_USER_MAP.name 反查 open_id
    open_id:    Optional[str] = None,  # 飞书用户 open_id 直传
    # === 可选 ===
    alert_id:   Optional[str] = None,  # 业务告警 ID（callback 异步更新卡片）
    account_id: Optional[str] = None,  # Gateway 账号 ID（多账号 bot）
) -> str:
```

### 收件人选择决策（LLM 推理规则）

| 场景 | 推荐参数 | 解析路径 |
|------|----------|----------|
| 已知飞书群 ID | `chat_id="oc_xxx"` | 直传 |
| 不知道群 ID，记群名 | `group_name="应急响应群"` | FEISHU_GROUP_MAP 反查 |
| 已知用户 open_id（webhook 回调场景） | `open_id="ou_xxx"` | 直传 |
| 不知道 open_id，记姓名 | `name="张三"` | FEISHU_USER_MAP 反查 |

### 发送链路

```
LLM → notify_feishu(p8_job_id, title, body, ..., name="张三")
  → build_feishu_card(text=body, options=[], title=title, alert_id=p8_job_id)
  → feishu_sender.send_to_user(name="张三")  # 解析 name → open_id
    → channel_gateway_client.send_message(
        conversation_id=<open_id>,
        receive_id_type="open_id",
        msg_type="interactive",
        content=<Card 2.0 JSON>,
        ...
      )
  → Gateway :8787/v1/messages/send → 飞书 im/v1/messages
```

### 仿真/模拟场景

直接调 `notify_feishu`（不通过 webhook）若只传 `assignee_role="属地责任人 + 班组长"` 而不传 `chat_id/group_name/name/open_id`，工具返回 `INVALID_ARGUMENT` 错误。这是**预期行为**，不是 bug：仿真必须显式指定收件人 ID（`name="张三"` 或 `group_name="应急响应群"`）。

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
