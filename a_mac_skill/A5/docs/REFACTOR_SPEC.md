# 重构说明文档 — main_loop 承担 mock 数据播放 + agent 调用

> **目标**:让 `main_loop.py` 成为唯一的核心循环入口，承担 mock 数据播放和 agent 调用双重职责；`frontend/app_a5.py` 简化为纯 Web 启动器，一键启动全部功能。

---

## 一、当前逻辑

### 1.1 当前各模块职责

| 模块 | 职责 |
|---|---|
| `main_loop.py` | 单线程同步循环：每秒调用 `collector.flush_second()` + `agent.tick()`，同步阻塞，等待 agent 完成才进下一秒 |
| `frontend/app_a5.py` | FastAPI + WebSocket：自己起 AsyncCollector + A5Agent，自己管异步循环，用 `AgentQueue` 实现 fire-and-forget 推送给前端 |
| `AsyncCollector` | 管理 3 个 mock 数据流（CV/传感器/定位），每秒 `flush_second()` 落盘 4 类文件（cv/sensor/position/snapshot） |
| `A5Agent` | 每秒收到 snapshot，判断违规；用 `EventDeduplicator` 做同一人同一事件的聚合/去重 |
| `AgentQueue` | 串行队列：主循环 fire-and-forget，agent 任务排队串行执行 |
| `EventDeduplicator` | 每 agent tick 调用一次，按 person_id 做状态机去重（NO_ACTIVE → NEW_EPISODE → ACTIVE → COOLDOWN） |

### 1.2 当前数据流（以 demo/app 为例）

```
frontend/app_a5.py WebSocket 连接
    │
    ├─ 每秒:
    │    collector.flush_second(sec)   → 落盘日志文件
    │    collector.get_snapshot()       → 取当前秒聚合数据
    │    agent_queue.submit()           → 丢任务到队列，立即返回（不阻塞）
    │    ws.send_json(tick)             → 推送实时数据给浏览器
    │
    └─ AgentQueue 后台:
         tick() → EventDeduplicator → raw_event 写盘
         callback → ws.send_json(agent_events)  → 推送 agent 结果
```

### 1.3 当前 main_loop.py 的问题

- 每秒同步调用 `agent.tick()`，agent 执行慢时会阻塞主循环
- agent 每秒调用一次，无法批量处理历史未处理数据
- 没有并行能力，所有 tick 串行

---

## 二、修改后逻辑

### 2.1 两个独立循环（均在 main_loop.py 内）

```
┌─────────────────────────────────────────────────────┐
│                   main_loop.py                       │
│                                                      │
│  循环 1: Mock数据播放器（固定每秒触发）               │
│  ─────────────────────────────────────────────────  │
│    for sec in 0..29:                                 │
│      await asyncio.sleep(1.0)                        │
│      collector.flush_second(sec)  → 落盘日志文件     │
│                                                      │
│  循环 2: Agent 检查循环（可配置间隔）                 │
│  ─────────────────────────────────────────────────  │
│    while True:                                       │
│      await asyncio.sleep(agent_interval_sec)         │
│      扫描 logs/ 目录，找出未处理的日志文件            │
│      每次喂给 agent N 条（参数）                      │
│      agent 并行调用（asyncio.gather）                │
│      agent.tick() → 违规判断 → 写 raw_event          │
│                                                      │
│  frontend/app_a5.py: 纯启动器，调用 main_loop 协程           │
└─────────────────────────────────────────────────────┘
```

### 2.2 循环 1：Mock 数据播放器

**触发条件**：固定每秒一次（`await asyncio.sleep(1.0)`）

**操作**：
```python
await collector.flush_second(sec)   # 落盘 cv/sensor/position/snapshot 4 类文件
```

**输出**：每秒在 `log_dir/` 下生成 4 个文件（永不覆盖）：
```
cv_{ts}_T00.json ~ T29.json
sensor_{ts}_T00.json ~ T29.json
position_{ts}_T00.json ~ T29.json
snapshot_{ts}_T00.json ~ T29.json
```

**关键约定**：落盘文件名中的 `Txx` 对应**场景秒**，便于 agent 扫描时按文件名判断"已处理"或"未处理"。

### 2.3 循环 2：Agent 检查循环

**触发条件**：每 `agent_interval_sec` 秒（参数可配置，默认 10 秒）

**扫描逻辑**：
```python
# 扫描 log_dir/ 下所有 snapshot_*.json
# 判断标准：文件名中 Txx 对应的秒是否已在 raw_events/ 下有对应记录
# 每次给 agent 喂 未处理的 N 条（参数，默认 10 条）
# 允许 agent 并行调用（asyncio.gather），互不阻塞
```

**喂给 agent 的数据**：
- ✅ CV 数据按**秒聚合**后的 `snapshot_*.json`（不是原始 25 帧）
- ✅ 包含每帧的 PPE 统计（helmet/goggles/suit 缺失率）
- ❌ 不喂原始 25 帧数据

**Agent 调用方式**：
```python
# 并行调用，互不阻塞
tasks = [agent.tick_from_snapshot(snapshot_file) for snapshot_file in unprocessed_files]
results = await asyncio.gather(*tasks)
```

**去重机制（取消 EventDeduplicator）**：
- 取消每秒同步调用 agent
- 取消 EventDeduplicator 状态机
- Agent 自主性体现在：基于作业票提示词规则，对日志进行告警判断
- 查重依赖：`query_past_events` 工具（查 raw_events/ 下已有事件）+ 时间窗口判断

### 2.4 文件标记机制（三级状态）

**核心原则：任务提交时就标记，不等 agent 返回结果。** 这样即使 agent 进程崩溃/重启，已提交的任务也不会被重复提交。

**目录结构**：
```
log_dir/
├── cv_{ts}_Txx.json           ← 原始日志（只写一次，无状态标记）
├── sensor_{ts}_Txx.json
├── position_{ts}_Txx.json
├── snapshot_{ts}_Txx.json     ← 聚合数据，agent 读取的入口
│
├── processing_Txx.json         ← ★ 任务已提交，agent 正在处理（中间状态）
│   内容: {"submitted_at": "2026-08-05T14:32:08", "retry": 0}
│
├── raw_event_Txx.json          ← ★ agent 处理完成，有结果（终态之一）
│   内容: {"wall_time": "...", "events": [...]}
│
└── error_Txx.json              ← ★ agent 处理失败（终态之一，可重试）
    内容: {"error": "LLM 调用失败: timeout", "failed_at": "...", "retry": 2}
```

**状态转换图**：
```
[无标记文件]
    │ 扫描发现 snapshot_Txx.json 无对应 raw_event/processing/error
    ▼
[创建 processing_Txx.json]
    │ 任务已提交，agent 开始处理
    ▼
  两条路径:
  ① 成功 → 删除 processing_，创建 raw_event_
  ② 失败 → 删除 processing_，创建 error_

[error_Txx.json 存在]
    │ retry < MAX_RETRIES
    ▼
[重新创建 processing_Txx.json，重试]
    │ retry >= MAX_RETRIES
    ▼
[放弃，仍保留 error_Txx.json]
```

**扫描与提交流程**：
```python
MAX_RETRIES = 3

def _get_status(log_dir: Path, sec: int) -> str:
    """返回: 'done' | 'processing' | 'error' | 'new'"""
    if (log_dir / f"raw_event_{sec:02d}.json").exists():
        return "done"
    if (log_dir / f"processing_{sec:02d}.json").exists():
        return "processing"
    err_file = log_dir / f"error_{sec:02d}.json"
    if err_file.exists():
        err = json.load(open(err_file))
        return "error" if err.get("retry", 0) >= MAX_RETRIES else "retryable"
    return "new"

def _submit_task(log_dir: Path, sec: int, wall_time: str):
    """提交任务：创建 processing 文件（乐观标记）"""
    json.dump({
        "submitted_at": wall_time,
        "retry": 0,
        "second": sec,
    }, open(log_dir / f"processing_{sec:02d}.json", "w"))

async def _on_success(log_dir: Path, sec: int, result: dict):
    """agent 成功返回：删除 processing，写 raw_event"""
    processing = log_dir / f"processing_{sec:02d}.json"
    if processing.exists():
        processing.unlink()
    json.dump(result, open(log_dir / f"raw_event_{sec:02d}.json", "w"), ensure_ascii=False, indent=2)

async def _on_error(log_dir: Path, sec: int, error: Exception, current_retry: int):
    """agent 失败：删除 processing，写 error（可重试）"""
    processing = log_dir / f"processing_{sec:02d}.json"
    if processing.exists():
        processing.unlink()
    json.dump({
        "error": str(error),
        "failed_at": datetime.now().isoformat(timespec="milliseconds"),
        "retry": current_retry + 1,
    }, open(log_dir / f"error_{sec:02d}.json", "w"), ensure_ascii=False, indent=2)
```

**扫描主循环**：
```python
async def agent_check_loop(log_dir: Path, interval_sec: int, batch_size: int):
    while True:
        await asyncio.sleep(interval_sec)

        # 1. 找出所有 Txx
        snapshot_files = sorted(log_dir.glob("snapshot_*.json"))
        unprocessed = []
        for sf in snapshot_files:
            # 从文件名提取秒数: snapshot_{ts}_T{xx}.json
            sec = int(sf.stem.split("_T")[1])
            status = _get_status(log_dir, sec)
            if status in ("new", "retryable"):
                unprocessed.append((sec, sf))

        # 2. 取最早未处理的 batch_size 条
        unprocessed = unprocessed[:batch_size]

        # 3. 提交任务（乐观标记）
        wall_time = datetime.now().isoformat(timespec="milliseconds")
        for sec, sf in unprocessed:
            current_retry = 0
            err_file = log_dir / f"error_{sec:02d}.json"
            if err_file.exists():
                current_retry = json.load(open(err_file)).get("retry", 0)
            _submit_task(log_dir, sec, wall_time)

        # 4. 并行执行
        tasks = []
        for sec, sf in unprocessed:
            err_file = log_dir / f"error_{sec:02d}.json"
            current_retry = json.load(open(err_file)).get("retry", 0) if err_file.exists() else 0
            tasks.append(_agent_process(log_dir, sec, sf, current_retry))

        await asyncio.gather(*tasks, return_exceptions=True)
```

**为什么选"任务提交就标记"而非"结果返回再标记"**：

| 方案 | 优点 | 缺点 |
|---|---|---|
| 提交时标记（processing） | 重启可恢复、不重复提交、进程崩溃安全 | 需清理 stale processing 文件（可加超时机制） |
| 结果返回再标记 | 简单，无中间状态 | agent 慢时同一秒会被重复提交给多个并行任务 |
| 不标记，靠任务队列去重 | 无文件污染 | 需要独立任务队列服务，重启丢状态 |

**异常处理与可恢复性**：
- `processing_` 文件存在但 agent 进程崩溃：下次扫描时检查文件存在但长期无结果，可判定为 stale，清理后重新提交
- `error_` 文件存在但 retry < MAX_RETRIES：下次扫描自动重试
- `error_` 文件存在且 retry >= MAX_RETRIES：跳过，留给人工处理
- 服务重启后：所有 `processing_` 和 `error_` 文件仍存在，可继续处理

### 2.4 frontend/app_a5.py 简化为一键启动

```python
# frontend/app_a5.py
@app.websocket("/ws/{scenario}")
async def ws_run(ws, scenario, interval=10):
    await ws.accept()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = f"logs/frontend/{scenario}_{ts}"

    # 启动 main_loop（数据播放 + agent 检查两个循环）
    asyncio.create_task(main_loop.run(scenario, log_dir, interval_sec=interval))

    # 循环推送日志文件变化（轮询 log_dir/）
    while True:
        updates = scan_log_dir(log_dir)
        await ws.send_json({"type": "log_update", "files": updates})
        await asyncio.sleep(1.0)
```

**简化后的 frontend/app_a5.py 职责**：
1. 提供 WebSocket 连接
2. 创建 `log_dir`
3. 启动 `main_loop.run()` 协程
4. 轮询 `log_dir/` 推送文件更新给前端
5. **不再管理 AsyncCollector / A5Agent / AgentQueue**

---

### 2.5 取消"防未来"机制（`set_agent_now` / `_agent_now`）

**当前代码存在的问题**：

原 `main_loop.py` 和 `frontend/app_a5.py` 中，collector 有一个 `_agent_now` 字段，通过 `set_agent_now()` 在每次 `agent.tick()` 前设置"agent 当前可观测的时间上限"，用于防止 agent 通过 `query_raw_logs` 工具查看未来数据。

```python
# 当前代码中的防未来逻辑
agent_now = collector._sec_to_wall(sec + 2)   # 比当前秒多 1 秒
self.collector.set_agent_now(agent_now)       # 设置上限
snapshot = collector.get_snapshot(wall_time)
events = await agent.tick(wall_time, snapshot, agent_now=agent_now)
```

问题：
- `_agent_now` 是内存状态，服务重启后丢失
- 与 `EventDeduplicator` 强耦合，agent 并行后状态机已取消
- 实现复杂：`_build_input` 里要手动扫描整个窗口、collector 要临时清除 `_agent_now` 等

**修改后用文件标记取代**：

循环 2 的 agent 只读取已落盘的 `snapshot_Txx.json` 文件。`snapshot_Txx` 在循环 1 的 `flush_second(sec)` 完成后才写入磁盘。

因此：
- **循环 1 写入 `snapshot_Txx.json`** → 文件出现即代表该秒数据已完整
- **循环 2 扫描文件时**，只会有 `T00` ~ `T(当前秒-1)` 的文件存在
- **agent 读到的文件永远不会是"未来"数据**

不需要任何代码层面的时间拦截逻辑。`query_raw_logs` 工具中的 `_agent_now` 检查、`get_snapshot` 中的 `respect_agent_now` 参数、`_build_input` 中的窗口扫描逻辑，全部可以删除。

```python
# 删除以下内容：
collector.set_agent_now(query_end)          # a5_agent.py tick()
self._agent_now = wall_time                  # async_collector.py set_agent_now()
saved_now = self._agent_now; self._agent_now = None  # get_snapshot 临时清除
snapshot["_query_start"] = start_wall        # AgentQueue._worker 注入窗口
```

---

## 三、关键设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 两个循环如何同步 | 循环 1 固定每秒，循环 2 按 `agent_interval_sec` 触发 | 数据播放是实时基准，agent 检查是异步的 |
| Agent 并行调用 | `asyncio.gather` | 允许多个 agent tick 同时运行，提高吞吐量 |
| 未处理数据判断 | 按 snapshot 文件名 Txx + 三级文件标记（processing/raw_event/error） | 任务提交即标记，重启可恢复，不重复提交 |
| 每次喂给 agent 的数据量 | `batch_size` 参数（默认 10 条） | 避免单次 prompt 过长 |
| 原始 25 帧数据 | 不喂给 agent，只喂秒级聚合的 snapshot | 减少 token 消耗，提高判断效率 |
| EventDeduplicator | 取消 | Agent 并行后不适合做状态机；改用 `query_past_events` 工具查重 |
| 防未来机制 | 取消 `set_agent_now` / `_agent_now`，改用文件标记 | 循环 1 先写文件，循环 2 只能读到已完成的数据，无需代码拦截 |
| frontend/app_a5.py | 纯 Web 启动器 + 文件轮询推送 | 与 main_loop 解耦，职责单一 |

---

## 四、文件改动清单

| 文件 | 改动 |
|---|---|
| `main_loop/main_loop.py` | 重写：新增 `run_with_agent(scenario, log_dir, agent_interval_sec, batch_size)`，包含两个 asyncio 循环 |
| `frontend/app_a5.py` | 大幅简化：移除 AsyncCollector/A5Agent/AgentQueue 相关代码，改为调用 main_loop 协程 + 轮询 log_dir/ |
| `agent/a5_agent.py` | 新增 `tick_from_snapshot(snapshot_file_path)` 方法：从 snapshot 文件加载数据，判断违规，写 raw_event |
| `agent/event_deduplicator.py` | 取消（不再需要） |
| `agent/tools.py` | 可能需要新增 `query_unprocessed_logs` 工具 |

---

## 五、启动方式（修改后）

```bash
# 一键启动 demo（含数据播放 + agent）
python A5/frontend/app_a5.py

# 或直接用 main_loop（无 Web 前端）
python A5/main_loop/main_loop.py --scenario B --agent-interval 10 --batch-size 10

# 指定端口
python A5/frontend/app_a5.py --port 8080
```

---

## 六、当前逻辑 vs 修改后逻辑 对照

| 维度 | 当前 | 修改后 |
|---|---|---|
| 数据播放循环 | 在 main_loop 或 demo/app 重复实现 | 统一在 main_loop.py，循环 1 |
| Agent 调用方式 | 每秒同步调用，或 AgentQueue 串行 | 按 `agent_interval_sec` 调用，支持并行 gather |
| 去重机制 | EventDeduplicator 状态机 | 取消，依赖 query_past_events 工具 |
| Agent 数据粒度 | 每秒 snapshot | 同左（秒级聚合），每次喂 batch_size 条 |
| demo/app 职责 | 管理 collector + agent + 异步队列 | 纯 Web 启动器 + 文件轮询 |
| 并行能力 | 无（串行 tick） | 有（asyncio.gather 并行多个 agent tick） |
