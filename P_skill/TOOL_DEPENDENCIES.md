# 工具与运行依赖

Main：bind_work_permit、query_pipeline、configure_schedule、list_time_windows、retry_time_window、retry_failed、close_time_monitoring、reopen_monitoring，共8个业务工具。平台任务提交/等待/心跳复用已有能力。

P6：read_current_window、read_current_evidence、save_current_description、fail_current_window，共4个工具。

P7保留6个顺序研判工具；P8/P9合并Agent保留8个处置/复核工具，并绑定模拟飞书同步接口。记录引用在P7/P8内部工具回执中仍存在；Main的P6时间调度不依赖这些记录ID。

完整Skill ZIP自带标准库业务代码；通用Python容器执行，SQLite资源挂载为/business。播放器提供只读/v4/data/windows和/v2/data/window。平台代码无需为本次窗口调度增加功能。P10不在本次验收范围。

当前契约：单作业票、时间调度（v5）。Main 管票与时间进度，P6 每次读取一个原始窗口并原子保存一份 P6_P7 描述；P7 顺序研判；P8/P9 维护同一卡片并复核材料。播放器只作为原始关联事件数据源，新流程不调用播放器任务领取/完成接口。窗口 ID、租约、精确时间和幂等由工具内部维护。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
