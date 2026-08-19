"""P8 服务管理 — 一键启停 / 状态查询 / 重启 P8 联调必需的全部后台进程

P8 联调需要同时启动三类进程：
  1. Channel Gateway (port 8787)  — 飞书入站消息接收 + 卡片出站
  2. chat_reply daemon            — Python 轮询 Gateway，调 P8 agent
  3. Web server (port 8080)       — REST API + Gradio UI

依赖关系：chat_reply 必须等 Gateway 就绪后再启动（要拉取 channel_events）。

用法：
    # CLI
    python agents/p8_service_manager.py                # 默认 start
    python agents/p8_service_manager.py start
    python agents/p8_service_manager.py stop
    python agents/p8_service_manager.py restart
    python agents/p8_service_manager.py status
    python agents/p8_service_manager.py start --foreground gateway   # 仅前台跑 gateway
    python agents/p8_service_manager.py start --no-wait-health      # 不等健康检查
    python agents/p8_service_manager.py stop --force                 # 强杀（gateway 默认走它自己的 --force）

    # Python API
    from agents.p8_service_manager import start_all, stop_all, status_all
    start_all()
    status_all()
    stop_all()
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

# ============================================================
# 常量 / 配置
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "data" / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

# 依赖关系：chat_reply 必须等 gateway 就绪
# 拓扑序：gateway → chat_reply → web （start 用）
STOP_ORDER = ["web", "chat_reply", "gateway"]
START_ORDER = ["gateway", "chat_reply", "web"]

# 健康检查 URL（None 表示只看进程 PID）
GATEWAY_HEALTHCHECK = "http://127.0.0.1:8787/healthz"
WEB_HEALTHCHECK = "http://127.0.0.1:8080/api/jobs/_smoke_/working-memory"   # 任意 job_id 都行，返 200 即通
CHAT_REPLY_HEALTHCHECK = None  # 没 HTTP 端口，靠 PID + 日志尾部

# 服务配置
SERVICES: dict[str, dict] = {
    "gateway": {
        "label": "Channel Gateway (port 8787)",
        # Gateway 自己写到 <gateway_dir>/.gateway.pid（feishu_gateway_cli.start_gateway:132）
        "pid_file": PROJECT_ROOT / "openclaw-channel-gateway-standalone" / ".gateway.pid",
        "log_file": PROJECT_ROOT / "openclaw-channel-gateway-standalone" / "gateway.python.log",
        "cmd_start": [sys.executable, "-m", "feishu_gateway_cli.start_gateway", "start"],
        "cmd_stop":  [sys.executable, "-m", "feishu_gateway_cli.start_gateway", "stop"],
        "managed_externally": True,   # PID 文件由 feishu_gateway_cli 自己写
        "healthcheck_url": GATEWAY_HEALTHCHECK,
        "startup_wait": 30.0,         # 飞书配置首次加载可能慢
    },
    "chat_reply": {
        "label": "chat_reply daemon (P8 轮询)",
        "pid_file": RUNTIME_DIR / "p8_chat_reply.pid",
        "log_file": RUNTIME_DIR / "p8_chat_reply.log",
        "cmd_start": [sys.executable, "-m", "A7.adapters.chat_reply", "run",
                      "--initial-sequence", "-1", "--interval", "1.0"],
        "cmd_stop":  None,            # 没 stop 子命令，直接 kill
        "managed_externally": False,
        "healthcheck_url": CHAT_REPLY_HEALTHCHECK,
        "startup_wait": 5.0,          # 仅检查 PID 存活
    },
    "web": {
        "label": "Web server (port 8080)",
        "pid_file": RUNTIME_DIR / "p8_web_server.pid",
        "log_file": RUNTIME_DIR / "p8_web_server.log",
        "cmd_start": [sys.executable, "web/server.py"],
        "cmd_stop":  None,
        "managed_externally": False,
        "healthcheck_url": WEB_HEALTHCHECK,
        "startup_wait": 10.0,
    },
}

# ============================================================
# 输出（彩色，ANSI）
# ============================================================

_ANSI = {
    "INFO":  "\033[36m",   # cyan
    "OK":    "\033[32m",   # green
    "WARN":  "\033[33m",   # yellow
    "ERR":   "\033[31m",   # red
    "DIM":   "\033[2m",
    "RESET": "\033[0m",
    "BOLD":  "\033[1m",
}


def _paint(level: str, msg: str) -> str:
    if sys.platform == "win32" and not os.environ.get("FORCE_COLOR"):
        # Windows cmd 默认不解析 ANSI；如检测不到 TTY 跳过颜色
        try:
            import colorama  # noqa: F401
            colorama.init()
        except ImportError:
            return msg
    color = _ANSI.get(level, "")
    reset = _ANSI["RESET"] if color else ""
    return f"{color}[{level:>4}] {msg}{reset}"


def _info(msg: str) -> None:
    print(_paint("INFO", msg), flush=True)


def _ok(msg: str) -> None:
    print(_paint("OK",   msg), flush=True)


def _warn(msg: str) -> None:
    print(_paint("WARN", msg), flush=True)


def _err(msg: str) -> None:
    print(_paint("ERR",  msg), file=sys.stderr, flush=True)


def _title(msg: str) -> None:
    print(_paint("BOLD", f"\n=== {msg} ==="), flush=True)


# ============================================================
# PID / 进程 / 端口 工具
# ============================================================

def _read_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid), encoding="utf-8")


def _is_pid_alive(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True,
        ).stdout
        # 命中行首为 "PID"（标题）或进程名包含
        return any(f'"{pid}"' in line for line in out.splitlines() if line.strip())
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _kill_pid(pid: int, force: bool = False) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F" if force else "/T", "/PID", str(pid)],
            capture_output=True,
        )
    else:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _wait_until_dead(pid: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            return True
        time.sleep(0.3)
    return not _is_pid_alive(pid)


def _wait_health(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                if r.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ============================================================
# 服务操作
# ============================================================

def start_service(
    name: str,
    *,
    foreground: bool = False,
    no_wait_health: bool = False,
) -> bool:
    """启动单个服务。返回是否成功。"""
    cfg = SERVICES[name]
    pid = _read_pid(cfg["pid_file"])
    if _is_pid_alive(pid):
        _info(f"{cfg['label']} 已在运行 (PID={pid})")
        return True

    _info(f"启动 {cfg['label']}...")

    if cfg["managed_externally"]:
        # Gateway: 用它自己的 start 子命令（PID 由它自己写）
        cmd = list(cfg["cmd_start"])
        if no_wait_health:
            cmd.append("--no-wait-health")
        if foreground:
            _info(f"前台运行: {' '.join(cmd)}")
            subprocess.run(cmd, cwd=str(PROJECT_ROOT))
            return True

        try:
            subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=cfg["startup_wait"] + 30)
        except subprocess.TimeoutExpired:
            _err(f"{name} 启动超时")
            return False

        pid = _read_pid(cfg["pid_file"])
        if not _is_pid_alive(pid):
            _err(f"{name} 启动后未存活（PID={pid}）")
            return False
    else:
        # chat_reply / web: 自己拉起，写 PID
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        try:
            log_fp = open(cfg["log_file"], "ab", buffering=0)
            log_fp.write(f"\n=== {name} started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
        except OSError as exc:
            _err(f"打开日志失败: {exc}")
            return False

        kwargs = {
            "cwd":       str(PROJECT_ROOT),
            "stdout":    log_fp,
            "stderr":    subprocess.STDOUT,
            "env":       env,
        }
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP 让 Ctrl+C 不传到子进程（detach）
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cfg["cmd_start"], **kwargs)
        except OSError as exc:
            _err(f"启动失败: {exc}")
            log_fp.close()
            return False

        _write_pid(cfg["pid_file"], proc.pid)
        pid = proc.pid

    _ok(f"{cfg['label']} 已启动 (PID={pid})")

    # 健康检查
    if not no_wait_health:
        url = cfg["healthcheck_url"]
        if url:
            _info(f"等待 {name} 健康检查 {url} ...")
            if _wait_health(url, cfg["startup_wait"]):
                _ok(f"{name} 健康检查通过")
            else:
                _warn(f"{name} 健康检查超时（{cfg['startup_wait']}s）—— 进程可能仍在启动")
                return False
        else:
            # chat_reply: 给进程几秒自我初始化
            time.sleep(min(cfg["startup_wait"], 3.0))
            if not _is_pid_alive(pid):
                _err(f"{name} 启动后立即退出，查看日志: {cfg['log_file']}")
                return False
            _ok(f"{name} 进程存活")
    return True


def stop_service(name: str, *, force: bool = False, timeout: float = 4.0) -> bool:
    """停止单个服务。"""
    cfg = SERVICES[name]
    pid = _read_pid(cfg["pid_file"])
    if pid is None:
        _info(f"{cfg['label']} PID 文件不存在，无需停止")
        return True
    if not _is_pid_alive(pid):
        _info(f"{cfg['label']} 未运行（PID={pid} 已退出），清理 PID 文件")
        cfg["pid_file"].unlink(missing_ok=True)
        return True

    _info(f"停止 {cfg['label']} (PID={pid})...")

    # 优先用服务自带的 stop 子命令（gateway）
    if cfg["managed_externally"] and cfg["cmd_stop"]:
        cmd = list(cfg["cmd_stop"]) + (["--force"] if force else [])
        try:
            subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            _warn(f"{name} 自带 stop 超时，改用直接 kill")
            _kill_pid(pid, force=True)
    else:
        _kill_pid(pid, force=force)

    if not _wait_until_dead(pid, timeout):
        _warn(f"{name} 未在 {timeout}s 内退出，强杀")
        _kill_pid(pid, force=True)
        _wait_until_dead(pid, 3.0)

    cfg["pid_file"].unlink(missing_ok=True)
    _ok(f"{cfg['label']} 已停止")
    return True


def status_service(name: str) -> dict:
    """查询单个服务状态。"""
    cfg = SERVICES[name]
    pid = _read_pid(cfg["pid_file"])
    alive = _is_pid_alive(pid)
    healthy = None
    if alive and cfg["healthcheck_url"]:
        healthy = _wait_health(cfg["healthcheck_url"], timeout=1.0)

    return {
        "name":     name,
        "label":    cfg["label"],
        "pid":      pid,
        "alive":    alive,
        "healthy":  healthy,
        "log_file": str(cfg["log_file"]),
    }


# ============================================================
# 组合操作
# ============================================================

def start_all(*, foreground: str = "", no_wait_health: bool = False) -> bool:
    """按依赖顺序启动所有服务。

    Args:
        foreground: "" / "gateway" / "chat_reply" / "web"
                    指定前台跑的服务（其他仍 daemonize）
        no_wait_health: 跳过健康检查等待
    """
    _title("启动 P8 服务")
    ok = True
    for name in START_ORDER:
        fg = (foreground == name)
        if not start_service(name, foreground=fg, no_wait_health=no_wait_health):
            ok = False
            _err(f"{name} 启动失败 — 后续服务可能也会失败")
    _title("启动完成")
    return ok


def stop_all(*, force: bool = False, timeout: float = 4.0) -> bool:
    """按依赖反序停止所有服务。"""
    _title("停止 P8 服务")
    ok = True
    for name in STOP_ORDER:
        if not stop_service(name, force=force, timeout=timeout):
            ok = False
    _title("停止完成")
    return ok


def status_all() -> dict:
    """查询所有服务状态 + 打印汇总。"""
    _title("P8 服务状态")
    result = {}
    for name in SERVICES:
        st = status_service(name)
        result[name] = st

        if st["alive"]:
            tag = _paint("OK", "RUNNING")
            extra = ""
            if st["healthy"] is True:
                extra = _paint("INFO", " [healthy]")
            elif st["healthy"] is False:
                extra = _paint("WARN", " [unhealthy]")
            print(f"  {tag}  {st['label']:<32} PID={st['pid']}{extra}", flush=True)
        else:
            tag = _paint("ERR", "STOPPED")
            print(f"  {tag}  {st['label']:<32} (no pid)", flush=True)

        # 日志尾部
        if st["log_file"]:
            log_path = Path(st["log_file"])
            if log_path.exists():
                size = log_path.stat().st_size
                print(f"           log: {st['log_file']} ({size} bytes)", flush=True)
    _title("查询完成")
    return result


# ============================================================
# CLI
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agents.p8_service_manager",
        description=(
            "P8 服务管理（Channel Gateway + chat_reply + Web server）："
            "一键 start/stop/restart/status"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    cmd = p.add_subparsers(dest="command", required=False)

    p_start = cmd.add_parser("start",  help="启动所有服务（默认）")
    p_start.add_argument(
        "--foreground", default="",
        choices=["", "gateway", "chat_reply", "web"],
        help="把指定服务跑在前台（其他仍 daemonize）",
    )
    p_start.add_argument(
        "--no-wait-health", action="store_true",
        help="启动时不等待健康检查",
    )

    p_stop = cmd.add_parser("stop",   help="停止所有服务")
    p_stop.add_argument("--force", action="store_true", help="强杀进程")
    p_stop.add_argument(
        "--timeout", type=float, default=4.0,
        help="每个服务停止的超时秒数（默认 4）",
    )

    p_restart = cmd.add_parser("restart", help="先停后启")
    p_restart.add_argument("--force", action="store_true")
    p_restart.add_argument("--timeout", type=float, default=4.0)
    p_restart.add_argument(
        "--foreground", default="",
        choices=["", "gateway", "chat_reply", "web"],
    )
    p_restart.add_argument("--no-wait-health", action="store_true")

    cmd.add_parser("status", help="查询所有服务状态")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd = args.command or "start"

    if cmd == "start":
        return 0 if start_all(
            foreground=getattr(args, "foreground", ""),
            no_wait_health=getattr(args, "no_wait_health", False),
        ) else 1
    if cmd == "stop":
        return 0 if stop_all(
            force=getattr(args, "force", False),
            timeout=getattr(args, "timeout", 4.0),
        ) else 1
    if cmd == "restart":
        ok_stop = stop_all(
            force=getattr(args, "force", False),
            timeout=getattr(args, "timeout", 4.0),
        )
        time.sleep(1.0)
        ok_start = start_all(
            foreground=getattr(args, "foreground", ""),
            no_wait_health=getattr(args, "no_wait_health", False),
        )
        return 0 if (ok_stop and ok_start) else 1
    if cmd == "status":
        status_all()
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())