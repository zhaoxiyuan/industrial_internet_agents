# 测试流程文档

> 本文档配合 `REFACTOR_SPEC.md` 使用。重构完成后需逐项核查，确保所有行为符合预期。

---

## 一、测试环境准备

### 1.1 启动基础服务

```bash
# 终端 1：agent_config前端（提供 LLM 配置）
cd A5
python agent_config/app.py

# 终端 2：确保依赖可用
pip install -r requirements.txt  # 或检查 pyproject.toml
```

### 1.2 测试目录清理

```bash
# 每次测试前清理旧日志
rm -rf A5/logs/test_*
rm -rf A5/logs/frontend/
rm -rf A5/logs/batch_*/
```

---

## 二、功能测试（按操作顺序）

### 测试 1：frontend/app_a5.py 一键启动

**操作**：
```bash
# 终端 3：启动 demo
cd A5
python frontend/app_a5.py --port 5001
```

**预期结果**：
- 服务启动无报错
- 浏览器打开 `http://127.0.0.1:5001` 能看到前端页面
- WebSocket 连接 `/ws/B` 可建立

**核查点**：
- [ ] `frontend/app_a5.py` 中没有 `AsyncCollector`、`A5Agent`、`AgentQueue` 的直接管理代码
- [ ] `frontend/app_a5.py` 只调用 `main_loop` 协程，不自己创建 collector/agent

---

### 测试 2：数据播放循环（循环 1）独立验证

**操作**：
```bash
cd A5
python main_loop/main_loop.py --scenario B --no-agent --log-dir logs/test_loop1
```

**预期结果**：
- 30 秒内每秒落盘 4 类文件：
  ```
  logs/test_loop1/
  ├── cv_{ts}_T00.json ~ T29.json
  ├── sensor_{ts}_T00.json ~ T29.json
  ├── position_{ts}_T00.json ~ T29.json
  └── snapshot_{ts}_T00.json ~ T29.json
  ```
- 每类各 30 个文件，共 120 个文件
- 每个 `snapshot_*.json` 包含 `cv_summary`（每人 PPE 聚合）、`sensor_summary`、`position_summary`

**核查点**：
- [ ] T00 ~ T29 共 30 个 snapshot 文件全部存在
- [ ] `snapshot_T08.json` 中 `cv_summary` 有 P7 头盔缺失率 ≥ 0.8（场景 B 的 T=8s 违规）
- [ ] 落盘文件名格式正确：`snapshot_{ts}_T{xx}.json`

---

### 测试 3：文件标记机制（processing/raw_event/error）

**操作**：在测试 2 的 log_dir 上验证

**预期结果**：
- 无 `processing_*.json`、`raw_event_*.json`、`error_*.json`（因为 `--no-agent`）
- 只有 `cv_`、`sensor_`、`position_`、`snapshot_` 四类文件

**核查点**：
- [ ] 目录中不存在 `processing_`、`raw_event_`、`error_` 文件

---

### 测试 4：Agent 检查循环（循环 2）基础调用

**操作**：
```bash
cd A5
python main_loop/main_loop.py --scenario B --log-dir logs/test_loop2 --agent-interval 5
```

**预期结果**：
- 30 秒内数据正常落盘（循环 1 正常）
- 每 5 秒扫描一次未处理文件，提交给 agent
- 有 `processing_Txx.json` 文件出现（任务提交标记）
- 有 `raw_event_Txx.json` 或 `error_Txx.json` 文件（最终状态）

**核查点**：
- [ ] `processing_T*.json` 在 agent 运行时存在，结束后消失或变成 `raw_event_`/`error_`
- [ ] 有 `raw_event_T08.json`（场景 B 的 PPE 违规秒）
- [ ] `raw_event_T08.json` 内容包含 `events` 数组，有违规人员信息

---

### 测试 5：并行调用验证

**操作**：
```bash
# batch_size=5, interval=5，第一次提交应有 5 个 processing_ 文件同时存在
cd A5
python main_loop/main_loop.py --scenario B --log-dir logs/test_parallel --agent-interval 5 --batch-size 5
```

**预期结果**：
- 某一时刻有 5 个 `processing_Txx.json` 并存
- agent 并行处理，互不阻塞

**核查点**：
- [ ] 某一时刻存在 2 个以上 `processing_*.json` 文件（并行证据）

---

### 测试 6：error 文件与重试机制

**操作**：模拟 agent 不可用（不启动agent_config前端）

```bash
# 不启动 agent_config/app.py，直接跑 main_loop
cd A5
python main_loop/main_loop.py --scenario B --log-dir logs/test_error --agent-interval 3
```

**预期结果**：
- 有 `error_Txx.json` 文件出现
- `error_Txx.json` 包含 `"retry": N` 字段（N < 3）
- `processing_Txx.json` 会在 error 写完后删除

**核查点**：
- [ ] `error_Txx.json` 存在且 retry 字段 < 3
- [ ] `processing_Txx.json` 不存在（已清理）
- [ ] 如果等待超过 1 分钟再次运行同一 log_dir（不改），error 文件会触发重试

---

### 测试 7：取消防未来机制验证

**操作**：
```bash
# 启动完整服务
cd A5
python agent_config/app.py  # 终端 1
python frontend/app_a5.py        # 终端 2
```

浏览器操作：
1. 连接 `/ws/B`
2. 观察 WebSocket 推送

**预期结果**：
- `query_raw_logs` 工具不再有 `_agent_now` 拦截
- `collector.set_agent_now()` 调用已删除
- agent 读取 `snapshot_Txx.json` 只能看到 ≤ 当前秒的数据

**核查点**：
- [ ] 代码中不存在 `set_agent_now` 调用
- [ ] 代码中不存在 `_agent_now` 属性
- [ ] `get_snapshot` 不再有 `respect_agent_now` 参数处理

---

### 测试 8：取消 EventDeduplicator 验证

**预期结果**：
- `EventDeduplicator` 类未被 import 或使用
- agent 判断违规直接写 `raw_event_`，不做状态机去重

**核查点**：
- [ ] `agent/a5_agent.py` 中没有 `EventDeduplicator` import
- [ ] `agent/event_deduplicator.py` 文件保留但未被调用（可保留用于参考）

---

### 测试 9：frontend/app_a5.py 简化验证

**操作**：连接 `http://127.0.0.1:5001` 并运行场景

**预期结果**：
- WebSocket 推送的数据包含 `log_dir` 信息
- `processing_` / `raw_event_` 文件状态正常
- 前端能收到 agent 结果（如果有）

**核查点**：
- [ ] `frontend/app_a5.py` 不到 200 行（简化后）
- [ ] `frontend/app_a5.py` 中没有 `AsyncCollector` 初始化
- [ ] `frontend/app_a5.py` 中没有 `A5Agent` 初始化
- [ ] `frontend/app_a5.py` 中没有 `AgentQueue` 使用

---

### 测试 10：多场景批量运行

**操作**：
```bash
cd A5
python main_loop/main_loop.py --scenario BCD --log-dir logs/test_batch --agent-interval 10
```

**预期结果**：
- 3 个场景各 30 秒，共 90 秒
- 每个场景有独立的 log_dir 子目录
- 每个子目录内文件标记正常

**核查点**：
- [ ] `logs/test_batch/run_01_B/`、`run_02_C/`、`run_03_D/` 三个目录
- [ ] 每个目录内 120 个原始文件 + 对应标记文件

---

## 三、测试顺序

```
测试 1 → 测试 2 → 测试 3 → 测试 4 → 测试 5 → 测试 6 → 测试 7 → 测试 8 → 测试 9 → 测试 10
  ↑         ↑         ↑         ↑         ↑         ↑         ↑         ↑         ↑
启动     验证循环1   验证标记   验证循环2   验证并行   验证error  验证防未来  验证去重    验证简化
服务     基础功能    基础结构   基础功能   调用       重试机制   已取消     已取消     app.py
```

> 测试 6（error 重试）需在前序测试确认 error 文件能正常生成后，手动将 retry 字段改小再重跑验证重试逻辑。

---

## 四、测试通过标准

- [ ] 所有 10 项测试的核查点全部通过
- [ ] 测试 4 中 `raw_event_T08.json` 包含场景 B 的头盔违规事件
- [ ] 测试 7 确认 `_agent_now` 机制已从代码中完全移除
- [ ] 测试 9 确认 `frontend/app_a5.py` 已简化为纯启动器
