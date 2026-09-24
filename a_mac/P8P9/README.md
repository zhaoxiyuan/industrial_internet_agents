# P8P9 — 风险处置闭环项目

按 [`docs/风险处置卡片交互设计.md`](../docs/风险处置卡片交互设计.md) 设计的 **2 agent + 3 service + 1 状态机层** 架构。

> ⚠️ **本项目不包含 LLM agent**。只留 3 个白名单接口给 agent 初始化 / 读状态；
> **所有状态变更必须经飞书按钮 → `callback_router` 或 Web 端 → `web_server`**。
> Agent 没有权限修改任何状态机。

---

## 1. 架构

```
P8P9/
├── models.py                  ← 常量 + 状态机转换表 + 风险等级映射
├── state_machine.py           ← ClosureService（持久化 + 乐观锁 + 原子写）
├── business_actions.py        ← 7 个业务动作 + 归档铁律（callback_router / web_server 入口）
├── cards.py                   ← 6 态 Card 2.0 build_job_card
├── links.py                   ← ClosureLinkService（短时附件 token）
├── agent_interface.py         ← agent 唯一接口（3 个白名单）
├── web_server.py              ← 可选 Flask（/api/closure/* + /dl/<token>）
├── services/
│   ├── card_render.py         ← 飞书卡片渲染 service（0.2s 节流）
│   ├── callback_router.py     ← 飞书 button callback 路由 service
│   └── audit_scheduler.py     ← P9 审核调度 service（mock 实现）
└── tests/
    ├── test_state_machine.py          ← 17 测试
    ├── test_business_actions.py       ← 17 测试
    ├── test_cards.py                  ← 11 测试
    ├── test_callback_router.py        ←  9 测试
    ├── test_agent_interface.py        ← 12 测试（边界护栏）
    ├── test_button_callback.py        ←  8 测试（6 态走通）
    └── test_send_card_real.py         ←  9 测试（真实飞书，需 P8P9_FEISHU_REAL_SEND=1）
```

### 数据流

```
飞书用户按按钮
        ↓
[POST /feishu/card/callback]
        ↓
services/callback_router.route_card_callback(payload)
        ↓
business_actions.<action>(job_id, **params)
        ↓
state_machine.ClosureService.set_job_status(...)
        ↓
services.card_render.update_job_card(...)  ← 0.2s 节流 + 飞书 CardKit sequence
        ↓
飞书群卡片更新
```

---

## 2. 6 态状态机

```
None ──init──→ open
              ↓ acknowledge_disposition
       acknowledged ←──── relinquish_job ─┐
              ↓                          │
       rectifying ←──┐                   │
              ↓      │                   │
       materials_in_audit                 │
              ↓ (P9 mock)                │
       ready_to_close (或 waiting_human_review)
              ↓ record_closure_review(approved)
            closed ──→ archived_to_lt=true (铁律)
```

**完整 LEGAL_JOB_TRANSITIONS** 见 [models.py](models.py)。

---

## 3. 接口契约

### 3.1 agent 接口（**白名单**，仅 3 个）

```python
from P8P9.agent_interface import (
    initialize_job_for_agent,  # 初始化 job（idempotent）
    bind_card_for_agent,       # 绑定飞书卡片（写 card_binding）
    get_state_for_agent,       # 读 state（脱敏 PII）
)

# agent 不能调：
# - set_job_status / set_event_status / patch_fields
# - acknowledge_disposition / submit_rectification_materials / relinquish_job
# - escalate_risk / downgrade_risk / record_closure_review
# - archive_job_to_long_term_memory
```

### 3.2 飞书 callback 路由表

| action | 路由目标 | 备注 |
|--------|---------|------|
| `acknowledge_disposition` | `business_actions.acknowledge_disposition` | open → acknowledged |
| `submit_rectification_materials` | `business_actions.submit_rectification_materials` | acknowledged/rectifying → materials_in_audit |
| `relinquish_job` | `business_actions.relinquish_job` | acknowledged/rectifying → open |
| `escalate_risk` | `business_actions.escalate_risk` | 改 event.risk_level（job_status 不变） |
| `downgrade_risk` | `business_actions.downgrade_risk` | 改 event.risk_level（job_status 不变） |
| `record_closure_review` | `business_actions.record_closure_review` | waiting_human_review/ready_to_close → closed/rectifying |
| `download_attachment` | `services.callback_router.create_attachment_download_link` | 返回短链 token |

### 3.3 业务动作（不暴露给 agent）

7 个函数 + 1 个归档铁律（`_archive_with_retry`）。见 [business_actions.py](business_actions.py)。

### 3.4 服务（不暴露给 agent）

3 个 service 函数：

```python
from P8P9.services.card_render import (
    update_job_card,                 # 刷新已绑卡片（节流 + sequence 自增）
    send_all_open_closure_cards,     # 首次发送（per-event 卡片）
    send_event_card_for_web,         # Web 端写状态后自动刷新（MEMORY.md 约束）
)
from P8P9.services.callback_router import route_card_callback
from P8P9.services.audit_scheduler import agent_audit_job
```

---

## 4. 业务规则速查

| 规则 | 位置 | 常量 |
|------|------|------|
| 防误触：comment ≥ 10 字 | [business_actions.py](business_actions.py) | `REVIEW_COMMENT_MIN=10` |
| 防误触：reason 含「驳回」自动翻面 | `record_closure_review` | — |
| 业务一致性：上传人 = 接取人 | `_ensure_accepted_by` | — |
| 退接次数 ≤ 2（§7.2.2） | `relinquish_job` | `MAX_RELINQUISH_COUNT=2` |
| 升级 reason ≥ 10 | `escalate_risk` | `ESCALATE_REASON_MIN=10` |
| 升级 new_level > current | `escalate_risk` | — |
| 升级单次最多 +2 级 | `escalate_risk` | `ESCALATE_MAX_DELTA=2` |
| 降级 reason ≥ 20 + ≥ 1 evidence | `downgrade_risk` | `DOWNGRADE_REASON_MIN=20` / `DOWNGRADE_EVIDENCE_MIN=1` |
| 退接 reason ≥ 10 | `relinquish_job` | `RELINQUISH_REASON_MIN=10` |
| 归档铁律：archived_to_lt 终态 = true | `_archive_with_retry` | — |

---

## 5. 测试方法

### 5.1 单元测试（无飞书）

```bash
# 跑全部单元测试（74 测试）
python -m pytest P8P9/tests/test_state_machine.py \
                 P8P9/tests/test_business_actions.py \
                 P8P9/tests/test_cards.py \
                 P8P9/tests/test_callback_router.py \
                 P8P9/tests/test_agent_interface.py \
                 P8P9/tests/test_button_callback.py -v
```

预期：74/74 PASSED。

### 5.2 真实飞书发送测试

```bash
# 设置环境变量开启真实发送
export P8P9_FEISHU_REAL_SEND=1
export P8P9_FEISHU_CHAT_ID=oc_d10e7b407369327a538c1204f7817499  # 默认值

# 跑真实发送测试
python -m pytest P8P9/tests/test_send_card_real.py -v
```

每个 job 用 `P8P9-REAL-` 前缀，**测完 conftest autouse 自动清理**（见 [conftest.py](tests/conftest.py) 的 `cleanup_p8p9_real_jobs` fixture）。

### 5.3 数据目录覆盖（重要）

测试时通过 `P8P9_BASE_DIR` 环境变量覆盖默认数据目录：

```python
@pytest.fixture
def closure_service(tmp_jobs_dir, monkeypatch):
    monkeypatch.setenv("P8P9_BASE_DIR", str(tmp_jobs_dir))
    return ClosureService()
```

---

## 6. 关键设计约束

| 约束 | 位置 | 验证 |
|------|------|------|
| agent 不能 import business_actions | [agent_interface.py](agent_interface.py) | `test_no_business_actions_import_in_agent_interface` |
| agent_interface 不能调 set_job_status / set_event_status / patch_fields | [agent_interface.py](agent_interface.py) | `test_no_set_job_status_in_agent_interface` |
| agent_interface 只能暴露 3 个接口 | `__all__` | `test_agent_interface_has_exactly_three_exports` |
| 卡片颜色：closed → grey | [cards.py](cards.py) | `test_title_color_grey_when_closed` |
| 危急（5 级）→ 🚨 + carmine | [cards.py](cards.py) | `test_critical_level_uses_carmine` |
| 退接按钮仅 accepted_by 本人 enable | [cards.py](cards.py) | `test_acknowledged_card_relinquish_only_for_accepted_user` |
| 原子写（tempfile + os.replace + fsync） | [state_machine.py](state_machine.py) | `test_atomic_write_*` |
| 乐观锁 | [state_machine.py](state_machine.py) | `test_version_conflict_*` |
| Card 2.0 button.value 是 JSON string | [cards.py](cards.py) | `test_value_must_be_json_string` |
| 卡片更新节流 0.2s | [services/card_render.py](services/card_render.py) | `_throttle()` |

---

## 7. 不在本项目范围

- ❌ LLM agent（P8 处置 / P9 审核均不在范围；agent_interface 只留接口）
- ❌ 角色权限映射（§10 #7）
- ❌ 个人卡片场景（personal_context）
- ❌ 长记忆查询接口
- ❌ 飞书 AI 卡片 3.0
- ❌ Web 详情页前端

---

## 8. 持久化布局

```
{P8P9_BASE_DIR}/
├── {job_id}/
│   └── closure_state.json      ← 作业状态
└── _long_term/
    └── {job_id}.json           ← 归档文件（§8 铁律）
```

默认 `P8P9_BASE_DIR=data/p8p9_jobs`；测试时通过 `P8P9_BASE_DIR` 环境变量覆盖。

---

## 9. 待开发：Web 详情页（前端整合时统一风格）

按 [`docs/风险处置卡片交互设计.md`](../docs/风险处置卡片交互设计.md) §2.4.1 + §1047 + §672，详情入口应是 `closure/entry/<link_id>` 短时 token 链接 + HTML 详情页。当前为临时 JSON API，**待前端整合时统一风格与开发**：

| 项 | 临时现状 | 设计文档期望 |
|---|---|---|
| 入口 URL | `/api/closure/jobs/<job_id>` | `/closure/entry/<link_id>` |
| 短时 token | ❌ 无 | ✅ 必须（复用 `ClosureLinkService` 机制） |
| 返回 | 裸 JSON | HTML 详情页 |
| 权限校验 | ❌ 无 | ✅ actor_open_id 一致性 |
| 操作入口（复核/审核/上传）| 仅群内按钮 | Web 表单 + 群内表单 双通道 |
| 多 event | ❌ | ✅ `?event_ids=` 参数勾选 |
| 生命周期 | 跟 job 永久可读 | 跟 job 走（closed → 失效） |

**当前代码注释已标记**（搜 `TODO(待开发·前端整合)`）：
- [`P8P9/web_server.py`](web_server.py) `GET /api/closure/jobs/<job_id>` 路由
- [`P8P9/links.py`](links.py) 文件头（缺 `create_closure_entry_link` / `consume_closure_entry_link`）
- [`P8P9/services/card_render.py`](services/card_render.py) `_entry_url()` 函数
- [`P8P9/cards.py`](cards.py) `link_button()` 函数