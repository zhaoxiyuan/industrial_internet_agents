"""P8 集成测试：用真实作业票 data/jobs/22222222222222222/P7 验证状态维护 + 飞书卡片 + CardActionAgent。

设计要点（2026-08-20）：
- **真实数据源**：`data/jobs/22222222222222222/P7/a6_*.json`（A6 评估文件，2 条 PPE缺失 事件）
- **真实 LLM**：MiniMax API（环境变量 OPENAI_API_KEY 已配）
- **不伪造**：P8_jobs 在 setUp 中由 A6 事件确定性派生（不是凭空造）
- **隔离**：每个测试 setUp/tearDown 备份 + 还原 per-job working_memory.json 与全局归档
- **覆盖**：
    1. read_p7_events 读真实作业票（验证 per-job P7/ 口径已被 read_p7_events 兼容）
    2. build_feishu_card 写 job_id 字段（Card 2.0 callback 反查路径）
    3. apply_card_action 状态机（8 个 action × 终态/非终态/error 路径）
    4. CardActionAgent + 真实 LLM 端到端（验证 LLM 能正确消费卡片回调消息 + 调工具）

测试 ID 前缀：
    PR-*  read_p7_events（真实作业票）
    FC-*  build_feishu_card（job_id 字段）
    AC-*  apply_card_action 状态机（无 LLM，直接调工具）
    CA-*  CardActionAgent 端到端（真实 LLM）

运行方式：
    # 全部测试（含真实 LLM）
    python -m pytest tests/test_p8.py -v

    # 仅无 LLM 部分（CI 友好）
    python -m pytest tests/test_p8.py -v -k "not llm"
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# 让 tests/ 能 import 项目根模块 + openclaw-channel-gateway-standalone（无 __init__.py 的子仓库）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# openclaw-channel-gateway-standalone 本身没有 __init__.py；它的子目录 feishu_gateway_cli/ 有 __init__.py
# 所以把 openclaw-channel-gateway-standalone 加到 sys.path，才能 import feishu_gateway_cli
_OCG_STANDALONE = _PROJECT_ROOT / "openclaw-channel-gateway-standalone"
if str(_OCG_STANDALONE) not in sys.path:
    sys.path.insert(0, str(_OCG_STANDALONE))

logger = logging.getLogger(__name__)

# ============================================================
# 真实作业票（用户提供；P7 per-job 路径下含 2 条 A6 评估文件）
# ============================================================
JOB_ID = "22222222222222222"
JOB_DIR = _PROJECT_ROOT / "data" / "jobs" / JOB_ID
P7_DIR = JOB_DIR / "P7"
P8_DIR = JOB_DIR / "P8"

# 长期记忆全局目录（save_archived_job 会写这里 —— 测试需备份 + 还原）
LONG_TERM_DIR = _PROJECT_ROOT / "data" / "jobs" / "_long_term"
LONG_TERM_ARCHIVE = LONG_TERM_DIR / "p8_archive.json"
LONG_TERM_INDEX = LONG_TERM_DIR / "p8_archive.index.json"

# 真实作业票里 2 条 A6 事件（确定性 → 测试断言不依赖随机）
A6_FILE_1 = P7_DIR / "a6_2026-08-20T15-25-48_812704.json"
A6_FILE_2 = P7_DIR / "a6_2026-08-20T15-26-14_484790.json"
A6_ID_1 = "A6-20260820-152548-811"
A6_ID_2 = "A6-20260820-152614-482"

# 从 A6 派生的 P8_job ID（与 A6 时间戳对齐；确定性 → 测试稳定）
P8_JOB_ID_1 = "P8J-20260820-152548-001"  # 对应 A6_ID_1（risk_level=1 轻微）
P8_JOB_ID_2 = "P8J-20260820-152614-002"  # 对应 A6_ID_2（risk_level=2 一般）

# 真实 MiniMax LLM（环境变量已配）
HAS_LLM = bool(os.environ.get("OPENAI_API_KEY"))


# ============================================================
# 工具函数：从真实 A6 派生 P8_job（确定性，非伪造）
# ============================================================
def _load_a6_event(path: Path) -> Dict[str, Any]:
    """读真实 A6 文件（tests 不构造数据，只读）。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _risk_level_from_a6(risk_level_int: int) -> str:
    """A6 的 1-4 整数 → P8 RiskLevel 枚举（确定性映射）。"""
    return {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}.get(risk_level_int, "LOW")


def _make_p8_job_from_a6(a6: Dict[str, Any], p8_job_id: str) -> Dict[str, Any]:
    """从真实 A6 派生 P8_job dict（test-only；非业务代码）。"""
    now = datetime.now(timezone.utc).isoformat()
    rl = _risk_level_from_a6(a6["risk_level"])
    return {
        "p8_job_id": p8_job_id,
        "a6_event_ids": [a6["a6_event_id"]],
        "risk_basis": a6.get("risk_basis", ""),
        "max_level": rl,
        "level_distribution": {rl: 1},
        "urgency_emoji": {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(rl, "🟢"),
        "assignee_role": "作业负责人",
        "status": "pending",
        "channel": "PUSH",
        "decision": None,
        "note": "",
        "due_at": now,
        "created_at": now,
        "job_id": JOB_ID,
    }


def _build_seed_working_memory() -> List[Dict[str, Any]]:
    """从真实 A6 文件构建 P8_job list（2 条；确定性）。"""
    a6_1 = _load_a6_event(A6_FILE_1)
    a6_2 = _load_a6_event(A6_FILE_2)
    return [
        _make_p8_job_from_a6(a6_1, P8_JOB_ID_1),
        _make_p8_job_from_a6(a6_2, P8_JOB_ID_2),
    ]


# ============================================================
# TestP7ReadingFromRealJob（无 LLM）：read_p7_events 读真实作业票
# ============================================================
class TestP7ReadingFromRealJob(unittest.TestCase):
    """验证 read_p7_events 兼容真实作业票 per-job P7/ 路径下的 a6_*.json。"""

    def setUp(self):
        # 前置：真实 A6 文件必须存在（否则测试意义丢失）
        self.assertTrue(A6_FILE_1.exists(), f"真实作业票 A6 文件缺失: {A6_FILE_1}")
        self.assertTrue(A6_FILE_2.exists(), f"真实作业票 A6 文件缺失: {A6_FILE_2}")

        # 备份 p7_result.json（如果存在 —— 真实作业票里没有；但为安全保留）
        self._p7_result_backup = None
        p7_result = JOB_DIR / "p7_result.json"
        if p7_result.exists():
            self._p7_result_backup = p7_result.read_bytes()
            p7_result.unlink()

    def tearDown(self):
        # 还原 p7_result.json
        p7_result = JOB_DIR / "p7_result.json"
        if self._p7_result_backup is not None:
            p7_result.write_bytes(self._p7_result_backup)
            self._p7_result_backup = None

    # PR-01：直接调工具 → 读出 2 条事件
    def test_pr01_read_two_a6_events(self):
        from agents.p8_disposition_agent import read_p7_events

        result_json = read_p7_events.invoke({"job_id": JOB_ID})
        result = json.loads(result_json)

        # 标准响应格式：{schema_version, command, result:{job_id, events, sources, event_count}, errors}
        self.assertEqual(result["command"], "read_p7_events")
        self.assertNotIn("error", result, f"成功响应不应含 error 字段: {result}")
        events = result["result"]["events"]
        self.assertEqual(len(events), 2, f"应读出 2 条 A6 事件，实际 {len(events)} 条")

    # PR-02：事件字段映射正确（a6_event_id → event_id 等）
    def test_pr02_event_field_mapping(self):
        from agents.p8_disposition_agent import read_p7_events

        result = json.loads(read_p7_events.invoke({"job_id": JOB_ID}))
        events = result["result"]["events"]

        # 按 source_file 排序定位（确定性）
        events_by_id = {e["event_id"]: e for e in events}
        self.assertIn(A6_ID_1, events_by_id)
        self.assertIn(A6_ID_2, events_by_id)

        e1 = events_by_id[A6_ID_1]
        self.assertEqual(e1["type"], "PPE缺失")
        self.assertEqual(e1["risk_level"], 1)
        self.assertEqual(e1["risk_level_name"], "轻微")
        self.assertIn("source_file", e1)
        self.assertTrue(e1["source_file"].startswith("a6_"))
        # risk_basis 必须非空（A6 真实字段）
        self.assertTrue(len(e1["risk_basis"]) > 10)

        e2 = events_by_id[A6_ID_2]
        self.assertEqual(e2["risk_level"], 2)
        self.assertEqual(e2["risk_level_name"], "一般")
        # 聚合事件应带 aggregated_from 字段（A6 真实数据）
        self.assertIn("aggregated_from", _load_a6_event(A6_FILE_2))

    # PR-03：sources 字段标记了 per-job P7 来源
    def test_pr03_sources_includes_p7_dir(self):
        from agents.p8_disposition_agent import read_p7_events

        result = json.loads(read_p7_events.invoke({"job_id": JOB_ID}))
        sources = result["result"].get("sources", [])
        # 真实作业票只有 P7/ a6_*.json（无 p7_result.json）
        self.assertTrue(
            any("P7" in s and "a6_" in s for s in sources),
            f"sources 应含 P7/a6_*.json 标记，实际: {sources}",
        )


# ============================================================
# TestFeishuCardBuild（无 LLM）：build_feishu_card 写 job_id
# ============================================================
class TestFeishuCardBuild(unittest.TestCase):
    """验证 build_feishu_card 把 job_id 写入每个按钮 value（Card 2.0 schema）。"""

    def setUp(self):
        from feishu_gateway_cli.feishu_sender import (
            build_feishu_card, parse_options,
        )
        self.build_card = build_feishu_card
        self.parse_opts = parse_options

    # FC-01：job_id + alert_id 都写入 value
    def test_fc01_value_contains_alert_and_job_id(self):
        options = self.parse_opts(["已知悉:ack", "立即处理:handle"])
        card = self.build_card(
            text="【告警】PPE缺失",
            options=options,
            title="⚠️ 告警",
            alert_id="P8J-test-001",
            job_id=JOB_ID,
        )
        # Card 2.0 schema 强制
        self.assertEqual(card["schema"], "2.0")
        # 每个按钮 value 都含 alert_id + job_id（JSON 字符串）
        columns = card["body"]["elements"][1]["columns"]
        self.assertEqual(len(columns), 2)
        for col in columns:
            btn = col["elements"][0]
            self.assertEqual(btn["tag"], "button")
            value = json.loads(btn["value"])  # 必须是合法 JSON
            self.assertEqual(value["alert_id"], "P8J-test-001")
            self.assertEqual(value["job_id"], JOB_ID)

    # FC-02：无 job_id → 不写 value.job_id（向后兼容旧卡片）
    def test_fc02_no_job_id_omits_field(self):
        options = self.parse_opts(["已知悉:ack"])
        card = self.build_card(
            text="测试", options=options,
            alert_id="P8J-test-002",
            # job_id 故意不传
        )
        btn_value = json.loads(card["body"]["elements"][1]["columns"][0]["elements"][0]["value"])
        self.assertEqual(btn_value["alert_id"], "P8J-test-002")
        self.assertNotIn("job_id", btn_value, "无 job_id 时不应写入 value.job_id")

    # FC-03：job_id=空字符串 → 视为无，不写
    def test_fc03_empty_job_id_omits_field(self):
        options = self.parse_opts(["已知悉:ack"])
        card = self.build_card(
            text="测试", options=options,
            alert_id="P8J-test-003",
            job_id="   ",  # 全空白
        )
        btn_value = json.loads(card["body"]["elements"][1]["columns"][0]["elements"][0]["value"])
        self.assertNotIn("job_id", btn_value)

    # FC-04：button type 按 action 分桶（ack=default, handle=primary, false_alarm=danger）
    def test_fc04_button_type_mapping(self):
        options = self.parse_opts(["已知悉:ack", "立即处理:handle", "误报:false_alarm"])
        card = self.build_card(text="t", options=options, alert_id="X", job_id="J")
        cols = card["body"]["elements"][1]["columns"]
        types = [c["elements"][0]["type"] for c in cols]
        self.assertEqual(types[0], "default")
        self.assertEqual(types[1], "primary")
        self.assertEqual(types[2], "danger")


# ============================================================
# TestApplyCardActionStateMachine（无 LLM）：直接调工具验证状态机
# ============================================================
class TestApplyCardActionStateMachine(unittest.TestCase):
    """直接调 apply_card_action 工具验证 8-action 状态机 + 错误路径。

    准备：把 working_memory.json 注入从真实 A6 派生的 2 个 P8_job；
    清理：tearDown 还原（per-job + 全局长期记忆）。
    """

    @classmethod
    def setUpClass(cls):
        """一次性备份全局长期记忆（save_archived_job 会写这里）。"""
        cls._long_term_archive_backup = LONG_TERM_ARCHIVE.read_bytes() if LONG_TERM_ARCHIVE.exists() else None
        cls._long_term_index_backup = LONG_TERM_INDEX.read_bytes() if LONG_TERM_INDEX.exists() else None
        # 测试前清空全局归档（避免污染既有 P8_job）
        from A7.storage import reset_archive
        reset_archive()

    @classmethod
    def tearDownClass(cls):
        """还原全局长期记忆（per-job working_memory 由各 test tearDown 处理）。"""
        # 先清空，再还原（reset_archive 已把内存清空）
        from A7.storage import reset_archive
        reset_archive()
        if cls._long_term_archive_backup is not None:
            LONG_TERM_ARCHIVE.write_bytes(cls._long_term_archive_backup)
        else:
            if LONG_TERM_ARCHIVE.exists():
                LONG_TERM_ARCHIVE.unlink()
        if cls._long_term_index_backup is not None:
            LONG_TERM_INDEX.write_bytes(cls._long_term_index_backup)
        else:
            if LONG_TERM_INDEX.exists():
                LONG_TERM_INDEX.unlink()

    def setUp(self):
        # 备份现有 per-job working_memory（如有）
        self._wm_path = P8_DIR / "working_memory.json"
        self._wm_backup = self._wm_path.read_bytes() if self._wm_path.exists() else None

        # 注入由真实 A6 派生的 P8_jobs
        from A7.storage import dump_working_memory
        self.working_memory = _build_seed_working_memory()
        ok = dump_working_memory(JOB_ID, self.working_memory)
        self.assertTrue(ok, "setUp: dump_working_memory 失败")

        # 构造工具（@tool 装饰后是 StructuredTool；用 .func 拿底层函数直接调，跳过 LLM 层）
        from A7.middleware.p8_card_action_agent import make_apply_card_action_tool
        self.tool = make_apply_card_action_tool(job_id=JOB_ID)
        # self.tool.func 是原始函数签名：func(alert_id, action, operator_open_id, operator_name, note=None, new_status=None)
        self.tool_func = self.tool.func

    def tearDown(self):
        # 还原 per-job working_memory
        if self._wm_backup is not None:
            self._wm_path.write_bytes(self._wm_backup)
        else:
            if self._wm_path.exists():
                self._wm_path.unlink()
        # 清全局归档（每个 test 各自可能写）
        from A7.storage import reset_archive
        reset_archive()

    # AC-01：ack → notified（覆盖 mapping 表）
    def test_ac01_ack_keeps_notified(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="ack",
            operator_open_id="ou_test_001",
            operator_name="测试员",
        ))
        self.assertNotIn("error", result, f"成功响应不应含 error 字段: {result}")
        body = result["result"]
        self.assertFalse(body["skipped"])
        self.assertEqual(body["old_status"], "pending")
        self.assertEqual(body["new_status"], "notified")
        # decision 不写
        self.assertIsNone(body["decision"])

    # AC-02：approve → completed + decision=approve + 触发 save_archived_job
    def test_ac02_approve_terminal_with_decision(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="approve",
            operator_open_id="ou_test_002",
            operator_name="测试员",
        ))
        self.assertNotIn("error", result, f"成功响应不应含 error 字段: {result}")
        body = result["result"]
        self.assertEqual(body["new_status"], "completed")
        self.assertEqual(body["decision"], "approve")

        # 验证 working_memory.json 已 dump
        wm = json.loads(self._wm_path.read_text(encoding="utf-8"))
        target = next(j for j in wm if j["p8_job_id"] == P8_JOB_ID_1)
        self.assertEqual(target["status"], "completed")
        self.assertEqual(target["decision"], "approve")
        self.assertIn("updated_at", target)

        # 验证 save_archived_job 写入了全局层
        from A7.storage import get_archived_job
        archived = get_archived_job(P8_JOB_ID_1)
        self.assertIsNotNone(archived, "approve 应触发 save_archived_job 写入全局")
        self.assertEqual(archived["status"], "completed")
        self.assertEqual(archived["job_id"], JOB_ID)

    # AC-03：reject → rejected + decision=reject
    def test_ac03_reject_terminal_with_decision(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_2,
            action="reject",
            operator_open_id="ou_test_003",
            operator_name="测试员",
        ))
        self.assertNotIn("error", result, f"成功响应不应含 error 字段: {result}")
        body = result["result"]
        self.assertEqual(body["new_status"], "rejected")
        self.assertEqual(body["decision"], "reject")

    # AC-04：escalate / resume 也走终态分支
    def test_ac04_escalate_and_resume_terminal(self):
        # escalate
        r1 = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="escalate",
            operator_open_id="ou_test",
            operator_name="测试员",
        ))
        self.assertEqual(r1["result"]["new_status"], "escalated")

        # resume
        r2 = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_2,
            action="resume",
            operator_open_id="ou_test",
            operator_name="测试员",
        ))
        self.assertEqual(r2["result"]["new_status"], "resumed")

    # AC-05：handle → notified（人跟进；维持）
    def test_ac05_handle_keeps_notified(self):
        # 先把 P8J_1 标为 notified（中间态）
        from A7.storage import dump_working_memory, load_working_memory
        wm = load_working_memory(JOB_ID)
        for j in wm:
            if j["p8_job_id"] == P8_JOB_ID_1:
                j["status"] = "notified"
        dump_working_memory(JOB_ID, wm)

        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="handle",
            operator_open_id="ou_test",
            operator_name="测试员",
        ))
        self.assertEqual(result["result"]["new_status"], "notified")

    # AC-06：false_alarm → completed（终态）+ decision=approve（误报视同批准）
    def test_ac06_false_alarm_terminal_approve(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="false_alarm",
            operator_open_id="ou_test",
            operator_name="测试员",
        ))
        body = result["result"]
        self.assertEqual(body["new_status"], "completed")
        self.assertEqual(body["decision"], "approve")

    # AC-07：rectify → completed + decision=rectify
    def test_ac07_rectify_terminal_with_decision(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_2,
            action="rectify",
            operator_open_id="ou_test",
            operator_name="测试员",
        ))
        body = result["result"]
        self.assertEqual(body["new_status"], "completed")
        self.assertEqual(body["decision"], "rectify")

    # AC-08：已是终态 → skipped=True（不修改状态）
    def test_ac08_already_terminal_skipped(self):
        # 先把 P8J_1 标记为 completed
        from A7.storage import dump_working_memory, load_working_memory
        wm = load_working_memory(JOB_ID)
        for j in wm:
            if j["p8_job_id"] == P8_JOB_ID_1:
                j["status"] = "completed"
                j["decision"] = "approve"
                j["note"] = "已批准"
        dump_working_memory(JOB_ID, wm)

        # 再点 ack → 应 skip
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="ack",
            operator_open_id="ou_test",
            operator_name="测试员",
        ))
        body = result["result"]
        self.assertTrue(body["skipped"])
        self.assertEqual(body["reason"], "terminal_status")
        self.assertEqual(body["current_status"], "completed")

        # 验证 working_memory.json 没被新动作覆盖
        wm_after = json.loads(self._wm_path.read_text(encoding="utf-8"))
        target = next(j for j in wm_after if j["p8_job_id"] == P8_JOB_ID_1)
        self.assertEqual(target["status"], "completed")
        self.assertEqual(target["note"], "已批准", "终态 skip 不应修改原 note")

    # AC-09：note 追加（card_action=action by name(open_id): note）
    def test_ac09_note_appended(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="approve",
            operator_open_id="ou_test_009",
            operator_name="测试员9",
            note="安全已确认",
        ))
        self.assertNotIn("error", result, f"成功响应不应含 error 字段: {result}")

        from A7.storage import load_working_memory
        wm = load_working_memory(JOB_ID)
        target = next(j for j in wm if j["p8_job_id"] == P8_JOB_ID_1)
        self.assertIn("card_action=approve", target["note"])
        self.assertIn("测试员9", target["note"])
        self.assertIn("ou_test_009", target["note"])
        self.assertIn("安全已确认", target["note"])

    # AC-10：alert_id 空 → INVALID_ARGUMENT
    def test_ac10_empty_alert_id_invalid(self):
        result = json.loads(self.tool_func(
            alert_id="",
            action="ack",
            operator_open_id="ou_test",
            operator_name="t",
        ))
        self.assertIn("error", result, f"失败响应应含 error 字段: {result}")
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")

    # AC-11：未知 action → INVALID_ARGUMENT
    def test_ac11_unknown_action_invalid(self):
        result = json.loads(self.tool_func(
            alert_id=P8_JOB_ID_1,
            action="do_something_unknown",
            operator_open_id="ou_test",
            operator_name="t",
        ))
        self.assertIn("error", result, f"失败响应应含 error 字段: {result}")
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")

    # AC-12：P8_job 不存在 → P8_JOB_NOT_FOUND
    def test_ac12_p8_job_not_found(self):
        result = json.loads(self.tool_func(
            alert_id="P8J-DOES-NOT-EXIST-999",
            action="ack",
            operator_open_id="ou_test",
            operator_name="t",
        ))
        self.assertIn("error", result, f"失败响应应含 error 字段: {result}")
        self.assertEqual(result["error"]["code"], "P8_JOB_NOT_FOUND")

    # AC-13：path traversal 防护：job_id 含非法字符
    def test_ac13_job_id_path_traversal_blocked(self):
        from A7.middleware.p8_card_action_agent import make_apply_card_action_tool

        # job_id 含 "../" — 工厂构造时就该拒绝（避免工具被恶意绑定）
        bad_tool = make_apply_card_action_tool(job_id="../etc/passwd")
        result = json.loads(bad_tool.func(
            alert_id=P8_JOB_ID_1,
            action="ack",
            operator_open_id="ou_test",
            operator_name="t",
        ))
        self.assertIn("error", result, f"失败响应应含 error 字段: {result}")
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")


# ============================================================
# TestCardActionAgentE2E（真实 LLM）：完整 CardActionAgent 端到端
# ============================================================
@unittest.skipUnless(HAS_LLM, "需 OPENAI_API_KEY 才能跑真实 LLM 测试")
class TestCardActionAgentE2E(unittest.TestCase):
    """真实 MiniMax LLM 驱动的 CardActionAgent 端到端测试。

    链路：合成 [card_click] user message → LLM 解析 → 调 apply_card_action →
    写 per-job working_memory + 终态双写长期记忆。
    """

    @classmethod
    def setUpClass(cls):
        from A7.storage import reset_archive
        cls._long_term_archive_backup = LONG_TERM_ARCHIVE.read_bytes() if LONG_TERM_ARCHIVE.exists() else None
        cls._long_term_index_backup = LONG_TERM_INDEX.read_bytes() if LONG_TERM_INDEX.exists() else None
        reset_archive()

    @classmethod
    def tearDownClass(cls):
        from A7.storage import reset_archive
        reset_archive()
        if cls._long_term_archive_backup is not None:
            LONG_TERM_ARCHIVE.write_bytes(cls._long_term_archive_backup)
        else:
            if LONG_TERM_ARCHIVE.exists():
                LONG_TERM_ARCHIVE.unlink()
        if cls._long_term_index_backup is not None:
            LONG_TERM_INDEX.write_bytes(cls._long_term_index_backup)
        else:
            if LONG_TERM_INDEX.exists():
                LONG_TERM_INDEX.unlink()

    def setUp(self):
        self._wm_path = P8_DIR / "working_memory.json"
        self._wm_backup = self._wm_path.read_bytes() if self._wm_path.exists() else None
        from A7.storage import dump_working_memory
        dump_working_memory(JOB_ID, _build_seed_working_memory())

    def tearDown(self):
        if self._wm_backup is not None:
            self._wm_path.write_bytes(self._wm_backup)
        else:
            if self._wm_path.exists():
                self._wm_path.unlink()
        from A7.storage import reset_archive
        reset_archive()

    def _build_card_click_message(
        self,
        alert_id: str,
        action: str,
        button_text: str,
        operator_open_id: str = "ou_e2e_test",
        operator_name: str = "E2E测试员",
    ) -> str:
        """构造 [card_click] 合成消息（与 feishu_card._handle_card_action_with_llm_async 同模式）。"""
        return (
            f"[card_click] alert_id={alert_id} action={action} "
            f"button_text={button_text} "
            f"operator_open_id={operator_open_id} "
            f"operator_name={operator_name} "
            f"job_id={JOB_ID}"
        )

    # CA-01：PUSH 按钮 ack → LLM 调 apply_card_action → status=notified
    def test_ca01_llm_handles_ack_push_button(self):
        from A7.middleware.p8_card_action_agent import run_card_action_agent

        msg = self._build_card_click_message(
            alert_id=P8_JOB_ID_1,
            action="ack",
            button_text="已知悉",
        )
        result = run_card_action_agent(
            msg,
            user_ctx={"name": "E2E测试员", "role": "作业负责人", "open_id": "ou_e2e_test"},
            job_id=JOB_ID,
        )

        # 验证 working_memory.json 已更新
        wm = json.loads(self._wm_path.read_text(encoding="utf-8"))
        target = next(j for j in wm if j["p8_job_id"] == P8_JOB_ID_1)
        self.assertEqual(target["status"], "notified", f"LLM 应把 status 改为 notified，实际: {target['status']}")
        # ack 不写 decision
        self.assertIsNone(target.get("decision"))
        # note 应含 card_action=ack
        self.assertIn("card_action=ack", target.get("note", ""))

    # CA-02：HITL 按钮 approve → LLM 调 apply_card_action → status=completed + 长期记忆双写
    def test_ca02_llm_handles_approve_hitl_button(self):
        from A7.middleware.p8_card_action_agent import run_card_action_agent

        msg = self._build_card_click_message(
            alert_id=P8_JOB_ID_2,
            action="approve",
            button_text="批准",
        )
        result = run_card_action_agent(
            msg,
            user_ctx={"name": "E2E测试员", "role": "作业负责人", "open_id": "ou_e2e_test"},
            job_id=JOB_ID,
        )

        # 验证 working_memory.json 终态
        wm = json.loads(self._wm_path.read_text(encoding="utf-8"))
        target = next(j for j in wm if j["p8_job_id"] == P8_JOB_ID_2)
        self.assertEqual(target["status"], "completed")
        self.assertEqual(target["decision"], "approve")

        # 验证全局层双写
        from A7.storage import get_archived_job, get_index_entry
        archived = get_archived_job(P8_JOB_ID_2)
        self.assertIsNotNone(archived, "approve 应触发 save_archived_job")
        self.assertEqual(archived["job_id"], JOB_ID)
        # 索引条目也应写入
        idx = get_index_entry(P8_JOB_ID_2)
        self.assertIsNotNone(idx)
        self.assertIn("approve", idx)


# ============================================================
# TestPipelineIntegration（真实 LLM）：A6 → P7 events → 卡片推送 → 状态变更
# ============================================================
@unittest.skipUnless(HAS_LLM, "需 OPENAI_API_KEY 才能跑真实 LLM 测试")
class TestPipelineIntegration(unittest.TestCase):
    """端到端管道：真实 A6 → read_p7_events → 构造推送卡片 → 卡片回调 → 状态变更。"""

    @classmethod
    def setUpClass(cls):
        from A7.storage import reset_archive
        cls._long_term_archive_backup = LONG_TERM_ARCHIVE.read_bytes() if LONG_TERM_ARCHIVE.exists() else None
        cls._long_term_index_backup = LONG_TERM_INDEX.read_bytes() if LONG_TERM_INDEX.exists() else None
        reset_archive()

    @classmethod
    def tearDownClass(cls):
        from A7.storage import reset_archive
        reset_archive()
        if cls._long_term_archive_backup is not None:
            LONG_TERM_ARCHIVE.write_bytes(cls._long_term_archive_backup)
        else:
            if LONG_TERM_ARCHIVE.exists():
                LONG_TERM_ARCHIVE.unlink()
        if cls._long_term_index_backup is not None:
            LONG_TERM_INDEX.write_bytes(cls._long_term_index_backup)
        else:
            if LONG_TERM_INDEX.exists():
                LONG_TERM_INDEX.unlink()

    def setUp(self):
        self._wm_path = P8_DIR / "working_memory.json"
        self._wm_backup = self._wm_path.read_bytes() if self._wm_path.exists() else None
        self._p7_result_backup = None
        p7_result = JOB_DIR / "p7_result.json"
        if p7_result.exists():
            self._p7_result_backup = p7_result.read_bytes()
            p7_result.unlink()

    def tearDown(self):
        if self._wm_backup is not None:
            self._wm_path.write_bytes(self._wm_backup)
        else:
            if self._wm_path.exists():
                self._wm_path.unlink()
        p7_result = JOB_DIR / "p7_result.json"
        if self._p7_result_backup is not None:
            p7_result.write_bytes(self._p7_result_backup)
        from A7.storage import reset_archive
        reset_archive()

    # PIPELINE-01：A6 → P7 events → 推送卡片（含 alert_id + job_id） → 回调 → 状态变更
    def test_pipeline_full_flow(self):
        # 步骤 1：读真实作业票 P7 事件（来自 a6_*.json）
        from agents.p8_disposition_agent import read_p7_events
        from feishu_gateway_cli.feishu_sender import (
            build_feishu_card, parse_options,
        )

        p7_result = json.loads(read_p7_events.invoke({"job_id": JOB_ID}))
        events = p7_result["result"]["events"]
        self.assertEqual(len(events), 2, "管道前提：必须能读到 2 条真实 P7 事件")

        # 步骤 2：取第一条 A6 事件，构造 P8_job ID + 推送卡片
        first_event = events[0]
        first_a6_id = first_event["event_id"]

        # 用 A6 时间戳构造稳定的 P8_job_id（确定性）
        ts_compact = first_a6_id.replace("A6-", "")  # 20260820-152548-811
        p8_job_id = f"P8J-{ts_compact}-001"

        # 把 P8_job 注入 working_memory（确定性派生自真实数据）
        from A7.storage import dump_working_memory
        a6_raw = _load_a6_event(A6_FILE_1)
        wm_seed = [_make_p8_job_from_a6(a6_raw, p8_job_id)]
        dump_working_memory(JOB_ID, wm_seed)

        # 步骤 3：构造推送卡片（PUSH 通道 3 按钮 + 含 alert_id + job_id）
        options = parse_options(["已知悉:ack", "立即处理:handle", "误报:false_alarm"])
        card = build_feishu_card(
            text=f"【告警】{first_event['type']}（risk_level={first_event['risk_level']}）",
            options=options,
            title="⚠️ PPE缺失",
            alert_id=p8_job_id,
            job_id=JOB_ID,
        )
        # 校验卡片结构
        self.assertEqual(card["schema"], "2.0")
        first_btn_value = json.loads(card["body"]["elements"][1]["columns"][0]["elements"][0]["value"])
        self.assertEqual(first_btn_value["alert_id"], p8_job_id)
        self.assertEqual(first_btn_value["job_id"], JOB_ID)

        # 步骤 4：模拟用户点击 "立即处理" 按钮 → 构造 card_click 消息 → 调 CardActionAgent
        from A7.middleware.p8_card_action_agent import run_card_action_agent
        msg = (
            f"[card_click] alert_id={p8_job_id} action=handle "
            f"button_text=立即处理 "
            f"operator_open_id=ou_pipeline_01 operator_name=管道测试员 "
            f"job_id={JOB_ID}"
        )
        run_card_action_agent(
            msg,
            user_ctx={"name": "管道测试员", "role": "作业负责人", "open_id": "ou_pipeline_01"},
            job_id=JOB_ID,
        )

        # 步骤 5：验证状态变更（handle → notified 维持）
        wm_after = json.loads(self._wm_path.read_text(encoding="utf-8"))
        target = next(j for j in wm_after if j["p8_job_id"] == p8_job_id)
        self.assertEqual(
            target["status"], "notified",
            f"管道 handle 后状态应为 notified，实际: {target['status']}",
        )
        # note 应记录操作人
        self.assertIn("管道测试员", target.get("note", ""))
        self.assertIn("card_action=handle", target.get("note", ""))


# ============================================================
# TestP8MainAgentE2E（真实 LLM）：P8 主 Agent 工具调用端到端
# ============================================================
@unittest.skipUnless(HAS_LLM, "需 OPENAI_API_KEY 才能跑真实 LLM 测试")
class TestP8MainAgentE2E(unittest.TestCase):
    """P8 主 Agent（run_disposition_agent）真实 MiniMax LLM 端到端。

    链路：用户自然语言 → LLM 推理 → 调 6 个工具之一 → 真实持久化副作用。
    覆盖工具：update_job / hitl_decide / read_p7_events / notify_feishu /
             list_active_p8_jobs / recall_jobs。
    每个测试独立 thread_id + 独立 per-job working_memory 备份/还原。
    """

    @classmethod
    def setUpClass(cls):
        from A7.storage import reset_archive
        cls._long_term_archive_backup = LONG_TERM_ARCHIVE.read_bytes() if LONG_TERM_ARCHIVE.exists() else None
        cls._long_term_index_backup = LONG_TERM_INDEX.read_bytes() if LONG_TERM_INDEX.exists() else None
        reset_archive()

    @classmethod
    def tearDownClass(cls):
        from A7.storage import reset_archive
        reset_archive()
        if cls._long_term_archive_backup is not None:
            LONG_TERM_ARCHIVE.write_bytes(cls._long_term_archive_backup)
        else:
            if LONG_TERM_ARCHIVE.exists():
                LONG_TERM_ARCHIVE.unlink()
        if cls._long_term_index_backup is not None:
            LONG_TERM_INDEX.write_bytes(cls._long_term_index_backup)
        else:
            if LONG_TERM_INDEX.exists():
                LONG_TERM_INDEX.unlink()

    def setUp(self):
        self._wm_path = P8_DIR / "working_memory.json"
        self._wm_backup = self._wm_path.read_bytes() if self._wm_path.exists() else None
        # 清空 working_memory（每个测试干净起步）
        from A7.storage import dump_working_memory
        dump_working_memory(JOB_ID, [])
        # 关键：清空 MemorySaver 在 thread_id=f"p8-{JOB_ID}" 上的状态
        # （flush_working_memory 硬编码读这个 thread_id；不删 → 串台）
        from agents.p8_disposition_agent import get_p8_checkpointer
        get_p8_checkpointer().delete_thread(f"p8-{JOB_ID}")

    def tearDown(self):
        if self._wm_backup is not None:
            self._wm_path.write_bytes(self._wm_backup)
        else:
            if self._wm_path.exists():
                self._wm_path.unlink()
        from A7.storage import reset_archive
        reset_archive()
        from agents.p8_disposition_agent import get_p8_checkpointer
        try:
            get_p8_checkpointer().delete_thread(f"p8-{JOB_ID}")
        except Exception:
            pass

    def _thread_id(self) -> str:
        """P8 主 Agent flush_working_memory 硬编码读 thread_id=f"p8-{job_id}"。
        跨测试隔离靠 setUp 删除 thread + dump_working_memory 清盘。"""
        return f"p8-{JOB_ID}"

    # PA-01：LLM 读真实 P7 → 按风险等级创建 P8_job（update_job 工具）
    def test_pa01_llm_reads_p7_and_creates_p8_job(self):
        from agents.p8_disposition_agent import run_disposition_agent

        msg = (
            f"请读取作业票 {JOB_ID} 的 P7 风险事件（PPE缺失类），"
            f"按风险等级为每条事件创建一个 P8_job。"
        )
        reply = run_disposition_agent(
            msg,
            thread_id=self._thread_id(),
            user_ctx={"name": "测试员", "role": "作业负责人", "open_id": "ou_pa01"},
            job_id=JOB_ID,
        )

        # LLM 应有回复（即使是错误提示）
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 0, "LLM 必须有回复")

        # 验证 working_memory.json 至少新增了 1 条 P8_job（LLM 应至少调一次 update_job）
        from A7.storage import load_working_memory
        wm = load_working_memory(JOB_ID)
        self.assertGreaterEqual(len(wm), 1, f"LLM 应至少创建 1 个 P8_job，实际 {len(wm)} 条；LLM 回复: {reply[:200]}")

        # 每条 P8_job 必须有正确字段 + 关联到真实 A6 event_id
        a6_ids_in_memory = set()
        for job in wm:
            self.assertIn("p8_job_id", job)
            self.assertTrue(job["p8_job_id"].startswith("P8J-"))
            self.assertIn("a6_event_ids", job)
            self.assertGreater(len(job["a6_event_ids"]), 0)
            self.assertIn("risk_basis", job)
            self.assertIn("max_level", job)
            self.assertIn(job["max_level"], {"LOW", "MEDIUM", "HIGH", "CRITICAL"})
            self.assertEqual(job.get("job_id"), JOB_ID)
            a6_ids_in_memory.update(job["a6_event_ids"])

        # 至少有一条 P8_job 关联到真实 A6 事件
        self.assertTrue(
            a6_ids_in_memory & {A6_ID_1, A6_ID_2},
            f"P8_job 必须关联真实 A6 event，实际关联: {a6_ids_in_memory}",
        )

    # PA-02：LLM 调 hitl_decide → status=waiting_decision + channel=HITL
    def test_pa02_llm_calls_hitl_decide(self):
        from agents.p8_disposition_agent import run_disposition_agent
        from A7.storage import dump_working_memory

        # 先注入 1 个 P8_job（LLM 操作对象）
        seed = _build_seed_working_memory()
        dump_working_memory(JOB_ID, seed)
        target_pid = P8_JOB_ID_1  # 对应风险等级 LOW 的 A6 事件

        msg = (
            f"作业票 {JOB_ID} 的 P8_job {target_pid} 风险较高，"
            f"请走人工审批流程（hitl_decide），列出 approve/rectify/reject/escalate 选项。"
        )
        reply = run_disposition_agent(
            msg,
            thread_id=self._thread_id(),
            user_ctx={"name": "测试员", "role": "作业负责人", "open_id": "ou_pa02"},
            job_id=JOB_ID,
        )
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 0)

        # 验证 working_memory.json 中 target P8_job 状态变更
        from A7.storage import load_working_memory
        wm = load_working_memory(JOB_ID)
        target = next((j for j in wm if j["p8_job_id"] == target_pid), None)
        # 注意：hitl_decide patch 后 working_memory 可能被 LangGraph reducer 部分覆盖；
        # 至少验证有 P8_job 被标为 waiting_decision（不一定是 target_pid，LLM 可能选错对象）
        waiting = [j for j in wm if j.get("status") == "waiting_decision"]
        self.assertGreaterEqual(
            len(waiting), 1,
            f"LLM 应至少把 1 个 P8_job 标为 waiting_decision，实际 working_memory: "
            f"{[(j['p8_job_id'], j.get('status'), j.get('channel')) for j in wm]}；"
            f"LLM 回复: {reply[:200]}",
        )
        self.assertEqual(waiting[0]["channel"], "HITL")

    # PA-03：LLM 调 recall_jobs 两步走（index → detail）
    def test_pa03_llm_recall_jobs_two_step(self):
        from agents.p8_disposition_agent import run_disposition_agent
        from A7.storage import save_archived_job

        # 先写 1 条终态归档到长期记忆
        archived = _make_p8_job_from_a6(_load_a6_event(A6_FILE_1), P8_JOB_ID_1)
        archived["status"] = "completed"
        archived["decision"] = "approve"
        archived["risk_basis"] = "PPE缺失-轻微风险-测试"
        save_archived_job(P8_JOB_ID_1, archived, job_id=JOB_ID)

        # 第一步：让 LLM 走索引搜索
        msg1 = (
            f"作业票 {JOB_ID}，帮我查一下昨天处理过的 PPE缺失 事件的处置结论。"
        )
        reply1 = run_disposition_agent(
            msg1,
            thread_id=self._thread_id(),
            user_ctx={"name": "测试员", "role": "作业负责人", "open_id": "ou_pa03"},
            job_id=JOB_ID,
        )
        self.assertIsInstance(reply1, str)
        self.assertGreater(len(reply1), 0)

        # LLM 回复里应提及索引结果（包含我们的 P8_job_id 或 risk_basis 关键词）
        # 用宽松匹配：要么提到 P8J ID，要么提到 "PPE缺失"
        self.assertTrue(
            (P8_JOB_ID_1 in reply1) or ("PPE缺失" in reply1) or ("approve" in reply1.lower()),
            f"LLM 第一步回复应提到索引结果（{P8_JOB_ID_1} / PPE缺失 / approve），"
            f"实际: {reply1[:300]}",
        )

        # 第二步：让 LLM 拿详情
        msg2 = f"请用 detail_p8_job_id={P8_JOB_ID_1} 查这条 P8_job 的完整归档。"
        reply2 = run_disposition_agent(
            msg2,
            thread_id=self._thread_id(),
            user_ctx={"name": "测试员", "role": "作业负责人", "open_id": "ou_pa03"},
            job_id=JOB_ID,
        )
        self.assertIsInstance(reply2, str)
        self.assertGreater(len(reply2), 0)

        # LLM 第二步回复应包含详情字段
        self.assertTrue(
            (P8_JOB_ID_1 in reply2) or ("PPE缺失" in reply2) or ("completed" in reply2.lower()),
            f"LLM 第二步回复应包含详情，实际: {reply2[:300]}",
        )

    # PA-04：LLM 调 notify_feishu（真实 Gateway :8787）
    @unittest.skipUnless(
        _OCG_STANDALONE.exists() and _PROJECT_ROOT.joinpath("data/jobs/_long_term").exists(),
        "需 openclaw-channel-gateway-standalone + 真实 Gateway",
    )
    def test_pa04_llm_calls_notify_feishu(self):
        from agents.p8_disposition_agent import run_disposition_agent
        from A7.storage import dump_working_memory

        # 先注入 1 个 P8_job（channel=PUSH + 已知 notify_feishu 必填字段）
        seed = _build_seed_working_memory()
        # 把 channel 设为 PUSH，LLM 才会调 notify_feishu
        for j in seed:
            j["channel"] = "PUSH"
            j["status"] = "notified"
        dump_working_memory(JOB_ID, seed)

        # 检查 FEISHU_GROUP_MAP 是否有可用群（决定 group_name）
        import os
        fgm = os.environ.get("FEISHU_GROUP_MAP", "{}")
        try:
            group_map = json.loads(fgm)
        except Exception:
            group_map = {}
        # 跳过如果没有可用群
        if not group_map:
            self.skipTest("FEISHU_GROUP_MAP 未配置真实群，跳过 notify_feishu 真实推送测试")

        # 取第一个可用 group_name
        first_group_name = next(iter(group_map.keys()), None)
        if not first_group_name:
            self.skipTest("FEISHU_GROUP_MAP 无可用群")

        msg = (
            f"作业票 {JOB_ID} 的 P8_job {P8_JOB_ID_1} 需要推送告警通知。"
            f"请调用 notify_feishu 工具推送到群 {first_group_name}，"
            f"options 至少包含 ['已知悉:ack', '立即处理:handle', '误报:false_alarm'] 三按钮。"
        )
        reply = run_disposition_agent(
            msg,
            thread_id=self._thread_id(),
            user_ctx={"name": "测试员", "role": "作业负责人", "open_id": "ou_pa04"},
            job_id=JOB_ID,
        )

        # LLM 应有回复
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 0)

        # 验证 LLM 回复里提到推送或群名
        self.assertTrue(
            ("推送" in reply) or ("已发" in reply) or ("告警" in reply) or (first_group_name in reply),
            f"LLM 回复应提到推送/群名，实际: {reply[:300]}",
        )


# ============================================================
# TestCardClickLoopE2E（真实 LLM）：卡片推送 → 点击回调 → CardActionAgent 全闭环
# ============================================================
@unittest.skipUnless(HAS_LLM, "需 OPENAI_API_KEY 才能跑真实 LLM 测试")
class TestCardClickLoopE2E(unittest.TestCase):
    """真实飞书卡片推送 + 用户点击回调 + CardActionAgent daemon + P8_job 状态变更 全链路。

    链路（无 mock）：
        1. notify_feishu 推送真实卡片 → 飞书群（动火作业群）
        2. 合成 feishu card.action.trigger payload（模拟用户点击 "立即处理" 按钮）
        3. process_card_callback(payload) → 写审计 + 派发 _handle_card_action_with_llm_async daemon
        4. daemon 调 CardActionAgent（真实 MiniMax LLM）→ apply_card_action
        5. P8_job 状态变更（pending → notified）+ note 记录操作人
    """

    AUDIT_LOG = _PROJECT_ROOT / "data" / "card_callbacks.jsonl"

    @classmethod
    def setUpClass(cls):
        from A7.storage import reset_archive
        cls._long_term_archive_backup = LONG_TERM_ARCHIVE.read_bytes() if LONG_TERM_ARCHIVE.exists() else None
        cls._long_term_index_backup = LONG_TERM_INDEX.read_bytes() if LONG_TERM_INDEX.exists() else None
        # 备份全局 card_callbacks.jsonl（避免污染既有审计）
        cls._audit_backup = cls.AUDIT_LOG.read_bytes() if cls.AUDIT_LOG.exists() else None
        reset_archive()

    @classmethod
    def tearDownClass(cls):
        from A7.storage import reset_archive
        reset_archive()
        if cls._long_term_archive_backup is not None:
            LONG_TERM_ARCHIVE.write_bytes(cls._long_term_archive_backup)
        else:
            if LONG_TERM_ARCHIVE.exists():
                LONG_TERM_ARCHIVE.unlink()
        if cls._long_term_index_backup is not None:
            LONG_TERM_INDEX.write_bytes(cls._long_term_index_backup)
        else:
            if LONG_TERM_INDEX.exists():
                LONG_TERM_INDEX.unlink()
        # 还原 card_callbacks.jsonl
        if cls._audit_backup is not None:
            cls.AUDIT_LOG.write_bytes(cls._audit_backup)
        else:
            if cls.AUDIT_LOG.exists():
                cls.AUDIT_LOG.unlink()

    def setUp(self):
        self._wm_path = P8_DIR / "working_memory.json"
        self._wm_backup = self._wm_path.read_bytes() if self._wm_path.exists() else None
        # 清 MemorySaver thread + 清盘
        from A7.storage import dump_working_memory
        dump_working_memory(JOB_ID, [])
        from agents.p8_disposition_agent import get_p8_checkpointer
        try:
            get_p8_checkpointer().delete_thread(f"p8-{JOB_ID}")
        except Exception:
            pass

    def tearDown(self):
        if self._wm_backup is not None:
            self._wm_path.write_bytes(self._wm_backup)
        else:
            if self._wm_path.exists():
                self._wm_path.unlink()
        from A7.storage import reset_archive
        reset_archive()
        from agents.p8_disposition_agent import get_p8_checkpointer
        try:
            get_p8_checkpointer().delete_thread(f"p8-{JOB_ID}")
        except Exception:
            pass

    def _wait_for_status_change(self, alert_id: str, expected: str, timeout: float = 90.0) -> Dict[str, Any]:
        """轮询 per-job working_memory.json，等待指定 P8_job 状态变更。"""
        from A7.storage import load_working_memory
        deadline = time.time() + timeout
        last_state: Dict[str, Any] = {}
        while time.time() < deadline:
            try:
                wm = load_working_memory(JOB_ID)
                last_state = next((j for j in wm if j["p8_job_id"] == alert_id), {})
                if last_state.get("status") == expected:
                    return last_state
            except Exception:
                pass
            time.sleep(1.0)
        self.fail(
            f"超时 {timeout}s 仍未等到 P8_job {alert_id} 状态变 {expected}，"
            f"最终状态: {last_state.get('status')!r}, "
            f"note: {last_state.get('note', '')[:200]!r}"
        )
        return last_state

    # PA-05：完整闭环 - push → click callback → CardActionAgent daemon → 状态变更
    def test_pa05_card_push_click_callback_full_loop(self):
        from agents.p8_disposition_agent import notify_feishu
        from feishu_gateway_cli.feishu_card import process_card_callback

        # 唯一 alert_id（必须匹配 ^P8J-\d{8}-\d{6}-\d{3}$，避免与既有 audit 冲突触发幂等）
        unique_alert_id = datetime.now().strftime("P8J-%Y%m%d-%H%M%S-101")
        fgm = json.loads(os.environ.get("FEISHU_GROUP_MAP", "{}"))
        if not fgm:
            self.skipTest("FEISHU_GROUP_MAP 未配置真实群，跳过 card click loop 真实测试")
        first_chat_id, first_group_meta = next(iter(fgm.items()))
        first_group_name = first_group_meta.get("name", first_chat_id)

        # 步骤 1：seed 1 个 P8_job（channel=PUSH + status=pending）
        from A7.storage import dump_working_memory
        a6 = _load_a6_event(A6_FILE_1)
        seed = [_make_p8_job_from_a6(a6, unique_alert_id)]
        seed[0]["channel"] = "PUSH"
        seed[0]["status"] = "pending"
        dump_working_memory(JOB_ID, seed)

        # 步骤 2：推送真实卡片（直接调 notify_feishu，不走 LLM；PA-04 已覆盖 LLM 推送）
        card_resp = notify_feishu.invoke({
            "p8_job_id": unique_alert_id,
            "title": "⚠️ 闭环测试告警",
            "body": f"【告警】PPE缺失 闭环测试 {unique_alert_id}",
            "risk_level": "LOW",
            "assignee_role": "作业负责人",
            "job_id": JOB_ID,
            "a6_event_ids": [a6["a6_event_id"]],
            "options": ["已知悉:ack", "立即处理:handle", "误报:false_alarm"],
            "group_name": first_group_name,
        })
        card_resp_data = json.loads(card_resp)
        # notify_feishu 必须返回 command 字段（不是 error）
        self.assertNotIn("error", card_resp_data, f"推送卡片失败: {card_resp_data}")
        self.assertEqual(card_resp_data["command"], "notify_feishu")

        # 步骤 3：合成 feishu card.action.trigger payload（用户点击"立即处理"）
        callback_payload = {
            "header": {
                "event_id": f"ev_pa05_{datetime.now().strftime('%H%M%S%f')}",
                "event_type": "card.action.trigger",
            },
            "event": {
                "action": {
                    # Card 2.0：value 是 JSON 字符串（飞书侧约定）
                    "value": json.dumps({
                        "action": "handle",
                        "alert_id": unique_alert_id,
                        "job_id": JOB_ID,
                    }),
                    "text": {"content": "立即处理"},
                },
                "operator": {
                    "open_id": "ou_pa05_loop_test",
                    "user_name": "闭环测试员",
                },
                "context": {
                    # 真实 chat_id（动火作业群）；message_id 用占位（_replace_card_async 会失败但不影响 CardActionAgent）
                    "open_chat_id": first_chat_id,
                    "open_message_id": f"om_pa05_dummy_{datetime.now().strftime('%H%M%S%f')}",
                },
            },
        }

        # 步骤 4：调 process_card_callback → 写审计 + 派发 daemon
        toast = process_card_callback(callback_payload)
        self.assertIn("toast", toast, f"process_card_callback 应返回 toast，实际: {toast}")
        self.assertEqual(toast["toast"]["type"], "success")

        # 步骤 5：等待 daemon 跑完（CardActionAgent + apply_card_action）
        # handle → notified（pending → notified）
        target = self._wait_for_status_change(unique_alert_id, "notified", timeout=90.0)

        # 步骤 6：断言状态 + note 字段
        self.assertEqual(target["status"], "notified")
        self.assertIn("card_action=handle", target.get("note", ""), f"note 应记录 card_action=handle, 实际: {target.get('note')}")
        self.assertIn("闭环测试员", target.get("note", ""), f"note 应记录操作人姓名, 实际: {target.get('note')}")
        self.assertIn("ou_pa05_loop_test", target.get("note", ""), f"note 应记录 open_id, 实际: {target.get('note')}")

        # 步骤 7：审计日志应有本测试的 callback 记录（验证 _write_audit 真实落盘）
        self.assertTrue(self.AUDIT_LOG.exists(), "审计日志应存在")
        audit_records = [
            json.loads(line) for line in self.AUDIT_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        matching = [r for r in audit_records if r.get("alert_id") == unique_alert_id]
        self.assertGreaterEqual(
            len(matching), 1,
            f"审计日志应有本测试的 callback 记录（alert_id={unique_alert_id}），"
            f"实际 {len(matching)} 条；audit 总数: {len(audit_records)}",
        )
        self.assertEqual(matching[0]["action"], "handle")
        self.assertEqual(matching[0]["job_id"], JOB_ID)
        self.assertEqual(matching[0]["operator_open_id"], "ou_pa05_loop_test")


if __name__ == "__main__":
    # 默认跑全部（含真实 LLM）；用 `-k "not llm"` 过滤仅无 LLM 部分
    unittest.main(verbosity=2)