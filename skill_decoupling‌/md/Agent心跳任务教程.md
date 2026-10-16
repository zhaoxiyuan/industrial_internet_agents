# Agent 持久心跳任务（v5）

心跳由平台持久计划触发。每轮是新根任务、独立对话，不继承上轮聊天；业务进度保存在数据库。submit_task仍只提交一次任务。

## 配置

在“Agent与绑定”的心跳区域设置周期（1–86400秒）、描述、input、首次触发方式、单轮timeout_seconds和防重入范围。Main/P6通常input={}，P6窗口N由Main configure_schedule配置，与心跳周期H无关。

```json
{
  "agent_id": "当前Main真实ID",
  "request_id": "当前作业-main-heartbeat-v1",
  "enabled": true,
  "interval_seconds": 60,
  "description": "查询当前作业与同票任务，仅做本轮有限调度，提交后结束；不等待子任务。",
  "input": {},
  "timeout_seconds": 120,
  "first_run": "after_interval",
  "overlap_policy": "skip",
  "overlap_scope": "self",
  "overlap_key": ""
}
```

self只检查目标Agent自身所有在途执行（包括手工提交），子Agent仍在运行不会阻止下一轮Main。上一轮Main未结束则跳过本次，不排队补发。chain才按计划及overlap_key检查整条协作链。执行器实际尚未退出时仍视为占用，不能只看终态标签。

## 权限与生命周期

管理员可管理全部计划。Agent只管理自己创建且仍有schedule权限的计划；读取成果另需result。Main可被授予自身schedule/status/result/overview，仍禁止自身submit递归。普通任务查看允许当前协作链，以及同一作业下自己此前提交且获授权的任务；不开放其他作业或私有聊天。

创建计划固定当前作业范围。人工关闭后自动停止该票计划，不能resume到新票；新票创建新计划。更新计划必须携带schedule_id、当前version和新的request_id，相同幂等键不同内容拒绝。

暂停/停止只影响未来触发，不取消已提交任务；人工关闭另执行在途取消及旧票写入封锁。恢复普通暂停计划重新校验配置与授权，不补历史周期。离线错过的触发计数保留。

## Main 每轮结束条件

query_pipeline、list_tasks和agent_status获取持久事实；按业务待办及平台排队/运行占用有限补位，然后结束。心跳不wait_tasks、不追踪整票闭环。无工作立即结束，失败按明确原因有限重试；P6完成的窗口不重复消费。单次验收任务仍可wait_tasks。

## 超时

timeout_seconds不超过Agent配置上限。执行超时记录TASK_TIMEOUT并撤销旧run写入资格，平台回收未提交领取；P6/P7/P8领取前已登记补偿意图，回执丢失也能按run恢复。已提交结果不回退、外部发送不假定撤销；租约过期保留作失联兜底。

## 管理接口与核对

GET/POST /api/schedules；GET/PATCH /api/schedules/{id}；POST /api/schedules/{id}/{pause|resume|stop}。Agent对应set_heartbeat_schedule/get_heartbeat_schedule/control_heartbeat_schedule。查看fired_count、skipped_count、最近任务及错误；skip不丢弃业务数据，积压应查询业务工具。

真实验收记录见P_skill/V5_LIVE_ACCEPTANCE.md；可使用P_skill/scripts/audit_live_workflow_v5.py只读复核，无隔离平台或伪造任务。
