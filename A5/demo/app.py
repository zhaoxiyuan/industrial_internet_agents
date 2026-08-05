"""
A5 Demo 前端 — FastAPI 后端 + WebSocket 推送

常用命令:
    # 启动(默认 5001 端口)
    python A5/demo/app.py

    # 指定端口
    python A5/demo/app.py --port 8080

    # 允许局域网访问
    python A5/demo/app.py --host 0.0.0.0 --port 5001

    # 打开浏览器
    http://127.0.0.1:5001

    # 停止已运行的服务
    python demo/app.py --stop
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from stream_players.async_collector import AsyncCollector
from scenario_data.mock_work_permit import WORK_PERMIT
from agent.work_permit_rules import get_rules, update_rules, reset_rules

app = FastAPI(title="A5 Demo")


# ============================================================
# REST: 作业票规则编辑
# ============================================================

@app.get("/api/rules")
async def api_get_rules():
    return {"rules": get_rules()}


@app.post("/api/rules")
async def api_update_rules(body: dict):
    new_text = body.get("rules", "")
    if not new_text:
        return {"error": "rules 不能为空"}
    update_rules(new_text)
    return {"rules": get_rules(), "status": "updated"}


@app.post("/api/rules/reset")
async def api_reset_rules():
    return {"rules": reset_rules(), "status": "reset"}

# 静态文件
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 首页
INDEX_HTML = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


# ============================================================
# WebSocket: 一个连接 = 一次场景运行
# ============================================================

@app.websocket("/ws/{scenario}")
async def ws_run(ws: WebSocket, scenario: str, interval: int = 10):
    """前端连接此 WebSocket,自动开始运行场景并推送每秒数据。
       interval: 每 N 秒调用一次 agent.tick(默认 10s)"""
    scenario = scenario.upper()
    if scenario not in "ABCDE":
        await ws.accept()
        await ws.send_json({"error": f"unknown scenario: {scenario}"})
        await ws.close()
        return

    await ws.accept()

    # 准备
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = f"logs/frontend/{scenario}_{ts}"
    collector = AsyncCollector(scenario, log_dir=log_dir)

    # 延迟导入 Agent
    from agent.a5_agent import A5Agent, AgentQueue
    agent = A5Agent(collector, WORK_PERMIT, f"{log_dir}/raw_events")
    agent_queue = AgentQueue(agent)

    await collector.start()
    await ws.send_json({"type": "start", "scenario": scenario,
                        "log_dir": log_dir, "duration_sec": 30,
                        "agent_interval_sec": interval})

    # Agent 完成时回调: 通过 WS 单独推送
    async def on_agent_done(events, tick_ms, error=None):
        if error:
            await ws.send_json({"type": "agent_error", "error": error})
            return
        agent_events = []
        for ev in events:
            agent_events.append({
                "type":       ev.get("type", "?"),
                "person":     ev.get("person", {}),
                "status":     ev.get("status", "ongoing"),
                "duration_sec": ev.get("duration_sec", 0),
                "first_seen": ev.get("first_seen", ""),
                "last_seen":  ev.get("last_seen", ""),
                "explanation": ev.get("explanation", ""),
            })
        await ws.send_json({
            "type":     "agent_events",
            "second":   events[0].get("second", 0) if events else 0,
            "events":   agent_events,
            "tick_ms":  round(tick_ms, 1),
        })

    # 主循环: 每秒采集+推送数据, agent 间隔 fire
    last_tick_wall = collector._sec_to_wall(0)  # 上次 tick 的时间
    for sec in range(30):
        await asyncio.sleep(1.0)
        await collector.flush_second(sec)

        wall_time = collector._sec_to_wall(sec + 1)
        agent_now = collector._sec_to_wall(sec + 2)
        snapshot = collector.get_snapshot(wall_time)

        # Agent: fire-and-forget(不阻塞), 只在 interval 整数倍时 fire
        call_agent = ((sec + 1) % interval == 0)
        if call_agent:
            # 传入累积时间段: [上次tick时间, 当前时间]
            agent_queue.submit(last_tick_wall, wall_time, snapshot, on_agent_done)
            last_tick_wall = wall_time

        # 提取 snapshot 摘要
        cv_logs = snapshot.get("cv_logs", [])
        sensor_data = snapshot.get("sensors", [{}])
        pos_data = snapshot.get("positions", [{}])

        # 推送(不含 agent_events — agent 结果异步推送)
        await ws.send_json({
            "type":      "tick",
            "second":     sec + 1,
            "wall_time":  wall_time,
            "cv_n":       len(cv_logs),
            "ppe":        _extract_ppe(cv_logs),
            "sensors":    _extract_sensor(sensor_data),
            "positions":  _extract_positions(pos_data),
            "agent_pending": agent_queue.pending_count,
        })

    # 等 Agent 队列排空(给足时间,每个 tick 最多 120s)
    await asyncio.sleep(1)
    max_wait = 120
    waited = 0
    while agent_queue.pending_count > 0 or agent_queue._running:
        if waited >= max_wait:
            break
        await asyncio.sleep(3)
        waited += 3
        # 通知前端等待中
        try:
            await ws.send_json({"type": "agent_waiting",
                "msg": f"等待Agent完成... {waited}s/{max_wait}s",
                "pending": agent_queue.pending_count})
        except Exception:
            break

    await collector.stop()

    # Agent 完成汇总
    summary = agent.summary()
    await ws.send_json({"type": "done", "summary": summary,
                        "total_time_s": 30, "log_dir": log_dir})

    

# ============================================================
# 辅助函数
# ============================================================

def _extract_ppe(cv_logs):
    """从 CV 日志中提取每人 PPE 状态(多数表决)"""
    if not cv_logs:
        return {}
    # 收集所有 person_id
    pids = sorted(set(log.get("person_id","") for log in cv_logs))
    result = {}
    for pid in pids:
        if not pid: continue
        plogs = [l for l in cv_logs if l.get("person_id") == pid]
        total = len(plogs)
        if total == 0: continue
        def _miss(name):
            return sum(1 for l in plogs
                       if not next((d["value"] for d in l.get("detections",[])
                                    if d["class_name"]==name), True))
        n_h = _miss("helmet")
        n_g = _miss("goggles")
        n_s = _miss("protective_suit")
        result[pid] = {
            "total": total,
            "helmet":       {"missing": n_h, "ratio": round(n_h/total, 2), "ok": n_h/total < 0.8},
            "goggles":      {"missing": n_g, "ratio": round(n_g/total, 2), "ok": n_g/total < 0.8},
            "protective_suit": {"missing": n_s, "ratio": round(n_s/total, 2), "ok": n_s/total < 0.8},
        }
    return result


def _extract_sensor(sensor_data):
    """从 snapshot 中提取传感器摘要"""
    if not sensor_data or not sensor_data[0].get("readings"):
        return {}
    result = {}
    for sid, r in sensor_data[0]["readings"].items():
        result[sid] = {
            "type":   r.get("type", sid),
            "value":  r.get("value"),
            "unit":   r.get("unit", ""),
            "status": r.get("status", "normal"),
        }
    return result


def _extract_positions(pos_data):
    """从 snapshot 中提取人员位置摘要"""
    if not pos_data or not pos_data[0].get("positions"):
        return {}
    result = {}
    for wid, p in pos_data[0]["positions"].items():
        result[wid] = {
            "area_id":         p.get("area_id", "?"),
            "in_danger_zone":  p.get("is_in_danger_zone", False),
        }
    return result


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("A5 Demo 前端服务启动: http://localhost:5001")
    uvicorn.run(app, host="0.0.0.0", port=5001, log_level="info")