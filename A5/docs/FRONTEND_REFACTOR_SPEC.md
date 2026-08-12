# 前端重构说明文档 — 多Tab标签页 + Agent独立控制

> **目标**:将 demo 前端改为多Tab标签页，支持独立控制 Agent 轮询的启停，数据播放与 Agent 处理解耦。

---

## 一、当前前端状态

### 1.1 单页布局
```
┌─ A5 作业过程监测 Demo ──────────────────────────────┐
│ [场景B ▼] [Agent:每5秒 ▼] [开始模拟]    状态:运行中  │
│ ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ │
│ ┌─ PPE检测 ─┐ ┌─ 传感器 ─┐ ┌─ 位置 ─┐ ┌─ Agent ─┐  │
│ │ P7 头盔NO │ │ 气体normal│ │ P7 危险区│ │ 队列:3  │  │
│ └──────────┘ └─────────┘ └───────┘ └────────┘      │
│ ┌─ 报警事件 ──────────────────────────────────────┐ │
│ │ T=8s PPE缺失 张师傅                              │ │
│ └─────────────────────────────────────────────────┘ │
│ ┌─ 作业票规则 ────────────────────────────────────┐ │
│ │ [刷新] [保存] [重置]                             │ │
│ │ <textarea>...</textarea>                        │ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 1.2 当前问题
- 只有一个页面，无 Tab 切换
- "开始模拟"按钮同时启动数据播放 + Agent 轮询，两者耦合
- 无法单独关闭/重启 Agent 轮询
- 没有系统提示词编辑界面
- 没有 Agent 处理进度的可视化展示
- 作业票规则和系统提示词混杂在同一区域

---

## 二、目标布局

```
┌──────────────────────────────────────────────────────────┐
│  [Mock进度] [报警看板] [Agent进度] [作业票规则] [系统提示词]  │  ← Tab 栏
├──────────────────────────────────────────────────────────┤
│                                                          │
│                    Tab 内容区                            │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 2.1 Tab1: Mock进度 + 报警看板
```
┌─ Mock进度 + 报警看板 ───────────────────────────────────────────┐
│ [场景B ▼] [开始播放] [暂停] [停止]     状态: T=12s/30s          │
│ ████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│                                                                │
│ ┌─ 实时数据 ───────────────────┐ ┌─ 报警事件 ───────────────┐  │
│ │ P7 头盔NO  气体warn  P11监护 │ │ 🔴 T=8s PPE缺失-头盔    │  │
│ │ P8 正常     温度normal       │ │ 🟡 T=14s 传感器告警     │  │
│ └──────────────────────────────┘ └──────────────────────────┘  │
│                                                                │
│ ┌─ 日志文件状态 ───────────────────────────────────────────┐   │
│ │ snapshot: 12/30  raw_event: 3  processing: 1  error: 0   │   │
│ └──────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

**功能**：
- 独立控制数据播放（开始/暂停/停止）
- 实时显示 T=xx/30s 进度
- 实时显示 PPE/传感器/位置卡片（左侧）
- 实时显示报警事件列表（右侧，按 raw_event 文件）
- 显示日志文件统计（底部：snapshot/raw_event/processing/error 各多少个）

### 2.2 Tab2: Agent进度
```
┌─ Agent进度 ─────────────────────────────────────────────────┐
│ Agent轮询: [启动] [停止]    间隔: [5秒 ▼]   批次: [10条 ▼]    │
│                                                                │
│ ┌─ 处理队列 ─────────────────────────────────────────────┐   │
│ │ ✅ 2026-08-06T09-52-12_703  已完成  tick=27s          │   │
│ │ 🔄 2026-08-06T09-52-13_703  处理中                    │   │
│ │ ⏳ 2026-08-06T09-52-14_703  等待中                    │   │
│ │ ❌ 2026-08-06T09-52-15_703  失败(retry=1/3)          │   │
│ └────────────────────────────────────────────────────────┘   │
│                                                                │
│ ┌─ 已处理统计 ───────────────────────────────────────────┐   │
│ │ 总计: 30  成功: 28  失败: 2  重试中: 0                 │   │
│ └────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

**功能**：
- 独立启动/停止 Agent 轮询（不影响数据播放）
- 实时显示 processing/raw_event/error 文件列表
- 显示每个任务的：状态、wall_time（替代Txx）、tick_ms、retry次数
- 显示成功/失败统计

### 2.3 Tab3: 作业票规则编辑
```
┌─ 作业票规则 ─────────────────────────────────────────────────┐
│  [保存] [重置] [恢复默认]                                     │
│                                                                │
│ <textarea>
│ 1. 必戴PPE: 头盔、护目镜、防护服
│ 2. 动火区: 10米内禁止明火
│ 3. 监护人: 必须在场
│ ...
│ </textarea>                                                  │
└────────────────────────────────────────────────────────────────┘
```

**功能**：
- 编辑作业票规则文本
- 保存到后端（/api/rules）
- 与 Agent 判断逻辑联动

### 2.4 Tab4: 系统提示词编辑
```
┌─ 系统提示词 ─────────────────────────────────────────────────┐
│  [保存] [重置]                                               │
│                                                                │
│ <textarea>
│ 你是一个作业安全监测智能体...
│ </textarea>                                                  │
└────────────────────────────────────────────────────────────────┘
```

**功能**：
- 编辑 Agent 的 system prompt
- 保存到后端（新增 `/api/system_prompt` 接口）
- 实时影响 Agent 判断行为

---

## 三、后端改动

### 3.1 当前后端问题

`frontend/app_a5.py` 的 `/ws/{scenario}` 同时做了：
1. 启动 `main_loop.run()`（数据播放 + Agent 轮询都在里面）
2. 轮询 `log_dir/` 推送文件变化

两者耦合，无法单独控制 Agent 启停。

### 3.2 新的后端架构

```
frontend/app_a5.py（一个 FastAPI 服务，两个独立资源）
├── REST API:
│   ├── POST /api/scenario/start        ← 启动数据播放（循环1）
│   ├── POST /api/scenario/stop         ← 停止数据播放
│   ├── GET  /api/scenario/status       ← 查询播放状态
│   ├── POST /api/agent/start           ← 启动Agent轮询（循环2）
│   ├── POST /api/agent/stop            ← 停止Agent轮询
│   ├── GET  /api/agent/status          ← 查询Agent状态
│   ├── GET  /api/logs/{path:path}      ← 读取日志文件（供前端轮询）
│   ├── GET  /api/rules                 ← 作业票规则
│   ├── POST /api/rules                 ← 更新作业票规则
│   ├── GET  /api/system_prompt         ← 系统提示词（新增）
│   └── POST /api/system_prompt         ← 更新系统提示词（新增）
│
└── WebSocket:
    └── /ws/{scenario}                  ← 推送实时状态（由前端主动轮询REST替代，可选）
```

**关键设计决策**：

| 决策 | 选择 | 理由 |
|---|---|---|
| 数据播放控制 | 通过 REST API 启动/停止 `main_loop` 中的循环1 | 前端可独立控制播放 |
| Agent控制 | 通过 REST API 启动/停止循环2 | 核心需求：独立启停Agent |
| 前端数据获取 | 轮询 REST `/api/logs/*` | 简单，避免WebSocket复杂性 |
| 两个循环是否同进程 | 可以同进程，通过共享 `log_dir` 通信 | 简单，无须引入消息队列 |
| 日志文件读取 | 前端每2秒轮询一次 REST 接口 | 配合文件标记机制，实时性足够 |

### 3.3 REST 接口设计

```python
# ── 数据播放控制 ──────────────────────────────────────
@app.post("/api/scenario/start")
async def start_scenario(body: dict):
    """
    启动数据播放（循环1）。
    body: {"scenario": "B", "log_dir": "logs/xxx"}
    返回: {"status": "started", "log_dir": "...", "duration_sec": 30}
    """
    # 在后台启动 _data_play_loop 协程
    # 将协程保存到 app.state
    pass

@app.post("/api/scenario/stop")
async def stop_scenario():
    """停止数据播放"""
    pass

@app.get("/api/scenario/status")
async def get_scenario_status():
    """
    返回: {
        "running": True/False,
        "current_second": 12,
        "total_seconds": 30,
        "log_dir": "logs/xxx"
    }
    """
    pass

# ── Agent控制 ──────────────────────────────────────────
@app.post("/api/agent/start")
async def start_agent(body: dict):
    """
    启动Agent轮询（循环2）。
    body: {"log_dir": "logs/xxx", "interval_sec": 5, "batch_size": 10}
    """
    pass

@app.post("/api/agent/stop")
async def stop_agent():
    """停止Agent轮询"""
    pass

@app.get("/api/agent/status")
async def get_agent_status():
    """
    返回: {
        "running": True/False,
        "interval_sec": 5,
        "batch_size": 10,
        "pending": _get_all_pending(log_dir),
        "stats": {"total": 30, "done": 28, "error": 2}
    }
    """
    pass

# ── 日志文件读取 ───────────────────────────────────────
@ app.get("/api/logs/{path:path}")
async def get_log_file(path: str):
    """
    读取 log_dir 下的任意文件。
    前端每2秒轮询一次，获取最新的 snapshot/raw_event/error 状态。
    """
    pass

# ── 系统提示词（新增）──────────────────────────────────
@app.get("/api/system_prompt")
async def get_system_prompt():
    return {"system_prompt": agent.system_prompt}

@app.post("/api/system_prompt")
async def update_system_prompt(body: dict):
    agent.set_system_prompt(body["system_prompt"])
    return {"status": "updated"}
```

---

## 四、前端改动

### 4.1 Tab 路由
使用 CSS `display` 切换，无页面刷新：

```javascript
function showTab(name) {
    document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
    document.getElementById('tab-' + name).style.display = 'block';
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('btn-' + name).classList.add('active');
}
```

### 4.2 轮询策略

| Tab | 轮询频率 | 数据源 |
|---|---|---|
| Mock进度 | 每1秒 | `/api/scenario/status` + `/api/logs/snapshots/*` |
| 报警看板 | 每2秒 | `/api/logs/raw_events/*` |
| Agent进度 | 每2秒 | `/api/agent/status` |
| 作业票规则 | 手动刷新 | `/api/rules` |
| 系统提示词 | 手动刷新 | `/api/system_prompt` |

### 4.3 文件结构
```
demo/
├── app.py              ← FastAPI 后端（重构）
├── templates/
│   └── index.html      ← 单文件前端（重写，多Tab）
└── static/
    └── (空，CSS/JS 都内联在 index.html)
```

---

## 五、启动方式

```bash
# 单一服务（同时启动数据播放和Agent轮询）
python A5/frontend/app_a5.py

# 或分开启动（可选，未来扩展）
python A5/frontend/app_a5.py --mode standalone
```

---

## 六、多并发场景支持问题（架构修复）

### 5.1 问题描述

当前 `state.log_dir` 是**单例**，每次调用 `/api/scenario/start` 会覆盖旧值：

```
用户: POST /api/scenario/start (B)  → log_dir = logs/frontend/B_xxx/
用户: POST /api/scenario/start (C)  → log_dir 被覆盖为 logs/frontend/C_yyy/
旧 agent 协程: 仍在读 logs/frontend/B_xxx/  ← 旧路径已丢失
```

### 5.2 修复方案

**方案：停止旧协程，切换到新目录**

每次 `POST /api/scenario/start` 时：
1. 停止旧的 scenario 协程 + agent 协程
2. 重置 `state.log_dir` 为新路径
3. 启动新的协程

```python
@app.post("/api/scenario/start")
async def start_scenario(body: dict = None):
    # 1. 先停止旧的（如果还在跑）
    if state.scenario_running:
        await stop_scenario_internal()  # 内部同步版本，不返回 HTTP
    if state.agent_running:
        await stop_agent_internal()

    # 2. 设置新目录
    scenario = (body or {}).get("scenario", "B")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = f"logs/frontend/{scenario}_{ts}"
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    state.scenario = scenario
    state.log_dir = log_dir

    # 3. 启动
    state.scenario_running = True
    state.scenario_task = asyncio.create_task(_run_data_play(scenario, log_dir))
    return {"status": "started", "log_dir": log_dir}
```

### 5.3 限制说明

- 当前设计为**单并发**：同一时刻只能有一个场景在跑
- 切换场景时旧 agent 协程会被自动停止
- 未来可扩展为多 `log_dir` 并存（需改用 `dict[run_id, GlobalState]`）

### 5.4 Agent 如何定位到正确的 log_dir

Agent 的 `_run_agent_loop` 在**启动时**通过参数传入 `log_dir`：

```python
state.agent_task = asyncio.create_task(_run_agent_loop(
    state.log_dir,   # ← 启动时捕获当时的值
    interval, batch_size,
))
```

协程内部不再引用 `state.log_dir`，所以协程运行期间不受外部覆盖影响。但协程启动后如果外部 `state.log_dir` 被新场景覆盖，协程仍继续读旧目录——**这就是为什么切换场景前必须先停旧协程**。

---

## 六、Tab 总览

| # | Tab名称 | 轮询频率 | 数据源 |
|---|---|---|---|
| 1 | Mock进度 + 报警看板 | 每1秒 | `/api/scenario/status` + `/api/agent/status` |
| 2 | Agent进度 | 每2秒 | `/api/agent/status` |
| 3 | 作业票规则 | 手动刷新 | `/api/rules` |
| 4 | 系统提示词 | 手动刷新 | `/api/system_prompt` |

## 七、实施顺序

1. **Phase 1: 后端基础** — 重构 `frontend/app_a5.py`，实现 `/api/scenario/*` 和 `/api/agent/*` 接口
2. **Phase 2: Tab1 (Mock进度+报警看板)** — 实现合并后的实时数据+事件展示
3. **Phase 3: Tab2 (Agent进度)** — 实现处理队列可视化
4. **Phase 4: Tab3+Tab4 (规则编辑)** — 作业票规则 + 系统提示词

预计工作量：3-4 天

---

## 七、与 REFACTOR_SPEC.md 的关系

- `REFACTOR_SPEC.md` 定义的 **双循环架构**（循环1数据播放、循环2 Agent轮询）是后端基础
- 本文档基于双循环架构，进一步实现 **前端独立控制两个循环 + 多Tab可视化**
- `main_loop.py` 的双循环逻辑保持不变，只是被 `frontend/app_a5.py` 通过 REST API 调用
