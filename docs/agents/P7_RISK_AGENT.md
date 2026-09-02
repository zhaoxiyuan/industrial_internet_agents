# P7: 风险研判与分级 Agent (p7_risk_agent)

## 概述

**风险研判与分级专家**，负责综合研判 A5 输出的候选风险事件并确定风险等级。

P7 基于 A6 实现：A6Agent 是核心研判引擎（LLM + 工具调用 + 活跃违规聚合），
本模块负责将其能力暴露为：
- **A6 Web 服务**（/a6 看板 + /api/a6/* 接口，由 P6 启动时挂载到共享端口 5002）
- **A5→A6 触发器**（in-process，供 P6 agent loop 调用）
- **LangChain 工具桩**（供 main_agent.py P7 阶段同步 .invoke() 调用）
- **CLI 入口**（供 `python -m agents.cli risk ...` 调用）

## 主要功能

1. **事件去重与聚合**：维护 event_id_map 与 active_violations 表，避免重复研判
2. **风险分级**：LLM + prompts.py 可编辑提示词（5 级：轻微/一般/较重/严重/危急）
3. **A6 数据工具**：
   - `query_active_violations` — 查询当前追踪中的违规
   - `query_surrounding_raw_events` — 查询指定 wall_time 前后窗口内的 A5 检测
4. **结果落盘**：保存到 `A6/logs/assessments/YYYYMMDD/a6_*.json`

## 入口函数 / 工具

| 名称 | 类型 | 说明 |
|------|------|------|
| `run_risk_agent(message)` | CLI 入口 | 根据关键字分发到 risk_analyze / risk_list |
| `risk_analyze(event_id)` | LangChain `@tool` | 同步工具：触发 A6Agent.process_event |
| `risk_list(task_id)` | LangChain `@tool` | 同步工具：读取 A6 研判结果 |
| `trigger_a6_assessment(event_id, event_data)` | async | P6 agent loop 调用的 in-process 触发器 |
| `register_a6_routes(app, a5_log_dir)` | 函数 | 将 A6 全部路由挂载到 FastAPI app |
| `get_a6_agent()` | 单例 | 获取 A6Agent 实例（延迟初始化） |

## A6 Web 服务（共享端口 5002）

P7 不单独启动端口，由 P6 启动时调用 `register_a6_routes(app, a5_log_dir=str(LOG_DIR))`
把以下路由挂到 P6 的 FastAPI app 上：

| 路由 | 方法 | 说明 |
|------|------|------|
| `/a6` | GET | A6 风险研判看板页面 |
| `/api/a6/assessments` | GET | 研判结果列表 |
| `/api/a6/assessments/{id}` | GET | 单条研判详情 |
| `/api/a6/status` | GET | A6 状态（事件 ID 映射 + 汇总） |
| `/api/a6/prompts` | GET | 全部提示词 |
| `/api/a6/prompts/{name}` | GET/POST | 读取/更新提示词 |
| `/api/a6/prompts/{name}/save` | POST | 提示词落盘到 prompts.py |
| `/api/a6/prompts/{name}/reset` | POST | 重置提示词为默认 |
| `/api/a6/process/{event_id}` | POST | HTTP 入口触发 A6 研判（P6 已改用 in-process） |
| `/api/a6/clear_logs` | POST | 清理 A6 日志 |

## A5→A6 调用关系

```
P6 _run_agent_loop
   ├─ A5 Agent.tick_batch()        (A5 监测)
   └─ trigger_a6_assessment(wt, r) ←── P7 (in-process，跳过 HTTP)
            ↓
       A6Agent.process_event()
            ↓
       save_assessment() → A6/logs/assessments/
```

> **历史变更**：早期版本 P6 通过 HTTP `POST /api/a6/process/{event_id}` 自调用（5002→5002），
> 现已改为 in-process 调用，减少一层网络、避免 ReadTimeout。

## 风险等级定义

| 数字 | 中文 | 颜色 | `level` 归一化 |
|------|------|------|----------------|
| 1 | 轻微 | 绿 | `LOW` |
| 2 | 一般 | 橙 | `LOW` |
| 3 | 较重 | 红橙 | `MEDIUM` |
| 4 | 严重 | 红 | `HIGH` |
| 5 | 危急 | 紫 | `CRITICAL` |

> P7 工具返回的 `level` 字段（LOW/MEDIUM/HIGH/CRITICAL）供 main_agent P7 阶段 high_risk 筛选使用。

## 文件位置

`agents/p7_risk_agent.py`

## 相关文档

- [P6_MONITOR_AGENT.md](P6_MONITOR_AGENT.md) — P6 通过共享端口 5002 提供 A5 监测与 A6 看板
- [A6 README](../../A6/README.md) — A6Agent 核心实现
