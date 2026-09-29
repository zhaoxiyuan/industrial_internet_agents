# P8P9/tests/fixtures/sample_events.py — 风险事件 mock 数据
#
# 5 个 risk_event，覆盖 risk_level 1-5 + 已关闭态。

from __future__ import annotations
from typing import Any, Dict, List


SAMPLE_RISK_EVENTS: List[Dict[str, Any]] = [
    {
        "risk_event_id": "A6-MOCK-001",
        "risk_level": 1,
        "risk_level_name": "轻微",
        "event_type": "PPE缺失",
        "involved_persons": ["P7"],
        "first_seen": "2026-09-16T10:00:00+00:00",
        "last_seen": "2026-09-16T10:00:07+00:00",
        "event_status": "pending",
        "risk_basis": "头盔缺失 7 秒（25/25 帧全部缺失）",
        "suggestions": ["持续观察", "10秒未恢复升级"],
        "evidence": {
            "ppe_item": "helmet",
            "missing_frames": 25,
            "evidence_id": "ev-low-001",
        },
    },
    {
        "risk_event_id": "A6-MOCK-002",
        "risk_level": 2,
        "risk_level_name": "一般",
        "event_type": "监护人离岗",
        "involved_persons": ["P8", "P9"],
        "first_seen": "2026-09-16T10:05:00+00:00",
        "last_seen": "2026-09-16T10:05:30+00:00",
        "event_status": "pending",
        "risk_basis": "监护人离开作业区域 30 秒；P8 仍在作业中",
        "suggestions": ["立即召回监护人", "暂停作业"],
        "evidence": {
            "guard_present": False,
            "evidence_id": "ev-mid-002",
        },
    },
    {
        "risk_event_id": "A6-MOCK-003",
        "risk_level": 3,
        "risk_level_name": "较重",
        "event_type": "可燃气体超标",
        "involved_persons": ["P10"],
        "first_seen": "2026-09-16T10:10:00+00:00",
        "last_seen": "2026-09-16T10:12:00+00:00",
        "event_status": "pending",
        "risk_basis": "动火作业区域可燃气体浓度超标 1.2 倍（持续 120s）",
        "suggestions": ["立即疏散人员", "关闭动火源", "上报值班"],
        "evidence": {
            "sensor_id": "gas-A3",
            "max_concentration_pct_lel": 24.0,
            "evidence_id": "ev-high-003",
        },
    },
    {
        "risk_event_id": "A6-MOCK-004",
        "risk_level": 4,
        "risk_level_name": "严重",
        "event_type": "未授权进入受限空间",
        "involved_persons": ["P11"],
        "first_seen": "2026-09-16T10:15:00+00:00",
        "last_seen": "2026-09-16T10:15:05+00:00",
        "event_status": "pending",
        "risk_basis": "无授权人员进入受限空间；未佩戴 PPE",
        "suggestions": ["立即驱离", "启动应急响应", "通报安全部门"],
        "evidence": {
            "zone": "limited-space-B",
            "unauthorized": True,
            "evidence_id": "ev-crit-004",
        },
    },
    {
        "risk_event_id": "A6-MOCK-005",
        "risk_level": 5,
        "risk_level_name": "危急",
        "event_type": "明火接触可燃气体",
        "involved_persons": ["P12", "P13"],
        "first_seen": "2026-09-16T10:20:00+00:00",
        "last_seen": "2026-09-16T10:20:10+00:00",
        "event_status": "pending",
        "risk_basis": "明火作业同时检测到可燃气体浓度 ≥ 2 倍 LEL；极高爆炸风险",
        "suggestions": ["立即停火", "全区域疏散", "拨打 119"],
        "evidence": {
            "gas_concentration_pct_lel": 48.0,
            "ignition_source": True,
            "evidence_id": "ev-urgent-005",
        },
    },
]


# 单事件 fixture（每个 risk_level 一份）
def event_by_level(level: int) -> Dict[str, Any]:
    for e in SAMPLE_RISK_EVENTS:
        if e.get("risk_level") == level:
            return e
    return SAMPLE_RISK_EVENTS[0]


__all__ = ["SAMPLE_RISK_EVENTS", "event_by_level"]