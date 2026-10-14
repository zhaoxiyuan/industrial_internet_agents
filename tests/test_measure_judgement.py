"""P1 安全措施判定层（知识库接入）测试。

覆盖：
- 手动填报路径有知识库时**全部措施送模型**，并逐条附条文依据
- 措施与知识库对不齐（上传 DOCX 的自带清单）时退回无依据判定
- 判定失败仍「一个勾都不打」
- 对齐判定对排版差异（空格/括号）宽容、对内容不同严格
- A1：知识库文件变更后缓存自动失效（长跑服务不必重启）
- S1：条文摘录不得丢掉关键子句（原文放得下就逐字给原文）
"""
import json
import os
import re
import tempfile
import time
import unittest
from unittest.mock import patch

import agents.p1_permit_agent as p1
from agents.workflow import list_safety_measures, decision_map
from agents.workflow import article_knowledge as ak
from agents.workflow.article_knowledge import (
    align_measures,
    basis_texts,
    load_knowledge,
)


class FakeResponse:
    def __init__(self, content):
        self.content = content


class RecordingModel:
    """按提示里出现的条目数回一个合规 JSON，并记录提示词。"""

    def __init__(self):
        self.calls = []
        self.answer = None

    def invoke(self, messages):
        prompt = messages[-1].content
        self.calls.append(prompt)
        count = len(re.findall(r"^\d+\. ", prompt, re.M))
        if self.answer is None:
            payload = {"items": [{"index": i, "needed": i % 2 == 1} for i in range(1, count + 1)]}
        else:
            payload = self.answer
        return FakeResponse(json.dumps(payload))


class KnowledgeAlignmentTests(unittest.TestCase):
    def test_template_measures_align(self):
        """模板措施与知识库附录 1 逐条一致 → 必须对齐（含射线那条仅差一个括号的）。"""
        for key in ("hot_work", "confined_space", "line_open", "high_work", "lifting",
                    "earth_work", "road_close", "radiation", "non_routine", "temp_power"):
            with self.subTest(key=key):
                self.assertIsNotNone(align_measures(key, list_safety_measures(key)))

    def test_temp_power_grid_is_parsed_and_aligned(self):
        """临时用电 ○ 网格接入后：解析器取到 23 条、与知识库逐条对齐（2026-10-12）。"""
        measures = list_safety_measures("temp_power")
        self.assertEqual(len(measures), 23)
        aligned = align_measures("temp_power", measures)
        self.assertIsNotNone(aligned, "○ 网格应与知识库同类型措施表对齐")
        # 逐条同一项：快照把 docx 的勾选框渲染成「(」，文本按归一化比（与对齐同一口径）
        from agents.workflow.article_knowledge import normalize_text
        self.assertEqual(
            [normalize_text(m["text"]) for m in measures],
            [normalize_text(i["text"]) for i in aligned],
        )

    def test_foreign_measures_unaligned(self):
        """上传 DOCX 自带的清单内容不同 → 对不齐，不得硬套依据。"""
        foreign = [{"index": i + 1, "text": f"上传票里的第{i + 1}条自定义措施内容"}
                   for i in range(15)]
        self.assertIsNone(align_measures("hot_work", foreign))

    def test_basis_texts(self):
        got = basis_texts("hot_work", 7)
        self.assertEqual([b["article_no"] for b in got], [67])
        self.assertIn("乙炔", got[0]["text"])
        # K2 补齐后动火#1 也挂上了依据；仍无依据的票面条目（办法里没有对应条文）返回空表
        self.assertEqual([b["article_no"] for b in basis_texts("hot_work", 1)], [58])
        self.assertEqual(basis_texts("lifting", 15), [])


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.model = RecordingModel()
        self.patchers = [
            patch.object(p1, "create_chat_model_with_logging", return_value=self.model),
            patch.object(p1, "push_websocket_log"),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_manual_path_uses_knowledge_and_grounding(self):
        measures = list_safety_measures("hot_work")
        decisions = p1._decide_safety_measures("JOB", "动火作业", ["作业内容：更换阀门"], measures)

        self.assertEqual(decisions["source"], "ai")
        self.assertEqual(decisions["mode"], "knowledge")
        # 序号必须覆盖全部措施，供 decision_map / 渲染端直接消费
        self.assertEqual([i["index"] for i in decisions["items"]],
                         list(range(1, len(measures) + 1)))
        self.assertEqual(len(decision_map(decisions)), len(measures))
        self.assertTrue(all(i["origin"] == "ai" for i in decisions["items"]))

        # 对齐成功 → 全部措施都送模型，且逐条附条文依据
        prompt = self.model.calls[0]
        asked = len(re.findall(r"^\d+\. ", prompt, re.M))
        self.assertEqual(asked, len(measures))
        self.assertIn("〖依据·第67条〗", prompt)

    def test_falls_back_to_full_when_unaligned(self):
        measures = [{"index": i + 1, "text": f"上传票第{i + 1}条自制措施内容"} for i in range(5)]
        decisions = p1._decide_safety_measures("JOB", "动火作业", [], measures)
        self.assertEqual(decisions["mode"], "full")
        self.assertEqual(len(decisions["items"]), 5)
        self.assertTrue(all(i["origin"] == "ai" for i in decisions["items"]))
        self.assertNotIn("〖依据", self.model.calls[0])

    def test_unknown_type_uses_full_mode(self):
        decisions = p1._decide_safety_measures(
            "JOB", "某不存在的作业", [], [{"index": 1, "text": "措施一"}, {"index": 2, "text": "措施二"}])
        self.assertEqual(decisions["mode"], "full")

    def test_failure_writes_no_marks(self):
        with patch.object(p1, "create_chat_model_with_logging", side_effect=RuntimeError("model down")):
            decisions = p1._decide_safety_measures(
                "JOB", "动火作业", [], list_safety_measures("hot_work"))
        self.assertEqual(decisions["source"], "unavailable")
        self.assertEqual(decisions["items"], [])
        self.assertEqual(decision_map(decisions), {})

    def test_bad_model_payload_writes_no_marks(self):
        self.model.answer = {"items": [{"index": 1, "needed": True}]}   # 条数不足
        decisions = p1._decide_safety_measures(
            "JOB", "动火作业", [], list_safety_measures("hot_work"))
        self.assertEqual(decisions["source"], "unavailable")
        self.assertEqual(decisions["items"], [])


class KnowledgeAvailabilityTests(unittest.TestCase):
    def test_knowledge_loads(self):
        kb = load_knowledge()
        self.assertIsNotNone(kb)
        self.assertEqual(len(kb["articles"]), 187)
        self.assertEqual(kb["stats"]["measure_items"], 144)
        self.assertEqual(kb["stats"]["measures_mapped"], 130)


class CacheInvalidationTests(unittest.TestCase):
    """A1：知识库文件重新生成后，长跑的服务必须自动改用新库，不需要重启。"""

    @staticmethod
    def _dump(path, version):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": version, "articles": [], "measures": []}, fh)

    def tearDown(self):
        ak.clear_cache()

    def test_reloads_when_file_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kb.json")
            self._dump(path, 1)
            ak.clear_cache()
            self.assertEqual(ak.load_knowledge(path)["version"], 1)
            self.assertEqual(ak.load_knowledge(path)["version"], 1)  # 命中缓存，未变
            time.sleep(0.01)
            self._dump(path, 2)
            self.assertEqual(ak.load_knowledge(path)["version"], 2)  # 文件变了 → 自动重载

    def test_missing_or_broken_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kb.json")
            ak.clear_cache()
            self.assertIsNone(ak.load_knowledge(path))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ 不是合法 JSON")
            self.assertIsNone(ak.load_knowledge(path))
            self._dump(path, 3)
            self.assertEqual(ak.load_knowledge(path)["version"], 3)  # 修好即可用


class BasisExcerptTests(unittest.TestCase):
    """S1：依据摘录不得丢掉关键子句。

    摘录只从 scenarios 里取时，会漏掉情形切分丢下的主旨句（第 117 条）；
    固定取前几片时，关键子句又可能落在窗口外（第 99 条的第 6 片）。
    """

    @classmethod
    def setUpClass(cls):
        cls.articles = {a["article_no"]: a for a in load_knowledge()["articles"]}

    def test_short_article_returns_verbatim(self):
        """原文放得下时逐字返回，主旨句不得丢。"""
        got = p1._basis_excerpt(
            self.articles[117], "涉及下述情况的吊装作业已编制作业方案，并已经审查批准。")
        self.assertIn("应当编制吊装作业方案", got)

    def test_long_article_keeps_relevant_clause(self):
        """超长条文压缩后，与本条措施最相关的子句必须出现且不超上限。"""
        got = p1._basis_excerpt(
            self.articles[99], "火灾爆炸危险场所，作业人员穿防静电服、工作鞋，作业时使用防爆灯具和防爆工具。")
        self.assertIn("防爆工具", got)
        self.assertLessEqual(len(got), 320)

    def test_confined_space_13_basis_mentions_water(self):
        """K1 联动：受限空间#13 现在挂第 87 条，摘录里应当看得到「冲洗水」。"""
        got = p1._basis_excerpt(self.articles[87], "盛有腐蚀性介质的容器作业现场已配备应急用冲洗水。")
        self.assertIn("冲洗水", got)


if __name__ == "__main__":
    unittest.main()
