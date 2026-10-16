# 接入契约

播放器GET /v4/data/windows：window_seconds=1..300，start_time/end_time可选，after为时间分页边界，limit=1..100。按原始秒关系记录组合连续窗口，不创建processing_tasks。运行中的不足N秒尾窗等待采样，结束/断档允许尾窗。

GET /v2/data/window?from_time=...&to_time=... 在一致数据库快照内返回PPE、传感器、定位、人员、设备、区域、证据和完整性。业务层按作业范围投影，原始播放器数据不包含作业票归属。

P6任务input={}或{start_time:列表中的窗口开始时间}。Main通过configure_schedule管理窗口大小。P7 input={}；P8/P9 input={mode:intake}或{mode:review,report_id:真实处置引用}。平台任务ID仅用于已有submit/wait协作接口，原始窗口ID不再在Agent之间传递。

当前契约：单作业票、时间调度（v5）。Main 管票与时间进度，P6 每次读取一个原始窗口并原子保存一份 P6_P7 描述；P7 顺序研判；P8/P9 维护同一卡片并复核材料。播放器只作为原始关联事件数据源，新流程不调用播放器任务领取/完成接口。窗口 ID、租约、精确时间和幂等由工具内部维护。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
