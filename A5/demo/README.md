# A5 Demo 前端 — 需求说明

> **定位**: 可视化运行 A5 场景,实时展示监控数据 + Agent 输出
> **目录**: `A5/demo/`
> **状态**: 需求梳理阶段

---

## 一、前端要干什么

```
用户在浏览器里:
  1. 选场景(A/B/C/D/E 或组合)
  2. 点"开始模拟"
  3. 看到实时数据(每秒更新):
     - 当前秒数(进度条)
     - CV 检测摘要(每人 PPE 状态)
     - 传感器读数(气体/温度/火焰)
     - 人员位置(动火区/休息室)
     - Agent 耗时(tick_ms)
  4. 有违规时弹窗高亮(红色)
  5. 跑完后显示汇总表
```

---

## 二、技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | **FastAPI** | 异步原生,WebSocket 推送,已有 .env |
| 前端 | 纯 HTML + JS | 不引入框架,一个文件够用 |
| 通信 | WebSocket + REST | WS 推实时数据,REST 拉历史 |
| 端口 | 5001(避免和 智能体配置:5000 冲突) |

---

## 三、API 设计

### 3.1 REST 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/scenarios` | 返回可用场景列表 `["A","B","C","D","E"]` |
| `GET` | `/api/logs/{scenario}/{run_id}/{type}/{sec}` | 读取已落盘日志(json 文件) |
| `GET` | `/api/raw_events/{scenario}/{run_id}` | 列出所有 raw_event 文件 |
| `POST` | `/api/run` | 启动一个场景(body: `{"scenario":"B"}`) |

### 3.2 WebSocket

```
ws://localhost:5001/ws/{scenario}/{run_id}

服务端每秒推送:
{
  "type": "tick",
  "second": 12,
  "wall_time": "2026-08-05T10:27:47.705",
  "snapshot": {
    "cv_summary": {
      "P7": {"helmet_missing_ratio":0.88, "is_violating":true},
      "P8": {"helmet_missing_ratio":0.0,  "is_violating":false},
      "P11":{"helmet_missing_ratio":0.0,  "is_violating":true}
    },
    "sensor_summary": {
      "gas_detector_03": {"value":0.2, "status":"normal"}
    },
    "position_summary": {
      "P7":  {"area_id":"动火区-A", "in_danger_zone":true}
    }
  },
  "agent_events": [
    {
      "type": "PPE缺失-头盔",
      "person": {"id":"P7", "name":"张师傅"},
      "explanation": "CV多数表决: 25/25帧未戴头盔(100%)"
    }
  ],
  "tick_ms": 5.2
}

场景结束时推送:
{
  "type": "done",
  "summary": {"total_events":2, "by_person":{"P7":2}},
  "total_time_s": 30.1
}
```

---

## 四、页面布局(草图)

```
┌─ A5 作业过程监测 ─────────────────────── 场景: [B ▼]  [▶ 开始] ─┐
│                                                                   │
│  ┌─ 实时概览 ──────────────────────────────────────────────────┐ │
│  │  T= 8s / 30s   ████████░░░░░░░░░░░░░░   tick=5.2ms       │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─ PPE检测 ──────────┐  ┌─ 传感器 ──────────┐  ┌─ 人员位置 ───┐│
│  │ P7(张师傅,焊工)    │  │ 可燃气体:0.2 LEL │  │ P7: 动火区-A││
│  │   头盔:❌(100%)    │  │   状态:normal     │  │    (危险区)   ││
│  │   护目镜:✅        │  │ 温度:26.0°C      │  │ P8: 动火区-A││
│  │   防护服:✅        │  │   状态:normal     │  │ P11: 动火区-A││
│  │ P8(王师傅,焊工)    │  │ 火焰探测:正常     │  │              ││
│  │   全部正常          │  │                   │  │              ││
│  └────────────────────┘  └───────────────────┘  └──────────────┘│
│                                                                   │
│  ┌─ Agent事件(仅当有违规时出现) ─────────────────────────────────┐│
│  │ 🚨 T=8s | PPE缺失-头盔 | 张师傅(焊工)                     ││
│  │    CV多数表决: 25/25帧未戴头盔(100%)                       ││
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
└─ 30s 结束后显示汇总: 共2个事件,P7:2次 ───────────────────────────┘
```

---

## 五、后端文件结构

```
demo/
├── README.md              ← 本文档
├── app.py                 ← FastAPI 后端(WEB 服务器 + WS 推送)
├── templates/
│   └── index.html         ← 前端页面(单文件)
└── static/
    └── app.js             ← 前端 JS(WS 通信 + DOM 更新)
```

## 六、后端核心逻辑(app.py)

```python
from fastapi import FastAPI, WebSocket
from stream_players.async_collector import AsyncCollector
from agent.a5_agent import A5Agent
from scenario_data.mock_work_permit import WORK_PERMIT

app = FastAPI()

@app.post("/api/run")
async def run_scenario(body: dict):
    scenario = body["scenario"]  # "B"
    run_id = f"{scenario}_{int(time.time())}"
    log_dir = f"logs/frontend/{run_id}"
    
    collector = AsyncCollector(scenario, log_dir=log_dir)
    agent = A5Agent(collector, WORK_PERMIT, f"{log_dir}/raw_events")
    
    await collector.start()
    
    # 后台协程: 每 1 秒跑 tick + 通过 WS 推送
    asyncio.create_task(_run_loop(scenario, run_id, collector, agent))
    
    return {"run_id": run_id, "status": "started"}

async def _run_loop(scenario, run_id, collector, agent):
    for sec in range(30):
        await asyncio.sleep(1.0)
        await collector.flush_second(sec)
        wall_time = collector._sec_to_wall(sec + 1)
        agent_now = collector._sec_to_wall(sec + 2)
        snapshot = collector.get_snapshot(wall_time)
        events = await agent.tick(wall_time, snapshot, agent_now=agent_now)
        
        # 推送给浏览器
        await ws_manager.broadcast(run_id, {
            "type": "tick",
            "second": sec + 1,
            "snapshot": _extract_summary(snapshot),
            "agent_events": events,
            "tick_ms": events[-1].get("_tick_ms") if events else getattr(agent, "_last_tick_ms", 0),
        })
    
    await collector.stop()
    await ws_manager.broadcast(run_id, {"type": "done", "summary": agent.summary()})
```

## 七、启动方式

```bash
# 启动前端服务
cd A5
python demo/app.py --host 0.0.0.0 --port 5001

# 浏览器打开
http://localhost:5001
```

## 八、待确认的设计点

| # | 问题 | 选项 |
|---|---|---|
| 1 | 前端是否支持同时跑多个场景? | A. 单场景(简单) B. 多 Tab |
| 2 | 是否需要"暂停/继续"按钮? | A. 不需要 B. 需要 |
| 3 | 是否展示原始日志(每帧)还是只展示聚合快照? | A. 只快照 B. 可展开看原始 |
| 4 | 是否需要回放功能(跑完后拖进度条)? | A. 不需要 B. 需要 |
| 5 | 跑完的场景结果是否要持久化(供以后查看)? | A. 保留文件 B. 浏览器刷新就丢 |

---

## 九、下一步

确认上述设计后,按顺序写:
1. `demo/app.py`(FastAPI + WebSocket)
2. `demo/templates/index.html`(前端页面)

预计工作量: 1 天。