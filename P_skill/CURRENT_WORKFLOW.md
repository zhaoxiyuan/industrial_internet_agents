# 当前作业与心跳契约（v5）

P6 的 read_current_window 返回作业范围内完整 monitoring_data（PPE秒统计、传感器、位置及元数据）和全部观测通道；未配置风险规则的通道也返回，规则仅作参考。read_current_evidence 支持 source=cv 分页读取同窗原始逐帧证据。位置离开授权区域也保留供研判。descriptions 必须覆盖全部观察序号，每项包含 observation/state/explanation，可用 exceptions 的 from_sample/to_sample（从1起闭区间）表达变化。逐秒数据不预先合并，P6按窗聚合，P7按事件聚合；未知证据不能声明正常。

人工关闭入口：POST /api/connections/{connection_id}/confirm-job-closed，参数 job_id（建议显式指定）、actor、reason、mode=human_override。入口只供可信管理操作，未向模型注册。核验已登记且有审核权的人工身份后，无论闭环检查有多少阻塞，立即关闭作业；未解决风险保留，阻塞记录在 unresolved_at_close。关闭、失效领取及释放当前票在同一业务事务；平台停止该票计划、取消在途任务，迟到写入被拒绝。mode=normal保留常规闭环检查。关闭后新票必须重新绑定，新心跳不能沿用旧票计划。

平台 submit_task 与 set_heartbeat_schedule 可传 timeout_seconds，不超过Agent上限。超时任务进入 failed/TASK_TIMEOUT，先持久化运行撤销标记，再由平台回收领取；P6/P7/P8领取前均登记失败回收意图，返回丢失也能恢复。已提交业务结果不回退，旧执行不能在重试后写入。租约到期仍作为平台不可用时的兜底。

心跳每轮创建独立根任务和独立对话。overlap_scope=self默认只检查目标Agent本身（包括手工任务），上一轮未结束则skip，不补排；子Agent在途不会阻塞下一轮Main。chain选项才检查整条协作链。每个计划固定创建时作业范围，关闭后停止。Main每轮查询业务状态与同票此前自己提交的任务，有限补位后立即结束，不等待全部子任务或全作业闭环。单次验收任务仍可wait_tasks。

平台注册的真实飞书连接为P8P9；旧P-skill-MOCK已移除。模型提示词不得宣称真实发送为模拟。F演练阈值在P6 Skill说明中，不作为生产规范。

真实结果见[V5真实平台验收](V5_LIVE_ACCEPTANCE.md)。人工关闭后旧票号禁止重新绑定；使用新票重新登记。


## 本轮提示词调度修订（仅提示词，不改变工具）

初始P6数据窗口30秒、平台并发3、业务max_parallel=3；Main管理P6最多三条30秒心跳通道，各自chain防重入且overlap_key分别为票号+p6-slot-1/2/3。单通道未退出只跳过该通道，其余通道仍可运行；独立P6原子领取不同窗口。不能给P6配置self后期待并发3，也不能给三通道共用overlap_key。Main仍self、180秒、超时300秒，独立对话，不等待子任务。

Main通过时间列表和各计划recent_runs判断并发合计吞吐、延迟与积压，可调整P6间隔/启用通道数（最多3）；窗口领取后尺寸锁定。P7每次调用连续按序领取、提交多份描述，追至当前最新可用数据或遇阻塞/接近预算，不再限制一次调用一份。正常/未知描述也必须推进，不跳过前序。P7无独立心跳，由Main有限派发；事件开放即可通知P8，不等恢复。

已移除的capacity/max_inflight试验代码没有部署；使用现有self/chain与overlap_key。工具和后端保持不变。本轮不自动运行测试；新一小时起点须由用户明确提供，不复用上轮16:02时间。
