"""A7/schema 单元测试（T1-T12）。

覆盖：
- P8Job 创建（N=1 / N=3 风险叠加）
- P8Job 字段校验（重复 / 格式 / 空数组）
- P8JobUpdate 创建/更新模式
- PushMessage idempotency_key 自动填充与显式覆盖
- 终态集合完整性（4 个）与 is_terminal_status 全 7 状态判定
"""
import sys
from pathlib import Path

# 让 tests/ 能找到 A7/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from A7.schema import (
    P8Job, P8JobUpdate, PushMessage,
    P8JobStatus, RiskLevel, Channel, ActionType,
    is_terminal_status, get_terminal_statuses, _TERMINAL_STATUSES,
)


def make_kwargs(**overrides):
    """P8Job 必填字段默认值（便于单测覆盖）。"""
    base = dict(
        risk_basis='test',
        max_level=RiskLevel.HIGH,
        urgency_emoji='\U0001f7e0',
        assignee_role='Workshop Director',
        due_at='2026-08-13T19:00:00Z',
        created_at='2026-08-13T18:00:00Z',
    )
    base.update(overrides)
    return base


def test_t1_n1_single_event():
    """T1: P8Job N=1 单事件创建。"""
    job = P8Job(
        p8_job_id='P8J-20260813-180000-001',
        a6_event_ids=['A6-20260813-094525-230'],
        **make_kwargs(),
    )
    assert len(job.a6_event_ids) == 1
    assert job.status == P8JobStatus.PENDING  # 默认
    print('T1 PASS: N=1 single event P8Job created')


def test_t2_n3_aggregate():
    """T2: P8Job N=3 风险叠加。"""
    job = P8Job(
        p8_job_id='P8J-20260813-180000-002',
        a6_event_ids=['A6-001', 'A6-002', 'A6-003'],
        level_distribution={RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 2},
        **make_kwargs(risk_basis='concurrent PPE+noise+restricted-area'),
    )
    assert len(job.a6_event_ids) == 3
    assert job.max_level == RiskLevel.HIGH
    print('T2 PASS: N=3 aggregate P8Job created')


def test_t3_duplicate_a6_events_rejected():
    """T3: a6_event_ids 内重复应拒绝。"""
    try:
        P8Job(
            p8_job_id='P8J-20260813-180000-003',
            a6_event_ids=['A6-X', 'A6-Y', 'A6-X'],
            **make_kwargs(),
        )
        assert False, 'should have raised'
    except Exception as e:
        assert 'unique' in str(e).lower()
        print('T3 PASS: duplicate a6_event_ids rejected')


def test_t4_bad_p8_job_id_format():
    """T4: p8_job_id 格式不符应拒绝。"""
    try:
        P8Job(
            p8_job_id='invalid-id',
            a6_event_ids=['A6-X'],
            **make_kwargs(),
        )
        assert False, 'should have raised'
    except Exception as e:
        print('T4 PASS: bad p8_job_id format rejected:', type(e).__name__)


def test_t5_empty_a6_events_rejected():
    """T5: a6_event_ids 空数组应拒绝（min_length=1）。"""
    try:
        P8Job(
            p8_job_id='P8J-20260813-180000-004',
            a6_event_ids=[],
            **make_kwargs(),
        )
        assert False, 'should have raised'
    except Exception as e:
        print('T5 PASS: empty a6_event_ids rejected')


def test_t6_p8jobupdate_create_mode():
    """T6: P8JobUpdate 创建模式（p8_job_id=None）。"""
    upd = P8JobUpdate(a6_event_ids=['A6-A', 'A6-B'])
    assert upd.p8_job_id is None
    assert upd.status is None
    print('T6 PASS: P8JobUpdate.create mode')


def test_t7_p8jobupdate_update_mode():
    """T7: P8JobUpdate 更新模式（指定 p8_job_id + status + channel + note）。"""
    upd = P8JobUpdate(
        a6_event_ids=['A6-A'],
        p8_job_id='P8J-20260813-180000-001',
        status=P8JobStatus.NOTIFIED,
        channel=Channel.HITL,
        note='by operator:zhang',
    )
    assert upd.status == P8JobStatus.NOTIFIED
    assert upd.channel == Channel.HITL
    print('T7 PASS: P8JobUpdate.update mode')


def test_t8_pushmessage_auto_idempotency():
    """T8: PushMessage 不传 idempotency_key 时自动用 p8_job_id 填充。"""
    msg = PushMessage(
        title='test', body='body',
        a6_event_ids=['A6-X'],
        assignee_role='Worker',
        risk_level=RiskLevel.HIGH,
        job_id='JOB-20260813-001',
        p8_job_id='P8J-20260813-180000-001',
    )
    assert msg.idempotency_key == 'P8J-20260813-180000-001'
    print('T8 PASS: idempotency_key auto-filled = p8_job_id')


def test_t9_pushmessage_explicit_idempotency():
    """T9: PushMessage 显式 idempotency_key 时原样使用。"""
    msg = PushMessage(
        title='t', body='b',
        a6_event_ids=['A6-X'],
        assignee_role='Worker',
        risk_level=RiskLevel.HIGH,
        job_id='JOB-1',
        p8_job_id='P8J-20260813-180000-001',
        idempotency_key='custom-key-123',
    )
    assert msg.idempotency_key == 'custom-key-123'
    print('T9 PASS: explicit idempotency_key honored')


def test_t10_terminal_set_complete():
    """T10: 终态集合恰好 4 个，且与 _TERMINAL_STATUSES 完全一致。"""
    expected = {'completed', 'rejected', 'escalated', 'resumed'}
    assert _TERMINAL_STATUSES == expected
    assert get_terminal_statuses() == frozenset(expected)
    print('T10 PASS: terminal set has 4 entries:', sorted(_TERMINAL_STATUSES))


def test_t11_is_terminal_status_all_seven():
    """T11: is_terminal_status 对全 7 个 P8JobStatus 正确判定。"""
    cases = [
        ('pending', False),
        ('notified', False),
        ('waiting_decision', False),
        ('completed', True),
        ('rejected', True),
        ('escalated', True),
        ('resumed', True),
    ]
    for status, expected in cases:
        got = is_terminal_status(status)
        assert got == expected, f'{status}: expected {expected}, got {got}'
    print('T11 PASS: all 7 P8JobStatus classified correctly')


def test_t12_enum_values_aligned():
    """T12: P8JobStatus enum 值与字符串集合对齐。"""
    valid_values = {'pending', 'notified', 'waiting_decision',
                    'completed', 'rejected', 'escalated', 'resumed'}
    for status in P8JobStatus:
        assert status.value in valid_values
    assert len(list(P8JobStatus)) == 7
    print('T12 PASS: 7 P8JobStatus enum values aligned with strings')


if __name__ == '__main__':
    tests = [
        test_t1_n1_single_event,
        test_t2_n3_aggregate,
        test_t3_duplicate_a6_events_rejected,
        test_t4_bad_p8_job_id_format,
        test_t5_empty_a6_events_rejected,
        test_t6_p8jobupdate_create_mode,
        test_t7_p8jobupdate_update_mode,
        test_t8_pushmessage_auto_idempotency,
        test_t9_pushmessage_explicit_idempotency,
        test_t10_terminal_set_complete,
        test_t11_is_terminal_status_all_seven,
        test_t12_enum_values_aligned,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f'  FAIL: {t.__name__}: {e}')
    print()
    print(f'RESULT: {passed}/{len(tests)} tests passed')
    sys.exit(0 if passed == len(tests) else 1)