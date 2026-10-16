---
name: p7-risk-assessment
description: 依据监测事实研判风险与处置建议，将连续异常聚合为事件，持续跟踪变化，确认恢复后形成最终报告。
metadata:
  context-scope: workflow
---

协作工具用agent_name从名称枚举选择实际执行者，不填Agent ID或创建者名称。P6心跳选P6，Main心跳选Main；人员P7/P8/P11不是Agent名称，任务及计划仍用回执ID。
循环处理描述，不能一份就退出：
1. 首先claim_description({})；no_work/blocked据实结束。本轮后台只绑定一份领取，不传claim/id/token。
2. 仅为required_assessment_keys中的每个key填一个assessment；列表空必须assessments=[]，不要给正常/未知项填风险等级。每个assessment（risk_level 0–5、basis、advice）；按既有规则和证据定级，不编造标准。仅normal/unknown则assessments=[]，unknown不是正常。
3. complete_assessment只传assessments，自动提交本轮当前领取。closed_events为空直接回步骤1，不读事件；非空只对返回ID逐项read_event→report_closed_event，再回步骤1。record_id不是event_id。
4. 真实错误、可信关闭或预算不足才停止；先提交当前项，不领取无法完成的新项；必要时renew_description_lease只传lease_seconds，不能复活过期领取；失败fail_description只传reason。报告实际提交数量、连续范围和阻塞。
工具负责事件聚合/恢复判定/通知入队。缺测、离场或数据结束不能证明风险恢复；不发飞书、不模拟材料或人工批准。

