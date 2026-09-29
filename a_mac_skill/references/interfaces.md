# 现有外部接口

P6 直接地址下：POST /api/monitor/start，POST /api/monitor/stop，GET /api/monitor/status?job_id=...，GET /api/agent/events?job_id=...。
P7 共用 P6 服务：GET /api/a6/assessments?job_id=...。
P8P9：GET /api/closure/jobs/{job_id}。

若经过 Nginx，P6 地址包含 /admin/p6，P8P9 前缀须按现场路由配置。开始监测请求字段请对照 agents/p6_monitor_agent.py 的 MonitorStartRequest；play_delay_sec=5 表示后端延迟播放。scripts/client.py 不自动猜测工单、数据源或服务地址。

原始 JSON 文件读取接口尚需边界校验，不作为通用任意文件读取能力开放。查询仅使用业务接口。
