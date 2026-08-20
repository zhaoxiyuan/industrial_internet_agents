"""A7/api 工作记忆控制器 单元测试（U1-U8）。

覆盖：
- A7/schema/p8_state._working_memory_reducer 行为（upsert / 删除哨兵 / 非 dict 防御）
- A7/api/p8_working_memory_ctrl.get_working_memory_snapshot 行为
  （参数校验 / 未知 thread / archived_recent 排序 / 上限 20）
- A7/middleware/p8_archive_middleware 行为
  （终态归档 + 删除 working / 非终态 no-op / 异常不阻断）

约定：
- 测试前 reset_archive() 清空；checkpointer 单例状态不重置（in-memory）
- 测试用 P8Job 简化 dict（不依赖 Pydantic 校验）
"""
import sys
import shutil
from pathlib import Path

# 让 tests/ 能找到 A7/ 与 agents/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from A7.schema.p8_state import (
    _working_memory_reducer,
    _TERMINAL_STATUSES,
    is_terminal_status,
)
from A7.storage import (
    save_archived_job,
    reset_archive,
    load_all_archived_jobs,
)
from A7.api.p8_working_memory_ctrl import (
    get_working_memory_snapshot,
    _read_archived_recent,
    ARCHIVED_RECENT_LIMIT,
)


# ============================================================================
# P8State Reducer 测试（U1-U3）
# ============================================================================

def test_u1_reducer_upsert_existing():
    """U1: reducer 对已存在的 p8_job_id 字段 upsert（合并）。"""
    existing = [
        {"p8_job_id": "P8J-A", "status": "pending"},
        {"p8_job_id": "P8J-B", "status": "notified"},
    ]
    new = [{"p8_job_id": "P8J-A", "status": "waiting_decision", "channel": "HITL"}]
    result = _working_memory_reducer(existing, new)

    assert len(result) == 2, f"应仍是 2 条，得到 {len(result)}"
    by_pid = {j["p8_job_id"]: j for j in result}
    assert by_pid["P8J-A"]["status"] == "waiting_decision"
    assert by_pid["P8J-A"]["channel"] == "HITL"
    # 未被更新的应原样保留
    assert by_pid["P8J-B"]["status"] == "notified"
    print("U1 PASS: reducer upserts existing pid")


def test_u2_reducer_delete_sentinel():
    """U2: reducer 接受 {"__delete__": "<pid>"} 哨兵删除对应元素。"""
    existing = [
        {"p8_job_id": "P8J-A", "status": "completed"},
        {"p8_job_id": "P8J-B", "status": "pending"},
    ]
    # 中间件触发删除 P8J-A
    new = [{"__delete__": "P8J-A"}]
    result = _working_memory_reducer(existing, new)

    assert len(result) == 1, f"删除后应剩 1 条，得到 {len(result)}"
    assert result[0]["p8_job_id"] == "P8J-B"
    print("U2 PASS: reducer honors __delete__ sentinel")


def test_u3_reducer_no_op_for_invalid_types():
    """U3: reducer 对非 list 输入 / 非 dict 元素防御性兜底。"""
    # 非 list 输入 → 返回 existing 副本过滤掉非 dict
    dirty = [
        {"p8_job_id": "P8J-A", "status": "pending"},
        None,           # 非法
        "not a dict",   # 非法
        {"p8_job_id": "P8J-B", "status": "notified"},
    ]
    result = _working_memory_reducer(dirty, [])
    assert len(result) == 2
    assert all(isinstance(j, dict) for j in result)
    print("U3 PASS: reducer filters non-dict entries")


# ============================================================================
# 控制器测试（U4-U6）
# ============================================================================

def _seed_archived_jobs(n: int, base_ts: str = "2026-08-13T18:00:00Z"):
    """构造 n 条 archived job，archived_at 递增便于排序断言。"""
    reset_archive()
    for i in range(n):
        pid = f"P8J-20260813-180000-{i:03d}"
        # 每条 archived_at + i 分钟
        hh, mm = 18, i % 60
        ts = f"2026-08-13T{hh:02d}:{mm:02d}:00Z"
        save_archived_job(
            pid,
            {
                "p8_job_id": pid,
                "max_level": "HIGH" if i % 2 else "MEDIUM",
                "risk_basis": f"test-{i}",
                "decision": "rectify",
                "note": f"by=test-{i}",
                "archived_at": ts,
                "a6_event_ids": [f"A6-{i}"],
                "summary": f"HIGH|rectify|by=test-{i}",
            },
        )


def test_u4_ctrl_rejects_empty_job_id():
    """U4: get_working_memory_snapshot("") → ValueError。"""
    try:
        get_working_memory_snapshot("")
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "job_id" in str(exc)
        print("U4 PASS: empty job_id rejected")


def test_u5_ctrl_unknown_thread_returns_empty_working():
    """U5: 未知 thread_id → working_memory=[]，archived_recent 仍真实返回。"""
    _seed_archived_jobs(3)
    result = get_working_memory_snapshot("JOB-NEVER-INVOKED-999")

    assert result["status"] == "ok"
    assert result["job_id"] == "JOB-NEVER-INVOKED-999"
    assert result["working_memory"] == [], "未知 thread 应返空 working_memory"
    assert len(result["archived_recent"]) == 3, "archived_recent 应真实存在"
    print("U5 PASS: unknown thread returns empty working + real archived")


def test_u6_ctrl_archived_recent_sorted_and_capped():
    """U6: archived_recent 按 archived_at 倒序 + 上限 ARCHIVED_RECENT_LIMIT(20)。"""
    _seed_archived_jobs(25)
    result = get_working_memory_snapshot("JOB-20260813-001")

    archived = result["archived_recent"]
    assert len(archived) == ARCHIVED_RECENT_LIMIT == 20, \
        f"上限应为 20，得到 {len(archived)}"

    # 验证倒序：archived_at 严格递减
    times = [j["archived_at"] for j in archived]
    assert times == sorted(times, reverse=True), \
        f"archived_recent 未按 archived_at 倒序: {times}"
    print("U6 PASS: archived_recent sorted DESC and capped at", ARCHIVED_RECENT_LIMIT)


# ============================================================================
# Middleware 测试（U7-U8）
# ============================================================================

def test_u7_middleware_archives_terminal_status():
    """U7: middleware 收到终态 P8_job → 写入长期记忆 + 返回删除哨兵。"""
    from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware

    reset_archive()

    mw = P8ArchiveMiddleware()
    state = {
        "working_memory": [
            {
                "p8_job_id": "P8J-TERM-001",
                "status": "completed",  # 终态
                "max_level": "HIGH",
                "risk_basis": "CH4 4.8%",
                "decision": "rectify",
                "note": "by=zhang",
                "a6_event_ids": ["A6-001"],
                "created_at": "2026-08-13T18:00:00Z",
            }
        ],
        "long_term_memory": {},
    }

    patch = mw.after_model(state, runtime=None)
    assert patch is not None, "终态应触发 patch"

    # 验证 patch 包含删除哨兵 + long_term_memory 写入
    working_patch = patch.get("working_memory")
    assert any(isinstance(j, dict) and (
                j.get("__p8__delete__") == "P8J-TERM-001" or
                j.get("__delete__") == "P8J-TERM-001"
               ) for j in working_patch), \
        f"patch 应含删除哨兵: {working_patch}"

    ltm_patch = patch.get("long_term_memory") or {}
    assert "P8J-TERM-001" in ltm_patch, \
        f"patch 应含 long_term_memory 写入: {ltm_patch}"

    # 验证 storage 已写入
    stored = load_all_archived_jobs()
    assert any(j["p8_job_id"] == "P8J-TERM-001" for j in stored), \
        "middleware 后 storage 应已写入 P8J-TERM-001"
    print("U7 PASS: terminal status → archived + delete sentinel")


def test_u8_middleware_no_op_for_non_terminal():
    """U8: middleware 收到非终态 P8_job → 返 None（no-op）。"""
    from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware

    reset_archive()

    mw = P8ArchiveMiddleware()
    state = {
        "working_memory": [
            {
                "p8_job_id": "P8J-PENDING-001",
                "status": "pending",  # 非终态
                "max_level": "HIGH",
                "a6_event_ids": ["A6-001"],
            }
        ],
        "long_term_memory": {},
    }

    patch = mw.after_model(state, runtime=None)
    assert patch is None, f"非终态应 no-op（返 None），得到 {patch}"

    # 验证 storage 没被写入
    stored = load_all_archived_jobs()
    assert not any(j["p8_job_id"] == "P8J-PENDING-001" for j in stored), \
        "非终态不应写入 storage"
    print("U8 PASS: non-terminal status → no-op")


# ============================================================================
# Bot 模式 chat_reply 集成测试（U9）
# ============================================================================
# ★ 2026-08-18：飞书侧消息**可选**带 [job_id=...] 前缀；不带时
#   - parse_job_id_marker 返 (None, 原文本)
#   - build_human_message(None, 原文本) 返 原文本（不带前缀）
#   - chat_reply_handler 不再触发 _JOB_ID_MISSING_HINT 拦截
#   - 无前缀消息**仍然**进 disposition_demo；LLM 通过 list_active_p8_jobs 自助查
# ============================================================================

def _make_event(text: str) -> dict:
    """构造一个最小可用的飞书入站 event dict（chat_reply_handler 用到的字段）。"""
    return {
        "id": "evt_test_bot_mode_001",
        "message": {"text": text},
        "chat_id": "oc_test_chat_bot_mode",
        "account_id": "test_account",
    }


def test_u9a_parse_no_prefix_returns_none():
    """U9a: parse_job_id_marker 对无前缀消息返回 (None, 原文本)。"""
    from A7.adapters.chat_reply import parse_job_id_marker

    job_id, user_msg = parse_job_id_marker("有哪些正在进行的审核任务？")
    assert job_id is None, f"无前缀应返 None job_id，得到 {job_id!r}"
    assert user_msg == "有哪些正在进行的审核任务？", \
        f"原文本应完整保留，得到 {user_msg!r}"
    print("U9a PASS: parse_job_id_marker honors no-prefix")


def test_u9b_build_human_message_without_job_id():
    """U9b: build_human_message(None, msg) 返 msg（不带前缀）。"""
    from A7.adapters.chat_reply import build_human_message

    result = build_human_message(None, "有哪些任务？")
    assert result == "有哪些任务？", f"无 job_id 时应原样返回，得到 {result!r}"
    assert "[job_id=" not in result, "无前缀消息绝不能被强加 [job_id=...]"

    # 对照：带 job_id 时仍正常加前缀（向后兼容）
    with_prefix = build_human_message("JOB-001", "为 A6-... 创建")
    assert with_prefix == "[job_id=JOB-001] 为 A6-... 创建", \
        f"带 job_id 时应加前缀，得到 {with_prefix!r}"
    print("U9b PASS: build_human_message handles no-prefix correctly")


def test_u9c_chat_reply_no_prefix_passes_to_disposition_demo():
    """U9c: chat_reply_handler 收到无前缀消息 → 调 disposition_demo（mocked），不抛。

    ★ 核心验证点：
      - 不再触发旧的 _JOB_ID_MISSING_HINT 拦截（被删）
      - disposition_demo 被调用，且参数**不含** [job_id=...] 前缀
      - LLM 输出经过 reply_to_event 回写
      - ack_event 标记事件已处理
    """
    from unittest.mock import patch, MagicMock

    # 模拟 disposition_demo 返回值（避免真实 LLM 调用）
    fake_llm_response = "当前有 2 条审核任务：P8J-001（CRITICAL）/P8J-002（HIGH）。"
    mock_demo = MagicMock(return_value=fake_llm_response)
    mock_reply = MagicMock()
    mock_ack = MagicMock()

    # patch chat_reply 模块内的符号引用 + channel_gateway_client 引用
    with patch("A7.adapters.chat_reply.disposition_demo", mock_demo), \
         patch("A7.adapters.chat_reply.reply_to_event", mock_reply), \
         patch("A7.adapters.chat_reply.ack_event", mock_ack):
        from A7.adapters.chat_reply import chat_reply_handler

        event = _make_event("当前有哪些正在进行的审核任务？")
        chat_reply_handler(event)  # **必须不抛**

    # 验证 1：disposition_demo 被调用了 1 次
    assert mock_demo.call_count == 1, \
        f"disposition_demo 应被调用 1 次，得到 {mock_demo.call_count}"

    # 验证 2：传给 disposition_demo 的 message **不含** [job_id=...] 前缀
    call_args = mock_demo.call_args
    # 兼容位置参数 / 关键字参数两种调用风格
    if call_args.args:
        passed_msg = call_args.args[0]
    else:
        passed_msg = call_args.kwargs.get("message", "")
    assert "[job_id=" not in passed_msg, \
        f"Bot 模式无前缀消息不应被强加 [job_id=...]：得到 {passed_msg!r}"
    assert "有哪些正在进行的审核任务" in passed_msg, \
        f"原文本应完整透传给 LLM：得到 {passed_msg!r}"

    # 验证 3：reply_to_event 用 LLM 输出回写
    assert mock_reply.call_count == 1, "reply_to_event 应被调用 1 次"
    reply_kwargs = mock_reply.call_args.kwargs or {}
    reply_args = mock_reply.call_args.args or ()
    passed_text = reply_kwargs.get("text") or (reply_args[1] if len(reply_args) > 1 else "")
    assert passed_text == fake_llm_response, \
        f"reply_to_event 应使用 LLM 输出回写，得到 {passed_text!r}"

    # 验证 4：ack_event 标记事件已处理
    assert mock_ack.call_count == 1, "ack_event 应被调用 1 次"
    print("U9c PASS: chat_reply_handler Bot 模式无前缀消息走通到 LLM")


def test_u9d_chat_reply_with_prefix_still_works():
    """U9d: 向后兼容：带 [job_id=...] 前缀的消息仍走原路径（不破坏旧调用方）。"""
    from unittest.mock import patch, MagicMock

    mock_demo = MagicMock(return_value="已为 JOB-001 创建 P8_job")
    mock_reply = MagicMock()
    mock_ack = MagicMock()

    with patch("A7.adapters.chat_reply.disposition_demo", mock_demo), \
         patch("A7.adapters.chat_reply.reply_to_event", mock_reply), \
         patch("A7.adapters.chat_reply.ack_event", mock_ack):
        from A7.adapters.chat_reply import chat_reply_handler

        event = _make_event("[job_id=JOB-20260818-001] 为 A6-001 创建处置任务")
        chat_reply_handler(event)

    assert mock_demo.call_count == 1
    call_args = mock_demo.call_args
    passed_msg = call_args.args[0] if call_args.args else call_args.kwargs.get("message", "")
    assert "[job_id=JOB-20260818-001]" in passed_msg, \
        f"带前缀应保留 [job_id=...] 标记：得到 {passed_msg!r}"
    assert "A6-001" in passed_msg
    print("U9d PASS: backward-compat with-prefix path still works")


def test_u10_middleware_job_id_pass_through():
    """U10 (2026-08-20): P8ArchiveMiddleware(job_id=...) 透传 → 触发 per-job 双写。

    验证：
    - middleware 构造时接受 job_id
    - 终态触发 after_model 时 save_archived_job 透传 job_id → per-job 归档文件生成
    """
    import json
    from pathlib import Path
    from A7.middleware.p8_archive_middleware import P8ArchiveMiddleware
    from agents.workflow.file_utils import get_job_dir

    reset_archive()
    job_id = "JOB-U10-PASS-001"
    per_job_dir = Path(get_job_dir(job_id)) / "P8"
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)

    mw = P8ArchiveMiddleware(job_id=job_id)
    assert mw.job_id == job_id, "job_id 未保存"

    state = {
        "working_memory": [
            {
                "p8_job_id": "P8J-U10-001",
                "status": "completed",  # 终态
                "max_level": "HIGH",
                "risk_basis": "CH4 4.8%",
                "decision": "rectify",
                "note": "by=zhang",
                "a6_event_ids": ["A6-001"],
                "created_at": "2026-08-13T18:00:00Z",
            }
        ],
        "long_term_memory": {},
    }
    patch = mw.after_model(state, runtime=None)
    assert patch is not None

    # per-job 文件应生成
    per_job_path = per_job_dir / "archived.json"
    assert per_job_path.exists(), f"per-job 归档文件未生成: {per_job_path}"
    per_data = json.loads(per_job_path.read_text(encoding='utf-8'))
    assert "P8J-U10-001" in per_data
    assert per_data["P8J-U10-001"]["job_id"] == job_id

    # 清理
    if per_job_dir.exists():
        shutil.rmtree(per_job_dir)
    print('U10 PASS: middleware job_id 透传 → per-job 双写 OK')


if __name__ == '__main__':
    tests = [
        test_u1_reducer_upsert_existing,
        test_u2_reducer_delete_sentinel,
        test_u3_reducer_no_op_for_invalid_types,
        test_u4_ctrl_rejects_empty_job_id,
        test_u5_ctrl_unknown_thread_returns_empty_working,
        test_u6_ctrl_archived_recent_sorted_and_capped,
        test_u7_middleware_archives_terminal_status,
        test_u8_middleware_no_op_for_non_terminal,
        # Bot 模式 (2026-08-18 新增)
        test_u9a_parse_no_prefix_returns_none,
        test_u9b_build_human_message_without_job_id,
        test_u9c_chat_reply_no_prefix_passes_to_disposition_demo,
        test_u9d_chat_reply_with_prefix_still_works,
        # 2026-08-20 per-job 化
        test_u10_middleware_job_id_pass_through,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f'  FAIL: {t.__name__}: {e}')
            import traceback
            traceback.print_exc()
            failed += 1
    print()
    print(f'RESULT: {passed}/{len(tests)} tests passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)