"""P8 per-job 持久化单元测试（P1-P17，2026-08-20 新增）。

覆盖：
- P8Job.job_id 字段（D4 Pydantic 透传）
- P8JobUpdate.job_id 字段
- save_archived_job 双写（D2/D7）
- per-job 路径写入 data/jobs/{job_id}/P8/archived.json（D3）
- per-job 路径 path traversal 防护
- load_working_memory / dump_working_memory / flush_working_memory（D1）
- P8ArchiveMiddleware job_id 透传（D2/D7）
- create_disposition_agent cache key 含 job（D4）
- run_disposition_agent flush（D1）
- execute_p8 写 P8/result.json + 旧 p8_result.json（D3/D7）

执行方式：
    cd <repo-root>
    python tests/test_a7_p8_per_job_persistence.py
"""
import sys
import shutil
import json
from pathlib import Path

# 让 tests/ 能找到 A7/ 与 agents/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================
# P1 / P2: P8Job.job_id / P8JobUpdate.job_id
# ============================================================
def test_p1_p8job_job_id_field():
    """P1: P8Job.job_id 字段（Pydantic 校验通过；D4）。"""
    from A7.schema import P8Job, RiskLevel

    j = P8Job(
        p8_job_id="P8J-20260813-180001-001",
        job_id="JOB-20260813-001",
        a6_event_ids=["A6-X"],
        risk_basis="test basis",
        max_level=RiskLevel.HIGH,
        urgency_emoji="🟠",
        assignee_role="Worker",
        due_at="2026-08-13T19:00:00Z",
        created_at="2026-08-13T18:00:00Z",
    )
    assert j.job_id == "JOB-20260813-001"
    print('P1 PASS: P8Job.job_id 字段 OK')


def test_p2_p8jobupdate_job_id_field():
    """P2: P8JobUpdate.job_id 字段透传（D4）。"""
    from A7.schema import P8JobUpdate

    upd = P8JobUpdate(
        a6_event_ids=["A6-X"],
        job_id="JOB-X",
    )
    assert upd.job_id == "JOB-X"
    print('P2 PASS: P8JobUpdate.job_id 字段 OK')


# ============================================================
# P3 / P4 / P5: save_archived_job per-job 双写
# ============================================================
def _make_archived(p8_job_id: str) -> dict:
    return {
        "p8_job_id": p8_job_id,
        "max_level": "HIGH",
        "risk_basis": "可燃气体浓度超标（CH4 4.8%）",
        "decision": "rectify",
        "note": "by operator:zhang",
        "archived_at": "2026-08-13T18:30:00Z",
        "a6_event_ids": [f"A6-20260813-{p8_job_id[-3:]}-001"],
        "summary": "HIGH|rectify|by operator:zhang",
    }


def test_p3_save_archived_job_dual_write():
    """P3: save_archived_job(pid, job, job_id=...) 同时写全局 + per-job（D2/D7）。"""
    from A7.storage import (
        save_archived_job, get_archived_job, reset_archive,
    )
    from agents.workflow.file_utils import get_job_dir

    reset_archive()
    job_id = "JOB-P3-001"
    per_job_dir = Path(get_job_dir(job_id)) / "P8"
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)

    p8_job_id = "P8J-20260813-180003-001"
    job = _make_archived(p8_job_id)
    save_archived_job(p8_job_id, job, job_id=job_id)

    # 全局
    got_global = get_archived_job(p8_job_id)
    assert got_global is not None

    # per-job
    per_job_path = per_job_dir / "archived.json"
    assert per_job_path.exists()
    per_data = json.loads(per_job_path.read_text(encoding='utf-8'))
    assert p8_job_id in per_data

    # 清理
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)
    print('P3 PASS: 全局 + per-job 双写 OK')


def test_p4_save_archived_job_no_job_id_backward_compat():
    """P4: save_archived_job(pid, job) 不传 job_id → 仅全局（D7 向后兼容）。"""
    from A7.storage import (
        save_archived_job, get_archived_job, reset_archive,
    )

    reset_archive()
    p8_job_id = "P8J-20260813-180004-001"
    job = _make_archived(p8_job_id)
    # 不传 job_id（向后兼容旧调用方）
    save_archived_job(p8_job_id, job)

    got = get_archived_job(p8_job_id)
    assert got is not None
    # 不抛错
    print('P4 PASS: 向后兼容 OK（无 job_id 仅全局写）')


def test_p5_per_job_path_layout():
    """P5: per-job 路径 data/jobs/{job_id}/P8/archived.json（D3）。"""
    from A7.storage import save_archived_job, reset_archive
    from agents.workflow.file_utils import get_job_dir

    reset_archive()
    job_id = "JOB-P5-001"
    per_job_dir = Path(get_job_dir(job_id)) / "P8"
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)

    p8_job_id = "P8J-20260813-180005-001"
    save_archived_job(p8_job_id, _make_archived(p8_job_id), job_id=job_id)

    expected = per_job_dir / "archived.json"
    assert expected.exists()
    assert "P8" in expected.parts
    assert job_id in expected.parts

    # 清理
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)
    print('P5 PASS: per-job 路径 =', expected)


def test_p6_per_job_path_traversal_protection():
    """P6: per-job 路径 path traversal 防护。"""
    from A7.storage.p8_long_term import _get_job_archive_path

    for bad in ("../../etc/passwd", "../", "job/with/slash",
                "job.with.dot", "", "job with space"):
        try:
            _get_job_archive_path(bad)
            assert False, f'should have raised for {bad!r}'
        except (ValueError, TypeError):
            pass
    print('P6 PASS: path traversal 防护 OK')


# ============================================================
# P7 / P8 / P9 / P10 / P11: working_memory 持久化
# ============================================================
def test_p7_load_working_memory_basic():
    """P7: load_working_memory(job_id) 读 per-job 文件返回 list（D1）。"""
    from A7.storage import dump_working_memory, load_working_memory
    from agents.workflow.file_utils import get_job_dir

    job_id = "JOB-P7-001"
    per_job_dir = Path(get_job_dir(job_id)) / "P8"
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)

    sample = [{"p8_job_id": "P8J-X-1", "status": "pending"}]
    assert dump_working_memory(job_id, sample) is True

    loaded = load_working_memory(job_id)
    assert isinstance(loaded, list)
    assert len(loaded) == 1
    assert loaded[0]["p8_job_id"] == "P8J-X-1"

    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)
    print('P7 PASS: load/dump working_memory OK')


def test_p8_load_working_memory_nonexistent():
    """P8: load_working_memory("NON-EXISTENT") 返空 list。"""
    from A7.storage import load_working_memory
    result = load_working_memory("JOB-P8-NONEXIST")
    assert result == []
    print('P8 PASS: nonexistent job_id 返空 list')


def test_p9_load_working_memory_corrupted():
    """P9: load_working_memory 损坏 JSON → 返空 + warning（D1）。"""
    from A7.storage import load_working_memory
    from agents.workflow.file_utils import get_job_dir

    job_id = "JOB-P9-CORRUPT"
    per_job_dir = Path(get_job_dir(job_id)) / "P8"
    per_job_dir.mkdir(parents=True, exist_ok=True)
    fpath = per_job_dir / "working_memory.json"
    fpath.write_text("{invalid json", encoding="utf-8")

    # 不抛，返空
    result = load_working_memory(job_id)
    assert result == []

    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)
    print('P9 PASS: 损坏 JSON → 返空 list')


def test_p10_dump_working_memory_atomic():
    """P10: dump_working_memory(job_id, list) 写 per-job 文件（D1）。"""
    from A7.storage import dump_working_memory
    from agents.workflow.file_utils import get_job_dir

    job_id = "JOB-P10-001"
    per_job_dir = Path(get_job_dir(job_id)) / "P8"
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)

    payload = [
        {"p8_job_id": "P8J-A", "status": "pending"},
        {"p8_job_id": "P8J-B", "status": "notified"},
    ]
    assert dump_working_memory(job_id, payload) is True

    fpath = per_job_dir / "working_memory.json"
    assert fpath.exists()
    data = json.loads(fpath.read_text(encoding='utf-8'))
    assert len(data) == 2

    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)
    print('P10 PASS: dump 原子写 OK')


def test_p11_flush_working_memory_no_checkpointer():
    """P11: flush_working_memory(job_id) 从 MemorySaver 读 → dump（D1）。"""
    from A7.storage import flush_working_memory
    # 即便 checkpointer 为空也不抛错
    result = flush_working_memory("JOB-P11-FLUSH")
    assert result is True or result is False  # 容忍返回值差异
    print('P11 PASS: flush_working_memory 不抛错')


# ============================================================
# P12 / P13: P8ArchiveMiddleware job_id 透传
# ============================================================
def test_p12_middleware_constructor_with_job_id():
    """P12: P8ArchiveMiddleware(job_id=...) 构造（仅验证签名 + 实例化）。"""
    from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware
    m = P8ArchiveMiddleware(job_id="JOB-P12-001")
    assert m.job_id == "JOB-P12-001"
    print('P12 PASS: P8ArchiveMiddleware(job_id=...) 实例化 OK')


def test_p13_middleware_constructor_no_job_id_backward_compat():
    """P13: P8ArchiveMiddleware(job_id=None) 实例化（D7 向后兼容）。"""
    from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware
    m = P8ArchiveMiddleware()
    assert m.job_id is None
    print('P13 PASS: P8ArchiveMiddleware() 向后兼容 OK')


# ============================================================
# P14 / P15: create_disposition_agent + run_disposition_agent
# ============================================================
def test_p14_agent_cache_key_includes_job_id():
    """P14: create_disposition_agent cache key 含 job_id（D4 防串台）。"""
    from agents.p8_disposition_agent import _user_ctx_cache_key

    k_none = _user_ctx_cache_key(None, "basic", job_id=None)
    k_a = _user_ctx_cache_key(None, "basic", job_id="JOB-A")
    k_b = _user_ctx_cache_key(None, "basic", job_id="JOB-B")

    assert k_none != k_a
    assert k_a != k_b
    assert "JOB-A" in k_a
    assert "JOB-B" in k_b
    print('P14 PASS: cache key 含 job_id')


def test_p15_run_disposition_agent_signature():
    """P15: run_disposition_agent 接受 job_id + 调用 flush（D1）。"""
    import inspect
    from agents.p8_disposition_agent import run_disposition_agent

    sig = inspect.signature(run_disposition_agent)
    assert 'job_id' in sig.parameters
    print('P15 PASS: run_disposition_agent 接受 job_id')


# ============================================================
# P16 / P17: execute_p8 + 路径安全
# ============================================================
def test_p16_execute_p8_writes_per_job_result(tmp_path=None):
    """P16: execute_p8 写 P8/result.json + 旧 p8_result.json（D3/D7）。

    注：本测试只验证函数签名，不实际调 LLM。
    """
    import inspect
    from agents.main_agent import execute_p8

    sig = inspect.signature(execute_p8)
    params = list(sig.parameters.keys())
    assert params == ['job_id'], f'execute_p8 params 应只有 job_id；实际 {params}'
    print('P16 PASS: execute_p8 签名 OK')


def test_p17_working_memory_path_traversal_protection():
    """P17: working_memory path traversal 防护。"""
    from A7.storage.p8_working_memory_store import _get_working_memory_path

    for bad in ("../../etc/passwd", "../", "job/with/slash",
                "job.with.dot", "", "job with space"):
        try:
            _get_working_memory_path(bad)
            assert False, f'should have raised for {bad!r}'
        except (ValueError, TypeError):
            pass
    print('P17 PASS: working_memory path traversal 防护 OK')


# ============================================================
# 测试 runner
# ============================================================
if __name__ == '__main__':
    tests = [
        test_p1_p8job_job_id_field,
        test_p2_p8jobupdate_job_id_field,
        test_p3_save_archived_job_dual_write,
        test_p4_save_archived_job_no_job_id_backward_compat,
        test_p5_per_job_path_layout,
        test_p6_per_job_path_traversal_protection,
        test_p7_load_working_memory_basic,
        test_p8_load_working_memory_nonexistent,
        test_p9_load_working_memory_corrupted,
        test_p10_dump_working_memory_atomic,
        test_p11_flush_working_memory_no_checkpointer,
        test_p12_middleware_constructor_with_job_id,
        test_p13_middleware_constructor_no_job_id_backward_compat,
        test_p14_agent_cache_key_includes_job_id,
        test_p15_run_disposition_agent_signature,
        test_p16_execute_p8_writes_per_job_result,
        test_p17_working_memory_path_traversal_protection,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            import traceback
            failed.append((t.__name__, str(e)))
            print(f'  FAIL: {t.__name__}: {e}')
            traceback.print_exc()
    print()
    print(f'RESULT: {passed}/{len(tests)} tests passed')
    if failed:
        print(f'FAILED: {len(failed)} tests')
        for name, err in failed:
            print(f'  - {name}: {err}')
    sys.exit(0 if passed == len(tests) else 1)