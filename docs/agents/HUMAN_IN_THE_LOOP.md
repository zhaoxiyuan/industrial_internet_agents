# HumanInTheLoop (hitl/human_in_the_loop)

## 概述

**人工确认管理器**，管理所有需要人工确认的阶段，提供统一的确认接口。

## 核心概念

### 两层 HITL 机制

| 层级 | 说明 | 位置 |
|------|------|------|
| **Workflow 层** | 阶段边界暂停 | `main_agent.py` 的 `check_and_request_confirmation()` |
| **Agent 层** | 工具调用前暂停 | 各 Agent 的 `HumanInTheLoopMiddleware` |

### 确认操作类型

```python
class ConfirmAction(str, Enum):
    APPROVE = "approve"     # 批准
    REJECT = "reject"       # 否决
    ESCALATE = "escalate"   # 升级
    PAUSE = "pause"         # 暂停
    RESUME = "resume"       # 恢复
    RECTIFY = "rectify"     # 整改
    CONFIRM = "confirm"     # 确认
```

## 核心类

### HumanConfirmRequest

人工确认请求：

```python
@dataclass
class HumanConfirmRequest:
    stage: str              # 阶段 P1-P10
    action: str             # 操作类型
    task_id: str            # 任务ID
    title: str              # 确认标题
    description: str        # 确认描述
    options: List[Dict]     # 可选操作选项
    data: Dict              # 附加数据
    requested_at: str        # 请求时间
```

### HumanConfirmResult

人工确认结果：

```python
@dataclass
class HumanConfirmResult:
    stage: str              # 阶段
    action: str             # 操作
    task_id: str            # 任务ID
    confirmed: bool         # 是否确认
    selected_option: str    # 选择的选项
    notes: Optional[str]    # 备注
    confirmed_by: str        # 确认人
    confirmed_at: str       # 确认时间
```

## HumanInTheLoop 类

### 主要方法

| 方法 | 说明 |
|------|------|
| `create_request()` | 创建人工确认请求 |
| `get_request()` | 获取待确认请求 |
| `submit_result()` | 提交确认结果 |
| `get_result()` | 获取确认结果 |
| `has_pending()` | 检查是否有待确认 |
| `has_confirmed()` | 检查是否已确认 |
| `list_pending()` | 列出所有待确认 |
| `clear_confirmation()` | 清除确认结果 |

### 阶段配置

`STAGE_CONFIG` 定义了每个阶段的：
- `title`: 确认标题
- `description`: 确认描述
- `next_action`: 下一动作（continue/end）
- `options`: 可选操作列表

## 使用示例

```python
from agents.hitl.human_in_the_loop import get_hitl_manager, create_confirm_request

# 获取管理器
hitl = get_hitl_manager()

# 创建确认请求
request = hitl.create_request(
    stage="P1",
    action="approve_permit",
    task_id="TASK-123",
    data={"permit_id": "PD-001"}
)

# 提交确认结果
result = hitl.submit_result(
    stage="P1",
    action="confirm",
    task_id="TASK-123",
    selected_option="approve",
    notes="确认通过"
)
```

## Workflow 层 HITL

在 `main_agent.py` 中使用：

```python
def check_and_request_confirmation(state, stage, action):
    confirm_key = f"{stage}_{action}"

    if is_stage_confirmed(state, confirm_key):
        return None  # 已确认，继续

    # 添加到待确认列表
    state["pending_confirmations"].append(confirm_key)
    state["error"] = f"[PENDING_CONFIRMATION] {confirm_key}"

    return None  # 返回 None 表示暂停
```

## Agent 层 HITL

在各 Agent 中使用 `HumanInTheLoopMiddleware`：

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware

hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "tool_name": True,   # 需要确认
        "tool_name": False, # 自动批准
    }
)

agent = create_agent(
    model=llm,
    tools=tools,
    middleware=[hitl_middleware],
    checkpointer=MemorySaver(),
)
```

## 文件位置

`agents/hitl/human_in_the_loop.py`
