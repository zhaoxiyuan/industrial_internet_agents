"""临时工具：遍历 events 刷新所有 event 卡片的 form 容器。

背景：update_job_card 只刷新 card_binding.card_id 那张。
open_work_ticket 时按 event 各发了一张卡，但只有最后一张（最高风险）的
card_id 写入了 card_binding。其他 N-1 张卡片仍是旧 schema（无 form 容器）。

用法：
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tests/refresh_all_event_cards.py 20260917094020030
"""
import sys

import feishu_gateway_cli.feishu_card as feishu_card
from P8P9.services.card_render import _build_card_json, _filter_state_for_event, _throttle
from P8P9.state_machine import ClosureService


def refresh_all_event_cards(job_id: str) -> None:
    svc = ClosureService()
    state = svc.get_state(job_id)
    version = state["version"]

    results = []
    for event in state.get("events", []):
        rid = event.get("risk_event_id")
        if not rid:
            continue
        alert_id = f"P8P9-{job_id}-{rid}"
        entry = feishu_card.lookup_card_id(alert_id)
        if not entry:
            print(f"  ⚠️  no card_id for alert_id={alert_id}")
            continue
        card_id = entry["card_id"]
        sequence = int(entry.get("sequence") or 0) + 1

        event_view = _filter_state_for_event(state, rid)
        new_card_json = _build_card_json(event_view, version)

        try:
            _throttle()
            feishu_card.update_card_entity(
                card_id, new_card_json,
                sequence=sequence,
                account_id=state.get("card_binding", {}).get("account_id"),
            )
            feishu_card.register_card(
                alert_id, card_id,
                account_id=state.get("card_binding", {}).get("account_id"),
                message_id=entry.get("message_id"),
                sequence=sequence,
                card_json=new_card_json,
            )
            results.append({"alert_id": alert_id, "card_id": card_id, "sequence": sequence, "status": "ok"})
        except Exception as e:
            results.append({"alert_id": alert_id, "card_id": card_id, "status": "error", "error": str(e)})

    print()
    print(f"=== {job_id} event 卡片刷新结果 ===")
    for r in results:
        print(f'  {r["alert_id"][:70]}...')
        print(f'    -> card_id={r["card_id"]} sequence={r.get("sequence", "N/A")} status={r["status"]}')
        if r["status"] == "error":
            print(f'    error: {r.get("error")}')


if __name__ == "__main__":
    job_id = sys.argv[1] if len(sys.argv) > 1 else "20260917094020030"
    refresh_all_event_cards(job_id)