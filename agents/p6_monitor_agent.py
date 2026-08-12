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
import sys
import time
from datetime import datetime
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

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, Response
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

# A6 核心组件
from A6.agent.a6_agent import A6Agent
from A6.agent.tools import A5DataTools, OutputTools
from A6.agent.prompt_manager import PromptManager

# ── FastAPI 应用 ─────────────────────────────────────────────────────────────
app = FastAPI(title="A5 作业过程监测 - P6 Monitor")

# 日志根目录：固定在 A5/logs
LOG_DIR = _ROOT / "A5" / "logs"

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

async function fetchLogCounts() {
  try {
    const r = await fetch("/api/logs");
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
    const r = await fetch("/api/scenario/status");
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
  try {
    const r = await fetch("/api/logs");
    const d = await r.json();
    const snaps = d.snapshots || [];
    if (snaps.length === 0) return;
    const latest = snaps[snaps.length - 1];
    const fr = await fetch("/api/logs/" + encodeURIComponent(latest));
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
    const r = await fetch("/api/agent/status");
    const d = await r.json();
    _agentRunning = d.running;
    document.getElementById("btnAgentStart").disabled = d.running;
    document.getElementById("btnAgentStop").disabled  = !d.running;
    document.getElementById("agentStatus").textContent = d.running ? "运行中" : "已停止";
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
  try {
    const r = await fetch("/api/agent/events");
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
  try {
    const r = await fetch("/api/scenario/start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ scenario }),
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    _scenarioRunning = true;
    document.getElementById("btnPlay").disabled = true;
    document.getElementById("btnStop").disabled = false;
    document.getElementById("statusText").textContent = "启动中... " + d.scenario;
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
  if (!confirm("确定清理 A5/logs 下所有日志文件？")) return;
  try {
    const r = await fetch("/api/logs/clear", {method:"POST"});
    const d = await r.json();
    document.getElementById("statusText").textContent = "已清理 " + d.removed + " 个文件";
    await fetchLogCounts();
    await fetchRawEvents();
  } catch(e) { alert("清理失败: " + e); }
}

async function startAgent() {
  const interval = parseInt(document.getElementById("selInterval").value);
  const batch    = parseInt(document.getElementById("selBatch").value);
  try {
    const r = await fetch("/api/agent/start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ interval_sec: interval, batch_size: batch }),
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    _agentRunning = true;
    document.getElementById("btnAgentStart").disabled = true;
    document.getElementById("btnAgentStop").disabled  = false;
    document.getElementById("agentStatus").textContent = "运行中";
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
startLogPoll();
</script>
</body>
</html>
"""


# ── A6 前端 HTML ────────────────────────────────────────────────────────────
INDEX_A6_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A6 风险研判看板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh}
.header{background:#16213e;padding:16px 24px;border-bottom:1px solid #0f3460;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:20px;color:#e94560}
.header .status{font-size:14px;color:#888}
.tabs{display:flex;background:#16213e;border-bottom:1px solid #0f3460}
.tab-btn{padding:12px 24px;background:none;border:none;color:#888;cursor:pointer;font-size:14px;border-bottom:2px solid transparent}
.tab-btn:hover{color:#e0e0e0}
.tab-btn.active{color:#e94560;border-bottom-color:#e94560}
.tab-content{display:none;padding:24px}
.tab-content.active{display:block}
.card{background:#16213e;border-radius:8px;padding:20px;margin-bottom:20px;border:1px solid #0f3460}
.card-title{font-size:14px;color:#888;margin-bottom:16px;text-transform:uppercase;letter-spacing:1px}
.stats{display:flex;gap:24px;flex-wrap:wrap}
.stat-item{text-align:center}
.stat-value{font-size:36px;font-weight:bold;color:#e94560}
.stat-label{font-size:12px;color:#888;margin-top:4px}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;margin-top:12px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #0f3460}
th{color:#888;font-size:12px;text-transform:uppercase}
tr:hover{background:#1a1a2e}
.badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:bold}
.badge-1{background:#4caf50;color:#fff}
.badge-2{background:#ff9800;color:#fff}
.badge-3{background:#ff5722;color:#fff}
.badge-4{background:#f44336;color:#fff}
.badge-5{background:#9c27b0;color:#fff}
button{padding:6px 16px;border:none;border-radius:4px;cursor:pointer;font-size:13px}
button.primary{background:#e94560;color:#fff}
button.danger{background:#c62828;color:#fff}
button:hover{opacity:0.9}
.empty{color:#555;font-size:14px;padding:40px;text-align:center}
</style>
</head>
<body>
<div class="header">
  <h1>A6 风险研判看板</h1>
  <span class="status" id="totalCount">共 0 条研判</span>
  <button class="btn-red" style="padding:4px 12px;font-size:12px;margin-left:12px" onclick="clearA6Logs()">🗑 清理日志</button>
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="showTab(this,'tab0')">研判列表</button>
  <button class="tab-btn" onclick="showTab(this,'tab1')">提示词配置</button>
</div>

<div id="tab0" class="tab-content active">
  <div class="card">
    <div class="card-title">统计概览</div>
    <div class="stats" id="statsRow">
      <div class="stat-item"><div class="stat-value" id="statTotal">0</div><div class="stat-label">总数</div></div>
      <div class="stat-item"><div class="stat-value" id="statLv1" style="color:#4caf50">0</div><div class="stat-label">轻微</div></div>
      <div class="stat-item"><div class="stat-value" id="statLv2" style="color:#ff9800">0</div><div class="stat-label">一般</div></div>
      <div class="stat-item"><div class="stat-value" id="statLv3" style="color:#ff5722">0</div><div class="stat-label">较重</div></div>
      <div class="stat-item"><div class="stat-value" id="statLv4" style="color:#f44336">0</div><div class="stat-label">严重</div></div>
      <div class="stat-item"><div class="stat-value" id="statLv5" style="color:#9c27b0">0</div><div class="stat-label">危急</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">研判记录</div>
    <div id="assessmentsList"><div class="empty">暂无研判数据，请先启动 A5 场景播放和 Agent</div></div>
  </div>
</div>

<div id="tab1" class="tab-content">
  <div class="card">
    <div class="card-title">风险分级提示词</div>
    <textarea id="riskPrompt" rows="10" style="width:100%;background:#0f3460;color:#e0e0e0;border:1px solid #1a1a2e;border-radius:4px;padding:10px;font-family:monospace;font-size:13px"></textarea>
    <div style="margin-top:10px;display:flex;gap:8px">
      <button class="primary" onclick="savePrompt('risk_classification')">💾 保存</button>
      <button onclick="resetPrompt('risk_classification')">↺ 重置</button>
      <button onclick="loadPrompt('risk_classification')">↻ 刷新</button>
    </div>
  </div>
</div>

<script>
function showTab(btn, id) {
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(id).classList.add('active');
}

let pollTimer = null;

function startPoll() {
  if (pollTimer) return;
  pollTimer = setInterval(loadAssessments, 2000);
}

async function loadAssessments() {
  try {
    const r = await fetch("/api/a6/assessments?limit=100");
    const d = await r.json();
    document.getElementById("totalCount").textContent = "共 " + d.total + " 条研判";
    renderAssessments(d.items || []);
    updateStats(d.items || []);
  } catch(e) { console.error("loadAssessments:", e); }
}

function updateStats(items) {
  const counts = {1:0,2:0,3:0,4:0,5:0};
  items.forEach(it => counts[it.risk_level] = (counts[it.risk_level]||0) + 1);
  document.getElementById("statTotal").textContent = items.length;
  document.getElementById("statLv1").textContent = counts[1];
  document.getElementById("statLv2").textContent = counts[2];
  document.getElementById("statLv3").textContent = counts[3];
  document.getElementById("statLv4").textContent = counts[4];
  document.getElementById("statLv5").textContent = counts[5];
}

function renderAssessments(items) {
  const el = document.getElementById("assessmentsList");
  if (!items.length) { el.innerHTML = '<div class="empty">暂无研判数据</div>'; return; }
  let html = '<div class="table-wrap"><table><thead><tr><th>事件类型</th><th>时间范围</th><th>涉及人员</th><th>风险等级</th><th>风险依据</th><th>建议</th><th>研判时间</th></tr></thead><tbody>';
  items.forEach(it => {
    const lv = it.risk_level || 2;
    const lvname = it.risk_level_name || "一般";
    html += '<tr>' +
      '<td>' + (it.event_type||'') + '</td>' +
      '<td>' + (it.first_seen||'') + ' ~ ' + (it.last_seen||'') + '</td>' +
      '<td>' + ((it.involved_persons||[]).join(', ')) + '</td>' +
      '<td><span class="badge badge-' + lv + '">' + lvname + '(' + lv + '级)</span></td>' +
      '<td>' + (it.risk_basis||'').replace(/\\n/g,'<br>').substring(0,80) + '</td>' +
      '<td>' + ((it.suggestions||[]).slice(0,2).join(', ')) + '</td>' +
      '<td>' + (it.timestamp||'') + '</td>' +
      '</tr>';
  });
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

async function loadPrompt(name) {
  try {
    const r = await fetch("/api/a6/prompts/" + name);
    const d = await r.json();
    const ta = document.getElementById("riskPrompt");
    if (ta && d.content) ta.value = d.content;
  } catch(e) { console.error("loadPrompt:", e); }
}

async function savePrompt(name) {
  const text = document.getElementById("riskPrompt").value;
  try {
    await fetch("/api/a6/prompts/" + name, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({content:text})
    });
    alert("保存成功");
  } catch(e) { alert("保存失败:"+e); }
}

async function resetPrompt(name) {
  try {
    await fetch("/api/a6/prompts/" + name + "/reset", {method:"POST"});
    loadPrompt(name);
  } catch(e) { console.error("resetPrompt:", e); }
}

async function clearA6Logs() {
  if (!confirm("确定清理 A6 所有研判日志？")) return;
  try {
    const r = await fetch("/api/a6/clear_logs", {method:"POST"});
    const d = await r.json();
    alert("已清理 " + d.removed + " 个文件/目录");
    loadAssessments();
  } catch(e) { alert("清理失败: " + e); }
}

loadPrompt("risk_classification");
loadAssessments();
startPoll();
</script>
</body>
</html>
"""


# ── 场景定义 A-E ─────────────────────────────────────────────────────────────
SCENARIOS = ["A", "B", "C", "D", "E"]

# A6 研判 API（自身端口，作为被调用方时使用）
A6_API_BASE = "http://localhost:5002"


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


# ── A6 全局状态 ─────────────────────────────────────────────────────────────
_a6_agent: Optional[A6Agent] = None
_a5_tools = None
_output_tools = None
_prompt_manager = None


def get_a6_agent() -> A6Agent:
    """获取或创建 A6Agent 单例（延迟初始化）"""
    global _a6_agent, _a5_tools, _output_tools, _prompt_manager
    if _a6_agent is None:
        _a5_tools = A5DataTools()
        _output_tools = OutputTools()
        _prompt_manager = PromptManager()
        _a6_agent = A6Agent(
            a5_log_dir="A5/logs",
            a6_output_dir="A6/logs",
        )
    return _a6_agent


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
# A6 研判触发
# ============================================================

# A6 研判 API（自身端口，作为被调用方时使用）
A6_API_BASE = "http://localhost:5002"


async def _trigger_a6_assessment(event_id: str, event_data: dict):
    """
    当 A5 产生告警事件时，触发 A6 进行风险研判。
    通过 HTTP 调用 A6 的手动研判接口，与 frontend/app_a5.py 保持一致。
    """
    if not event_data.get("events"):
        return
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{A6_API_BASE}/api/a6/process/{event_id}",
                json=event_data,
            )
            if response.status_code == 200:
                print(f"[A5→A6] 事件 {event_id} 已触发 A6 研判")
            else:
                print(f"[A5→A6] 事件 {event_id} 触发失败: {response.status_code}")
    except Exception as e:
        import traceback
        print(f"[A5→A6] 事件 {event_id} 调用异常: {type(e).__name__}: {e}")
        traceback.print_exc()


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

async def _run_agent_loop(log_dir: str, interval_sec: int, batch_size: int):
    agent = A5Agent(collector=None, work_permit=WORK_PERMIT, raw_event_dir=log_dir)
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

    while state.agent_running:
        await asyncio.sleep(interval_sec)
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

        if not unprocessed:
            continue

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

        async def _process_batch(bk, wts, snaps):
            try:
                items = list(zip(wts, snaps))
                results = await agent.tick_batch(items)
                _remove_batch(log_path, bk)

                for wt, snap in items:
                    inflight_wts.discard(wt)
                    result = results.get(wt, {"wall_time": wt, "events": []})
                    await _on_success(log_path, wt, result)
                    # 有告警事件时触发 A6 研判（每个 batch 调用一次，与 A5 批量输出对齐）
                    if result.get("events"):
                        asyncio.create_task(_trigger_a6_assessment(wt, result))
            except Exception as e:
                _remove_batch(log_path, bk)
                for wt in wts:
                    inflight_wts.discard(wt)
                    await _on_error(log_path, wt, e, 0)

        pending_tasks.append(asyncio.create_task(_process_batch(batch_key, wall_times, snapshots_data)))

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


@app.get("/a6", response_class=HTMLResponse)
async def a6_page():
    """A6 风险研判看板页面"""
    return Response(
        content=INDEX_A6_HTML,
        media_type="text/html; charset=utf-8"
    )


# ═══════════════════════════════════════════════════════════════════════════
# A6 API 路由（/api/a6/*）
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/a6/assessments")
async def get_assessments(start: str = None, end: str = None, risk_level: int = None, limit: int = 100):
    """获取研判结果列表"""
    from A6.agent.tools import A5DataTools, OutputTools
    from pathlib import Path

    output_dir = Path(str(_ROOT / "A6" / "logs" / "assessments"))
    if not output_dir.exists():
        return {"total": 0, "items": []}

    results = []
    for date_dir in sorted(output_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("a6_*.json"), reverse=True):
            try:
                data = json.load(open(f, encoding="utf-8"))
                if start and data.get("timestamp", "") < start:
                    continue
                if end and data.get("timestamp", "") > end:
                    continue
                if risk_level and data.get("risk_level") != risk_level:
                    continue
                # 自动过滤无风险记录（risk_level=0），前端不展示
                if data.get("risk_level", 0) == 0:
                    continue
                results.append({
                    "a6_event_id": data.get("a6_event_id"),
                    "event_type": data.get("event_type"),
                    "first_seen": data.get("first_seen"),
                    "last_seen": data.get("last_seen"),
                    "duration_sec": data.get("duration_sec", 0),
                    "involved_persons": data.get("involved_persons", []),
                    "risk_level": data.get("risk_level"),
                    "risk_level_name": data.get("risk_level_name"),
                    "risk_basis": data.get("risk_basis"),
                    "suggestions": data.get("suggestions", []),
                    "reasoning": data.get("reasoning"),
                    "timestamp": data.get("timestamp"),
                })
                if len(results) >= limit:
                    break
            except (json.JSONDecodeError, IOError):
                continue
        if len(results) >= limit:
            break
    return {"total": len(results), "items": results}


@app.get("/api/a6/assessments/{a6_event_id}")
async def get_assessment(a6_event_id: str):
    """获取单条研判详情"""
    from pathlib import Path
    output_dir = Path(str(_ROOT / "A6" / "logs" / "assessments"))
    if not output_dir.exists():
        return {"error": "不存在"}
    for date_dir in sorted(output_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("a6_*.json"), reverse=True):
            try:
                data = json.load(open(f, encoding="utf-8"))
                if data.get("a6_event_id") == a6_event_id:
                    return data
            except (json.JSONDecodeError, IOError):
                continue
    return {"error": "未找到"}


@app.get("/api/a6/status")
async def get_a6_status():
    """获取 A6 状态"""
    tools = A5DataTools(a5_log_dir=str(LOG_DIR))
    status = tools._load_status()
    return {
        "event_id_map": status.get("event_id_map", {}),
        "summary": status.get("summary", {}),
    }


@app.get("/api/a6/prompts")
async def get_all_prompts():
    """获取所有提示词"""
    pm = get_a6_agent()
    return _prompt_manager.get_all_prompts() if _prompt_manager else {}


@app.get("/api/a6/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """获取指定提示词"""
    pm = PromptManager()
    prompt = pm.read_prompt(prompt_name)
    if prompt.get("error"):
        return {"error": prompt["error"]}
    return prompt


@app.post("/api/a6/prompts/{prompt_name}")
async def update_prompt(prompt_name: str, body: dict):
    """更新提示词"""
    pm = PromptManager()
    success = pm.update_prompt(prompt_name=prompt_name, new_content=body.get("content", ""), reason=body.get("reason"))
    return {"status": "success" if success else "failed"}


@app.post("/api/a6/prompts/{prompt_name}/save")
async def save_prompt(prompt_name: str):
    """保存提示词到文件"""
    pm = PromptManager()
    success = pm.save_to_file(prompt_name=prompt_name)
    return {"status": "success" if success else "failed", "saved_at": datetime.now().isoformat()}


@app.post("/api/a6/prompts/{prompt_name}/reset")
async def reset_prompt(prompt_name: str):
    """重置提示词为默认值"""
    pm = PromptManager()
    success = pm.reset_to_default(prompt_name)
    return {"status": "success" if success else "failed"}


@app.post("/api/a6/process/{event_id}")
async def process_a6_event(event_id: str, body: Optional[dict] = None):
    """手动触发 A6 研判"""
    import traceback
    try:
        agent = get_a6_agent()
        result = await agent.process_event(event_id, event_data=body)
        return result
    except Exception as e:
        print(f"[A6 API] process_a6_event({event_id}) 异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        return {"status": "error", "message": f"{type(e).__name__}: {e}", "event_id": event_id}


@app.post("/api/a6/clear_logs")
async def clear_a6_logs():
    """清理 A6 日志"""
    import shutil
    log_dir = _ROOT / "A6" / "logs"
    count = 0
    if log_dir.exists():
        for item in log_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    count += 1
            except Exception:
                pass
    return {"status": "cleared", "removed": count}


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


# 场景/数据播放控制
@app.post("/api/scenario/start")
async def start_scenario(body: dict = None):
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
async def get_scenario_status():
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
        "cv_count": cv_count,
        "log_files": _count_log_files(log_dir),
    }


# Agent 控制
@app.post("/api/agent/start")
async def start_agent(body: dict = None):
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
async def get_agent_status():
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
    events = []
    for f in sorted(Path(LOG_DIR).glob("raw_event_*.json")):
        try:
            data = json.load(open(f, encoding="utf-8"))
            for ev in data.get("events", []):
                ev["_source_file"] = f.name
                events.append(ev)
        except Exception:
            pass
    return {"events": events}


# 日志文件读取
@app.get("/api/logs/{path:path}")
async def get_log_file(path: str):
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
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in LOG_DIR.iterdir():
        if f.is_file():
            f.unlink()
            count += 1
    return {"status": "cleared", "removed": count}


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
        _run_agent_loop(log_dir, interval_sec=2, batch_size=10),
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
