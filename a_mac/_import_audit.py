"""a/ 内部 import 自包含性审计。

目标:确认 a/ 下所有 Python 模块 import 都能解析,且不依赖 a/ 外部资源。
"""
import sys
import os
import importlib
from pathlib import Path

# 把 a/ 加入 sys.path(沙箱里也加一份保险)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

results = {"ok": 0, "fail": 0, "warn": 0}
fails = []
warns = []


def check(name, fn=None):
    try:
        m = importlib.import_module(name)
        if fn:
            fn(m)
        results["ok"] += 1
        print(f"  OK  {name}")
    except Exception as e:
        # 区分:运行时依赖未装 vs 路径错误
        msg = str(e)
        if any(x in msg for x in ("No module named '", "ImportError: ")):
            if any(lib in msg for lib in ("requests", "flask", "langchain", "langchain_core", "tenacity", "dotenv", "pydantic", "psutil")):
                results["warn"] += 1
                warns.append((name, "运行时依赖未装: " + msg[:80]))
                print(f"  WRN {name}: {msg[:80]}")
            else:
                # 可能是 a/ 内部路径错误
                results["fail"] += 1
                fails.append((name, msg[:120]))
                print(f"  FAIL {name}: {msg[:120]}")
        else:
            results["fail"] += 1
            fails.append((name, msg[:120]))
            print(f"  FAIL {name}: {msg[:120]}")


# ── 模块级 import 校验 ────────────────────────────────────────────
print("\n=== 1. 顶层模块 ===")
for mod in [
    "agents",                              # a/agents/__init__.py
    "A5",                                  # A5/__init__.py
    "A5.agent",                            # A5/agent/__init__.py
    "A5.scenario_data",
    "A5.stream_players",
    "A6_A7",                               # a/A6_A7/__init__.py
    "A6_A7.a6_runtime",
    "A6_A7.a6_runtime.agent",
    "A7",                                  # a/A7/__init__.py
    "A7.adapters",
    "A7.api",
    "A7.middleware",
    "A7.prompt",
    "A7.schema",
    "A7.storage",
    "P8P9",                                # P8P9/__init__.py
    "P8P9.services",
    "feishu_gateway_cli",                  # feishu_gateway_cli/__init__.py
    "frontend",                            # frontend/__init__.py(可能不存在)
]:
    check(mod)

print("\n=== 2. 关键业务模块(动态导入) ===")
for mod in [
    "agents.p6_monitor_agent",
    "agents.p7_risk_agent",
    "agents.p8_disposition_agent",
    "agents.p9_agent",
    "agents.channel_gateway_client",
    "agents.workflow",
    "agents.model",
    "agents.utils",
    "agents.system_prompt",
    "A5.agent.a5_agent",
    "A5.agent.work_permit_rules",
    "A5.agent.system_prompt",
    "A5.agent.event_deduplicator",
    "A5.stream_players.async_collector",
    "A6_A7.a6_runtime.agent.a6_agent",
    "A6_A7.a6_runtime.agent.tools",
    "A6_A7.a6_runtime.agent.prompts",
    "A6_A7.a6_runtime.agent.prompt_manager",
    "A7.adapters.chat_reply",
    "A7.adapters.feishu_outbox",
    "A7.adapters.p8_service_manager",
    "A7.api.p8_working_memory_ctrl",
    "A7.middleware.p8_archive_middleware",
    "A7.middleware.p8_card_action_agent",
    "A7.prompt.p8_prompt",
    "A7.schema.p8_models",
    "A7.schema.p8_state",
    "A7.storage.p8_long_term",
    "A7.storage.p8_working_memory_store",
    "P8P9.state_machine",
    "P8P9.business_actions",
    "P8P9.cards",
    "P8P9.links",
    "P8P9.models",
    "P8P9.agent_interface",
    "P8P9.services.card_render",
    "P8P9.services.callback_router",
    "P8P9.services.audit_scheduler",
    "P8P9.web_server",
    "feishu_gateway_cli.start_gateway",
    "feishu_gateway_cli.feishu_card",
    "feishu_gateway_cli.feishu_receiver",
    "feishu_gateway_cli.feishu_sender",
    "feishu_gateway_cli.feishu_config_app",
    "frontend.app_config",
    "frontend.start_all",
    "aaaseverstart",
]:
    check(mod)

print("\n=== 3. 关键符号导出校验 ===")
def _check_a6_prompts(m):
    """A6 风险分级 prompt 必含 6 级 + 加成规则(原版 171 行)"""
    p = m.RISK_CLASSIFICATION_SYSTEM_PROMPT
    assert "6 级" in p or "六级" in p, "RISK_CLASSIFICATION prompt 应含 6 级分级"
    assert "加成" in p or "持续时间加成" in p, "应含加成规则"
    assert "query_recent_p7_assessments" in p, "应含 P7 时间窗查询工具"
check("A6_A7.a6_runtime.agent.prompts", _check_a6_prompts)

def _check_p8_disp(m):
    """P8 agent v2: open_work_ticket + resend_current_card 两个 tool"""
    tools = {t.name for t in m.disposition_demo.__globals__.values()
             if hasattr(t, "name") and isinstance(t.name, str)}
    # 实际上 disposition_demo 是一个 wrapper;P8 的 tools 通过 create_disposition_agent() 注册
    assert hasattr(m, "open_work_ticket"), "缺 open_work_ticket"
    assert hasattr(m, "resend_current_card"), "缺 resend_current_card"
    assert hasattr(m, "create_disposition_agent"), "缺 create_disposition_agent"
    assert hasattr(m, "disposition_demo"), "缺 disposition_demo"
    assert hasattr(m, "get_p8_checkpointer"), "缺 get_p8_checkpointer"
check("agents.p8_disposition_agent", _check_p8_disp)

def _check_p9_dual_agent(m):
    """P9 v2.1: 单文件含 2 agent(审核 + 关闭文案),两入口两 prompt"""
    assert hasattr(m, "create_audit_agent"), "缺 P9 审核 agent 工厂"
    assert hasattr(m, "run_p9_materials_audit"), "缺 P9 审核入口"
    assert hasattr(m, "create_closure_agent"), "缺 P9 关闭文案 agent 工厂"
    assert hasattr(m, "run_p9_closure_review"), "缺 P9 关闭文案入口"
    assert hasattr(m, "audit_demo"), "缺 audit_demo"
    assert hasattr(m, "closure_demo"), "缺 closure_demo"
check("agents.p9_agent", _check_p9_dual_agent)

def _check_p8p9_state_machine(m):
    """P8P9 状态机:ClosureService + 7 业务动作 + agent_interface"""
    assert hasattr(m, "ClosureService"), "缺 ClosureService"
    assert hasattr(m, "StateNotFound"), "缺 StateNotFound"
    assert hasattr(m, "IllegalTransition"), "缺 IllegalTransition"
    assert hasattr(m, "initialize_job_for_agent"), "缺 initialize_job_for_agent"
    assert hasattr(m, "bind_card_for_agent"), "缺 bind_card_for_agent"
    assert hasattr(m, "get_state_for_agent"), "缺 get_state_for_agent"
    assert hasattr(m, "build_job_card"), "缺 build_job_card"
    assert hasattr(m, "send_all_open_closure_cards"), "缺 send_all_open_closure_cards"
check("P8P9", _check_p8p9_state_machine)

def _check_gateway_paths(m):
    """GATEWAY_DIR 必须指向 a/gateway/(不是 a/openclaw-channel-gateway-standalone)"""
    g = m.GATEWAY_DIR
    expected = Path(__file__).resolve().parent / "gateway"
    assert str(g.resolve()) == str(expected.resolve()), \
        f"GATEWAY_DIR={g} != {expected}"
    assert g.exists(), f"GATEWAY_DIR 不存在: {g}"
    assert (g / "start.mjs").exists(), "start.mjs 缺失"
    assert (g / ".env").exists(), "Gateway .env 缺失"
    assert (g / "config" / "config.feishu.local.json").exists(), "Gateway config.feishu.local.json 缺失"
check("feishu_gateway_cli.start_gateway", _check_gateway_paths)

def _check_p8_service_manager(m):
    """A7 p8_service_manager 必须指向 a/gateway/ 的 .gateway.pid(不能有 a/openclaw-channel-gateway-standalone/ 残留)"""
    gw = m.SERVICES["gateway"]
    pid = Path(gw["pid_file"])
    expected = Path(__file__).resolve().parent / "gateway" / ".gateway.pid"
    assert str(pid.resolve()) == str(expected.resolve()), \
        f"gateway pid_file={pid} != {expected}"
    # 必须不含 web 服务(a/ 不含 web/server.py)
    assert "web" not in m.SERVICES, "a/ 子项目应剔除 web 服务(web/server.py 不在范围)"
    # 必须不含 openclaw-channel-gateway-standalone
    assert "openclaw-channel-gateway-standalone" not in str(gw["pid_file"]), \
        f"pid_file 还残留旧路径: {gw['pid_file']}"
check("A7.adapters.p8_service_manager", _check_p8_service_manager)

def _check_card_render_paths(m):
    """P8P9 card_render 不应再有 openclaw-channel-gateway-standalone 作为实际路径(允许注释里出现历史说明)"""
    import inspect, re
    src = inspect.getsource(m)
    # 去掉所有注释行
    code_lines = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    # 用变量名 OLD_GATEWAY_DIR 代替裸字符串(避免审计脚本自检误报)
    OLD_GATEWAY_DIR = "openclaw-channel-gateway-standalone"
    assert OLD_GATEWAY_DIR not in code, \
        "card_render 实际代码还有 openclaw-channel-gateway-standalone 路径引用"
check("P8P9.services.card_render", _check_card_render_paths)

# ── 全量静态路径引用扫描(最后一道防线) ──────────────────────

import ast as _ast

print("\n=== 4. 全 a/ .py 文件静态扫描 'openclaw-channel-gateway-standalone' 路径引用 ===")
print("    (注释/docstring 中的历史说明允许保留)")
print("    (a/gateway/ 内 Node.js 元数据 [package.json / User-Agent / Docker image] 也允许)")
print("    (审计脚本自身 _import_audit.py 排除 — 含审计规则定义)")

all_py_files = list(ROOT.rglob("*.py"))
# 排除 gateway/test/ 与 gateway/scripts/(Node.js 工具,Python 测试可放行)
real_py_files = [f for f in all_py_files if not any(
    part in f.parts for part in ("gateway",)
)]
real_py_files += [f for f in all_py_files if "gateway" in f.parts and
                   not any(p in f.parts for p in ("test", "scripts")) and
                   f.suffix == ".py"]

path_violations = []
self_file = Path(__file__).resolve()  # 审计脚本本身不算
for f in real_py_files:
    if f.resolve() == self_file:
        continue
    try:
        text = f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    # 移除 docstring(三引号块)
    lines = text.splitlines()
    in_docstring = False
    docstring_marker = None
    cleaned = []
    # 用变量名匹配,而不是裸字符串
    target = "openclaw-channel-gateway-standalone"
    for line in lines:
        s = line.strip()
        if not in_docstring:
            if '"""' in s or "'''" in s:
                # 单行 docstring
                if s.count('"""') == 2 or s.count("'''") == 2:
                    continue  # 整行 docstring,跳过
                else:
                    in_docstring = True
                    docstring_marker = '"""' if '"""' in s else "'''"
                    # 去掉这一行的 docstring 开始部分
                    if docstring_marker in s:
                        idx = s.index(docstring_marker)
                        rest = s[idx + 3:].strip()
                        if rest:
                            cleaned.append(rest)
                        continue
                    continue
            # 跳过纯注释行
            if s.startswith("#"):
                continue
            cleaned.append(line)
        else:
            # 在 docstring 块里
            if docstring_marker in line:
                in_docstring = False
                idx = line.index(docstring_marker)
                rest = line[idx + 3:].strip()
                if rest:
                    cleaned.append(rest)
                continue
            # 跳过整行
            continue
    code = "\n".join(cleaned)
    if target in code:
        path_violations.append(f)

if path_violations:
    for f in path_violations:
        print(f"  FAIL {f.relative_to(ROOT)}")
    print(f"\nFAIL 数量: {len(path_violations)}")
    sys.exit(1)
else:
    print(f"  OK 全 {len(real_py_files)} 个 .py 文件无路径残留")
print()

def _check_gateway_client(m):
    """Channel Gateway client 必须能导出 6 个核心符号"""
    for sym in ("send_message", "reply_to_event", "poll_inbound_events",
                "ack_event", "GatewayError", "SendMessageResult"):
        assert hasattr(m, sym), f"缺 {sym}"
check("agents.channel_gateway_client", _check_gateway_client)

def _check_aaaseverstart(m):
    """aaaseverstart 必须有 4 个服务(gateway/p8p9/feishu_cfg/chat_reply)"""
    names = {s.name for s in m.SERVICES}
    expected = {"gateway", "p8p9", "feishu_cfg", "chat_reply"}
    assert expected.issubset(names), f"缺服务: {expected - names}"
    # gateway 应是 node 模式
    gw = m.SERVICES_BY_NAME["gateway"]
    assert gw.run_mode == "node", "gateway 应该是 node 模式"
check("aaaseverstart", _check_aaaseverstart)


print("\n" + "=" * 60)
print(f"OK:   {results['ok']}")
print(f"WRN:  {results['warn']}(运行时依赖未装,可忽略)")
print(f"FAIL: {results['fail']}")
print("=" * 60)
if fails:
    print("\n失败详情:")
    for n, m in fails:
        print(f"  {n}: {m}")
    sys.exit(1)
sys.exit(0)