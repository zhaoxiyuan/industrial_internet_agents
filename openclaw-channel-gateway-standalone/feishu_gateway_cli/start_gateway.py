"""feishu_gateway_cli.start_gateway — 用 Python 启动 OpenClaw Channel Gateway Standalone。

================================================================================
职责
================================================================================

把 `openclaw-channel-gateway-standalone/` 子目录的 Node Gateway 启动/停止/
查询/重启封装成 Python 命令，省去手动 `cd` + 找 Node + 拼参数。

核心能力：
- start_gateway()         启动 Gateway（前台 + 后台两种模式）
- stop_gateway()          通过 PID 文件 + 端口扫描停止
- gateway_status()        健康检查 + 进程状态 + 日志 tail
- restart_gateway()       stop + start

================================================================================
为什么需要这个
================================================================================

启动 Gateway 需要：
1. 切换到 `openclaw-channel-gateway-standalone/` 子目录（start.mjs 读 cwd/.env）
2. 保证 `node` 在 PATH 里
3. 启动后等 `/healthz` 通
4. 把 PID 记下来方便后续 stop

直接用 shell 也能做，但 P8 的端到端测试 + 开发循环会反复启动 Gateway，
Python 封装让 `python -m feishu_gateway_cli.start_gateway start` 一行搞定。

================================================================================
用法
================================================================================

::

    # 前台启动（Ctrl+C 停止）
    python -m feishu_gateway_cli.start_gateway start --foreground

    # 后台启动（默认；写 PID 文件 + 等待 /healthz 通）
    python -m feishu_gateway_cli.start_gateway start

    # 查询状态
    python -m feishu_gateway_cli.start_gateway status

    # 停止
    python -m feishu_gateway_cli.start_gateway stop

    # 重启
    python -m feishu_gateway_cli.start_gateway restart

    # 自定义 config / host / port
    python -m feishu_gateway_cli.start_gateway start \\
        --config config/config.feishu.local.json \\
        --host 127.0.0.1 --port 8787 \\
        --health-timeout 30

    # 也可作为库调用
    from feishu_gateway_cli.start_gateway import start_gateway, stop_gateway, gateway_status
    start_gateway(background=True, wait_health=True)
    print(gateway_status())
    stop_gateway()

================================================================================
环境变量（与 Gateway 子目录 .env 对应）
================================================================================

start_gateway() 会把 ``openclaw-channel-gateway-standalone/.env`` 读出来
作为子进程的额外环境变量（不覆盖已有 process.env）。

Gateway 自身读：``CG_API_KEY`` / ``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``
/ ``FEISHU_VERIFICATION_TOKEN`` 等。

================================================================================
设计权衡
================================================================================

- **前台 vs 后台**：
  前台（foreground）= 同步阻塞，子进程就是当前 shell；适合开发调试。
  后台（background）= 写 PID 文件并立即返回；适合自动化。

- **Node 查找**：先看 PATH，找不到则给清晰错误（不会自动装 Node）。

- **PID 管理**：PID 写到 ``<gateway_dir>/.gateway.pid``，避免冲突。
  进程已退出但 PID 文件残留时自动清理。

- **/healthz 等待**：默认 30 秒；后台模式必须等通过才算启动成功。

- **跨平台**：Windows 用 ``subprocess.Popen`` + ``CREATE_NEW_PROCESS_GROUP``；
  POSIX 用 ``start_new_session=True`` 让子进程独立于父进程组。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


# ============================================================
# 日志
# ============================================================

logger = logging.getLogger("a7.start_gateway")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ============================================================
# 路径
# ============================================================

PKG_DIR: Path = Path(__file__).resolve().parent              # feishu_gateway_cli/
STANDALONE_DIR: Path = PKG_DIR.parent                        # openclaw-channel-gateway-standalone/
PROJECT_ROOT: Path = STANDALONE_DIR.parent                   # 项目根
GATEWAY_DIR: Path = PROJECT_ROOT / "openclaw-channel-gateway-standalone"
DEFAULT_CONFIG: Path = GATEWAY_DIR / "config" / "config.feishu.local.json"
PID_FILE: Path = GATEWAY_DIR / ".gateway.pid"
LOG_FILE: Path = GATEWAY_DIR / "gateway.python.log"


# ============================================================
# 数据类
# ============================================================

@dataclass
class GatewayProcess:
    """Gateway 进程状态。"""

    pid: Optional[int] = None
    host: str = "127.0.0.1"
    port: int = 8787
    background: bool = True
    config_path: Optional[str] = None
    started_at: Optional[float] = None        # time.time()
    health_ok: bool = False
    ready_ok: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "background": self.background,
            "config_path": self.config_path,
            "started_at": self.started_at,
            "uptime_seconds": (time.time() - self.started_at) if self.started_at else None,
            "health_ok": self.health_ok,
            "ready_ok": self.ready_ok,
            "error": self.error,
        }


# ============================================================
# .env 解析（与 start.mjs 一致的 KEY=VALUE 简化版）
# ============================================================

def _parse_env_file(path: Path) -> Dict[str, str]:
    """简化版 dotenv 解析：忽略注释、空行、export 前缀。"""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 剥外层单/双引号
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        env[key] = value
    return env


# ============================================================
# 内部辅助
# ============================================================

def _find_node() -> Optional[str]:
    """查找 node 可执行文件。"""
    node = shutil.which("node")
    return node


def _port_in_use(host: str, port: int) -> bool:
    """简易端口占用检查（避免依赖 psutil）。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.connect((host, port))
            return True
        except (OSError, socket.timeout):
            return False


def _wait_for_health(
    host: str,
    port: int,
    timeout: float,
    interval: float = 0.5,
) -> bool:
    """等待 /healthz 返回 200。"""
    url = f"http://{host}:{port}/healthz"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(interval)
    return False


def _check_ready(host: str, port: int, api_key: str, timeout: float = 5.0) -> bool:
    """检查 /readyz（需鉴权）。"""
    url = f"http://{host}:{port}/readyz"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否存活。"""
    if pid is None or pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows：OpenProcess + GetExitCodeProcess
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle == 0:
                return False
            STILL_ACTIVE = 259
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return exit_code.value == STILL_ACTIVE
        else:
            # POSIX：kill -0
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


# ============================================================
# 公开 API
# ============================================================

def start_gateway(
    *,
    config: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    background: bool = True,
    wait_health: bool = True,
    health_timeout: float = 30.0,
) -> GatewayProcess:
    """启动 Gateway 进程。

    Args:
        config:      配置文件路径；默认 ``config/config.feishu.local.json``。
        host:        健康检查地址。
        port:        健康检查端口。
        background:  True=写 PID 文件后立即返回；False=前台阻塞运行。
        wait_health: 后台模式下是否等 /healthz 通。
        health_timeout: 等待 /healthz 的最长时间（秒）。

    Returns:
        :class:`GatewayProcess`，含 pid / health_ok / error 等字段。

    Raises:
        FileNotFoundError: Node 未装 或 gateway 目录缺失。
        RuntimeError:      端口已被占用 / 启动后 health 检查失败。
    """
    if not GATEWAY_DIR.exists():
        raise FileNotFoundError(
            f"Gateway 子目录不存在: {GATEWAY_DIR}\n"
            "请确认 openclaw-channel-gateway-standalone/ 已 clone 到项目根。"
        )

    node = _find_node()
    if not node:
        raise FileNotFoundError(
            "未找到 node 可执行文件。请安装 Node.js ≥ v18 后重试。"
        )

    if _port_in_use(host, port):
        raise RuntimeError(
            f"端口 {port} 已被占用。先调用 stop_gateway() 或 `taskkill /F /PID <PID>`。"
        )

    config_path = config or DEFAULT_CONFIG
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    # 合并 env：子进程继承 process.env，但 gateway .env 不覆盖已有同名 key
    gw_env = _parse_env_file(GATEWAY_DIR / ".env")
    child_env = os.environ.copy()
    for k, v in gw_env.items():
        child_env.setdefault(k, v)

    cmd = [node, "start.mjs", "--config", str(config_path)]

    logger.info("启动 Gateway: %s (cwd=%s, background=%s)", cmd, GATEWAY_DIR, background)

    state = GatewayProcess(
        host=host,
        port=port,
        background=background,
        config_path=str(config_path),
        started_at=time.time(),
    )

    if background:
        # 后台：把 stdout/stderr 重定向到 LOG_FILE
        log_handle = open(LOG_FILE, "ab", buffering=0)
        kwargs: Dict[str, Any] = {
            "cwd": str(GATEWAY_DIR),
            "env": child_env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        finally:
            log_handle.close()

        _write_pid(proc.pid)
        state.pid = proc.pid
        logger.info("Gateway 后台启动: pid=%s, log=%s", proc.pid, LOG_FILE)

        if wait_health:
            state.health_ok = _wait_for_health(host, port, health_timeout)
            if not state.health_ok:
                state.error = f"/healthz 在 {health_timeout}s 内未通"
                logger.error(state.error)
                raise RuntimeError(state.error)
            # /readyz 检查（用 gateway .env 的 CG_API_KEY）
            api_key = gw_env.get("CG_API_KEY", "")
            if api_key:
                state.ready_ok = _check_ready(host, port, api_key)
                logger.info("/readyz: %s", "OK" if state.ready_ok else "FAIL")
        return state

    # 前台：阻塞；Ctrl+C 由父进程 SIGINT，子进程也会收
    try:
        return_code = subprocess.call(
            cmd,
            cwd=str(GATEWAY_DIR),
            env=child_env,
        )
        state.error = f"前台进程退出 code={return_code}"
        state.pid = os.getpid()  # 父进程 pid，标记而已
        return state
    except KeyboardInterrupt:
        state.error = "用户中断 (Ctrl+C)"
        return state


def stop_gateway(
    *,
    timeout: float = 10.0,
    force: bool = False,
) -> GatewayProcess:
    """停止 Gateway（通过 PID 文件）。

    Args:
        timeout: 等待进程退出最长时间（秒）。
        force:   True 时超过 timeout 强制 kill。

    Returns:
        :class:`GatewayProcess`，含 pid / error 等。
    """
    pid = _read_pid()
    state = GatewayProcess(
        pid=pid,
        started_at=None,
        error=None,
    )

    if pid is None:
        state.error = "未找到 PID 文件（可能 Gateway 未启动，或在别处启动的）"
        logger.warning(state.error)
        _clear_pid()
        return state

    if not _pid_alive(pid):
        state.error = f"PID={pid} 已退出；清理 PID 文件"
        logger.info(state.error)
        _clear_pid()
        return state

    logger.info("停止 Gateway: pid=%s (force=%s)", pid, force)

    try:
        if os.name == "nt":
            # Windows：发 CTRL_BREAK_EVENT（仅当用 CREATE_NEW_PROCESS_GROUP 时生效）
            import ctypes
            CTRL_BREAK_EVENT = 1
            kernel32 = ctypes.windll.kernel32
            kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)
        else:
            os.kill(pid, signal.SIGTERM)

        # 等退出
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _pid_alive(pid):
                break
            time.sleep(0.2)

        if _pid_alive(pid):
            if not force:
                state.error = f"进程 {timeout}s 内未退出；用 force=True 强杀"
                logger.warning(state.error)
                return state
            # 强杀
            logger.warning("强杀进程 pid=%s", pid)
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            time.sleep(0.3)
    except (OSError, PermissionError) as exc:
        state.error = f"停止失败: {exc}"
        logger.exception("停止失败")
    finally:
        _clear_pid()

    state.health_ok = not _port_in_use(state.host or "127.0.0.1", state.port or 8787)
    return state


def gateway_status(
    host: str = "127.0.0.1",
    port: int = 8787,
) -> GatewayProcess:
    """查询 Gateway 状态（PID + health + ready + 日志位置）。

    Returns:
        :class:`GatewayProcess`，含 health_ok / ready_ok / pid 等。
    """
    pid = _read_pid()
    state = GatewayProcess(
        pid=pid,
        host=host,
        port=port,
        started_at=None,
    )

    if pid is not None and not _pid_alive(pid):
        state.error = f"stale PID file: pid={pid} 已退出"
        logger.warning(state.error)
        _clear_pid()
        state.pid = None

    if _port_in_use(host, port):
        state.health_ok = _wait_for_health(host, port, timeout=2.0)
        if state.health_ok:
            gw_env = _parse_env_file(GATEWAY_DIR / ".env")
            api_key = gw_env.get("CG_API_KEY", "")
            if api_key:
                state.ready_ok = _check_ready(host, port, api_key)

    return state


def restart_gateway(
    *,
    config: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    background: bool = True,
    wait_health: bool = True,
    health_timeout: float = 30.0,
) -> GatewayProcess:
    """重启 Gateway（stop + start）。

    返回启动后的 :class:`GatewayProcess`。
    """
    stop_state = stop_gateway()
    if stop_state.error and "未找到" not in stop_state.error:
        logger.warning("stop 阶段警告: %s", stop_state.error)
    # 给端口一点时间释放
    time.sleep(0.5)
    return start_gateway(
        config=config,
        host=host,
        port=port,
        background=background,
        wait_health=wait_health,
        health_timeout=health_timeout,
    )


# ============================================================
# CLI
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m feishu_gateway_cli.start_gateway",
        description="用 Python 启动/停止/查询/重启 OpenClaw Channel Gateway Standalone。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None,
                        help=f"Gateway 配置文件（默认 {DEFAULT_CONFIG.name}）")
    common.add_argument("--host", default="127.0.0.1", help="健康检查地址")
    common.add_argument("--port", type=int, default=8787, help="健康检查端口")
    common.add_argument("--health-timeout", type=float, default=30.0,
                        help="等 /healthz 的最长时间（秒）")

    p_start = sub.add_parser("start", parents=[common], help="启动 Gateway")
    p_start.add_argument("--foreground", action="store_true",
                         help="前台运行（阻塞；Ctrl+C 停止）")
    p_start.add_argument("--no-wait-health", action="store_true",
                         help="不等待 /healthz（默认等待）")

    p_stop = sub.add_parser("stop", help="停止 Gateway（按 PID 文件）")
    p_stop.add_argument("--timeout", type=float, default=10.0, help="等待退出秒数")
    p_stop.add_argument("--force", action="store_true", help="超时后强杀")

    p_status = sub.add_parser("status", parents=[common], help="查询状态")
    p_restart = sub.add_parser("restart", parents=[common], help="重启 Gateway")
    p_restart.add_argument("--foreground", action="store_true")
    p_restart.add_argument("--no-wait-health", action="store_true")

    return parser


def _main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "start":
        try:
            state = start_gateway(
                config=getattr(args, "config", None),
                host=getattr(args, "host", "127.0.0.1"),
                port=getattr(args, "port", 8787),
                background=not args.foreground,
                wait_health=not args.no_wait_health,
                health_timeout=args.health_timeout,
            )
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"[start] FAILED: {exc}")
            return 2
        print("[start] OK")
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0 if state.health_ok else 1

    if args.cmd == "stop":
        state = stop_gateway(timeout=args.timeout, force=args.force)
        print("[stop]")
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0 if not state.error else 1

    if args.cmd == "status":
        state = gateway_status(host=args.host, port=args.port)
        print("[status]")
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        # 顺便显示 PID / log 文件位置
        print(f"[paths]")
        print(f"  gateway_dir = {GATEWAY_DIR}")
        print(f"  pid_file    = {PID_FILE}")
        print(f"  log_file    = {LOG_FILE}")
        return 0 if state.health_ok else 1

    if args.cmd == "restart":
        state = restart_gateway(
            config=args.config,
            host=args.host,
            port=args.port,
            background=not args.foreground,
            wait_health=not args.no_wait_health,
            health_timeout=args.health_timeout,
        )
        print("[restart]")
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0 if state.health_ok else 1

    parser.print_help()
    return 1


__all__ = [
    "GatewayProcess",
    "start_gateway",
    "stop_gateway",
    "gateway_status",
    "restart_gateway",
    "GATEWAY_DIR",
    "DEFAULT_CONFIG",
    "PID_FILE",
    "LOG_FILE",
]


if __name__ == "__main__":
    raise SystemExit(_main())