"""
Command-line entry for agents_graph.

用法：
    python -m agents.agents_graph --mermaid
    python -m agents.agents_graph --ascii
    python -m agents.agents_graph --static-html docs/agents_graph_static.html
    python -m agents.agents_graph --mermaid-md docs/agents_graph.mmd
    python -m agents.agents_graph --dynamic-html web/static/agents_graph_dynamic.html
    python -m agents.agents_graph --run-demo <job_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph import build_workflow_graph
from .render import (
    render_ascii,
    render_dynamic_html,
    render_mermaid,
    render_static_html,
)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.agents_graph",
        description="P1-P10 StateGraph 可视化与运行入口",
    )
    parser.add_argument("--mermaid", action="store_true", help="打印 Mermaid 源代码")
    parser.add_argument("--ascii", action="store_true", help="打印 ASCII 图")
    parser.add_argument("--mermaid-md", type=str, default=None, help="把 Mermaid 源写入 .mmd 文件")
    parser.add_argument("--static-html", type=str, default=None, help="生成静态可视化 HTML")
    parser.add_argument("--dynamic-html", type=str, default=None, help="生成动态可视化 HTML")
    parser.add_argument(
        "--dynamic-state-url",
        type=str,
        default="/api/graph/state",
        help="动态 HTML 轮询的状态端点（默认 /api/graph/state）",
    )
    parser.add_argument("--run-demo", type=str, default=None, metavar="JOB_ID",
                        help="使用给定 job_id 跑一次工作流（不会调用 LLM；走 execute_stage 实际路径）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli()
    args = parser.parse_args(argv)
    graph = build_workflow_graph()

    did_anything = False

    if args.mermaid:
        print(render_mermaid(graph))
        did_anything = True
    if args.ascii:
        print(render_ascii(graph))
        did_anything = True
    if args.mermaid_md:
        out = Path(args.mermaid_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_mermaid(graph), encoding="utf-8")
        print(f"已写入 Mermaid: {out}")
        did_anything = True
    if args.static_html:
        out = render_static_html(args.static_html, graph)
        print(f"已写入静态 HTML: {out}")
        did_anything = True
    if args.dynamic_html:
        out = render_dynamic_html(args.dynamic_html, args.dynamic_state_url, graph)
        print(f"已写入动态 HTML: {out}")
        did_anything = True
    if args.run_demo:
        from .graph import run_workflow_graph
        from .state import make_initial_state

        # 不真正调 LLM：暂时不支持在不修改各 pX_agent 的情况下跳过。
        # 默认行为：调用各 execute_stage，需要前置的 application.json / 工作目录。
        print(f"运行 demo 工作流：job_id={args.run_demo}")
        final = run_workflow_graph(args.run_demo, application=make_initial_state(args.run_demo).get("application"))
        print(json.dumps(
            {
                "workflow_status": final.get("workflow_status"),
                "current_stage": final.get("current_stage"),
                "stages": {k: v.get("status") for k, v in (final.get("stages") or {}).items()},
            },
            ensure_ascii=False,
            indent=2,
        ))
        did_anything = True

    if not did_anything:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
