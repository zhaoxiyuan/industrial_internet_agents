"""办法知识库锁定测试：结构完整性、情形质量、条款↔措施映射防漂移、生成幂等。

对应 `data/knowledge/permit_articles.json` + `tools/build_article_knowledge.py`。

设计意图（R5）：`MEASURE_REFS` 以 `(type_key, seq)` 单键索引措施，措施条数或顺序一变即**静默错配**
（勾叉依据挂到别的措施上，不报错）。这里对解析后的映射快照打指纹锁死，任何漂移都让测试失败。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_PATH = os.path.join(ROOT, "data", "knowledge", "permit_articles.json")
BUILDER = os.path.join(ROOT, "tools", "build_article_knowledge.py")

# 冻结指纹：任何对 MEASURE_REFS（含 seq 重排）或措施原文的改动都必须显式更新这里并复核。
EXPECTED_MEASURE_REFS_SHA256 = "98d7c7b65e6b72b4ae0032ab05ce08cb76c5f0f7e9f342f37318c84e23415ceb"


def load_kb():
    with open(KB_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class TestKnowledgeStructure(unittest.TestCase):
    def setUp(self):
        self.kb = load_kb()

    def test_top_level_keys(self):
        self.assertEqual(
            list(self.kb.keys()),
            ["version", "updated_at", "mapping_updated_at", "source_document", "corpus", "snapshot",
             "description", "semantics", "stats", "articles", "measures", "appendix_levels",
             "outline", "filling_rules", "measure_refs"],
        )
        self.assertEqual(self.kb["version"], 4)

    def test_counts(self):
        kb = self.kb
        self.assertEqual(len(kb["articles"]), 187)
        self.assertEqual(len(kb["measures"]), 10)
        self.assertEqual(sum(m["count"] for m in kb["measures"]), 144)
        self.assertEqual(sum(len(g["items"]) for g in kb["appendix_levels"]), 87)
        self.assertEqual(len(kb["outline"]), 22)
        self.assertEqual(len(kb["filling_rules"]), 2)

    def test_article_no_continuous(self):
        nos = [a["article_no"] for a in self.kb["articles"]]
        self.assertEqual(nos, list(range(1, 188)))

    def test_semantics_values(self):
        sem = self.kb["semantics"]
        self.assertEqual(set(sem), {"check", "cross", "conform", "blank"})
        self.assertIn("人工可忽略", sem["cross"])       # × 不涉及 → 可忽略
        self.assertIn("现场填", sem["blank"])           # 空 → 操作员现场填
        # ○ 是临时用电 ○ 网格的记号，语义是「该项要求已符合」，与 √「涉及」区分开
        self.assertIn("符合", sem["conform"])

    def test_outline_has_bare_sections(self):
        """R4：两个无编号节标题必须在大纲里（曾在兜底逻辑中丢失）。"""
        names = [o["name"] for o in self.kb["outline"]]
        self.assertEqual(len([o for o in self.kb["outline"] if o["kind"] == "chapter"]), 13)
        self.assertEqual(len([o for o in self.kb["outline"] if o["kind"] == "section"]), 9)
        self.assertIn("通用安全要求", names)
        self.assertIn("动火分类与分级", names)
        # 附录里的表格/等级标题不得混入大纲
        for bad in ("附录1", "附录2", "附录3", "编号："):
            self.assertNotIn(bad, names)
        self.assertFalse(any("许可证" in n for n in names))
        # 全部落在正文区（能对上条号）
        self.assertTrue(all(o["start_article_no"] is not None for o in self.kb["outline"]))


class TestScenarioQuality(unittest.TestCase):
    """情形（when）只能是「条件短语」，不得混入要求/定义/职责文本。"""

    MODAL = re.compile(r"应当|应|不得|严禁|不应|必须|禁止|不准")
    DEF = re.compile(r"是指|称为")
    DUTY = re.compile(r"负责")

    def setUp(self):
        self.whens = [s["when"] for a in load_kb()["articles"]
                      for s in a["scenarios"] if s.get("when")]

    def test_when_not_empty_but_clean(self):
        self.assertGreater(len(self.whens), 50)
        self.assertEqual([w for w in self.whens if self.MODAL.search(w)], [])
        self.assertEqual([w for w in self.whens if self.DEF.search(w)], [])
        self.assertEqual([w for w in self.whens if self.DUTY.search(w)], [])
        self.assertEqual([w for w in self.whens if "。" in w], [])

    def test_from_lead_conversions(self):
        """路径 A（引导句「…之一的：」→ 子项转条件）应恰好命中 art050/051/124。"""
        got = sorted({a["article_no"] for a in load_kb()["articles"]
                      for s in a["scenarios"] if s.get("from_lead")})
        self.assertEqual(got, [50, 51, 124])


class TestMeasureRefsLock(unittest.TestCase):
    """R5：条款↔措施映射必须与措施清单严格对齐，防静默错配。"""

    def setUp(self):
        self.kb = load_kb()
        self.refs = self.kb["measure_refs"]
        self.by_type = {m["type_key"]: m for m in self.kb["measures"]}

    def test_fingerprint(self):
        canon = json.dumps(self.refs, ensure_ascii=False, sort_keys=True)
        got = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        self.assertEqual(
            got, EXPECTED_MEASURE_REFS_SHA256,
            "MEASURE_REFS 或其解析出的措施原文发生变化。请逐条复核 130 条映射仍挂在正确的措施上，"
            "确认无误后更新 EXPECTED_MEASURE_REFS_SHA256。",
        )

    def test_every_mapped_seq_exists(self):
        for key, mapping in self.refs.items():
            measure = self.by_type.get(key)
            self.assertIsNotNone(measure, f"映射里的作业类型 {key} 在措施清单中不存在")
            seqs = {it["seq"] for it in measure["items"]}
            for seq, text in mapping.items():
                self.assertIn(int(seq), seqs, f"{key} #{seq} 不在该表措施序号里")
                self.assertTrue(text, f"{key} #{seq} 解析不到措施原文（已错配）")

    def test_mapped_text_matches_measure(self):
        """映射快照里的原文必须与措施清单里的文本逐字一致。"""
        for key, mapping in self.refs.items():
            item_text = {str(it["seq"]): it["text"] for it in self.by_type[key]["items"]}
            for seq, text in mapping.items():
                self.assertEqual(text, item_text[seq])

    def test_article_refs_point_to_real_articles(self):
        valid = {a["article_no"] for a in self.kb["articles"]}
        for measure in self.kb["measures"]:
            for it in measure["items"]:
                for no in it["refs"]:
                    self.assertIn(no, valid, f"{measure['type_key']} #{it['seq']} → 第{no}条 不存在")

    def test_mapped_count(self):
        total = sum(1 for m in self.kb["measures"] for it in m["items"] if it["refs"])
        self.assertEqual(total, 130)

    def test_hot_work_fully_mapped(self):
        """动火 15 条必须全部挂上依据（K2 补齐），防止回退成无依据判定。"""
        gaps = [it["seq"] for it in self.by_type["hot_work"]["items"] if not it["refs"]]
        self.assertEqual(gaps, [], f"动火作业这些措施又变成无依据了：{gaps}")

    def test_mapping_coverage_floor(self):
        """依据覆盖率下限：未挂上的是「办法中确无对应条文」的票面条目，不该继续扩大。"""
        total = sum(len(m["items"]) for m in self.kb["measures"])
        mapped = sum(1 for m in self.kb["measures"] for it in m["items"] if it["refs"])
        self.assertGreaterEqual(mapped / total, 0.75, f"依据覆盖率跌到 {mapped}/{total}")

    def test_verdict_2026_10_12_changes(self):
        """第 4 轮裁决确认的 8 处映射变更，在此定点锁定。

        `measure_refs` 快照只记措施原文、不含条号，所以**指纹变化捕获不到改条号**——
        这类改动必须靠定点断言守住（否则被改回去也不会有人报警）。
        """
        cases = {
            ("confined_space", 3): [74],        # 通用 37 → 本类型章 74（中和或置换）
            ("confined_space", 9): [43, 81],    # 跨章 144 → 43（手持电动工具）+ 81（使用防爆工具）
            ("non_routine", 7): [33, 43],       # 并入 43（GB 2894 安全警示标志）
            ("non_routine", 8): [15, 40],       # 去冗余 8，补交底条 40
            ("non_routine", 12): [34, 46],      # 撤特级动火专属的 70（跨类型）
            ("lifting", 14): [119],             # 去与细则等价的概括条 124
            ("lifting", 18): [33, 122],         # 并入通用 33（设置警戒线）
            ("lifting", 21): [123],             # 去与细则等价的概括条 124
        }
        for (key, seq), want in cases.items():
            with self.subTest(key=key, seq=seq):
                got = next(it["refs"] for it in self.by_type[key]["items"] if it["seq"] == seq)
                self.assertEqual(got, want, f"{key} #{seq} 的映射被改动，须重新复核")

    def test_k1_confined_space_13_points_to_87(self):
        """K1：受限空间#13（应急冲洗水）必须指第 87 条。

        第 82 条讲的是「个体防护装备」，与应急冲洗水无关；第 87 条逐字含
        「盛有腐蚀性介质的容器作业现场应当配备应急用冲洗水等」。这条错配由
        「全库最优条文扫描」发现，指纹锁测不出（快照由配置派生），故在此定点锁定。
        """
        refs = next(it["refs"] for it in self.by_type["confined_space"]["items"] if it["seq"] == 13)
        self.assertEqual(refs, [87])

    def test_temp_power_refs_stay_inside_its_own_chapter(self):
        """临时用电 ○ 网格接入（2026-10-12）：19 条依据必须全部落在本类型章（第 125~146 条）。

        R1/R5：该类型只挂本类型章专属条，跨章即为错配。另外 4 条（电源接入点确认、
        移动式电缆盘、恶劣天停电已交代、受限空间）正文确无对口条文，**保持不挂**——
        一旦有人为了凑覆盖率硬挂上去，这条断言会拦住。
        """
        by_no = {a["article_no"]: a for a in self.kb["articles"]}
        items = self.by_type["temp_power"]["items"]
        for it in items:
            for no in it["refs"]:
                self.assertEqual(
                    by_no[no]["chapter"], "临时用电作业安全要求",
                    f"临时用电 #{it['seq']} → 第{no}条 跨出了本类型章（{by_no[no]['chapter']}）",
                )
                self.assertTrue(125 <= no <= 146, f"第{no}条不在临时用电章条号区间内")
        gaps = sorted(it["seq"] for it in items if not it["refs"])
        self.assertEqual(gaps, [1, 19, 22, 23], f"无依据的条目变了：{gaps}")

    def test_temp_power_verdict_2026_10_12(self):
        """临时用电接入时的定点映射（改条号不会被指纹捕获，必须定点锁定）。"""
        cases = {
            3: [126],        # 可燃气体检测分析
            5: [128],        # 自备电源不得接入电网
            7: [144],        # 手持电动工具
            12: [139],       # 一机一闸一保护
            13: [139, 144],  # RCD 选型 + 15mA / 0.1s
            16: [132],       # 架空线路
            20: [127],       # 防爆区域
            21: [134, 136],  # 接地良好
        }
        for seq, want in cases.items():
            with self.subTest(seq=seq):
                got = next(it["refs"] for it in self.by_type["temp_power"]["items"] if it["seq"] == seq)
                self.assertEqual(got, want, f"临时用电 #{seq} 的映射被改动，须重新复核")

    def test_no_conditional_flag_on_measures(self):
        """措施上不得再有 conditional 标记。

        「恒适用 / 条件式」是已移除的判定机制（规则集靠正则 + 人工校正表维护，判定层改为
        **全部措施送模型、逐条附条文依据**）。此断言防止该字段被无声加回——一旦回归，
        判定层会重新出现"不经模型直接打勾"的路径。
        """
        for m in self.kb["measures"]:
            for it in m["items"]:
                self.assertNotIn("conditional", it, f"{m['type_key']} #{it['seq']} 又带上了 conditional")
        for key in ("measures_conditional", "measures_always"):
            self.assertNotIn(key, self.kb["stats"])


class TestBuilderIdempotent(unittest.TestCase):
    """复跑生成脚本，产物必须与提交版本逐字节一致（无外部依赖漂移）。"""

    def test_regeneration_is_byte_identical(self):
        with open(KB_PATH, encoding="utf-8") as handle:
            committed = json.load(handle)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "kb.json")
            # 产物里带「生成当天日期」：跨天复跑必然逐字节不同，幂等就废了。注回提交版本
            # 那一天再比，验的才是「内容没漂」，而不是「今天恰好还是那一天」。
            env = dict(os.environ, KB_OUTPUT_PATH=out, KB_UPDATED_AT=committed["updated_at"])
            proc = subprocess.run(
                [sys.executable, BUILDER], cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))
            with open(out, "rb") as a, open(KB_PATH, "rb") as b:
                self.assertEqual(a.read(), b.read(), "知识库产物与提交版本不一致（生成已漂移）")


if __name__ == "__main__":
    unittest.main()
