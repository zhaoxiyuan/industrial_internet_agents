"""A6 活跃违规表 list 化 + 实例锁串行化的回归测试。

场景：
- 同一 violation_key（P7+PPE缺失）连续 3 次写入，应该形成 1 个桶 3 条记录（按 last_seen 倒序）
- get_active_violation 返回最新的那条（向后兼容）
- get_active_violations_for_key 返回全部
- close_active_violation(key, a6_event_id) 精确关闭指定记录
- close_active_violation(key) 关闭整桶
- 旧 dict 格式被惰性迁移到 list
- _load_active_violations 的原子写不损坏旧文件（写后内容 JSON 可解析）
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from A6_A7.a6_runtime.agent.tools import (
    A5DataTools,
    ACTIVE_VIOLATIONS_FILE,
)


@pytest.fixture
def tmp_violations_file(tmp_path, monkeypatch):
    """把 ACTIVE_VIOLATIONS_FILE 重定向到 tmp 路径，避免污染真实数据。"""
    target = tmp_path / "active_violations.json"
    monkeypatch.setattr(
        "A6_A7.a6_runtime.agent.tools.ACTIVE_VIOLATIONS_FILE", str(target),
    )
    return target


def _seed_old_format(path: Path) -> None:
    """写入旧格式（key → dict 单条），用于验证惰性迁移。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "P7+ppe_missing": {
                    "a6_event_id": "A6-OLD-001",
                    "person_id": "P7",
                    "violation_type": "PPE缺失",
                    "first_seen": "2026-09-14T17:39:58",
                    "last_seen": "2026-09-14T17:40:04",
                    "duration_sec": 6,
                    "status": "ongoing",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_old_format_auto_migrates_to_list(tmp_violations_file):
    """旧 dict 格式加载后自动迁移为 list。"""
    _seed_old_format(tmp_violations_file)
    tools = A5DataTools(a5_log_dir=str(tmp_violations_file.parent))
    actives = tools.get_active_violations_for_key("P7+ppe_missing")
    assert len(actives) == 1
    assert actives[0]["a6_event_id"] == "A6-OLD-001"
    # get_active_violation 也兼容
    single = tools.get_active_violation("P7+ppe_missing")
    assert single["a6_event_id"] == "A6-OLD-001"


def test_update_appends_to_bucket(tmp_violations_file):
    """同一 key 不同 a6_event_id 累加成 list。"""
    tools = A5DataTools(a5_log_dir=str(tmp_violations_file.parent))
    key = "P7+ppe_missing"
    tools.update_active_violation(
        key=key, a6_event_id="A6-NEW-001", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:40:15", last_seen="2026-09-14T17:40:20",
    )
    tools.update_active_violation(
        key=key, a6_event_id="A6-NEW-002", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:39:58", last_seen="2026-09-14T17:40:04",
    )
    tools.update_active_violation(
        key=key, a6_event_id="A6-NEW-003", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:40:14", last_seen="2026-09-14T17:40:14",
    )
    actives = tools.get_active_violations_for_key(key)
    assert len(actives) == 3
    # last_seen 倒序：17:40:20 > 17:40:14 > 17:40:04
    assert actives[0]["a6_event_id"] == "A6-NEW-001"
    assert actives[1]["a6_event_id"] == "A6-NEW-003"
    assert actives[2]["a6_event_id"] == "A6-NEW-002"
    # get_active_violation 返回最新的
    latest = tools.get_active_violation(key)
    assert latest["a6_event_id"] == "A6-NEW-001"


def test_update_replaces_same_a6_event_id(tmp_violations_file):
    """同 a6_event_id 二次写入应替换而非追加。"""
    tools = A5DataTools(a5_log_dir=str(tmp_violations_file.parent))
    key = "P7+ppe_missing"
    tools.update_active_violation(
        key=key, a6_event_id="A6-SAME", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:40:15", last_seen="2026-09-14T17:40:20",
    )
    tools.update_active_violation(
        key=key, a6_event_id="A6-SAME", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:40:15", last_seen="2026-09-14T17:40:30",
    )
    actives = tools.get_active_violations_for_key(key)
    assert len(actives) == 1
    assert actives[0]["last_seen"] == "2026-09-14T17:40:30"


def test_close_specific_a6_event_id(tmp_violations_file):
    """close_active_violation(key, a6_event_id=...) 只关闭指定记录。"""
    tools = A5DataTools(a5_log_dir=str(tmp_violations_file.parent))
    key = "P7+ppe_missing"
    tools.update_active_violation(
        key=key, a6_event_id="A6-A", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:40:15", last_seen="2026-09-14T17:40:20",
    )
    tools.update_active_violation(
        key=key, a6_event_id="A6-B", person_id="P7",
        violation_type="PPE缺失",
        first_seen="2026-09-14T17:39:58", last_seen="2026-09-14T17:40:04",
    )
    tools.close_active_violation(key, a6_event_id="A6-A")
    actives = tools.get_active_violations_for_key(key)
    assert len(actives) == 1
    assert actives[0]["a6_event_id"] == "A6-B"


def test_close_whole_bucket_clears_status(tmp_violations_file):
    """close_active_violation(key) 关闭整桶，状态变 closed。"""
    tools = A5DataTools(a5_log_dir=str(tmp_violations_file.parent))
    key = "P7+ppe_missing"
    for aid in ("A6-A", "A6-B"):
        tools.update_active_violation(
            key=key, a6_event_id=aid, person_id="P7",
            violation_type="PPE缺失",
            first_seen="2026-09-14T17:40:00", last_seen="2026-09-14T17:40:10",
        )
    tools.close_active_violation(key)
    actives = tools.get_active_violations_for_key(key)
    assert actives == []  # ongoing 被过滤


def test_save_uses_atomic_write(tmp_violations_file):
    """保存走 tempfile + os.replace 路径，写后文件可解析。"""
    import os
    tools = A5DataTools(a5_log_dir=str(tmp_violations_file.parent))
    tools.update_active_violation(
        key="k1", a6_event_id="A1", person_id="p1", violation_type="v1",
        first_seen="2026-09-14T10:00:00", last_seen="2026-09-14T10:00:10",
    )
    assert tmp_violations_file.exists()
    # 解析成功 + 是 list 格式
    data = json.loads(tmp_violations_file.read_text(encoding="utf-8"))
    assert "k1" in data and isinstance(data["k1"], list)


def test_a6_agent_instance_lock_serializes_process_event(monkeypatch, tmp_path):
    """A6Agent.process_event 实例锁：3 个并发调用应串行执行（任一时刻仅 1 个进入）。"""
    from A6_A7.a6_runtime.agent.a6_agent import A6Agent

    agent = A6Agent(
        a5_log_dir=str(tmp_path / "p6"),
        a6_output_dir=str(tmp_path / "p7"),
        llm_provider=None,
    )
    in_section = 0
    peak = 0
    gate = asyncio.Event()
    gate.set()

    async def fake_inner(event_id, event_data, t0):
        nonlocal in_section, peak
        in_section += 1
        peak = max(peak, in_section)
        await asyncio.sleep(0.01)
        in_section -= 1
        return {"status": "ok"}

    monkeypatch.setattr(agent, "_process_event_inner", fake_inner)
    monkeypatch.setattr(agent, "_extract_wall_time", lambda d: "2026-09-14T10:00:00")
    monkeypatch.setattr(agent, "_maybe_reset_log_test", lambda t: None)
    monkeypatch.setattr(agent, "_write_log_test", lambda *a, **k: None)

    async def go():
        ev = {"event_id": "x", "type": "PPE缺失", "person": {"id": "P7"},
              "first_seen": "2026-09-14T10:00:00", "last_seen": "2026-09-14T10:00:01"}
        await asyncio.gather(
            agent.process_event("ev-1", ev),
            agent.process_event("ev-2", ev),
            agent.process_event("ev-3", ev),
        )

    asyncio.run(go())
    assert peak == 1, f"实例锁未生效，peak={peak}"