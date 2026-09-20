"""A6 P7 工具（query_recent_p7_assessments / write_p7_assessment / find_assessment_by_event_id）的回归测试。

覆盖需求：
1. query_recent_p7_assessments 按时间窗过滤
2. query_recent_p7_assessments 按 person_id / violation_type 过滤
3. write_p7_assessment create / update / delete 三动作
4. update 找不到目标 a6_event_id 时返回 success=False
5. delete 找不到目标时返回 success=False
6. find_assessment_by_event_id 按 aggregated_from 找回 LLM 刚写的文件
7. update 后原 a6_event_id 不变、aggregated_from 追加新 a5_event_id、time 取极值

不依赖 LLM —— 直接测 OutputTools 层。
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

import pytest


# ── helpers ─────────────────────────────────────────────────────────────────


def _write_assessment(p7_dir: Path, a6_id: str, *, first_seen: str, last_seen: str,
                     person_id: str = "P7", violation_type: str = "PPE缺失",
                     aggregated_from=None, risk_level: int = 1) -> Path:
    fp = p7_dir / f"a6_{a6_id.replace(':', '-').replace('.', '_')}.json"
    record = {
        "a6_event_id": a6_id,
        "aggregated_from": aggregated_from or [a6_id],
        "event_type": violation_type,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "wall_time": last_seen,
        "duration_sec": 0.0,
        "involved_persons": [person_id],
        "risk_level": risk_level,
        "risk_level_name": "轻微",
        "risk_basis": "测试",
        "suggestions": [],
        "reasoning": "",
        "evidence": {},
        "timestamp": datetime.now().isoformat(),
    }
    fp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return fp


# ── query_recent_p7_assessments ─────────────────────────────────────────────


def test_query_recent_filters_by_window(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    _write_assessment(p7, "A6-OLD-1", first_seen="2026-09-15T08:59:00", last_seen="2026-09-15T08:59:10")
    _write_assessment(p7, "A6-NEAR", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05")
    _write_assessment(p7, "A6-FAR", first_seen="2026-09-15T09:05:00", last_seen="2026-09-15T09:05:05")

    t = OutputTools(output_dir=str(p7))
    # 锚点 09:00:00, window=60s → A6-NEAR 命中，A6-OLD-1/A6-FAR 不在窗口
    got = t.query_recent_p7_assessments(wall_time="2026-09-15T09:00:00", window_sec=60)
    ids = [r["a6_event_id"] for r in got]
    assert "A6-NEAR" in ids
    assert "A6-OLD-1" not in ids
    assert "A6-FAR" not in ids


def test_query_recent_filters_by_person(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    _write_assessment(p7, "A6-P7", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05",
                      person_id="P7")
    _write_assessment(p7, "A6-P8", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05",
                      person_id="P8")

    t = OutputTools(output_dir=str(p7))
    got = t.query_recent_p7_assessments(wall_time="2026-09-15T09:00:00", window_sec=60, person_id="P7")
    ids = [r["a6_event_id"] for r in got]
    assert ids == ["A6-P7"]


def test_query_recent_filters_by_violation_type(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    # 用 \u 转义避免 Windows 源码编码问题（cp936 vs utf-8）
    PPE_MISSING = "PPE缺失"          # PPE缺失
    GUARD_MISSING = "监护人离岗"  # 监护人离岗
    _write_assessment(p7, "A6-PPE", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05",
                      violation_type=PPE_MISSING)
    _write_assessment(p7, "A6-GUARD", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05",
                      violation_type=GUARD_MISSING)

    t = OutputTools(output_dir=str(p7))
    got = t.query_recent_p7_assessments(
        wall_time="2026-09-15T09:00:00", window_sec=60, violation_type=PPE_MISSING,
    )
    ids = [r["a6_event_id"] for r in got]
    assert ids == ["A6-PPE"]
    # 大小写 / 空格不敏感
    got2 = t.query_recent_p7_assessments(
        wall_time="2026-09-15T09:00:00", window_sec=60, violation_type="ppe 缺失",
    )
    assert [r["a6_event_id"] for r in got2] == ["A6-PPE"]


def test_query_recent_includes_filepath(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    fp = _write_assessment(p7, "A6-X", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05")

    t = OutputTools(output_dir=str(p7))
    got = t.query_recent_p7_assessments(wall_time="2026-09-15T09:00:00", window_sec=60)
    assert got[0]["_filepath"] == str(fp)


def test_query_recent_no_match_returns_empty(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    t = OutputTools(output_dir=str(tmp_path / "missing_dir"))
    assert t.query_recent_p7_assessments(wall_time="2026-09-15T09:00:00", window_sec=60) == []


# ── write_p7_assessment ─────────────────────────────────────────────────────


def test_write_p7_create_generates_id(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(
        action="create",
        assessment={
            "aggregated_from": ["A5-1"],
            "event_type": "PPE缺失",
            "first_seen": "2026-09-15T09:00:00",
            "last_seen": "2026-09-15T09:00:05",
            "involved_persons": ["P7"],
            "risk_level": 1,
        },
    )
    assert result["success"] is True
    assert result["action"] == "create"
    assert result["a6_event_id"].startswith("A6-")
    # 文件确实落盘
    assert Path(result["filepath"]).exists()
    record = json.loads(Path(result["filepath"]).read_text(encoding="utf-8"))
    assert record["a6_event_id"] == result["a6_event_id"]
    assert record["aggregated_from"] == ["A5-1"]


def test_write_p7_update_extends_aggregated_from(tmp_path):
    """核心回归：update 后原 a6_event_id 不变、aggregated_from 追加、time 取极值。"""
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    _write_assessment(p7, "A6-EXISTING",
                     first_seen="2026-09-15T08:59:00", last_seen="2026-09-15T08:59:30",
                     aggregated_from=["A5-OLD-1"])

    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(
        action="update",
        a6_event_id="A6-EXISTING",
        assessment={
            "a6_event_id": "A6-EXISTING",
            "aggregated_from": ["A5-OLD-1", "A5-NEW-1"],
            "event_type": "PPE缺失",
            "first_seen": "2026-09-15T08:59:00",   # 取 min
            "last_seen": "2026-09-15T09:00:30",     # 取 max
            "involved_persons": ["P7"],
            "risk_level": 2,
            "risk_basis": "聚合后风险提升",
            "wall_time": "2026-09-15T09:00:30",
            "duration_sec": 90,
        },
    )
    assert result["success"] is True
    assert result["a6_event_id"] == "A6-EXISTING"  # ID 不变！

    # 文件被覆盖（不是新增）
    files = list(p7.glob("a6_*.json"))
    assert len(files) == 1

    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["a6_event_id"] == "A6-EXISTING"
    assert "A5-NEW-1" in record["aggregated_from"]
    assert "A5-OLD-1" in record["aggregated_from"]
    assert record["first_seen"] == "2026-09-15T08:59:00"
    assert record["last_seen"] == "2026-09-15T09:00:30"


def test_write_p7_update_missing_target_fails(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(
        action="update",
        a6_event_id="A6-NONEXISTENT",
        assessment={"a6_event_id": "A6-NONEXISTENT", "event_type": "PPE缺失"},
    )
    assert result["success"] is False
    assert "未找到" in result["error"]


def test_write_p7_delete_removes_file(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    _write_assessment(p7, "A6-DEL", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05")

    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(action="delete", a6_event_id="A6-DEL")
    assert result["success"] is True
    assert result["action"] == "delete"
    assert list(p7.glob("a6_*.json")) == []


def test_write_p7_delete_missing_returns_false(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(action="delete", a6_event_id="A6-NOPE")
    assert result["success"] is False


def test_write_p7_create_missing_assessment_fails(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(action="create")  # assessment=None
    assert result["success"] is False
    assert "assessment" in result["error"]


def test_write_p7_unknown_action_fails(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    t = OutputTools(output_dir=str(p7))
    result = t.write_p7_assessment(action="merge")
    assert result["success"] is False
    assert "unknown action" in result["error"]


# ── find_assessment_by_event_id ─────────────────────────────────────────────


def test_find_assessment_by_event_id(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    _write_assessment(p7, "A6-X", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05",
                     aggregated_from=["A5-OTHER", "A5-LOOKING-FOR"])
    _write_assessment(p7, "A6-Y", first_seen="2026-09-15T09:00:10", last_seen="2026-09-15T09:00:15",
                     aggregated_from=["A5-DIFFERENT"])

    t = OutputTools(output_dir=str(p7))
    found = t.find_assessment_by_event_id("A5-LOOKING-FOR")
    assert found is not None
    assert found["a6_event_id"] == "A6-X"
    assert "A5-LOOKING-FOR" in found["aggregated_from"]


def test_find_assessment_by_event_id_no_match(tmp_path):
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    _write_assessment(p7, "A6-X", first_seen="2026-09-15T09:00:00", last_seen="2026-09-15T09:00:05",
                     aggregated_from=["A5-1"])

    t = OutputTools(output_dir=str(p7))
    assert t.find_assessment_by_event_id("A5-NOT-THERE") is None


# ── 端到端：模拟 LLM 行为（create → query → update → query → delete） ─────────


def test_end_to_end_aggregation_flow(tmp_path):
    """完整模拟：写入旧记录 → LLM 查询 → LLM update 追加 aggregated_from → LLM 查询能看到更新。"""
    from A6_A7.a6_runtime.agent.tools import OutputTools
    p7 = tmp_path / "p7"
    p7.mkdir()
    t = OutputTools(output_dir=str(p7))

    # 1. 创建第一个 P7 记录（来自 A5-1）
    t.write_p7_assessment(action="create", assessment={
        "aggregated_from": ["A5-1"],
        "event_type": "PPE缺失",
        "first_seen": "2026-09-15T09:00:00",
        "last_seen": "2026-09-15T09:00:10",
        "involved_persons": ["P7"],
        "risk_level": 1,
    })
    # 取出刚生成的 a6_event_id
    files = list(p7.glob("a6_*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    target_id = record["a6_event_id"]

    # 2. LLM 模拟：query 应能看到该记录
    nearby = t.query_recent_p7_assessments(
        wall_time="2026-09-15T09:00:30", window_sec=60, person_id="P7", violation_type="PPE缺失",
    )
    assert len(nearby) == 1
    assert nearby[0]["a6_event_id"] == target_id

    # 3. LLM 模拟：update（合并 A5-2）
    t.write_p7_assessment(action="update", a6_event_id=target_id, assessment={
        "a6_event_id": target_id,
        "aggregated_from": ["A5-1", "A5-2"],
        "event_type": "PPE缺失",
        "first_seen": "2026-09-15T09:00:00",
        "last_seen": "2026-09-15T09:00:30",
        "involved_persons": ["P7"],
        "risk_level": 2,
        "risk_basis": "聚合后升级",
    })

    # 4. 文件数仍是 1（update 不新建文件）
    assert len(list(p7.glob("a6_*.json"))) == 1

    # 5. 找回 A5-2 的研判文件
    found = t.find_assessment_by_event_id("A5-2")
    assert found is not None
    assert found["a6_event_id"] == target_id
    assert set(found["aggregated_from"]) == {"A5-1", "A5-2"}
    assert found["risk_level"] == 2  # 升级了
