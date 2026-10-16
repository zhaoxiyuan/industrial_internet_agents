# 小模型 Agent 工具循环上下文压缩调研

调研日期：2026-10-16（Asia/Shanghai）  
用途：作为 skill 平台后续实现、真实 Agent 测试和问题排查的参考。  
状态：调研结论及隔离实验记录，不代表功能已接入或业务验收通过。

## 1. 结论与适用环境

对当前 Qwen 7.6B、32K 环境，优先采用“每轮规则裁剪 + 结构化任务状态 + 原始结果外存”，将 LLM 摘要作为后备。不要默认每次工具返回后都额外调用一次模型写摘要，以免增加延迟，并让小模型承担额外的信息筛选任务。

这里的压缩发生在同一任务内部：

```text
模型 → tool call → 工具回执 → 压缩下一次输入 → 模型
```

不需要刷新 `thread_id`。任务和检查点可以保持不变，仅改变送给模型的上下文视图。

用户确认的 Mac 推理后端配置：

- 模型：`qwen25-agent`，7.6B，Q4_K_M。
- 单请求上下文：32768 tokens。
- 模型推理并发：5 路；每路独立拥有 32K，不能相加。
- 系统提示词、工具定义、用户消息、历史、工具结果和输出共同占用窗口。
- 输入目标约 24K，输出预留约 8K；实际输出仍受模型和网关限制。
- 接口、模型名称、鉴权沿用现有配置。

调研时，Main/P6/P7 的 `max_tokens` 为 8000。模型并发 5 不等于业务并发 5；P6 业务并发约束仍不超过 3。

## 2. 现有开源实现

| 实现 | 压缩机制 | 是否额外调用模型 | 对平台的适用性 |
|---|---|---|---|
| LangChain `ContextEditingMiddleware` / `ClearToolUsesEdit` | 超过阈值后，将旧工具结果替换为占位符，保留最近 N 条结果，并可排除指定工具 | 否 | 最容易接入，优先评估 |
| DeepAgents 大结果外存 | 大工具结果写入文件，模型只接收预览和路径，需要时再读取 | 否 | 适合原始监测数据、日志、长证据 |
| DeepAgents 旧工具参数截断 | 缩短旧 `AIMessage.tool_calls` 的大参数，保留最近消息 | 否 | 避免 P6 长 descriptions 在调用参数和后续读取中重复出现 |
| LangChain `SummarizationMiddleware` | 达到阈值后用摘要替换旧消息，保留最近消息，选择不拆开 AI/Tool 配对的切分边界 | 通常是 | 第二层兜底，需要额外预算和失败处理 |
| LangMem `SummarizationNode` / `RunningSummary` | 增量维护摘要及已总结消息标识，可单独生成模型输入而保留原始消息列表 | 是 | 适合完整审计与模型输入分离的自定义 LangGraph |
| Qwen-Agent 输入裁剪 | 超预算后缩短函数结果、删除中间步骤，必要时进一步截断消息 | 否 | 可借鉴策略，不必更换现有框架 |

### 2.1 LangChain：只编辑请求视图

`ContextEditingMiddleware.wrap_model_call` 复制消息列表，对副本应用编辑，再通过 `request.override(messages=...)` 调用模型。原始状态消息可继续用于检查点和审计。

`ClearToolUsesEdit` 的关键参数：

- `trigger`：触发清理的 token 阈值。
- `keep`：保留最近多少条工具结果，**不是完整业务轮数**。
- `exclude_tools`：指定不清理的工具名。
- `clear_tool_inputs`：是否同时清空对应的旧工具调用参数。
- `clear_at_least`：清理时至少尝试回收的 token 数量。

限制：通用清理器不理解业务领取、提交和重试状态。仅保留最后几条工具结果，不能保证有效 claim 或当前证据还在上下文，也不能保证一个巨大的最新回执装得下。

源码：[context_editing.py](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/middleware/context_editing.py)

### 2.2 DeepAgents：大结果外存与旧参数截断

大工具结果可以保存到后端，模型只接收预览与路径；替换消息仍保留工具调用 ID 等身份字段。读取外存结果的权限、分页、大小上限和保留策略需要与平台对接。

旧工具调用参数也会占上下文。例如 P6 提交的整份 `descriptions` 已存在于 AI 工具调用消息中，后续读取描述可能再次返回相同内容。只压缩工具输出，不能解决这一部分重复。

DeepAgents 的 `truncate_args_settings` 可配置触发条件、保留范围和参数值长度；这是独立于完整摘要的轻量优化。当前有效领取、尚未确认结果的提交及关键参数不应直接截断。

源码：

- [大结果外存与预览](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/_message_eviction.py)
- [文件系统中间件](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/filesystem.py)
- [旧参数截断与摘要](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/summarization.py)

### 2.3 LangChain / LangMem：按阈值摘要

LangChain 摘要中间件支持 token、消息数或模型上下文比例等触发方式，保留指定范围的最近消息。它会更新图状态中的消息历史，因此接入前要检查平台中依赖完整历史的提交保护、审计和恢复逻辑。

LangMem 的 `RunningSummary` 记录已有摘要和已总结消息 ID，避免反复总结同一批消息；`SummarizationNode` 可输出单独的摘要消息字段，将模型输入与完整历史解耦。

摘要需要防止丢失精确时间、记录 ID、未提交状态、错误和未知投递状态。摘要模型本身也受上下文窗口限制；摘要失败不能被当作成功压缩。

参考：

- [LangChain 摘要源码](https://github.com/langchain-ai/langchain/blob/master/libs/langchain_v1/langchain/agents/middleware/summarization.py)
- [LangMem 摘要源码](https://github.com/langchain-ai/langmem/blob/main/src/langmem/short_term/summarization.py)
- [LangMem 使用指南](https://langchain-ai.github.io/langmem/guides/summarization/)

### 2.4 Qwen-Agent：分层裁剪

`_truncate_input_messages_roughly` 体现了无需模型的分层策略：先缩短函数结果，再删除中间步骤，必要时进一步截断最后步骤或普通消息。源码明确说明函数调用和多模态 token 估算存在不精确之处。

值得借鉴其逐层回收思路，但不能直接认为设置 `max_input_tokens` 就已经精确覆盖我们最终请求中的全部工具定义和模板开销。

源码：[qwen_agent/llm/base.py](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py)

### 2.5 JetBrains Research：Observation Masking

`the-complexity-trap` 提供保留最近观测、隐藏旧观测的历史处理器，并比较了 Observation Masking、LLM 摘要及混合策略。

其编码 Agent 实验表明，简单遮蔽能显著降低成本，LLM 摘要未稳定优于简单遮蔽。该结果支持先测试低开销方案，**不能直接推导当前 Qwen 工业任务的成功率、吞吐或节省比例**。

参考：

- [项目与实验结论](https://github.com/JetBrains-Research/the-complexity-trap)
- [历史处理器源码](https://github.com/JetBrains-Research/the-complexity-trap/blob/main/sweagent/agent/history_processors.py)
- [原始论文](https://arxiv.org/abs/2508.21433)

### 2.6 其他方向：LLMLingua

[Microsoft LLMLingua](https://github.com/microsoft/LLMLingua) 提供模型驱动的提示词压缩及 LangChain 集成。对当前以精确 JSON、阈值、时间和领取状态为主的流程，建议先评估结构化裁剪，再考虑此类有损压缩；不能默认压缩后的文本仍完整保留业务语义。

## 3. 当前平台核实结果

2026-10-16 对磐石云运行容器进行了只读核查：

| 依赖 | 实际版本 |
|---|---|
| LangChain | 1.4.4 |
| LangGraph | 1.2.14 |
| DeepAgents | 0.7.24 |
| langchain-core | 1.6.9 |

组件已安装，但当前 `platform_core/runtime.py` 的 `create_agent` 只接入平台中间件和 Skills 中间件，**未接入上述通用上下文编辑或摘要机制**。

现有 P6/P7 紧凑业务回执已经减少重复样本，但不等于完整的循环历史压缩。现有提交保护和有限续处理提醒也不等于压缩机制。

当前任务上下文行为：

- 每个任务使用任务 ID 作为 LangGraph `thread_id`。
- 新对话不继承其他对话历史。
- 同一会话继续发消息虽创建新任务，但平台会重新加载该会话历史。
- 每轮心跳创建独立根任务，不继承上一轮聊天历史；业务状态通过工具读取。
- 同一任务等待子任务后恢复时，沿用原 thread_id 和检查点。

因此，新对话与心跳的隔离不能解决同一任务内多次 tool call 累积导致的上下文增长。

### 隔离小实验

在磐石云实际依赖上运行了无模型、无业务数据的实验：

- 构造 6 组工具调用与结果。
- 配置保留最近 2 条工具结果，并排除一个指定工具。
- 3 条旧工具结果被替换为占位符。
- 最近两条结果、指定保护的结果及全部 `tool_call_id` 保留。
- 对副本操作，原始消息未改变。

该实验只验证组件行为，不证明 Qwen 的任务成功率。没有修改平台运行配置或触发真实业务操作。

默认参数不能照搬：调研时 `ClearToolUsesEdit.trigger` 默认为 100000，`FilesystemMiddleware.tool_token_limit_before_evict` 默认为 20000。前者高于本模型窗口，后者可能让单份结果占据大部分输入预算。

GitHub 主分支会变化，落地时应以锁定版本源码为准，不能直接使用主分支新增接口。

## 4. 建议的平台实现

```text
工具完成
  → 完整结果保存到业务库/审计记录
  → 代码更新结构化任务状态
  → 构造本轮模型输入副本
  → 裁剪已完成事项的旧结果和旧参数
  → 按当前阶段筛选工具
  → 核算总输入预算
  → 调用模型
```

### 4.1 用结构化状态替代不断增长的叙述

由代码维护，而非让模型自由总结：

- 当前票号、精确起止、用户授权范围。
- 当前阶段、有效领取、必需处理项。
- 已成功提交的连续进度。
- 未解决错误、允许的下一步、证据引用。
- 外部动作的已确认/未知状态及恢复条件。

数据库和真实回执是事实来源，模型之前的文字总结不能覆盖它们。此前错误的阈值结论也不能因被压成摘要而变成可信事实。

### 4.2 按业务提交边界裁剪

P7 的基本边界是 `claim_description → complete_assessment` 成功结束：

| 内容 | 当前项未提交 | 提交成功后 |
|---|---|---|
| 当前监测事实 | 保留 | 下一轮中移除或缩短 |
| 有效 claim、必需异常 key | 原样保留 | 不再作为有效领取使用 |
| 提交结果 | 保留真实回执 | 缩为记录号、时间范围、状态、事件 ID |
| 更早描述 | 保留简短完成记录 | 保留简短完成记录 |
| 原始证据 | 按需读取 | 继续外存 |

只有真实提交成功才允许跨过该裁剪边界。结果未知、提交失败、仍有效的领取及尚未处理的关闭事件不能被当成已完成事项清理。

### 4.3 按阶段筛选工具

LangChain 支持在 `wrap_model_call` 中使用 `request.override(tools=...)` 筛选工具。可以根据平台可信状态，为“未领取”“已领取”“事件归档”等阶段提供更小的工具集合。

例如 P7 已领取时，优先提供提交、证据读取和失败工具，减少重复领取或误读事件。筛选不能替代服务端权限和参数校验，也不能裁掉当前必需的恢复入口。

参考：[LangChain 动态工具选择](https://docs.langchain.com/oss/python/langchain/context-engineering#selecting-tools)

### 4.4 总预算检查

输入约 24K 是全部输入的总预算，不是仅限制消息历史：

```text
系统提示词 + 动态注入的 Skill + 工具 schema + 用户消息
+ 状态卡 + 历史 + 当前工具结果 + 模板开销
+ 预留输出 + 安全余量 ≤ 32768
```

建议使用与后端匹配的 Qwen tokenizer 核算中文和 JSON，并用后端实际 usage 校准。通用字符估算只宜作为回退，不可直接声称是精确硬上限。

如果最新的一份必需证据本身就过大，清理旧历史也无济于事；需要分页、按字段读取或拆分处理。

建议落地顺序：

1. 业务完成后裁剪旧回执和旧参数，并保留结构化状态。
2. 大结果外存与受控分页读取。
3. 按阶段筛选工具，并实施完整请求预算检查。
4. 超预算时再使用受控增量摘要。

以上是建议，尚未以真实 Qwen 任务验证或部署。

## 5. 后续测试与问题排查清单

| 测试项 | 应检查的结果 |
|---|---|
| 同一任务连续处理 1、3、10 份描述 | 输入不随已完成描述无限线性增长；当前项事实完整 |
| 当前有效领取跨多次模型调用 | claim、所需证据及必需异常项不丢失 |
| 工具调用配对 | AI tool call 与对应 ToolMessage 不被拆散；并行调用的全部结果保持配对 |
| 提交参数错误后修正 | 最近错误与当前有效领取保留；不重复产生业务结果 |
| 提交结果未知 | 不因裁剪或摘要误判为未执行并自动重发 |
| closed_events 非空 | 待归档事件保留，不能仅因描述提交成功而清掉 |
| 大于预算的最新工具结果 | 走分页或外存，不只依赖清理旧消息 |
| 工具调用参数很长 | 同时测量旧 AI 参数与工具输出，不只统计 ToolMessage |
| 中文、JSON、全部工具 schema | 压缩触发阈值与实际请求 usage 对齐 |
| Skills 动态注入 | 总预算核算发生在最终提示词与工具集合确定之后 |
| 等待子任务后恢复 | thread_id、业务状态和压缩策略一致，检查点可恢复 |
| 同一会话发下一条消息 | 不因重新加载原始会话历史而绕过预算检查 |
| 摘要失败、超时或不完整 | 不把失败占位符当成可靠摘要替换关键事实 |
| 多 Agent 并发 | 每个任务的状态与外存引用隔离，不能串入其他票或任务 |

建议记录：每轮完整输入 token、各部分占比、压缩前后大小、保留/清理消息 ID、压缩耗时、额外摘要调用次数、模型耗时、业务提交成功数、重复调用数、错误类型和连续进度。

对比实验应使用相同模型、业务数据、任务范围和输出配置，分别比较无压缩、规则裁剪、规则裁剪加状态卡、再加摘要。组件能缩短输入不等于业务成功率提高，需要分别报告。

压缩主要针对上下文累积和干扰。P6 阈值误判、P7 提前退出、工具参数错误仍需分别诊断，不能一律归因于窗口大小。

补充官方参考：[短期记忆与消息裁剪](https://docs.langchain.com/oss/python/langchain/short-term-memory)。
