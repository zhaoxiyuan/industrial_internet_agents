---
name: p1-permit-workflow
description: 用于理解、修改或排查本项目 P1 作业许可阶段，包括作业票／许可证、JSA（工作前安全分析）、两次审批、安全措施适用性判定及勾叉准确性与稳定性、知识库依据注入、出票和票面渲染。相关定位词包括 p1_permit_agent、jsa_approval、permit_final_approval、permit_template、申请指纹、p1_result、permit.json。上述词须结合 P1 语境判断；仅讨论 P2–P10 如何消费或验证作业票，或不涉及 P1 的一般流程调度时，不使用本 skill。
---

# P1 作业许可流程

本 skill 描述 **P1（作业许可）阶段** 的完整运行链路与改动须知，供修改 / 排查 P1 相关功能时快速定位。

## 边界（重要）

**只覆盖 P1。** P2–P10 各阶段在哪个文件、有哪些工具，请用 `ai-ready` skill——那是横向地图，本 skill 是 P1 的纵向深挖。本 skill 涉及的文件都属于 P1 链路：`p1_permit_agent.py`、`workflow/{jsa_analyze,jsa_report,permit_template,article_knowledge}.py`、`data/permit_templates/`、`data/knowledge/`。

**这里只写路径，不搬内容。** 提示词、字段配置这类东西，skill 里只写"在哪个文件"，不把正文抄进来。原因很直白：抄一份进来就有两处内容，改了原文件、抄件不会跟着改，读 skill 的人拿到的就是错的。`ai-ready` 当初正是把 P1 信息抄进了自己里面，后来 P1 改成"两次审批"，那份抄件就一直错着。

## 链路总览

```
浏览器                                web/app.js  fetch POST /api/workflow/start
   ▼
[路由]      web/server.py:69              /api/workflow/* 分发
   ▼
[启动]      web/api/workflow.py:387       handle_workflow_start → 起后台线程
   ▼
[调度]      agents/main_agent.py:1174     run_workflow（阶段循环 :1215）
              └ :1025 execute_stage_with_retry
              └ :762  STAGE_EXECUTORS["P1"]  →  p1_permit_agent.execute_stage
   ▼
[P1]        agents/p1_permit_agent.py:1196  execute_stage   ← 会被进入两次
              │
              ├─ 第一遍（未批准）:1263 _build_jsa_result ……【模型调用 1：JSA】
              │     jsa_analyze.py（生成+校验） + jsa_report.py（报告渲染）
              │     → :1271 挂起，等 jsa_approval（第一次审批）
              │
              └─ 第二遍（已批准）:1251 选 builder
                    _build_manual_permit_result(:703) 或 _build_docx_permit_result(:277)
                    └ :537 _decide_safety_measures ……【模型调用 2：措施判定】
                         └ article_knowledge.py（知识库对齐 + 注入条文依据）
                    └ permit_template.py（票面行 + 渲染）
                    → :1291 _process_p1_result 落盘
                    → :1380 挂起，等 permit_final_approval（第二次审批）
   ▼
[审批]      web/api/workflow.py:451 → main_agent.py:1313 confirm_and_continue
   ▼
批准 → P1 completed → 循环进入 P2
```

**两次模型调用是本流程唯一的外部网络依赖**（其余为纯 Python：规则 + 文件读写）。但它们**不是唯一的失败来源**——模板读取、文件读写、配置错误同样会让流程失败。

## 两次模型调用速查

主路径（网页工作流）有**两处**业务模型调用，由 `execute_stage` 的 `approved` 判定（`:1240`）分流：申请未批准 → 第一遍走 JSA；JSA 批准后 → 第二遍走出票与措施判定。**正常成功运行各发生一次**，但**空清单、重新执行、断点恢复都会改变实际次数**。除这两处外，主路径不再调用模型。（另有 CLI 的 mock 工具链会加载 `P1_PERMIT_SYSTEM_PROMPT.md`，见 `references/constraints.md` 第八节。）

| | JSA 分析 | 安全措施判定 |
|---|---|---|
| 入口 | `p1_permit_agent.py:122 _build_jsa_result` | `p1_permit_agent.py:537 _decide_safety_measures` |
| 生成模块 | `workflow/jsa_analyze.py` | 同左（**在 p1 内部**）+ `article_knowledge.py` |
| 提示词 | `agents/system_prompt/JSA_SYSTEM_PROMPT.md` | `agents/system_prompt/P1_MEASURES_SYSTEM_PROMPT.md` |
| 温度 / 思考 | temperature 0.2，思考强度**服务默认** | temperature 0.0，`reasoning_effort="low"` |
| 知识库依据 | **不接**（上下文只有措施清单原文） | **接**：对齐成功后，仅**配置了 `refs` 的条目**附 `〖依据·第N条〗` + 条文原文 |
| 失败行为 | `source="unavailable"`，**不产出内容** | `source="unavailable"`、`items=[]`，**不写任何勾**，退回人工 |
| 拦阻 | JSA 失败 → **不允许批准出票**（`main_agent.py:1371`） | 判定失败 → 票面措施不出勾，仍可人工复核 |

> 注意：JSA 提示词里写着"部分措施后面附有办法条文依据"，但 **JSA 上下文实际不接知识库**（实测 `〖依据` 出现 0 次）——这是提示词与实现不符，改 JSA 时别被它误导。

## 关键文件索引（改什么看哪里）

| 想改的东西 | 文件 |
|---|---|
| P1 全部业务逻辑（分流 / 两次审批 / 判定 / 出票） | `agents/p1_permit_agent.py` |
| JSA 生成规则、上下文、严格解析 | `agents/workflow/jsa_analyze.py` |
| JSA 报告 docx 渲染 | `agents/workflow/jsa_report.py` |
| 票面字段 / 落位 mode / 渲染 / 措施清单 | `agents/workflow/permit_template.py` + `data/permit_templates/permit_templates.json` |
| 知识库读取与措施对齐 | `agents/workflow/article_knowledge.py` + `data/knowledge/permit_articles.json` |
| 知识库结构 / 生成脚本 | `tools/build_article_knowledge.py`（生成）+ `data/knowledge/permit_articles.json`（数据本体） |
| 两处提示词口径 | `agents/system_prompt/{JSA,P1_MEASURES}_SYSTEM_PROMPT.md` |
| 模型与思考强度参数 | `agents/model/chat_model.py`、`agents/model/config.py` |
| 阶段调度 / 审批 / 重试 / 锁 | `agents/main_agent.py`、`agents/workflow/{file_utils,job_persistence}.py` |
| 请求路由 / 下载接口 | `web/server.py`、`web/api/workflow.py` |

## 硬约束（改 P1 前必读）

完整红线与理由见 `references/constraints.md`，速记五条：

1. **主工作流入口强制先批准 JSA**：基本信息 → JSA → 批准 → 出票；出票入口（`execute_stage:1251`）在 `if approved:` 内，JSA 分析失败时连"批准"都批不动。但这**不等于"所有入口都受保障"**：下载接口只在 `p1_phase=="jsa_review"` 时返回 409（**不是**正向的批准校验），CLI 的 mock 工具链也能产出草稿。改 P1 时不要动这几道闸。
2. **两次模型调用的失败都必须"降级为空"，不许塞编造内容**。
3. **前端改动有跨仓库红线**：`web/app.js` 新增 `fetch('/api/…')` 必须同步改 console 侧 `EXPECTED_API_FETCHES`，否则「作业监测」打不开。
4. **单测必须打桩模型**（JSA / 判定会真调，一次几十秒）；改完文件必须回读校验。
5. **知识库判定靠三者配合**：提示词只给口径，条文依据由 `p1_permit_agent.py` 代码注入，数据来自知识库 JSON——改口径改提示词，改依据注入改代码。

## 详细参考

**完整运行流程**（按时间顺序，含文件行号）：阅读 `references/flow.md`。

**三大机制深挖**（两次审批状态机 / 知识库对齐与依据注入 / 票面落位 mode）：阅读 `references/mechanisms.md`。

**红线与坑**（改代码前必读）：阅读 `references/constraints.md`。
