# Agent 协作拓扑

更新：2026-10-13。入口为平台导航“协作拓扑”，前后端已经实现。它展示平台实际注册的 Agent 与有方向的协作授权，不按 P 阶段名称硬编码关系。

## 视图和交互

- 节点显示名称、启用状态、当前执行状态、绑定 Skill 数与运行/等待任务数。支持键盘 Tab/Enter 选择。
- 实线箭头表示含 submit 权限的授权；点线表示仅查询或管理权限；灰色虚线表示已停用的授权。在途任务用绿色流动线突出，停用授权仍保留时可在详情核对其在途数量。线是授权关系，不代表每次都已经调用。
- 点击节点，侧栏展示职责、模型配置状态、执行位置、并发容量、心跳计划数量、Skill、工具绑定、上下游权限和最近更新的 5 条任务。可以跳转配置、授权表单或任务详情。
- 搜索名称/职责/ID，显示或隐藏停用授权，只看选中节点的相邻关系；缩放、适应画布、画布滚动。搜索不删除配置。
- 页面每 4 秒更新拓扑；隐藏页面不轮询，已有执行中的任务沿用平台轮询。没有变化时保留节点、焦点和详情。失败时保留上次快照并显示刷新错误。
- 小屏将详情移至画布下方，减少节点因左右分栏而过度缩小。支持减少动态效果的系统设置。

## 状态含义

“已启用”来自 Agent 定义 enabled；“已激活”来自任务事实，二者不是同一个字段。

| 显示 | 判定 |
|---|---|
| 运行中 | 存在 running 任务 |
| 执行收尾中 | 任务已进入终态但 executor_active 仍为真 |
| 等待协作 | 存在 waiting 任务 |
| 排队中 | 存在 queued 任务 |
| 已停用 | 无以上在途状态，enabled=false |
| 待配置 | 无以上状态，模型、地址或密钥未配置完整 |
| 空闲 | 已启用且模型配置完整，无在途任务 |

状态按表中先后优先展示，同时保留各类计数。已激活统计运行、收尾、等待协作；排队另列。停用不会伪造任务取消，仍运行时显示“运行中 / 已停用”。模型已配置不等于健康探测通过。任务失败保留在近期任务中，不把历史失败解释为当前 Agent 永久故障。

## 后端接口

`GET /api/agent-topology` 返回 generated_at、summary、nodes、edges；`GET /api/overview` 同时返回 agent_topology。接口沿用现有管理页的 Host/Origin 约束，不属于公网回调路由。

来源：agents、agent_grants、bindings、skills、schedules 与持久 tasks。只从当前授权生成边，不从历史任务推测新权限；未绑定协作的独立 Agent 也显示。使用全部任务计算在途和累计计数，不受统一任务表最近 100 条的裁剪影响；详情只返回 5 条摘要。

白名单投影不返回模型密钥、secret_ref、base_url、任务输入/结果或检查点。接口不调用模型、不探测外部服务、不派单、不修改数据库。启停按钮明确调用原有 Agent PATCH 接口，权限管理仍进入原授权表单。

前端通过强连通分量处理循环/双向授权，再分层布置节点；SVG 画线与 HTML 按钮不依赖外部 CDN。用户可控文本全部转义。

## 文件与验证

- 后端：[topology.py](../platform_core/topology.py)，接入 service.py 与 collaboration_api.py。
- 前端：[topology.js](../web/topology.js)、[topology.css](../web/topology.css)，接入 index.html、app.js；app.py 显式允许两个新静态文件。
- 测试：[test_topology.py](../tests/test_topology.py)：授权来源和撤销、停用/在途状态、超过100条任务、隐私投影、空图/环/心跳、真实HTTP静态资源和API。
- 浏览器夹具：[preview_topology.py](../tests/preview_topology.py)：临时数据库、明确演示名称、不运行调度器、不调用模型，覆盖运行/等待/排队/空闲/停用及文本转义。

```powershell
# 在平台目录使用项目虚拟环境
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_topology.py -v
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_collaboration.py -q
./.venv/Scripts/python.exe tests/preview_topology.py --port 18771
```

本地拓扑 5 项、原协作 18 项通过。浏览器实测节点详情、搜索空态、相邻筛选、缩放、启停后在途状态保留以及脚本文本转义。Mac 更新仍按本地修改→代码包哈希核对→Docker构建→测试→启动平台执行；不在 Mac 单独编辑代码。演示数据只验证界面，真实平台节点和任务由 API 返回。

## 删除 Agent

“Agent 与绑定”列表及拓扑节点详情均有“删除 Agent”。先通过 `GET /api/agents/<id>/deletion` 获取影响范围，再由用户确认；`DELETE /api/agents/<id>` 在短事务中重新校验。删除定义、专属模型密钥、工具绑定及入站/出站协作授权，保留历史任务、会话、成果、Skill和外部服务。历史 Agent 名称从只含名称和删除摘要的 deleted_agents 记录展示，不保留密钥或完整配置，也不提供界面恢复。

有 queued/running/waiting 任务、终态执行器仍占用、未确认退出的外部执行、进行中的模型测试、未停止的心跳计划、飞书入站默认 Agent 引用或风险卡片范围引用时，后端拒绝删除并说明处理方式。停用 Agent 或暂停计划不能绕过这些检查。实际删除与派单、绑定/授权修改共享进程锁，并在 SQLite 写事务中重查，不凭页面先前状态直接删除。

初始化脚本按已存在的唯一 Agent 名称、HTTP定义和绑定范围复用记录；有歧义时停止而不创建更多重复项。`P_skill/scripts/deduplicate_validation.py` 只依据 setup.json 与业务数据库一致的角色ID保留当前工作流，只清理没有历史任务的同名重复项；执行前备份私有平台数据库。专门的删除回归覆盖级联关系、历史保留、忙碌门禁、业务引用与HTTP接口。
