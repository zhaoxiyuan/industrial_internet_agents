import io
import unittest
import zipfile

from web.api.docx_permit import parse_docx_bytes


NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(rows, title="动火作业许可证"):
    def cell(value):
        return f'<w:tc><w:p><w:r><w:t>{value}</w:t></w:r></w:p></w:tc>'

    table = "".join("<w:tr>" + "".join(cell(value) for value in row) + "</w:tr>" for row in rows)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="{NS}"><w:body>
    <w:p><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:tbl>{table}</w:tbl></w:body></w:document>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("[Content_Types].xml", "<Types/>")
    return output.getvalue()


class DocxPermitTests(unittest.TestCase):
    def test_blank_hot_work_template_is_recognized_without_fake_values(self):
        result = parse_docx_bytes(make_docx([
            ["作业内容", ""],
            ["作业地点", "", "动火部位", ""],
            ["作业时间", "自 年 月 日 时 分始，至 年 月 日 时 分止"],
        ]), "空白模板.docx")

        self.assertTrue(result["is_blank"])
        self.assertEqual(result["application"]["job_type"], "动火作业")
        self.assertEqual(result["application"]["planned_start"], "")
        self.assertIn("作业内容", result["missing_fields"])

    def test_filled_fields_and_two_work_times_are_extracted(self):
        result = parse_docx_bytes(make_docx([
            ["申请单位", "作业单位A"],
            ["申请人", "赵红", "作业人", "李工"],
            ["作业地点", "催化装置区"],
            ["作业内容", "空气预热器人孔清理"],
            ["作业时间", "自2026年9月9日9时30分始，至2026年9月9日17时0分止"],
            ["1", "清除周边可燃物", "√", "赵红"],
        ]), "已填写.docx")

        app = result["application"]
        self.assertFalse(result["is_blank"])
        self.assertEqual(app["work_unit"], "作业单位A")
        self.assertEqual(app["region"], "催化装置区")
        self.assertEqual(app["planned_start"], "2026-09-09T09:30:00+08:00")
        self.assertEqual(app["planned_end"], "2026-09-09T17:00:00+08:00")
        self.assertEqual(app["safety_measures"][0]["description"], "清除周边可燃物")

    def test_rejects_non_docx_zip(self):
        with self.assertRaisesRegex(ValueError, "有效的 DOCX"):
            parse_docx_bytes(b"not a zip")


if __name__ == "__main__":
    unittest.main()
