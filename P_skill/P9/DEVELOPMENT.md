> 2026-10-15 当前上游已改为时间窗口单票流程：Main按时间调度，P6一次写入一份描述；本文的下游/界面机制继续适用。旧源领取及候选票补办流程不再适用；本轮真实验收以 LIVE_PLATFORM_ACCEPTANCE.md 为准。P10本轮不测试。


# P9 整改复核开发契约

P9与P8共用一个`p8p9-disposition` Agent和工具包，不另建SKILL.md或重复注册。业务提示词在[P8/SKILL.md](../P8/SKILL.md)，工程调用规则在[P8/tools.json](../P8/tools.json)；详细原文见[接口汇总](../SKILL提示词与工具接口说明.md)。

## 当前实际流程

Main提交`input={"mode":"review","report_id":"真实处置ID"}`。P8/P9先`read_review_snapshot(report_id)`冻结当前材料，再对每份材料`read_material(snapshot_id,material_id)`，最后`save_review_opinion(snapshot_id,decision,reason,limitations)`，并使用平台`feishu_sync_risk_card(report_id)`同步原卡片。没有额外claim_review/validate_material_manifest注册工具；校验已在实际工具内部完成。

快照绑定同一Agent和本次run_id。处置必须处于materials_in_audit，提交时材料版本必须未变，全部材料必须已读。相同快照和意见幂等。当前读取器仅接受≤32000字节、UTF-8可解码的text/plain；任一材料不可读时只允许unknown，外部视觉结果目前不能替代工具读取标记。新版本材料必须新建快照重审。

意见decision取sufficient/insufficient/unknown，保存后waiting_human_review，不代替人工批准。人工接取、提交、退接、升降级及终审由平台签名回调与受控业务命令校验角色、版本、幂等；原表单和飞书协议保持既有实现。

## 三种结束

| 对象 | 结束依据 |
|---|---|
| P7风险现象 | 连续正常证据达到恢复时长，完整覆盖；缺测不算恢复 |
| 单事件整改处置 | 授权人工终审通过，模型意见只是参考 |
| 整个作业 | 明确监测截止且处理完整、所有风险最终报告及通知完成、卡片投递、处置闭环、人工确认 |

`verify_job_closable`仅返回closable/blockers，不执行关闭。风险仍开放时可以通知和处置，处置关闭不会反向关闭风险。P10阶段或最终报告由Main安排，不由P9自行调度。存储、字段和错误详见[持续通知](../LIVE_EVENT_NOTIFICATIONS.md)及[提示词与工具分工](../PROMPT_TOOL_SEPARATION.md)。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](../CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
