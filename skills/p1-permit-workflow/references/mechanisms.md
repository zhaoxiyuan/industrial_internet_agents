# P1 三大机制深挖

改判定 / 改票面 / 改审批时最常需要查的三块。按机制组织，与时间线的对应见 `flow.md`。

## 机制一：两次审批状态机

P1 一个阶段里塞了**两次独立审批**：`jsa_approval`（JSA 报告审批）与 `permit_final_approval`（作业票最终审批）。二者共享 `p1_result.json`，靠三个字段区分阶段。

### 三个关键字段

| 字段 | 含义 | 写入处 |
|---|---|---|
| `jsa_application_fingerprint` | 申请全文的 sha256。**申请一改就失配**，是"批准是否仍有效"的锚 | `p1_permit_agent.py:1238` |
| `p1_phase` | `jsa_review`（等 JSA 审批）/ `permit_build`（已批、正出票）/ `permit_review`（等最终审批） | `:1267` / `main_agent.py:1402` / `:1250` |
| `pending_confirmation.approval_id` | 每次挂起生成一个 uuid4；审批请求必须带同一个 id，**防误批上一轮** | `:1272` / `:1382` |

### 状态流转

```
execute_stage 进入
   │
   ├─ approved? ──────────────────────────────────────────────┐
   │   (指纹一致 AND jsa_approval.decision=="approve" AND 未否决)│
   │                                                            │
   │  否                                                        是
   │   │                                                        │
   │   ├─ same_application AND p1_phase=="jsa_review"?          │
   │   │     是 → 直接 return existing_result（别重复跑模型）    │
   │   │     否 → 跑 JSA（模型 1）                              │
   │   │          → p1_phase="jsa_review"                       │
   │   │          → 挂 pending=jsa_approval（uuid A）           │
   │   └────────────────────────────────────────────────────────┤
   │                                                            │
   └──────── 人工批准（confirm_and_continue）◄──────────────────┘
                 │  校验 approval_id == uuid A（:1367）
                 │  校验 JSA 可用（:1371，否则拒绝批准）
                 │  写 jsa_approval + p1_phase="permit_build"
                 ▼
            重进 execute_stage → approved 为真
                 → 复用 jsa_result（不重跑模型 1）
                 → 判定措施（模型 2）+ 出票
                 → p1_phase="permit_review"
                 → 挂 pending=permit_final_approval（uuid B）
                 ▼
            人工最终批准 → 阶段 completed → 进 P2
```

**为什么用指纹而不是"有没有批准过"**：申请内容一旦被编辑（哪怕改一个字），旧的 JSA 就不再对应本次作业，指纹失配 → `approved=False` → 系统**自动要求重跑 JSA 并重新审批**。这是"基本信息变了就必须重新分析"的落实点。

**两个 approval_id 为什么必须不同**：`jsa_approval` 的 uuid 与 `permit_final_approval` 的 uuid 各自独立生成。前端拿旧的预览页点确认，id 对不上就被 `main_agent.py:1367` 拦下（返回 `already_processed`），不会把上一轮的审批状态误当成这一轮的。

**否决的处理**：否决是终止决定，不像批准那样把阶段标记完成。当前阶段记为 `failed`，P1 调 `reset_permit_execution`，用户点"启动"由断点恢复重跑，**再走一遍完整审批**。

**`completed` 的含义**：`stage_result.completed=True` 只表示**本次业务计算完成**，不等于人工审批通过。只要还挂着 `pending_confirmation`，流程控制就先按"待审批"处理，不会推进到下一阶段。

## 机制二：知识库对齐与依据注入

"用知识库判定"**不靠提示词**——提示词只给一句口径，条文依据是**代码注入**的。三层配合：

| 层 | 在哪 | 职责 |
|---|---|---|
| 口径 | `P1_MEASURES_SYSTEM_PROMPT.md`「带有条文依据的，以条文为准，不要凭常识推翻」 | 告诉模型怎么用依据 |
| 注入 | `p1_permit_agent.py:419 _measures_user_message`（`:431` 拼 `〖依据·第N条〗`）+ `:470 _basis_excerpt` | 把条文原文拼进提示词 |
| 数据 | `data/knowledge/permit_articles.json`，经 `workflow/article_knowledge.py` 读取 | 条文与映射的来源 |

### 对齐（`align_measures`）

判定前，把**本次措施清单**与**知识库同类型措施表**按位置逐条对齐（`article_knowledge.py:align_measures`）——条件是：**同类型 + 条数一致 + 逐条归一化相似度 ≥ 0.85（进入比对前还要求长度比 ≥ 0.8）**，**允许文字差异**（不是逐字相等）。**任一条件不满足就返回 `None`**，判定退回 `mode="full"`——仍然调模型，但没有条文依据。

- `mode="knowledge"`：对齐成功；**配置了 `refs` 的条目**后附 `〖依据·第N条〗`（无映射的条目没有依据）。
- `mode="full"`：对不齐，无依据判定。**典型**情形是上传 DOCX（其自带清单与附录 1 通常不同），但**是否 knowledge 只取决于对齐是否成功，不取决于输入来源**——手动与 DOCX 都走同一个 `_decide_safety_measures:551`。

### 依据摘录（`_basis_excerpt`）

从条文里截 ≤320 字给模型，**原文优先、逐字返回**。

### 边界（重要）

- 是否 `mode="knowledge"` **取决于对齐是否成功，与输入来源无关**：手动与 DOCX 都走同一个 `_decide_safety_measures`，只是 DOCX 自带清单**通常**对不齐。
- 知识库重新生成后**自动换新、无需重启**：`load_knowledge()` 缓存键是 `(绝对路径, mtime_ns, size)`。
- 判定**只有一条路径**：全部措施连同依据送模型逐条判 √/×，**不存在"不经模型直接打勾"**。

### 知识库本身由什么构成

本 skill **不描述**知识库的内部结构（字段、条数、映射数量）——这些会随重新生成变化，抄进文档就会过期（曾出现文档写"111 条映射"、实际却是 130 条的情况）。需要时直接看这三个源：

- **结构与数据模型**（各字段含义、三层怎么切）: 直接读 `data/knowledge/permit_articles.json` 的顶层键（`articles` / `measures` / `measure_refs` / `stats` / `version`）或生成脚本 `tools/build_article_knowledge.py`
- **生成脚本**（从办法 `.doc` 解析、幂等、末尾带映射健康度自检）: `tools/build_article_knowledge.py`
- **数据本身**（要查条数看 JSON 里的 `stats`，不要记死数字）: `data/knowledge/permit_articles.json`

## 机制三：票面落位 mode

`permit_template.py` 的 `build_permit_rows` 按每个字段的 `mode` 决定值写到票面哪里。八种：

| mode | 语义 | 典型用途 |
|---|---|---|
| `fill` | 写入标签右侧紧邻的空格；右侧不可用时退到「表头行 + 值行」的正下方空格 | 多数单值栏（介质、规格…） |
| `below` | 值写在标签**正下方同列**。跨分组时右邻空格属于别组，必须显式指定 | 参数区「4 组表头 / 8 子列」 |
| `append` | 追加到标签**右侧格** | 值格固定不可用时 |
| `same` | 追加到**标签格自身** | 单格整行 |
| `para` | 只写标签**所在的那一段**（`_inject_into_paragraph`） | 「作业范围…：／签字：…」两段式格 |
| `check` | **真在票面自带的框里打勾**（□→☑） | 作业类别、是否编制方案、风险勾选 |
| `overwrite` | **清掉目标格已有文字再写** | 值格自带占位（「年 月 日 时 分」） |
| `inline` | 在「标签＋冒号」**之后**插入 | 一格挤两个标签 |

### `check` 的坑（最易错）

**框有两种形态**：`<w:t>` 里的字符「□」（U+25A1）与 **`<w:sym>` 符号控件**（无文字，`_cell_text` 看不见）。空框 `Wingdings 2 / 00A3` → 勾框 `0052`；`Symbol / F0D6` 是表头里的 √，**不是框别动**。

定位规则：先找标签格自身、再找右侧格、再找下一行；**取离选项"最近"的框，两侧等距取左**；选项与框常被拆进不同 run，须整格拼接后映射回具体 token；**无框退化 `same`**（别丢信息）。打勾不进 `<w:t>`，**测试断言必须查 XML**。

### 字段配置的两条铁律

**① `common_fields` 只放 10 个类型都真印着该栏的字段。** 否则无关作业表单凭空多出填不进票面的填空。只在某一张票有的栏 → 放该类型 `extra_fields`。

**② 标题存两处**（`permit_templates.json` 的 `title` + docx 标题段落）。模板重导出后必须清「推荐样式」；填值只能改写单元格**已有段落**，**不得新增段落**（会溢出到第二页）。

## 附：主要失败与降级

| 情形 | 现有行为 |
|---|---|
| JSA 分析失败 | `source="unavailable"`，不产出内容；**不允许批准出票**（`main_agent.py:1371`） |
| JSA 报告渲染失败 | 分析结果仍可用于审批，但 `jsa_report.docx` 可能不可下载（渲染失败只记 warning） |
| 知识库缺失 / 对齐失败 | 退回 `mode="full"` 无依据判定（**仍调模型**，不是失败、也不是全部留空） |
| 措施清单为空 | `source="no-measures"`、`items=[]`，不写勾 |
| 措施判定失败 | `source="unavailable"`、`items=[]`，**不写任何勾**，退回人工 |
| 重复 / 过期审批请求 | 校验等待状态与 `approval_id`，返回 `already_processed`，不重复推进 |
| 出票失败后恢复 | 同申请且批准记录仍有效 → 复用 JSA，**重新判定措施并出票**；申请变化或被否决 → 重新分析 + 审批 |
