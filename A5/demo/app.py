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

app = FastAPI(title="A5 Demo")

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
async def ws_run(ws: WebSocket, scenario: str):
    """前端连接此 WebSocket,自动开始运行场景并推送每秒数据"""
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

    # 延迟导入 Agent,避免 Mock LLM 报错
    try:
        from agent.a5_agent import A5Agent
        agent = A5Agent(collector, WORK_PERMIT, f"{log_dir}/raw_events")
    except Exception:
        agent = None

    await collector.start()
    await ws.send_json({"type": "start", "scenario": scenario,
                        "log_dir": log_dir, "duration_sec": 30})

    # 30 秒主循环
    active_events = []
    for sec in range(30):
        await asyncio.sleep(1.0)
        await collector.flush_second(sec)

        wall_time = collector._sec_to_wall(sec + 1)
        agent_now = collector._sec_to_wall(sec + 2)
        snapshot = collector.get_snapshot(wall_time)

        # Agent tick
        agent_events = []
        tick_ms = 0
        if agent:
            events = await agent.tick(wall_time, snapshot, agent_now=agent_now)
            tick_ms_raw = events[-1].pop("_tick_ms", 0) if events else 0
            tick_ms = round(tick_ms_raw, 1)
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

        # 提取 snapshot 摘要
        cv_logs = snapshot.get("cv_logs", [])
        sensor_data = snapshot.get("sensors", [{}])
        pos_data = snapshot.get("positions", [{}])

        # 推送
        payload = {
            "type":      "tick",
            "second":     sec + 1,
            "wall_time":  wall_time,
            "cv_n":       len(cv_logs),
            "ppe":        _extract_ppe(cv_logs),
            "sensors":    _extract_sensor(sensor_data),
            "positions":  _extract_positions(pos_data),
            "agent_events": agent_events,
            "tick_ms":    tick_ms,
        }
        await ws.send_json(payload)

        if agent_events:
            active_events.extend(agent_events)

    await collector.stop()

    # 完成
    total_time = 30.0  # 实时 30 秒
    if agent:
        summary = agent.summary()
    else:
        summary = {"total_events": 0, "by_person": {}}

    await ws.send_json({
        "type":    "done",
        "summary": summary,
        "total_time_s": total_time,
        "log_dir": log_dir,
    })


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