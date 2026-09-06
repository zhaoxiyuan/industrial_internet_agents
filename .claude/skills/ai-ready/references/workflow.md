# 工作流与 HITL 参考

## P1-P10 执行顺序

```
P1(许可) → P2(任务) → P3(上下文) → P4(绑定) → P5(验证)
    → P6(监控) → P7(风险) → P8(处置) → P9(闭环) → P10(归档)
```

---

## 文件传递协调

每个阶段通过文件系统传递数据，无直接 API 调用：

| 文件 | 路径 | 说明 |
|------|------|------|
| 作业申请 | `data/jobs/{job_id}/application.json` | 用户提交 |
| P1 结果 | `data/jobs/{job_id}/p1_result.json` | 作业票数据 |
| P2 结果 | `data/jobs/{job_id}/p2_result.json` | 任务实例 |
| ... | ... | ... |
| P10 结果 | `data/jobs/{job_id}/p10_result.json` | 归档完成 |
| 作业票 | `data/jobs/{job_id}/permit.json` | P1 生成的作业票 |
| 执行日志 | `data/jobs/{job_id}/logs.json` | 所有操作日志 |
| 确认记录 | `data/jobs/{job_id}/confirmation/` | 人工确认历史 |

---

## 两层 HumanInTheLoop

### Layer 1: Workflow 层

位于 `main_agent.py`，在阶段边界暂停等待人工确认：

```python
def check_and_request_confirmation(job_id: str, stage: str) -> dict:
    """检查阶段是否需要人工确认"""
    result = read_json_file(get_stage_result_path(job_id, stage))
    if result.get("pending_confirmation"):
        # 暂停，等待 confirm_and_continue()
        return result["pending_confirmation"]
    return None

def confirm_and_continue(job_id: str, stage: str) -> dict:
    """人工确认后恢复执行"""
    # 清除 pending_confirmation 标志
    # 触发下一阶段
```

**中断标志**: `pending_confirmation` 字段存在于阶段结果文件中

---

### Layer 2: Agent 层

位于 `hitl/human_in_the_loop.py`，使用 `HumanInTheLoopMiddleware` 在工具调用前中断：

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver

# 每个 Agent 有独立的 checkpointer
_permit_checkpointer = MemorySaver()

hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "permit_submit": True,      # 需要确认
        "jsa_analyze": True,        # 需要确认
        "permit_generate_draft": False,  # 不需要确认
        "permit_check": False,      # 不需要确认
    }
)

agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=load_system_prompt("P1"),
    middleware=[hitl_middleware],
    checkpointer=_permit_checkpointer,
)
```

---

## HITL 中断恢复流程

### Agent 层中断恢复

```python
# 1. 检查是否中断
if is_agent_interrupted(thread_id):
    state = agent.get_state(config)
    next_tools = list(state.next)  # ['jsa_analyze', ...]

# 2. 获取下一个工具
tools = get_agent_next_tools(thread_id)

# 3. 恢复执行（人工确认后）
result = agent.invoke(None, config)  # config 包含 thread_id

# 4. 清除注册表（如需）
clear_agent_registry(thread_id)
```

---

## 工作流状态管理

### 核心函数 (`agents/workflow/`)

```python
# workflow/job_persistence.py
def ensure_job_dir(job_id: str) -> str:
    """确保作业目录存在"""

def get_job_dir(job_id: str) -> str:
    """获取作业目录路径"""

def get_stage_result_path(job_id: str, stage: str) -> str:
    """获取阶段结果文件路径"""

def read_json_file(path: str) -> dict:
    """读取 JSON 文件"""

def write_json_file(path: str, data: dict) -> None:
    """写入 JSON 文件"""

# workflow/workflow_state.py
def get_workflow_state(job_id: str) -> dict:
    """获取工作流当前状态"""

def save_confirmation(job_id: str, stage: str, confirmed: bool) -> None:
    """保存人工确认结果"""
```

---

## REST API 端点

### 启动工作流
```
POST /api/workflow/start
Body: {"application": {...}}
Response: {"job_id": "xxx", "status": "started"}
```

### 确认阶段
```
POST /api/workflow/confirm
Body: {"job_id": "xxx", "stage": "P1", "confirmed": true}
Response: {"status": "confirmed", "next_stage": "P2"}
```

### 获取状态
```
GET /api/workflow/state?job_id=xxx
Response: {"job_id": "xxx", "current_stage": "P3", "pending_confirmation": null}
```

---

## 阶段执行入口

每个 Agent 文件暴露 `execute_stage(job_id, resume)` 函数：

```python
def execute_stage(job_id: str, resume: bool = False) -> dict:
    """阶段执行入口

    Args:
        job_id: 作业 ID
        resume: 是否从中断恢复

    Returns:
        阶段执行结果（写入 p{n}_result.json）
    """
```

---

## 日志规范

所有 Web API 端点日志格式：
```
[HTTP方法] [端点路径] 进入: 请求参数
[HTTP方法] [端点路径] 响应: 响应数据
[HTTP方法] [端点路径] 异常: 错误信息
```

WebSocket 日志推送：
```python
from .utils.logging_handler import push_websocket_log
push_websocket_log(thread_id, "INFO", "AGENT", "message", {context})
```
