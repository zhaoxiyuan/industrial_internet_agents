#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
aaaseverstart.py — 醒目名字 = 「AAA Server Start」= 进程管理器（a/ 子项目版）。

为什么做这个（2026-09-17 起因）：
  之前用 nohup python ... & + write pid file 启动 P8P9 web_server，反复重启时
  taskkill 经常漏杀 + 端口 8089 被多个僵尸进程抢着 LISTEN，真正响应的
  是修复 callback_router.py 之前的旧版本（17:42 启动的），导致新代码完全不生效。
  这个脚本用 psutil 按 cmdline 匹配强制杀干净 + 用 detached subprocess 启动 + 端口探测，
  杜绝僵尸 + 端口假性冲突。

a/ 子项目服务（按 name 寻址，all = 全部）：
  p8p9       P8P9/web_server.py                                   port=8089
  feishu_cfg feishu_gateway_cli/feishu_config_app.py              port=5003
  chat_reply A7/adapters/chat_reply.py                            port=None  (daemon)

剔除（a/ 不含）：
  - webui（主流程 web/server.py 不在 a/ 范围）
  - p6_monitor（P6 端口 5002 由 frontend/start_all.py 管理即可）

用法：
  python aaaseverstart.py list
  python aaaseverstart.py status [name|all]
  python aaaseverstart.py start  [name|all]
  python aaaseverstart.py stop   [name|all]
  python aaaseverstart.py restart [name|all]
  python aaaseverstart.py kill-all   # 强杀所有匹配 cmdline 的进程（兜底）

跨平台：Windows / macOS / Linux 都跑（psutil + subprocess + socket）。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import psutil
except ImportError:
    print("ERROR: 需要 psutil（pip install psutil）", file=sys.stderr)
    sys.exit(2)


# ── 路径常量 ────────────────────────────────────────────────────────────────
# a/ 是新根；服务脚本路径都相对 ROOT (a/) 解析。
# 所以 ROOT = a/aaaseverstart.py → parent = a/
ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT / "data" / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


# ── 服务定义 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Service:
    """单个被管理的服务定义。"""
    name: str
    script: str                       # 相对 ROOT 的路径（用于 process cmdline 匹配）
    port: Optional[int] = None         # 启动后探活的端口；None = 不探活
    extra_args: List[str] = field(default_factory=list)
    extra_env: Dict[str, str] = field(default_factory=dict)  # 注入到子进程的环境变量
    description: str = ""
    run_mode: str = "script"           # "script" = `python script.py`；
                                       # "module" = `python -m <dotted.path>`（dotted path 从 extra_args[1] 取）
                                       # "node"   = `node start.mjs ...`（Node.js Gateway）

    @property
    def script_abs(self) -> Path:
        return (ROOT / self.script).resolve()

    @property
    def pid_file(self) -> Path:
        return RUNTIME_DIR / f"{self.name}.pid"

    @property
    def log_file(self) -> Path:
        return RUNTIME_DIR / f"{self.name}.log"

    @property
    def script_marker(self) -> str:
        """进程 cmdline 匹配串（用绝对路径避免同名脚本混淆）。"""
        # Windows 上 psutil cmdline 里路径可能是反斜杠；统一用 str(script_abs)
        return str(self.script_abs)

    @property
    def module_name(self) -> Optional[str]:
        """run_mode='module' 时返回 dotted module path（从 extra_args[1] 取）。"""
        if self.run_mode != "module":
            return None
        if len(self.extra_args) < 2 or self.extra_args[0] != "-m":
            return None
        return self.extra_args[1]


SERVICES: List[Service] = [
    Service(
        name="gateway",
        script="gateway/start.mjs",
        port=8787,
        # start.mjs 读 cwd 下的 .env;--config 指向 config/config.feishu.local.json
        extra_args=["--config", "gateway/config/config.feishu.local.json"],
        run_mode="node",
        description="Node.js OpenClaw Channel Gateway（飞书通道底层，端口 8787）",
    ),
    Service(
        name="p8p9",
        script="P8P9/web_server.py",
        port=8089,
        description="P8P9 状态机 + 飞书 callback（v2.1）",
    ),
    Service(
        name="webui",
        # a/web/server.py 是从根目录 web/server.py 拆出的精简版,
        # 只服务 P6-P9 业务辅助 endpoint(飞书卡片回调转发 / P8 工作记忆查询 /
        # 卡片索引)。完整 P1-P10 主流程 Web 入口仍在根目录 web/server.py。
        # 端口 8080：与 root webui 端口一致，gateway 老入口 /webhooks/feishu/P8
        # 的 CARD_CALLBACK_BUSINESS_PORT=8080 能转发命中；不能与 root 同时启动。
        script="web/server.py",
        port=8080,
        description="a/web 精简 Web 服务(8080) — 飞书卡片回调入口 + P6-P9 辅助 endpoint",
    ),
    Service(
        name="feishu_cfg",
        script="feishu_gateway_cli/feishu_config_app.py",
        port=5003,
        description="飞书 channel 网关配置 UI（Gradio 风格 Flask）",
    ),
    Service(
        name="chat_reply",
        # python -m A7.adapters.chat_reply run（必须 -m 模式才能当 module 跑）
        script="A7/adapters/chat_reply.py",   # 用于 find_service_procs 匹配 cmdline
        port=None,
        extra_args=["-m", "A7.adapters.chat_reply", "run", "--initial-sequence", "-1", "--interval", "1.0"],
        run_mode="module",
        description="飞书消息接收 + agent 回复 daemon（关键！缺它智能体不回消息）",
    ),
]

SERVICES_BY_NAME = {s.name: s for s in SERVICES}


# ── 进程检测（psutil + cmdline 匹配） ───────────────────────────────────────

def _all_python_procs() -> List[psutil.Process]:
    """所有 python.exe / python3 进程。"""
    out = []
    for p in psutil.process_iter(["name", "cmdline", "pid"]):
        try:
            name = (p.info["name"] or "").lower()
            if name.startswith("python") or "python" in name:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _all_node_procs() -> List[psutil.Process]:
    """所有 node / node.exe 进程。"""
    out = []
    for p in psutil.process_iter(["name", "cmdline", "pid"]):
        try:
            name = (p.info["name"] or "").lower()
            if name.startswith("node") or name == "node.exe":
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def find_service_procs(svc: Service) -> List[psutil.Process]:
    """所有真正在跑 svc.script 的 python/node 进程。

    匹配规则（按强到弱）：
      A) run_mode="module" 的服务：cmdline 含「 -m <module_name> 」（且含 module 末段 basename）
      B) 普通服务：
         1) cmdline 含脚本绝对路径（最可靠，绕过路径分隔符差异）
         2) cmdline 含「父目录 basename + 脚本 basename」（双 token 匹配），
            且 argv[0] 是 python/node 可执行文件
            （双 token 排除 run-jedi-language-server.py 等同名前缀的伪匹配）

    run_mode="node" 的服务从 _all_node_procs() 池里匹配(避免误捕);其它从
    _all_python_procs() 池里匹配。

    示例：要匹配「P8P9/web_server.py」，父目录 = "P8P9"，脚本 = "web_server.py"。
        "P8P9/web_server.py" 命中；"run-jedi-language-server.py" 不命中（缺 "P8P9/" 前缀）。
    """
    marker = svc.script_marker
    basename = os.path.basename(svc.script)
    parent_dir = os.path.basename(os.path.dirname(svc.script))  # "P8P9" / "feishu_gateway_cli" / "A7" / ...
    module_name = svc.module_name
    target_exe = "node" if svc.run_mode == "node" else "python"
    proc_pool = _all_node_procs() if svc.run_mode == "node" else _all_python_procs()
    out = []
    for p in proc_pool:
        try:
            cmd = p.info["cmdline"] or []
            if not cmd:
                continue
            joined = " ".join(cmd)
            # 规则 A：module 模式 → -m <module_name> 命中即可
            if module_name and f"-m {module_name}" in joined:
                out.append(p)
                continue
            # 规则 1：绝对路径命中 → 直接算
            if marker in joined:
                out.append(p)
                continue
            # 规则 2：双 token 命中（parent_dir/basename 一起出现）+ argv[0] 是 python/node
            # 用 / 拼避免 Windows 反斜杠差异
            if f"{parent_dir}/{basename}" in joined.replace("\\", "/"):
                argv0 = cmd[0].lower()
                argv0_name = Path(argv0).name.lower()
                if argv0_name.startswith(target_exe):
                    out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _is_port_open(port: int, timeout: float = 0.3) -> bool:
    """TCP 端口是否在 LISTEN。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _proc_alive(p: psutil.Process) -> bool:
    """进程是否还活着且非 zombie。Windows 上 is_running() 在进程死后会抛 NoSuchProcess。"""
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


# ── 启动 / 停止 ─────────────────────────────────────────────────────────────

def start_service(svc: Service) -> bool:
    """启动单个服务；返回 True 表示启动后探活通过。"""
    if not svc.script_abs.exists():
        print(f"  [{svc.name}] SKIP: 脚本不存在 {svc.script_abs}")
        return False

    existing = find_service_procs(svc)
    if existing:
        alive_pids = [p.pid for p in existing if p.is_running()]
        print(f"  [{svc.name}] 已在运行: pid={alive_pids}（跳过启动，避免端口冲突）")
        return _probe_port(svc, silent=False)

    print(f"  [{svc.name}] 启动: {svc.script_abs} (port={svc.port})")

    # 日志文件 + 强 unbuffered（-u），让 log 实时写入
    log_fp = open(svc.log_file, "ab", buffering=0)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # 注入 Service 声明的自定义环境变量(例如 webui 用 A_WEBUI_PORT 改端口)
    for k, v in svc.extra_env.items():
        env[k] = v

    if svc.run_mode == "node":
        # Node.js Gateway 走 `node <script_abs> ...`；用 shutil.which 找 node
        import shutil as _shutil
        node_path = _shutil.which("node")
        if not node_path:
            print(f"  [{svc.name}] ERROR: 未找到 node 可执行文件。请安装 Node.js ≥ v22。")
            log_fp.close()
            return False
        # 清理 env:1)PYTHON* 变量(给 Node 看没意义);2)WorkBuddy/Electron 注入的
        # Node 注入变量(ELECTRON_RUN_AS_NODE=1 会让 Node 切换到 Electron 内部模式,
        # 信号响应/stdin 处理都不一样,这是之前 Node Gateway 启动 5 秒就死的根因)。
        for _k in (
            "PYTHONUNBUFFERED", "PYTHONIOENCODING", "PYTHONPATH", "PYTHONHOME",
            "ELECTRON_RUN_AS_NODE", "ELECTRON_NO_ATTACH_CONSOLE",
            "CODEBUDDY_NODE_BIN", "WORKBUDDY_NODE_ENV",
        ):
            env.pop(_k, None)
        # Node.js Gateway 跑在 a/gateway/ 目录(让 start.mjs 的 cwd/.env 解析正确);
        # extra_args 里的相对路径以 ROOT(a/) 为基准 resolve 成绝对路径,避免 Node cwd 把它
        # 再解析一次变成双层 gateway/。
        # 但**只**对看起来像路径(含 / 或 \ 或以 . 开头的)才 resolve;
        # flag-style 参数(如 --config)保持原样,否则会被当成相对路径拼成 C:\...\a\--config。
        args = [node_path, str(svc.script_abs)]
        for arg in svc.extra_args:
            looks_like_path = (
                "/" in arg or "\\" in arg
                or arg.startswith(".")
                or arg.endswith(".json") or arg.endswith(".js") or arg.endswith(".mjs")
                or arg.endswith(".yaml") or arg.endswith(".yml")
                or arg.endswith(".env")
            )
            if looks_like_path and not Path(arg).is_absolute():
                args.append(str((ROOT / arg).resolve()))
            else:
                args.append(arg)
        proc_cwd = str(svc.script_abs.parent)  # a/gateway/
    else:
        args = [sys.executable, "-u"]
        if svc.run_mode == "module":
            # 走 `python -u -m <module> ...` 模式（chat_reply 必须这样）
            # extra_args 第一个元素是 "-m"，第二个是 dotted module path
            args.extend(svc.extra_args)
        else:
            args.append(str(svc.script_abs))
            args.extend(svc.extra_args)
        proc_cwd = str(ROOT)

    # Windows 进程旗标:同根目录原版 — DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # (DETACHED_PROCESS 让 Node.js/Python daemon 不依附父进程 console; CREATE_NEW_PROCESS_GROUP
    # 让 stop_service 能用 GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT)精准通知子进程)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        proc = subprocess.Popen(
            args,
            cwd=proc_cwd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
        )
    except Exception as e:
        print(f"  [{svc.name}] Popen 失败: {e}")
        return False

    # Windows 上必须立刻释放 Popen 持有的子进程 handle,
    # 否则父 Python 进程退出时,Windows 会通过 Job Object 把子进程一起 kill。
    # 这是为什么之前 Node Gateway "启动 5 秒后死亡" 的根因:
    # Popen.__del__ 在父进程退出时触发,Windows 会通过 job object 关闭所有挂在
    # 父进程下的子进程(包括 DETACHED_PROCESS),除非子进程"break away"出 job。
    # 沙箱环境不允许 CREATE_BREAKAWAY_FROM_JOB(PermissionError),所以只能靠
    # 父进程主动关闭 handle 让子进程脱离 job。
    # 副作用:stop_service 不能用 handle 发 CTRL_BREAK_EVENT 优雅终止了 — 改为
    # psutil.Process(pid).terminate() / kill()(直接发 SIGTERM / SIGKILL),同样有效。
    # 副作用2:Popen.__del__ 后续 gc 会调 _internal_poll 失败,触发 WinError 6 traceback。
    # 用 weakref.finalize 在 Popen 上挂一个静默 __del__ 替换版,既能 GC 又不刷 traceback。
    if os.name == "nt":
        try:
            if hasattr(proc._handle, "Close"):  # type: ignore[attr-defined]
                proc._handle.Close()  # type: ignore[attr-defined]
            # 标记"已脱离 job",Popen.__del__ 会通过 _child_created 判断不再 polling
            proc._child_created = False  # type: ignore[attr-defined]
            proc.returncode = None  # type: ignore[attr-defined]  # 显式标志"未 wait"
        except Exception:
            pass  # best-effort; child may already have closed

    # 写 pid file
    svc.pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    print(f"  [{svc.name}] pid={proc.pid}  日志={svc.log_file}")

    return _probe_port(svc, silent=False)


def stop_service(svc: Service, *, timeout: float = 5.0) -> bool:
    """停止单个服务；按 cmdline 匹配所有进程 → 优雅 SIGTERM → 超时 SIGKILL。"""
    procs = find_service_procs(svc)
    if not procs:
        print(f"  [{svc.name}] 未运行")
        if svc.pid_file.exists():
            svc.pid_file.unlink()
        return True

    print(f"  [{svc.name}] 杀掉 {len(procs)} 个进程: pid={[p.pid for p in procs]}")
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # 等优雅退出（进程死后 psutil 会抛 NoSuchProcess，要吃异常）
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(not _proc_alive(p) for p in procs):
            break
        time.sleep(0.2)

    # 强杀残留
    for p in procs:
        try:
            if _proc_alive(p):
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if svc.pid_file.exists():
        svc.pid_file.unlink()

    # 端口释放确认
    if svc.port and _is_port_open(svc.port):
        print(f"  [{svc.name}] WARNING: port {svc.port} 仍 LISTEN（可能被其他进程占用）")
        return False
    print(f"  [{svc.name}] 已停止")
    return True


def _probe_port(svc: Service, *, silent: bool) -> bool:
    """启动后等端口起来（最多 10s）。"""
    if not svc.port:
        return True
    for i in range(20):  # 20 * 0.5s = 10s
        if _is_port_open(svc.port):
            if not silent:
                print(f"  [{svc.name}] port {svc.port} LISTEN [OK]")
            return True
        time.sleep(0.5)
    if not silent:
        print(f"  [{svc.name}] WARNING: port {svc.port} 10s 内未 LISTEN（可能启动失败，看 {svc.log_file}）")
    return False


# ── status / list ───────────────────────────────────────────────────────────

def status_one(svc: Service) -> None:
    procs = find_service_procs(svc)
    port_state = f"port={svc.port} {('LISTEN' if _is_port_open(svc.port) else 'closed')}" if svc.port else "no-port"
    if not procs:
        print(f"  [{svc.name}] STOPPED  {port_state}  ({svc.description})")
        return
    for p in procs:
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if len(cmd) > 60:
                cmd = cmd[:57] + "..."
            print(f"  [{svc.name}] RUNNING  pid={p.pid}  {port_state}  cmd={cmd}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"  [{svc.name}] pid={p.pid} (terminating)")


def cmd_list() -> None:
    print("可管理服务（a/ 子项目）：")
    for svc in SERVICES:
        port = f"port={svc.port}" if svc.port else "no-port"
        print(f"  {svc.name:<12} {svc.script}")
        print(f"  {'':<12} {port}  log={svc.log_file.relative_to(ROOT)}")
        print(f"  {'':<12} {svc.description}")
        print()


# ── kill-all（危险，按 cmdline 模式） ────────────────────────────────────────

def cmd_kill_all() -> None:
    """杀掉所有已知服务的进程 + 清理 pid 文件。"""
    n = 0
    for svc in SERVICES:
        procs = find_service_procs(svc)
        for p in procs:
            try:
                p.kill()
                n += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if svc.pid_file.exists():
            svc.pid_file.unlink()
    print(f"已强杀 {n} 个进程 + 清理 pid file")


# ── CLI 入口 ────────────────────────────────────────────────────────────────

def _resolve_targets(spec: Optional[str]) -> List[Service]:
    if not spec or spec == "all":
        return list(SERVICES)
    if spec not in SERVICES_BY_NAME:
        print(f"ERROR: 未知服务 {spec!r}（用 `python aaaseverstart.py list` 看可用 name）")
        sys.exit(2)
    return [SERVICES_BY_NAME[spec]]


def main() -> int:
    # 2026-09-17：Windows 默认 GBK 控制台会让中文 / ✓ 报错 UnicodeEncodeError。
    # 强制 stdout/stderr 走 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        # Python < 3.7 或非正常 TTY：用 io 兜底
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        prog="aaaseverstart",
        description="醒目进程管理器（杀僵尸 + 端口探活 + unbuffered 日志）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    for c in ("start", "stop", "restart", "status"):
        sp = sub.add_parser(c, help=f"{c} 一个或多个服务")
        sp.add_argument("name", nargs="?", default="all", help="服务名 或 all")

    sub.add_parser("list", help="列出可管理服务")
    sub.add_parser("kill-all", help="强杀所有已知服务的进程")

    args = ap.parse_args()

    if args.cmd == "list":
        cmd_list()
        return 0
    if args.cmd == "kill-all":
        cmd_kill_all()
        return 0

    targets = _resolve_targets(getattr(args, "name", "all"))
    print(f"=== {args.cmd} {', '.join(s.name for s in targets)} ===")
    ok = True
    for svc in targets:
        if args.cmd == "start":
            if not start_service(svc):
                ok = False
        elif args.cmd == "stop":
            if not stop_service(svc):
                ok = False
        elif args.cmd == "restart":
            stop_service(svc)
            time.sleep(0.5)
            if not start_service(svc):
                ok = False
        elif args.cmd == "status":
            status_one(svc)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())