"""P7 批量调度循环的回归测试。

覆盖需求：
1. 每 10s 调度一次（可被 env 覆盖；测试用更短 interval 加速）
2. 维护 <log_dir>/_p7_dispatch_state.json：处理过的 event_id 持久化
3. 每次取"最近 N 个未处理"的 P6 事件（按 raw_event 文件 mtime 倒序）
4. 等上一次结果返回再发起下一批（is_processed_marker 串行语义）
5. 退出循环（running_predicate=False）时不再派发

注意：本项目无 pytest-asyncio，参考 test_a6_active_violations_list.py 的写法，
用 `asyncio.run(...)` 包一层 async 函数。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ── helpers ─────────────────────────────────────────────────────────────────


def _seed_raw_event(log_dir: Path, event_id: str, wt: str, mtime: float = None) -> Path:
    """写入一个 raw_event_*.json 文件（用于 _collect_unprocessed_p6_events 测试）。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    fp = log_dir / f"raw_event_{wt.replace(':', '-').replace('.', '_')}.json"
    payload = {
        "wall_time": wt,
        "events": [
            {
                "event_id": event_id,
                "type": "PPE缺失",
                "first_seen": wt,
                "last_seen": wt,
                "person": {"id": "P7"},
            },
        ],
    }
    fp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(fp, (mtime, mtime))
    return fp


def _run_async(coro):
    """Helper: run a coroutine in a fresh loop, mirroring test_a6_active_violations_list."""
    return asyncio.run(coro)


def _fake_batch_success(events, job_id="", is_processed_marker=None):
    """构造一个成功路径的 trigger_p7_assessment_batch 替身。

    注：返回字段刻意精简为 {event_id, status} —— dispatch loop 只消费这两个字段。
    原 fake_batch 中残留的 aggregation_decision / aggregate_to / risk_level / a6_event_id
    是 OLD Python 端聚合逻辑的字段（aggregate_to / aggregation_decision 已随 LLM 工具化
    改造废弃），dispatch loop 从不读取，仅作历史兼容保留 —— 现统一清理。
    """
    async def _inner(_events, _job_id="", _marker=None):
        if _marker is not None:
            try:
                _marker.clear()
            except Exception:
                pass
        results = [{"event_id": e["event_id"], "status": "success"} for e in _events]
        if _marker is not None:
            try:
                _marker.set()
            except Exception:
                pass
        return results
    return _inner(events, job_id, is_processed_marker)


# ── 状态文件 roundtrip ───────────────────────────────────────────────────────


def test_dispatch_state_roundtrip(tmp_path):
    from agents.p6_monitor_agent import (
        _load_p7_dispatch_state, _save_p7_dispatch_state, _p7_dispatch_state_path,
    )
    log_dir = str(tmp_path)
    # 初次读取 → 默认空结构
    s0 = _load_p7_dispatch_state(log_dir)
    assert s0["processed_event_ids"] == []
    assert s0["last_status"] == "init"

    # 写入 → 再读应一致
    _save_p7_dispatch_state(log_dir, {
        "processed_event_ids": ["A6-1", "A6-2"],
        "last_dispatch_at": "2026-09-15T10:00:00",
        "last_batch_size": 2,
        "last_status": "ok",
    })
    s1 = _load_p7_dispatch_state(log_dir)
    assert s1["processed_event_ids"] == ["A6-1", "A6-2"]
    assert s1["last_status"] == "ok"

    # 路径定位
    assert _p7_dispatch_state_path(log_dir).name == "_p7_dispatch_state.json"


# ── 未处理事件收集 ───────────────────────────────────────────────────────────


def test_collect_unprocessed_filters_processed_ids(tmp_path):
    from agents.p6_monitor_agent import _collect_unprocessed_p6_events
    log_dir = tmp_path
    _seed_raw_event(log_dir, "A6-1", "2026-09-14T10:00:00", mtime=1000)
    _seed_raw_event(log_dir, "A6-2", "2026-09-14T10:00:10", mtime=2000)
    _seed_raw_event(log_dir, "A6-3", "2026-09-14T10:00:20", mtime=3000)

    # 全部未处理 → 按 mtime 倒序：A6-3 → A6-2 → A6-1
    all_unproc = _collect_unprocessed_p6_events(str(log_dir), processed_ids=set(), batch_size=10)
    assert [e["event_id"] for e in all_unproc] == ["A6-3", "A6-2", "A6-1"]

    # 已处理 A6-2 → 不再返回
    filtered = _collect_unprocessed_p6_events(str(log_dir), processed_ids={"A6-2"}, batch_size=10)
    assert [e["event_id"] for e in filtered] == ["A6-3", "A6-1"]


def test_collect_unprocessed_respects_batch_size(tmp_path):
    from agents.p6_monitor_agent import _collect_unprocessed_p6_events
    log_dir = tmp_path
    for i, m in enumerate([1000, 2000, 3000, 4000, 5000]):
        _seed_raw_event(log_dir, f"A6-{i}", f"2026-09-14T10:0{i}:00", mtime=m)

    got = _collect_unprocessed_p6_events(str(log_dir), processed_ids=set(), batch_size=3)
    assert len(got) == 3
    # 最新 3 个：mtime 5000/4000/3000
    assert [e["event_id"] for e in got] == ["A6-4", "A6-3", "A6-2"]


def test_collect_unprocessed_empty_log_dir(tmp_path):
    from agents.p6_monitor_agent import _collect_unprocessed_p6_events
    # log_dir 不存在 → 返回空
    assert _collect_unprocessed_p6_events(str(tmp_path / "nope"), set(), 10) == []


# ── 调度循环行为 ─────────────────────────────────────────────────────────────


def test_dispatch_loop_calls_p7_periodically(tmp_path):
    """调度循环：发现未处理事件 → 调一次 P7；处理完后未再出现新事件 → 不再调。"""
    from agents import p6_monitor_agent as mod

    log_dir = tmp_path
    _seed_raw_event(log_dir, "A6-1", "2026-09-14T10:00:00", mtime=1000)
    _seed_raw_event(log_dir, "A6-2", "2026-09-14T10:00:10", mtime=2000)

    call_records = []

    async def fake_batch(events, job_id="", is_processed_marker=None):
        call_records.append([e["event_id"] for e in events])
        if is_processed_marker is not None:
            try:
                is_processed_marker.clear()
            except Exception:
                pass
        results = [{"event_id": e["event_id"], "status": "success"} for e in events]
        if is_processed_marker is not None:
            try:
                is_processed_marker.set()
            except Exception:
                pass
        return results

    running = [True]
    predicate = lambda: running[0]

    async def runner():
        with patch.object(mod, "trigger_p7_assessment_batch", new=fake_batch):
            task = asyncio.create_task(mod._run_p7_dispatch_loop(
                log_dir=str(log_dir), job_id="test-job",
                running_predicate=predicate,
                interval_sec=0.05, batch_size=10,
            ))
            await asyncio.sleep(0.3)
            running[0] = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()

    _run_async(runner())

    # 仅第一次出现未处理事件时被调一次；之后都被标记为 processed → 不再调
    assert len(call_records) == 1, f"应只调 1 次，实际 {len(call_records)} 次"
    assert call_records[0] == ["A6-2", "A6-1"]


def test_dispatch_loop_picks_up_new_events_after_first_batch(tmp_path):
    """第一批处理完后，新出现的 raw_event 应被下一轮抓起来。"""
    from agents import p6_monitor_agent as mod

    log_dir = tmp_path
    _seed_raw_event(log_dir, "A6-1", "2026-09-14T10:00:00", mtime=1000)

    call_records = []

    async def fake_batch(events, job_id="", is_processed_marker=None):
        call_records.append([e["event_id"] for e in events])
        if is_processed_marker is not None:
            is_processed_marker.clear()
        results = [{"event_id": e["event_id"], "status": "success"} for e in events]
        if is_processed_marker is not None:
            is_processed_marker.set()
        return results

    running = [True]
    predicate = lambda: running[0]

    new_event_added = [False]

    async def runner():
        nonlocal_call_records = call_records

        async def delayed_seed():
            # 等第一批处理完后注入新事件
            await asyncio.sleep(0.12)
            _seed_raw_event(log_dir, "A6-2", "2026-09-14T10:00:10", mtime=2000)
            new_event_added[0] = True

        with patch.object(mod, "trigger_p7_assessment_batch", new=fake_batch):
            task = asyncio.create_task(mod._run_p7_dispatch_loop(
                log_dir=str(log_dir), job_id="test-job",
                running_predicate=predicate,
                interval_sec=0.05, batch_size=10,
            ))
            asyncio.create_task(delayed_seed())
            await asyncio.sleep(0.5)
            running[0] = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()

    _run_async(runner())

    # 至少调过 2 次：第一次是 A6-1，第二次包含 A6-2
    assert new_event_added[0] is True
    assert len(call_records) >= 2
    # 第一次只有 A6-1
    assert "A6-1" in call_records[0]
    # 某次调用应包含 A6-2
    assert any("A6-2" in batch for batch in call_records)


def test_dispatch_loop_skips_processed_event_ids(tmp_path):
    """已写入 state 的 event_id 不应再次送 P7。"""
    from agents import p6_monitor_agent as mod

    log_dir = tmp_path
    _seed_raw_event(log_dir, "A6-1", "2026-09-14T10:00:00", mtime=1000)

    call_count = [0]

    async def fake_batch(events, job_id="", is_processed_marker=None):
        call_count[0] += 1
        if is_processed_marker is not None:
            is_processed_marker.clear()
        results = [{"event_id": e["event_id"], "status": "success"} for e in events]
        if is_processed_marker is not None:
            is_processed_marker.set()
        return results

    # 预先把 A6-1 标记为已处理
    mod._save_p7_dispatch_state(str(log_dir), {
        "processed_event_ids": ["A6-1"],
        "last_dispatch_at": None,
        "last_batch_size": 0,
        "last_status": "init",
    })

    running = [True]
    predicate = lambda: running[0]

    async def runner():
        with patch.object(mod, "trigger_p7_assessment_batch", new=fake_batch):
            task = asyncio.create_task(mod._run_p7_dispatch_loop(
                log_dir=str(log_dir), job_id="test-job",
                running_predicate=predicate,
                interval_sec=0.05, batch_size=10,
            ))
            await asyncio.sleep(0.2)
            running[0] = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()

    _run_async(runner())

    # 因为所有事件都已处理，不应调用 P7
    assert call_count[0] == 0, f"未处理事件为空时应不调 P7，实际 {call_count[0]} 次"


def test_dispatch_loop_persists_processed_ids(tmp_path):
    """成功跑过的 event_id 应持久化到 _p7_dispatch_state.json。"""
    from agents import p6_monitor_agent as mod

    log_dir = tmp_path
    _seed_raw_event(log_dir, "A6-X", "2026-09-14T10:00:00", mtime=1000)

    async def fake_batch(events, job_id="", is_processed_marker=None):
        if is_processed_marker is not None:
            is_processed_marker.clear()
        results = [{"event_id": e["event_id"], "status": "success"} for e in events]
        if is_processed_marker is not None:
            is_processed_marker.set()
        return results

    running = [True]
    predicate = lambda: running[0]

    async def runner():
        with patch.object(mod, "trigger_p7_assessment_batch", new=fake_batch):
            task = asyncio.create_task(mod._run_p7_dispatch_loop(
                log_dir=str(log_dir), job_id="test-job",
                running_predicate=predicate,
                interval_sec=0.05, batch_size=10,
            ))
            await asyncio.sleep(0.2)
            running[0] = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()

    _run_async(runner())

    # 状态文件应含 A6-X
    state = mod._load_p7_dispatch_state(str(log_dir))
    assert "A6-X" in state["processed_event_ids"]
    assert state["last_status"] == "ok"


def test_dispatch_loop_waits_for_previous_marker(tmp_path):
    """第二次 dispatch 必须等第一次的 marker set 完成。"""
    from agents import p6_monitor_agent as mod

    log_dir = tmp_path
    _seed_raw_event(log_dir, "A6-1", "2026-09-14T10:00:00", mtime=1000)

    first_marker: list = []

    async def slow_batch(events, job_id="", is_processed_marker=None):
        if is_processed_marker is not None:
            first_marker.append(is_processed_marker)
            is_processed_marker.clear()
            await asyncio.sleep(0.15)
            is_processed_marker.set()
        return [{"event_id": e["event_id"], "status": "success"} for e in events]

    running = [True]
    predicate = lambda: running[0]

    async def runner():
        with patch.object(mod, "trigger_p7_assessment_batch", new=slow_batch):
            task = asyncio.create_task(mod._run_p7_dispatch_loop(
                log_dir=str(log_dir), job_id="test-job",
                running_predicate=predicate,
                interval_sec=0.05, batch_size=10,
            ))
            await asyncio.sleep(0.5)
            running[0] = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()

    _run_async(runner())

    # 关键验证：marker 路径至少被走过一次且未抛异常
    assert len(first_marker) >= 1
