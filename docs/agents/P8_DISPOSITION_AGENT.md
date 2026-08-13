# P8: 人机协同处置 Agent (p8_disposition_agent)

## 概述

**人机协同处置专家**，负责创建和跟踪处置任务，按角色与权限推送责任人。

## 主要功能

1. 创建处置任务
2. 按风险等级推送（消息/短信/电话）
3. 形成整改、暂停、复核或升级建议
4. 跟踪处置状态

## 入口函数

| 函数 | 说明 |
|------|------|
| `run_disposition_agent(message)` | 运行 P8 Agent |
| `disposition_demo(message, history)` | Gradio ChatInterface 兼容格式 |

## 工具定义

| 工具 | 触发条件 | 说明 | HITL |
|------|----------|------|------|
| `disposition_create` | 用户创建处置任务 | 创建处置任务 | ✅ 需要确认 |
| `disposition_confirm` | 用户确认处置 | 人工确认处置任务 | ✅ 需要确认 |
| `disposition_status` | 用户查看状态 | 查看处置状态 | ❌ 自动批准 |
| `disposition_list` | 用户列出任务 | 列出所有处置任务 | ❌ 自动批准 |
| `recall_jobs` | 用户明确查询历史（"昨天那个事件最后怎么处理的"） | **长期记忆查询（罗盘长期记忆 LLM 入口）**：索引层子串搜索 + 数据层精确查询；两步走检索模式 | ❌ 自动批准（只读） |

> **罗盘长期记忆入口**：`recall_jobs` 是 P8 长期记忆的 **LLM 唯一入口**，
> 数据源 [A7/storage/p8_long_term.py](../../A7/storage/p8_long_term.py) 顶部"长期记忆接口（罗盘长期记忆）"注释块。
> 内部实现：索引层（轻量；一句话描述）+ 数据层（按需精确加载）；
> "两步走"检索：`recall_jobs(query)` 拿到索引 → `recall_jobs(detail_p8_job_id=...)` 拿完整归档。

## HITL 支持

```python
hitl_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "disposition_create": True,    # 创建处置任务需要确认
        "disposition_confirm": True,    # 确认处置需要确认
        "disposition_status": False,     # 查询状态自动批准
        "disposition_list": False,      # 查询列表自动批准
        "recall_jobs": False,            # 长期记忆只读 — 自动批准（不阻塞）
    }
)
```

## 推送策略

| 风险等级 | 推送方式 | 处理时限 |
|----------|----------|----------|
| `LOW` | 消息推送 | 24h |
| `MEDIUM` | 短信+消息 | 4h |
| `HIGH` | 电话+短信+消息 | 1h |
| `CRITICAL` | 电话直达 | 实时 |

## 操作类型

| 操作 | 说明 |
|------|------|
| `approve` | 批准 |
| `reject` | 否决 |
| `escalate` | 升级 |
| `rectify` | 整改 |
| `pause` | 暂停作业 |
| `resume` | 恢复作业 |

## 人工控制点

- 高风险事件：必须人工确认
- 写入操作：必须人工确认
- 暂停作业：必须人工确认
- 恢复作业：必须人工确认

## 文件位置

`agents/p8_disposition_agent.py`

## 长期记忆后端（罗盘长期记忆）

数据源：[`A7/storage/p8_long_term.py`](../../A7/storage/p8_long_term.py)

- **双层 JSON 持久化**：索引层（`p8_archive.index.json`，轻量）+ 数据层（`p8_archive.json`，完整）
- **仓库级全局**：路径 `data/jobs/_long_term/`；跨 job_id 共享
- **9 个公共函数**（详见源码顶部注释块）：
  - 写入（仅 P8ArchiveMiddleware）：`save_archived_job`
  - 数据层：`get_archived_job` / `search_archived_jobs` / `load_all_archived_jobs`
  - 索引层：`get_index_entry` / `search_archived_descriptions` / `load_all_index_entries`
  - 维护：`reset_archive`
- **索引条目格式**：`[<max_level>] <risk_basis 前 30 字>；<decision> by <decider> @ <archived_at 截 YYYY-MM-DD HH:MM>`
- **线程安全**：模块级 `threading.Lock` 包裹所有 IO
- **进程重启可恢复**：模块导入时自动 `_init()` 从 JSON 加载到内存
