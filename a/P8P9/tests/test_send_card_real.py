# P8P9/tests/test_send_card_real.py — 真实飞书发送集成测试
#
# 仅当 P8P9_FEISHU_REAL_SEND=1 时跑；否则自动 skip。
# 每个 job 用 P8P9-REAL- 前缀，conftest autouse cleanup 会测完即删。
#
# 覆盖：
#   - send_all_open_closure_cards 真实发送初始 open 卡片
#   - update_job_card 真实刷新卡片（已绑定 → 改 job_status → 真实 update）
#   - 7 个 job_status 模板卡片全部真实发出
#
# 真实 chat_id 用 P8P9_FEISHU_CHAT_ID 环境变量；默认 oc_d10e7b407369327a538c1204f7817499。

from __future__ import annotations
import os
import time
import uuid
import pytest

from P8P9.tests.fixtures.sample_events import event_by_level


def _unique_event(level: int) -> dict:
    """真实飞书发送测试专用：每个测试生成唯一 risk_event_id。

    避免两次跑同一个测试时，alert_id 重复（feishu Gateway 会 IDEMPOTENCY_CONFLICT）。
    注意：必须深拷贝 event_by_level 的返回值（它返回的是 SAMPLE_RISK_EVENTS 的引用），
    否则会污染后续 test_state_machine 等用同 fixture 的测试。
    """
    import copy
    e = copy.deepcopy(event_by_level(level))
    e["risk_event_id"] = f"A6-REAL-{uuid.uuid4().hex[:8]}"
    return e


# ─── 7 个 job_status 走完整流程（真实发送） ──────────────────────────────────

@pytest.mark.parametrize("target_status", [
    "open",
    "acknowledged",
    "rectifying",
    "materials_in_audit",
    "waiting_human_review",
    "ready_to_close",
    "closed",
])
def test_real_send_card_for_each_job_status(
    target_status,
    p8p9_real_mode, closure_service, mock_audit_scheduler,
    feishu_chat_id,
):
    """每个 job_status 都真实发送一张卡片到飞书群。

    流程：
      1. 初始化 job（job_id = P8P9-REAL-{STATUS}-xxx）
      2. 把 job 推到目标 status（mock 状态机变更）
      3. send_all_open_closure_cards 真实发送卡片
      4. 验证响应包含 status=ok + 至少一个 event 发送成功
      5. 验证 state.card_binding 已写入（card_id / alert_id）
    """
    from P8P9.services.card_render import send_all_open_closure_cards
    from P8P9 import business_actions

    svc = closure_service
    job_id = f"P8P9-REAL-{target_status.upper().replace('_', '-')}-{uuid.uuid4().hex[:6]}"
    actor = {"open_id": "ou_real_test", "name": "Real Sender"}

    # 1) 初始化（用唯一 event_id 避免飞书 idempotency 冲突）
    svc.initialize_job(job_id, actor="seed", events=[_unique_event(2)])

    # 2) 推到目标 status
    if target_status == "open":
        pass
    elif target_status == "acknowledged":
        business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=0)
    elif target_status == "rectifying":
        business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=0)
        svc.set_job_status(
            job_id, "rectifying",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
    elif target_status == "materials_in_audit":
        business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=0)
        svc.set_job_status(
            job_id, "rectifying",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
        business_actions.submit_rectification_materials(
            job_id,
            review_text="已补充材料（真实发送测试）",
            submissions=[], actor=actor,
            expected_version=svc.get_state(job_id)["version"],
        )
    elif target_status == "waiting_human_review":
        business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=0)
        svc.set_job_status(
            job_id, "rectifying",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
        business_actions.submit_rectification_materials(
            job_id,
            review_text="已补充材料（真实发送测试）",
            submissions=[], actor=actor,
            expected_version=svc.get_state(job_id)["version"],
        )
        svc.set_job_status(
            job_id, "waiting_human_review",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
    elif target_status == "ready_to_close":
        business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=0)
        svc.set_job_status(
            job_id, "rectifying",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
        business_actions.submit_rectification_materials(
            job_id,
            review_text="已补充材料（真实发送测试）",
            submissions=[], actor=actor,
            expected_version=svc.get_state(job_id)["version"],
        )
        svc.set_job_status(
            job_id, "ready_to_close",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
    elif target_status == "closed":
        business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=0)
        svc.set_job_status(
            job_id, "rectifying",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
        business_actions.submit_rectification_materials(
            job_id,
            review_text="已补充材料（真实发送测试）",
            submissions=[], actor=actor,
            expected_version=svc.get_state(job_id)["version"],
        )
        svc.set_job_status(
            job_id, "ready_to_close",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )
        business_actions.record_closure_review(
            job_id,
            decision="approved",
            comment="终审通过；归档完成（真实发送测试）。",
            actor=actor, expected_version=svc.get_state(job_id)["version"],
        )

    # 3) 真实发送卡片
    result = send_all_open_closure_cards(
        job_id, actor="real-test", chat_id=feishu_chat_id,
    )

    # 4) 验证
    assert result.get("status") == "ok", f"send_all_open_closure_cards 失败：{result}"
    results = result.get("results") or []
    assert results, f"没有 event 卡片被发出：{result}"
    # 至少 1 个 event 发送成功
    sent_count = sum(1 for r in results if r.get("status") == "sent")
    assert sent_count >= 1, f"至少 1 张卡片真实发送；got={results}"

    # 5) card_binding 已写入（含 card_id + alert_id）
    final = svc.get_state(job_id)
    binding = final.get("card_binding") or {}
    assert binding.get("card_id"), "card_binding.card_id 必须已写入"
    assert binding.get("alert_id"), "card_binding.alert_id 必须已写入"
    assert binding.get("chat_id") == feishu_chat_id


# ─── update_job_card 真实刷新（已绑定 → 改状态 → update） ──────────────────

def test_real_update_card_after_status_change(
    p8p9_real_mode, closure_service, mock_audit_scheduler,
    feishu_chat_id,
):
    """真实场景：先发卡片 → 改状态 → 真实刷新卡片。"""
    from P8P9.services.card_render import send_all_open_closure_cards, update_job_card
    from P8P9 import business_actions

    svc = closure_service
    job_id = f"P8P9-REAL-UPDATE-CARD-{uuid.uuid4().hex[:6]}"
    actor = {"open_id": "ou_real_test", "name": "Real Sender"}

    svc.initialize_job(job_id, actor="seed", events=[_unique_event(3)])

    # 1) 首次发送 → open 卡片
    r1 = send_all_open_closure_cards(
        job_id, actor="real-test", chat_id=feishu_chat_id,
    )
    assert r1.get("status") == "ok"
    binding = svc.get_state(job_id).get("card_binding") or {}
    card_id_1 = binding.get("card_id")
    assert card_id_1

    # 2) 改状态到 acknowledged
    business_actions.acknowledge_disposition(job_id, actor=actor, expected_version=svc.get_state(job_id)["version"])

    # 3) 真实刷新卡片
    r2 = update_job_card(job_id, version=svc.get_state(job_id)["version"])
    assert r2.get("status") == "updated", f"卡片刷新失败：{r2}"
    assert r2.get("card_id") == card_id_1


# ─── 多 event 真实发送 ──────────────────────────────────────────────────────

def test_real_send_card_with_multiple_events(
    p8p9_real_mode, closure_service,
    feishu_chat_id,
):
    """job 含 3 个不同级别 event → 真实发送 3 张 per-event 卡片。"""
    from P8P9.services.card_render import send_all_open_closure_cards

    svc = closure_service
    job_id = f"P8P9-REAL-MULTI-EV-{uuid.uuid4().hex[:6]}"
    events = [
        _unique_event(1),
        _unique_event(3),
        _unique_event(5),  # 危急
    ]

    svc.initialize_job(job_id, actor="seed", events=events)

    result = send_all_open_closure_cards(
        job_id, actor="real-test", chat_id=feishu_chat_id,
    )
    assert result.get("status") == "ok"
    results = result.get("results") or []
    sent = [r for r in results if r.get("status") == "sent"]
    assert len(sent) >= 3, f"3 张 per-event 卡片真实发送；got={len(sent)}"