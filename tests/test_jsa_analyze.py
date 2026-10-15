"""JSA 分析链路测试：章节规则、上下文构造、严格解析、失败降级，以及「不再有假数据」。"""
import json
import unittest
from unittest.mock import MagicMock, patch

from agents.workflow import get_permit_type
from agents.workflow.jsa_analyze import (
    H2S_SECTION_TITLE,
    JSA_SOURCE_AI,
    JSA_SOURCE_UNAVAILABLE,
    RISK_LEVELS,
    build_jsa_context,
    generate_jsa,
    parse_jsa_payload,
    special_section_titles,
)

HOT_WORK, CONFIG = get_permit_type("hot_work")
LINE_OPEN, _ = get_permit_type("line_open")
HIGH_WORK, _ = get_permit_type("high_work")
LIFTING, _ = get_permit_type("lifting")


def _payload(**overrides):
    payload = {
        "job_name": "含硫化氢天然气管道盲板倒换作业",
        "risk_summary": "本作业涉及大口径高压含硫天然气管线开口，风险集中在隔离失效与中毒。",
        "pre_checks": ["完成工艺隔离并挂牌锁定", "确认泄压放空完成", "气体检测合格"],
        "steps": [
            {"step": "现场准备及隔离", "hazard": "H₂S 泄漏", "consequence": "人员中毒",
             "level": "高", "control": "设置警戒区并配置检测与监护人员"},
            {"step": "搭设作业平台", "hazard": "人员坠落", "consequence": "人员伤害",
             "level": "高", "control": "验收平台，双挂安全带"},
            {"step": "拆除法兰螺栓", "hazard": "残余压力释放", "consequence": "喷射伤害",
             "level": "高", "control": "确认零压力后作业，人员站上风向"},
            {"step": "抽换盲板", "hazard": "盲板坠落", "consequence": "挤压砸伤",
             "level": "中", "control": "采用吊装辅助，禁止人工强行搬运"},
        ],
        "special_sections": [
            {"title": H2S_SECTION_TITLE, "items": ["严禁依靠气味判断", "配置便携式检测仪"]},
        ],
        "emergency": "泄漏时立即停止作业并撤离至上风向。",
        "final_checklist": ["施工方案审批完成", "气体检测合格", "监护人到位"],
        "risk_options": [],
        "main_risks": ["H₂S中毒", "高压天然气泄漏", "火灾爆炸"],
    }
    payload.update(overrides)
    return payload


class SectionRuleTests(unittest.TestCase):
    """专项章节由规则决定，不让模型自由增减。"""

    def test_h2s_section_is_triggered_by_medium(self):
        titles = special_section_titles({"medium": "含硫化氢天然气"}, LINE_OPEN, [])
        self.assertIn(H2S_SECTION_TITLE, titles)

    def test_h2s_section_triggers_on_job_content_too(self):
        titles = special_section_titles({"work_content": "处理含硫污水管线"}, LINE_OPEN, [])
        self.assertIn(H2S_SECTION_TITLE, titles)

    def test_plain_medium_does_not_trigger_h2s_section(self):
        titles = special_section_titles({"medium": "压缩空气"}, LINE_OPEN, [])
        self.assertNotIn(H2S_SECTION_TITLE, titles)

    def test_related_type_adds_its_section(self):
        titles = special_section_titles({}, LINE_OPEN, ["high_work", "lifting"])
        self.assertEqual(titles, ["管线打开（盲板抽堵）专项控制措施", "高处作业及吊装控制措施"])
        self.assertEqual(
            titles.count("高处作业及吊装控制措施"), 1, "高处与吊装应合成一节、不能重复"
        )

    def test_own_type_section_included(self):
        titles = special_section_titles({}, HOT_WORK, [])
        self.assertEqual(titles, ["动火作业专项控制措施"])


class ContextTests(unittest.TestCase):
    def test_context_carries_job_content_measures_and_sections(self):
        values = {"work_content": "抽换 DN600 盲板", "medium": "含硫化氢天然气"}
        lines = build_jsa_context(values, LINE_OPEN, [H2S_SECTION_TITLE], ["高处作业"])
        joined = "\n".join(lines)
        self.assertIn("作业内容：抽换 DN600 盲板", joined)
        self.assertIn("作业类型：管线打开（盲板抽堵）", joined)
        self.assertIn("介质：含硫化氢天然气", joined)
        self.assertIn("本作业还涉及的其他作业类型：高处作业", joined)
        self.assertIn("安全措施清单", joined)
        self.assertIn(H2S_SECTION_TITLE, joined)


class ParseTests(unittest.TestCase):
    def test_accepts_valid_payload(self):
        parsed = parse_jsa_payload(json.dumps(_payload(), ensure_ascii=False), [H2S_SECTION_TITLE])
        self.assertEqual(parsed["job_name"], "含硫化氢天然气管道盲板倒换作业")
        self.assertEqual(len(parsed["steps"]), 4)
        self.assertEqual(parsed["special_sections"][0]["title"], H2S_SECTION_TITLE)

    def test_accepts_payload_wrapped_in_code_fence(self):
        text = "```json\n" + json.dumps(_payload(), ensure_ascii=False) + "\n```"
        parsed = parse_jsa_payload(text, [H2S_SECTION_TITLE])
        self.assertEqual(len(parsed["steps"]), 4)

    def test_rejects_risk_level_outside_the_three(self):
        payload = _payload()
        payload["steps"][0]["level"] = "极高"
        with self.assertRaisesRegex(ValueError, "level"):
            parse_jsa_payload(json.dumps(payload, ensure_ascii=False))

    def test_rejects_step_missing_fields(self):
        payload = _payload()
        del payload["steps"][0]["control"]
        with self.assertRaisesRegex(ValueError, "control"):
            parse_jsa_payload(json.dumps(payload, ensure_ascii=False))

    def test_rejects_too_few_steps(self):
        payload = _payload(steps=_payload()["steps"][:2])
        with self.assertRaisesRegex(ValueError, "至少 4 条"):
            parse_jsa_payload(json.dumps(payload, ensure_ascii=False))

    def test_rejects_empty_list_field(self):
        payload = _payload(main_risks=[])
        with self.assertRaisesRegex(ValueError, "main_risks"):
            parse_jsa_payload(json.dumps(payload, ensure_ascii=False))

    def test_rejects_section_titles_that_do_not_match(self):
        payload = _payload()
        payload["special_sections"] = [{"title": "自创章节", "items": ["x"]}]
        with self.assertRaisesRegex(ValueError, "专项章节"):
            parse_jsa_payload(json.dumps(payload, ensure_ascii=False), [H2S_SECTION_TITLE])

    def test_rejects_non_json(self):
        with self.assertRaisesRegex(ValueError, "JSON"):
            parse_jsa_payload("模型今天不想输出 JSON")

    def test_risk_levels_are_exactly_three(self):
        self.assertEqual(RISK_LEVELS, ("高", "中", "低"))


class GenerateTests(unittest.TestCase):
    def _model(self, content=None, error=None):
        model = MagicMock()
        if error is not None:
            model.invoke.side_effect = error
        else:
            model.invoke.return_value = MagicMock(content=content)
        return model

    def test_degrades_when_model_fails(self):
        """模型不可用时留空并给出明确原因——绝不返回编造的分析。"""
        model = self._model(error=RuntimeError("model down"))
        with patch("agents.model.chat_model.create_chat_model_with_logging", return_value=model):
            result = generate_jsa("J", "动火作业", ["【作业基本信息】"], [], [])
        self.assertEqual(result["source"], JSA_SOURCE_UNAVAILABLE)
        self.assertNotIn("hazards", result)
        self.assertIn("model down", result["error"])

    def test_degrades_when_model_returns_bad_payload(self):
        model = self._model(content="{'job_name': 'x'}")
        with patch("agents.model.chat_model.create_chat_model_with_logging", return_value=model):
            result = generate_jsa("J", "动火作业", ["【作业基本信息】"], [], [])
        self.assertEqual(result["source"], JSA_SOURCE_UNAVAILABLE)

    def test_returns_payload_on_success(self):
        model = self._model(content=json.dumps(_payload(), ensure_ascii=False))
        with patch("agents.model.chat_model.create_chat_model_with_logging", return_value=model):
            result = generate_jsa("J", "管线打开（盲板抽堵）",
                                  ["【作业基本信息】"], [H2S_SECTION_TITLE], [])
        self.assertEqual(result["source"], JSA_SOURCE_AI)
        self.assertEqual(result["type_name"], "管线打开（盲板抽堵）")
        self.assertEqual(len(result["steps"]), 4)


class P1JsaResultTests(unittest.TestCase):
    """P1 侧把 steps 映射成 hazards（兼容前端既有展示），失败则不带 hazards。"""

    def test_steps_are_mapped_to_hazards(self):
        import agents.p1_permit_agent as p1

        with patch("agents.workflow.jsa_analyze.generate_jsa",
                   return_value={**_payload(), "source": JSA_SOURCE_AI}), \
                patch.object(p1, "_render_jsa_report_safely", return_value=""):
            result = p1._build_jsa_result({}, {"work_content": "抽盲板"}, LINE_OPEN, "J")
        self.assertEqual(len(result["hazards"]), 4)
        self.assertEqual(result["hazards"][0]["description"], "H₂S 泄漏")
        self.assertEqual(result["hazards"][0]["severity"], "高")

    def test_unavailable_result_has_no_hazards(self):
        import agents.p1_permit_agent as p1

        with patch("agents.workflow.jsa_analyze.generate_jsa",
                   return_value={"source": JSA_SOURCE_UNAVAILABLE, "error": "model down"}):
            result = p1._build_jsa_result({}, {"work_content": "抽盲板"}, LINE_OPEN, "J")
        self.assertNotIn("hazards", result)


class NoFakeDataTests(unittest.TestCase):
    """防回归：曾经冒充 JSA 的两处假数据必须保持消失。"""

    def test_jsa_profiles_is_gone(self):
        import agents.p1_permit_agent as p1

        self.assertFalse(hasattr(p1, "JSA_PROFILES"), "写死的 JSA_PROFILES 又回来了")

    def test_permit_content_fallback_has_no_hazards(self):
        import agents.p1_permit_agent as p1

        permit, _ = p1._build_permit_content({"job_content": "动火焊接", "region": "催化装置"})
        self.assertEqual(permit["hazards"], [])
        self.assertEqual(permit["measures"], [])


if __name__ == "__main__":
    unittest.main()
