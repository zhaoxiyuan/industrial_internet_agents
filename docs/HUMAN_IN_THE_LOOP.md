# Human-in-the-loop 说明

Human-in-the-loop（HITL）负责在关键阶段暂停自动流程，把上下文交给人工，并根据批准或否决结果继续或终止。当前系统有两套配合使用的机制：主工作流以磁盘状态为准，阶段 Agent 可以使用 LangChain 中间件在工具调用前中断。

## 1. 主工作流流程

```text
run_workflow(application, thread_id)
  → 按 P1…P10 调用 execute_stage_with_retry
  → 阶段返回 pending_confirmation
  → 保存阶段结果和 workflow 状态为 waiting
  → Web 查询 /api/workflow/state 并展示确认内容
  → POST /api/workflow/confirm
      ├─ approve：清除等待标记，从下一阶段继续
      └─ reject：当前阶段记为 failed，流程停止，可稍后 resume
```

P1 是兼容工具级 checkpoint 的特殊阶段：批准后可能先用 `execute_p1(..., resume=True)` 恢复被中断的工具调用，再进入 P2。其他阶段通常已完成业务计算，只等待阶段边界确认。

## 2. 标准数据结构

### 2.1 阶段返回的确认请求

各阶段通过统一返回对象把确认点交给主工作流：

```json
{
  "job_id": "20260914093000123",
  "stage": "P5",
  "completed": true,
  "pending_confirmation": {
    "stage": "P5",
    "action": "approve",
    "message": "请确认是否允许开工",
    "data": {}
  }
}
```

稳定约束：

- `pending_confirmation` 存在即表示工作流必须暂停，不能继续执行下一阶段。
- `stage` 使用大写 `P1`–`P10`；`job_id` 必须沿用入口生成的全流程标识。
- `data` 只放 UI 决策所需的业务快照，不放 secret、token 或不可序列化对象。

### 2.2 内存确认对象

`agents/hitl/human_in_the_loop.py` 提供可复用的数据类：

```python
HumanConfirmRequest(
    stage: str,
    action: str,
    task_id: str,
    title: str,
    description: str,
    options: list[dict[str, str]],
    data: dict,
    requested_at: str,
)

HumanConfirmResult(
    stage: str,
    action: str,
    task_id: str,
    confirmed: bool,
    selected_option: str,
    notes: str | None,
    confirmed_by: str,
    confirmed_at: str,
)
```

管理器以 `f"{stage}_{task_id}"` 为键保存待确认和已完成结果。它是进程内单例，不提供跨进程持久化；Web 主流程恢复应依赖 `data/jobs/{job_id}/` 下的状态文件，而不能只依赖该内存对象。

## 3. Python 标准接口

### 3.1 工作流级接口（推荐）

```python
from agents.main_agent import (
    run_workflow,
    confirm_and_continue,
    get_workflow_state,
    list_pending_confirmations,
)

result = run_workflow(application, thread_id=job_id)
pending = list_pending_confirmations(job_id)
result = confirm_and_continue(job_id, "P5", "approve", notes="现场确认通过")
state = get_workflow_state(job_id)
```

`confirm_and_continue` 的 `decision` 只接受 `approve` 或 `reject`。`async_execute=True` 时接口立即返回 `status=executing`，调用方应继续查询状态或监听 WebSocket。

### 3.2 通用内存管理器接口

```python
from agents.hitl.human_in_the_loop import (
    create_confirm_request,
    get_hitl_manager,
    submit_confirm_result,
)

request = create_confirm_request("P8", "disposition", task_id, data={"risk_id": "R-1"})
result = submit_confirm_result("P8", "disposition", task_id, "approve")
manager = get_hitl_manager()
```

稳定公开方法包括 `create_request`、`get_request`、`submit_result`、`get_result`、`has_pending`、`has_confirmed`、`list_pending` 和 `clear_confirmation`。

## 4. HTTP 标准接口

### 启动工作流

```http
POST /api/workflow/start
Content-Type: application/json

{ "work_type": "动火作业", "area": "罐区" }
```

响应立即返回：

```json
{"status":"starting","job_id":"20260914093000123","message":"工作流启动中..."}
```

### 查询等待状态

```http
GET /api/workflow/state?thread_id=20260914093000123
```

```json
{
  "status": "waiting",
  "pending": ["P5"],
  "pending_data": {"P5": {"stage": "P5"}},
  "confirmed": ["P1", "P2", "P3", "P4"],
  "current_stage": "P5",
  "thread_id": "20260914093000123"
}
```

### 提交决策

```http
POST /api/workflow/confirm
Content-Type: application/json

{
  "thread_id": "20260914093000123",
  "stage": "P5",
  "decision": "approve",
  "notes": "现场确认通过",
  "async_execute": true
}
```

### 从失败阶段恢复

```http
POST /api/workflow/resume
Content-Type: application/json

{"job_id":"20260914093000123","stage":"P5","force":false}
```

省略 `stage` 时从记录的失败阶段恢复；超过重试限制时只有显式 `force=true` 才会重试。

## 5. 阶段控制点

| 阶段 | 人工决定 | 典型选项 |
|---|---|---|
| P1 | 最终审批作业票 | 批准 / 驳回 |
| P2 | 是否纳入智能监测 | 纳入 / 跳过 |
| P3 | 是否接受或补充缺失信息 | 继续 / 补充 |
| P4 | 是否确认资源绑定 | 确认 / 修改 |
| P5 | 是否允许开工 | 批准 / 整改 |
| P6 | 默认无固定阶段确认点 | 由监测事件驱动 |
| P7 | 高风险研判确认 | 下发 / 升级 / 误报 |
| P8 | 处置、暂停和恢复 | 下发 / 暂停 / 恢复 |
| P9 | 是否关闭事件和作业 | 关闭 / 拒绝 |
| P10 | 是否确认归档报告 | 确认 / 修订 |

阶段文档记录实际工具级确认点；本表描述主流程的业务控制语义。

## 6. 持久化与幂等

```text
data/jobs/{job_id}/
  application.json
  workflow_status.json
  logs.json
  confirmations.json
  execution_status.json
  p1_result.json … p10_result.json
```

- 对同一 `job_id + stage` 重复提交确认前，应先查询当前 pending；已离开 waiting 的阶段不应再次触发业务副作用。
- 否决会落盘并把阶段标记为失败，不会自动执行后续阶段。
- 恢复前读取原申请和阶段结果；不要根据前端缓存重建状态。
- P8 飞书按钮还有独立的事件/动作幂等规则，详见 [飞书接口](FEISHU_API.md)。

## 7. 已知边界

- 内存 `HumanInTheLoop` 管理器与主工作流落盘确认是两套状态，不能互相替代。
- `STAGE_CONFIG` 中有比 Web API 更丰富的业务选项，但当前 `/api/workflow/confirm` 只接受 `approve`、`reject`；其他动作由阶段专属接口或飞书回调处理。
- Web 客户端必须把 `thread_id` 原样回传，不能用阶段内部 `task_id` 替代。
