# a/ — P6-P9 独立子项目

这是从工业互联网边缘智能作业监测系统 (`../`) 剥离出的 **P6-P9 子集**,作为相对独立的项目工程,内部 import 完全自包含。

## 包含内容

| 子目录 | 角色 |
|---|---|
| `agents/` | P6 监测、P7 研判、P8 处置、P9 审核/关闭文案 agent + 模型/工具/工作流 |
| `A5/` | 实时监测 + 全套 mock 数据(场景 + 播放器 + 异步采集器) |
| `A6_A7/` | A6 风险研判 runtime + 提示词 + 工具 |
| `A7/` | 飞书通道适配层 + P8 持久化 + middleware + schema |
| `P8P9/` | 状态机子系统(纯 Python,不依赖 LangChain) |
| `gateway/` | **Node.js OpenClaw Channel Gateway(完整源码)** — `src/`、`config/`、`start.mjs`、`package.json`、`.env` 等 |
| `feishu_gateway_cli/` | 飞书通道 Python 客户端(精简版,被 a/agents/ 等引用) |
| `frontend/` | 配置前端 (app_config.py) + 一键启动 (start_all.py) |
| `agent_config/` | app_config.py 使用的 .env + saved_configs.json 索引 |
| `data/` | 运行时数据目录(jobs/、runtime/、feishu_card_index.json) |
| `agents/system_prompt/` | P6-P9 阶段 system prompt(真实 .md) |
| `.env` | 配置好的环境(LLM/飞书) |
| `requirements.txt` / `pyproject.toml` | Python 依赖声明 |

## 启动方式

### 一键启动(推荐)

```bash
cd a/
python frontend/start_all.py
```

会依次启动:

| 端口 | 服务 | 入口 |
|---|---|---|
| 5000 | 配置前端 | `frontend/app_config.py` |
| 5002 | P6 监测 + A5 + A6 (FastAPI 自带前端) | `agents/p6_monitor_agent.py` |
| 8089 | P8P9 状态机 Web 服务 | `P8P9/web_server.py` |

启动选项:

```bash
# 不启动配置前端
python frontend/start_all.py --no-config

# 不启动 P8P9
python frontend/start_all.py --no-p8p9

# 自定义端口
python frontend/start_all.py --config-port 5000 --p6-port 5002 --p8p9-port 8089
```

### 进程管理(生产化部署用)

`aaaseverstart.py` 是醒目进程管理器,提供 psutil 按 cmdline 强制杀僵尸 + 端口探活 + unbuffered 日志。

```bash
# 查看可管理服务
python aaaseverstart.py list

# 查看运行状态
python aaaseverstart.py status
python aaaseverstart.py status p8p9

# 启动 / 停止 / 重启
python aaaseverstart.py start all
python aaaseverstart.py stop p8p9
python aaaseverstart.py restart feishu_cfg

# 兜底:强杀所有已知服务的进程
python aaaseverstart.py kill-all
```

a/ 子集可管理的服务:

| name | 脚本 / 命令 | 端口 | 类型 |
|---|---|---|---|
| `gateway` | `node gateway/start.mjs --config gateway/config/config.feishu.local.json` | 8787 | Node.js |
| `p8p9` | `P8P9/web_server.py` | 8089 | Python |
| `feishu_cfg` | `feishu_gateway_cli/feishu_config_app.py` | 5003 | Python(Flask) |
| `chat_reply` | `python -m A7.adapters.chat_reply run ...` | (daemon) | Python |

剔除服务(原项目有但 a/ 不含):
- `webui` — 主流程 `web/server.py` 不在 a/ 范围

### 单独启动

```bash
cd a/

# P6 监测服务(A5 + A6 整合)
python agents/p6_monitor_agent.py
# 访问: http://localhost:5002/

# 配置前端
python frontend/app_config.py
# 访问: http://localhost:5000/

# P8P9 状态机 Web
python P8P9/web_server.py
# 访问: http://localhost:8090/
```

### 飞书通道 Gateway

需要启动 OpenClaw Channel Gateway(Node.js 服务,源码位于 `a/gateway/`):

```bash
# 1. 装 Node.js 依赖(只在 a/gateway/ 首次启动时执行)
cd a/gateway && npm install

# 2. 用 Python 启动 Gateway(读 a/gateway/.env 和 a/gateway/config/config.feishu.local.json)
python -m feishu_gateway_cli.start_gateway start   # 启动 :8787
python -m feishu_gateway_cli.start_gateway status  # 查看状态
python -m feishu_gateway_cli.start_gateway stop    # 停止

# 或直接用 Node.js 跑
cd a/gateway
node start.mjs --config config/config.feishu.local.json

# 也可加进 aaaseverstart.py 进程管理
python aaaseverstart.py start gateway
python aaaseverstart.py status gateway
```

**链路示例(完全在 a/ 内部自洽)**:

```
飞书用户/群
  ↕ Webhook
a/gateway/start.mjs              (Node.js, :8787)
  ↕ /v1/events, /v1/messages/*
a/agents/channel_gateway_client.py + a/A7/... (Python 同步客户端)
  ↕
a/agents/p8_disposition_agent.py + a/A7/middleware/p8_card_action_agent.py
```

## 设计要点

- **绝对包名 import 自动指向 a/ 内部**:由于 a/ 是新根目录,`from A5.x import ...`、`from A6_A7.x import ...`、`from P8P9.x import ...`、`from agents.x import ...`、`from feishu_gateway_cli import ...`、`from A7.x import ...` 等不需要改路径,全部解析到 a/ 自身。
- **不依赖外部 `data/jobs/`**:每个作业目录在 a/data/jobs/ 下独立生成。
- **不依赖外部 .env**:agent_config/.env 已就绪,a/.env 同样就绪。
- **Node.js Channel Gateway 不内嵌**:通过 `start_gateway.py` 启动外部进程(`pip install -e feishu_gateway_cli/` 已足够)。

## mock 数据流

```
a/A5/scenario_data/
  ├── types.py                  # 数据类型 dataclass
  ├── mock_work_permit.py       # 作业票
  ├── mock_cv.py                # CV 事件剧本(5 个场景 A-E)
  ├── mock_sensors.py           # 传感器剧本
  ├── mock_positioning.py       # UWB 定位剧本
  └── mock_vl.py                # VL 响应剧本

a/A5/stream_players/
  ├── mock_cv_player.py         # 25 FPS 播放器
  ├── mock_sensor_stream.py
  ├── mock_positioning_stream.py
  └── async_collector.py        # 异步数据汇集器(P6 调用入口)
```

调用链路:
1. `p6_monitor_agent.run_a5_monitoring(scenario, job_id, ...)` 启动
2. `AsyncCollector` 拉取 mock 数据 → 写入 `a/A5/logs/{job_id}/snapshot_*.json` + `cv_/sensor_/position_*.json`
3. `A5Agent.tick_from_snapshot(...)` 读取 snapshot → LLM 推理 → 写入 `raw_event_*.json`
4. P6 调度器:每 10s 收集未送 P7 的事件 → `trigger_p7_assessment_batch` 调 A6 风险研判
5. A6 写入 `a/A5/logs/{job_id}/assessments/a6_*.json`

## P8P9 状态机

```
P8 Agent (open_work_ticket)
  ↓ 创建 P8P9 job
P8P9/state_machine.py::ClosureService.create_job
  ↓ 写入 data/jobs/{job_id}/closure_state.json
P8P9/services/card_render.send_event_card
  ↓ 通过 agents.channel_gateway_client → Channel Gateway → 飞书

[用户在飞书群内点击按钮]
  ↓ webhook → /api/feishu/card-callback
feishu_card.process_card_callback
  ↓ 调用 P8 CardAction Agent
A7/middleware/p8_card_action_agent.run_card_action_agent
  ↓ apply_card_action: load_working_memory → set_job_status → save_archived_job
P8P9/state_machine.ClosureService.set_job_status
  ↓ 写入 closure_state.json
card_render.update_job_card
  ↓ 飞书卡原位更新

[人工在 web 后台 record_closure_review]
  ↓ POST /api/closure/jobs/{job_id}/record-closure-review
P8P9/web_server.py
  ↓ P8P9/business_actions.record_closure_review
  ↓ 如 approved → 调 P9 关闭文案
P9 run_p9_closure_review(job_id)
  ↓ 生成 ≤500 字关闭理由
card_render.update_job_card → 飞书卡 P9 段更新
```