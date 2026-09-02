# AGENTS_GRAPH — P1-P10 StateGraph 编排与可视化

本模块基于 LangGraph `StateGraph` 将 P1-P10 各 Agent 串接为一张统一的工作流图，
并提供静态 / 动态可视化能力。

- 代码位置：`agents/agents_graph/`
- 状态存储：`agents/agents_graph/state.py` (TypedDict `WorkflowState`)
- 节点工厂：`agents/agents_graph/nodes.py`
- 图构建：`agents/agents_graph/graph.py`
- 可视化：`agents/agents_graph/render.py`
- 静态 HTML：`docs/agents/AGENTS_GRAPH.html`
- 动态 HTML：`web/static/agents_graph_dynamic.html`

---

## 1. 设计

### 1.1 节点

每个阶段（P1-P10）单独成节点，对应一个 `node_Px(state)` 闭包，内部统一：

1. 把 `stages[Px].status` 置为 `running`，记 `started_at`
2. 调用对应的 `pX_agent.execute_stage(state["job_id"])`
3. 根据返回：
   - `result["pending_confirmation"]` 存在 → `stages[Px].status = waiting`，整图状态置为 `waiting`
   - `result["error"]` 存在 → `stages[Px].status = failed`，整图状态置为 `failed`
   - 其他 → `stages[Px].status = completed`
4. 触发 `_broadcast_callback(job_id, snapshot)`，可选（供 WebSocket 等推送）
5. 把更新后的 state 切片返回（LangGraph 自动合并）

> 注：P8 不直接暴露 `execute_stage`；通过 `main_agent.p8_execute_stage`（复用既有 `execute_p8`
> 逻辑）接入。

### 1.2 边

- **固定边**：`__start__ -> P1`
- **条件边**：每个阶段后通过 `add_conditional_edges(Px, router, {next: Px+1, END, wait_human})`
  按状态分流：
  - `stages[Px].status == "completed"` → 下一阶段（无则 `END`）
  - `stages[Px].status == "waiting"` → 路由到伪节点 `wait_human`
  - `stages[Px].status == "failed"` → 路由到 `END`
- **终态**：`wait_human -> END`，`P10 -> END`

`wait_human` 是一个静态伪节点，进入后只标记 `workflow_status="waiting"`，
工作流整体暂停，等待上层 `confirm_and_continue` 调用恢复。

### 1.3 状态

```python
class WorkflowState(TypedDict, total=False):
    job_id: str
    application: dict
    workflow_status: str       # running | waiting | completed | failed
    current_stage: str
    interrupt_reason: Optional[str]
    last_stage_result: Optional[dict]
    stages: Dict[str, StageInfo]
```

每个 `StageInfo` 含 `status` / `started_at` / `completed_at` / `error` /
`pending_confirmation` / 精简 `result`。

---

## 2. 用法

### 2.1 命令行

```bash
# 1) 打印 ASCII
python -m agents.agents_graph --ascii

# 2) 打印 / 写 Mermaid 源
python -m agents.agents_graph --mermaid
python -m agents.agents_graph --mermaid-md docs/agents/AGENTS_GRAPH.mmd

# 3) 生成静态 HTML（自包含 + Mermaid CDN）
python -m agents.agents_graph --static-html docs/agents/AGENTS_GRAPH.html

# 4) 生成动态 HTML（轮询 /api/graph/state 高亮节点）
python -m agents.agents_graph --dynamic-html web/static/agents_graph_dynamic.html \
    --dynamic-state-url /api/graph/state
```

### 2.2 程序化调用

```python
from agents.agents_graph import (
    build_workflow_graph,
    run_workflow_graph,
    render_mermaid,
    render_static_html,
    set_broadcast_callback,
)

# 仅编译
graph = build_workflow_graph()
mermaid_text = render_mermaid(graph)
render_static_html("docs/agents/AGENTS_GRAPH.html", graph)

# 跑一次真实工作流（每节点触发 broadcast callback，并写 sidecar JSON）
def my_broadcast(job_id, snapshot):
    print(job_id, snapshot["workflow_status"], snapshot["current_stage"])

set_broadcast_callback(my_broadcast)
final = run_workflow_graph("JOB-20260902-001", application={...})
print(final["workflow_status"], final["stages"]["P5"]["status"])
```

---

## 3. 可视化

### 3.1 静态视图 (`docs/agents/AGENTS_GRAPH.html`)

- 自包含 HTML，引用 Mermaid CDN 在线渲染；
- 显示完整 P1-P10 + 条件分流；
- 图例标注 running / completed / waiting / failed / pending。

### 3.2 动态视图 (`web/static/agents_graph_dynamic.html`)

- 每 1.5s 轮询 `state_url`（默认 `/api/graph/state`）获取最新 JSON 快照；
- 节点颜色随 `stages[*].status` 动态变化；
- 顶部 pill 标签实时显示 `workflow_status` 与 `current_stage`。

> 配套约定：`graph.run_workflow_graph` 会把快照写到
> `data/graph_states/{job_id}.json`，可作为该端点的简单实现。

---

## 4. 验证

合成测试覆盖三种分支：

| 分支 | workflow_status | current_stage | 说明 |
| --- | --- | --- | --- |
| 全部成功 | `completed` | `P10` | P1→P10 顺序完成 |
| 中途 HITL 等待 | `waiting` | 当前阶段 | 停在 `pending_confirmation` 阶段 |
| 失败 | `failed` | 出错阶段 | 出错阶段置 `failed`，后续阶段 `pending` |

复现：

```bash
python -c "
from agents.agents_graph import build_workflow_graph
from agents.agents_graph.state import make_initial_state
from agents.agents_graph import nodes as _nodes

# 用 stub executor 测试
orig = _nodes._make_stage_node
def stub(stage, executor):
    def _e(jid): return {'completed': True, 'task_id': jid}
    return orig(stage, _e)
_nodes._make_stage_node = stub
print(build_workflow_graph().invoke(make_initial_state('DEMO')))
_nodes._make_stage_node = orig
"
```

---

## 5. 与现有 main_agent 的关系

`main_agent.run_workflow` / `confirm_and_continue` 仍是当前服务 (`web/server.py`)
的主工作流入口，本模块提供：

1. 一种**图结构化**视角（节点 / 边 / 条件可视化）；
2. 一种**统一调度**入口（StateGraph）供未来由 LangGraph Studio / 时间回溯
   调试使用。

二者并存，互不修改现有调用链。如果需要把 `run_workflow` 改为调用
`run_workflow_graph`，可以在 `main_agent.py` 增补一个适配器：
保留各阶段的 `pending_confirmation` 检测与人工恢复逻辑。
