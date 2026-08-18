"""
一键启动 A5 + A6 + AgentConfig 前后端服务

启动顺序：
  1. AgentConfig（端口5000）— LLM/VL 模型配置管理
  2. A6（端口5002）— 风险研判看板
  3. A5（端口5001）— 作业过程监测
  A5/A6 必须后启动，因为依赖 AgentConfig 的配置

启动:
    python frontend/start_all.py
    python frontend/start_all.py --a5-port 5001 --a6-port 5002 --config-port 5000
"""
import sys
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    import argparse
    parser = argparse.ArgumentParser(description="一键启动 A5 + A6 + AgentConfig")
    parser.add_argument("--config-port", type=int, default=5000,
                        help="AgentConfig 端口（默认 5000）")
    parser.add_argument("--a5-port", type=int, default=5001,
                        help="A5 前端端口（默认 5001）")
    parser.add_argument("--a6-port", type=int, default=5002,
                        help="A6 前端端口（默认 5002）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    args = parser.parse_args()

    print("=" * 60)
    print("A5 + A6 + AgentConfig 一键启动")
    print("=" * 60)
    print(f"  AgentConfig: http://localhost:{args.config_port}  (模型配置)")
    print(f"  A5 前端:     http://localhost:{args.a5_port}      (作业监测)")
    print(f"  A6 前端:     http://localhost:{args.a6_port}      (风险研判)")
    print(f"  A5 Agent 告警将自动触发 A6 研判")
    print("=" * 60)

    # 1. 启动 AgentConfig（必须最先启动，配置 LLM/VL）
    print("\n[1/3] 启动 AgentConfig 模型配置中心 ...")
    config_proc = subprocess.Popen(
        [sys.executable, "frontend/app_config.py",
         "--host", args.host,
         "--port", str(args.config_port)],
        cwd=str(ROOT),
        env={**subprocess.os.environ},
    )
    time.sleep(1)
    if config_proc.poll() is not None:
        print("[错误] AgentConfig 启动失败，请检查端口是否被占用")
        return

    # 2. 启动 A6（依赖 AgentConfig 的配置）
    print("[2/3] 启动 A6 风险研判看板 ...")
    a6_proc = subprocess.Popen(
        [sys.executable, "frontend/app_a6.py"],
        cwd=str(ROOT),
        env={**subprocess.os.environ},
    )
    time.sleep(1)
    if a6_proc.poll() is not None:
        print("[错误] A6 启动失败，请检查端口是否被占用")
        config_proc.terminate()
        return

    # 3. 启动 A5
    print("[3/3] 启动 A5 作业过程监测 ...")
    a5_proc = subprocess.Popen(
        [sys.executable, "frontend/app_a5.py",
         "--host", args.host,
         "--port", str(args.a5_port)],
        cwd=str(ROOT),
        env={**subprocess.os.environ},
    )

    print("\n" + "=" * 60)
    print("已全部启动，按 Ctrl+C 停止全部服务")
    print("=" * 60)

    try:
        a5_proc.wait()
    except KeyboardInterrupt:
        print("\n正在停止服务 ...")
        a5_proc.terminate()
        a6_proc.terminate()
        config_proc.terminate()
        a5_proc.wait()
        a6_proc.wait()
        config_proc.wait()
        print("已停止")


if __name__ == "__main__":
    main()
