> 2026-10-15 当前上游已改为时间窗口单票流程：Main按时间调度，P6一次写入一份描述；本文的下游/界面机制继续适用。旧源领取及候选票补办流程不再适用；本轮真实验收以 LIVE_PLATFORM_ACCEPTANCE.md 为准。P10本轮不测试。


P10 不重新开发 Skill，平台导入原 [work-ticket-report](../../a_mac_skill/P10/SKILL.md)。执行桥位于 `runtime/industrial_skills/p10_bridge.py`，仅提供数据快照范围、原运行环境和异步 submit/status HTTP 入口。原四个查询工具、统计、模板和渲染服务保持原代码。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](../CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
