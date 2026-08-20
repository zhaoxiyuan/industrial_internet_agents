# Agent 详细参考

## P1 Permit Agent

**文件**: `agents/p1_permit_agent.py`
**System Prompt**: `agents/system_prompt/P1_PERMIT_SYSTEM_PROMPT.md`
**工厂函数**: `create_permit_agent()`, `create_permit_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p1_result.json`
**Checkpointer**: `_permit_checkpointer = MemorySaver()`

**工具**: `permit_submit`, `jsa_analyze`, `permit_generate_draft`, `permit_check`

**HITL 配置**:
```python
hitl_middleware = HumanInTheLoopMiddleware(interrupt_on={
    "permit_submit": True,
    "jsa_analyze": True,
    "permit_generate_draft": True,
    "permit_check": True,
})
```

**全局注册表**: `_agent_registry: Dict[str, Any]` - 按 thread_id 缓存 Agent 实例

**关键函数**:
- `is_agent_interrupted(thread_id)` - 检查中断状态
- `get_agent_next_tools(thread_id)` - 获取下一个待执行工具
- `clear_agent_registry(thread_id)` - 清除注册表
- `run_permit_agent_with_hitl(message, thread_id, resume)` - HITL 执行入口

---

## P2 Task Agent

**文件**: `agents/p2_task_agent.py`
**System Prompt**: `agents/system_prompt/P2_TASK_SYSTEM_PROMPT.md`
**工厂函数**: `create_task_agent()`, `create_task_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p2_result.json`

**工具**: `task_list`, `task_get`, `task_instance_create`, `task_subscribe`

---

## P3 Context Agent

**文件**: `agents/p3_context_agent.py`
**System Prompt**: `agents/system_prompt/P3_CONTEXT_SYSTEM_PROMPT.md`
**工厂函数**: `create_context_agent()`, `create_context_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p3_result.json`

**工具**: `context_build`, `context_validate`, `context_history`
**职责**: 构建标准作业上下文（11 维度）

---

## P4 Binding Agent

**文件**: `agents/p4_binding_agent.py`
**System Prompt**: `agents/system_prompt/P4_BINDING_SYSTEM_PROMPT.md`
**工厂函数**: `create_binding_agent()`, `create_binding_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p4_result.json`

**工具**: `binding_match`, `binding_status`, `binding_confirm`, `binding_request_manual`
**职责**: 自动匹配监控资源（摄像头、传感器、定位）

---

## P5 Verify Agent

**文件**: `agents/p5_verify_agent.py`
**System Prompt**: `agents/system_prompt/P5_VERIFY_SYSTEM_PROMPT.md`
**工厂函数**: `create_verify_agent()`, `create_verify_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p5_result.json`

**工具**: `verify_checklist`, `verify_execute`, `verify_recommendation`
**职责**: 作业前条件验证清单和执行

---

## P6 Monitor Agent

**文件**: `agents/p6_monitor_agent.py`
**System Prompt**: `agents/system_prompt/P6_MONITOR_SYSTEM_PROMPT.md`
**端口**: 5002 (FastAPI，与 P7 A6 路由共享)
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p6_result.json`

**工具**: `monitor_start`, `monitor_events`
**集成**: A5 前端 + A6 路由（通过 `p7_risk_agent.register_a6_routes()` 挂载）

**启动命令**: `python agents/p6_monitor_agent.py --port 5002`

---

## P7 Risk Agent

**文件**: `agents/p7_risk_agent.py`
**System Prompt**: `agents/system_prompt/P7_RISK_SYSTEM_PROMPT.md`
**路由挂载**: 通过 `register_a6_routes()` 挂载到 P6 的 FastAPI
**阶段触发**: `trigger_a6_assessment()` 进程内触发

**工具**: `risk_analyze`, `risk_list`
**职责**: 风险评估，调用 A6 Agent

---

## P8 Disposition Agent

**文件**: `agents/p8_disposition_agent.py`
**System Prompt**: `agents/system_prompt/P8_DISPOSITION_SYSTEM_PROMPT.md`
**工厂函数**: `create_disposition_agent()`, `create_disposition_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p8_result.json`

**工具**: `disposition_create`, `disposition_confirm`, `disposition_status`, `disposition_list`, `recall_jobs`
**A7 集成**: `recall_jobs` 是 A7 长期记忆的 LLM 入口

---

## P9 Closure Agent

**文件**: `agents/p9_closure_agent.py`
**System Prompt**: `agents/system_prompt/P9_CLOSURE_SYSTEM_PROMPT.md`
**工厂函数**: `create_closure_agent()`, `create_closure_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p9_result.json`

**工具**: `closure_status`, `closure_verify`, `closure_report`, `closure_close`
**职责**: 闭环跟踪、完整性检查、报告生成

---

## P10 Archive Agent

**文件**: `agents/p10_archive_agent.py`
**System Prompt**: `agents/system_prompt/P10_ARCHIVE_SYSTEM_PROMPT.md`
**工厂函数**: `create_archive_agent()`, `create_archive_agent_with_hitl()`
**阶段执行**: `execute_stage(job_id, resume)` → `data/jobs/{job_id}/p10_result.json`

**工具**: `archive_task`, `archive_cases`, `archive_performance`, `archive_suggestions`
**职责**: 归档、误检/漏检/规则冲突案例挖掘、效能分析

---

## Main Agent

**文件**: `agents/main_agent.py`
**System Prompt**: `agents/system_prompt/MAIN_AGENT_SYSTEM_PROMPT.md`
**工厂函数**: `create_main_agent()`
**职责**: P1-P10 工作流协调（文件传递方式）

**工具**: `start_workflow_tool`, `execute_stage_tool`, `get_status_tool`, `confirm_stage_tool`, `list_pending_tool`

**关键函数**:
- `run_workflow(job_id, application)` - 完整工作流
- `confirm_and_continue(job_id, stage)` - 确认后恢复
- `get_workflow_state(job_id)` - 获取状态
- `list_pending_confirmations()` - 列出待确认

**阶段执行器注册** (`STAGE_EXECUTORS`):
```python
STAGE_EXECUTORS = {
    "P1": p1_permit_agent.execute_stage,
    "P2": p2_task_agent.execute_stage,
    # ...
}
```

---

## Agent 创建模式

### 基础版本
```python
def create_agent():
    llm = create_chat_model_with_logging("AGENT_NAME")
    tools = [tool1, tool2, ...]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("AGENT_NAME"))
```

### HITL 版本
```python
def create_agent_with_hitl():
    hitl_middleware = HumanInTheLoopMiddleware(
        interrupt_on={"tool1": True, "tool2": False}
    )
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("AGENT_NAME"),
        middleware=[hitl_middleware],
        checkpointer=_checkpointer,
    )
```

### LLM 模型创建
```python
from .model.chat_model import create_chat_model_with_logging
llm = create_chat_model_with_logging("AGENT_NAME", job_id)
```

### System Prompt 加载
```python
from .utils.system_prompt import load_system_prompt
system_prompt = load_system_prompt("P1")  # 返回字符串
```
