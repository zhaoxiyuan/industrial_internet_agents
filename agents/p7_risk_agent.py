"""
P7: 风险研判与分级 - 基于 A6 实现（完整模块）

将原 p6_monitor_agent.py 中的 A6 部分迁移至此：
- A6 Agent 单例管理（get_a6_agent）
- A6 风险研判触发（trigger_a6_assessment，供 P6 agent loop 同步调用）
- A6 前端 HTML（/a6 风险研判看板）
- A6 FastAPI 路由（/api/a6/*，由 P6 启动时通过 register_a6_routes 挂载）
- A6 提示词管理（/api/a6/prompts/*）
- LangChain 工具桩（risk_analyze / risk_list，直接调用 A6Agent）
- CLI 入口（run_risk_agent）

调用方式：
- P6 → P7 in-process 调用 trigger_a6_assessment(event_id, event_data)
- main_agent → P7 同步工具 .invoke() 调 risk_analyze / risk_list
- 浏览器 → 5002/a6 看研判看板，/api/a6/* 看研判 API
"""
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, Dict, List

# ── 项目路径 ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# ── 加载 .env ───────────────────────────────────────────────────────────────
from dotenv import load_dotenv
_env_file = _ROOT / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

from langchain_core.tools import tool

# A6 核心组件
from A6.agent.a6_agent import A6Agent
from A6.agent.tools import A5DataTools, OutputTools
from A6.agent.prompt_manager import PromptManager


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
    // 跟随 P6 monitor 的 active job_id（localStorage 共享，per-job 模式必传）
    const jobId = localStorage.getItem("p6_active_job_id") || "";
    const url = jobId
      ? "/api/a6/assessments?limit=100&job_id=" + encodeURIComponent(jobId)
      : "/api/a6/assessments?limit=100";  // 不传 → 后端兜底回退（兼容旧数据）
    const r = await fetch(url);
    if (r.status === 400) {
      const err = await r.json();
      document.getElementById("totalCount").textContent = err.error || "请先启动 per-job 监测";
      return;
    }
    const d = await r.json();
    document.getElementById("totalCount").textContent =
      "共 " + d.total + " 条研判" + (d.job_id ? " (job=" + d.job_id + ")" : "");
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
  const jobId = localStorage.getItem("p6_active_job_id") || "";
  if (!jobId) { alert("请先在 P6 主页「per-job 监测」面板启动一个 job（点击「🚀 开始监测」）"); return; }
  if (!confirm("确定清理 data/jobs/" + jobId + "/P7 下所有研判文件？")) return;
  try {
    const r = await fetch("/api/a6/clear_logs", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({job_id: jobId}),
    });
    const d = await r.json();
    alert("已清理 " + d.removed + " 个文件/目录 (job=" + d.job_id + ")");
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


# ============================================================
# A6 单例状态（per-job 缓存；2026-08-19 per-job 化改造）
# ============================================================
#
# 改造前：_a6_agent 全局单例，所有 job 共享同一实例 → 数据写到全局 A6/logs/
# 改造后：按 job_id 缓存 Dict[job_id → A6Agent]
#        每个 job_id 独立 a5_log_dir / a6_output_dir；
#        log_test_dir 全局共用 A6/log_test（用户要求 A6/log_test 不变）；
#        多 job 路径（a5_log_dir / a6_output_dir）完全隔离；当前实现只让一个 active job 跑。

_a6_agents: Dict[str, A6Agent] = {}
_a5_tools = None
_output_tools = None
_prompt_manager = None


def get_a6_agent(job_id: str) -> A6Agent:
    """按 job_id 获取或创建 A6Agent 实例（per-job 缓存）。

    Args:
        job_id: 主流程作业 ID（YYYYMMDDHHMMSS + 3位随机数）
                 用于定位 a5_log_dir + a6_output_dir

    Returns:
        该 job_id 对应的 A6Agent 实例（首次调用时创建并缓存）
    """
    if job_id in _a6_agents:
        return _a6_agents[job_id]

    # per-job 路径 —— 复用 p6_monitor_agent 的 helper（保持单一真源）
    from agents.p6_monitor_agent import get_p6_log_dir, get_p7_log_dir
    a5_log_dir = get_p6_log_dir(job_id)              # data/jobs/{job_id}/P6
    a6_output_dir = get_p7_log_dir(job_id)           # data/jobs/{job_id}/P7

    agent = A6Agent(
        a5_log_dir=a5_log_dir,
        a6_output_dir=a6_output_dir,
        # log_test_dir 不传 → A6Agent 默认 A6/log_test（用户要求 A6/log_test 不变）
    )
    _a6_agents[job_id] = agent
    print(
        f"[get_a6_agent] 创建 per-job A6Agent: job_id={job_id}, "
        f"a5_log_dir={a5_log_dir}, a6_output_dir={a6_output_dir}"
    )
    return agent


def clear_a6_agent(job_id: str) -> None:
    """清除 job_id 对应的 A6Agent 缓存（job 停止时调用，避免内存泄漏）"""
    _a6_agents.pop(job_id, None)


def get_a5_tools():
    """获取 A5DataTools 单例（供 P7 路由用）"""
    global _a5_tools
    if _a5_tools is None:
        _a5_tools = A5DataTools()
    return _a5_tools


def get_output_tools():
    """获取 OutputTools 单例"""
    global _output_tools
    if _output_tools is None:
        _output_tools = OutputTools()
    return _output_tools


def get_prompt_manager():
    """获取 PromptManager 单例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager


# ============================================================
# In-process A5→A6 触发器（供 P6 agent loop 调用）
# ============================================================

async def trigger_a6_assessment(event_id: str, event_data: dict, *, job_id: str = ""):
    """
    当 A5 产生告警事件时，in-process 触发 A6 风险研判。

    与原 HTTP 触发等价（避免跨进程/跨网络），由 P6 的 agent loop 直接 await。

    Args:
        event_id: A5 事件 ID
        event_data: 事件详情（含 events[]）
        job_id: 必填（P6 端通过 _active_jobs 当前 active 取）；
                用于定位 per-job 的 a5_log_dir + a6_output_dir
    """
    if not job_id:
        print(f"[A5→A6] 警告：trigger_a6_assessment({event_id}) 缺少 job_id，回退到全局 A6Agent")
    if not event_data or not event_data.get("events"):
        return None
    try:
        agent = get_a6_agent(job_id) if job_id else get_a6_agent_global()
        result = await agent.process_event(event_id, event_data=event_data)
        print(f"[A5→A6] 事件 {event_id} 研判状态: {result.get('status')} (job_id={job_id or 'global'})")
        return result
    except Exception as e:
        import traceback
        print(f"[A5→A6] 事件 {event_id} 调用异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def get_a6_agent_global() -> A6Agent:
    """回退入口：job_id 缺失时使用全局 A6Agent（向后兼容旧调用方）。"""
    if "_global" in _a6_agents:
        return _a6_agents["_global"]
    agent = A6Agent(
        a5_log_dir="A5/logs",
        a6_output_dir="A6/logs",
    )
    _a6_agents["_global"] = agent
    return agent


# ============================================================
# LangChain 工具桩（供 main_agent.py 的 P7 阶段 .invoke() 调用）
# ============================================================

@tool
def risk_analyze(event_id: str) -> str:
    """
    风险分析工具。供 main_agent P7 阶段调用。
    内部直接调用 A6Agent.process_event，返回 JSON 字符串。
    """
    try:
        agent = get_a6_agent_global()  # 工具桩路径：默认全局 A6Agent（向后兼容 main_agent P7 阶段调用）
        try:
            loop = asyncio.get_running_loop()
            # 已在事件循环中 → 起线程跑，避免嵌套 loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, agent.process_event(event_id))
                result = fut.result(timeout=120)
        except RuntimeError:
            # 无运行中的事件循环 → 直接 asyncio.run
            result = asyncio.run(agent.process_event(event_id))

        # 归一化字段：P8 高危筛选靠 e.get("level")
        assessment = result.get("assessment") or {}
        risk_level = assessment.get("risk_level", 0)
        level_map = {1: "LOW", 2: "LOW", 3: "MEDIUM", 4: "HIGH", 5: "CRITICAL"}
        level = level_map.get(risk_level, "LOW")

        return json.dumps({
            "status": result.get("status", "ok"),
            "result": {
                "event_id": event_id,
                "level": level,
                "risk_level": risk_level,
                "risk_level_name": assessment.get("risk_level_name", ""),
                "risk_basis": assessment.get("risk_basis", ""),
                "suggestions": assessment.get("suggestions", []),
                "a6_event_id": assessment.get("a6_event_id", ""),
                "raw_result": result,
            },
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "result": {"event_id": event_id, "level": "LOW", "risk_level": 0},
        }, ensure_ascii=False)


@tool
def risk_list(task_id: str) -> str:
    """风险列表工具。供 main_agent P7 阶段调用。"""
    try:
        output_dir = _ROOT / "A6" / "logs" / "assessments"
        if not output_dir.exists():
            return json.dumps({"status": "ok", "result": {"events": []}}, ensure_ascii=False)

        events = []
        for date_dir in sorted(output_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for f in sorted(date_dir.glob("a6_*.json"), reverse=True):
                try:
                    data = json.load(open(f, encoding="utf-8"))
                    risk_level = data.get("risk_level", 0)
                    level_map = {1: "LOW", 2: "LOW", 3: "MEDIUM", 4: "HIGH", 5: "CRITICAL"}
                    events.append({
                        "event_id": data.get("a6_event_id", ""),
                        "event_type": data.get("event_type", ""),
                        "level": level_map.get(risk_level, "LOW"),
                        "risk_level": risk_level,
                        "risk_level_name": data.get("risk_level_name", ""),
                        "involved_persons": data.get("involved_persons", []),
                        "timestamp": data.get("timestamp", ""),
                    })
                    if len(events) >= 50:
                        break
                except (json.JSONDecodeError, IOError):
                    continue
            if len(events) >= 50:
                break

        return json.dumps({
            "status": "ok",
            "result": {"task_id": task_id, "events": events},
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "result": {"task_id": task_id, "events": []},
        }, ensure_ascii=False)


# ============================================================
# CLI 入口（供 python -m agents.cli risk 调用）
# ============================================================

def run_risk_agent(message: str) -> str:
    """
    CLI 入口：根据 message 自动分发到风险研判或列表查询。

    支持的关键词：
        analyze / 研判 / 综合  → 走 risk_analyze（需提供 event_id）
        list / 列出            → 走 risk_list
    """
    import re
    try:
        msg_lower = message.lower()
        if any(k in msg_lower for k in ("analyze", "研判", "综合")):
            m = re.search(r"(?:candidate_event_id|risk_event_id|event_id)[:：\s]+([\w\-]+)", message)
            event_id = m.group(1) if m else "unknown"
            return risk_analyze.invoke(event_id)
        if any(k in msg_lower for k in ("list", "列出", "列表")):
            m = re.search(r"(?:task_id)[:：\s]+([\w\-]+)", message)
            task_id = m.group(1) if m else "all"
            return risk_list.invoke(task_id)
        return json.dumps({"status": "ok", "message": message}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


# ============================================================
# A6 FastAPI 路由注册（由 P6 启动时调用 register_a6_routes(app)）
# ============================================================

def register_a6_routes(app, a5_log_dir: Optional[str] = None,
                       a6_output_dir: Optional[str] = None):
    """
    将 A6 全部路由 + /a6 页面挂载到 FastAPI app。
    P6 启动时调用：register_a6_routes(app, a5_log_dir=str(LOG_DIR))

    Args:
        a5_log_dir:    默认 a5_log_dir（A5/logs 或全局路径）；不传则用 _ROOT/A5/logs
        a6_output_dir: 默认 a6_output_dir（A6/logs 或全局路径）；不传则用 _ROOT/A6/logs
                       注意：per-job 数据通过 ?job_id=xxx 查询参数按需访问，
                       此参数仅控制默认（不传 job_id）的路径。
    """
    from fastapi.responses import Response, JSONResponse

    a5_log_path = Path(a5_log_dir) if a5_log_dir else _ROOT / "A5" / "logs"
    a6_output_path = Path(a6_output_dir) if a6_output_dir else _ROOT / "A6" / "logs"

    # ── A6 风险研判看板页面 ─────────────────────────────────────────
    @app.get("/a6", response_class=Response)
    async def a6_page():
        """A6 风险研判看板页面"""
        return Response(
            content=INDEX_A6_HTML,
            media_type="text/html; charset=utf-8"
        )

    # ── 研判列表 ─────────────────────────────────────────
    @app.get("/api/a6/assessments")
    async def get_assessments(start: str = None, end: str = None,
                              risk_level: int = None, limit: int = 100,
                              job_id: Optional[str] = None):
        """获取研判结果列表

        Args:
            job_id: 传值时只读 data/jobs/{job_id}/P7/a6_*.json（per-job 扁平）；
                    不传时读全局 a6_output_path/assessments/<YYYYMMDD>/（向后兼容）
        """
        if job_id:
            # per-job 模式（2026-08-19 改造）：扁平 a6_*.json 在 data/jobs/{job_id}/P7/
            from agents.p6_monitor_agent import get_p7_log_dir
            output_dir = Path(get_p7_log_dir(job_id))
        else:
            output_dir = a6_output_path / "assessments"
        if not output_dir.exists():
            return {"total": 0, "items": []}

        results = []
        # 兼容两种结构：扁平 (a6_*.json) 与按日期分 (assessments/<YYYYMMDD>/a6_*.json)
        candidate_files = []
        if job_id:
            candidate_files = sorted(output_dir.glob("a6_*.json"), reverse=True)
        else:
            for date_dir in sorted(output_dir.iterdir(), reverse=True):
                if not date_dir.is_dir():
                    continue
                candidate_files.extend(sorted(date_dir.glob("a6_*.json"), reverse=True))
        for f in candidate_files:
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
        return {"total": len(results), "items": results}

    @app.get("/api/a6/assessments/{a6_event_id}")
    async def get_assessment(a6_event_id: str, job_id: Optional[str] = None):
        """获取单条研判详情

        Args:
            job_id: 传值时只在 data/jobs/{job_id}/P7/ 查找；
                    不传时在全局 a6_output_path/assessments/<YYYYMMDD>/ 查找
        """
        if job_id:
            from agents.p6_monitor_agent import get_p7_log_dir
            search_dirs = [Path(get_p7_log_dir(job_id))]
        else:
            base = a6_output_path / "assessments"
            search_dirs = [base / d for d in sorted(base.iterdir(), reverse=True) if d.is_dir()] \
                if base.exists() else []
            # 也兜底扫扁平目录（per-job 模式下不传 job_id 也能找到）
            if a6_output_path.exists():
                search_dirs.insert(0, a6_output_path)
        for d in search_dirs:
            if not d.exists():
                continue
            for f in sorted(d.glob("a6_*.json"), reverse=True):
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
        tools = get_a5_tools()
        # 重新指定 a5_log_dir（兼容传入的 LOG_DIR）
        tools.a5_log_dir = a5_log_path
        status = tools._load_status()
        return {
            "event_id_map": status.get("event_id_map", {}),
            "summary": status.get("summary", {}),
        }

    # ── 提示词管理 ─────────────────────────────────────────
    @app.get("/api/a6/prompts")
    async def get_all_prompts():
        pm = get_prompt_manager()
        return pm.get_all_prompts()

    @app.get("/api/a6/prompts/{prompt_name}")
    async def get_prompt(prompt_name: str):
        pm = get_prompt_manager()
        prompt = pm.read_prompt(prompt_name)
        if prompt.get("error"):
            return {"error": prompt["error"]}
        return prompt

    @app.post("/api/a6/prompts/{prompt_name}")
    async def update_prompt(prompt_name: str, body: dict):
        pm = get_prompt_manager()
        success = pm.update_prompt(
            prompt_name=prompt_name,
            new_content=body.get("content", ""),
            reason=body.get("reason"),
        )
        return {"status": "success" if success else "failed"}

    @app.post("/api/a6/prompts/{prompt_name}/save")
    async def save_prompt(prompt_name: str):
        pm = get_prompt_manager()
        success = pm.save_to_file(prompt_name=prompt_name)
        return {"status": "success" if success else "failed",
                "saved_at": datetime.now().isoformat()}

    @app.post("/api/a6/prompts/{prompt_name}/reset")
    async def reset_prompt(prompt_name: str):
        pm = get_prompt_manager()
        success = pm.reset_to_default(prompt_name)
        return {"status": "success" if success else "failed"}

    # ── 手动触发研判（外部 HTTP 调用入口） ─────────────────────────────
    @app.post("/api/a6/process/{event_id}")
    async def process_a6_event(event_id: str, body: Optional[dict] = None):
        """手动触发 A6 研判（HTTP 接口，P6 已改为 in-process 调用）

        Args:
            event_id: A5 事件 ID
            body: 必含 job_id（per-job 模式）；不传则用全局 A6Agent
        """
        import traceback
        try:
            job_id = (body or {}).get("job_id", "")
            agent = get_a6_agent(job_id) if job_id else get_a6_agent_global()
            result = await agent.process_event(event_id, event_data=body)
            return result
        except Exception as e:
            print(f"[A6 API] process_a6_event({event_id}) 异常: {type(e).__name__}: {e}")
            traceback.print_exc()
            return {"status": "error",
                    "message": f"{type(e).__name__}: {e}",
                    "event_id": event_id}

    # ── 清理日志 ─────────────────────────────────────────
    @app.post("/api/a6/clear_logs")
    async def clear_a6_logs(body: dict):
        """清理 A6 日志（per-job 模式）。

        body.job_id 必传：data/jobs/{job_id}/P7/（清空 a6_*.json）；
        不传直接 400 报错（避免误清全局 A6/logs）。
        """
        job_id = (body or {}).get("job_id", "")
        if not job_id or not re.match(r"^\d{17}$", job_id):
            return JSONResponse(content={"error": "job_id 必传且须为 17 位数字"}, status_code=400)
        import shutil
        from agents.p6_monitor_agent import get_p7_log_dir
        log_dir = Path(get_p7_log_dir(job_id))
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
        return {"status": "cleared", "removed": count, "job_id": job_id, "base": str(log_dir)}


# ============================================================
# 兼容导出（main_agent.py 之外的旧引用）
# ============================================================

# 这些是早期占位符，P7 现在通过真实 A6Agent 调度，旧引用保留为 None
create_risk_agent = None
create_risk_agent_with_hitl = None
risk_demo = None
risk_grade = None
risk_cases = None
