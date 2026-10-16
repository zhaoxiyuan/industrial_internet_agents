> 2026-10-15 当前上游已改为时间窗口单票流程：Main按时间调度，P6一次写入一份描述；本文的下游/界面机制继续适用。旧源领取及候选票补办流程不再适用；本轮真实验收以 LIVE_PLATFORM_ACCEPTANCE.md 为准。P10本轮不测试。


# P7/P8 持续风险通知契约

首次风险出现即可通知并推送，后续变化更新同一卡片；最终报告仍只有事件closed后生成。P7风险状态与P8/P9人工处置状态独立。P8调用时机始终由Main决定。

P7 complete_assessment与状态保存同事务写event_notification，按event_id唯一，保存event_version。open、uncertain、closing、closed均登记；缺测/时间断档也通知。积压变化合并为最新版本，processing时更新快照但保留租约，failed保持失败待Main retry_failed，不因新观察绕过重试上限。P7不写card_intent、不启动P8、不发飞书。

Main query_pipeline公开通知队列和版本，pending时安排P8P9 mode=intake。首次告警及时调度，持续更新频率由Main/计划配置控制。业务队列登记不是后台自动调度指令。

P8 claim_risk_report保留名称兼容，优先领取event_notification（claim.kind相应变化），然后兼容旧report。initialize_disposition读取最新P7 event，稳定report_id=digest(job_id,event_id,'final')，创建或更新同一处置并写card_intent。保留accepted_by、materials、opinion、human_review、delivery与status；人工升降级使用risk_level_override保留，P7等级另存在event_snapshot。租约幂等调用不重新创建处置或卡片。失败使用原fail_risk_report，Main retry_failed支持event_notification。

开放事件的report记录是is_final=false的实时视图，仅供原平台卡片/复核接口兼容，不是最终归档。P7 report_closed_event只接受closed并生成is_final=true不可变归档；使用同一report_id，不再创建第二张卡。老报告没有is_final字段按最终报告兼容。P8已接收时最终报告不重新入库为第二次初始处置；新快照通过通知继续更新。

平台render优先使用P8明确保存的event_snapshot，并在原CardKit表单前展示风险状态及处置状态；risk卡片唯一键、CardKit create/update、message uuid、sequence、签名回调、上传/审核表单约定保持原代码。仅P8同步或人工动作产生card_intent，平台可以刷新其已授权意图；P7新版本本身不触发卡片更新。投递成功保存last_synced_event_version；unknown不盲目再次首发。

持续风险：风险持续；缺测：观测中断，尚未确认结束；正常但未够阈值：恢复确认中；closed：风险现象已结束。处置closed不代替正常证据，风险closed也不自动关闭处置。全作业仍需全部最终报告、通知同步、监测结束、处置及人工确认。

共享SQLite、完整ZIP与通用Python执行器保持现有方案，P10本体复用，真实飞书跳过。完整包重新生成发布；同一批共用模块哈希一致。


## 当前验收

旧验收业务历史已备份并清理。当前单票时间窗口 v4 的实际任务、3 个事件及同卡更新结果统一见 [真实验收](LIVE_PLATFORM_ACCEPTANCE.md)。本轮不调用 P10，不发送真实飞书。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
