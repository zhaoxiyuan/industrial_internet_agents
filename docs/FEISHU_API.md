# 飞书标准接口说明

本文描述 P8 与飞书之间的现行链路、数据格式和标准接口。低层 Gateway HTTP 客户端详见 [Gateway 标准接口](GATEWAY.md)，应用创建、凭据和 Webhook 配置见 [飞书配置教程](FEISHU_CONFIG.md)。

## 1. 组件与职责

```text
P8 Agent
  ├─ 普通对话 → A7.adapters.chat_reply → Channel Gateway → 飞书文本回复
  └─ 处置通知 → notify_feishu → feishu_sender
                         ├─ 飞书 CardKit：创建卡片实体
                         └─ Channel Gateway：发送 card_id 引用

飞书按钮回调
  → Gateway webhook
  → Web :8080 /api/feishu/card-callback
  → feishu_card.process_card_callback
  → CardActionAgent 更新 P8Job
  → CardKit PUT / IM PATCH 原位更新同一条消息
```

服务启动顺序由 `A7/adapters/p8_service_manager.py` 定义：`gateway → chat_reply → web`；停止顺序相反。

## 2. 两类消息必须分开

| 场景 | 接口 | 飞书格式 |
|---|---|---|
| 用户普通提问、状态查询、Agent 回复 | `chat_reply_handler` / `reply_to_event` | `msg_type=text` |
| 告警、处置选择、需要按钮 | `notify_feishu` | Card 2.0 interactive |

普通回复不要包装成卡片；卡片必须用于有明确操作按钮的处置场景。

## 3. 卡片发送标准接口

P8 工具：

```python
notify_feishu(
    p8_job_id: str,
    title: str,
    body: str,
    risk_level: str,
    assignee_role: str,
    job_id: str,
    a6_event_ids: list[str],
    options: list[str],
    *,
    chat_id: str | None = None,
    group_name: str | None = None,
    alert_id: str | None = None,
    account_id: str | None = None,
) -> str
```

约束：

- `chat_id` 与 `group_name` 二选一且互斥；当前交互卡只支持群发。
- `options` 至少一个，格式为 `显示文字:action`，例如 `立即处理:handle`。
- `alert_id` 默认等于 `p8_job_id`。
- `idempotency_key` 默认等于 `p8_job_id`。
- `assignee_role` 是业务岗位描述，不是飞书账号或收件人 ID。

适配层标准接口：

| 接口 | 作用 |
|---|---|
| `parse_options(list[str]) -> list[(label, action)]` | 解析按钮配置 |
| `build_feishu_card(text, options, title, alert_id, job_id) -> dict` | 构造 Card 2.0，不访问网络 |
| `send_to_group_card(card, *, chat_id/group_name, account_id, alert_id, idempotency_key)` | 创建实体并经 Gateway 发送 |
| `register_card(alert_id, card_id, ...)` | 保存卡片实体索引 |
| `lookup_card_id(alert_id)` | 查询卡片实体及 sequence |

## 4. Card 2.0 数据结构

```json
{
  "schema": "2.0",
  "header": {
    "template": "red",
    "title": {"tag": "plain_text", "content": "高风险告警"}
  },
  "body": {
    "elements": [
      {"tag": "markdown", "content": "告警正文"},
      {
        "tag": "column_set",
        "columns": [
          {
            "tag": "column",
            "elements": [{
              "tag": "button",
              "text": {"tag": "plain_text", "content": "立即处理"},
              "type": "primary",
              "value": "{\"action\":\"handle\",\"alert_id\":\"P8J-...\",\"job_id\":\"20260914123000123\"}"
            }]
          }
        ]
      }
    ]
  }
}
```

飞书 Card 2.0 要求按钮 `value` 是 JSON 字符串，不是对象。

## 5. 发送流程

1. `build_feishu_card` 构建卡片 JSON。
2. CardKit `POST /open-apis/cardkit/v1/cards/` 创建实体并取得 `card_id`。
3. Gateway `POST /v1/messages/send` 发送 interactive 消息，content 为：

```json
{"type":"card","data":{"card_id":"<card_id>"}}
```

4. `data/feishu_card_index.json` 保存 `alert_id → card_id/message_id/account_id/sequence/card_json`。
5. 该索引是运行时文件，已加入 `.gitignore`；删除后可重新生成，但已有卡片的原位更新能力会丢失。

CardKit 创建、Gateway 发送、后续更新必须使用同一个飞书应用账号。`account_id` 为空时使用 `CG_DEFAULT_ACCOUNT_ID`，再回退 `default`。

## 6. 按钮回调标准接口

Web 入口：

```http
POST /api/feishu/card-callback
GET  /api/feishu/card-callbacks?limit=50
```

回调业务字段来自 `event.action.value`，兼容 dict、单层 JSON 字符串和双层 JSON 字符串。解码后：

```json
{
  "action": "handle",
  "alert_id": "P8J-...",
  "job_id": "20260914123000123"
}
```

处理顺序：

1. `url_verification` 直接返回 challenge。
2. 校验 `event.action.value.action`。
3. 按 alert_id 查询审计日志，拦截重复终态操作。
4. 追加 `data/card_callbacks.jsonl` 审计记录。
5. 在 daemon 线程运行 `CardActionAgent`，快速向飞书返回 toast。
6. Agent 成功更新 P8Job 后，原位更新卡片。

同步响应只返回 toast，不在 callback 响应中返回 card：

```json
{"toast":{"type":"success","content":"已记录您的处置（立即处理）"}}
```

## 7. 原位更新和幂等

- CardKit 新卡：`PUT /cardkit/v1/cards/{card_id}`，`sequence` 必须严格递增。
- 历史 inline 卡：降级为 `PATCH /im/v1/messages/{message_id}`。
- 不撤回、不重发，因此群聊中保持同一 message_id。
- 飞书返回卡片交互中错误 `200810` 时有限退避重试。
- 同一 alert_id 的终态点击由审计日志拦截。
- `ack/handle` 是非终态第一步，卡片会切换为第二阶段操作；`approve/reject/escalate/resume/rectify/false_alarm` 属于终态动作。

旧卡如果缺少 job_id，只记录审计并做兼容展示，不调用 CardActionAgent，避免错误更新其他作业。

## 8. 文本回复、重试与 outbox

`A7.adapters.chat_reply.chat_reply_handler(event)`：

1. 过滤非 P8 或已终态事件。
2. 解析文本、用户身份和可选 `[job_id=...]`。
3. 群聊用 chat_id、单聊用 open_id 作为会话隔离键。
4. 调 `disposition_demo`。
5. 清理思考标签并截断到飞书限制。
6. 用 `reply-{event_id}` 作为幂等键回复并 ACK。

可安全判定的临时失败会重试；结果不确定的失败写入 A7 outbox，由 worker 使用同一幂等键重发，防止重复消息。

## 9. 运行时数据与安全

| 路径 | 作用 | 是否提交 Git |
|---|---|---|
| `data/feishu_card_index.json` | card_id、message_id、sequence | 否 |
| `data/card_callbacks.jsonl` | 按钮审计日志 | 否 |
| `A7/data/` | daemon/outbox/运行日志 | 否 |
| `.env` | app secret、Gateway 配置 | 否 |

日志不得输出 app secret、完整 token 或 Authorization 头。公网部署详情页时设置 `P8_DETAIL_BASE_URL`，默认的 `127.0.0.1:8080` 仅适合本机。

## 10. 最小排障顺序

1. Gateway 是否健康、账号是否启用。
2. `chat_id/group_name` 是否能解析，CardKit 和 Gateway 是否使用同一 account_id。
3. `options` 是否符合 `label:action`，按钮 value 是否含 job_id。
4. Web 8080 是否收到 `/api/feishu/card-callback`。
5. `data/card_callbacks.jsonl` 是否新增审计。
6. `data/jobs/{job_id}/P8/working_memory.json` 是否更新。
7. `data/feishu_card_index.json` 的 card_id、message_id、sequence 是否完整。

P8 状态语义见 [P8 人机协同处置](P8.md)。
