---
name: p8-p9-dual-agent-split
description: "P8/P9 风险处置闭环中两个真 LLM agent + 两个 service 的职责边界；P9 现在是单文件含两个 agent（审核 + 关闭文案），职责正交但不合并函数"
metadata:
  node_type: memory
  type: project
  originSessionId: 7050c2e5-a79f-45f7-ae5b-255de7c917a0
  modified: 2026-09-17T09:35:00.000Z
---

P8/P9 在 `docs/风险处置卡片交互设计.md` §2.0.1 是**两个真 LLM agent + 两个 service**，不是 1 个 agent 干所有事：

- **P8 处置 Agent**（`agents/p8_disposition_agent.py`）—— 业务互动决策：开票（`open_work_ticket`）+ 重发卡片（`resend_current_card`）。**有写 `job_status` 权限**。
- **P9 审核 Agent**（`agents/p9_agent.py::create_audit_agent` / `run_p9_materials_audit`）—— 材料质量审核：读 evidence + 复核文字，给 `{comment, confidence, evidence_check}` 写入 `state.review.p9_opinion`。**v2.1 起不推 job_status**（保持 `materials_in_audit`），决策权完全归人工 `record_closure_review`。
- **P9 关闭文案 Agent**（`agents/p9_agent.py::create_closure_agent` / `run_p9_closure_review`）—— 生成 ≤500 字关闭理由纯文本，写入 `state.review.p9_opinion_text` + 卡片 P9 段（approved → closed 触发）。**必须剥 `<think>...</think>` 推理块**（修过这个 bug：2026-09-17）。
- **卡片渲染 service**（`P8P9/services/card_render.py`）—— 纯流程：读 job_status → 选模板 → 调 Cardkit。不调 LLM。
- **回调路由 service**（`P8P9/services/callback_router.py`）—— 纯流程：群按钮 → if-else 校验 → 路由到 P8/P9。不调 LLM。
- **审核调度 service**（`P8P9/services/audit_scheduler.py`）—— daemon thread 调 P9 真审核 agent；LLM 失败时 fallback 到 `_run_p9_audit_fallback_placeholder`（`is_mock=true`，job_status 不动）。

**P9 文件结构演进**：早期是 `p9_audit_agent.py` + `p9_closure_agent.py` 两个文件；2026-09-17 v2.1 合并为单文件 `agents/p9_agent.py`，**但保持两个独立工厂 + 两个独立入口 + 两个独立 system_prompt + 两套 logger**。

★ 为什么合并到一个 .py：
- 两个 agent 都属于 P9 阶段（v2.1 设计文档 §2.0.1 的核心组件）
- 两个 agent 都无 tool（纯对话 LLM），共用 chat_model / extract_output / system_prompt loader
- agents/ 目录按"阶段"组织（p1_*.py / p2_*.py / ... / p10_*.py），两个 P9 文件违反单阶段单文件原则

★ 为什么**不**合并函数 / 工厂 / prompt：
- 审核 = 意见类（结构化 JSON + 反幻觉 + **不**推动状态机）；关闭 = 文案类（≤500 字纯文本）
- 触发时机不同：审核在 `materials_in_audit` 阶段；关闭在 `approved → closed` 终态
- 输出 schema 不同：审核必须严格 JSON（service 解析后写入 review.p9_opinion）；关闭只要纯文本（**剥 thinking 块后**）
- 合并后 prompt 既要管意见又要管文案 → LLM 易混杂 comment 与文案字段 → JSON 解析失败
- MiniMax-M3 reasoning 模型会先输出 `<think>...</think>` 块再用 JSON：
  - 审核：`_parse_audit_output` 必须剥 thinking 块（修过这个 bug，2026-09-17）
  - 关闭：`run_p9_closure_review` 必须剥 thinking 块（修过这个 bug：2026-09-17；不剥会把推理链写入 p9_opinion_text，污染字段 + JSON 失败）

★ v2.1 关键设计修正（2026-09-17）：
- P9 audit agent **没有决策权限** —— 只输出 comment（可包含「建议驳回 / 建议补充 / 建议人工复核」意见）
- P9 audit agent **不推 job_status** —— job_status 保持 `materials_in_audit`
- 决策权完全归人工 `record_closure_review(decision="approved"|"rejected")`
- 业务侧（`business_actions.record_closure_review`）需要支持从 `materials_in_audit` 直接调：
  - 状态机 `materials_in_audit → closed` 不被允许（必须经过 `ready_to_close`）
  - 因此 approved 分支需先内部 `set_job_status("ready_to_close")` 再 `set_job_status("closed")`
- 卡片渲染（`cards.py::_build_materials_in_audit`）从 confidence + is_mock 推断显示：
  - `is_mock=true` → 「P9 审核待人工介入」
  - `confidence>=0.8` → 「P9 审核已通过 · 建议关闭」（参考意见）
  - `0.5~0.8` → 「P9 审核中 · 建议人工复核」
  - `<0.5` → 「P9 审核中 · 建议驳回」
- 视图层（`agent_interface.py`）暴露给前端：`comment / confidence / evidence_check / audited_at / auditor / is_mock`（**不再含 verdict**）

★ 关键边界与故障点：
- P9 审核 agent **严禁**有 tool（不能查 P7 / 不能改状态 / 不能归档）
- P9 关闭 agent 同样严禁有 tool（纯文本生成）
- P8 严禁暴露 `submit_materials / acknowledge / relinquish / escalate / downgrade / record_review` 等修改 job_status 的 tool —— 这些走 `business_actions` 而非 P8 agent
- `P8P9/web_server.py` 启动时必须显式 `from P8P9.services import audit_scheduler as _audit_scheduler_module  # noqa: F401`（2026-09-17 修过：web_server 单独启动时 audit_scheduler 不被 import → `_register_self()` 不触发 → P9 真审核 agent 永远不会被调用）

**Why:** 第一版我误把"读状态选模板渲染"包装成 P9 卡片动作 agent，用户指出这是确定性流程不需要 LLM、定义上不属于 agent。**判定标准：需不需要 LLM 推理？不需要 → service；需要 → agent。** v2.1 又修正了"审核 agent 是否决策"的设计问题：审核 agent 给出 verdict 自动推状态机属于"决策"，但用户硬约束是"决策归人工"，所以改成纯文本意见。

**How to apply:**
- 以后任何把"流程化操作"包装成 agent 的提议都要先问"这里真的需要 LLM 决策吗"。已删除的 `P8_CARD_ACTION_SYSTEM_PROMPT.md` 就是反例——它本质是卡片操作 SOP，不是 agent prompt。
- 任何 P9 类的 LLM 调用都要**剥 `<think>...</think>` 块**（MiniMax-M3 reasoning 模型通用问题）；不要相信 `extract_output` 返回的"最终内容"是干净的。
- P9 单文件结构是用户明确决策（"可以合并但写到同一个 py"），以后改 P9 时保持「两工厂 / 两入口 / 两 prompt / 两 logger」四个独立。
- 修改 `record_closure_review` 时：必须保持 materials_in_audit 可直接调 approved（v2.1 设计：用户卡在 materials_in_audit 必须能解锁）。