"""JSA 报告渲染测试。

重点盯两件事：
1. **样例内容必须清干净**——模板是「已填好的样例报告」，照「填空格」的思路做会把别人的
   硫化氢、DN600 留在报告里；
2. **章节号要随专项章节数顺延**——专项有几节是数据决定的，后面几章的编号跟着变。
"""
import os
import re
import tempfile
import unittest
import zipfile

from agents.workflow import jsa_report, get_permit_type

LINE_OPEN, _ = get_permit_type("line_open")

# 刻意「像模板」的样例值：用来验证它们不会串进输出
TEMPLATE_WORDS = (
    "含硫化氢天然气管道盲板倒换作业JSA分析报告",
    "现场准备及隔离",
    "搭设高处作业平台",
    "施工方案审批完成",
    "SCBA空气呼吸器、检测仪、应急器材配置到位",
    "本作业涉及大口径、高压力、含硫化氢天然气管线开口操作",
)

PAYLOAD = {
    "source": "ai",
    "job_name": "乙烯装置换热器检修作业",
    "risk_summary": "本作业涉及高处与吊装交叉作业，风险集中在坠落与吊物打击。",
    "pre_checks": ["办理作业许可", "确认隔离措施", "完成气体检测"],
    "steps": [
        {"step": "现场准备", "hazard": "无关人员进入", "consequence": "人员伤害",
         "level": "中", "control": "设置警戒区"},
        {"step": "搭设平台", "hazard": "平台失稳", "consequence": "人员坠落",
         "level": "高", "control": "验收合格后使用"},
        {"step": "拆除螺栓", "hazard": "工具坠落", "consequence": "物体打击",
         "level": "中", "control": "工具系挂安全绳"},
        {"step": "吊装换热器", "hazard": "吊物失稳", "consequence": "人员砸伤",
         "level": "高", "control": "专人指挥并试吊"},
        {"step": "回装复位", "hazard": "密封失效", "consequence": "介质泄漏",
         "level": "高", "control": "按扭矩紧固并查漏"},
    ],
    "special_sections": [
        {"title": "高处作业及吊装控制措施", "items": ["平台验收合格后使用", "安全带双挂点", "吊物下方禁止站人"]},
    ],
    "emergency": "发生人员坠落时立即停止作业并启动应急响应。",
    "final_checklist": ["作业许可已办理", "隔离措施已确认", "监护人已到位", "应急器材已就位"],
    "main_risks": ["高处坠落", "吊物打击", "介质泄漏"],
}

VALUES = {
    "work_content": "乙烯装置换热器检修",
    "work_place": "乙烯装置 E-201",
    "work_height": "约 6m",
}


def _render(payload=None, values=None, related=None):
    """渲染到临时文件并返回 (document.xml 文本, body 文本)。"""
    with tempfile.TemporaryDirectory() as directory:
        out = os.path.join(directory, "jsa.docx")
        jsa_report.render_jsa_report(
            payload or PAYLOAD, values if values is not None else VALUES,
            LINE_OPEN, out, related or [],
        )
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
            xml = archive.read("word/document.xml").decode("utf-8")
    body = re.search(r"<w:body>(.*)</w:body>", xml, re.S).group(1)
    return xml, body, names


def _plain(block_xml):
    return "".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", block_xml))


def _blocks(body):
    return re.findall(r"<w:tbl>.*?</w:tbl>|<w:p\b.*?</w:p>|<w:p\b[^>]*/>", body, re.S)


def _headings(body):
    return [
        _plain(block) for block in _blocks(body)
        if 'w:pStyle w:val="Heading2"' in block
    ]


class JsaReportRenderTests(unittest.TestCase):
    def test_template_sample_text_is_completely_gone(self):
        """模板是「已填好的样例报告」：它的内容必须整份消失，只留样式。"""
        _, body, _ = _render()
        for probe in TEMPLATE_WORDS:
            self.assertNotIn(probe, body, f"样例内容「{probe[:16]}」残留在报告里")

    def test_own_data_lands_in_the_report(self):
        _, body, _ = _render()
        for probe in ("乙烯装置换热器检修作业JSA分析报告", "搭设平台", "吊物失稳",
                      "工具系挂安全绳", "监护人已到位", "乙烯装置 E-201"):
            self.assertIn(probe, body, f"报告里缺少「{probe}」")

    def test_section_numbers_shift_with_special_sections(self):
        """专项章节有几节由数据决定，后面的章号必须跟着顺延。"""
        base = {**PAYLOAD, "special_sections": []}
        self.assertEqual(
            _headings(_render(base)[1]),
            ["一、作业基本信息", "二、作业风险概述", "三、作业前安全确认要求", "四、JSA风险分析",
             "五、应急处置要求", "六、作业前最终确认清单"],
        )
        two = {**PAYLOAD, "special_sections": PAYLOAD["special_sections"] * 2}
        self.assertEqual(
            _headings(_render(two)[1])[-3:],
            ["六、高处作业及吊装控制措施", "七、应急处置要求", "八、作业前最终确认清单"],
        )

    def test_special_section_titles_come_from_data(self):
        _, body, _ = _render()
        self.assertIn("高处作业及吊装控制措施", body)
        self.assertIn("1. 平台验收合格后使用 2. 安全带双挂点 3. 吊物下方禁止站人", body)

    def test_table_rows_match_step_count(self):
        _, body, _ = _render()
        table = [block for block in _blocks(body) if block.startswith("<w:tbl")][0]
        rows = re.findall(r"<w:tr[ >].*?</w:tr>", table, re.S)
        self.assertEqual(len(rows), len(PAYLOAD["steps"]) + 1, "表格应为表头 + 每步一行")
        header = [_plain(cell).strip() for cell in re.findall(r"<w:tc>.*?</w:tc>", rows[0], re.S)]
        self.assertEqual(header, ["作业步骤", "主要危险", "可能后果", "风险等级", "控制措施"])
        first = [_plain(cell).strip() for cell in re.findall(r"<w:tc>.*?</w:tc>", rows[1], re.S)]
        self.assertEqual(first, ["现场准备", "无关人员进入", "人员伤害", "中", "设置警戒区"])

    def test_bullets_and_checklist_counts(self):
        _, body, _ = _render()
        bullet_count = sum(1 for block in _blocks(body) if 'w:pStyle w:val="ListBullet"' in block)
        self.assertEqual(bullet_count, len(PAYLOAD["pre_checks"]))
        checklist = [block for block in _blocks(body) if _plain(block).startswith("□")]
        self.assertEqual(len(checklist), len(PAYLOAD["final_checklist"]))
        self.assertIn("□ 监护人已到位", body)

    def test_info_lines_skip_empty_values(self):
        """取不到的字段直接不写——不编造申请里没有的信息。"""
        _, body, _ = _render(values={"work_content": "换热器检修"})
        self.assertIn("作业名称：", body)
        self.assertNotIn("管道规格：", body)
        self.assertNotIn("运行压力：", body)

    def test_sectpr_is_preserved(self):
        """页面设置（sectPr）必须原样保留并落在正文末尾，否则打印版式会变。"""
        xml, body, _ = _render()
        self.assertEqual(xml.count("<w:sectPr"), 1, "sectPr 应当只有一个")
        self.assertTrue(body.rstrip().endswith("</w:sectPr>"), "sectPr 必须在正文末尾")
        self.assertIn('w:w="12240"', body, "页面尺寸被改动了")
        # 重建正文的那一步不该自己产出 sectPr——它由渲染层从模板原样接上
        self.assertNotIn(
            "sectPr",
            jsa_report.build_document_body(PAYLOAD, VALUES, LINE_OPEN),
            "build_document_body 不该自己造 sectPr",
        )

    def test_other_package_parts_survive(self):
        _, _, names = _render()
        for part in ("word/document.xml", "word/styles.xml", "word/numbering.xml"):
            self.assertIn(part, names)

    def test_refuses_to_render_without_steps(self):
        with self.assertRaisesRegex(ValueError, "作业步骤"):
            jsa_report.build_document_body({**PAYLOAD, "steps": []}, VALUES, LINE_OPEN)

    def test_refuses_to_render_a_failed_analysis(self):
        """分析没成功就不该产出报告——空报告比没有报告更危险。"""
        with self.assertRaisesRegex(ValueError, "未成功生成"):
            _render(payload={**PAYLOAD, "source": "unavailable"})


if __name__ == "__main__":
    unittest.main()
