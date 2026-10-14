# 红线与坑（改 P1 前必读）

## 一、顺序是代码强制的，不是约定

`作业申请基本信息 → JSA 分析 → JSA 批准 → 措施判定/出票 → 最终审批`。这套顺序由三道代码强制的关卡保证，**改 P1 时不要绕过它们**：

| 闸 | 位置 | 拦什么 |
|---|---|---|
| 申请指纹 | `p1_permit_agent.py:1238-1240` | `fingerprint = sha256(申请全文)`。申请被改动 → 指纹失配 → 旧的 JSA 批准作废，**必须重跑 JSA 并重新审批** |
| JSA 批准事实 | `:1240` | `approved = 指纹一致 AND jsa_approval.decision=="approve" AND 未被否决` |
| 首次不建票面 | `:1276-1279` | 首轮只写 `permit.json` 的 `{application, jsa_result, p1_phase}`，注释明写"**不能提前构建票面**" |

**主工作流入口**的出票口只有一个：`execute_stage:1251` 的 `builder(...)`，它在 `if approved:` 里。无论手动填报还是上传 DOCX，都走这一个门。但这**不等于"所有入口都受保障"**：下载接口（`web/api/workflow.py:341`）只在 `p1_phase == "jsa_review"` 时返回 409，**没有正向校验"已批准"**——`permit.json` 缺失或 `p1_phase` 字段不存在时不会拦截；CLI 的 `permit generate-draft` 走 mock 工具链（`run_permit_agent`）也能产出草稿、不经两次审批。

上游再加一道保险（`main_agent.py:1371-1375`）：JSA 分析失败时（`source != "ai"` 或无 steps），**连"批准"按钮都批不动**，直接返回错误。这是《办法》八不准第一条落到代码里的位置。

## 二、前端改动的跨仓库红线

`web/app.js` 里的 `fetch('/api/…')` 被 console 侧**精确计数校验**（fail-closed）：

- console（`edge-console-zsy/lib/job-monitor-proxy.js`）里 `EXPECTED_API_FETCHES` **当前 = 26** = `web/app.js` 中 `fetch('/api/…')` 的条数；两条 WS 模板字面量各恰好 1 次。
- **新增/删除一个 fetch，必须同步改**：console 侧常量 + 该仓文档 + 本仓 `tests/test_jsa_ui.js` 断言。
- 改完要**重启 console**（dev 重启；production `npm run build`；Docker 重建镜像），否则「作业监测」因计数不符抛 `SCRIPT_ADAPTATION_MISMATCH`——报错指向「接入配置」，**极易误判为网络问题**。
- 校验方式：直接 `require` 那支 `adaptScript` 跑当前 app.js，比看源码可靠。

另外三条前端硬约束：**新增的请求形式必须能被 console 适配**（`fetch('/api/…')` 会被改写为 `/jobmon/api/…`，适配后不能残留未处理的绝对请求，否则计数不符）；新前端资源必须**内嵌进 app.js**（白名单只放行 `/index.html`、`/app.js`、`/favicon.ico`、`/api/*`、`/data/input/*.json`）；`index.html` 的 script 标签**不要加 `?v=` 版本参数**。

## 三、改代码的方式

**改完必须回读校验。** IDE 可能回写吞掉外部改动——写完/改完用 Read 或 Python 落盘后立即回读，确认内容真的是新的。

**模板填值不得新增段落。** 只能改写单元格**已有段落**，新增段落会溢出到第二页。（见 mechanisms.md 字段铁律）

## 四、测试

**单测必须打桩模型。** JSA 与措施判定都会**真调模型**（一次几十秒），`test_execution_recovery` 不打桩一次跑 48 秒。凡测试路径可能触达模型，**要同时隔离两处**：打桩 `_decide_safety_measures` **和** `_build_jsa_result`（或更内层的 `generate_jsa`）——**只打桩措施判定不能保证没有真实模型调用**（首跑 `execute_stage` 会先跑 JSA）。

**测试环境与命令**（conda env `proj01`）：

```bash
/Users/dyjyyds/miniconda3/envs/proj01/bin/python -m unittest \
  tests.test_permit_template tests.test_measure_judgement tests.test_docx_permit \
  tests.test_article_knowledge tests.test_workflow_history
```

测试可**逐模块列出**；`python -m unittest discover -s tests` **也能用**（实测可跑通，`tests/` 缺 `__init__.py` 并不妨碍 discover）。基线：P1 相关回归当前全绿；全量约 60 秒（`test_permit_template` 26s 是大头，docx 渲染开销）。核心测试模块：`test_jsa_analyze`（JSA 上下文/解析/降级）、`test_jsa_approval`（两次审批/旧请求/恢复）、`test_jsa_pipeline`（JSA→判定→票面数据联系）、`test_jsa_report`（报告渲染）、`test_permit_template`（票面落位）、`test_jsa_ui.js`（前端审批/预览/下载/接入适配）。

**不要用 `timeout` 命令**——这台机器（macOS）**没有 `timeout`**。写成 `timeout 300 python ... | grep` 会 command not found，看到"空输出"会误以为测试卡住，实际根本没跑。

`tests/test_p8.py` / `test_p8_service_manager.py` 的失败是**既有环境问题**（缺 fixture、未装 pytest），与 P1 改动无关，别去修。

## 五、模型调用与思考模式（易错）

> 下列数值为 2026-10-13 实测，会随服务端变化，**不作为长期保证**；需要时重新测。

模型 `deepseek-v4-flash`（`api.deepseek.com`），**服务端默认 `thinking: enabled` 且 `reasoning_effort: high`**；思考 token 占输出 **90% 以上**（真实判定单次 3700~6300 token）。

**参数必须走 `extra_body`**——`reasoning_effort` 作为顶层参数直传**会被 LangChain 丢弃**：

```python
extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "low"}   # 降档
extra_body={"thinking": {"type": "disabled"}}                              # 关闭
```

**档位映射**：`minimal→low`、`low→low`、`medium→high`、`high→high`、`xhigh→high`、`max/ultra→max`——即 `medium` 等于 `high`，`minimal` 与 `low` 同档。**真实任务的思考量由任务难度主导，effort 档位几乎不构成约束**；唯一确定有效的是"开关"（disabled 快 30~100 倍，但会少勾）。

**当前配置**：措施判定 `MEASURES_REASONING_EFFORT="low"`（`p1_permit_agent.py`），实测平均 27.9 秒（默认 high 为 53.7 秒）；**JSA 仍用服务默认**（未做对照实验）。

## 六、判定结果不可复现（已知隐患）

> 下列数值为 2026-10-13 实测。

思考模式下 `temperature` 失效，**同一输入、同一档位，判 √ 数会在 11~13 间波动**。措施判定是要签字确认的环节，前后两次跑出不同的勾，业务上需要交代——这是设计层面未处理的问题，**不是 bug，改参数解决不了**。

实测：low 档连跑 5 次，15 条里 14 条完全一致，**仅个别条目摇摆**；摇摆条目的共同点是"**判定所需的适用条件不在申请输入里**"（如"是否用气焊"决定气瓶措施是否适用）。所以想让它更确定，方向是**补采集判定条件类字段**或**把条文的适用条件结构化**，而不是调模型参数。

## 七、环境与目录

- 跑测试与跑服务**一律用** `/Users/dyjyyds/miniconda3/envs/proj01/bin/python`。
- 工作流三个 WebSocket 端口 **8080 / 8081 / 8082 硬编码**。
- **`data/jobs_trash/` 是回收站目录**（删除工单是"移入"而非物理删除），**别当垃圾清理**。
- 删除工单接口 `/api/workflow/delete`：非法 id 400、不存在 404、**执行中 409 拒绝**。

## 八、一处容易改错的死代码

`agents/main_agent.py:85` 有一个 `def execute_p1(job_id, resume)`，注释还写着"保留 HITL 逻辑"，但 **`:759` 的 `execute_p1 = p1_execute_stage` 把它覆盖掉了**——模块加载完成后，`main_agent.execute_p1` 指向的是 `p1_permit_agent.execute_stage`（真执行器），`:85` 那个函数**永远不会被调用**。

后果：`:85` 里那条"P1 Agent 工具链"分支在**网页工作流**里走不到（因为名字被覆盖）。但**不能说它整体不可达**——**CLI 能进入**：`agents/cli/__init__.py:16` 导入 `run_permit_agent`，`permit submit / analyze-jsa / generate-draft / check` 都调它 → `p1_permit_agent.py:1181 create_permit_agent()` → 加载 `P1_PERMIT_SYSTEM_PROMPT.md`。改 P1 时手滑改了 `:85`，**网页**行为不会有变化——白改，还容易误判成"改了没生效"。

相关事实：`P1_PERMIT_SYSTEM_PROMPT.md` 在这条（网页主路径走不到的）工具链里被加载；**网页主路径真正用的是** `JSA_SYSTEM_PROMPT.md` 与 `P1_MEASURES_SYSTEM_PROMPT.md`。这些函数仍被 `tests/test_execution_recovery.py` 直接调用做单测，**不能简单删除**；要清理需连测试一并处理，属独立的"删死代码"任务。
