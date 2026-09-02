"""
A5 Demo 前端 — 独立控制数据播放 + Agent 轮询

架构:
  - REST API 控制两个独立循环的启停
  - 前端轮询 REST 接口获取状态（每1秒）

启动:
    python frontend/app_a5.py
    python frontend/app_a5.py --port 8080
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import httpx

# 项目根目录（frontend/ 与 A5/ 同级）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from A5.agent.work_permit_rules import get_rules, update_rules, reset_rules
from A5.agent.system_prompt import (
    get_system_prompt, update_system_prompt, reset_system_prompt,
)
from A5.stream_players.async_collector import AsyncCollector

app = FastAPI(title="A5 Demo")

# 日志根目录：固定在 A5/logs
LOG_DIR = _ROOT / "A5" / "logs"

# ============================================================
# 静态文件
# ============================================================

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

INDEX_HTML = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


# ============================================================
# 全局状态（两个循环的协程/任务）
# ============================================================

class GlobalState:
    def __init__(self):
        self.scenario_task: Optional[asyncio.Task] = None
        self.agent_task: Optional[asyncio.Task] = None
        self.collector: Optional[AsyncCollector] = None
        self.scenario: str = "B"
        self.log_dir: str = ""
        self.agent_interval: int = 10
        self.agent_batch_size: int = 10
        self.agent_running: bool = False
        self.scenario_running: bool = False
        self._start_wall: Optional[datetime] = None

    def reset(self):
        self.scenario_task = None
        self.agent_task = None
        self.collector = None
        self.scenario = "B"
        self.log_dir = ""
        self.agent_running = False
        self.scenario_running = False
        self._start_wall = None

state = GlobalState()


# ============================================================
# 作业票规则 + 系统提示词
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


@app.get("/api/system_prompt")
async def api_get_system_prompt():
    return {"system_prompt": get_system_prompt()}


@app.post("/api/system_prompt")
async def api_update_system_prompt(body: dict):
    new_text = body.get("system_prompt", "")
    if not new_text:
        return {"error": "system_prompt 不能为空"}
    update_system_prompt(new_text)
    return {"system_prompt": get_system_prompt(), "status": "updated"}


@app.post("/api/system_prompt/reset")
async def api_reset_system_prompt():
    return {"system_prompt": reset_system_prompt(), "status": "reset"}


# ============================================================
# 场景/数据播放控制
# ============================================================

@app.post("/api/scenario/start")
async def start_scenario(body: dict = None):
    """启动数据播放（循环1），会自动停止旧协程"""
    # 1. 停止旧的 scenario + agent 协程
    await _stop_all()

    scenario = (body or {}).get("scenario", "B") if body else "B"
    log_dir = str(LOG_DIR)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    state.scenario = scenario
    state.log_dir = log_dir
    state.scenario_running = True

    state.scenario_task = asyncio.create_task(_run_data_play(scenario, log_dir))

    return {
        "status": "started",
        "scenario": scenario,
        "log_dir": log_dir,
        "duration_sec": 30,
    }


async def _stop_all():
    """停止所有旧协程（内部用，场景切换时调用）

    注意：只停止 scenario/collector，不杀 agent——agent 独立运行。
    """
    if state.scenario_task:
        state.scenario_running = False
        state.scenario_task.cancel()
        try:
            await state.scenario_task
        except (asyncio.CancelledError, Exception):
            pass
        state.scenario_task = None

    if state.collector:
        try:
            await state.collector.stop()
        except Exception:
            pass
        state.collector = None

    state.scenario_running = False


@app.post("/api/scenario/stop")
async def stop_scenario():
    """停止数据播放"""
    if state.scenario_task:
        state.scenario_task.cancel()
        try:
            await state.scenario_task
        except asyncio.CancelledError:
            pass
    if state.collector:
        try:
            await state.collector.stop()
        except Exception:
            pass
    state.scenario_running = False
    return {"status": "stopped"}


@app.get("/api/scenario/status")
async def get_scenario_status():
    """查询数据播放状态（始终从磁盘读，scenario 停止后仍能显示最新数据）"""
    log_dir = state.log_dir or str(LOG_DIR)

    # 进度：优先从 collector 读，scenario 停止后从磁盘 snapshot 文件数推算
    current_sec = 0
    if state.scenario_running and state.collector:
        try:
            start_wall = state.collector._start_wall
            if start_wall:
                elapsed = (datetime.now() - start_wall).total_seconds()
                current_sec = min(int(elapsed), 30)
        except Exception:
            current_sec = 0
        cv_count = len(state.collector._cv_raw)
    else:
        # 从 snapshot 文件数量推算进度
        lp = Path(log_dir)
        snapshot_count = len([f for f in lp.iterdir() if f.is_file() and f.name.startswith("snapshot_")])
        current_sec = min(snapshot_count, 30)
        cv_count = snapshot_count * 75  # 估算值

    return {
        "running": state.scenario_running,
        "current_second": current_sec,
        "total_seconds": 30,
        "log_dir": log_dir,
        "cv_count": cv_count,
        "log_files": _count_log_files(log_dir),
    }


# ============================================================
# Agent 控制
# ============================================================

@app.post("/api/agent/start")
async def start_agent(body: dict = None):
    """启动 Agent 轮询（循环2），不依赖场景状态，直接读 A5/logs"""
    if state.agent_running and state.agent_task:
        return {"error": "Agent 已在运行中，请先停止"}, 400

    interval = (body or {}).get("interval_sec", 10)
    batch_size = (body or {}).get("batch_size", 10)

    state.agent_interval = interval
    state.agent_batch_size = batch_size
    state.agent_running = True

    log_dir = str(LOG_DIR)
    state.agent_task = asyncio.create_task(_run_agent_loop(log_dir, interval, batch_size))

    return {"status": "started", "interval_sec": interval, "batch_size": batch_size, "log_dir": log_dir}


@app.post("/api/agent/stop")
async def stop_agent():
    """停止 Agent 轮询"""
    if state.agent_task:
        state.agent_task.cancel()
        try:
            await state.agent_task
        except asyncio.CancelledError:
            pass
    state.agent_running = False
    return {"status": "stopped"}


@app.get("/api/agent/status")
async def get_agent_status():
    """查询 Agent 轮询状态，始终读 A5/logs"""
    log_dir = str(LOG_DIR)
    pending = _get_all_batches(Path(log_dir))
    done = sum(1 for v in pending.values() if v["status"] == "done")
    error = sum(1 for v in pending.values() if v["status"] == "error")
    retryable = sum(1 for v in pending.values() if v["status"] == "retryable")
    processing = sum(1 for v in pending.values() if v["status"] == "processing")
    total = len(pending)

    return {
        "running": state.agent_running,
        "interval_sec": state.agent_interval,
        "batch_size": state.agent_batch_size,
        "pending": pending,
        "stats": {
            "total": total,
            "done": done,
            "error": error,
            "retryable": retryable,
            "processing": processing,
        },
        "log_files": _count_log_files(log_dir),
    }


@app.get("/api/agent/events")
async def get_agent_events():
    """返回所有 raw_event 文件中的事件列表，始终读 A5/logs"""
    import glob
    events = []
    for f in sorted(glob.glob(f"{LOG_DIR}/raw_event_*.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
            for ev in data.get("events", []):
                ev["_source_file"] = Path(f).name
                events.append(ev)
        except Exception:
            pass
    return {"events": events}


# ============================================================
# 日志文件读取（供前端轮询）
# ============================================================

@app.get("/api/logs/{path:path}")
async def get_log_file(path: str):
    """读取 A5/logs 下的任意文件。"""
    file_path = LOG_DIR / path
    if not file_path.exists() or not file_path.is_file():
        return {"error": f"文件不存在: {path}"}, 404
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/api/logs")
async def list_log_files():
    """列出 A5/logs 下所有文件，按类型分组"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    result = {}
    for f in sorted(LOG_DIR.iterdir()):
        if f.is_file():
            name = f.name
            if name.startswith("raw_event_"):
                key = "raw_events"
            elif name.startswith("processing_"):
                key = "processing"
            elif name.startswith("error_"):
                key = "errors"
            elif name.startswith("snapshot_"):
                key = "snapshots"
            elif name.startswith("cv_"):
                key = "cv"
            elif name.startswith("sensor_"):
                key = "sensor"
            elif name.startswith("position_"):
                key = "position"
            else:
                key = "other"
            if key not in result:
                result[key] = []
            result[key].append(name)
    for k in result:
        result[k] = sorted(result[k])
    return result


@app.post("/api/logs/clear")
async def clear_logs():
    """清理 A5/logs 下所有文件"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in LOG_DIR.iterdir():
        if f.is_file():
            f.unlink()
            count += 1
    return {"status": "cleared", "removed": count}


# ============================================================
# WebSocket（保留，向后兼容）
# ============================================================

@app.websocket("/ws/{scenario}")
async def ws_run(ws: WebSocket, scenario: str):
    """
    保留旧接口：前端连接后自动启动场景 + 轮询推送。
    新前端应使用 REST API + 轮询，不使用此接口。
    """
    await ws.accept()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = f"logs/frontend/{scenario}_{ts}"
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # 启动场景
    asyncio.create_task(_run_data_play(scenario, log_dir))
    # 启动 agent
    asyncio.create_task(_run_agent_loop(log_dir, 10, 10))
    # 启动 log_test 定期清理（每5分钟一次）
    asyncio.create_task(_clean_log_test_periodic())

    last_check = 0
    try:
        while True:
            await asyncio.sleep(1)
            if time.time() - last_check < 1:
                continue
            last_check = time.time()

            pending = _get_all_batches(Path(log_dir))
            updates = []
            for key, info in pending.items():
                try:
                    if info["status"] == "done":
                        rf = Path(log_dir) / f"raw_event_{key}.json"
                        data = json.load(open(rf, encoding="utf-8"))
                        ev_count = len(data.get("events", []))
                        updates.append({"wall_time_key": key, "event_count": ev_count, "type": "raw_event"})
                    elif info["status"] in ("processing", "retryable", "error"):
                        updates.append({
                            "wall_time_key": key, "retry": info.get("retry", 0),
                            "status": info["status"], "type": "error",
                        })
                except Exception:
                    pass

            if updates:
                await ws.send_json({"type": "log_update", "updates": updates, "log_dir": log_dir})
    except WebSocketDisconnect:
        pass


# ============================================================
# 内部协程：数据播放（循环1）
# ============================================================

async def _clean_log_test_periodic(interval_sec: int = 300):
    """每 interval_sec 秒清空一次 A5/log_test，不影响主循环"""
    from A5.agent.a5_agent import Path as AgentPath
    while True:
        await asyncio.sleep(interval_sec)
        try:
            log_test_dir = Path(__file__).resolve().parent.parent / "log_test"
            if log_test_dir.is_dir():
                for f in log_test_dir.iterdir():
                    f.unlink(missing_ok=True)
                print(f"[log_test cleanup] deleted {len(list(log_test_dir.iterdir())) if log_test_dir.exists() else 0} files before cleanup")
        except Exception as e:
            print(f"[log_test cleanup] error: {e}")


async def _run_data_play(scenario: str, log_dir: str):
    """循环1：每秒落盘日志文件"""
    collector = AsyncCollector(scenario, log_dir=log_dir)
    state.collector = collector
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    await collector.start()

    for sec in range(30):
        await asyncio.sleep(1.0)
        try:
            await collector.flush_second(sec)
        except asyncio.CancelledError:
            break

        if not state.scenario_running:
            break

    # 场景结束，标记
    state.scenario_running = False
    try:
        await collector.stop()
    except Exception:
        pass


# ============================================================
# 内部协程：Agent 轮询（循环2）
# ============================================================

async def _run_agent_loop(log_dir: str, interval_sec: int, batch_size: int):
    """循环2：每 interval_sec 秒启动下一批，允许并发生成"""
    from A5.agent.a5_agent import A5Agent
    from A5.scenario_data.mock_work_permit import WORK_PERMIT

    agent = A5Agent(collector=None, work_permit=WORK_PERMIT, raw_event_dir=log_dir)

    log_path = Path(log_dir)
    pending_tasks: list = []
    # 记录正在处理中的 wall_times，防止同一 snapshot 被多批选中
    inflight_wts: set = set()

    def _load_inflight():
        """从 processing_batch_*.json 恢复 in-flight 集合"""
        inflight_wts.clear()
        for f in log_path.glob("processing_batch_*.json"):
            try:
                data = json.load(open(f, encoding="utf-8"))
                for wt in data.get("wall_times", []):
                    inflight_wts.add(wt)
            except Exception:
                pass

    def _cleanup_done_tasks():
        """移除已完成任务，并从 inflight 清除其 wall_times"""
        for t in pending_tasks:
            if t.done():
                try:
                    result = t.result()
                    # result = {(wt, snap) for wt, snap in items}
                    for wt, _ in (result or []):
                        inflight_wts.discard(wt)
                except Exception:
                    pass
                pending_tasks.remove(t)

    while state.agent_running:
        await asyncio.sleep(interval_sec)

        # 0. 恢复 in-flight 状态（防止进程重启后状态丢失）
        _load_inflight()
        _cleanup_done_tasks()

        # 1. 扫描 snapshot，排除已在 in-flight 的
        snapshot_files = sorted(log_path.glob("snapshot_*.json"))
        unprocessed = []
        for sf in snapshot_files:
            safe_key = sf.stem[len("snapshot_"):]
            wall_time = safe_key.replace("-", ":").replace("_", ".")
            if wall_time in inflight_wts:
                continue
            status = _get_status_by_walltime(log_path, wall_time)
            if status in ("new", "retryable"):
                unprocessed.append((wall_time, sf))

        if not unprocessed:
            continue

        # 2. 取最早 batch_size 条
        batch = sorted(unprocessed)[:batch_size]
        wall_times = [wt for wt, _ in batch]
        batch_key = _sanitize_wall_time(wall_times[0])
        _submit_batch(log_path, wall_times, batch_key)
        for wt in wall_times:
            inflight_wts.add(wt)

        # 3. 加载 snapshot 数据
        snapshots_data = []
        for wt, sf in batch:
            try:
                data = json.load(open(sf, encoding="utf-8"))
                snap = data.get("snapshots", [{}])[0] if data.get("snapshots") else {}
                snapshots_data.append(snap)
            except Exception:
                snapshots_data.append({})

        # 4. 启动后台任务（不等结果）
        async def _process_batch(bk, wts, snaps):
            try:
                items = list(zip(wts, snaps))
                results = await agent.tick_batch(items)
                _remove_batch(log_path, bk)
                for wt, snap in items:
                    inflight_wts.discard(wt)
                    result = results.get(wt, {"wall_time": wt, "events": []})
                    await _on_success(log_path, wt, result)
                    # 有告警事件时触发 A6 研判（每个事件单独触发，用真实 event_id）
                    if result.get("events"):
                        for ev in result["events"]:
                            ev_id = ev.get("event_id") or wt
                            asyncio.create_task(_trigger_a6_assessment(ev_id, {"events": [ev]}))
            except Exception as e:
                _remove_batch(log_path, bk)
                for wt in wts:
                    inflight_wts.discard(wt)
                    await _on_error(log_path, wt, e, 0)

        pending_tasks.append(asyncio.create_task(_process_batch(batch_key, wall_times, snapshots_data)))

    # 等待所有 pending 任务完成
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    state.agent_running = False


# ============================================================
# 辅助函数（从 main_loop 复制，保持独立）
# ============================================================

def _sanitize_wall_time(wall_time: str) -> str:
    """
    将 wall_time 字符串转为合法的文件名 key。
    "2026-08-10T11:34:09.945" -> "2026-08-10T11-34-09_945"
    """
    return wall_time.replace(":", "-").replace(".", "_")


def _get_status_by_walltime(log_dir: Path, wall_time: str) -> str:
    """
    根据 wall_time 判断该秒数据的状态。
    - processing_*.json / processing_batch_*.json → processing
    - error_*.json → error
    - raw_event_*.json → done
    - 否则 → new
    """
    key = _sanitize_wall_time(wall_time)
    if (log_dir / f"processing_{key}.json").exists():
        return "processing"
    if (log_dir / f"processing_batch_{key}.json").exists():
        return "processing"
    if (log_dir / f"error_{key}.json").exists():
        return "error"
    if (log_dir / f"raw_event_{key}.json").exists():
        return "done"
    return "new"


def _submit_task(log_dir: Path, wall_time: str, retry: int = 0):
    key = _sanitize_wall_time(wall_time)
    json.dump({
        "submitted_at": wall_time,
        "retry": retry,
    }, open(log_dir / f"processing_{key}.json", "w", encoding="utf-8"))


def _submit_batch(log_dir: Path, wall_times: list, batch_key: str):
    """提交整批：写一个 processing_batch_*.json 标记"""
    json.dump({
        "wall_times": wall_times,
        "submitted_at": datetime.now().isoformat(timespec="milliseconds"),
    }, open(log_dir / f"processing_batch_{batch_key}.json", "w", encoding="utf-8"))


def _remove_batch(log_dir: Path, batch_key: str):
    p = log_dir / f"processing_batch_{batch_key}.json"
    if p.exists():
        p.unlink()


async def _on_success(log_dir: Path, wall_time: str, result: dict):
    key = _sanitize_wall_time(wall_time)
    processing = log_dir / f"processing_{key}.json"
    if processing.exists():
        processing.unlink()
    json.dump(result, open(log_dir / f"raw_event_{key}.json", "w",
                           encoding="utf-8"), ensure_ascii=False, indent=2)


async def _on_error(log_dir: Path, wall_time: str, error: Exception, current_retry: int):
    key = _sanitize_wall_time(wall_time)
    processing = log_dir / f"processing_{key}.json"
    if processing.exists():
        processing.unlink()
    json.dump({
        "error": str(error),
        "failed_at": datetime.now().isoformat(timespec="milliseconds"),
        "retry": current_retry + 1,
    }, open(log_dir / f"error_{key}.json", "w",
            encoding="utf-8"), ensure_ascii=False, indent=2)


def _get_all_batches(log_dir: Path) -> dict:
    """
    扫描 processing_batch_*.json，返回批次状态。
    返回 {batch_key: {"status": "processing"|"done"|"error", "wall_times": [...]}}
    """
    result = {}
    for f in log_dir.iterdir():
        if f.is_file() and f.name.startswith("processing_batch_"):
            key = f.name[len("processing_batch_"):-5]
            try:
                data = json.load(open(f, encoding="utf-8"))
                result[key] = {
                    "status": "processing",
                    "wall_times": data.get("wall_times", []),
                }
            except Exception:
                result[key] = {"status": "processing", "wall_times": []}
    # raw_event 文件说明对应批次已处理完成（不在队列显示，直接删掉批次标记文件）
    for f in log_dir.iterdir():
        if f.is_file() and f.name.startswith("raw_event_"):
            key = f.name[len("raw_event_"):-5]
            if key in result:
                result[key]["status"] = "done"
            # 顺便删掉残留的 processing_batch 文件
            batch_file = log_dir / f"processing_batch_{key}.json"
            if batch_file.exists():
                batch_file.unlink()
    # error_*.json 也要纳入（批次文件已在 _on_error 时删掉，只剩 error 文件）
    for f in log_dir.iterdir():
        if f.is_file() and f.name.startswith("error_"):
            key = f.name[len("error_"):-5]
            if key not in result:
                result[key] = {"status": "error", "wall_times": [key.replace("-", ":").replace("_", ".")]}
    # 过滤掉 done，只保留 processing 和 error
    result = {k: v for k, v in result.items() if v["status"] != "done"}
    return result


def _count_log_files(log_dir: str) -> dict:
    lp = Path(log_dir)
    if not lp.exists():
        return {}
    counts = {}
    for f in lp.iterdir():
        if f.is_file():
            name = f.name
            if name.startswith("raw_event_"):
                k = "raw_events"
            elif name.startswith("processing_"):
                k = "processing"
            elif name.startswith("error_"):
                k = "errors"
            elif name.startswith("snapshot_"):
                k = "snapshots"
            elif name.startswith("cv_"):
                k = "cv"
            elif name.startswith("sensor_"):
                k = "sensor"
            elif name.startswith("position_"):
                k = "position"
            else:
                continue
            counts[k] = counts.get(k, 0) + 1
    return counts


# ============================================================
# A6 研判触发
# ============================================================

A6_API_BASE = "http://localhost:5002"


async def _trigger_a6_assessment(event_id: str, event_data: dict):
    """
    当 A5 产生告警事件时，触发 A6 进行风险研判。

    Args:
        event_id: 事件ID（从 raw_event 文件名或事件内容中获取）
        event_data: 事件完整数据（包含 events 列表）
    """
    if not event_data.get("events"):
        return  # 无告警事件，不触发 A6

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 调 A6 的手动研判接口
            response = await client.post(
                f"{A6_API_BASE}/api/a6/process/{event_id}",
                json=event_data
            )
            if response.status_code == 200:
                print(f"[A5→A6] 事件 {event_id} 已触发 A6 研判")
            else:
                print(f"[A5→A6] 事件 {event_id} 触发失败: {response.status_code}")
    except Exception as e:
        print(f"[A5→A6] 事件 {event_id} 调用异常: {e}")


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    print(f"A5 Demo 启动: http://localhost:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
