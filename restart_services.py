"""重启 P8P9 web_server + A7.adapters.chat_reply daemon。

用法：python restart_services.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path(r"C:\Users\13021\Desktop\agent-skill\industrial_internet_agents")
PY = r"D:\python\python.exe"

# 关键 cmdline 子串（用于 kill + 启动后校验）
P8P9_KEY = "P8P9/web_server.py"
CHAT_KEY = "A7.adapters.chat_reply"


def kill_all_matching(keyword: str, name: str) -> int:
    """kill 所有 cmdline 含 keyword 的 python 进程。返回 kill 数。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {{ "
         f"$cmd=(Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.Id)\" -ErrorAction SilentlyContinue | "
         f"Select-Object -ExpandProperty CommandLine); "
         f"if ($cmd -like '*{keyword}*') {{ "
         f"Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; "
         f"Write-Host \"killed $name PID=$($_.Id)\" "
         f"}} }}"],
        capture_output=True, text=True,
    )
    killed = sum(1 for line in out.stdout.splitlines() if line.startswith("killed"))
    print(f"[kill] {name}: {killed} 个进程被终止")
    return killed


def start_in_background(cmd_args, log_out: Path, log_err: Path, name: str) -> int:
    print(f"[start] {name}: {' '.join(cmd_args)}", flush=True)
    log_out.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [PY] + cmd_args,
        cwd=str(PROJ),
        stdout=open(log_out, "ab"),
        stderr=open(log_err, "ab"),
        creationflags=flags,
    )
    return proc.pid


def check_alive(cmdline_keyword: str, expected_min: int = 1) -> bool:
    """检查存活进程数 >= expected_min。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Process python -ErrorAction SilentlyContinue | "
         f"Where-Object {{ "
         f"$cmd=(Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.Id)\" -ErrorAction SilentlyContinue | "
         f"Select-Object -ExpandProperty CommandLine); "
         f"$cmd -like '*{cmdline_keyword}*' "
         f"}}).Count"],
        capture_output=True, text=True,
    )
    try:
        n = int(out.stdout.strip())
    except (ValueError, TypeError):
        n = 0
    print(f"[check] {cmdline_keyword} 存活 {n} 个（期望 >= {expected_min}）")
    return n >= expected_min


def main() -> None:
    print("=" * 60)
    print("[1/4] kill 所有旧 P8P9 / chat_reply 进程")
    print("=" * 60)
    kill_all_matching(P8P9_KEY, "P8P9 web_server")
    kill_all_matching(CHAT_KEY, "chat_reply")
    time.sleep(3)

    print()
    print("=" * 60)
    print("[2/4] 启动 P8P9 web_server（端口 8089）")
    print("=" * 60)
    p8p9_pid = start_in_background(
        [P8P9_KEY],
        PROJ / "P8P9/server.out.log",
        PROJ / "P8P9/server.err.log",
        "P8P9 web_server",
    )
    print(f"P8P9 web_server PID={p8p9_pid}")
    time.sleep(5)

    print()
    print("=" * 60)
    print("[3/4] 启动 A7.adapters.chat_reply daemon")
    print("=" * 60)
    chat_pid = start_in_background(
        ["-m", "A7.adapters.chat_reply", "run", "--initial-sequence", "-1", "--interval", "1.0"],
        PROJ / "data/runtime/p8_chat_reply.log",
        PROJ / "data/runtime/p8_chat_reply.err.log",
        "chat_reply",
    )
    print(f"chat_reply PID={chat_pid}")
    time.sleep(3)

    print()
    print("=" * 60)
    print("[4/4] 校验：端口 8089 + 各进程存活")
    print("=" * 60)
    port = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-NetTCPConnection -LocalPort 8089 -ErrorAction SilentlyContinue | "
         "Select-Object LocalPort, OwningProcess, State | Format-Table -AutoSize"],
        capture_output=True, text=True,
    )
    print("port 8089:")
    print(port.stdout)

    p8p9_ok = check_alive(P8P9_KEY, 1)
    chat_ok = check_alive(CHAT_KEY, 1)

    # 列出所有 P8P9 / chat_reply 进程
    procs = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process python -ErrorAction SilentlyContinue | ForEach-Object { "
         "$cmd=(Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.Id)\" -ErrorAction SilentlyContinue | "
         "Select-Object -ExpandProperty CommandLine); "
         "if ($cmd -like '*P8P9/web_server*' -or $cmd -like '*A7.adapters.chat_reply*') { "
         "[PSCustomObject]@{Id=$_.Id; Cmd=$cmd.Substring(0, [Math]::Min(80, $cmd.Length))} "
         "} } | Format-Table -AutoSize -Wrap"],
        capture_output=True, text=True,
    )
    print("存活进程：")
    print(procs.stdout)

    # in-process 校验模块可用
    print("=" * 60)
    print("[bonus] in-process 模块校验")
    print("=" * 60)
    result = subprocess.run(
        [PY, "-c",
         "import sys; sys.path.insert(0, r'C:\\Users\\13021\\Desktop\\agent-skill\\industrial_internet_agents'); "
         "from P8P9.agent_interface import initialize_job_for_agent; "
         "from P8P9.services.card_render import _send_event_card; "
         "print('all modules OK')"],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")

    # 总结
    print("=" * 60)
    print("[done]")
    print("=" * 60)
    if p8p9_ok and chat_ok:
        print("[OK] P8P9 web_server + chat_reply are alive")
    else:
        print(f"[FAIL] P8P9 OK={p8p9_ok}, chat_reply OK={chat_ok}")
        sys.exit(1)


if __name__ == "__main__":
    main()