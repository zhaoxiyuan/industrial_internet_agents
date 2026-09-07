"""
可视化层

提供：
    - render_mermaid(graph=None) -> Mermaid 源代码字符串
    - render_ascii(graph=None)    -> 文本视图
    - render_static_html(path)    -> 自包含静态 HTML（内置 Mermaid CDN）
    - render_dynamic_html(path)   -> 自包含动态 HTML（轮询 state sidecar 高亮节点）
    - write_state_sidecar(state, path)  -> 把当前状态写入 JSON（供动态页读取）
    - set_broadcast_callback(cb)  -> 转发到 nodes 模块
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Callable

from .nodes import set_broadcast_callback  # re-export
from .state import STAGES
from .graph import build_workflow_graph


__all__ = [
    "render_mermaid",
    "render_ascii",
    "render_static_html",
    "render_dynamic_html",
    "write_state_sidecar",
    "set_broadcast_callback",
]


_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"


# ============================================================
# Mermaid / ASCII 渲染
# ============================================================

def render_mermaid(graph=None) -> str:
    """导出 LangGraph 内部表示的 Mermaid 图。"""
    g = graph if graph is not None else build_workflow_graph()
    return g.get_graph().draw_mermaid()


def render_ascii(graph=None) -> str:
    """导出 ASCII 图（适合终端打印 / 写 README）。"""
    g = graph if graph is not None else build_workflow_graph()
    return g.get_graph().draw_ascii()


# ============================================================
# HTML 生成
# ============================================================

_STATIC_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Agent Graph - 静态视图 (P1-P10)</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; background: #fafafa; color: #222; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #666; margin-bottom: 20px; }}
    .mermaid {{ background: #fff; padding: 24px; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); }}
    .legend {{ display: flex; gap: 16px; margin-top: 20px; flex-wrap: wrap; }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 999px; background: #fff; border: 1px solid #eee; }}
    .legend-dot {{ width: 14px; height: 14px; border-radius: 4px; }}
    pre {{ background: #0f172a; color: #d1d5db; padding: 16px; border-radius: 12px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Agent Graph - P1 → P10 静态视图</h1>
  <div class="meta">基于 LangGraph StateGraph，节点 = 阶段，边 = 顺序 + 条件（HITL 暂停 / 失败 / 下一阶段）。</div>
  <div class="mermaid">
{mermaid}
  </div>
  <div class="legend">
    <div class="legend-item"><span class="legend-dot" style="background:#fde68a"></span>running</div>
    <div class="legend-item"><span class="legend-dot" style="background:#bbf7d0"></span>completed</div>
    <div class="legend-item"><span class="legend-dot" style="background:#fecaca"></span>waiting (HITL 暂停)</div>
    <div class="legend-item"><span class="legend-dot" style="background:#fecaca"></span>failed</div>
    <div class="legend-item"><span class="legend-dot" style="background:#e5e7eb"></span>pending</div>
  </div>
  <details style="margin-top:24px">
    <summary>Mermaid 源</summary>
    <pre>{mermaid_escaped}</pre>
  </details>
  <script src="{cdn}"></script>
  <script>mermaid.initialize({{ startOnLoad: true, theme: 'default', flowchart: {{ curve: 'linear' }} }});</script>
</body>
</html>
"""


_DYNAMIC_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Agent Graph - 动态视图 (P1-P10)</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; background: #fafafa; color: #222; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #666; margin-bottom: 20px; }}
    .mermaid {{ background: #fff; padding: 24px; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); }}
    .status-bar {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }}
    .pill {{ padding: 6px 14px; border-radius: 999px; font-weight: 600; }}
    .pill-running {{ background: #fde68a; color: #92400e; }}
    .pill-waiting {{ background: #fecaca; color: #991b1b; }}
    .pill-completed {{ background: #bbf7d0; color: #166534; }}
    .pill-failed {{ background: #fca5a5; color: #7f1d1d; }}
    .pill-idle {{ background: #e5e7eb; color: #374151; }}
    .legend {{ display: flex; gap: 16px; margin-top: 20px; flex-wrap: wrap; }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 999px; background: #fff; border: 1px solid #eee; }}
    .legend-dot {{ width: 14px; height: 14px; border-radius: 4px; }}
    .footer {{ color: #666; font-size: 13px; margin-top: 20px; }}
  </style>
</head>
<body>
  <h1>Agent Graph - P1 → P10 动态视图</h1>
  <div class="meta">轮询 <code>{state_url}</code> 获取最新 <code>stages[*]</code> 状态并高亮对应节点。</div>
  <div class="status-bar">
    <span id="workflowStatus" class="pill pill-idle">workflow: idle</span>
    <span id="currentStage" class="pill pill-idle">current: -</span>
  </div>
  <div class="mermaid" id="graph">
{mermaid}
  </div>
  <div class="legend">
    <div class="legend-item"><span class="legend-dot" style="background:#fde68a"></span>running</div>
    <div class="legend-item"><span class="legend-dot" style="background:#bbf7d0"></span>completed</div>
    <div class="legend-item"><span class="legend-dot" style="background:#fecaca"></span>waiting (HITL 暂停)</div>
    <div class="legend-item"><span class="legend-dot" style="background:#fecaca"></span>failed</div>
    <div class="legend-item"><span class="legend-dot" style="background:#e5e7eb"></span>pending</div>
  </div>
  <div class="footer">每 1.5s 轮询一次；启动 <code>run_workflow_graph</code> 后会看到节点依次变为 running / completed / waiting。</div>
  <script src="{cdn}"></script>
  <script>
const COLORS = {{
  pending:   '#e5e7eb',
  running:   '#fde68a',
  completed: '#bbf7d0',
  waiting:   '#fecaca',
  failed:    '#fca5a5',
}};
const STAGES = {stages_json};
let lastSignature = '';

function setStatus(name, css, text) {{
  const el = document.getElementById(name);
  el.className = 'pill ' + css;
  el.textContent = text;
}}

async function pollOnce() {{
  try {{
    const r = await fetch('{state_url}', {{ cache: 'no-store' }});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    const stages = data.stages || {{}};
    const wf = data.workflow_status || 'idle';
    const cur = data.current_stage || '-';
    setStatus('workflowStatus',
      wf === 'running' ? 'pill-running' :
      wf === 'waiting' ? 'pill-waiting' :
      wf === 'completed' ? 'pill-completed' :
      wf === 'failed' ? 'pill-failed' : 'pill-idle',
      'workflow: ' + wf);
    setStatus('currentStage', 'pill-idle', 'current: ' + cur);

    // 构造签名，避免无变化时重渲染 mermaid
    const sig = JSON.stringify(stages) + '|' + wf + '|' + cur;
    if (sig === lastSignature) return;
    lastSignature = sig;

    const lines = [
      'flowchart LR',
      '  classDef pending fill:#e5e7eb,stroke:#6b7280,color:#1f2937',
      '  classDef running fill:#fde68a,stroke:#b45309,color:#78350f',
      '  classDef completed fill:#bbf7d0,stroke:#15803d,color:#14532d',
      '  classDef waiting fill:#fecaca,stroke:#b91c1c,color:#7f1d1d',
      '  classDef failed fill:#fca5a5,stroke:#b91c1c,color:#7f1d1d',
    ];
    STAGES.forEach((s, i) => {{
      const status = (stages[s] || {{status: 'pending'}}).status;
      lines.push('  ' + s + '["' + s + '"]:::' + status);
      if (i + 1 < STAGES.length) {{
        lines.push('  ' + s + ' --> ' + STAGES[i + 1]);
      }}
    }});
    lines.push('  classDef ' + cur + 'State fill:#fde68a,stroke:#b45309,color:#78350f');

    const container = document.getElementById('graph');
    container.innerHTML = '';
    container.classList.remove('mermaid');
    void container.offsetWidth;  // 触发重排
    container.classList.add('mermaid');
    container.textContent = lines.join('\\n');
    if (window.mermaid) {{
      window.mermaid.run({{ nodes: [container] }});
    }}
  }} catch (e) {{
    console.warn('poll failed', e);
    setStatus('workflowStatus', 'pill-failed', 'workflow: poll error');
  }}
}}

setInterval(pollOnce, 1500);
pollOnce();
  </script>
</body>
</html>
"""


def render_static_html(output_path: str | Path, graph=None) -> Path:
    """写出静态可视化 HTML（包含 Mermaid 图）。"""
    mermaid = render_mermaid(graph)
    html = _STATIC_HTML_TEMPLATE.format(
        mermaid=mermaid.strip(),
        mermaid_escaped=mermaid,
        cdn=_MERMAID_CDN,
    )
    p = Path(output_path)
    p.write_text(html, encoding="utf-8")
    return p


def render_dynamic_html(
    output_path: str | Path,
    state_url: str = "/api/graph/state",
    graph=None,
) -> Path:
    """写出动态可视化 HTML。

    HTML 通过 fetch(state_url) 轮询 JSON 状态；每个阶段的状态变化都会重新渲染 mermaid。
    """
    mermaid = render_mermaid(graph)
    html = _DYNAMIC_HTML_TEMPLATE.format(
        mermaid=mermaid.strip(),
        cdn=_MERMAID_CDN,
        state_url=state_url,
        stages_json=json.dumps(STAGES),
    )
    p = Path(output_path)
    p.write_text(html, encoding="utf-8")
    return p


# ============================================================
# Sidecar
# ============================================================

def write_state_sidecar(state: Dict[str, Any], path: str | Path) -> Path:
    """把当前 state 快照写入 JSON 文件（动态 HTML 会轮询该文件）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p
