"""
P6: 作业过程动态监测 - 基于 A5 实现（完整 Web 服务）

将 frontend/app_a5.py 的全部功能迁移至此：
- FastAPI Web 服务（端口 5002）
- 场景数据播放控制
- Agent 轮询控制
- 作业票规则管理
- 系统提示词管理
- 前端 HTML 页面
- WebSocket 实时推送

启动方式：
    python agents/p6_monitor_agent.py
    python agents/p6_monitor_agent.py --port 8080
    然后访问 http://localhost:5002/ 或 http://localhost:8080/ 或 http://localhost:自定义的端口号/
    以及 http://localhost:5002/a6
"""
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── 项目路径 ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# ── 加载 .env ───────────────────────────────────────────────────────────────
from dotenv import load_dotenv
_env_file = _ROOT / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

# A5 核心组件
from A5.agent.a5_agent import A5Agent
from A5.agent.work_permit_rules import get_rules, update_rules, reset_rules
from A5.agent.system_prompt import (
    get_system_prompt, update_system_prompt, reset_system_prompt,
)
from A5.scenario_data.mock_work_permit import WORK_PERMIT
from A5.stream_players.async_collector import AsyncCollector

# P7 提供 A6 Agent + A6 路由（共享端口 5002，P6 启动时通过 register_a6_routes 挂载）
# 使用绝对导入以兼容 `python agents/p6_monitor_agent.py` 直接调用（__main__ 模式下相对导入会失败）
from agents.p7_risk_agent import trigger_a6_assessment, register_a6_routes

# ── FastAPI 应用 ─────────────────────────────────────────────────────────────
app = FastAPI(title="A5 作业过程监测 - P6 Monitor")

# 日志根目录：固定在 A5/logs
LOG_DIR = _ROOT / "A5" / "logs"

# ── per-job 运行状态机 ──────────────────────────────────────────────────────
# 当前同时只跑一个 active job（mutex 守门），但按 job_id 路径完全隔离，
# 为未来多并发做准备。
_monitor_mutex = asyncio.Lock()
_active_jobs: Dict[str, Dict[str, Any]] = {}  # job_id → {scenario, status, collector_task, agent_task, started_at, ...}


def _get_active_job_id() -> str:
    """返回当前 running 的 job_id（用于在 _run_agent_loop 透传给 trigger_a6_assessment）。
    若无 active job 则返回空串（向后兼容旧全局路径）。"""
    for job_id, state in _active_jobs.items():
        if state.get("status") in ("agent_running", "playing", "starting"):
            return job_id
    return ""


# ── 挂载 A6 路由（P7 模块提供，共享端口 5002）───────────────────────────────
# 包括 /a6 页面 + /api/a6/* 全部接口
register_a6_routes(app, a5_log_dir=str(LOG_DIR))

# ── 前端 HTML ───────────────────────────────────────────────────────────────
INDEX_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A5 作业过程监测 Demo</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:system-ui,monospace; background:#0f172a; color:#e2e8f0; min-height:100vh; }

#app { max-width:1200px; margin:0 auto; padding:20px; }

/* ── 顶部标题 + 控制栏 ── */
.header { display:flex; align-items:center; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
h1 { font-size:18px; color:#e2e8f0; white-space:nowrap; }
.controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
select,button { padding:7px 14px; font-size:13px; border-radius:6px; border:none; cursor:pointer; }
select { background:#1e293b; color:#e2e8f0; }
.btn-blue { background:#3b82f6; color:#fff; font-weight:bold; }
.btn-red  { background:#ef4444; color:#fff; font-weight:bold; }
.btn-green{ background:#16a34a; color:#fff; }
.btn-gray { background:#475569; color:#fff; }
button:disabled { opacity:0.5; cursor:not-allowed; }

/* ── Tab 栏 ── */
.tab-bar { display:flex; gap:4px; margin-bottom:16px; border-bottom:1px solid #334155; }
.tab-btn {
  padding:8px 20px; font-size:13px; background:transparent; color:#94a3b8;
  border:none; cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-1px;
  transition:all 0.15s;
}
.tab-btn:hover { color:#e2e8f0; }
.tab-btn.active { color:#3b82f6; border-bottom-color:#3b82f6; }

/* ── Tab 内容 ── */
.tab-content { display:none; }
.tab-content.active { display:block; }

/* ── 通用卡片 ── */
.card { background:#1e293b; border-radius:8px; padding:16px; margin-bottom:12px; }
.card h3 { font-size:13px; color:#94a3b8; margin-bottom:10px; border-bottom:1px solid #334155; padding-bottom:6px; }

/* ── 进度条 ── */
.progress { width:100%; height:8px; background:#334155; border-radius:4px; margin-bottom:12px; overflow:hidden; }
.progress-bar { height:100%; background:#3b82f6; transition:width 0.3s; border-radius:4px; }

/* ── 状态文字 ── */
.status { font-size:13px; color:#94a3b8; }
.status-run { color:#4ade80; }
.status-stop{ color:#ef4444; }

/* ── 网格布局 ── */
.grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.grid-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
.grid-4 { display:grid; grid-template-columns:2fr 1fr; gap:12px; }

/* ── 列表行 ── */
.row { display:flex; justify-content:space-between; padding:5px 0; font-size:13px; border-bottom:1px solid #1e293b; }
.row:last-child { border-bottom:none; }
.row .label { color:#94a3b8; }

/* ── 颜色 ── */
.ok   { color:#4ade80; }
.no   { color:#ef4444; font-weight:bold; }
.warn { color:#fbbf24; }
.info { color:#60a5fa; }

/* ── 事件卡片 ── */
.event-item {
  background:#7f1d1d; border-left:3px solid #ef4444;
  padding:10px 12px; margin-bottom:8px; border-radius:4px; font-size:13px;
}
.event-item.closed { background:#14532d; border-left-color:#4ade80; }
.event-item .ev-head { display:flex; justify-content:space-between; margin-bottom:4px; }
.ev-time { color:#f87171; font-weight:bold; }
.ev-type { color:#fca5a5; }
.ev-person { color:#fde68a; }
.ev-exp { color:#fca5a5; font-size:12px; margin-top:4px; }
.ev-closed { color:#4ade80; font-size:12px; }

/* ── Agent 队列 ── */
.queue-item {
  display:flex; align-items:center; gap:10px;
  padding:8px 12px; border-radius:6px; margin-bottom:6px; font-size:13px;
}
.queue-item.done       { background:#14532d; border-left:3px solid #4ade80; }
.queue-item.processing { background:#1e293b; border-left:3px solid #3b82f6; animation:pulse 1s infinite; }
.queue-item.waiting    { background:#1e293b; border-left:3px solid #64748b; opacity:0.6; }
.queue-item.error      { background:#7f1d1d; border-left:3px solid #ef4444; }
.queue-item .qicon { font-size:16px; }
.queue-item .qkey { color:#94a3b8; font-size:12px; }
.queue-item .qms  { margin-left:auto; font-size:12px; color:#94a3b8; }

/* ── textarea ── */
textarea { width:100%; background:#0f172a; color:#e2e8f0; border:1px solid #334155; border-radius:6px; padding:10px; font-size:12px; font-family:monospace; line-height:1.6; resize:vertical; }
.btn-row { display:flex; gap:8px; margin-bottom:10px; }

/* ── 统计徽章 ── */
.stats-row { display:flex; gap:16px; margin-bottom:12px; flex-wrap:wrap; }
.stat-badge { background:#334155; padding:6px 14px; border-radius:20px; font-size:13px; }
.stat-badge .val { color:#e2e8f0; font-weight:bold; margin-right:4px; }
.stat-badge .lbl { color:#94a3b8; }

/* ── 无数据提示 ── */
.empty { color:#475569; font-size:13px; padding:20px; text-align:center; }

/* ── 日志文件统计 ── */
.log-counts { display:flex; gap:12px; flex-wrap:wrap; font-size:13px; }
.log-count { background:#334155; padding:4px 12px; border-radius:4px; }
.log-count .v { color:#60a5fa; font-weight:bold; }

/* ── 系统提示词 ── */
.sp-warning { font-size:12px; color:#fbbf24; margin-bottom:8px; }

/* ── 动画 ── */
@keyframes pulse { 0%,100%{opacity:1;} 50%{opacity:0.6;} }

/* ── 锁定文本框（job_id 监测中）── */
input:disabled, textarea:disabled { opacity:0.7; cursor:not-allowed; }
input[disabled] { background:#1e293b !important; color:#94a3b8 !important; }
</style>
</head>
<body>
<div id="app">

  <!-- 顶部 -->
  <div class="header">
    <h1>A5 作业过程监测 Demo</h1>
    <span id="statusText" class="status">未启动</span>
  </div>

  <!-- Tab 栏 -->
  <div class="tab-bar">
    <button class="tab-btn active" id="btn-tab0" onclick="showTab(0)">Mock进度 + 报警看板</button>
    <button class="tab-btn"        id="btn-tab1" onclick="showTab(1)">Agent进度</button>
    <button class="tab-btn"        id="btn-tab2" onclick="showTab(2)">作业票规则</button>
    <button class="tab-btn"        id="btn-tab3" onclick="showTab(3)">系统提示词</button>
  </div>

  <!-- ========== Tab0: Mock进度 + 报警看板 ========== -->
  <div class="tab-content active" id="tab0">

    <!-- 控制栏 -->
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
      <select id="selScenario">
        <option value="B">场景 B - 工人摘头盔</option>
        <option value="A">场景 A - 正常作业</option>
        <option value="C">场景 C - 可燃气体上升</option>
        <option value="D">场景 D - 监护人离岗</option>
        <option value="E">场景 E - 多重违规</option>
      </select>
      <button class="btn-blue" id="btnPlay" onclick="startScenario()">▶ 开始播放</button>
      <button class="btn-red"  id="btnStop" onclick="stopScenario()" disabled>■ 停止</button>
    </div>

    <!-- 进度条 -->
    <div class="progress"><div id="progressBar" class="progress-bar" style="width:0%"></div></div>

    <!-- 实时数据 + 报警事件 -->
    <div class="grid-4">
      <!-- 左：实时数据 -->
      <div>
        <div class="card">
          <h3>PPE 检测</h3>
          <div id="ppeList"><div class="empty">无数据</div></div>
        </div>
        <div class="card">
          <h3>传感器</h3>
          <div id="sensorList"><div class="empty">无数据</div></div>
        </div>
        <div class="card">
          <h3>人员位置</h3>
          <div id="positionList"><div class="empty">无数据</div></div>
        </div>
      </div>
      <!-- 右：报警事件 -->
      <div>
        <div class="card">
          <h3>报警事件 <span id="evCount" style="color:#94a3b8;font-size:12px;font-weight:normal"></span></h3>
          <div id="eventList" style="max-height:320px;overflow-y:auto;"><div class="empty">暂无事件</div></div>
        </div>
      </div>
    </div>

    <!-- 日志文件统计 -->
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <h3 style="margin:0;border:none;padding:0">日志文件状态</h3>
        <button class="btn-red" style="padding:4px 12px;font-size:12px;" onclick="clearLogs()">🗑 清理日志</button>
      </div>
      <div class="log-counts" id="logCounts">
        <div class="log-count">snapshot: <span class="v" id="cntSnapshot">-</span></div>
        <div class="log-count">raw_event: <span class="v" id="cntRaw">-</span></div>
        <div class="log-count">processing: <span class="v" id="cntProcessing">-</span></div>
        <div class="log-count">error: <span class="v" id="cntError">-</span></div>
        <div class="log-count">cv: <span class="v" id="cntCv">-</span></div>
        <div class="log-count">sensor: <span class="v" id="cntSensor">-</span></div>
        <div class="log-count">position: <span class="v" id="cntPosition">-</span></div>
      </div>
    </div>
  </div>

  <!-- ========== Tab1: Agent进度 ========== -->
  <div class="tab-content" id="tab1">
    <div class="card">
      <h3>Agent 控制</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
        <select id="selInterval">
          <option value="3">间隔: 3秒</option>
          <option value="5">间隔: 5秒</option>
          <option value="10" selected>间隔: 10秒</option>
        </select>
        <select id="selBatch">
          <option value="5">批次: 5条</option>
          <option value="10" selected>批次: 10条</option>
        </select>
        <button class="btn-blue" id="btnAgentStart" onclick="startAgent()">▶ 启动Agent</button>
        <button class="btn-red"  id="btnAgentStop"  onclick="stopAgent()" disabled>■ 停止Agent</button>
        <span id="agentStatus" class="status">未启动</span>
      </div>

      <!-- ── per-job 监测（2026-08-19 新增；与 P1-P5 job 规范对齐） ── -->
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px;padding:10px;background:#1e293b;border-radius:6px;">
        <span style="color:#94a3b8;font-size:12px;">📋 per-job 监测（写入 data/jobs/&lt;job_id&gt;/P6 + P7）：</span>
        <label style="color:#94a3b8;font-size:12px;">作业 ID:</label>
        <input id="txtJobId" type="text" placeholder="例: 20260819154312029"
               style="padding:6px 10px;font-size:13px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:4px;width:180px;" />
        <label style="color:#94a3b8;font-size:12px;">场景:</label>
        <select id="selScenarioJob" style="padding:6px 10px;font-size:13px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:4px;">
          <option value="A">A - 正常作业</option>
          <option value="B" selected>B - 工人摘头盔</option>
          <option value="C">C - 可燃气体上升</option>
          <option value="D">D - 监护人离岗</option>
          <option value="E">E - 多重违规</option>
        </select>
        <button class="btn-blue" id="btnMonitorStart" onclick="startMonitor()">🚀 开始监测（5秒后播放）</button>
        <button class="btn-red"  id="btnMonitorStop"  onclick="stopMonitor()" disabled>■ 停止监测</button>
        <span id="monitorStatus" class="status">未启动</span>
      </div>
    </div>

    <div class="card">
      <h3>处理队列 <span id="queueStats" style="color:#94a3b8;font-size:12px;font-weight:normal"></span></h3>
      <div id="queueList"><div class="empty">无处理记录</div></div>
    </div>

    <div class="card">
      <h3>统计</h3>
      <div class="stats-row" id="statsRow">
        <div class="stat-badge"><span class="val" id="statTotal">0</span><span class="lbl"> 处理批次</span></div>
        <div class="stat-badge"><span class="val" id="statDone" style="color:#4ade80">0</span><span class="lbl"> 已完成条</span></div>
        <div class="stat-badge"><span class="val" id="statError" style="color:#ef4444">0</span><span class="lbl"> 失败条</span></div>
      </div>
    </div>
  </div>

  <!-- ========== Tab2: 作业票规则 ========== -->
  <div class="tab-content" id="tab2">
    <div class="card">
      <div class="btn-row">
        <button class="btn-green" onclick="saveRules()">💾 保存</button>
        <button class="btn-gray"  onclick="resetRules()">↺ 重置</button>
        <button class="btn-gray"  onclick="loadRules()">↻ 刷新</button>
      </div>
      <textarea id="rulesEditor" rows="18"></textarea>
    </div>
  </div>

  <!-- ========== Tab3: 系统提示词 ========== -->
  <div class="tab-content" id="tab3">
    <div class="card">
      <p class="sp-warning">⚠ 修改系统提示词会影响 Agent 判断逻辑，请谨慎操作</p>
      <div class="btn-row">
        <button class="btn-green" onclick="saveSystemPrompt()">💾 保存</button>
        <button class="btn-gray"  onclick="resetSystemPrompt()">↺ 重置</button>
        <button class="btn-gray"  onclick="loadSystemPrompt()">↻ 刷新</button>
      </div>
      <textarea id="spEditor" rows="18"></textarea>
    </div>
  </div>

</div><!-- #app -->

<script>
let _scenarioRunning = false;
let _agentRunning = false;
let _currentSecond = 0;
let _pollLogTimer = null;
let _pollScenarioTimer = null;
let _pollAgentTimer = null;

const WORKERS = {
  P7:  { name:"张师傅", role:"焊工" },
  P8:  { name:"王师傅", role:"焊工" },
  P11: { name:"赵师傅", role:"监护人" },
};

function showTab(idx) {
  document.querySelectorAll('.tab-content').forEach((el,i) =>
    el.classList.toggle('active', i===idx));
  document.querySelectorAll('.tab-btn').forEach((el,i) =>
    el.classList.toggle('active', i===idx));
}

function startLogPoll() {
  if (_pollLogTimer) return;
  _pollLogTimer = setInterval(pollLog, 1000);
}
function stopLogPoll() {
  if (_pollLogTimer) { clearInterval(_pollLogTimer); _pollLogTimer = null; }
}
function startScenarioPoll() {
  if (_pollScenarioTimer) return;
  _pollScenarioTimer = setInterval(pollScenario, 1000);
}
function stopScenarioPoll() {
  if (_pollScenarioTimer) { clearInterval(_pollScenarioTimer); _pollScenarioTimer = null; }
}
function startAgentPoll() {
  if (_pollAgentTimer) return;
  _pollAgentTimer = setInterval(pollAgent, 1000);
}
function stopAgentPoll() {
  if (_pollAgentTimer) { clearInterval(_pollAgentTimer); _pollAgentTimer = null; }
}

async function pollLog() {
  await Promise.all([fetchLogCounts(), fetchRawEvents()]);
}

// 读 job_id 文本框（首选）→ 兑底用 _monitorJobId（最后一次有效值）→ 都没有则空串走全局 A5/logs
//   - 文本框为主：用户改文本框后轮询立即跟随
//   - 兑底：文本框被清空时仍能查最后一次历史的 job_id
//   - 空串：保持向后兼容（页面首次加载、还没填 job_id 时走 A5/logs）
function getActiveJobId() {
  const tb = document.getElementById("txtJobId");
  const txt = tb ? tb.value.trim() : "";
  return txt || _monitorJobId || "";
}

async function fetchLogCounts() {
  // 读文本框 → 兑底 _monitorJobId → 默认 A5/logs
  const jobId = getActiveJobId();
  const url = jobId ? "/api/logs?job_id=" + encodeURIComponent(jobId) : "/api/logs";
  try {
    const r = await fetch(url);
    const d = await r.json();
    document.getElementById("cntSnapshot").textContent  = (d.snapshots  || []).length;
    document.getElementById("cntRaw").textContent       = (d.raw_events || []).length;
    document.getElementById("cntProcessing").textContent= (d.processing || []).length;
    document.getElementById("cntError").textContent     = (d.errors     || []).length;
    document.getElementById("cntCv").textContent        = (d.cv         || []).length;
    document.getElementById("cntSensor").textContent    = (d.sensor     || []).length;
    document.getElementById("cntPosition").textContent  = (d.position   || []).length;
  } catch(e) { console.error("fetchLogCounts:", e); }
}

async function pollScenario() {
  try {
    // 跟随 per-job 模式：读 data/jobs/{job_id}/P6/ 进度
    const jobId = getActiveJobId();
    const url = jobId ? "/api/scenario/status?job_id=" + encodeURIComponent(jobId) : "/api/scenario/status";
    const r = await fetch(url);
    const d = await r.json();
    _currentSecond = d.current_second || 0;
    const pct = Math.min(100, (_currentSecond / 30) * 100);
    document.getElementById("progressBar").style.width = pct + "%";
    document.getElementById("statusText").textContent =
      _scenarioRunning
        ? "T=" + _currentSecond + "s/30s | CV=" + (d.cv_count || 0) + "条"
        : "已停止";
    await fetchLatestSnapshot();
  } catch(e) { console.error("pollScenario:", e); }
}

async function fetchLatestSnapshot() {
  // 读文本框 → 兑底 _monitorJobId → 默认 A5/logs
  const jobId = getActiveJobId();
  const url = jobId ? "/api/logs?job_id=" + encodeURIComponent(jobId) : "/api/logs";
  try {
    const r = await fetch(url);
    const d = await r.json();
    const snaps = d.snapshots || [];
    if (snaps.length === 0) return;
    const latest = snaps[snaps.length - 1];
    const fr = await fetch("/api/logs/" + encodeURIComponent(latest) +
                          (jobId ? "?job_id=" + encodeURIComponent(jobId) : ""));
    if (!fr.ok) return;
    const sd = await fr.json();
    if (!sd.snapshots || !sd.snapshots[0]) return;
    renderSnapshot(sd.snapshots[0]);
  } catch(e) { console.error("fetchLatestSnapshot:", e); }
}

function renderSnapshot(snap) {
  const cv = snap.cv_summary || {};
  let ppeHtml = "";
  for (const [pid, info] of Object.entries(WORKERS)) {
    const w = info;
    const cvInfo = cv[pid];
    if (!cvInfo) {
      ppeHtml += "<div class=\\"row\\"><span>" + pid + "(" + w.name + "," + w.role + ")</span><span class=\\"warn\\">无数据</span></div>";
      continue;
    }
    const hOk = cvInfo.helmet_missing_ratio < 0.8;
    const hR  = (cvInfo.helmet_missing_ratio * 100).toFixed(0);
    const gOk = cvInfo.goggles_missing_ratio < 0.8;
    const gR  = ((cvInfo.goggles_missing_ratio || 0) * 100).toFixed(0);
    const sOk = cvInfo.suit_missing_ratio < 0.8;
    const sR  = ((cvInfo.suit_missing_ratio || 0) * 100).toFixed(0);
    ppeHtml += "<div class=\\"row\\">" +
      "<span>" + pid + "(" + w.name + ")</span>" +
      "<span>头盔:<span class=\\"" + (hOk?"ok":"no") + "\\">" + (hOk?"OK":"NO("+hR+"%)") + "</span> " +
      "<span>护目镜:<span class=\\"" + (gOk?"ok":"no") + "\\">" + (gOk?"OK":"NO("+gR+"%)") + "</span> " +
      "<span>防护服:<span class=\\"" + (sOk?"ok":"no") + "\\">" + (sOk?"OK":"NO("+sR+"%)") + "</span>" +
      "</div>";
  }
  document.getElementById("ppeList").innerHTML = ppeHtml || "<div class=empty>无</div>";

  const ss = snap.sensor_summary || {};
  let sHtml = "";
  for (const [sid, r] of Object.entries(ss)) {
    const cls = r.status === "alarm" ? "no" : r.status === "warning" ? "warn" : "ok";
    sHtml += "<div class=\\"row\\"><span>" + sid + "</span><span class=\\"" + cls + "\\">" + r.status + "</span></div>";
  }
  document.getElementById("sensorList").innerHTML = sHtml || "<div class=empty>无</div>";

  const ps = snap.position_summary || {};
  let posHtml = "";
  for (const [wid, p] of Object.entries(ps)) {
    const zone = p.in_danger_zone ? "危险区" : "普通区";
    const cls = p.in_danger_zone ? "ok" : "warn";
    const w = WORKERS[wid] || { name: wid };
    posHtml += "<div class=\\"row\\"><span>" + w.name + "(" + wid + ")</span><span class=\\"" + cls + "\\">" + p.area_id + "(" + zone + ")</span></div>";
  }
  document.getElementById("positionList").innerHTML = posHtml || "<div class=empty>无</div>";
}

async function pollAgent() {
  await fetchAgentStatus();
}

async function fetchAgentStatus() {
  try {
    // 跟随 per-job 模式：读 data/jobs/{job_id}/P6/ 处理队列
    const jobId = getActiveJobId();
    const url = jobId ? "/api/agent/status?job_id=" + encodeURIComponent(jobId) : "/api/agent/status";
    const r = await fetch(url);
    const d = await r.json();
    _agentRunning = d.running;
    document.getElementById("btnAgentStart").disabled = d.running;
    document.getElementById("btnAgentStop").disabled  = !d.running;
    document.getElementById("agentStatus").textContent =
      d.running ? ("运行中" + (d.job_id ? " (job=" + d.job_id + ")" : " (全局)")) : "已停止";
    const s = d.stats || {};
    document.getElementById("statTotal").textContent  = s.total || 0;
    document.getElementById("statDone").textContent   = s.done || 0;
    document.getElementById("statError").textContent  = s.error || 0;
    document.getElementById("queueStats").textContent = "批次 " + (s.total||0) + " | 已完成 " + (s.done||0) + " 条 | 失败 " + (s.error||0) + " 条";
    renderQueue(d.pending || {});
  } catch(e) { console.error("poll agent:", e); }
}

function renderQueue(pending) {
  const items = Object.entries(pending).sort();
  const active = items.filter(([, info]) => {
    const st = info.status || "processing";
    return st === "processing" || st === "retryable";
  });
  if (active.length === 0) {
    document.getElementById("queueList").innerHTML = "<div class=empty>无处理中任务</div>";
    return;
  }
  let html = "";
  for (const [batch_key, info] of active) {
    const wts = info.wall_times || [];
    const count = info.count || wts.length;
    const first_wt = wts[0] || batch_key;
    const wallTime = first_wt.replace(/-/g,":").replace(/_/g,".");
    const status = info.status || "processing";
    const clsMap = { processing: "processing", retryable: "processing" };
    const iconMap = { processing: "🔄", retryable: "🔄" };
    const labelMap = { processing: "处理中", retryable: "重试中" };
    const cls = clsMap[status] || "processing";
    const icon = iconMap[status] || "🔄";
    const label = labelMap[status] || "处理中";
    html += "<div class=\\"queue-item " + cls + "\\">" +
      "<span class=\\"qicon\\">" + icon + "</span>" +
      "<span class=\\"qkey\\">" + wallTime.slice(11,23) + " (" + count + "条)</span>" +
      "<span class=\\"qms\\">" + label + "</span>" +
      "</div>";
  }
  document.getElementById("queueList").innerHTML = html;
}

async function fetchRawEvents() {
  // 读文本框 → 兑底 _monitorJobId → 默认 A5/logs
  const jobId = getActiveJobId();
  const url = jobId ? "/api/agent/events?job_id=" + encodeURIComponent(jobId) : "/api/agent/events";
  try {
    const r = await fetch(url);
    if (!r.ok) return;
    const d = await r.json();
    const events = d.events || [];
    if (events.length === 0) {
      document.getElementById("eventList").innerHTML = "<div class=empty>暂无事件</div>";
      document.getElementById("evCount").textContent = "";
      return;
    }
    let html = "";
    for (const ev of events) {
      const pid = ev.person?.id || "?";
      const w = WORKERS[pid] || { name: pid, role: "" };
      const closed = ev.status === "closed";
      html += "<div class=\\"event-item " + (closed ? "closed" : "") + "\\">" +
        "<div class=\\"ev-head\\">" +
          "<span class=\\"ev-type\\">" + (ev.type || "未知") + "</span>" +
          "<span class=\\"ev-time\\">" + (ev.wall_time || ev._source_file || "") + "</span>" +
        "</div>" +
        "<div class=\\"ev-person\\">" + w.name + "(" + w.role + ")</div>" +
        (ev.explanation ? "<div class=\\"ev-exp\\">" + ev.explanation + "</div>" : "") +
        (closed ? "<div class=\\"ev-closed\\">✓ 已关闭</div>" : "") +
        "</div>";
    }
    document.getElementById("eventList").innerHTML = html;
    document.getElementById("evCount").textContent = "共 " + events.length + " 个事件";
  } catch(e) { console.error("fetchRawEvents:", e); }
}

async function startScenario() {
  const scenario = document.getElementById("selScenario").value;
  // 跟随 per-job 模式：读文本框优先 → 兑底 _monitorJobId → 都没有走全局 A5/logs
  const jobId = getActiveJobId();
  try {
    const r = await fetch("/api/scenario/start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ scenario, job_id: jobId }),
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    _scenarioRunning = true;
    document.getElementById("btnPlay").disabled = true;
    document.getElementById("btnStop").disabled = false;
    document.getElementById("statusText").textContent =
      "启动中... " + d.scenario + (d.job_id ? " (job=" + d.job_id + ")" : " (全局)");
    startScenarioPoll();
  } catch(e) { alert("启动失败: " + e); }
}

async function stopScenario() {
  try {
    await fetch("/api/scenario/stop", {method:"POST"});
    _scenarioRunning = false;
    document.getElementById("btnPlay").disabled = false;
    document.getElementById("btnStop").disabled = true;
    document.getElementById("statusText").textContent = "已停止";
    stopScenarioPoll();
  } catch(e) { alert("停止失败: " + e); }
}

async function clearLogs() {
  // 优先读文本框（用户当前关注哪个 job_id 就清哪个），兑底用 _monitorJobId
  const jobId = getActiveJobId();
  if (!jobId) {
    alert("请先在「per-job 监测」面板启动一个 job（点击「🚀 开始监测」）");
    return;
  }
  if (!confirm("确定清理 data/jobs/" + jobId + "/P6 下所有日志文件？")) return;
  try {
    const r = await fetch("/api/logs/clear", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({job_id: jobId}),
    });
    const d = await r.json();
    document.getElementById("statusText").textContent = "已清理 " + d.removed + " 个文件 (job=" + d.job_id + ")";
    await fetchLogCounts();
    await fetchRawEvents();
  } catch(e) { alert("清理失败: " + e); }
}

async function startAgent() {
  const interval = parseInt(document.getElementById("selInterval").value);
  const batch    = parseInt(document.getElementById("selBatch").value);
  // 跟随 per-job 模式：读文本框优先 → 兑底 _monitorJobId → 都没有走全局 A5/logs
  const jobId = getActiveJobId();
  try {
    const r = await fetch("/api/agent/start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ interval_sec: interval, batch_size: batch, job_id: jobId }),
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    _agentRunning = true;
    document.getElementById("btnAgentStart").disabled = true;
    document.getElementById("btnAgentStop").disabled  = false;
    document.getElementById("agentStatus").textContent =
      "运行中" + (d.job_id ? " (job=" + d.job_id + ")" : " (全局)");
    startAgentPoll();
  } catch(e) { alert("Agent启动失败: " + e); }
}

async function stopAgent() {
  try {
    await fetch("/api/agent/stop", {method:"POST"});
    _agentRunning = false;
    document.getElementById("btnAgentStart").disabled = false;
    document.getElementById("btnAgentStop").disabled  = true;
    document.getElementById("agentStatus").textContent = "已停止";
    stopAgentPoll();
  } catch(e) { alert("Agent停止失败: " + e); }
}

// ── per-job 监测（2026-08-19 新增） ──────────────────────────────────────────
let _monitorJobId = "";
let _monitorPollTimer = null;

async function startMonitor() {
  const jobId = document.getElementById("txtJobId").value.trim();
  const scenario = document.getElementById("selScenarioJob").value;
  // 跟随 selInterval / selBatch（之前硬编码 2 / 10 → 前端下拉框失效）
  const interval = parseInt(document.getElementById("selInterval").value);
  const batch    = parseInt(document.getElementById("selBatch").value);
  if (!jobId) { alert("请输入 job_id（17 位时间戳，例: 20260819154312029）"); return; }
  if (!/^\d{17}$/.test(jobId)) {
    alert("job_id 格式错误，应为 17 位数字（YYYYMMDDHHMMSS + 3位随机）");
    return;
  }
  _monitorJobId = jobId;
  try {
    const r = await fetch("/api/monitor/start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        job_id: jobId,
        scenario: scenario,
        duration_sec: 30,
        interval_sec: interval,
        batch_size: batch,
        play_delay_sec: 5,
      }),
    });
    const d = await r.json();
    if (d.status === "ok") {
      // ★ 启动时锁定 job_id 文本框 + 禁用 "开始监测" 按钮
      //   防止用户在 5s 播放 + 30s 收集期间修改 job_id 造成数据源不一致
      //   终态（completed/cancelled/error）或 stop 后会解锁
      document.getElementById("txtJobId").disabled = true;
      document.getElementById("btnMonitorStart").disabled = true;
      document.getElementById("btnMonitorStop").disabled  = false;
      document.getElementById("monitorStatus").textContent =
        `运行中（5秒后播放场景 ${d.scenario}）`;
      // 共享给 /a6 页面（A6 看板前端从 localStorage 读取）
      localStorage.setItem("p6_active_job_id", d.job_id);
      addLog(`✅ monitor_start 成功: job_id=${d.job_id}, p6_dir=${d.p6_dir}`);
      addLog(`▶️ 5 秒后开始播放 mock 数据...`);
      startMonitorPoll();
      // _monitorJobId 已在 startMonitor 入口处设为 jobId；4 类 UI 轮询在 page load 已启，
      // 下一次 pollActiveJobs 会同步确认 → 切到 per-job dir 渲染
      _monitorJobId = jobId;
      try { localStorage.setItem("p6_active_job_id", jobId); } catch(e) {}
    } else {
      addLog(`❌ monitor_start 失败: ${d.error}`);
      alert(d.error || "monitor_start 失败");
    }
  } catch(e) {
    addLog(`❌ monitor_start 异常: ${e}`);
    alert("monitor_start 异常: " + e);
  }
}

async function stopMonitor() {
  if (!_monitorJobId) { alert("没有运行中的 job_id"); return; }
  try {
    const r = await fetch("/api/monitor/stop", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ job_id: _monitorJobId }),
    });
    const d = await r.json();
    addLog(`■ monitor_stop: ${d.status} (job_id=${d.job_id}, final=${d.final_status || d.note})`);
    // ★ 解锁 job_id 文本框 + 启用 "开始监测" 按钮
    //   5 类 UI 轮询继续以文本框（= _monitorJobId）作为 job_id 源，不会停
    document.getElementById("txtJobId").disabled = false;
    document.getElementById("btnMonitorStart").disabled = false;
    document.getElementById("btnMonitorStop").disabled  = true;
    document.getElementById("monitorStatus").textContent = "已停止";
    stopMonitorPoll();
    localStorage.removeItem("p6_active_job_id");
    // 不清空 _monitorJobId：作为下行兑底，用户清空文本框后仍能查这个 job_id
  } catch(e) {
    addLog(`❌ monitor_stop 异常: ${e}`);
    alert("monitor_stop 异常: " + e);
  }
}

function startMonitorPoll() {
  if (_monitorPollTimer) clearInterval(_monitorPollTimer);
  _monitorPollTimer = setInterval(async () => {
    if (!_monitorJobId) { stopMonitorPoll(); return; }
    try {
      const r = await fetch(`/api/monitor/status?job_id=${encodeURIComponent(_monitorJobId)}`);
      const d = await r.json();
      if (d.status === "ok" && d.job_state) {
        const s = d.job_state;
        const label = `status=${s.status}` + (s.scenario ? `, 场景=${s.scenario}` : "");
        document.getElementById("monitorStatus").textContent = label;
        if (["completed", "cancelled", "error"].includes(s.status)) {
          addLog(`📋 monitor 终态: job_id=${_monitorJobId}, status=${s.status}`);
          // ★ 终态解锁：解除文本框锁定 + 启用 "开始监测" 按钮
          //   但 _monitorJobId 保留（兑底用）→ 5 类 UI 轮询继续读这个 job_id
          //   轮询不停止：用户可改文本框切换到别的 job_id，或保持查看当前完成数据
          document.getElementById("txtJobId").disabled = false;
          document.getElementById("btnMonitorStart").disabled = false;
          document.getElementById("btnMonitorStop").disabled  = true;
          // 不调用 stopMonitorPoll()：继续每 2s 探活，万一后端还有别的 alive job
          // （目前不会，但保留探活机制便于未来多并发）
        }
      }
    } catch(e) { /* 静默 */ }
  }, 2000);
}

function stopMonitorPoll() {
  if (_monitorPollTimer) { clearInterval(_monitorPollTimer); _monitorPollTimer = null; }
}

// 页面加载时自动恢复 active job 状态（解决"刷新页面就看不到 PPE/传感器/位置数据"问题）
// 流程：
//   1) 从 localStorage 读上次记录的 active job_id
//   2) 调 /api/monitor/status 确认该 job 还在 _active_jobs 中（status != null/completed/cancelled/error）
//   3) 活着：恢复 _monitorJobId + 启动 pollScenario/pollLog/pollMonitor
//   4) 死了/读不到：清 localStorage，保持空 job_id（按全局 A5/logs 显示）
async function initMonitorOnLoad() {
  let saved = "";
  try { saved = localStorage.getItem("p6_active_job_id") || ""; } catch(e) {}
  if (!saved) return;

  try {
    const r = await fetch("/api/monitor/status?job_id=" + encodeURIComponent(saved));
    const d = await r.json();
    const s = (d.status === "ok") ? d.job_state : null;
    if (s && !["completed", "cancelled", "error"].includes(s.status)) {
      _monitorJobId = saved;
      console.log("[init] 恢复 active job_id=" + saved + ", status=" + s.status);
      // 启动 per-job 模式必需的全部轮询
      startLogPoll();          // 文件计数
      startScenarioPoll();     // snapshot 渲染（PPE/传感器/位置）—— 关键
      startMonitorPoll();      // monitor 状态文本
    } else {
      console.log("[init] job_id=" + saved + " 已不在 active，清掉 localStorage");
      try { localStorage.removeItem("p6_active_job_id"); } catch(e) {}
    }
  } catch(e) {
    console.error("[init] 恢复 active job 失败:", e);
  }
}

// 每 1 秒探活后端 _active_jobs → 仅同步 localStorage + 兜底 _monitorJobId
//   - 5 类轮询现在直接读文本框 txtJobId.value（getActiveJobId），不再依赖这里
//   - 本函数只保留两个作用：
//     ① 用户清空文本框后，仍能通过 _monitorJobId 兑底获取最后一次的有效 job_id
//     ② 同步 localStorage 给 /a6 共享面板使用
//   - 后端 alive job 变化时，同步 _monitorJobId（仅当文本框为空时）
let _activeJobsTimer = null;
async function pollActiveJobs() {
  try {
    const r = await fetch("/api/monitor/status");
    if (!r.ok) return;
    const d = await r.json();
    const aliveJobs = (d.jobs || []).filter(j =>
      ["starting", "agent_running", "playing"].includes(j.status));
    if (aliveJobs.length === 0) {
      // 后端无 alive job → _monitorJobId 保留（兑底用，文本框已被清空时仍能查）
      // 不清空 _monitorJobId，不删除 localStorage（让 /a6 看板能继续查看历史 job）
      return;
    }
    // 后端有 alive job → 仅当 _monitorJobId 尚未记录时同步一次（兑底）
    // 文本框为主：用户已输入的值不会被这里覆盖
    if (!_monitorJobId) {
      const newJobId = aliveJobs[0].job_id;
      _monitorJobId = newJobId;
      console.log("[pollActiveJobs] 兑底 _monitorJobId=" + newJobId);
      try { localStorage.setItem("p6_active_job_id", newJobId); } catch(e) {}
    }
  } catch(e) {
    console.error("[pollActiveJobs] 探活失败:", e);
  }
}
function startActiveJobsPoll() {
  if (_activeJobsTimer) return;
  _activeJobsTimer = setInterval(pollActiveJobs, 1000);
  // 立即跑一次，page load 后不需要等 1s
  pollActiveJobs();
}
function stopActiveJobsPoll() {
  if (_activeJobsTimer) { clearInterval(_activeJobsTimer); _activeJobsTimer = null; }
}

async function loadRules() {
  const r = await fetch("/api/rules");
  const d = await r.json();
  document.getElementById("rulesEditor").value = d.rules || "";
}
async function saveRules() {
  const text = document.getElementById("rulesEditor").value;
  await fetch("/api/rules", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({rules:text})});
  document.getElementById("statusText").textContent = "规则已保存";
}
async function resetRules() {
  await fetch("/api/rules/reset", {method:"POST"});
  loadRules();
  document.getElementById("statusText").textContent = "规则已重置";
}

async function loadSystemPrompt() {
  const r = await fetch("/api/system_prompt");
  const d = await r.json();
  document.getElementById("spEditor").value = d.system_prompt || "";
}
async function saveSystemPrompt() {
  const text = document.getElementById("spEditor").value;
  await fetch("/api/system_prompt", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({system_prompt:text})});
  document.getElementById("statusText").textContent = "系统提示词已保存";
}
async function resetSystemPrompt() {
  await fetch("/api/system_prompt/reset", {method:"POST"});
  loadSystemPrompt();
  document.getElementById("statusText").textContent = "系统提示词已重置";
}

loadRules();
loadSystemPrompt();
// ★ 5 类 UI 轮询在 P6 服务启动后就一直运行（用户要求，不依赖任何按钮/条件）
//   - startLogPoll       → 日志文件状态 + 报警事件（fetchLogCounts + fetchRawEvents）
//   - startScenarioPoll  → snapshot 渲染 PPE/传感器/位置（fetchLatestSnapshot）
//   - startAgentPoll     → 处理队列 + Agent 进度（fetchAgentStatus）
//   - startActiveJobsPoll→ 仅同步 _monitorJobId（兑底给文本框为空时使用）+ 同步 localStorage
startLogPoll();
startScenarioPoll();
startAgentPoll();
startActiveJobsPoll();
</script>
</body>
</html>
"""


# ── 场景定义 A-E ─────────────────────────────────────────────────────────────
SCENARIOS = ["A", "B", "C", "D", "E"]


# ============================================================
# 全局状态
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
        self.scenario_job_id: str = ""    # 当前 scenario 关联的 job_id（per-job 模式）
        self.agent_job_id: str = ""       # 当前 agent 关联的 job_id（per-job 模式）
        self._start_wall: Optional[datetime] = None

    def reset(self):
        self.scenario_task = None
        self.agent_task = None
        self.collector = None
        self.scenario = "B"
        self.log_dir = ""
        self.agent_running = False
        self.scenario_running = False
        self.scenario_job_id = ""
        self.agent_job_id = ""
        self._start_wall = None


state = GlobalState()


# ── LangChain 工具兼容桩（供 main_agent.py 调用）────────────────────────────
# main_agent.py 通过 from .p6_monitor_agent import monitor_start, monitor_events 调用
# 实际 A5/A6 逻辑通过 FastAPI 独立运行，不依赖这些桩

from langchain_core.tools import tool


@tool
def monitor_start(task_id: str) -> str:
    """启动 A5 监测会话。供 main_agent P6 阶段调用。"""
    import json, uuid
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    return json.dumps({"status": "started", "result": {"session_id": session_id}})


@tool
def monitor_events(task_id: str) -> str:
    """获取 A5 监测事件列表。供 main_agent P6 阶段调用。"""
    # 返回空事件列表（实际事件通过 FastAPI 轮询获取）
    return ""


# A6 单例 / A6 路由已迁移至 agents/p7_risk_agent.py，
# 由本文件通过 register_a6_routes(app) 挂载到共享端口 5002。


# ============================================================
# 辅助函数
# ============================================================

def _sanitize_wall_time(wall_time: str) -> str:
    return wall_time.replace(":", "-").replace(".", "_")


def _get_status_by_walltime(log_dir: Path, wall_time: str) -> str:
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


def _submit_batch(log_dir: Path, wall_times: list, batch_key: str):
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
    json.dump(result, open(log_dir / f"raw_event_{key}.json", "w",
                           encoding="utf-8"), ensure_ascii=False, indent=2)


async def _on_error(log_dir: Path, wall_time: str, error: Exception, current_retry: int):
    key = _sanitize_wall_time(wall_time)
    json.dump({
        "error": str(error),
        "failed_at": datetime.now().isoformat(timespec="milliseconds"),
        "retry": current_retry + 1,
    }, open(log_dir / f"error_{key}.json", "w",
            encoding="utf-8"), ensure_ascii=False, indent=2)


def _get_all_batches(log_dir: Path) -> dict:
    result = {}
    for f in log_dir.iterdir():
        if f.is_file() and f.name.startswith("processing_batch_"):
            key = f.name[len("processing_batch_"):-5]
            try:
                data = json.load(open(f, encoding="utf-8"))
                result[key] = {"status": "processing", "wall_times": data.get("wall_times", [])}
            except Exception:
                result[key] = {"status": "processing", "wall_times": []}
    for f in log_dir.iterdir():
        if f.is_file() and f.name.startswith("raw_event_"):
            key = f.name[len("raw_event_"):-5]
            if key in result:
                result[key]["status"] = "done"
            batch_file = log_dir / f"processing_batch_{key}.json"
            if batch_file.exists():
                batch_file.unlink()
    for f in log_dir.iterdir():
        if f.is_file() and f.name.startswith("error_"):
            key = f.name[len("error_"):-5]
            if key not in result:
                result[key] = {"status": "error", "wall_times": [key.replace("-", ":").replace("_", ".")]}
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
# A6 研判触发已迁移至 agents/p7_risk_agent.py::trigger_a6_assessment
# 本文件由 `from .p7_risk_agent import trigger_a6_assessment` 直接调用
# ============================================================


# ============================================================
# 内部协程：数据播放（循环1）
# ============================================================

async def _run_data_play(scenario: str, log_dir: str):
    collector = AsyncCollector(scenario, log_dir=log_dir)
    state.collector = collector

    await collector.start()

    for sec in range(30):
        await asyncio.sleep(1.0)
        try:
            await collector.flush_second(sec)
        except asyncio.CancelledError:
            break

        if not state.scenario_running:
            break

    state.scenario_running = False
    try:
        await collector.stop()
    except Exception:
        pass


# ============================================================
# 内部协程：Agent 轮询（循环2）
# ============================================================

async def _run_agent_loop(log_dir: str, interval_sec: int, batch_size: int,
                        agent: Optional[A5Agent] = None,
                        running_predicate=None):
    """Agent 轮询循环。

    Args:
        log_dir: 读 snapshot + 写 raw_event 路径
        interval_sec: 轮询间隔
        batch_size: 每批最多 wall_time 数
        agent: 复用 A5Agent 实例（per-job 模式传入，避免重复构造 LLM）
        running_predicate: callable() -> bool；返回 False 时退出循环。
            默认 lambda: state.agent_running（兼容旧 /api/agent/start）；
            per-job 模式传 lambda: True（只要 job 没 cancel 就持续跑）。
    """
    if agent is None:
        # 兼容旧调用：不传 agent 时 new 一个全局 A5Agent（raw_event 写到 LOG_DIR）
        agent = A5Agent(collector=None, work_permit=WORK_PERMIT, raw_event_dir=log_dir)
    if running_predicate is None:
        running_predicate = lambda: state.agent_running
    log_path = Path(log_dir)
    pending_tasks: list = []
    inflight_wts: set = set()

    def _load_inflight():
        inflight_wts.clear()
        for f in log_path.glob("processing_batch_*.json"):
            try:
                data = json.load(open(f, encoding="utf-8"))
                for wt in data.get("wall_times", []):
                    inflight_wts.add(wt)
            except Exception:
                pass

    def _cleanup_done_tasks():
        for t in pending_tasks:
            if t.done():
                try:
                    result = t.result()
                    for wt, _ in (result or []):
                        inflight_wts.discard(wt)
                except Exception:
                    pass
                pending_tasks.remove(t)

    while running_predicate():
        _load_inflight()
        _cleanup_done_tasks()

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

        if unprocessed:
            batch = sorted(unprocessed)[:batch_size]
            wall_times = [wt for wt, _ in batch]
            batch_key = _sanitize_wall_time(wall_times[0])
            _submit_batch(log_path, wall_times, batch_key)
            for wt in wall_times:
                inflight_wts.add(wt)

            snapshots_data = []
            for wt, sf in batch:
                try:
                    data = json.load(open(sf, encoding="utf-8"))
                    snap = data.get("snapshots", [{}])[0] if data.get("snapshots") else {}
                    snapshots_data.append(snap)
                except Exception:
                    snapshots_data.append({})

            # ★ 调度异步 batch 处理（fire-and-forget；不阻塞 polling loop）
            pending_tasks.append(asyncio.create_task(_process_batch(batch_key, wall_times, snapshots_data)))

        ###############################################################################
        # ⚠️⚠️⚠️  P6 → P7 调用核心段（_process_batch） ⚠️⚠️⚠️
        # -----------------------------------------------------------------------------#
        # 下面这个内嵌协程是 P6（监测）调 P7（风险研判）的【唯一入口】。
        # A5 抽取到事件后，会从这里异步把数据丢给 P7 的 A6Agent 做风险研判。
        #
        # 📌 调用链一览（请按行号阅读）：
        #   921~925  准备与 A5 批量处理
        #              - items   = [(wall_time_1, snap_1), (wall_time_2, snap_2), ...]
        #              - results = await agent.tick_batch(items)
        #                  └─ A5Agent 在一次 LLM 调用里同时抽多个 wall_time 的事件
        #              - _remove_batch(log_path, bk)
        #                  └─ 删除 processing_batch_<bk>.json（解除"处理中"标记，
        #                     _run_agent_loop 下次轮询不会再把这个 batch 拉起来）
        #
        #   927~930  逐个 wall_time 收尾
        #              - inflight_wts.discard(wt)
        #                  └─ 从 in-flight 集合里移除（避免下次轮询重复处理）
        #              - result = results.get(wt, {...})
        #                  └─ 防御性 fallback：万一 A5 没产出这个 wt 的 result
        #              - await _on_success(log_path, wt, result)
        #                  └─ 写 A5/logs/result_<wt>.json（前端轮询用）+ 推 WebSocket
        #
        #   931~934  🚨【关键】触发 P7 A6 风险研判 🚨
        #              - if result.get("events"):
        #                  └─ 过滤：只有"抽到了告警事件"的 wt 才触发研判
        #                     没有事件的空 batch 不浪费 P7 算力
        #              - asyncio.create_task(trigger_a6_assessment(wt, result))
        #                  └─ 🔑 三个核心要点：
        #                      ① **in-process 调用**（不是 HTTP）
        #                         P6 和 P7 共享同一个 FastAPI app + 同一个事件循环
        #                         走 HTTP 等于绕 localhost，徒增延迟和故障点
        #                         所以 P7 暴露 `trigger_a6_assessment` 这个 async 函数
        #                         给 P6 直接 await（见 p7_risk_agent.py:284）
        #                      ② **fire-and-forget**（不 await 当前 task）
        #                         create_task 立即返回，不阻塞 P6 主循环
        #                         下一批 snapshot 可以立刻继续处理
        #                      ③ **每个 wall_time 触发一次**
        #                         与 A5 批量输出对齐：1 个 wall_time → 1 次研判
        #
        #   935~939  异常分支：整批失败时的清理
        #              - _remove_batch(log_path, bk)
        #                  └─ 同样要删 processing_batch 标记（否则永远卡住）
        #              - 逐个 wt 调 _on_error(...) 写错误日志
        #                  └─ 第 4 个参数 0 表示"无重试次数"（一次失败就放下）
        #
        # 📌 调用关系总结：
        #   P6._process_batch ──(create_task)──> P7.trigger_a6_assessment
        #                                        └─> A6Agent.process_event
        #                                            └─> 写 A6/logs/assessments/*.json
        #
        # 📌 与"另一条路径"的区别：
        #   这条是【事件驱动】路径：A5 在监测循环里自动触发
        #   main_agent 在 P7 阶段走【工作流驱动】路径：人工触发 → risk_analyze tool
        #   两条路最终都落到 A6Agent.process_event
        #
        # 📌 改造时务必注意：
        #   1. 不要把 create_task 改成 await — 会把 P6 主循环卡死
        #   2. 不要把 in-process 改成 HTTP — 同进程内绕一圈无意义且脆弱
        #   3. _on_success 必须先于 trigger_a6_assessment — 否则前端看不到事件就先收到研判
        ###############################################################################
        async def _process_batch(bk, wts, snaps):
            try:
                items = list(zip(wts, snaps))
                results = await agent.tick_batch(items)
                _remove_batch(log_path, bk)

                for wt, snap in items:
                    inflight_wts.discard(wt)
                    result = results.get(wt, {"wall_time": wt, "events": []})
                    await _on_success(log_path, wt, result)
                    # 🚨 有告警事件时触发 A6 研判（每个 batch 调用一次，与 A5 批量输出对齐）
                    #    P7 in-process 调用，跳过 HTTP — 详见上方注释块
                    if result.get("events"):
                        # 透传当前 active job_id（per-job 模式）；无 active 时空串 → 回退到全局 A6Agent
                        asyncio.create_task(trigger_a6_assessment(wt, result, job_id=_get_active_job_id()))
            except Exception as e:
                _remove_batch(log_path, bk)
                for wt in wts:
                    inflight_wts.discard(wt)
                    await _on_error(log_path, wt, e, 0)

        # 等待下一轮前先 sleep（让 scenario 有时间写入新 snapshot）
        await asyncio.sleep(interval_sec)

    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    state.agent_running = False


# ============================================================
# FastAPI 路由
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    return Response(
        content=INDEX_HTML,
        media_type="text/html; charset=utf-8"
    )


# ── A6 路由已迁移至 agents/p7_risk_agent.py::register_a6_routes ──
# 启动时在 P6 文件底部统一挂载，保证共享端口 5002。

# 作业票规则 + 系统提示词
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
# per-job 监测任务（2026-08-19 新增；与 P1-P5 job 规范对齐）
# ============================================================

async def _run_data_play_for_job(job_id: str, scenario: str, log_dir: str,
                                  duration_sec: int = 30):
    """per-job 版本：把 cv/sensor/position/snapshot 写到 data/jobs/{job_id}/P6/

    与 _run_data_play（全局路径）相比，仅 log_dir 不同；其他逻辑一致。
    """
    collector = AsyncCollector(scenario=scenario, log_dir=log_dir)
    await collector.start()
    for sec in range(duration_sec):
        await asyncio.sleep(1.0)
        try:
            await collector.flush_second(sec)
        except asyncio.CancelledError:
            break
    await collector.stop()


async def _run_agent_loop_for_job(job_id: str, interval_sec: int = 10,
                                   batch_size: int = 10):
    """per-job 版本：raw_event 写到 data/jobs/{job_id}/P6/

    A5Agent.raw_event_dir 显式指向 per-job 目录；
    其余逻辑复用 _run_agent_loop（trigger_a6_assessment 时透传当前 active job_id）。

    退出条件：_active_jobs[job_id].status in (cancelled/error/completed)
    或 _active_jobs[job_id] 不存在（被强制停掉）。
    """
    p6_dir = get_p6_log_dir(job_id)
    state = _active_jobs.get(job_id)
    if state is None:
        print(f"[P6] _run_agent_loop_for_job: job_id={job_id} 不在 _active_jobs 中，跳过")
        return

    # per-job A5Agent：raw_event 写到 data/jobs/{job_id}/P6/raw_event_*.json
    agent = A5Agent(collector=None, work_permit=WORK_PERMIT, raw_event_dir=p6_dir)

    # per-job 退出条件：job 被 cancel / error / 不在 _active_jobs 中
    #   - "completed" 不算退出：agent_loop 是常驻服务,一个 job 跑完不应该让整个 loop 自杀
    #     应当继续监听 _active_jobs 里其他 job / 后续新建的 job
    #   - 真正的停止信号只有 ① 用户主动 cancel ② job 异常到 error ③ entry 被踢出 _active_jobs
    def _running():
        s = _active_jobs.get(job_id)
        return s is not None and s.get("status") not in ("cancelled", "error")

    await _run_agent_loop(p6_dir, interval_sec=interval_sec, batch_size=batch_size,
                          agent=agent, running_predicate=_running)


async def _stop_one_job_active(job_id: str, state: Dict[str, Any]):
    """强制停掉一个 active job 的所有 task + 清 A6Agent 缓存"""
    if state.get("collector_task") and not state["collector_task"].done():
        state["collector_task"].cancel()
        try:
            await state["collector_task"]
        except (asyncio.CancelledError, Exception):
            pass
    if state.get("agent_task") and not state["agent_task"].done():
        state["agent_task"].cancel()
        try:
            await state["agent_task"]
        except (asyncio.CancelledError, Exception):
            pass
    state["status"] = "cancelled"
    state["ended_at"] = datetime.now(timezone.utc).isoformat()
    # 显式清除 A6Agent 缓存，避免内存泄漏（p7_risk_agent 提供）
    try:
        from agents.p7_risk_agent import clear_a6_agent
        clear_a6_agent(job_id)
    except Exception as exc:
        print(f"[P6] clear_a6_agent({job_id}) 失败: {exc}")


# 场景/数据播放控制
@app.post("/api/scenario/start")
async def start_scenario(body: dict = None):
    await _stop_all()

    scenario = (body or {}).get("scenario", "B") if body else "B"
    job_id = (body or {}).get("job_id", "") if body else ""
    # job_id 传值 → 写到 data/jobs/{job_id}/P6/（per-job 模式）；
    # 不传 → 保留旧行为写 A5/logs（向后兼容调试）
    if job_id:
        if not re.match(r"^\d{17}$", job_id):
            return JSONResponse(content={"error": f"job_id 格式错误: {job_id}"}, status_code=400)
        log_dir = get_p6_log_dir(job_id)
    else:
        log_dir = str(LOG_DIR)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    state.scenario = scenario
    state.log_dir = log_dir
    state.scenario_running = True
    state.scenario_job_id = job_id or ""

    state.scenario_task = asyncio.create_task(_run_data_play(scenario, log_dir))

    return {
        "status": "started",
        "scenario": scenario,
        "job_id": job_id,
        "log_dir": log_dir,
        "duration_sec": 30,
    }


async def _stop_all():
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
async def get_scenario_status(job_id: Optional[str] = None):
    # job_id 传值 → 读 data/jobs/{job_id}/P6/（per-job）；
    # 不传 → 回退 state.log_dir 或 LOG_DIR（向后兼容）
    if job_id:
        log_dir = get_p6_log_dir(job_id)
    else:
        log_dir = state.log_dir or str(LOG_DIR)

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
        lp = Path(log_dir)
        snapshot_count = len([f for f in lp.iterdir() if f.is_file() and f.name.startswith("snapshot_")])
        current_sec = min(snapshot_count, 30)
        cv_count = snapshot_count * 75

    return {
        "running": state.scenario_running,
        "current_second": current_sec,
        "total_seconds": 30,
        "log_dir": log_dir,
        "job_id": job_id or state.scenario_job_id,
        "cv_count": cv_count,
        "log_files": _count_log_files(log_dir),
    }


# Agent 控制
@app.post("/api/agent/start")
async def start_agent(body: dict = None):
    # 跨模式保护：per-job 模式（/api/monitor/start）有 active job 在跑时，
    # 禁止再启全局 agent loop —— 否则两个 loop 会竞争同一 per-job 目录，
    # 导致 batch 切分不均匀（user 2026-08-20 反馈："开始监测"后又点"启动 Agent"
    # 会启 2 个 loop → 5 批不均匀 2/9/9/9/1 而非 3 批 ×10）
    async with _monitor_mutex:
        for active_job_id, active_state in list(_active_jobs.items()):
            if active_state.get("status") in ("starting", "agent_running", "playing"):
                return JSONResponse(
                    content={
                        "error": (
                            f"per-job 模式有 active job {active_job_id} 在跑，"
                            f"请先停止监测（/api/monitor/stop）"
                        )
                    },
                    status_code=400,
                )

    if state.agent_running and state.agent_task:
        return JSONResponse(content={"error": "Agent 已在运行中，请先停止"}, status_code=400)

    interval = (body or {}).get("interval_sec", 10)
    batch_size = (body or {}).get("batch_size", 10)
    job_id = (body or {}).get("job_id", "") if body else ""

    # job_id 传值 → 写到 data/jobs/{job_id}/P6/（per-job）；
    # 不传 → 保留旧行为写 A5/logs（向后兼容）
    if job_id:
        if not re.match(r"^\d{17}$", job_id):
            return JSONResponse(content={"error": f"job_id 格式错误: {job_id}"}, status_code=400)
        log_dir = get_p6_log_dir(job_id)
    else:
        log_dir = str(LOG_DIR)

    state.agent_interval = interval
    state.agent_batch_size = batch_size
    state.agent_running = True
    state.agent_job_id = job_id or ""

    state.agent_task = asyncio.create_task(_run_agent_loop(log_dir, interval, batch_size))

    return {
        "status": "started",
        "interval_sec": interval,
        "batch_size": batch_size,
        "job_id": job_id,
        "log_dir": log_dir,
    }


# ── per-job 监测端点（2026-08-19 新增；与 P1-P5 job 规范对齐）───────────────
class MonitorStartRequest(BaseModel):
    job_id: str
    scenario: str = "B"          # A/B/C/D/E
    duration_sec: int = 30
    interval_sec: int = 10
    batch_size: int = 10
    play_delay_sec: int = 5      # 用户要求：5 秒后播放 mock 数据


class MonitorStopRequest(BaseModel):
    job_id: str


@app.post("/api/monitor/start")
async def monitor_start(req: MonitorStartRequest):
    """前端「开始监测」按钮：等价于「点击开始agent → 5秒后播放mock数据」"""
    print(f"[monitor_start] 进入: job_id={req.job_id}, scenario={req.scenario}, "
          f"play_delay_sec={req.play_delay_sec}")

    # job_id 校验（与 P1-P5 命名一致：YYYYMMDDHHMMSS + 3位随机）
    if not req.job_id or not re.match(r"^\d{17}$", req.job_id):
        return {"status": "error", "error": f"job_id 格式错误，应为 17 位时间戳: {req.job_id}"}

    if req.scenario not in SCENARIOS:
        return {"status": "error", "error": f"scenario 必须是 {SCENARIOS} 之一"}

    # 目录准备（P6/P7 路径完全隔离）
    p6_dir = get_p6_log_dir(req.job_id)
    p7_dir = get_p7_log_dir(req.job_id)

    # 检查并强制停掉所有 active job（用户决策：当前同时只跑一个 active job）
    async with _monitor_mutex:
        for active_job_id, active_state in list(_active_jobs.items()):
            if active_state.get("status") in ("starting", "agent_running", "playing"):
                print(f"[monitor_start] 检测到已有 active job {active_job_id}，强制停掉")
                await _stop_one_job_active(active_job_id, active_state)
                _active_jobs.pop(active_job_id, None)

        # 创建新的 per-job 状态
        state = {
            "scenario": req.scenario,
            "status": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "collector_task": None,
            "agent_task": None,
            "play_delay_sec": req.play_delay_sec,
            "p6_dir": p6_dir,
            "p7_dir": p7_dir,
        }
        _active_jobs[req.job_id] = state

    try:
        # 立即启动 Agent（等价于点击"开始agent"按钮）
        async def run_agent_then_complete():
            try:
                state["status"] = "agent_running"
                await _run_agent_loop_for_job(req.job_id, req.interval_sec, req.batch_size)
                state["status"] = "agent_completed"
            except asyncio.CancelledError:
                state["status"] = "cancelled"
            except Exception as exc:
                import traceback
                traceback.print_exc()
                state["status"] = "error"
                state["error"] = str(exc)

        agent_task = asyncio.create_task(run_agent_then_complete())
        state["agent_task"] = agent_task

        # 后端 asyncio.sleep(play_delay_sec) 后再启动 collector（5秒后播放 mock 数据）
        async def delayed_play_then_complete():
            try:
                await asyncio.sleep(req.play_delay_sec)
                # 中途若有新 job 抢锁，旧 job 不再启动 collector（mutex 在 stop_one 时已置 cancelled）
                if state["status"] == "cancelled":
                    return
                state["status"] = "playing"
                await _run_data_play_for_job(req.job_id, req.scenario, p6_dir,
                                             duration_sec=req.duration_sec)
                if state["status"] not in ("cancelled", "error"):
                    state["status"] = "completed"
            except asyncio.CancelledError:
                state["status"] = "cancelled"
            except Exception as exc:
                import traceback
                traceback.print_exc()
                state["status"] = "error"
                state["error"] = str(exc)
            finally:
                state["ended_at"] = datetime.now(timezone.utc).isoformat()

        collector_task = asyncio.create_task(delayed_play_then_complete())
        state["collector_task"] = collector_task

        print(f"[monitor_start] 响应: job_id={req.job_id}, status={state['status']}, "
              f"p6_dir={p6_dir}, p7_dir={p7_dir}")
        return {
            "status": "ok",
            "job_id": req.job_id,
            "scenario": req.scenario,
            "p6_dir": p6_dir,
            "p7_dir": p7_dir,
            "play_delay_sec": req.play_delay_sec,
            "duration_sec": req.duration_sec,
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": f"internal: {exc}"}


@app.post("/api/monitor/stop")
async def monitor_stop(req: MonitorStopRequest):
    """主动停掉一个 job_id 的监测"""
    print(f"[monitor_stop] 进入: job_id={req.job_id}")
    state = _active_jobs.get(req.job_id)
    if not state:
        return {"status": "ok", "note": "job 未在运行"}
    async with _monitor_mutex:
        await _stop_one_job_active(req.job_id, state)
        _active_jobs.pop(req.job_id, None)
    return {"status": "ok", "job_id": req.job_id, "final_status": state["status"]}


@app.get("/api/monitor/status")
async def monitor_status(job_id: Optional[str] = None):
    """查询当前 active jobs 或指定 job_id 状态"""
    if job_id:
        state = _active_jobs.get(job_id)
        if not state:
            return {"status": "not_found", "job_id": job_id}
        return {
            "status": "ok",
            "job_id": job_id,
            "job_state": {
                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in state.items()
                if k not in ("collector_task", "agent_task")
            },
        }
    # 返回所有 active jobs（去重 sensitive 字段）
    return {
        "status": "ok",
        "active_count": len(_active_jobs),
        "jobs": [
            {"job_id": jid, **{k: v for k, v in st.items() if k not in ("collector_task", "agent_task")}}
            for jid, st in _active_jobs.items()
        ],
    }


@app.post("/api/agent/stop")
async def stop_agent():
    try:
        if state.agent_task:
            state.agent_task.cancel()
            try:
                await state.agent_task
            except (asyncio.CancelledError, Exception):
                pass
    except Exception as e:
        print(f"[agent/stop] error: {e}")
    state.agent_running = False
    state.agent_task = None
    return {"status": "stopped"}


@app.get("/api/agent/status")
async def get_agent_status(job_id: Optional[str] = None):
    # job_id 传值 → 读 data/jobs/{job_id}/P6/（per-job）；
    # 不传 → 回退 LOG_DIR（向后兼容）
    if job_id:
        log_dir = get_p6_log_dir(job_id)
        # per-job 模式：running 标志读 per-job state 而不是全局 state.agent_running
        # （全局标志只对 /api/agent/start 老路径有意义；per-job 用 _active_jobs[job_id].status）
        job_state = _active_jobs.get(job_id, {})
        job_status = job_state.get("status", "")
        running_flag = job_status in ("starting", "agent_running", "playing")
    else:
        log_dir = str(LOG_DIR)
        running_flag = state.agent_running
        job_status = ""
    pending = _get_all_batches(Path(log_dir))
    done = sum(1 for v in pending.values() if v["status"] == "done")
    error = sum(1 for v in pending.values() if v["status"] == "error")
    retryable = sum(1 for v in pending.values() if v["status"] == "retryable")
    processing = sum(1 for v in pending.values() if v["status"] == "processing")
    total = len(pending)

    return {
        "running": running_flag,
        "status": job_status,
        "interval_sec": state.agent_interval,
        "batch_size": state.agent_batch_size,
        "job_id": job_id or state.agent_job_id,
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
async def get_agent_events(job_id: Optional[str] = None):
    """读 A5 raw_event_*.json 列表。

    job_id 传值时读 data/jobs/{job_id}/P6/raw_event_*.json（per-job 路径）；
    不传时回退到 A5/logs（向后兼容 — 页面加载时默认显示全局历史事件）。
    """
    if job_id:
        if not re.match(r"^\d{17}$", job_id):
            return JSONResponse(content={"error": f"job_id 格式错误: {job_id}"}, status_code=400)
        base = Path(get_p6_log_dir(job_id))
    else:
        base = LOG_DIR
    events = []
    for f in sorted(base.glob("raw_event_*.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
            for ev in data.get("events", []):
                ev["_source_file"] = f.name
                events.append(ev)
        except Exception:
            pass
    return {"events": events, "job_id": job_id, "base": str(base)}


# 日志文件读取
@app.get("/api/logs/{path:path}")
async def get_log_file(path: str, job_id: Optional[str] = None):
    """读取单文件（cv/sensor/position/snapshot/raw_event/...）。

    job_id 传值时读 data/jobs/{job_id}/P6/{path}（per-job 路径）；
    不传时回退到 A5/logs（向后兼容 — 页面加载时默认显示全局历史数据）。
    """
    if job_id:
        if not re.match(r"^\d{17}$", job_id):
            return JSONResponse(content={"error": f"job_id 格式错误: {job_id}"}, status_code=400)
        base = get_p6_log_dir(job_id)
    else:
        base = str(LOG_DIR)
    file_path = Path(base) / path
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse(content={"error": f"文件不存在: {path} (base={base})"}, status_code=404)
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/api/logs")
async def list_log_files(job_id: Optional[str] = None):
    """列出日志目录下的文件，按前缀分组。

    job_id 传值时列 data/jobs/{job_id}/P6/（per-job 路径）；
    不传时回退到 A5/logs（向后兼容 — 页面加载时默认显示全局历史数据）。
    """
    if job_id:
        if not re.match(r"^\d{17}$", job_id):
            return JSONResponse(content={"error": f"job_id 格式错误: {job_id}"}, status_code=400)
        base = Path(get_p6_log_dir(job_id))
    else:
        base = LOG_DIR
    base.mkdir(parents=True, exist_ok=True)
    result = {}
    for f in sorted(base.iterdir()):
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
async def clear_logs(body: dict):
    """清空日志目录。

    body.job_id 必传：data/jobs/{job_id}/P6/（per-job 路径）；
    不传直接 400 报错（避免误清全局 A5/logs）。
    """
    job_id = (body or {}).get("job_id", "")
    if not job_id or not re.match(r"^\d{17}$", job_id):
        return JSONResponse(content={"error": "job_id 必传且须为 17 位数字"}, status_code=400)
    base = Path(get_p6_log_dir(job_id))
    base.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in base.iterdir():
        if f.is_file():
            f.unlink()
            count += 1
    return {"status": "cleared", "removed": count, "job_id": job_id, "base": str(base)}


# WebSocket（保留，向后兼容）
@app.websocket("/ws/{scenario}")
async def ws_run(ws: WebSocket, scenario: str):
    await ws.accept()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = f"logs/frontend/{scenario}_{ts}"
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    asyncio.create_task(_run_data_play(scenario, log_dir))
    asyncio.create_task(_run_agent_loop(log_dir, 10, 10))
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


async def _clean_log_test_periodic(interval_sec: int = 300):
    while True:
        await asyncio.sleep(interval_sec)
        try:
            log_test_dir = _ROOT / "log_test"
            if log_test_dir.is_dir():
                for f in log_test_dir.iterdir():
                    f.unlink(missing_ok=True)
        except Exception as e:
            print(f"[log_test cleanup] error: {e}")


# ============================================================
# 核心入口函数（供外部 Job 调用）
# ============================================================

def get_a5_log_dir(job_id: str) -> str:
    """获取 A5 日志目录路径"""
    from agents.workflow.file_utils import get_job_dir
    log_dir = get_job_dir(job_id) + "/a5_logs"
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    return log_dir


def get_p6_log_dir(job_id: str) -> str:
    """获取 P6 日志目录路径：data/jobs/{job_id}/P6

    P6 阶段 A5 流数据 + raw_event 写入此目录；
    与 P1-P5 get_stage_result_path(job_id, "p{n}") 命名风格一致。
    """
    from agents.workflow.file_utils import get_job_dir
    log_dir = get_job_dir(job_id) + "/P6"
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    return log_dir


def get_p7_log_dir(job_id: str) -> str:
    """获取 P7 日志目录路径：data/jobs/{job_id}/P7

    P7 阶段 A6 风险研判结果（a6_*.json）写入此目录（扁平结构）；
    与 P6/P7 per-job 化约定一致。
    """
    from agents.workflow.file_utils import get_job_dir
    log_dir = get_job_dir(job_id) + "/P7"
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    return log_dir


async def run_a5_monitoring(job_id: str, work_permit: dict, duration_sec: int = 30) -> dict:
    """
    运行 A5 完整监测流程（数据播放 + Agent 轮询 + A6 研判）
    """
    import random

    scenario = random.choice(SCENARIOS)
    print(f"[A5] 随机选择场景: {scenario}")

    log_dir = get_a5_log_dir(job_id)
    print(f"[A5] 日志目录: {log_dir}")

    collector = AsyncCollector(scenario=scenario, log_dir=log_dir)

    async def run_collector():
        await collector.start()
        for sec in range(duration_sec):
            await asyncio.sleep(1.0)
            try:
                await collector.flush_second(sec)
            except asyncio.CancelledError:
                break
        await collector.stop()

    await asyncio.gather(
        run_collector(),
        _run_agent_loop(log_dir, interval_sec=10, batch_size=10),
    )

    log_path = Path(log_dir)
    raw_events_files = list(log_path.glob("raw_event_*.json"))
    if not raw_events_files:
        raise RuntimeError(
            f"P6 阶段 LLM 未被调用：没有生成 raw_event_*.json 文件。"
            f"请检查 A5_LLM_PROTOCOL/A5_LLM_API_KEY 等配置是否正确。"
        )

    raw_events = []
    for f in sorted(raw_events_files):
        try:
            data = json.load(open(f, encoding="utf-8"))
            raw_events.extend(data.get("events", []))
        except Exception:
            pass

    return {
        "log_dir": log_dir,
        "scenario": scenario,
        "snapshot_count": len(list(log_path.glob("snapshot_*.json"))),
        "raw_event_count": len(raw_events_files),
        "events": raw_events,
    }


def map_a5_events_to_p6(raw_events: list, job_id: str) -> list:
    """将 A5 raw_event 格式映射为 P6 candidate_events 格式"""
    candidate_events = []
    for ev in raw_events:
        candidate_events.append({
            "event_type": "candidate_event",
            "event_id": ev.get("event_id", ""),
            "description": ev.get("explanation", ""),
            "confidence": 0.9,
            "evidence": [{"source": "A5", "type": ev.get("type"), "person": ev.get("person", {})}],
            "severity": "PENDING_A6",
            "timestamp": ev.get("wall_time", ""),
            "type": ev.get("type", ""),
            "person": ev.get("person", {}),
        })
    return candidate_events


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    print(f"A5 作业过程监测 P6 启动: http://localhost:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
