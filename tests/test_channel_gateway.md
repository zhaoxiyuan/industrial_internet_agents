# tests/test_channel_gateway.md

> **Channel Gateway 客户端命令行测试文档**
>
> 与 `agents/_test_notify_e2e.py` 互补：后者是 P8 notify 端到端（USER_MAP →
> send_feishu → Gateway → 飞书），本文档是 `agents/channel_gateway_client.py`
> **逐接口**的命令行验证。
>
> **测试形式**：每个接口对应一个 `python -c "..."` 单行命令，可单独复制执行。
> **无副作用命令**（健康检查 / 读取 / 解析 / 列表查询）可立即跑；
> **副作用命令**（发消息 / ACK）需在终端明确看到输出后再执行。
>
> **前置条件**：
> - Gateway Standalone 已启动并监听 `GATEWAY_HOST`
> - 根 `.env` 已配置 `GATEWAY_HOST` / `CG_API_KEY` / `FEISHU_USER_MAP`
> - （发消息类）已建好测试群并把 `chat_id` 写入 `FEISHU_USER_MAP`

---

## 0. 接口总览（先看清要测什么）

| # | 接口 | HTTP | 测试命令分类 |
|---|------|------|------------|
| 1 | `GatewayConfig.from_env` | — | 配置读取（无副作用） |
| 2 | `client.health()` | `GET /healthz` | 健康检查（无副作用） |
| 3 | `client.ready()` | `GET /readyz` | 就绪检查（无副作用） |
| 4 | `send_message(...)` | `POST /v1/messages/send` | 主动发消息（有副作用） |
| 5 | `reply_to_event(...)` | `POST /v1/messages/reply` | 回复入站事件（有副作用） |
| 6 | `poll_inbound_events(...)` | `GET /v1/events` | 单次轮询（无副作用） |
| 7 | `iter_inbound_events(...)` | `GET /v1/events` | 持续轮询（流式，无副作用） |
| 8 | `ack_event(...)` | `POST /v1/events/{id}/ack` | 标记已处理（有副作用） |
| 9 | `client.retry_event(...)` | `POST /v1/events/{id}/retry` | 重投（幂等） |
| 10 | `client.list_outbound_intents(...)` | `GET /v1/outbound/intents` | 出站意图（无副作用） |
| 11 | `client.list_receipts(...)` | `GET /v1/receipts` | 出站回执（无副作用） |
| 12 | `client.list_dead_letters(...)` | `GET /v1/dead-letters` | 死信列表（无副作用） |
| 13 | `resolve_recipients(role)` | — | USER_MAP 解析（无副作用） |
| 14 | `get_conversation_id(role)` | — | USER_MAP 向后兼容（无副作用） |
| 15 | `get_user_map()` | — | USER_MAP 原始读取（无副作用） |
| 16 | `send_feishu(p8_job, job_id)` | — | P8 端到端推送（内部走接口 4，有副作用） |

---

## 1. 准备：确认依赖与配置

```bash
cd "c:/Users/13021/Desktop/agent-skill/industrial_internet_agents"

# 1.1 确认 channel_gateway_client 可导入
python -c "from agents.channel_gateway_client import ChannelGatewayClient; print('import OK')"

# 1.2 确认 feishu_gateway_cli USER_MAP 可导入（与 _test_notify_e2e.py 一致）
python -c "from feishu_gateway_cli import resolve_recipients, get_user_map; print('A7 OK')"

# 1.3 确认 dotenv 加载 .env 后 env vars 就位
python -c "import os; print('GATEWAY_HOST=', os.getenv('GATEWAY_HOST')); print('CG_API_KEY set=', bool(os.getenv('CG_API_KEY'))); print('FEISHU_USER_MAP set=', bool(os.getenv('FEISHU_USER_MAP')))"
```

期望：三个变量都有值。若 `CG_API_KEY set=False`，接口 3/4/.../12 会返回 401。

---

## 2. 接口 1：`GatewayConfig.from_env` — 配置读取

**原理**：从 `.env` 自动读取 `GATEWAY_HOST` / `CG_API_KEY` / `CG_DEFAULT_CHANNEL`，
兼容 `CHANNEL_GATEWAY_HOST` / `CHANNEL_GATEWAY_API_KEY` 两套命名。

```bash
python -c "
from agents.channel_gateway_client import GatewayConfig
c = GatewayConfig.from_env()
print(f'host={c.host}')
print(f'api_key_len={len(c.api_key)} (期望 16-50 字符)')
print(f'default_channel={c.default_channel}')
print(f'default_account_id={c.default_account_id}')
print(f'timeout={c.timeout}s')
"
```

期望：`host=http://127.0.0.1:8787`、`api_key_len > 0`、`default_channel=feishu`。

---

## 3. 接口 2：`client.health()` — 进程存活

**原理**：`GET /healthz` 是**唯一无需鉴权**的端点，只检查 Gateway 进程是否存活。

```bash
python -c "
from agents.channel_gateway_client import get_default_client
print(get_default_client().health())
"
```

期望：`{'status': 'ok'}` 或 `{'status': 'healthy'}`。若返回非 200 或报错：
- `Connection refused` → Gateway 未启动
- 超时 → 网络/防火墙

---

## 4. 接口 3：`client.ready()` — 状态存储就绪

**原理**：`GET /readyz` 鉴权，检查状态存储是否就绪（能否持久化事件）。
比 health 更严格——存活但存储坏了也算 not ready。

```bash
python -c "
from agents.channel_gateway_client import get_default_client
print(get_default_client().ready())
"
```

期望：200 + 类似 `{'ready': true}`。401 则 `CG_API_KEY` 配错。

---

## 5. 接口 13/14/15：USER_MAP 解析（无副作用，优先验证）

**原理**：
- `get_user_map()` 读 `FEISHU_USER_MAP` 环境变量，返回
  `{open_id: {chat_id, role, name}}` 原始字典
- `resolve_recipients(role)` 按 role 过滤，返回 `List[RecipientInfo]`
  （每个含 `receive_id_type`，自动判定 chat_id 群聊 / open_id 单聊）
- `get_conversation_id(role)` 向后兼容：返回首个匹配收件人的
  `chat_id` 或 `open_id`（旧代码用）

### 5.1 `get_user_map()` 原始读取

```bash
python -c "
from feishu_gateway_cli import get_user_map
um = get_user_map()
print(f'USER_MAP 总数: {len(um)}')
for oid, info in um.items():
    print(f'  · {oid}  role={info[\"role\"]}  name={info[\"name\"]}  chat_id={info.get(\"chat_id\", \"\") or \"(空→单聊)\"}')
"
```

### 5.2 `resolve_recipients(role)` 按 role 解析

```bash
# 列出 USER_MAP 中所有不同的 role
python -c "
from feishu_gateway_cli import get_user_map
print('USER_MAP 中的 role:', sorted({info['role'] for info in get_user_map().values()}))
"

# 按 role 解析（替换下面的 role 为实际值）
python -c "
from feishu_gateway_cli import resolve_recipients
role = '作业负责人'   # ← 改成 USER_MAP 里真实存在的 role
recs = resolve_recipients(role)
print(f'{role}: {len(recs)} 个收件人')
for r in recs:
    print(f'  · name={r.name}  open_id={r.open_id}  chat_id={r.chat_id or \"(空)\"}  type={r.receive_id_type}')
"

# 不存在的 role 触发 DEV_FALLBACK
python -c "
from feishu_gateway_cli import resolve_recipients
print(resolve_recipients('不存在XYZ_ROLE'))
"
```

### 5.3 `get_conversation_id(role)` 向后兼容

```bash
python -c "
from feishu_gateway_cli import get_conversation_id
print(get_conversation_id('作业负责人'))    # 期望: oc_xxx 或 ou_xxx
print(get_conversation_id('不存在XYZ_ROLE')) # 期望: feishu_dev_不存在XYZ_ROLE
"
```

---

## 6. 接口 4：`send_message(...)` — 主动发消息

**原理**：`POST /v1/messages/send` 把消息作为「出站意图」提交给 Gateway。
Gateway 异步调飞书 API，回执存到 `list_receipts()`。
`idempotency_key` 用相同值重复调用会返回**原结果**（`replayed=True`）。

### 6.1 单收件人：群聊（chat_id）

```bash
python -c "
from agents.channel_gateway_client import send_message
res = send_message(
    text='【测试】Channel Gateway 主动发消息 - 群聊',
    conversation_id='oc_你的chat_id',
    receive_id_type='chat_id',
    idempotency_key='test-cli-001',
)
print(f'intent_id={res.intent_id}')
print(f'status={res.status}')
print(f'platform_message_id={res.platform_message_id}')
print(f'replayed={res.replayed}')
"
```

### 6.2 单收件人：单聊（open_id）

```bash
python -c "
from agents.channel_gateway_client import send_message
res = send_message(
    text='【测试】Channel Gateway 主动发消息 - 单聊',
    receive_id='ou_你的open_id',
    receive_id_type='open_id',
    idempotency_key='test-cli-002',
)
print(f'status={res.status}, msg={res.platform_message_id}')
"
```

### 6.3 幂等键验证（重放保护）

```bash
python -c "
from agents.channel_gateway_client import send_message
# 第一次
r1 = send_message(
    text='幂等测试',
    conversation_id='oc_你的chat_id',
    receive_id_type='chat_id',
    idempotency_key='idem-key-12345',
)
# 第二次（同样的 key，应该 replayed=True 且 intent_id 一致）
r2 = send_message(
    text='幂等测试',
    conversation_id='oc_你的chat_id',
    receive_id_type='chat_id',
    idempotency_key='idem-key-12345',
)
assert r1.intent_id == r2.intent_id, '幂等失败!'
assert r2.replayed is True, '第二次应标记 replayed'
print(f'OK: intent_id={r1.intent_id} 在两次调用中一致; replayed={r2.replayed}')
"
```

### 6.4 Gateway 错误处理（鉴权失败）

```bash
python -c "
from agents.channel_gateway_client import configure_default_client, send_message, GatewayConfig, GatewayError
# 故意配一个错的 key
configure_default_client(GatewayConfig(host='http://127.0.0.1:8787', api_key='wrong-key'))
try:
    send_message(text='test', conversation_id='oc_x', receive_id_type='chat_id')
except GatewayError as e:
    print(f'caught GatewayError: code={e.code} status={e.status_code} retryable={e.retryable}')
"
# 测试完恢复默认客户端
python -c "from agents.channel_gateway_client import configure_default_client, GatewayConfig; configure_default_client(GatewayConfig.from_env()); print('default client restored')"
```

期望：`code='UNAUTHORIZED'` 或 `HTTP_401`。

---

## 7. 接口 16：`send_feishu(p8_job, job_id)` — P8 端到端推送

**原理**：这是 `_test_notify_e2e.py` 的核心路径——P8 业务层把
`P8Job` 转成飞书消息，内部走 `resolve_recipients` → 对每个收件人调
`channel_gateway_client.send_message`。返回 `FeishuNotifyResult`，含
`status` (`ok` / `partial` / `failed`) 和每个收件人的 `RecipientStatus`。

### 7.1 用临时 USER_MAP 跑端到端（不污染真实 .env）

```bash
python -c "
import os, json, tempfile
from pathlib import Path
import re
# 把临时 USER_MAP 写进临时 .env（不改真实文件）
tmp = tempfile.mkdtemp(prefix='e2e_')
env_path = Path(tmp) / '.env'
src = Path('.env')
if src.exists():
    env_path.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
test_role = 'E2E_TEST_ROLE'
test_chat = 'oc_你的chat_id'  # ← 替换
test_open = 'ou_e2e_only'
new_map = json.dumps({test_open: {'chat_id': test_chat, 'role': test_role, 'name': 'E2E测试'}}, ensure_ascii=False, separators=(',', ':'))
content = env_path.read_text(encoding='utf-8')
if 'FEISHU_USER_MAP=' in content:
    content = re.sub(r'FEISHU_USER_MAP=.*', f'FEISHU_USER_MAP={new_map}', content)
else:
    content += f'\nFEISHU_USER_MAP={new_map}\n'
env_path.write_text(content, encoding='utf-8')
os.environ['FEISHU_USER_MAP'] = new_map
# 同时让 channel_gateway_client 用这份临时 .env
import feishu_gateway_cli.feishu_config_app as cfg
cfg.ENV_FILE = env_path

# 构造 P8Job
from A7.schema.p8_models import P8Job, RiskLevel, P8JobStatus, Channel
from feishu_gateway_cli import send_feishu
job = P8Job(
    p8_job_id='P8-CLI-E2E-001',
    a6_event_ids=['A6-E2E-1'],
    risk_basis='命令行端到端测试：可燃气体浓度超标',
    max_level=RiskLevel.HIGH,
    level_distribution={RiskLevel.HIGH: 1},
    urgency_emoji='🟠',
    assignee_role=test_role,
    channel=Channel.PUSH,
    status=P8JobStatus.pending,
    decision=None,
    note='cli test',
    due_at='2026-08-13T19:00:00+00:00',
    created_at='2026-08-13T18:00:00+00:00',
)
result = send_feishu(job, job_id='CLI-E2E-JOB-001')
print(f'status={result.status}  recipients={len(result.recipients)}')
for rs in result.recipients:
    print(f'  · {rs.name} {rs.open_id} status={rs.status} msg={rs.message_id} err={rs.error}')
"
```

期望：`status='ok'`，飞书群收到消息。若 `status='failed'`，看 `result.error`。

### 7.2 多收件人：USER_MAP 同 role 多人

```bash
python -c "
import os, json
# 临时塞两个同 role 的人进 USER_MAP
multi_map = {
    'ou_person_a': {'chat_id': 'oc_test_group', 'role': 'multi_role', 'name': '甲'},
    'ou_person_b': {'chat_id': '',            'role': 'multi_role', 'name': '乙'},
}
os.environ['FEISHU_USER_MAP'] = json.dumps(multi_map, ensure_ascii=False, separators=(',', ':'))

from feishu_gateway_cli import resolve_recipients, send_feishu
from A7.schema.p8_models import P8Job, RiskLevel, P8JobStatus, Channel
print(f'收件人: {len(resolve_recipients(\"multi_role\"))}')

job = P8Job(
    p8_job_id='P8-CLI-MULTI', a6_event_ids=['x'],
    risk_basis='multi test', max_level=RiskLevel.MEDIUM,
    level_distribution={RiskLevel.MEDIUM: 1}, urgency_emoji='🟡',
    assignee_role='multi_role', channel=Channel.PUSH, status=P8JobStatus.pending,
    decision=None, note='', due_at='2026-08-13T19:00:00+00:00',
    created_at='2026-08-13T18:00:00+00:00',
)
result = send_feishu(job, job_id='CLI-MULTI')
print(f'agg_status={result.status}')
ok = sum(1 for r in result.recipients if r.status == 'ok')
print(f'成功 {ok}/{len(result.recipients)}')
for rs in result.recipients:
    print(f'  · {rs.name} type={rs.status} msg={rs.message_id}')
"
```

期望：`agg_status` 是 `ok`（全部成功）或 `partial`（部分成功）。

---

## 8. 接口 6/7：轮询入站事件

**原理**：`GET /v1/events?after_sequence=N` 拉取 Gateway 持久化的入站事件。
`after_sequence` 单调递增 → **天然断点续传**。事件被 `ack_event`
标记后从默认 active 列表移除（或按 `status` 过滤查询）。

### 8.1 `poll_inbound_events` 单次拉取

```bash
python -c "
from agents.channel_gateway_client import poll_inbound_events
r = poll_inbound_events(after_sequence=0, limit=10)
print(f'events={len(r.events)} latestSequence={r.latest_sequence}')
for e in r.events[:5]:
    txt = e.get('message', {}).get('text', '')
    sender = e.get('sender', {}).get('id', '?')
    conv = e.get('conversation', {}).get('id', '?')
    print(f'  seq={e.get(\"sequence\")} from={sender} conv={conv} text={txt[:40]}')
"
```

期望：先在飞书给 Bot 发条消息，再跑这条命令 → 至少拿到 1 条事件。

### 8.2 `iter_inbound_events` 持续轮询（生成器）

```bash
# 持续 30 秒（按 Ctrl+C 退出）
python -c "
from agents.channel_gateway_client import iter_inbound_events
import time
print('开始持续轮询 30s（飞书给 Bot 发消息会立即收到）...')
deadline = time.time() + 30
for ev in iter_inbound_events(initial_sequence=0, idle_sleep=1.0, poll_interval=2.0):
    print(f'  seq={ev.get(\"sequence\")} text={ev.get(\"message\", {}).get(\"text\", \"\")[:50]}')
    if time.time() > deadline:
        break
print('done.')
"

# stop_on_empty 模式（一次性消费，用于批处理）
python -c "
from agents.channel_gateway_client import iter_inbound_events
print('单次空轮询模式：')
for ev in iter_inbound_events(stop_on_empty=True, max_iterations=3):
    print(ev.get('id'), ev.get('message', {}).get('text', '')[:30])
print('exit.')
"
```

---

## 9. 接口 5：`reply_to_event(...)` — 回复入站事件

**原理**：`POST /v1/messages/reply` 继承原事件的 channel / 会话上下文。
需先有一个入站事件 id（来自接口 6/7）。

```bash
# 9.1 拉一个事件
EVENT_ID=$(python -c "
from agents.channel_gateway_client import poll_inbound_events
r = poll_inbound_events(after_sequence=0, limit=1)
print(r.events[0]['id'] if r.events else '')
")
echo "event_id=$EVENT_ID"

# 9.2 回复它
python -c "
from agents.channel_gateway_client import reply_to_event
import sys
event_id = '$EVENT_ID'
if not event_id:
    print('没有事件，先去飞书发一条'); sys.exit(1)
res = reply_to_event(event_id=event_id, text='【自动回复】已收到')
print(f'status={res.status} msg={res.platform_message_id}')
"
```

---

## 10. 接口 8：`ack_event(...)` — 标记事件已处理

**原理**：`POST /v1/events/{id}/ack` 标记为 `acked` 或 `ignored`，
避免后续轮询重复投递。

```bash
python -c "
from agents.channel_gateway_client import poll_inbound_events, ack_event
r = poll_inbound_events(after_sequence=0, limit=5)
for e in r.events:
    print(f'ack seq={e.get(\"sequence\")} id={e[\"id\"]}')
    ret = ack_event(event_id=e['id'], status='acked', details={'consumer': 'cli-test'})
    print(f'  -> {ret}')
"
```

期望：返回 `{'status': 'acked', ...}` 或类似。

### 接口 9：`retry_event(...)` — 重投

```bash
# 把刚 ack 的事件重新置回 pending
python -c "
from agents.channel_gateway_client import poll_inbound_events, retry_event
r = poll_inbound_events(after_sequence=0, limit=1, status='acked')
if r.events:
    eid = r.events[0]['id']
    print(retry_event(event_id=eid))
else:
    print('没有已 ack 的事件')
"
```

---

## 11. 接口 10/11/12：出站意图 / 回执 / 死信

**原理**：发消息会产生 `intent`（意图）和 `receipt`（回执）。
Gateway 在投递失败超过阈值时把事件移到 `dead-letters`。

### 11.1 出站意图列表

```bash
python -c "
from agents.channel_gateway_client import get_default_client
c = get_default_client()
print('--- sent ---')
print(c.list_outbound_intents(status='sent', limit=10))
print('--- failed ---')
print(c.list_outbound_intents(status='failed', limit=10))
"
```

### 11.2 出站回执列表

```bash
python -c "
from agents.channel_gateway_client import get_default_client
print(get_default_client().list_receipts(limit=10))
"
```

### 11.3 死信列表

```bash
python -c "
from agents.channel_gateway_client import get_default_client
print(get_default_client().list_dead_letters())
"
```

---

## 12. 端到端综合（与 `_test_notify_e2e.py` 对齐）

把上面所有验证串起来跑一遍，等价于 `agents/_test_notify_e2e.py`
的 TEST 1+3+5（不含 TEST 2 dataclass / TEST 4 真实推送，那是
notify 自己的端到端，本文档测的是 gateway client 接口）：

```bash
python -c "
print('=== Step 1: Gateway health ===')
from agents.channel_gateway_client import get_default_client
print(get_default_client().health())

print('=== Step 2: USER_MAP resolve ===')
from feishu_gateway_cli import resolve_recipients
for role in ['作业负责人', '车间主任']:
    recs = resolve_recipients(role)
    print(f'  {role}: {len(recs)}')

print('=== Step 3: events poll ===')
from agents.channel_gateway_client import poll_inbound_events
r = poll_inbound_events(after_sequence=0, limit=10)
print(f'  events={len(r.events)} latestSequence={r.latest_sequence}')

print('=== Step 4: outbox intents ===')
print(f'  intents={len(get_default_client().list_outbound_intents(limit=20).get(\"intents\", []))}')
"
```

---

## 13. 故障排查

| 现象 | 排查命令 / 原因 |
|------|---------------|
| `Connection refused` | `health()` 失败 → Gateway 未启动（端口 8787 没人监听） |
| `401 Unauthorized` | `CG_API_KEY` 与 Gateway `--api-key` 不一致 |
| `400` `idempotency_key missing` | 部分 Gateway 强制要求 `Idempotency-Key` 头 |
| `ambiguous: true` in GatewayError | Gateway 返回了矛盾状态（发送过程中网络抖），建议重试 |
| `chat_id` 群消息没人收到 | 检查 Bot 是否在群里；`FEISHU_USER_MAP` 里该 role 对应的人是否在群里 |
| `status='failed'` in FeishuNotifyResult | 看 `result.error`，通常是 chat_id 失效 / Bot 权限不足 |
| `feishu_dev_xxx` 占位符 | `resolve_recipients` 没命中 USER_MAP，role 名拼写或 USER_MAP 没加载 |
| `env_path` 看错 .env | `python -c "from agents.channel_gateway_client import get_default_client; print(get_default_client().config)"` |

---

## 14. 与 `_test_notify_e2e.py` 对照

| 文档章节 | 对应 `_test_notify_e2e.py` | 本文档扩展点 |
|---------|---------------------------|------------|
| §5 USER_MAP 解析 | TEST 1（`resolve_recipients` / `get_conversation_id`） | 加 `get_user_map()` 原始读取 |
| §6 主动发消息 | TEST 4（`send_feishu`） | 加 `send_message` 直调 + 幂等验证 |
| §7 send_feishu | TEST 4 | 多收件人 USER_MAP 演示 |
| §8 events 轮询 | TEST 5（`/v1/events`） | 加 `iter_inbound_events` 持续模式 |
| §6.4 错误处理 | — | `GatewayError.code/retryable/ambiguous` 验证 |
| §11 outbox 查询 | — | `list_outbound_intents/receipts/dead_letters` |

---

## 15. 一键回归（把所有"无副作用"测试串起来）

```bash
python -c "
import sys
fails = []

def check(name, fn):
    try:
        fn()
        print(f'  [OK] {name}')
    except AssertionError as e:
        print(f'  [FAIL] {name}: {e}')
        fails.append(name)
    except Exception as e:
        print(f'  [ERR]  {name}: {type(e).__name__}: {e}')
        fails.append(name)

print('== A. 配置 & 健康 ==')
from agents.channel_gateway_client import GatewayConfig, get_default_client
check('from_env', lambda: (GatewayConfig.from_env(),))
check('health', lambda: (get_default_client().health(),))
check('ready', lambda: (get_default_client().ready(),))

print('== B. USER_MAP ==')
from feishu_gateway_cli import get_user_map, resolve_recipients, get_conversation_id
check('user_map_nonempty', lambda: get_user_map() and None or (_ for _ in ()).throw(AssertionError('空 USER_MAP')))
check('resolve_work_lead', lambda: resolve_recipients('作业负责人') if '作业负责人' in {i['role'] for i in get_user_map().values()} else None)

print('== C. events 拉取 ==')
from agents.channel_gateway_client import poll_inbound_events, list_outbound_intents, list_receipts, list_dead_letters
check('poll', lambda: (poll_inbound_events(after_sequence=0, limit=5),))
check('list_intents', lambda: (list_outbound_intents(limit=5),))
check('list_receipts', lambda: (list_receipts(limit=5),))
check('list_dead', lambda: (list_dead_letters(),))

print('== D. 异常路径 ==')
from agents.channel_gateway_client import configure_default_client, GatewayConfig, send_message, GatewayError
old_cfg = GatewayConfig.from_env()
configure_default_client(GatewayConfig(host=old_cfg.host, api_key='wrong'))
def t_401():
    try:
        send_message(text='x', conversation_id='oc_x', receive_id_type='chat_id')
    except GatewayError as e:
        assert e.status_code == 401, f'期望 401 实得 {e.status_code}'
        return
    raise AssertionError('期望 GatewayError')
check('401 on wrong key', t_401)
configure_default_client(old_cfg)

if fails:
    print(f'\\n[FAIL] {len(fails)} 项: {fails}')
    sys.exit(1)
print('\\n[ALL OK] 全部无副作用测试通过')
"
```

期望：所有 `[OK]`。任一 `[FAIL]/[ERR]` 表示对应接口不工作，按 §13 排查。