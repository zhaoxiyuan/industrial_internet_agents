# P8P9/tests/conftest.py — pytest fixtures + 飞书 mock / 真实发送切换

from __future__ import annotations
import os
import shutil
import sys
from pathlib import Path

import pytest


# 确保项目根 + ocg-standalone 加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_OCG = _PROJECT_ROOT / "openclaw-channel-gateway-standalone"
if str(_OCG) not in sys.path:
    sys.path.insert(0, str(_OCG))


# ─── tmp_path fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def tmp_jobs_dir(tmp_path):
    """每个测试用 tmp_path 作为数据目录；测完自动清理。"""
    jobs_dir = tmp_path / "p8p9_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return jobs_dir


@pytest.fixture
def closure_service(tmp_jobs_dir, monkeypatch):
    """ClosureService 指向 tmp_jobs_dir（每测试独立）。"""
    monkeypatch.setenv("P8P9_BASE_DIR", str(tmp_jobs_dir))
    from P8P9.state_machine import ClosureService
    return ClosureService()


@pytest.fixture
def sample_events():
    from P8P9.tests.fixtures.sample_events import SAMPLE_RISK_EVENTS
    return SAMPLE_RISK_EVENTS


@pytest.fixture
def sample_states():
    from P8P9.tests.fixtures.sample_state import SAMPLE_STATES, get_sample
    return {"all": SAMPLE_STATES, "get": get_sample}


# ─── 飞书 mock 开关 ─────────────────────────────────────────────────────────

@pytest.fixture
def feishu_real_send():
    """读环境变量 P8P9_FEISHU_REAL_SEND；为 "1" 时真实发送。"""
    return os.environ.get("P8P9_FEISHU_REAL_SEND") == "1"


@pytest.fixture
def feishu_chat_id():
    """默认飞书 chat_id（来自生产环境）。真实发送测试使用。"""
    return os.environ.get("P8P9_FEISHU_CHAT_ID", "oc_d10e7b407369327a538c1204f7817499")


# ─── mock card_render ───────────────────────────────────────────────────────

@pytest.fixture
def mock_card_renderer(monkeypatch):
    """替换 services/card_render.update_job_card 为 mock；不实际调飞书。"""
    from P8P9.services import card_render
    calls = []

    def fake_update(job_id, version, *, actor_open_id=None):
        calls.append({"job_id": job_id, "version": version, "actor_open_id": actor_open_id})
        return {"status": "mocked", "job_id": job_id}

    monkeypatch.setattr(card_render, "update_job_card", fake_update)
    # 同时替换 business_actions 注入的引用
    from P8P9 import business_actions
    business_actions.register_card_renderer(fake_update)
    return calls


# ─── mock audit_scheduler ───────────────────────────────────────────────────

@pytest.fixture
def mock_audit_scheduler(monkeypatch):
    """替换 audit_scheduler.agent_audit_job 为同步 mock。"""
    from P8P9.services import audit_scheduler as _audit
    calls = []

    def fake_audit(job_id, *, intent="materials_audit"):
        calls.append({"job_id": job_id, "intent": intent})
        return {"status": "mocked", "job_id": job_id, "intent": intent}

    monkeypatch.setattr(_audit, "agent_audit_job", fake_audit)
    from P8P9 import business_actions
    business_actions.register_audit_scheduler(fake_audit)
    return calls


# ─── 真实 P8P9 测试模式 ─────────────────────────────────────────────────────

@pytest.fixture
def p8p9_real_mode():
    """真实飞书发送测试 fixture（仅当 P8P9_FEISHU_REAL_SEND=1 时启用）。"""
    if os.environ.get("P8P9_FEISHU_REAL_SEND") != "1":
        pytest.skip("设置 P8P9_FEISHU_REAL_SEND=1 才能跑真实飞书发送测试")
    yield


# ─── 自动清理 ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_p8p9_real_jobs():
    """每个测试结束后清理 data/p8p9_jobs/P8P9-REAL-* 残留（mock 数据用完即删）。"""
    yield
    jobs_root = _PROJECT_ROOT / "data" / "p8p9_jobs"
    if not jobs_root.exists():
        return
    for d in jobs_root.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith("P8P9-REAL-") or d.name.startswith("JOB-MOCK-T-"):
            shutil.rmtree(d, ignore_errors=True)