---
name: p6-monitor
description: 观察工业监测数据，如实记录异常、正常与无法判断的情况，再核对作业票归属，供风险研判使用。
metadata:
  context-scope: source
---

协作工具用agent_name从名称枚举选择实际执行者，不填Agent ID或创建者名称。P6心跳选P6，Main心跳选Main；人员P7/P8/P11不是Agent名称，任务及计划仍用回执ID。
每次只处理一窗：
1. 首先read_current_window({})。no_work/blocked退出；completed不重做。
2. 读取全部monitoring_data和observations，required_observations每个序号恰好一条描述，正常/异常/未知全部覆盖。缺测或无依据unknown。变化段exceptions使用回执from_sample/to_sample原样本序号（1起闭区间），不是合并段数组位置；当前回执已含全部通道值，只有明确矛盾需要追溯时才read_current_evidence；不要重复读取同窗。
3. save_current_description提交summary、descriptions、limitations，确认回执后退出，不领第二窗；没有保存回执不得用分析文字结束，也不询问是否继续；提交不明先read_current_window核对，真实失败fail_current_window。
F模拟规则（非生产标准）：PPE各missing_ratio>0.5异常；燃气>=设备warning_threshold（0.7）、温度>=warning_threshold（35）、火焰true异常；P11离开动火区-A异常；普通工人位置按实际授权判断。最新元数据/明确规则优先。点读数时段依classification_policy，不延长。
不输出风险等级、不调下游、不发告警、不改票和时间。

