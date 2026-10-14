"""许可证模板测试：模板完整性、字段注入规则、docx 生成与 P1 手动填报路径。"""
import io
import json
import os
import re
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import agents.workflow.file_utils as file_utils
from agents.workflow import get_job_dir, save_job_application
from agents.workflow.file_utils import write_json_file
from agents.workflow.permit_template import (
    _locate_label,
    _measure_row_indexes,
    _read_document_xml,
    _table_xml,
    _TR_RE,
    build_permit_rows,
    decision_map,
    fill_safety_measures,
    get_permit_type,
    get_template_path,
    list_permit_types,
    list_safety_measures,
    resolve_fields,
    render_permit_docx,
)
from web.api.workflow import handle_workflow_permit_docx, handle_workflow_permit_types

JOB = "20261009120000001"

# 10 张票里只有这两张没有「关联/涉及的其他作业」栏——该字段在这两张票里必须被 drop，
# 表单上也不该出现（用户填了也上不了票）。
TYPES_WITHOUT_RELATED_COLUMN = {"temp_power", "radiation"}

# 与 agents/workflow/permit_template._locate_label 相同的定位口径：全表精确优先，其次前缀。
def _label_row_index(rows, label, measure_texts=(), contains=False):
    """同口径，但返回下标——「表头行 + 值行」版式要按下标取下一行。

    ``measure_texts`` 是该类型安全措施的正文集合：含这些文本的行属于措施表，
    与生产端 ``_locate_label`` 一样整体跳过（否则「作业高度」会命中吊装的措施正文）。

    同样与生产端一致：非签字类字段不认领「…（签字）：」格子——那是人工签署位。
    """
    label = (label or "").strip()
    own_signature = "签字" in label

    def claimable(text):
        return own_signature or "（签字）" not in text

    candidates = [
        index for index, row in enumerate(rows)
        if not any(cell["text"].strip() in measure_texts for cell in row)
    ]
    for index in candidates:
        if any(claimable(cell["text"]) and cell["text"].strip() == label for cell in rows[index]):
            return index
    for index in candidates:
        if any(claimable(cell["text"]) and cell["text"].strip().startswith(label) for cell in rows[index]):
            return index
    if contains:
        # 与生产端一致：inline 字段的标签可能在格中间（一格挤两个标签）。
        for index in candidates:
            if any(claimable(cell["text"]) and label in cell["text"].strip() for cell in rows[index]):
                return index
    return None


def _label_row(rows, label, measure_texts=()):
    index = _label_row_index(rows, label, measure_texts)
    return None if index is None else rows[index]


def _rendered_xml(key, values):
    """渲染成 docx 后取 document.xml——符号勾选框不进 <w:t>，只能在这一层看。"""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "permit.docx")
        render_permit_docx(key, values, path)
        with zipfile.ZipFile(path) as archive:
            return archive.read("word/document.xml").decode("utf-8")


def _tick_options(field, raw):
    """勾选字段的取值拆成一个个选项（bool → 「是」/「否」），逐项核对。"""
    if field["type"] == "bool":
        return ["是" if raw else "否"]
    if isinstance(raw, (list, tuple)):
        return [str(option) for option in raw]
    return [str(raw)]


def _tick_count(xml):
    """勾选总数——票面框有两种：符号控件（Wingdings 2 的 0052）与字符 ☑。"""
    return xml.count('w:char="0052"') + xml.count("\u2611")


def _value_text(field, raw):
    if field["type"] == "bool":
        return "是" if raw else "否"
    if isinstance(raw, (list, tuple)):
        return "、".join(raw)
    if isinstance(raw, dict):
        return f"{raw.get('start', '')} 至 {raw.get('end', '')}"
    return raw


def _sample_values(fields):
    values = {}
    for field in fields:
        field_type = field["type"]
        if field_type == "bool":
            values[field["key"]] = True
        elif field_type == "multiselect":
            values[field["key"]] = field["options"][:2]
        elif field_type == "select":
            values[field["key"]] = field["options"][0]
        elif field_type == "time_range":
            values[field["key"]] = {"start": "2026-10-09 09:00", "end": "2026-10-09 17:00"}
        else:
            values[field["key"]] = f"⟨{field['key']}⟩"
    return values


def _document_xml(path):
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


# 只匹配段落本身，不匹配 <w:pPr> 等以 <w:p 开头但非段落的标签。
_PARAGRAPH_RE = re.compile(r"<w:p[ />]")


def _paragraph_count(document_xml):
    return len(_PARAGRAPH_RE.findall(document_xml))


_TR_RE = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
_TC_RE = re.compile(r"<w:tc>.*?</w:tc>", re.S)
_WT_RE = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")


def _table_rows(document_xml):
    """取文档里第一张表，返回 [[单元格文本, …], …]。"""
    table = re.search(r"<w:tbl[ >].*?</w:tbl>", document_xml, re.S).group(0)
    return [
        ["".join(_WT_RE.findall(cell)).strip() for cell in _TC_RE.findall(row)]
        for row in _TR_RE.findall(table)
    ]


class PermitTemplateTests(unittest.TestCase):
    """模板定义与模板文件的一致性。"""

    def test_all_types_available(self):
        types = list_permit_types()
        self.assertEqual(len(types), 10)
        self.assertEqual(len({item["key"] for item in types}), 10)

    def test_every_type_has_template_file(self):
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            path = get_template_path(type_def)
            self.assertTrue(os.path.exists(path), f"模板文件缺失: {path}")
            self.assertGreater(os.path.getsize(path), 0)

    def test_titles_have_no_recommended_style_suffix(self):
        for item in list_permit_types():
            self.assertNotIn("推荐样式", item["title"])
            self.assertIn("许可证", item["title"])

    def test_every_type_has_required_fields_and_they_are_bounded(self):
        for item in list_permit_types():
            required = [field for field in item["fields"] if field["required"]]
            self.assertTrue(required, f"{item['name']} 没有必填项")
            # 表单要求精简：每个类型的必填项不应过多
            self.assertLessEqual(len(required), 12, f"{item['name']} 必填项过多")
            for field in item["fields"]:
                self.assertTrue(field["label"], f"{item['name']} 存在无标签字段")

    def test_related_works_options_exclude_own_type(self):
        """「关联的其他作业类型」的候选项 = 除本类型外的所有类型，且非必填。

        临时用电与射线票面没有这一栏，字段被 drop——它们不该出现在字段列表里。
        """
        types = list_permit_types()
        names = [item["name"] for item in types]
        for item in types:
            field = next((f for f in item["fields"] if f["key"] == "related_works"), None)
            if item["key"] in TYPES_WITHOUT_RELATED_COLUMN:
                self.assertIsNone(field, f"{item['name']} 没有该栏，字段应当被 drop 掉")
                continue
            self.assertIsNotNone(field, f"{item['name']} 缺少 related_works 字段")
            self.assertEqual(field["type"], "multiselect")
            self.assertEqual(field["label"], "关联的其他作业类型")
            self.assertFalse(field["required"], f"{item['name']} 的关联作业类型应是非必填")
            self.assertEqual(
                sorted(field["options"]), sorted(n for n in names if n != item["name"]),
                f"{item['name']} 的关联作业类型候选项不对（须排除自己）",
            )

    def test_unknown_type_is_rejected(self):
        type_def, _ = get_permit_type("不存在的类型")
        self.assertIsNone(type_def)
        with self.assertRaises(ValueError):
            build_permit_rows("不存在的类型", {})

    def test_template_files_do_not_contain_recommended_style(self):
        """模板 docx 自身的标题不能带「推荐样式」，且必须与配置的标题一致。

        模板是从办法附录 1 导出的，原始标题写作「…（推荐样式）」；导出后需要改写。
        重新导出模板时必须重新清理，否则下载到的许可证会带着这四个字。
        """
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            document_xml = _document_xml(get_template_path(type_def))
            self.assertNotIn("推荐样式", document_xml, f"{item['name']} 模板文件里仍含「推荐样式」")
            self.assertIn(item["title"], document_xml, f"{item['name']} 模板文件里找不到标题「{item['title']}」")


class PermitFillTests(unittest.TestCase):
    """填值落位：每个字段的值必须落在它标签所在的那一行。"""

    # 没有「可选落位」这种豁免：**字段在票面找不到落点，就必须被该类型显式 drop 掉**。
    # 早年这里有个白名单（related_works / local_supervisor / guardian）把"多数票没有"的
    # 字段一律放过，结果掩盖了真问题——临时用电与射线的表单上顶着「属地监督/监护人/
    # 关联作业类型」三个填空，而这两张票根本没有这些栏，用户填了也上不了票。

    def test_every_field_lands_on_its_label_row(self):
        """每个字段要么在票面上有落点，要么被 drop——不存在「填了上不了票」的中间态。"""
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            dropped = set(type_def.get("drop") or [])
            values = _sample_values(resolve_fields(type_def))
            rows = build_permit_rows(item["key"], values)["rows"]
            measure_texts = {m["text"] for m in list_safety_measures(item["key"])}
            for field in resolve_fields(type_def):
                index = _label_row_index(
                    rows, field["label"], measure_texts, contains=(field.get("mode") == "inline")
                )
                if index is None:
                    self.assertNotIn(
                        field["key"], dropped,   # drop 掉的字段不该出现在 resolve_fields 里
                        f"{item['name']}「{field['form_label']}」既被 drop 又出现在字段列表",
                    )
                    self.fail(
                        f"{item['name']} 的「{field['form_label']}」在票面上找不到这一栏："
                        f"要么把 label 改对，要么加进该类型的 drop（现在表单上有、票面填不进）"
                    )
                expected = _value_text(field, values[field["key"]])
                if not expected:
                    continue
                # 管线打开的参数行是「表头行 + 值行」版式：表头印介质/温度/…/编号，
                # 值写在正下方一行的同列，所以标签行与其下一行都算落在位。
                candidates = rows[index: index + 2]
                if field.get("mode") == "check":
                    # 打勾模式不写文字：每个选项**各自**印在票面上（模板本来就印着），
                    # 不会拼成「、」串——所以逐项核对，而不是拿拼好的串去比。
                    for option in _tick_options(field, values[field["key"]]):
                        self.assertTrue(
                            any(option in cell["text"] for row in candidates for cell in row),
                            f"{item['name']}「{field['label']}」的选项「{option}」不在标签行",
                        )
                    continue
                self.assertTrue(
                    any(expected in cell["text"] for row in candidates for cell in row),
                    f"{item['name']}「{field['label']}」的值未落在标签行或其正下方的值行",
                )

    def test_fill_mode_uses_the_cell_right_of_the_label(self):
        # 动火的「申请单位」右侧是空格，值应落在右侧格而不是标签格里。
        rows = build_permit_rows("hot_work", {"apply_unit": "炼油三部"})["rows"]
        first_row = rows[0]
        label_index = next(i for i, cell in enumerate(first_row) if cell["text"].strip() == "申请单位")
        self.assertEqual(first_row[label_index + 1]["text"].strip(), "炼油三部")
        self.assertEqual(first_row[label_index]["text"].strip(), "申请单位")

    def test_same_mode_appends_to_the_label_cell(self):
        # 受限空间的「作业内容描述：」整行只有一个格子，值追加在该格内。
        rows = build_permit_rows("confined_space", {"work_content": "清罐底污泥"})["rows"]
        row = _label_row(rows, "作业内容描述")
        self.assertTrue(any(cell["text"].strip().endswith("清罐底污泥") for cell in row))

    def test_related_works_lands_in_every_template_that_has_the_column(self):
        """8 张有该栏的票面都要能填上，且多选用「、」拼成一行。

        高处与吊装的右侧格已被模板自带的作业类型勾选清单占用，`fill` 模式会退化为
        追加到标签格——信息照样落在票面上，所以断言只看「值是否出现在该行」。
        """
        sentinel = ["断路作业", "射线作业"]
        for item in list_permit_types():
            if item["key"] in TYPES_WITHOUT_RELATED_COLUMN:
                continue
            rows = build_permit_rows(item["key"], {"related_works": sentinel})["rows"]
            joined = "".join(cell["text"] for row in rows for cell in row)
            self.assertIn("断路作业、射线作业", joined, f"{item['name']} 的关联作业类型没写进票面")

    def test_related_column_presence_is_locked(self):
        """锁定现状：10 张票里只有临时用电与射线没有「关联/涉及的其他作业」栏。

        这两张票的该字段是 **drop 掉**的——表单上也不该出现。将来若给其中一张补上、
        或哪张模板改版丢了这一栏，这条会失败，那正是要同步改配置与测试的信号。
        """
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            field = next((f for f in resolve_fields(type_def) if f["key"] == "related_works"), None)
            if item["key"] in TYPES_WITHOUT_RELATED_COLUMN:
                self.assertIsNone(field, f"{item['name']} 没有该栏，字段应当被 drop 掉")
                continue
            self.assertIsNotNone(field, f"{item['name']} 缺少 related_works 字段")
            rows = build_permit_rows(item["key"], {})["rows"]
            self.assertTrue(
                any(field["label"] in cell["text"] for row in rows for cell in row),
                f"{item['name']} 的「关联/涉及」栏没在票面上找到",
            )

    def test_types_without_the_related_column_do_not_fail(self):
        """临时用电与射线的票面没有该栏：给了值也不报错，只是填不进去。"""
        for item in list_permit_types():
            if item["key"] not in TYPES_WITHOUT_RELATED_COLUMN:
                continue
            rows = build_permit_rows(item["key"], {"related_works": ["断路作业", "射线作业"]})["rows"]
            joined = "".join(cell["text"] for row in rows for cell in row)
            self.assertNotIn("断路作业、射线作业", joined, f"{item['name']} 不该有位置容纳关联作业名")

    def test_condition_fields_lands_on_line_open_permit(self):
        """管线打开是唯一印有工况栏的票面：管道参数（介质/温度/压力）+ 盲板参数
        （材质/规格/编号）+ 垫片规格，七个值全部必须上票。

        「规格」栏归属盲板参数、不是管道规格（按 gridSpan 展开后的列分组确认），
        所以字段叫 blind_spec、表单上写「盲板规格」，别再叫回管道规格。
        """
        rows = build_permit_rows("line_open", {
            "medium": "含硫化氢天然气", "op_temperature": "45℃", "op_pressure": "5.4MPa",
            "blind_material": "碳钢", "blind_spec": "DN600", "blind_no": "BL-07",
            "gasket_spec": "DN600 缠绕垫",
        })["rows"]
        joined = "".join(cell["text"] for row in rows for cell in row)
        for label, value in (
            ("介质", "含硫化氢天然气"), ("温度", "45℃"), ("压力", "5.4MPa"),
            ("材质", "碳钢"), ("规格", "DN600"), ("编号", "BL-07"),
            ("垫片规格", "DN600 缠绕垫"),
        ):
            self.assertIn(value, joined, f"{label}没写进管线打开票面")

    def test_condition_fields_land_on_the_value_row_below_the_header(self):
        """参数行是「表头行 + 值行」：值必须写进表头正下方那一行，不能追加到表头格里。

        表头行印「介质/温度/压力/材质/规格/编号」，右邻格是下一个表头（不是值格），
        所以靠 `_blank_cell_below` 定位——看错版式就会把值印到表头上。
        """
        values = {"medium": "含硫化氢天然气", "blind_spec": "DN600", "op_pressure": "5.4MPa"}
        rows = build_permit_rows("line_open", values)["rows"]
        header = _label_row_index(rows, "介质")
        self.assertIsNotNone(header, "没找到参数表头行")
        header_texts = [cell["text"].strip() for cell in rows[header]]
        self.assertIn("温度", header_texts, "参数表头行不是预期的那一列布局")
        self.assertEqual(
            header_texts, ["", "介质", "温度", "压力", "材质", "规格", "编号", ""],
            "表头行被写进了填值，版面已错",
        )
        value_texts = [cell["text"].strip() for cell in rows[header + 1]]
        self.assertEqual(value_texts[1], "含硫化氢天然气", "介质没落在「介质」列下方")
        self.assertEqual(value_texts[3], "5.4MPa", "压力没落在「压力」列下方")
        self.assertEqual(value_texts[5], "DN600", "规格没落在「规格」列下方")

    def test_blind_number_does_not_spill_into_the_gasket_cell(self):
        """「编号」右边紧邻的空格其实是「垫片规格」的值格——跨了参数分组。

        按 fill 的「右邻空格」口径会填错组，所以这类版式一律用 below 模式：
        值按 gridSpan 展开后的网格列往下找，而不是往右找。
        """
        rows = build_permit_rows("line_open", {"blind_no": "BL-07"})["rows"]
        header = _label_row_index(rows, "介质")
        value_texts = [cell["text"].strip() for cell in rows[header + 1]]
        self.assertEqual(value_texts[6], "BL-07", "盲板编号没落在「编号」列下方")
        self.assertEqual(value_texts[7], "", "盲板编号串进了垫片规格的值格")

    def test_fields_do_not_fill_signature_cells(self):
        """签字栏由人工现场签署：非签字类字段不得把名字写进「…（签字）：」格。

        射线的尾部是签字承诺区，其中一行正是「监护人（签字）：…」——通用字段「监护人」
        若按前缀匹配认领它，就等于系统替监护人签名。
        """
        rows = build_permit_rows("radiation", {"guardian": "张三"})["rows"]
        joined = "".join(cell["text"] for row in rows for cell in row)
        self.assertNotIn("张三", joined, "监护人不该被写进射线的签字承诺区")
        # 反向：标签自带「签字」的字段填的是姓名（动火人（签字）），必须照常上票。
        hot_rows = build_permit_rows("hot_work", {"hot_person": "李四"})["rows"]
        hot_joined = "".join(cell["text"] for row in hot_rows for cell in row)
        self.assertIn("李四", hot_joined, "动火人姓名应照常写进票面")

    def test_supervisor_and_guardian_land_in_the_templates_that_have_them(self):
        """属地监督/监护人是 8 张票共有的栏位（临时用电与射线没有）。"""
        for item in list_permit_types():
            if item["key"] in ("temp_power", "radiation"):
                continue
            rows = build_permit_rows(item["key"], {
                "local_supervisor": "⟨属地⟩", "guardian": "⟨监护⟩",
            })["rows"]
            joined = "".join(cell["text"] for row in rows for cell in row)
            self.assertIn("⟨属地⟩", joined, f"{item['name']} 缺「属地监督」栏")
            self.assertIn("⟨监护⟩", joined, f"{item['name']} 缺「监护人」栏")

    def test_gas_detection_values_land_on_hot_work_and_confined_space(self):
        """动火与受限空间的气体检测数据有专门栏位，必须上票（受限空间没有「检测位置」栏）。"""
        common = {"detect_time": "09:20", "o2_conc": "20.9", "lel_conc": "0", "toxic_conc": "0"}
        for key, extra in (("hot_work", {"detect_place": "塔顶平台"}), ("confined_space", {})):
            rows = build_permit_rows(key, dict(common, **extra))["rows"]
            joined = "".join(cell["text"] for row in rows for cell in row)
            for value in list(common.values()) + list(extra.values()):
                self.assertIn(value, joined, f"{key} 的气体检测数据没上票")

    def test_basic_fields_never_write_into_the_measure_table(self):
        """基本信息一律绕开安全措施表那一段。

        吊装的「作业高度」前缀正好撞上措施正文「作业高度和转臂范围内无架空线路。」，
        一旦按前缀匹配写进去，记号列被占、打标行数就少一条。
        """
        measures = list_safety_measures("lifting")
        self.assertTrue(
            any(m["text"].startswith("作业高度") for m in measures),
            "吊装措施里没有以「作业高度」开头的那条，本用例前提已变",
        )
        type_def, _ = get_permit_type("lifting")
        table_xml = _table_xml(_read_document_xml(get_template_path(type_def)))
        rows_xml = _TR_RE.findall(table_xml)
        # 不排除措施段时确实会命中（否则本用例恒真，护栏失效也测不出来）
        self.assertNotEqual(
            _locate_label(rows_xml, "作业高度"), (-1, -1),
            "「作业高度」已不撞措施行，本用例的前提变了",
        )
        self.assertEqual(
            _locate_label(rows_xml, "作业高度", _measure_row_indexes(table_xml)), (-1, -1),
            "措施段没被排除，「作业高度」会写进记号列、打标少一条",
        )

        rows = build_permit_rows("lifting", {"work_place": "催化装置区"})["rows"]
        for measure in measures:
            self.assertTrue(
                any(measure["text"] in cell["text"] for row in rows for cell in row),
                f"措施被改写: {measure['text'][:20]}",
            )

    CONDITION_KEYS = {
        "medium", "op_temperature", "op_pressure",
        "blind_material", "blind_spec", "blind_no", "gasket_spec",
    }

    def test_condition_fields_are_only_defined_for_line_open(self):
        """工况参数只在管线打开定义——其余 9 个类型不该出现这些填空。

        放进 common_fields 会让动火、断路这类跟管道无关的作业也要填「管道规格/
        运行压力/作业高度」，而它们的票面一张都印不下这些栏，值只进 JSON 不上票。
        """
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            keys = {f["key"] for f in resolve_fields(type_def)}
            if item["key"] == "line_open":
                self.assertTrue(
                    self.CONDITION_KEYS <= keys,
                    f"管线打开缺工况字段：{sorted(self.CONDITION_KEYS - keys)}",
                )
            else:
                self.assertFalse(
                    self.CONDITION_KEYS & keys,
                    f"{item['name']} 不该有工况参数字段：{sorted(self.CONDITION_KEYS & keys)}",
                )

    def test_medium_is_required_for_line_open(self):
        """盲板抽堵必须先声明介质——管线打开的介质是必填。"""
        line_open_def, _ = get_permit_type("line_open")
        medium = next(f for f in resolve_fields(line_open_def) if f["key"] == "medium")
        self.assertTrue(medium["required"], "管线打开的介质应保持必填")

    def test_requires_overrides_required_for_a_type(self):
        """requires 是类型级必填覆盖：通用字段默认非必填，某个类型可单独要它必填。"""
        type_def, _ = get_permit_type("hot_work")
        clone = json.loads(json.dumps(type_def))
        clone["requires"] = {"local_supervisor": True}
        default = next(f for f in resolve_fields(type_def) if f["key"] == "local_supervisor")
        overridden = next(f for f in resolve_fields(clone) if f["key"] == "local_supervisor")
        self.assertFalse(default["required"], "属地监督默认应为非必填")
        self.assertTrue(overridden["required"], "requires 没能把它改成必填")

    def test_para_mode_writes_after_the_label_not_after_the_signature(self):
        """「作业范围、方式…：／签字：　年 月 日 时 分」是两段式格。

        same 模式按整格末尾追加，值会跑到签字区右边；para 只写标签所在的那一段。
        """
        for key, field_key in (("earth_work", "earth_scope"), ("non_routine", "nr_scope")):
            rows = build_permit_rows(key, {field_key: "开挖 2m×3m，深 1.5m"})["rows"]
            row = _label_row(rows, "作业范围、方式(包括深度、面积，并附简图)")
            self.assertIsNotNone(row, f"{key} 找不到「作业范围、方式」那一行")
            text = "".join(cell["text"] for cell in row)
            self.assertIn("：\u3000开挖 2m×3m，深 1.5m", text, f"{key} 的值没接在标签冒号之后")
            self.assertLess(text.index("开挖"), text.index("签字"), f"{key} 的值跑到签字区后面了")

    TICKED = "\u2611"   # ☑
    UNTICKED = "\u25a1"  # □

    def test_check_mode_ticks_symbol_checkbox_options(self):
        """动火作业类型整栏用的是 `<w:sym>` 符号控件（不是「□」字符），同样要能打勾。

        符号控件**没有文字**，票面文本里根本看不到它，所以只能查 XML：
        空框 `00A3` 被换成带勾框 `0052`（同为 Wingdings 2 字体）。
        """
        baseline = _rendered_xml("hot_work", {})
        empty = baseline.count('w:char="00A3"')
        self.assertGreater(empty, 0, "动火模板里没有符号勾选框，本用例前提已变")
        self.assertEqual(baseline.count('w:char="0052"'), 0, "初始就有勾选框，前提已变")

        for chosen in (["焊接"], ["焊接", "气割", "打磨"], ["锤击", "临时用电"]):
            xml = _rendered_xml("hot_work", {"hot_types": chosen})
            self.assertEqual(xml.count('w:char="0052"'), len(chosen), f"{chosen} 打勾数不对")
            self.assertEqual(
                xml.count('w:char="00A3"'), empty - len(chosen), f"{chosen} 勾完后空框数不对"
            )

    def test_check_mode_ignores_symbols_that_are_not_checkboxes(self):
        """「是划√否划×」的表头用的是 Symbol 字体的 √（F0D6），不是勾选框，不能动它。"""
        icon = 'w:char="F0D6"'
        baseline = _rendered_xml("hot_work", {})
        self.assertEqual(
            _rendered_xml("hot_work", {"plan_required": True}).count(icon),
            baseline.count(icon),
            "打勾时误改了表头里的 √ 符号",
        )

    def _assert_ticked(self, text, option, message):
        """勾选符号紧邻该选项即可——模板里「☑是」和「是☑」两种写法都有。"""
        pattern = (
            rf"{self.TICKED}\s{{0,2}}{re.escape(option)}"
            rf"|{re.escape(option)}\s{{0,2}}{self.TICKED}"
        )
        self.assertRegex(text, pattern, message)

    def test_check_mode_ticks_the_chosen_option_only(self):
        """作业类别三选一：勾中项那一个框，另外两个保持空框。"""
        for chosen, others in (("堵盲板", ("抽盲板", "其他")),
                               ("抽盲板", ("堵盲板", "其他")),
                               ("其他", ("堵盲板", "抽盲板"))):
            rows = build_permit_rows("line_open", {"work_category": chosen})["rows"]
            text = "".join(cell["text"] for cell in _label_row(rows, "作业类别"))
            self.assertIn(f"{self.TICKED} {chosen}", text, f"{chosen} 没被勾上")
            self.assertEqual(text.count(self.TICKED), 1, f"{chosen} 只该勾一个框")
            for other in others:
                self.assertIn(f"{self.UNTICKED} {other}", text, f"{other} 不该被打勾")

    def test_check_mode_ticks_yes_or_no(self):
        """「是否编制方案　是□　否□」这类二选一：真值勾「是」、假值勾「否」，且只勾一个。"""
        for key, label, field_key in (
            ("hot_work", "是否编制方案", "plan_required"),
            ("earth_work", "是否编制作业方案", "plan_required"),
            ("road_close", "是否编制作业方案", "plan_required"),
            ("confined_space", "是否附安全工作方案", "attach_plan"),
        ):
            for value in (True, False):
                rows = build_permit_rows(key, {field_key: value})["rows"]
                text = "".join(cell["text"] for cell in _label_row(rows, label))
                self._assert_ticked(
                    text, "是" if value else "否",
                    f"{key}「{label}」填 {value} 时勾错了框",
                )
                self.assertEqual(text.count(self.TICKED), 1, f"{key}「{label}」勾了不止一个框")

    def test_check_mode_falls_back_to_text_when_the_template_has_no_box(self):
        """高处Ⅰ级/Ⅳ级、吊装一级：模板压根没给这几级印框，退化为追加文字，不能丢信息。"""
        for key, level in (("high_work", "Ⅰ级"), ("high_work", "Ⅳ级"), ("lifting", "一级")):
            rows = build_permit_rows(key, {"high_level": level, "lifting_level": level})["rows"]
            text = "".join(cell["text"] for cell in _label_row(rows, "作业等级"))
            self.assertIn(level, text, f"{key} 的 {level} 没上票")
            self.assertNotIn(self.TICKED, text, f"{key} 的 {level} 没有框，不该出现勾选符号")

    def test_overwrite_mode_replaces_placeholder_text(self):
        """「作业申请时间」的值格自带占位「年 月 日 时 分」：要替换，不能叠成两串时间。"""
        for key in ("hot_work", "confined_space", "line_open", "high_work", "lifting",
                    "earth_work", "road_close", "non_routine"):
            rows = build_permit_rows(key, {"apply_time": "2026-10-13 09:20"})["rows"]
            text = "".join(cell["text"] for cell in rows[0])
            self.assertIn("2026-10-13 09:20", text, f"{key} 的申请时间没写上")
            self.assertIsNone(
                re.search(r"年\s{2,}月\s{2,}日\s{2,}时\s{2,}分", text),
                f"{key} 的申请时间占位文字没被替换掉",
            )

    def test_inline_mode_writes_after_its_own_label(self):
        """受限空间一格挤两个标签：值必须各写在自己标签的冒号后面。"""
        rows = build_permit_rows("confined_space", {
            "gas_position": "上部/中部/下部", "space_medium": "含油污水",
        })["rows"]
        text = "".join(cell["text"] for cell in rows[8])
        self.assertRegex(text, r"气体检测部位[：:]\s*上部/中部/下部")
        self.assertRegex(text, r"受限空间原介质名称[：:]\s*含油污水")
        self.assertLess(text.index("上部"), text.index("受限空间原介质名称"), "值写串了位置")

    def test_risk_checkboxes_are_ticked_per_type(self):
        """8 张票的「存在的风险 / 危害因素识别」都是勾选清单（符号框与字符框都有）。"""
        cases = {
            "hot_work": ["火灾", "触电", "中毒"],
            "confined_space": ["窒息", "坠落"],
            "high_work": ["高处坠落/落物", "噪声"],
            "lifting": ["倾覆/坍塌", "恶劣天气"],
            "earth_work": ["坍塌"],
            "road_close": ["泄漏", "落物"],
            "radiation": ["辐射伤害", "其他伤害"],
            "non_routine": ["爆炸", "火灾"],
        }
        for key, chosen in cases.items():
            before = _tick_count(_rendered_xml(key, {}))
            after = _tick_count(_rendered_xml(key, {"risks": chosen}))
            self.assertEqual(after - before, len(chosen), f"{key} 的风险打勾数不对")

    def test_check_mode_does_not_tick_the_neighbouring_column(self):
        """管线打开第 10 行并排两栏（位置示意图 / 作业方案）：谁的值都不能勾到隔壁去。

        两栏标签只差几个字，靠前缀匹配定位时右侧格正是另一栏——先找标签格自身的框才行。
        """
        for values, drawing_option, plan_option in (
            ({"attach_drawing": True, "attach_plan": False}, "是", "否"),
            ({"attach_drawing": False, "attach_plan": True}, "否", "是"),
        ):
            row = build_permit_rows("line_open", values)["rows"][10]
            drawing, plan = row[0]["text"], row[1]["text"]
            self._assert_ticked(drawing, drawing_option, f"{values} 时位置示意图栏勾错了")
            self._assert_ticked(plan, plan_option, f"{values} 时作业方案栏勾错了")
            self.assertEqual(drawing.count(self.TICKED), 1, "位置示意图栏勾了不止一个框")
            self.assertEqual(plan.count(self.TICKED), 1, "作业方案栏勾了不止一个框")

    def test_append_mode_keeps_template_text(self):
        # 「作业时间」右侧格自带"自 年 月 日…"，值应追加在模板文字之后。
        rows = build_permit_rows("hot_work", {"work_time": {"start": "S1", "end": "E1"}})["rows"]
        row = _label_row(rows, "作业时间")
        joined = " ".join(cell["text"] for cell in row)
        self.assertIn("自", joined)
        self.assertIn("S1 至 E1", joined)

    def test_short_label_does_not_steal_the_longer_label_cell(self):
        # 受限空间表里「作业区域所在单位」与「作业区域」同表，短标签必须落在自己那一行。
        values = {"area_unit": "焦化车间", "work_place": "受限空间作业区"}
        rows = build_permit_rows("confined_space", values)["rows"]
        area_row = _label_row(rows, "作业区域所在单位")
        place_row = _label_row(rows, "作业区域")
        self.assertIn("焦化车间", " ".join(cell["text"] for cell in area_row))
        self.assertIn("受限空间作业区", " ".join(cell["text"] for cell in place_row))
        self.assertNotIn("受限空间作业区", " ".join(cell["text"] for cell in area_row))

    def test_blank_values_are_skipped(self):
        rows = build_permit_rows("hot_work", {"apply_unit": "", "hot_place": None})["rows"]
        baseline = build_permit_rows("hot_work", {})["rows"]
        self.assertEqual(
            [[cell["text"] for cell in row] for row in rows],
            [[cell["text"] for cell in row] for row in baseline],
        )


class PermitDocxTests(unittest.TestCase):
    """按模板生成 docx。"""

    def test_render_docx_contains_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = os.path.join(temp_dir, "permit.docx")
            render_permit_docx(
                "hot_work",
                {"apply_unit": "炼油三部", "work_content": "清理 F-0102 人孔"},
                out_path,
            )
            self.assertTrue(os.path.exists(out_path))
            with zipfile.ZipFile(out_path) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("炼油三部", document_xml)
            self.assertIn("清理 F-0102 人孔", document_xml)

    def test_render_docx_keeps_table_row_count(self):
        type_def, _ = get_permit_type("hot_work")
        source_rows = build_permit_rows("hot_work", {})["rows"]
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = os.path.join(temp_dir, "permit.docx")
            render_permit_docx("hot_work", {"apply_unit": "X"}, out_path)
            filled_rows = build_permit_rows("hot_work", {"apply_unit": "X"})["rows"]
        self.assertEqual(len(filled_rows), len(source_rows))
        self.assertEqual(len(source_rows), 43)

    def test_render_docx_does_not_add_paragraphs(self):
        """填入值不得新增段落。

        每填一个字段就多一个段落会让该行变高，整张表累积后溢出到第二页（表现为空白页），
        版面也不再与模板一致。生成件的段落数必须与模板完全相同。
        """
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            values = _sample_values(resolve_fields(type_def))
            with tempfile.TemporaryDirectory() as temp_dir:
                out_path = os.path.join(temp_dir, "permit.docx")
                render_permit_docx(item["key"], values, out_path)
                filled_count = _paragraph_count(_document_xml(out_path))
            source_count = _paragraph_count(_document_xml(get_template_path(type_def)))
            self.assertEqual(
                filled_count, source_count,
                f"{item['name']} 生成后段落数由 {source_count} 变为 {filled_count}，会产生空白页",
            )

    def test_injected_runs_reuse_template_run_properties(self):
        """填入内容必须沿用模板的字体字号，不能落到文档默认样式上。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = os.path.join(temp_dir, "permit.docx")
            render_permit_docx("hot_work", {"apply_unit": "炼油三部"}, out_path)
            document_xml = _document_xml(out_path)
        match = re.search(
            r'<w:r><w:rPr>(?P<rpr>.*?)</w:rPr><w:t xml:space="preserve">炼油三部</w:t>',
            document_xml,
        )
        self.assertIsNotNone(match, "未按模板 run 形式注入内容")
        # sz 21 = 10.5pt，与模板正文一致
        self.assertIn('w:sz w:val="21"', match.group("rpr"))

    def test_rendered_docx_has_no_recommended_style(self):
        """下载得到的许可证不得出现「推荐样式」四个字。"""
        for item in list_permit_types():
            type_def, _ = get_permit_type(item["key"])
            values = _sample_values(resolve_fields(type_def))
            with tempfile.TemporaryDirectory() as temp_dir:
                out_path = os.path.join(temp_dir, "permit.docx")
                render_permit_docx(item["key"], values, out_path)
                document_xml = _document_xml(out_path)
            self.assertNotIn("推荐样式", document_xml, f"{item['name']} 生成的许可证里含「推荐样式」")

    def test_unknown_type_render_raises(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                render_permit_docx("不存在", {}, os.path.join(temp_dir, "x.docx"))


class JsonHandler:
    """与 tests/test_workflow_history.py 一致的简化响应收集器。"""

    def __init__(self):
        self.data = None
        self.status_code = None

    def send_json(self, data, status=200):
        self.data = data
        self.status_code = status


class FileHandler(JsonHandler):
    """能接收二进制响应体的 handler，用于下载接口。"""

    def __init__(self):
        super().__init__()
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status_code = code

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


class PermitApiTests(unittest.TestCase):
    """接口层：类型清单与许可证下载。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.patch = patch.object(file_utils, "get_jobs_dir", return_value=self.temp_dir.name)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp_dir.cleanup()

    def test_permit_types_endpoint(self):
        handler = JsonHandler()
        handle_workflow_permit_types(handler)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.data["status"], "ok")
        self.assertEqual(len(handler.data["types"]), 10)

    def test_download_rejects_non_manual_job(self):
        save_job_application(JOB, {"input_source": "docx", "job_content": "x"})
        handler = JsonHandler()
        handle_workflow_permit_docx(handler, JOB)
        self.assertEqual(handler.status_code, 400)
        self.assertIn("没有可下载的许可证", handler.data["error"])

    def test_download_rejects_bad_job_id(self):
        handler = JsonHandler()
        handle_workflow_permit_docx(handler, "bad")
        self.assertEqual(handler.status_code, 400)

    def test_download_writes_ai_measure_marks_from_permit_json(self):
        """下载的许可证要按 permit.json 里的 AI 判定写出 √/×，「确认人」列留空。"""
        save_job_application(JOB, {
            "input_source": "manual", "permit_type": "high_work",
            "permit_fields": {"applicant": "李强", "work_people": "王明", "high_level": "Ⅱ级"},
        })
        measures = list_safety_measures("high_work")
        decisions = {"source": "ai", "model": "stub", "decided_at": "2026-10-09T00:00:00+00:00",
                     "items": [{"index": m["index"], "needed": m["index"] <= 12} for m in measures]}
        write_json_file(
            os.path.join(get_job_dir(JOB), "permit.json"),
            {"permit_content": {"measure_decisions": decisions}},
        )

        handler = FileHandler()
        handle_workflow_permit_docx(handler, JOB)
        self.assertEqual(handler.status_code, 200)

        with zipfile.ZipFile(io.BytesIO(handler.wfile.getvalue())) as archive:
            filled = archive.read("word/document.xml").decode("utf-8")
        source = _document_xml(get_template_path(get_permit_type("high_work")[0]))
        self.assertEqual(_paragraph_count(filled), _paragraph_count(source))

        marks = [row for row in _table_rows(filled) if len(row) == 4 and row[2] in ("√", "×")]
        self.assertEqual(len(marks), len(measures))
        for position, row in enumerate(marks, 1):
            self.assertEqual(row[2], "√" if position <= 12 else "×")
            self.assertEqual(row[3], "", "「确认人」列必须留空给人工签字")


class P1ManualStageTests(unittest.TestCase):
    """P1 手动填报路径。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.patch = patch.object(file_utils, "get_jobs_dir", return_value=self.temp_dir.name)
        self.patch.start()
        # 单元测试不打真实模型：安全措施判定替换为确定性桩（奇数条判为需要）。
        import agents.p1_permit_agent as p1

        self.measures_patch = patch.object(p1, "_decide_safety_measures", side_effect=self._stub_decide)
        self.measures_patch.start()
        # JSA 也走模型，同样换成确定性桩（真实链路的解析/降级由 test_jsa_analyze 单独测）。
        self.jsa_patch = patch.object(p1, "_build_jsa_result", side_effect=self._stub_jsa)
        self.jsa_patch.start()

    def _run_to_permit(self):
        import agents.p1_permit_agent as p1
        from agents.main_agent import confirm_and_continue
        from agents.workflow import get_stage_result_path, read_json_file
        result = p1.execute_stage(JOB)
        if result.get("error"):
            return result
        self.assertEqual(result["pending_confirmation"]["type"], "jsa_approval")
        self.assertNotIn("permit_content", result)
        with patch("agents.main_agent.STAGE_EXECUTORS", {"P1": p1.execute_stage}):
            confirm_and_continue(JOB, "P1", approval_id=result["pending_confirmation"]["approval_id"])
        return read_json_file(get_stage_result_path(JOB, "p1"))

    @staticmethod
    def _stub_decide(job_id, type_name, context_lines, measures):
        return {
            "source": "ai",
            "model": "stub",
            "decided_at": "2026-10-09T00:00:00+00:00",
            "items": [{"index": m["index"], "needed": m["index"] % 2 == 1} for m in measures],
        }

    @staticmethod
    def _stub_jsa(application, values, type_def, job_id, related=None):
        steps = [
            {"step": f"步骤{index}", "hazard": "危险", "consequence": "后果",
             "level": "高", "control": "措施"}
            for index in range(1, 5)
        ]
        return {
            "source": "ai",
            "type_name": type_def["name"],
            "generated_at": "2026-10-13T00:00:00+00:00",
            "job_name": "测试作业",
            "risk_summary": "桩",
            "pre_checks": ["检查一", "检查二", "检查三"],
            "steps": steps,
            "special_sections": [],
            "emergency": "应急",
            "final_checklist": ["确认一"],
            "main_risks": ["风险一", "风险二", "风险三"],
            "hazards": [
                {"id": f"H-{index:03d}", "description": step["hazard"], "severity": step["level"],
                 "measures": [step["control"]]}
                for index, step in enumerate(steps, 1)
            ],
        }

    def tearDown(self):
        self.jsa_patch.stop()
        self.measures_patch.stop()
        self.patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _application(permit_type, fields=None):
        base = {
            "apply_unit": "炼油三部",
            "area_unit": "焦化车间",
            "applicant": "李强",
            "work_people": "王明",
            "work_place": "焦化 I 装置区",
            "work_content": "清理 F-0102 空气预热器内部积灰",
            "work_time": {"start": "2026-10-09 09:00", "end": "2026-10-09 17:00"},
        }
        base.update(fields or {})
        return {"input_source": "manual", "permit_type": permit_type, "permit_fields": base}

    def test_manual_stage_produces_permit_with_template_rows(self):
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("hot_work", {
            "hot_place": "F-0102 人孔",
            "hot_person": "王明",
            "hot_cert": "P-0881",
            "hot_types": ["焊接"],
            "plan_required": True,
        }))
        result = self._run_to_permit()

        self.assertTrue(result["completed"])
        self.assertIsNone(result.get("error"))
        content = result["permit_content"]
        self.assertEqual(content["permit_title"], "动火作业许可证")
        self.assertEqual(content["permit_type"], "动火作业")
        self.assertEqual(content["job_type"], "动火作业")
        self.assertEqual(content["input_source"], "manual")
        self.assertEqual(len(content["permit_rows"]), 43)
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["pending_confirmation"]["type"], "permit_final_approval")
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir.name, JOB, "permit.json")))

    def test_apply_time_is_filled_by_the_system(self):
        """作业申请时间由系统填（提交那一刻），表单里该字段只读——票面占位被替换。"""
        save_job_application(JOB, self._application("hot_work", {
            "hot_place": "E-201 接管", "hot_types": ["焊接"],
        }))
        import agents.p1_permit_agent as p1

        result = self._run_to_permit()
        values = result["permit_content"]["permit_values"]
        self.assertRegex(values.get("apply_time", ""), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        first_row = "".join(cell["text"] for cell in result["permit_content"]["permit_rows"][0])
        self.assertIn(values["apply_time"], first_row, "申请时间没上票面")
        self.assertIsNone(
            re.search(r"年\s{2,}月\s{2,}日\s{2,}时\s{2,}分", first_row),
            "票面上还留着「年 月 日 时 分」占位",
        )

    def test_related_types_are_recorded_but_not_judged(self):
        """关联作业类型只登记、不判措施——一个作业只出一张票（主类型那张）。

        关联类型的措施取出来是为后续 JSA 报告准备（决定要覆盖哪些专项章节），
        绝不能混进本次的安全措施判定。
        """
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("line_open", {
            "work_category": "抽盲板",
            "pipe_name": "DN600 天然气管道",
            "medium": "含硫化氢天然气",
            "related_works": ["高处作业", "吊装作业"],
        }))
        result = self._run_to_permit()
        content = result["permit_content"]

        related = {item["key"]: item for item in content["related_permit_types"]}
        self.assertEqual(set(related), {"high_work", "lifting"})
        self.assertEqual(related["high_work"]["name"], "高处作业")
        self.assertEqual(len(related["high_work"]["measures"]), 20)
        self.assertEqual(len(related["lifting"]["measures"]), 22)
        for item in related.values():
            self.assertNotIn("needed", item, "关联类型不该带判定结果")

        # 票面措施仍只判主类型（管线打开 11 条）
        self.assertEqual(len(content["measure_decisions"]["items"]), 11)
        self.assertEqual(len(content["safety_measures"]), 11)
        self.assertEqual(result["data_origin"]["related_permit_types"], "manual_form")

    def test_related_works_is_not_required(self):
        """关联作业类型非必填：不填不算缺项，登记为空列表。"""
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("hot_work", {
            "hot_place": "F-0102 人孔", "hot_person": "王明", "hot_cert": "P-0881",
            "hot_types": ["焊接"], "plan_required": True,
        }))
        result = self._run_to_permit()
        self.assertNotIn("关联的其他作业类型", result["missing_fields"])
        self.assertEqual(result["permit_content"]["related_permit_types"], [])

    def test_related_works_ignores_self_and_unknown_names(self):
        """关联作业类型里的主类型自身、无法识别的名字、重复项一律跳过且不报错。"""
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("hot_work", {
            "hot_place": "F-0102 人孔", "hot_person": "王明", "hot_cert": "P-0881",
            "hot_types": ["焊接"], "plan_required": True,
            "related_works": ["动火作业", "查无此作业", "高处作业", "高处作业"],
        }))
        result = self._run_to_permit()
        related = result["permit_content"]["related_permit_types"]
        self.assertEqual([item["key"] for item in related], ["high_work"])

    def test_manual_temp_power_judges_all_measures(self):
        """临时用电 ○ 网格接入后：手动填报走 AI 判定 23 条，不再落到 no-measures。

        接入前该类型模板侧解析不出措施（`list_safety_measures` 返回 []），
        `_decide_safety_measures` 直接走 `no-measures`、一条都不判。这条用例守住端到端。
        """
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("temp_power", {
            "permit_no": "LD-2026-0001", "voltage": "380",
        }))
        result = self._run_to_permit()

        self.assertTrue(result["completed"])
        self.assertEqual(result["missing_fields"], [])
        content = result["permit_content"]
        self.assertEqual(content["permit_title"], "临时用电许可证")
        decisions = content["measure_decisions"]
        self.assertEqual(decisions["source"], "ai", "临时用电必须走 AI 判定，不能是 no-measures")
        self.assertEqual(len(decisions["items"]), 23)
        self.assertEqual([d["index"] for d in decisions["items"]], list(range(1, 24)))

    def test_missing_fields_only_reflect_the_form(self):
        import agents.p1_permit_agent as p1

        fields = {"hot_person": "王明", "hot_types": ["焊接"], "plan_required": True}
        save_job_application(JOB, self._application("hot_work", fields))
        result = self._run_to_permit()

        # 只报该类型表单里必填但未填的项，不掺入表单未采集的下游字段
        self.assertEqual(result["missing_fields"], ["动火部位", "动火人证书编号"])

    def test_bool_false_is_not_treated_as_missing(self):
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("confined_space", {
            "space_name": "F-0102 内部",
            "attach_plan": False,
        }))
        result = self._run_to_permit()
        self.assertEqual(result["missing_fields"], [])

    def test_unknown_permit_type_fails_the_stage(self):
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("不存在的类型"))
        result = self._run_to_permit()

        self.assertFalse(result["completed"])
        self.assertIn("未知的作业类型", result["error"])
        self.assertNotIn("pending_confirmation", result)

    def test_manual_does_not_use_the_tool_chain(self):
        import agents.p1_permit_agent as p1

        save_job_application(JOB, self._application("hot_work", {
            "hot_place": "F-0102 人孔",
            "hot_person": "王明",
            "hot_cert": "P-0881",
            "hot_types": ["焊接"],
            "plan_required": True,
        }))
        with patch.object(p1, "run_permit_agent_with_hitl") as agent_call:
            result = self._run_to_permit()
        agent_call.assert_not_called()
        self.assertTrue(result["completed"])


class SafetyMeasureTests(unittest.TestCase):
    """安全措施清单解析、AI 判定接入与 √/× 回填。"""

    # 与各模板的安全措施表 1:1 对齐。临时用电是「符合划○」的 3 列网格，
    # 2026-10-12 接入后同样纳入（解析器按表头形态分支取列）。
    EXPECTED_COUNTS = {
        "hot_work": 15, "confined_space": 15, "line_open": 11, "high_work": 20, "lifting": 22,
        "earth_work": 13, "road_close": 7, "radiation": 5, "non_routine": 13, "temp_power": 23,
    }

    def test_measure_list_matches_each_template(self):
        for item in list_permit_types():
            measures = list_safety_measures(item["key"])
            self.assertEqual(
                len(measures), self.EXPECTED_COUNTS[item["key"]],
                f"{item['name']} 解析出的措施条数与模板不符",
            )
            self.assertEqual([m["index"] for m in measures], list(range(1, len(measures) + 1)))
            for measure in measures:
                self.assertTrue(measure["text"], f"{item['name']} 出现空措施")
                self.assertFalse(
                    measure["text"].startswith(("其他安全措施", "其它安全措施")),
                    f"{item['name']} 未排除结尾的自由填写行",
                )

    def test_marks_land_in_mark_column_and_layout_is_unchanged(self):
        """√/× 必须写进「是划√否划×」列，且不得新增段落（否则会多出空白页）。

        临时用电是「符合划○」的 3 列网格、没有记号专列，由下面那条用例单独覆盖。
        """
        for item in list_permit_types():
            if item["key"] == "temp_power":
                continue
            measures = list_safety_measures(item["key"])
            if not measures:
                continue
            type_def, _ = get_permit_type(item["key"])
            decisions = {"source": "ai", "items": [
                {"index": m["index"], "needed": m["index"] % 2 == 1} for m in measures
            ]}
            with tempfile.TemporaryDirectory() as temp_dir:
                out_path = os.path.join(temp_dir, "permit.docx")
                render_permit_docx(
                    item["key"], _sample_values(resolve_fields(type_def)), out_path, decisions
                )
                filled = _document_xml(out_path)
            source = _document_xml(get_template_path(type_def))

            self.assertEqual(
                _paragraph_count(filled), _paragraph_count(source),
                f"{item['name']} 回填标记后段落数变化，版面会与模板不一致",
            )
            marked = 0
            for row in _table_rows(filled):
                if len(row) == 4 and row[2] in ("√", "×"):
                    marked += 1
                    self.assertEqual(row[3], "", f"{item['name']}「确认人」列必须留空给人工签字")
            self.assertEqual(marked, len(measures), f"{item['name']} 打标行数不符")

    def test_temp_power_grid_marks_use_conform_symbol(self):
        """临时用电 ○ 网格：记号写 ○/× 且落在正确的项格里（一格一项，一行可多项）。"""
        measures = list_safety_measures("temp_power")
        self.assertEqual(len(measures), 23)
        type_def, _ = get_permit_type("temp_power")
        # 每 3 条判一条 false，其余 true —— 一行 4 项时必须逐格落对
        expected = {m["index"]: m["index"] % 3 != 0 for m in measures}
        decisions = {"source": "ai", "items": [
            {"index": i, "needed": needed} for i, needed in expected.items()
        ]}
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = os.path.join(temp_dir, "temp_power.docx")
            render_permit_docx("temp_power", _sample_values(resolve_fields(type_def)), out_path, decisions)
            filled = _document_xml(out_path)
        source = _document_xml(get_template_path(type_def))

        self.assertEqual(
            _paragraph_count(filled), _paragraph_count(source),
            "回填 ○/× 后段落数变化，版面会与模板不一致",
        )
        # 表头那句「符合划‘○’，识别无此项划‘×’」本身带记号，必须排除掉；
        # 项格里的前导勾选框（XML 里是 <w:sym>，快照渲染成「(」或「□」）要去掉再比。
        marked = {}
        for row in _table_rows(filled):
            for cell in row:
                text = re.sub(r"^[（(□\s]+", "", cell.strip()).strip()
                if text.startswith("检查确认"):
                    continue
                if text.endswith(("○", "×")):
                    marked[text[:-1].strip()] = text[-1]
        self.assertEqual(len(marked), len(measures), "打记号的项数与措施条数不符")
        for measure in measures:
            self.assertIn(measure["text"], marked, f"第 {measure['index']} 条没有打记号")
            mark = marked[measure["text"]]
            self.assertEqual(mark, "○" if expected[measure["index"]] else "×")
        # 记号必须是 ○，不能沿用 √
        self.assertNotIn("√", "".join(marked.values()))

    def test_no_marks_when_decision_source_is_unavailable(self):
        type_def, _ = get_permit_type("hot_work")
        measures = list_safety_measures("hot_work")
        decisions = {"source": "unavailable", "error": "model down", "items": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            out_path = os.path.join(temp_dir, "permit.docx")
            render_permit_docx("hot_work", {}, out_path, decisions)
            filled = _document_xml(out_path)
        marks = [row for row in _table_rows(filled) if len(row) == 4 and row[2] in ("√", "×")]
        self.assertEqual(marks, [])
        self.assertEqual(measures and decision_map(decisions), {})

    def test_parser_rejects_invalid_payloads(self):
        import agents.p1_permit_agent as p1

        with self.assertRaises(ValueError):        # 不是 JSON
            p1._parse_measure_decisions("模型今天不想输出 JSON", 2)
        with self.assertRaises(ValueError):        # 条数不符
            p1._parse_measure_decisions(json.dumps({"items": [{"index": 1, "needed": True}]}), 2)
        with self.assertRaises(ValueError):        # 序号缺失
            p1._parse_measure_decisions(json.dumps({"items": [
                {"index": 1, "needed": True}, {"index": 3, "needed": True}]}), 2)
        with self.assertRaises(ValueError):        # 布尔非法
            p1._parse_measure_decisions(json.dumps({"items": [
                {"index": 1, "needed": "也许"}, {"index": 2, "needed": True}]}), 2)

    def test_parser_accepts_code_fence_and_unambiguous_literals(self):
        import agents.p1_permit_agent as p1

        payload = "```json\n" + json.dumps({"items": [
            {"index": 1, "needed": "true"}, {"index": 2, "needed": 0}]}) + "\n```"
        self.assertEqual(
            p1._parse_measure_decisions(payload, 2),
            [{"index": 1, "needed": True}, {"index": 2, "needed": False}],
        )

    def test_ai_failure_falls_back_to_manual(self):
        import agents.p1_permit_agent as p1

        measures = list_safety_measures("hot_work")
        with patch.object(p1, "create_chat_model_with_logging", side_effect=RuntimeError("model down")), \
                patch.object(p1, "push_websocket_log"):
            decisions = p1._decide_safety_measures("J", "动火作业", [], measures)
        self.assertEqual(decisions["source"], "unavailable")
        self.assertEqual(decisions["items"], [])
        self.assertIn("model down", decisions["error"])
        # 失败时不得写任何勾
        self.assertEqual(decision_map(decisions), {})

    def test_docx_catalog_conversion_skips_free_text_row(self):
        import agents.p1_permit_agent as p1

        catalog = [
            {"sequence": 1, "description": "措施一", "selected": True},
            {"sequence": 2, "description": "措施二", "selected": False},
            {"sequence": 3, "description": "其他安全措施：硬隔离。编制人：李建国", "selected": False},
        ]
        measures = p1._catalog_to_measures(catalog)
        self.assertEqual([m["index"] for m in measures], [1, 2])
        self.assertEqual([m["text"] for m in measures], ["措施一", "措施二"])

        decisions = {"source": "ai", "items": [
            {"index": 1, "needed": True}, {"index": 2, "needed": False}]}
        annotated = p1._annotate_catalog(catalog, decisions)
        self.assertTrue(annotated[0]["ai_needed"])
        self.assertFalse(annotated[1]["ai_needed"])
        self.assertNotIn("ai_needed", annotated[2])


if __name__ == "__main__":
    unittest.main()
