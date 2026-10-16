# Main时间调度

Main负责单票登记、时间列表、按次/心跳触发、并发数量、失败恢复和P7/P8交接。模型只需看开始/结束时间、状态、连续进度。业务实现是timeflow.py，目录由build_skills.py生成。

已移除query_acquisition、set_window_seconds、retry_source_window、retry_source_task、close_source、close_monitoring等旧模型入口及旧实现。当前9个工具详见tools.json；本次不暴露P10导出入口。

当前契约：单作业票、时间调度（v5）。Main 管票与时间进度，P6 每次读取一个原始窗口并原子保存一份 P6_P7 描述；P7 顺序研判；P8/P9 维护同一卡片并复核材料。播放器只作为原始关联事件数据源，新流程不调用播放器任务领取/完成接口。窗口 ID、租约、精确时间和幂等由工具内部维护。

configure_participants 修改当前单票的接取/审核范围，支持指定多人或 registered（真实绑定飞书连接的全部登记用户）。该工具和 bind_work_permit 的目录上下文由平台从 P8/P9 绑定注入；回调按最新目录校验。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](../CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
