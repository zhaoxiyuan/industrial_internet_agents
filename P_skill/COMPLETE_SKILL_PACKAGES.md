# 完整Skill包交付

源实现为runtime/industrial_skills；scripts/build_skills.py生成main/P6/P7/P8的scripts/_business、tools.json和dist ZIP。修改源代码后重新生成，不能单独编辑生成副本。references/bundle.json记录p6-p7-timeflow-v4及各模块SHA256。

导入既有平台并更新既有Agent与绑定，不重复创建Agent。通用p-skill-python-executor只需Python，业务代码来自只读/skills，业务数据来自/business。旧P6工具不出现在新目录，旧实现也不随包携带。

当前契约：单作业票、时间调度（v5）。Main 管票与时间进度，P6 每次读取一个原始窗口并原子保存一份 P6_P7 描述；P7 顺序研判；P8/P9 维护同一卡片并复核材料。播放器只作为原始关联事件数据源，新流程不调用播放器任务领取/完成接口。窗口 ID、租约、精确时间和幂等由工具内部维护。


当前执行契约已更新为v5：完整窗口观测、可信人工决定关闭、超时撤销并回收领取、Main独立心跳仅对自身防重入。详见[当前作业与心跳契约](CURRENT_WORKFLOW.md)。文中带日期的验收记录保留当时事实，不作为现行工具约束。
