# 工具详细参考

## 工具响应格式

所有工具返回标准 JSON:
```json
{
    "schema_version": "1.0",
    "command": "tool_name",
    "timestamp": "ISO8601",
    "result": {...},
    "errors": []
}
```

错误格式:
```json
{
    "schema_version": "1.0",
    "command": "tool_name",
    "timestamp": "ISO8601",
    "result": null,
    "errors": [{"code": "ERROR_CODE", "message": "错误信息", "recoverable": true}]
}
```

---

## P1 Permit Agent 工具

### permit_submit
```python
@tool(description="提交作业申请，返回作业票草稿。当用户提供作业申请信息时触发。")
def permit_submit(application: str) -> str:
    """
    提交作业申请，返回作业票草稿。

    参数:
        application: 作业申请 JSON 字符串，包含 job_content, region, equipment,
                    personnel, planned_start, planned_end 等字段
    返回:
        标准 JSON 响应，包含 task_id, permit_draft_id, status, missing_fields
    """
```

**触发场景**: 用户提供作业申请信息时
**返回示例**:
```json
{
    "result": {
        "task_id": "TASK-受限空间作业-炼油-20260820120000",
        "permit_draft_id": "PD-20260820120000",
        "status": "draft",
        "missing_fields": [],
        "jsa_complete": true
    }
}
```

---

### jsa_analyze
```python
@tool(description="分析JSA，识别危害因素和对应措施。当用户请求分析JSA时触发。")
def jsa_analyze(task_id: str) -> str:
    """
    分析 JSA（作业安全分析），识别危害因素和对应措施。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 hazards 分析结果
    """
```

**触发场景**: 用户请求分析 JSA 时
**返回示例**:
```json
{
    "result": {
        "task_id": "TASK-xxx",
        "hazards": [
            {
                "id": "H-001",
                "description": "受限空间内存在有毒有害气体",
                "severity": "高",
                "measures": ["气体检测", "强制通风", "佩戴呼吸器"]
            }
        ],
        "completeness_score": 0.85,
        "missing_items": ["建议补充应急救援预案"]
    }
}
```

---

### permit_generate_draft
```python
@tool(description="生成作业票草稿，包含缺失项提示。当用户请求生成作业票草稿时触发。")
def permit_generate_draft(task_id: str) -> str:
```

**触发场景**: 用户请求生成作业票草稿时

---

### permit_check
```python
@tool(description="查询作业票状态。当用户查询作业票状态时触发。")
def permit_check(permit_id: str) -> str:
```

**触发场景**: 用户查询作业票状态时

---

## P2 Task Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `task_list` | `(region: str, status: str)` | 列出任务 |
| `task_get` | `(task_id: str)` | 获取任务详情 |
| `task_instance_create` | `(task_id: str)` | 创建任务实例（幂等） |
| `task_subscribe` | `(task_id: str)` | 订阅任务变更 |

---

## P3 Context Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `context_build` | `(task_id: str)` | 构建 11 维度标准作业上下文 |
| `context_validate` | `(context_id: str)` | 验证上下文完整性 |
| `context_history` | `(context_id: str)` | 获取上下文变更历史 |

---

## P4 Binding Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `binding_match` | `(equipment_type: str, region: str)` | 自动匹配监控资源 |
| `binding_status` | `(binding_id: str)` | 查看绑定状态 |
| `binding_confirm` | `(binding_id: str)` | 人工确认绑定 |
| `binding_request_manual` | `(task_id: str, reason: str)` | 请求人工补充资源 |

---

## P5 Verify Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `verify_checklist` | `(task_id: str)` | 生成验证清单 |
| `verify_execute` | `(checklist_id: str)` | 执行验证检查 |
| `verify_recommendation` | `(task_id: str)` | 获取启动建议 |

---

## P6 Monitor Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `monitor_start` | `(session_id: str)` | 启动 A5 监控会话 |
| `monitor_events` | `(session_id: str, filter: str)` | 获取监控事件列表 |

---

## P7 Risk Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `risk_analyze` | `(event_data: str)` | 风险分析（调用 A6） |
| `risk_list` | `(start_time: str, end_time: str)` | 列出风险事件 |

---

## P8 Disposition Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `disposition_create` | `(job_id: str, event_type: str, data: str)` | 创建处置任务 |
| `disposition_confirm` | `(task_id: str, result: str)` | 确认处置任务 |
| `disposition_status` | `(task_id: str)` | 查看处置状态 |
| `disposition_list` | `(job_id: str, status: str)` | 列出所有处置任务 |
| `recall_jobs` | `(query: str, limit: int)` | 查询长期记忆（A7） |

---

## P9 Closure Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `closure_status` | `(job_id: str)` | 跟踪闭环状态 |
| `closure_verify` | `(job_id: str)` | 执行闭环完整性检查 |
| `closure_report` | `(job_id: str)` | 生成作业过程报告 |
| `closure_close` | `(job_id: str, notes: str)` | 关闭事件和作业 |

---

## P10 Archive Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `archive_task` | `(task_id: str)` | 归档任务数据 |
| `archive_cases` | `(start_date: str, end_date: str, case_type: str)` | 挖掘案例 |
| `archive_performance` | `(start_date: str, end_date: str)` | 分析处置效能 |
| `archive_suggestions` | `(scenario: str)` | 生成规则优化建议 |

---

## Main Agent 工具

| 工具名 | 签名 | 触发场景 |
|--------|------|----------|
| `start_workflow_tool` | `(application: str)` | 启动工作流 |
| `execute_stage_tool` | `(job_id: str, stage: str)` | 执行指定阶段 |
| `get_status_tool` | `(job_id: str)` | 查询作业状态 |
| `confirm_stage_tool` | `(job_id: str, stage: str)` | 确认阶段完成 |
| `list_pending_tool` | `()` | 列出待确认项 |

---

## 添加新工具流程

1. 在对应 Agent 文件中添加 `@tool` 装饰的函数
2. 在 `create_*_agent()` 中添加到 `tools` 列表
3. 更新 `agents/__init__.py` 导出（如需要）
4. 更新本知识库 `references/tools.md`
5. 验证语法: `python -m py_compile agents/<file>.py`
