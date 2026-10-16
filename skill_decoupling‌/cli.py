"""统一 CLI：独立可执行入口，stdout 只输出 JSON，不在此持有平台数据库。

python cli.py catalog 显示内置命令说明；Runtime 使用 invoke 和一次性票据。
管理员可用 run --agent ID --command NAME，从 stdin 提供 JSON 业务参数。
管理调用需要 PLATFORM_CLI_TOKEN，不能让模型设置自己身份。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    """解析固定命令并通过 HTTP 桥接，所有异常以结构化 JSON 返回。"""
    parser = argparse.ArgumentParser(description="统一能力 CLI：内置功能、外部 MCP、HTTP 直连")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8770")
    sub = parser.add_subparsers(dest="action", required=True)
    catalog = sub.add_parser("catalog", help="查看内置命令或指定 Agent 的已授权命令契约")
    catalog.add_argument("--agent", default="")
    sub.add_parser("invoke", help="Runtime 的一次性票据调用；JSON 从 stdin 读取")
    run = sub.add_parser("run", help="授权管理员按指定 Agent 权限执行；JSON 从 stdin 读取")
    run.add_argument("--agent", required=True)
    run.add_argument("--command", required=True)
    args = parser.parse_args()
    try:
        if not args.endpoint.startswith(("http://", "https://")):
            raise ValueError("endpoint 必须为 HTTP/HTTPS")
        data = None if args.action == "catalog" else json.load(sys.stdin)
        headers = {"Content-Type": "application/json"}
        route = "catalog" if args.action == "catalog" else "invoke"
        if args.action == "catalog" and args.agent:
            from urllib.parse import quote
            route = "catalog?agent_id=" + quote(args.agent, safe="")
        if args.action == "run":
            route = "run"
            token = os.environ.get("PLATFORM_CLI_TOKEN", "")
            # 同一系统用户可使用本地受限令牌文件；Runtime 一律使用更小范围票据。
            file = Path(__file__).resolve().parent / "data" / ".cli-admin-token"
            if not token and file.exists():
                token = file.read_text(encoding="utf-8").strip()
            headers["Authorization"] = "Bearer " + token
            data = {"agent_id": args.agent, "command": args.command, "arguments": data}
        req = urllib.request.Request(args.endpoint.rstrip("/") + "/api/cli/" + route,
                                     data=None if data is None else json.dumps(data).encode(), headers=headers)
        # 本机桥接不使用系统代理，避免平台代理变量让请求绕到外网。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=55) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            result = json.load(error)
            result.setdefault("ok", False)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok", True) else 1
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "error": {"code": "CLI_REQUEST_FAILED", "message": "CLI 参数或连接无效"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    # CLI 回执统一为 UTF-8；Windows 默认代码页不能破坏中文工具说明与任务成果。
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    sys.exit(main())
