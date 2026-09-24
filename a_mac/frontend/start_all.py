"""
a/frontend/start_all.py — 一键启动 a/ 子项目的全部服务

启动顺序:
  1. frontend/app_config.py     (端口 5000)  LLM/VL 配置管理
  2. agents/p6_monitor_agent.py (端口 5002)  P6 监测 + A5 + A6 (FastAPI 自带前端)
  3. P8P9/web_server.py        (端口 8089)  P8P9 状态机 Web 服务(含 closure 接口)

启动:
    python frontend/start_all.py
    python frontend/start_all.py --no-config --no-p8p9
"""
import sys
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # a/


def _spawn(label, args, port=None):
    """启动子进程,做端口占用探测。"""
    print(f"\n[+] 启动 {label} ...")
    proc = subprocess.Popen(
        [sys.executable, *args],
        cwd=str(ROOT),
        env={**__import__("os").environ},
    )
    time.sleep(1)
    if proc.poll() is not None:
        print(f"  [ERROR] {label} 启动失败(进程已退出)")
        return None
    if port:
        print(f"  {label} PID={proc.pid}  http://localhost:{port}")
    else:
        print(f"  {label} PID={proc.pid}")
    return proc


def main():
    import argparse
    parser = argparse.ArgumentParser(description="a/ P6-P9 一键启动")
    parser.add_argument("--config-port", type=int, default=5000,
                       help="app_config 配置前端端口(默认 5000)")
    parser.add_argument("--p6-port", type=int, default=5002,
                       help="P6 监测服务端口(默认 5002)")
    parser.add_argument("--p8p9-port", type=int, default=8089,
                       help="P8P9 web 服务端口(默认 8089,与 P8P9/web_server.py / aaaseverstart.py 一致)")
    parser.add_argument("--no-config", action="store_true",
                       help="不启动 app_config 配置前端")
    parser.add_argument("--no-p8p9", action="store_true",
                       help="不启动 P8P9 web_server")
    parser.add_argument("--no-p6", action="store_true",
                       help="不启动 P6 监测")
    args = parser.parse_args()

    print("=" * 70)
    print("a/ P6-P9 独立子集 — 一键启动")
    print("=" * 70)
    print(f"  root: {ROOT}")
    if not args.no_config:
        print(f"  [config] 配置前端:  http://localhost:{args.config_port}")
    if not args.no_p6:
        print(f"  [p6]     监测+研判: http://localhost:{args.p6_port}")
    if not args.no_p8p9:
        print(f"  [p8p9]   状态机:    http://localhost:{args.p8p9_port}")
    print("=" * 70)

    procs = []

    if not args.no_config:
        p = _spawn(
            "app_config 配置前端",
            ["frontend/app_config.py", "--host", "127.0.0.1", "--port", str(args.config_port)],
            args.config_port,
        )
        if p:
            procs.append(p)
        else:
            print("启动 app_config 失败,后续服务继续(不阻断)")

    if not args.no_p6:
        p = _spawn(
            "P6 监测 + A5 + A6",
            ["agents/p6_monitor_agent.py", "--port", str(args.p6_port)],
            args.p6_port,
        )
        if p:
            procs.append(p)

    if not args.no_p8p9:
        p = _spawn(
            "P8P9 状态机 Web",
            ["P8P9/web_server.py", "--port", str(args.p8p9_port)],
            args.p8p9_port,
        )
        if p:
            procs.append(p)

    if not procs:
        print("\n[ERROR] 没有任何服务被启动(--no-* 全部勾选?)")
        return

    print("\n" + "=" * 70)
    print("全部服务已启动,按 Ctrl+C 停止全部")
    print("=" * 70)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\n正在停止服务 ...")
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()
        print("已停止")


if __name__ == "__main__":
    main()