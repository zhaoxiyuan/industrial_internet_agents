# P8P9/tests/test_agent_interface.py — agent 接口边界护栏测试
#
# 覆盖：
#   - 3 个只读/初始化接口能正常调
#   - agent_interface 模块不能 import business_actions（写状态入口）
#   - 静态 grep 验证 agent_interface.py 不含 set_job_status/record_closure_review 等关键字
#   - 不存在 set_job_status_for_agent 等写接口

from __future__ import annotations
import re
from pathlib import Path

import pytest

from P8P9 import agent_interface
from P8P9.tests.fixtures.sample_events import event_by_level


# ─── 1. 三个接口能正常调 ──────────────────────────────────────────────────────

def test_initialize_job_for_agent_works(closure_service):
    state = agent_interface.initialize_job_for_agent(
        "JOB-AI-001", actor="P8-DispositionAgent", events=[event_by_level(2)],
    )
    assert state["job_status"] == "open"
    assert state["version"] == 0
    assert len(state["events"]) >= 1


def test_initialize_job_idempotent(closure_service):
    s1 = agent_interface.initialize_job_for_agent(
        "JOB-AI-IDEM", actor="agent", events=[event_by_level(1)],
    )
    s2 = agent_interface.initialize_job_for_agent(
        "JOB-AI-IDEM", actor="agent", events=[event_by_level(5)],  # 不同 events
    )
    # 已存在 → 返回原 state（events 不会变化）
    assert s1["version"] == s2["version"]


def test_bind_card_for_agent_works(closure_service):
    agent_interface.initialize_job_for_agent(
        "JOB-AI-BIND", actor="agent", events=[event_by_level(2)],
    )
    result = agent_interface.bind_card_for_agent(
        "JOB-AI-BIND", chat_id="oc_test_chat",
        actor={"open_id": "ou_agent_x", "name": "Agent"},
    )
    assert result["status"] == "bound"
    assert result["chat_id"] == "oc_test_chat"


def test_bind_card_idempotent_same_chat(closure_service):
    agent_interface.initialize_job_for_agent(
        "JOB-AI-BIND-IDEM", actor="agent", events=[event_by_level(2)],
    )
    actor = {"open_id": "ou_a", "name": "A"}
    r1 = agent_interface.bind_card_for_agent(
        "JOB-AI-BIND-IDEM", chat_id="oc_same", actor=actor,
    )
    r2 = agent_interface.bind_card_for_agent(
        "JOB-AI-BIND-IDEM", chat_id="oc_same", actor=actor,
    )
    assert r1["status"] == "bound"
    assert r2["status"] == "exists"


def test_get_state_for_agent_works(closure_service):
    agent_interface.initialize_job_for_agent(
        "JOB-AI-GET", actor="agent", events=[event_by_level(3)],
    )
    state = agent_interface.get_state_for_agent("JOB-AI-GET")
    assert state["job_id"] == "JOB-AI-GET"
    assert state["job_status"] == "open"
    # open_id 脱敏
    if state["events"]:
        for e in state["events"]:
            for p in e["involved_persons"]:
                if p.startswith("ou_"):
                    assert p == "ou_***"


def test_get_state_redacts_open_id(closure_service):
    from P8P9 import business_actions
    agent_interface.initialize_job_for_agent(
        "JOB-AI-PII", actor="agent", events=[event_by_level(2)],
    )
    # 模拟业务动作写 accepted_by
    business_actions.acknowledge_disposition(
        "JOB-AI-PII", actor={"open_id": "ou_secret_user", "name": "张三"}, expected_version=0,
    )
    # agent 读 → 脱敏
    state = agent_interface.get_state_for_agent("JOB-AI-PII")
    assert state["accepted_by"]["open_id"] == "ou_***"
    # 名字也脱敏
    assert state["accepted_by"]["name"] != "张三"
    assert state["accepted_by"]["name"].endswith("*")


# ─── 2. 静态边界护栏 ────────────────────────────────────────────────────────

_AGENT_INTERFACE_PATH = Path(agent_interface.__file__).resolve()


def test_no_business_actions_import_in_agent_interface():
    """agent_interface.py 不能 import business_actions。"""
    src = _AGENT_INTERFACE_PATH.read_text(encoding="utf-8")
    # 检查 import 行
    forbidden_imports = [
        "import business_actions",
        "from . import business_actions",
        "from .business_actions",
        "from .. import business_actions",
        "from ..business_actions",
        "from P8P9 import business_actions",
        "from P8P9.business_actions",
    ]
    for line in src.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import") and not stripped.startswith("from"):
            continue
        for forb in forbidden_imports:
            assert forb not in stripped, f"agent_interface.py 不允许: {stripped!r}"


def test_no_set_job_status_in_agent_interface():
    """agent_interface.py 不应出现 set_job_status / patch_fields 调用。

    patch_fields 虽然不是状态机转换入口（不改 job_status），但能写任意业务字段。
    bind card 必须经 bind_card_for_agent 这个白名单函数，不能让 agent 直接调 patch_fields。
    """
    src = _AGENT_INTERFACE_PATH.read_text(encoding="utf-8")
    # 注释 / docstring 中的出现可允许；但实际调用点不允许
    lines = src.splitlines()
    for i, line in enumerate(lines, start=1):
        # 跳过注释行
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        assert "set_job_status" not in stripped, (
            f"agent_interface.py:{i} 不允许出现 set_job_status: {stripped!r}"
        )
        assert "patch_fields" not in stripped, (
            f"agent_interface.py:{i} 不允许出现 patch_fields: {stripped!r}"
        )


def test_no_record_review_in_agent_interface():
    """agent_interface.py 不应出现 record_closure_review / record_review。"""
    src = _AGENT_INTERFACE_PATH.read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        assert "record_closure_review" not in stripped, (
            f"agent_interface.py 不允许出现 record_closure_review: {stripped!r}"
        )
        assert "record_review" not in stripped, (
            f"agent_interface.py 不允许出现 record_review: {stripped!r}"
        )


def test_no_escalate_or_downgrade_or_relinquish_in_agent_interface():
    """agent_interface.py 不应出现 escalate / downgrade / relinquish 等写动作。"""
    src = _AGENT_INTERFACE_PATH.read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # 排除在注释 / docstring 中出现
        for keyword in ("escalate_risk", "downgrade_risk", "relinquish_job", "submit_rectification_materials"):
            assert keyword not in stripped, (
                f"agent_interface.py 不允许出现 {keyword}: {stripped!r}"
            )


# ─── 3. 动态属性检查 ────────────────────────────────────────────────────────

def test_agent_interface_has_exactly_three_exports():
    """__all__ 应只含 3 个接口。"""
    assert sorted(agent_interface.__all__) == sorted([
        "initialize_job_for_agent",
        "bind_card_for_agent",
        "get_state_for_agent",
    ])


def test_no_for_agent_write_methods():
    """不能有 set_job_status_for_agent / record_review_for_agent 等写接口。

    也不能直接暴露通用写入方法：patch_fields / set_job_status / set_event_status。
    bind_card_for_agent 是唯一可用的写入入口，底层调 state_machine.bind_card 白名单方法。
    """
    forbidden_methods = [
        "set_job_status_for_agent",
        "set_event_status_for_agent",
        "patch_fields_for_agent",
        "bind_card_raw_for_agent",
        "acknowledge_disposition_for_agent",
        "submit_rectification_materials_for_agent",
        "relinquish_job_for_agent",
        "escalate_risk_for_agent",
        "downgrade_risk_for_agent",
        "record_closure_review_for_agent",
        "archive_job_for_agent",
        # 也不能直接暴露通用写入方法
        "patch_fields",
        "set_job_status",
        "set_event_status",
    ]
    for name in forbidden_methods:
        assert not hasattr(agent_interface, name), (
            f"agent_interface.{name} 不应存在"
        )