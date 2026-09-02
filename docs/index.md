# Web 启动执行链路

## 整体流程

```
用户点击启动
    ↓
前端立即返回（异步执行）
    ↓
前端每2秒轮询状态
    ↓
工作流执行中...
    ↓
遇到确认点 → 弹窗 → 用户确认 → 继续轮询
    ↓
工作流完成 → 停止轮询
```

## 详细链路

### 1. 前端启动 (`web/index.html`)

```html
<button class="btn btn-primary" onclick="startWorkflow()">🚀 启动</button>
```

### 2. 前端启动函数 (`web/app.js`)

```javascript
function startWorkflow() {
    const app = buildApplicationJson();  // 收集表单数据

    fetch('/api/workflow/start', {      // 调用后端接口
        method: 'POST',
        body: JSON.stringify(app)
    }).then(data => {
        startPollingWorkflow(data.job_id);  // 开始轮询状态
    });
}
```

### 3. 后端接口 (`web/server.py`)

```python
elif path == "/api/workflow/start":
    # 生成作业单编号
    job_id = datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999):03d}"

    # 立即返回，不等待工作流完成
    self.send_json({
        "status": "starting",
        "job_id": job_id,
        "message": "工作流启动中..."
    })

    # 后台线程执行工作流
    def run_workflow_background(job_id, app):
        result = run_workflow(app, thread_id=job_id)

    t = threading.Thread(target=run_workflow_background, args=(job_id, app))
    t.daemon = True
    t.start()
```

### 4. 工作流执行 (`agents/main_agent.py`)

```python
def run_workflow(application, thread_id):
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_workflow()

    # 保存作业申请
    save_job_application(thread_id, application)
    add_job_log(thread_id, {"action": "workflow_start", ...})

    # 初始化状态
    initial_state = {
        "application": application,
        "current_stage": "P1",
        "confirmed_stages": {},
        "pending_confirmations": [],
    }

    # 执行 P1→P2→...→P10
    result = graph.invoke(initial_state, config)

    # 保存状态快照
    save_job_state(thread_id, result, result.get("current_stage"))
```

### 5. P1 节点执行 (`agents/main_agent.py`)

```python
def p1_permit(state):
    # 步骤1: permit_submit - 提交作业申请
    # 步骤2: jsa_analyze - JSA分析
    # 步骤3: generate_draft - 生成作业票草稿
    # 步骤4: save_permit - 保存作业票

    # 每步调用 check_and_request_confirmation
    # 如果需要确认，设置 pending_confirmations 并返回 END
```

### 6. 前端轮询 (`web/app.js`)

```javascript
function startPollingWorkflow(jobId) {
    // 每 2 秒轮询一次状态
    workflowPollInterval = setInterval(() => {
        pollWorkflowState(jobId);
    }, 2000);
}

function pollWorkflowState(jobId) {
    fetch('/api/workflow/state?thread_id=' + jobId)
        .then(r => r.json())
        .then(data => {
            if (data.pending && data.pending.length > 0) {
                // 有待确认项，弹窗
                showHitlModal(pending[0], data.pending_data);
            } else if (data.status === 'completed') {
                // 工作流完成
                stopPolling();
            }
        });
}
```

### 7. HITL 弹窗确认 (`web/app.js`)

```javascript
function confirmDecision(decision) {
    const jobId = state.workflowState.threadId;
    const stage = pending[0];  // 待确认的阶段

    fetch('/api/workflow/confirm', {
        method: 'POST',
        body: JSON.stringify({
            thread_id: jobId,
            stage: stage,
            decision: decision
        })
    }).then(data => {
        // 继续轮询
        startPollingWorkflow(jobId);
    });
}
```

### 8. 后端确认接口 (`web/server.py`)

```python
elif path == "/api/workflow/confirm":
    thread_id = data.get("thread_id")
    stage = data.get("stage")
    decision = data.get("decision")

    result = confirm_and_continue(thread_id, stage, decision)
    pending = list_pending_confirmations(thread_id)

    self.send_json({
        "status": "waiting" if pending else "completed",
        "pending": [p.get("stage") for p in pending],
        ...
    })
```

### 9. 工作流继续 (`agents/main_agent.py`)

```python
def confirm_and_continue(thread_id, stage, decision):
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_workflow()

    # 更新确认状态
    confirmed_stages[stage] = True
    pending_confirmations.remove(stage)

    graph.update_state(config, update)
    result = graph.invoke(None, config)  # 继续执行

    save_job_state(thread_id, result, ...)
    add_job_log(thread_id, {"action": "workflow_continue", ...})

    return result
```

## 状态码

| 状态 | 说明 |
|------|------|
| `idle` | 空闲，无工作流 |
| `starting` | 工作流启动中 |
| `waiting` | 等待人工确认 |
| `completed` | 工作流完成 |
| `error` | 执行错误 |

## 持久化文件

```
data/jobs/{job_id}/
├── application.json    # 作业申请
├── state.json         # 工作流状态快照
├── logs.json          # 执行日志
└── confirmations.json # 确认记录
```

## 时序图

```
用户        前端                后端                工作流
 │           │                  │                   │
 │  点击启动  │                  │                   │
 │──────────>│                  │                   │
 │           │  /api/workflow/start                  │
 │           │────────────────>│                   │
 │           │  {job_id}       │                   │
 │           │<────────────────│                   │
 │           │                  │   后台线程        │
 │           │                  │──────────────────>│
 │  开始轮询  │                  │                   │
 │──────────>│                  │                   │
 │           │  /api/workflow/state                │
 │           │────────────────>│                   │
 │           │<────────────────│                   │
 │           │                  │  P1执行中...      │
 │           │                  │──────────────────>│
 │           │                  │  暂停(需确认)     │
 │           │<────────────────│                   │
 │  弹窗     │                  │                   │
 │──────────>│                  │                   │
 │  用户确认  │                  │                   │
 │──────────>│                  │                   │
 │           │  /api/workflow/confirm              │
 │           │────────────────>│                   │
 │           │                  │  confirm_and_continue
 │           │                  │──────────────────>│
 │           │                  │  继续执行P2...    │
 │           │                  │                   │
 │  轮询状态  │                  │                   │
 │──────────>│                  │                   │
 │           │  /api/workflow/state                │
 │           │────────────────>│                   │
 │           │<────────────────│                   │
 │           │                  │  P2执行中...     │
 │           │                  │                   │
 │           │                  │  ...              │
 │           │                  │                   │
 │           │                  │  完成             │
 │           │<────────────────│                   │
 │  停止轮询  │                  │                   │
 │           │                  │                   │
```

## 关键函数

| 文件 | 函数 | 说明 |
|------|------|------|
| `app.js` | `startWorkflow()` | 启动工作流 |
| `app.js` | `startPollingWorkflow()` | 开始轮询 |
| `app.js` | `pollWorkflowState()` | 轮询状态 |
| `app.js` | `confirmDecision()` | 确认决策 |
| `server.py` | `/api/workflow/start` | 启动接口 |
| `server.py` | `/api/workflow/confirm` | 确认接口 |
| `server.py` | `/api/workflow/state` | 状态查询 |
| `main_agent.py` | `run_workflow()` | 执行工作流 |
| `main_agent.py` | `confirm_and_continue()` | 确认后继续 |
