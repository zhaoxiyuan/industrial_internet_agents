"""P8 服务管理器 — start_service 端口预检单元测试（2026-08-20 新增）。

背景：start_service 原逻辑只读 .pid 文件判断进程是否在跑。当 .pid 文件是
脏值（指向已死 PID）但实际端口被某健康 Node Gateway 占用时，原逻辑会
错误地调 start_gateway 子命令 → 子命令报"端口已被占用"→ 失败。

修复：start_service 在 PID 检查前加端口预检：
- 端口被占 + /healthz 通 → 视为"已在运行"，返回 True
- 端口被占 + /healthz 不通 → 报错（不盲目启动覆盖），返回 False
- 端口空闲 → 走原有 PID 检查 + 启动流程

测试策略：mock _port_in_use / _wait_health / _port_holder_pid / _read_pid /
_is_pid_alive + subprocess.Popen/run，覆盖 4 种关键分支。

**测试隔离（2026-08-20 修复）**：每个测试用 tmp_path 重写 SERVICES[name]["pid_file"]
+ log_file，避免污染真实运行时目录（如 A7/data/runtime/p8_chat_reply.pid）。
之前版本直接把 mock 的 Popen().pid 写进了真实 PID 文件，导致后续
p8_service_manager start 报"启动后立即退出"。
"""
from __future__ import annotations

import os
import sys
import socket
from pathlib import Path
from unittest import mock

# 确保从仓库根目录 import
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

from A7.adapters import p8_service_manager as psm  # noqa: E402


# ============================================================
# 夹具：每个测试把 SERVICES[name] 的 pid_file / log_file 重定向到 tmp_path
# ============================================================

@pytest.fixture
def isolated_services(tmp_path, monkeypatch):
    """把 SERVICES 字典复制一份，把每个服务的 pid_file / log_file 重定向到 tmp_path。

    返回改写后的 SERVICES 字典（已 monkeypatch 进 psm 模块）。
    测试结束后 monkeypatch 自动还原。
    """
    new_services = {}
    for name, cfg in psm.SERVICES.items():
        cfg_copy = dict(cfg)
        cfg_copy["pid_file"] = tmp_path / f"{name}.pid"
        cfg_copy["log_file"] = tmp_path / f"{name}.log"
        # 重建 cfg 里的子 dict（如 web 的 env）
        new_services[name] = cfg_copy
    monkeypatch.setattr(psm, "SERVICES", new_services)
    return new_services


# ============================================================
# PSM-01: 端口被占 + healthz 通 → 短路返回 True
# ============================================================

def test_psm_01_port_held_and_healthy_short_circuits(isolated_services):
    """.pid 文件脏值（指向已死 PID 99999）+ 端口被占 + /healthz 通
    → start_service 应识别为"已在运行"并返回 True，**不**调 cmd_start。
    """
    cfg = isolated_services["gateway"]

    with mock.patch.object(psm, "_read_pid", return_value=99999) as m_read, \
         mock.patch.object(psm, "_port_in_use", return_value=True) as m_port, \
         mock.patch.object(psm, "_port_holder_pid", return_value=18848) as m_holder, \
         mock.patch.object(psm, "_wait_health", return_value=True) as m_health, \
         mock.patch.object(psm, "_is_pid_alive", return_value=False) as m_alive, \
         mock.patch.object(psm.subprocess, "Popen") as m_popen, \
         mock.patch.object(psm.subprocess, "run") as m_run:

        ok = psm.start_service("gateway", foreground=False)

    assert ok is True, "端口被占 + healthz 通应短路返回 True"
    m_port.assert_called_once()
    m_holder.assert_called_once()
    m_health.assert_called_once()
    # _read_pid 应**不**被调用（端口预检已短路）
    m_read.assert_not_called()
    m_alive.assert_not_called()
    m_popen.assert_not_called()
    m_run.assert_not_called()
    # PID 文件不应被写入
    assert not cfg["pid_file"].exists(), f"端口短路不应写 PID 文件：{cfg['pid_file']}"


# ============================================================
# PSM-02: 端口被占 + healthz 不通 → 报错返回 False（不盲目启动）
# ============================================================

def test_psm_02_port_held_and_unhealthy_returns_false(isolated_services):
    """端口被某非 gateway 进程占着 + healthz 不通
    → start_service 应报错返回 False，给出占用方 PID + 修复建议。
    """
    cfg = isolated_services["gateway"]

    with mock.patch.object(psm, "_port_in_use", return_value=True), \
         mock.patch.object(psm, "_port_holder_pid", return_value=99999), \
         mock.patch.object(psm, "_wait_health", return_value=False), \
         mock.patch.object(psm, "_read_pid", return_value=None), \
         mock.patch.object(psm.subprocess, "Popen") as m_popen, \
         mock.patch.object(psm.subprocess, "run") as m_run:

        ok = psm.start_service("gateway", foreground=False)

    assert ok is False, "端口被占 + healthz 不通应返回 False"
    m_popen.assert_not_called()
    m_run.assert_not_called()
    assert not cfg["pid_file"].exists()


# ============================================================
# PSM-03: PID 文件指向活进程 + 端口空闲 → 原有 PID 检查短路
# ============================================================

def test_psm_03_pid_alive_short_circuits_no_port_check(isolated_services):
    """.pid 文件正确（指向当前 Python 进程）+ 端口空闲
    → 端口预检**不**应触发现有"已在运行"短路。
    """
    with mock.patch.object(psm, "_port_in_use", return_value=False) as m_port, \
         mock.patch.object(psm, "_read_pid", return_value=os.getpid()), \
         mock.patch.object(psm, "_is_pid_alive", return_value=True), \
         mock.patch.object(psm.subprocess, "Popen") as m_popen, \
         mock.patch.object(psm.subprocess, "run") as m_run:

        ok = psm.start_service("gateway", foreground=False)

    assert ok is True, "PID alive 应短路返回 True"
    m_port.assert_called_once()
    m_popen.assert_not_called()
    m_run.assert_not_called()


# ============================================================
# PSM-04: PID 死 + 端口空闲 → 走原有启动流程（managed_externally）
# ============================================================

def test_psm_04_pid_dead_port_free_runs_cmd_start(isolated_services):
    """.pid 文件死值 + 端口空闲 + managed_externally
    → 应调 cmd_start（subprocess.run）并验证 start_gateway 写入的 PID 存活。
    """
    def _alive(pid):
        # 第一次检查（PID=None）→ 死；启动后检查（PID=12345）→ 活
        return pid is not None and pid == 12345
    with mock.patch.object(psm, "_port_in_use", return_value=False), \
         mock.patch.object(psm, "_read_pid", side_effect=[None, 12345]) as m_read, \
         mock.patch.object(psm, "_is_pid_alive", side_effect=_alive), \
         mock.patch.object(psm, "_wait_health", return_value=True), \
         mock.patch.object(psm.subprocess, "run") as m_run, \
         mock.patch.object(psm.subprocess, "Popen") as m_popen:

        ok = psm.start_service("gateway", foreground=False)

    assert ok is True, "PID 死 + 端口空闲 + managed_externally 应走 cmd_start"
    m_run.assert_called_once()
    m_popen.assert_not_called()
    assert m_read.call_count == 2


# ============================================================
# PSM-05: chat_reply 无端口字段 → 不触发端口预检
# ============================================================

def test_psm_05_chat_reply_no_port_check(isolated_services):
    """chat_reply cfg 无 host/port → 端口预检应跳过。
    即便 _port_in_use 被调也不影响（chat_reply 没传 cfg['port']）。
    """
    cfg = isolated_services["chat_reply"]
    assert "port" not in cfg, "chat_reply 不应有 port 字段"

    def _alive(pid):
        return pid is not None
    with mock.patch.object(psm, "_port_in_use") as m_port, \
         mock.patch.object(psm, "_read_pid", return_value=None), \
         mock.patch.object(psm, "_is_pid_alive", side_effect=_alive), \
         mock.patch.object(psm, "_wait_health", return_value=True), \
         mock.patch.object(psm.subprocess, "Popen") as m_popen:

        ok = psm.start_service("chat_reply", foreground=False)

    assert ok is True
    m_port.assert_not_called(), "chat_reply 不应触发 _port_in_use"
    # 验证 PID 文件被 Popen().pid 写入（即 mock 路径走完）
    # 注意：因 Popen 是 mock，pid 是 MagicMock，但路径被重定向到 tmp_path
    # 不会污染真实 A7/data/runtime/p8_chat_reply.pid
    assert cfg["pid_file"].exists()


# ============================================================
# PSM-06: web 端口被占 + healthz 通 → 短路
# ============================================================

def test_psm_06_web_port_held_short_circuits(isolated_services):
    """web 服务（managed_externally=False）+ 端口 8080 被占 + healthz 通
    → start_service 应短路返回 True，**不**调 subprocess.Popen。
    """
    cfg = isolated_services["web"]

    with mock.patch.object(psm, "_port_in_use", return_value=True), \
         mock.patch.object(psm, "_port_holder_pid", return_value=54321), \
         mock.patch.object(psm, "_wait_health", return_value=True), \
         mock.patch.object(psm.subprocess, "Popen") as m_popen, \
         mock.patch.object(psm.subprocess, "run") as m_run:

        ok = psm.start_service("web", foreground=False)

    assert ok is True
    m_popen.assert_not_called()
    m_run.assert_not_called()
    assert not cfg["pid_file"].exists()


# ============================================================
# PSM-07: _port_in_use / _port_holder_pid 助手单元（真 socket）
# ============================================================

def test_psm_07_helpers_real_socket():
    """_port_in_use：起一个临时 socket server，看是否能被探测到。
    _port_holder_pid：Windows 上应能拿到 PID。
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    try:
        assert psm._port_in_use("127.0.0.1", port) is True

        if sys.platform == "win32":
            holder = psm._port_holder_pid("127.0.0.1", port)
            assert holder == os.getpid(), f"expected current PID, got {holder}"
    finally:
        server_sock.close()


# ============================================================
# PSM-08: 修复回归 — 验证真实 PID 文件不被测试污染
# ============================================================

def test_psm_08_real_pid_files_not_polluted(isolated_services):
    """回归测试：跑完测试后，真实的 A7/data/runtime/p8_chat_reply.pid
    不应包含 mock repr。
    """
    # 跑一次 PSM-05 等价路径（触发 Popen().pid 写入）
    def _alive(pid):
        return pid is not None
    with mock.patch.object(psm, "_port_in_use"), \
         mock.patch.object(psm, "_read_pid", return_value=None), \
         mock.patch.object(psm, "_is_pid_alive", side_effect=_alive), \
         mock.patch.object(psm, "_wait_health", return_value=True), \
         mock.patch.object(psm.subprocess, "Popen"):
        psm.start_service("chat_reply", foreground=False)

    real_pid = Path("A7/data/runtime/p8_chat_reply.pid")
    if real_pid.exists():
        content = real_pid.read_text(encoding="utf-8").strip()
        assert not content.startswith("<MagicMock"), (
            f"测试污染了真实 PID 文件: {content!r}"
        )
        # 真实 PID 必须是可 parse 成 int 的
        int(content)  # raises ValueError if polluted


# ============================================================
# main
# ============================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))