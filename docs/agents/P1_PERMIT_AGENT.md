# P1: 作业预约、JSA分析与作业票 Agent (p1_permit_agent)

## 概述

**作业许可管理专家**，负责处理作业申请、JSA（作业安全分析）和作业票生成。

## 主要功能

1. 根据作业基础信息识别作业类型，生成作业表单
2. 调用 JSA 分析工具识别危害因素和对应措施
3. 检查票证必填字段、风险措施完整性、人员资质冲突
4. 仅生成草稿，不自动审批

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_permit_agent(message)` | 运行 P1 Agent |
| `run_permit_agent_with_hitl(message, thread_id, resume, decision, notes)` | 运行 P1 Agent（支持 HITL 中断恢复） |
| `is_agent_interrupted(thread_id)` | 检查 Agent 是否处于中断状态 |
| `get_agent_next_tools(thread_id)` | 获取 Agent 下一个待执行工具 |
| `clear_agent_registry(thread_id)` | 清除 Agent 注册表 |
| `permit_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 |
|------|----------|------|
| `permit_submit` | 用户提交作业申请 | 提交作业申请，返回作业票草稿 |
| `jsa_analyze` | 用户请求 JSA 分析 | 分析 JSA，识别危害因素和措施 |
| `permit_generate_draft` | 用户请求生成作业票 | 生成作业票草稿，包含缺失项提示 |
| `permit_check` | 用户查询作业票状态 | 查询作业票状态 |

## HITL 支持

### Agent 注册表机制（兼容旧中断作业）

P1 Agent 使用全局 `_agent_registry` 注册表缓存 Agent 实例，支持同一 `thread_id` 的中断恢复：

```python
_agent_registry: Dict[str, Any] = {}  # thread_id → Agent 实例

def create_permit_agent_with_hitl(thread_id: str = "default"):
    """复用注册表中的 Agent，支持中断恢复"""
    if thread_id in _agent_registry:
        return _agent_registry[thread_id]  # 复用已有实例
    # ... 创建新 Agent 并注册
```

### 单次审批流程

```
execute_p1(job_id)
    ↓
run_permit_agent_with_hitl(message, job_id)
    ↓
自动完成申请整理、JSA 分析和作业票草稿生成
    ↓
返回一次 pending_confirmation（作业票最终审批）
    ↓
用户确认 → confirm_and_continue(P1)
    ↓
批准则进入 P2；否决则停止在 P1
```

每次 P1 执行只弹出一次审批窗口。旧版本已停在工具级 checkpoint 的作业仍保留恢复兼容逻辑。

### 审批配置

```python
# P1 全部处理完成后统一生成一次确认项
result["pending_confirmation"] = {
    "type": "permit_final_approval",
    "message": "P1 作业申请、JSA 与作业票草稿已生成，请进行最终审批",
}
```

## 执行链路

```
permit_submit (提交申请)
    ↓
jsa_analyze (JSA分析)
    ↓
permit_generate_draft (生成草稿)
    ↓
save_permit (保存作业票)
    ↓
人工确认 approve_permit
```

## 内部状态

```python
 PermitAgentState = {
    "messages": list,  # 对话消息
}
```

## P1 子步骤

| 步骤 | 说明 |
|------|------|
| `permit_submit` | 提交作业申请 |
| `jsa_analyze` | JSA 分析 |
| `generate_draft` | 生成作业票草稿 |
| `save_permit` | 保存作业票到文件 |
| `completed` | 完成 |

## 文件位置

`agents/p1_permit_agent.py`
