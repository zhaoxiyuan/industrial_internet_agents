"""
许可证模板：读取 data/permit_templates 下的类型定义与模板 docx，
按「标签格 → 值格」规则把填写值写进表格，输出结构化行或生成 docx。

写入方式（字段的 mode）：
- fill   写入标签单元格右侧紧邻的格子（该格为空时）；右侧格被占用时先试「表头行 + 值行」
         版式的正下方空格，都没有才退化为追加到标签格
- below  明确写入标签格**正下方**的同列空格（按 gridSpan 展开的网格列对齐）。
         用于「分组表头行 + 子表头行 + 值行」的合并结构——此时标签的右侧格可能已经是
         另一组的值格（如「编号」右边是「垫片规格」的值格），按 fill 猜必错。
- append 追加到标签单元格右侧紧邻的格子
- same   追加到标签单元格本身（用于整行只有标签格的复合行）
- para   追加到「标签所在的那一段」末尾，而不是整格的最后一段。用于「标签：／签字：…」
         两段式复合格——按整格末尾追加会把值写到签字区后面（动土、非常规的作业范围行）。
- check  不打文字，把票面自带的「□」改成「☑」。用于模板印好的勾选项（作业类别、是否编制
         方案、作业等级）；该选项票面没有框时（高处Ⅰ级、吊装一级）退化为 same。
- overwrite 清掉目标格里已有的文字再写值。用于值格自带占位文字的栏位
         （「作业申请时间」右边印着「年 月 日 时 分」），追加会变成两串时间叠在一起。
- inline 在格内「标签（含冒号）」之后插入值，而不是段末或整格末尾。用于**一格挤两个
         标签**的栏位（受限空间「气体检测部位：___ 受限空间原介质名称：___」）。

只处理表格结构，不引入新依赖（zipfile + 正则改写 document.xml，与 web/api/docx_permit.py 的解析方式一致）。
"""
import json
import os
import re
import zipfile

CONFIG_FILENAME = "permit_templates.json"

MODE_FILL = "fill"
MODE_BELOW = "below"
MODE_APPEND = "append"
MODE_SAME = "same"
MODE_PARA = "para"
MODE_CHECK = "check"
MODE_OVERWRITE = "overwrite"
MODE_INLINE = "inline"

# 票面的勾选框有两种形态，打勾要两种都认：
#   1. <w:t> 里的普通字符「□」（占一个字符）；
#   2. <w:sym> 符号控件——没有文字，但票面上就是一个框（动火作业类型、危害识别等栏全用它）。
BOX_CHAR = "\u25a1"      # □ 未勾选（字符形态）
CHECK_MARK = "\u2611"    # ☑ 已勾选（字符形态）
_EMPTY_BOX_CODES = {"00A3", "A3", "F0A3"}   # Wingdings 2 的空方框
TICKED_SYM = '<w:sym w:font="Wingdings 2" w:char="0052"/>'   # 同字体的带勾方框
_SYM_RE = re.compile(r"<w:sym[^>]*/>")
_CELL_TOKEN_RE = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>|<w:sym[^>]*/>")

_TR_RE = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
_TC_RE = re.compile(r"<w:tc>.*?</w:tc>", re.S)
_T_RE = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")
_TBL_RE = re.compile(r"<w:tbl[ >].*?</w:tbl>", re.S)
_GS_RE = re.compile(r"<w:gridSpan[^>]*w:val=\"(\d+)\"")
_P_RE = re.compile(r"<w:p(?:\s[^>]*)?>.*?</w:p>|<w:p(?:\s[^>]*)?/>", re.S)
_P_OPEN_RE = re.compile(r"<w:p(?:\s[^>]*)?/?>")
_R_RE = re.compile(r"<w:r(?:\s[^>]*)?>.*?</w:r>", re.S)
_RPR_RE = re.compile(r"<w:rPr>.*?</w:rPr>", re.S)


# ============================================================
# 目录与配置
# ============================================================

def get_templates_dir() -> str:
    """许可证模板目录：<项目根>/data/permit_templates"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "permit_templates")


def get_template_path(type_def: dict) -> str:
    return os.path.join(get_templates_dir(), type_def["template"])


def load_config() -> dict:
    with open(os.path.join(get_templates_dir(), CONFIG_FILENAME), encoding="utf-8") as fh:
        return json.load(fh)


def resolve_fields(type_def: dict, config: dict = None) -> list:
    """通用字段（去掉本类型没有的）+ 本类型的额外字段，按表单顺序返回。"""
    config = config or load_config()
    drop = set(type_def.get("drop") or [])
    labels = type_def.get("labels") or {}
    modes = type_def.get("modes") or {}
    requires = type_def.get("requires") or {}

    fields = []
    for field in config["common_fields"]:
        if field["key"] in drop:
            continue
        merged = dict(field)
        if field["key"] in labels:
            merged["label"] = labels[field["key"]]
        if field["key"] in modes:
            merged["mode"] = modes[field["key"]]
        if field["key"] in requires:
            merged["required"] = bool(requires[field["key"]])
        fields.append(merged)
    fields.extend(type_def.get("extra_fields") or [])
    return fields


# 「关联的其他特殊作业、非常规作业」是唯一一个候选项需要动态生成的字段：
# 它的可选项就是**除本类型之外**的其他作业类型名。配置里 options 留空，由
# list_permit_types() 按当前类型补齐；票面写值走通用的 multiselect 分支。
RELATED_WORKS_KEY = "related_works"


def list_permit_types() -> list:
    """给前端的类型清单：类型 + 该类型的表单字段定义。"""
    config = load_config()
    all_names = [t["name"] for t in config["types"]]
    types = []
    for type_def in config["types"]:
        fields = []
        for field in resolve_fields(type_def, config):
            options = field.get("options") or []
            if field["key"] == RELATED_WORKS_KEY:
                # 一次作业不可能把自己列为自己的「关联作业」。
                options = [name for name in all_names if name != type_def["name"]]
            fields.append({
                "key": field["key"],
                "label": field.get("form_label") or field.get("label"),
                "type": field.get("type", "text"),
                "required": bool(field.get("required")),
                "options": options,
            })
        types.append({
            "key": type_def["key"],
            "name": type_def["name"],
            "title": type_def["title"],
            "fields": fields,
        })
    return types


def get_permit_type(key_or_name: str):
    """按 key / 名称 / 标题查找类型定义，返回 (type_def, config)；找不到返回 (None, config)。"""
    config = load_config()
    for type_def in config["types"]:
        if key_or_name in (type_def["key"], type_def["name"], type_def["title"]):
            return type_def, config
    return None, config


# ============================================================
# docx 表格读写
# ============================================================

def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _unescape(text: str) -> str:
    return (
        str(text)
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def _cell_text(cell_xml: str) -> str:
    return _unescape("".join(_T_RE.findall(cell_xml))).strip()


def _is_blank(cell_xml: str) -> bool:
    return not _cell_text(cell_xml)


def _table_xml(document_xml: str) -> str:
    match = _TBL_RE.search(document_xml)
    if not match:
        raise ValueError("模板中未找到表格")
    return match.group(0)


def _run_properties(cell_xml: str) -> str:
    """取单元格里第一个 run 的 <w:rPr>，让填入文本沿用模板自己的字体字号。"""
    for run in _R_RE.findall(cell_xml):
        match = _RPR_RE.search(run)
        if match:
            return match.group(0)
    return ""


def _inject(cell_xml: str, text: str, rpr: str = "") -> str:
    """把文本作为新的 run 写进单元格内**已有**的最后一个段落。

    关键：**不新增段落**。每填一个字段多一个段落就会让该行变高，整张表累积后溢出到
    第二页（表现为空白页）；同时沿用该段落原有的 pPr（对齐、行距）与给定的 rPr，
    使填入内容的排版与模板一致。
    """
    run = f'<w:r>{rpr}<w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'
    paragraphs = list(_P_RE.finditer(cell_xml))
    if not paragraphs:
        return cell_xml.replace("</w:tc>", f"<w:p>{run}</w:p></w:tc>", 1)
    last = paragraphs[-1]
    paragraph = last.group(0)
    opening = _P_OPEN_RE.match(paragraph)
    if opening and opening.group(0).endswith("/>"):
        # 空段落写成自闭合形式（<w:p/>），补上内容后展开为普通段落
        new_paragraph = opening.group(0)[:-2] + ">" + run + "</w:p>"
    else:
        new_paragraph = paragraph[: -len("</w:p>")] + run + "</w:p>"
    return cell_xml[: last.start()] + new_paragraph + cell_xml[last.end():]


def _inject_into_paragraph(cell_xml: str, label: str, text: str, rpr: str = ""):
    """把文本追加到**标签所在段落**末尾；找不到该段落返回 ``None``。

    动土、非常规的「作业范围、方式…：」格是两段式——第一段是标签，第二段是
    「签字：　年　月　日　时　分」。按整格末尾追加（same）会把值写到签字区右边。
    """
    label = (label or "").strip()
    if not label:
        return None
    for match in _P_RE.finditer(cell_xml):
        paragraph = match.group(0)
        if label in _cell_text(paragraph):
            return (
                cell_xml[: match.start()]
                + _inject(paragraph, text, rpr)
                + cell_xml[match.end():]
            )
    return None


def _box_index(text: str, option: str, gap: int = 3):
    """找到「属于该选项」的那个 □ 的下标；找不到返回 ``None``。

    取**离选项最近**的那个框，且框与选项之间只允许空白（至多 ``gap`` 个）——光看
    「框在前还是在后」会认错：

    - 「□是　□否」里，「是」的框在左边紧挨着（距离 0），不是右边隔了两个空格的那个；
    - 「是□　否□」里，「否」的框在右边紧挨着，不会被左边的框抢走；
    - 「Ⅰ级　　　□Ⅱ级」里，「Ⅰ级」与最近的框隔着一大段空白，判定为**没有**它的框
      （模板没给 Ⅰ级 印框），交给上层退化为追加文字。
    """
    best, best_gap = None, None
    for found in re.finditer(re.escape(option), text):
        start, end = found.span()
        # 先左后右：两侧一样近时取左边的框——模板里选项前的框才是它自己的。
        # 「□锤击□临时用电」里「锤击」左右各一个框（都紧邻），左边那个才是它的。
        for step in (-1, 1):
            cursor = end if step > 0 else start - 1
            skipped = 0
            while 0 <= cursor < len(text) and text[cursor].isspace() and skipped <= gap:
                cursor += step
                skipped += 1
            if 0 <= cursor < len(text) and text[cursor] == BOX_CHAR and skipped <= gap:
                if best_gap is None or skipped < best_gap:
                    best, best_gap = cursor, skipped
    return best


def _cell_tokens(cell_xml: str) -> list:
    """拆出格子里「参与文本定位的片段」：``(种类, 起始, 结束, 文本)``。

    ``<w:sym>`` 符号控件**没有文字**，可它在票面上就是一个勾选框，所以给它记一个
    「□」占位——否则像「动火作业类型」这种整栏都用符号控件的，压根找不到框在哪。
    已勾选的符号记成「☑」，这样它不会被当成候选框再勾一次。
    """
    tokens = []
    for match in _CELL_TOKEN_RE.finditer(cell_xml):
        raw = match.group(0)
        if raw.startswith("<w:t"):
            tokens.append(("t", match.start(1), match.end(1), _unescape(match.group(1))))
            continue
        code = re.search(r'w:char="([^"]*)"', raw)
        code = code.group(1).upper() if code else ""
        tokens.append(
            ("sym", match.start(), match.end(), BOX_CHAR if code in _EMPTY_BOX_CODES else CHECK_MARK)
        )
    return tokens


def _tick_box(cell_xml: str, option: str, mark: str = CHECK_MARK):
    """给格子里某个选项打勾；格子里没有这个选项的框则返回 ``None``。

    选项文字与框常被拆进不同 run（「是」「□」各一个，符号控件更是独立 run），所以先在
    整格拼出的文本上定位，再映射回具体那个 token 改——只在单个 run 里找会漏。
    """
    option = (option or "").strip()
    if not option:
        return None
    tokens = _cell_tokens(cell_xml)
    index = _box_index("".join(token[3] for token in tokens), option)
    if index is None:
        return None
    cursor = 0
    for kind, start, end, text in tokens:
        if cursor <= index < cursor + len(text):
            if kind == "sym":
                return cell_xml[:start] + TICKED_SYM + cell_xml[end:]
            offset = index - cursor
            updated = text[:offset] + mark + text[offset + 1:]
            return cell_xml[:start] + _escape(updated) + cell_xml[end:]
        cursor += len(text)
    return None


def _check_options(field: dict, raw) -> list:
    """勾选模式要**逐项**打勾，所以不能先拼成「、」分隔的一整串。"""
    if raw is None or raw == "":
        return []
    if field.get("type") == "bool":
        return ["是" if _format_value(field, raw) == "是" else "否"]
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw).strip()]


def _overwrite_cell(cell_xml: str, text: str, rpr: str = "") -> str:
    """清掉格内已有文字，只留新值——用于值格自带占位文字的栏位。

    「作业申请时间」右侧印着「年 月 日 时 分」，追加写入会变成
    `年 月 日 时 分　2026-10-13 09:00` 两串时间叠在一起。

    只清**最后一个段落**里的 run 并原地写值：不新增段落，行高不变。
    """
    run = f'<w:r>{rpr}<w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'
    paragraphs = list(_P_RE.finditer(cell_xml))
    if not paragraphs:
        return cell_xml.replace("</w:tc>", f"<w:p>{run}</w:p></w:tc>", 1)
    last = paragraphs[-1]
    paragraph = re.sub(r"<w:r[ >].*?</w:r>", "", last.group(0), flags=re.S)
    opening = _P_OPEN_RE.match(paragraph)
    if opening and opening.group(0).endswith("/>"):
        paragraph = opening.group(0)[:-2] + ">" + run + "</w:p>"
    else:
        paragraph = paragraph[: -len("</w:p>")] + run + "</w:p>"
    return cell_xml[: last.start()] + paragraph + cell_xml[last.end():]


def _inject_after(cell_xml: str, label: str, text: str, rpr: str = ""):
    """在格内「标签（含冒号）」之后插入值；找不到标签返回 ``None``。

    受限空间有一格挤着两个标签：「气体检测部位：____ 受限空间原介质名称：____」。
    这种情况 para（写整段末尾）分不开，必须各自写在自己标签后面。
    """
    label = (label or "").strip()
    if not label:
        return None
    tokens = _cell_tokens(cell_xml)
    full = "".join(token[3] for token in tokens)
    start = full.find(label)
    if start < 0:
        return None
    cursor = start + len(label)
    while cursor < len(full) and full[cursor] in "：: \t\u3000":
        cursor += 1
    accumulated = 0
    for kind, token_start, token_end, piece in tokens:
        if accumulated <= cursor <= accumulated + len(piece):
            if kind != "t":
                return None
            offset = cursor - accumulated
            updated = piece[:offset] + text + piece[offset:]
            return cell_xml[:token_start] + _escape(updated) + cell_xml[token_end:]
        accumulated += len(piece)
    return None


_SIGN_CELL_RE = re.compile(r"[（(]\s*签字\s*[)）]")


def _locate_label(rows: list, label: str, skip_rows=None, contains: bool = False):
    """定位标签单元格，返回 (行下标, 格下标)；找不到返回 (-1, -1)。

    先在**全表**范围找精确匹配，找不到再找前缀匹配。必须全表两轮扫，否则同一行里
    靠前的长标签（如「作业区域所在单位」）会抢走短标签（「作业区域」）要填的格子。

    ``skip_rows`` 是要跳过的行下标集合（见 ``_measure_row_indexes``）。**安全措施表
    那一段必须整体排除**：措施正文是整句，前缀匹配极易命中（吊装的「作业高度」就
    撞上了措施「作业高度和转臂范围内无架空线路。」），一旦把值写进措施行，记号列
    与行结构都被破坏，打标随之错位。

    签字栏同样不得认领：「监护人（签字）：」这类格子是**现场人工签署**位，系统代填
    等于替人签名。例外是标签自身带「签字」的字段（动火的「动火人（签字）」），
    它填的是姓名，本来就该上票。
    """
    label = (label or "").strip()
    if not label:
        return -1, -1
    skip_rows = skip_rows or set()
    own_signature = "签字" in label

    def claimable(text: str) -> bool:
        return own_signature or not _SIGN_CELL_RE.search(text)

    cells_per_row = [_TC_RE.findall(row) for row in rows]
    for row_index, row_cells in enumerate(cells_per_row):
        if row_index in skip_rows:
            continue
        for cell_index, cell in enumerate(row_cells):
            if _cell_text(cell) == label and claimable(_cell_text(cell)):
                return row_index, cell_index
    for row_index, row_cells in enumerate(cells_per_row):
        if row_index in skip_rows:
            continue
        for cell_index, cell in enumerate(row_cells):
            if _cell_text(cell).startswith(label) and claimable(_cell_text(cell)):
                return row_index, cell_index
    if contains:
        # 兜底：标签在格中间（受限空间「气体检测部位：… 受限空间原介质名称：…」一格两标签）。
        # 只给 inline 模式用——它本来就要靠「标签在格内某处」来定位插入点。
        for row_index, row_cells in enumerate(cells_per_row):
            if row_index in skip_rows:
                continue
            for cell_index, cell in enumerate(row_cells):
                text = _cell_text(cell)
                if label in text and claimable(text):
                    return row_index, cell_index
    return -1, -1


def _measure_row_indexes(table_xml: str) -> set:
    """安全措施表占用的行下标集合——填基本信息时整体绕开这一段。"""
    rows = _TR_RE.findall(table_xml)
    indexes = set()
    for row_xml, _cell_index, _text, _label in _measure_rows(table_xml):
        for index, row in enumerate(rows):
            if row == row_xml:
                indexes.add(index)
                break
    return indexes


def _grid_offsets(row_xml: str) -> list:
    """该行每个格子的起始网格列号（按 gridSpan 累加）。

    合并单元格让「第 n 个格」在上下两行不是同一列——管线打开的参数区就是
    「4 组表头 / 8 个子列」，按下标取下方格必然错位。
    """
    offsets, cursor = [], 0
    for cell in _TC_RE.findall(row_xml):
        match = _GS_RE.search(cell)
        offsets.append(cursor)
        cursor += int(match.group(1)) if match else 1
    return offsets


def _blank_cell_below(rows: list, row_index: int, cell_index: int):
    """「表头行 + 值行」版式里标签正下方的空格，取不到返回 None。

    管线打开的参数行就是这样：第 8 行印「介质/温度/压力/材质/规格/编号」六个表头，
    第 9 行才是同列的空值格——改写右侧格或标签格都会把值印到表头上。

    按**网格列**而不是下标对齐：标签行与值行的格数常常不等（合并单元格），
    下标相同并不代表同属一列。
    """
    if row_index + 1 >= len(rows):
        return None
    here, below = rows[row_index], rows[row_index + 1]
    here_cells, below_cells = _TC_RE.findall(here), _TC_RE.findall(below)
    if not (0 <= cell_index < len(here_cells)):
        return None
    start = _grid_offsets(here)[cell_index]
    for index, offset in enumerate(_grid_offsets(below)):
        if offset == start and _is_blank(below_cells[index]):
            return below_cells[index]
    return None


def _format_value(field: dict, raw) -> str:
    """按字段类型把填写值转成写进表格的文本。"""
    if raw is None:
        return ""
    field_type = field.get("type")
    if field_type == "bool":
        if isinstance(raw, str):
            return "是" if raw.strip() in ("是", "true", "True", "yes", "1") else "否"
        return "是" if raw else "否"
    if field_type == "time_range" and isinstance(raw, dict):
        start = str(raw.get("start") or "").strip()
        end = str(raw.get("end") or "").strip()
        if not start and not end:
            return ""
        return f"{start} 至 {end}".strip()
    if isinstance(raw, (list, tuple, set)):
        return "、".join(str(item).strip() for item in raw if str(item).strip())
    return str(raw).strip()


def fill_table_xml(table_xml: str, fields: list, values: dict) -> str:
    """按字段顺序把填写值写进表格，返回改写后的表格 XML。"""
    # 安全措施表那一段不参与标签匹配（详见 _locate_label 的 skip_rows）。
    measure_rows = _measure_row_indexes(table_xml)
    for field in fields:
        text = _format_value(field, (values or {}).get(field["key"]))
        if not text:
            continue
        mode = field.get("mode") or MODE_FILL

        rows = _TR_RE.findall(table_xml)
        row_index, cell_index = _locate_label(
            rows, field["label"], measure_rows, contains=(mode == MODE_INLINE)
        )
        if row_index < 0:
            continue
        row = rows[row_index]
        cells = _TC_RE.findall(row)
        label_cell = cells[cell_index]
        right = cells[cell_index + 1] if cell_index + 1 < len(cells) else None

        if mode == MODE_PARA:
            # 只写标签所在的那一段，别碰同格里的签字区。
            rpr = _run_properties(label_cell)
            updated = _inject_into_paragraph(label_cell, field["label"], "\u3000" + text, rpr)
            target_cell = updated if updated is not None else _inject(label_cell, "\u3000" + text, rpr)
            table_xml = table_xml.replace(
                row, row.replace(label_cell, target_cell, 1), 1
            )
            continue

        if mode == MODE_OVERWRITE:
            # 值格自带占位文字（「年 月 日 时 分」）：清掉再写，不能追加。
            target = right if right is not None else label_cell
            rpr = _run_properties(target) or _run_properties(label_cell)
            table_xml = table_xml.replace(
                row, row.replace(target, _overwrite_cell(target, text, rpr), 1), 1
            )
            continue

        if mode == MODE_INLINE:
            # 一格挤两个标签：各自写在自己标签的冒号后面。
            inserted = _inject_after(label_cell, field["label"], text, _run_properties(label_cell))
            if inserted is not None:
                table_xml = table_xml.replace(row, row.replace(label_cell, inserted, 1), 1)
                continue
            mode = MODE_SAME   # 找不到该标签的落点，退回追加文字，别丢信息

        if mode == MODE_CHECK:
            # 候选范围：标签格自身（「是否编制方案　是□　否□」）、右侧格（「作业类别 │ □堵盲板…」），
            # 以及**下一行**——高处/吊装的「危害因素识别：」独占一行，勾选项排在下—行。
            # 顺序不能反：管线打开第 10 行并排「位置示意图 / 作业方案」两栏，先试右侧会把勾
            # 打到隔壁那一栏去。
            options = _check_options(field, (values or {}).get(field["key"]))
            hosts = [(label_cell, row)]
            if right is not None:
                hosts.append((right, row))
            if row_index + 1 < len(rows) and (row_index + 1) not in measure_rows:
                hosts.extend(
                    (cell, rows[row_index + 1]) for cell in _TC_RE.findall(rows[row_index + 1])
                )
            ticked = None
            for host, host_row in hosts:
                current, hit = host, False
                for option in options:
                    next_xml = _tick_box(current, option)
                    if next_xml is not None:
                        current, hit = next_xml, True
                if hit:
                    ticked = (host, host_row, current)
                    break
            if ticked is not None:
                host, host_row, current = ticked
                table_xml = table_xml.replace(host_row, host_row.replace(host, current, 1), 1)
                continue
            # 票面没给这个选项印框（高处Ⅰ级、吊装一级）：退回追加文字，别把值丢掉。
            mode = MODE_SAME

        target = None
        if mode == MODE_BELOW:
            below = _blank_cell_below(rows, row_index, cell_index)
            if below is not None:
                target, row = below, rows[row_index + 1]
        if target is None:
            if mode == MODE_SAME:
                target = label_cell
            elif right is not None and (mode == MODE_APPEND or _is_blank(right)):
                target = right
            elif mode == MODE_FILL:
                # 右侧格不存在（标签是本行末格）或已被模板文字占用（如「作业时间」行
                # 自带「自 年 月 日…」）：先试「表头行 + 值行」版式的正下方空格，
                # 都不行才退化为追加到标签格。append 语义不受影响，它本来就追加在
                # 模板文字之后。
                below = _blank_cell_below(rows, row_index, cell_index)
                if below is not None:
                    target, row = below, rows[row_index + 1]
                else:
                    target = label_cell
            else:
                target = label_cell

        # 目标格已有模板文字时补一个全角空格，避免填入值与模板文字粘连
        # （如「…时　分止」+ 时间值、「其他：」+ 自定义内容）。
        written = ("\u3000" + text) if not _is_blank(target) else text
        # 沿用模板自身的字体字号：优先取目标格，其次取标签格。
        rpr = _run_properties(target) or _run_properties(label_cell)

        table_xml = table_xml.replace(row, row.replace(target, _inject(target, written, rpr), 1), 1)
    return table_xml


# ============================================================
# 安全措施表：解析清单 / 回填判定
# ============================================================

# 措施表结尾的「其他安全措施：…编制人（签字）：」是自由填写行，不参与 AI 判断。
_MEASURE_TAIL_RE = re.compile(r"^(?:其他|其它)安全措施")

# 安全措施表有两种版式（与 tools/build_article_knowledge.py 的 build_measures 同口径——
# 它从同一批表的编辑器结构快照里抽措施，两边必须给出**同样的条数与顺序**，
# 否则访问层的 align_measures 对不齐，会静默退回「无依据判定」）：
#   column「序号 / 安全措施 / 是划√否划× / 确认人」——9 类模板，一行一项，记号另有专列；
#   grid  「符合划‘○’，识别无此项划‘×’」的网格——临时用电，一格一项，记号写在项格内。
MEASURE_LAYOUT_COLUMN = "column"
MEASURE_LAYOUT_GRID = "grid"

MARK_CHECK = "√"     # column 版式：是划√
MARK_CROSS = "×"     # 两种版式共用：否划×
MARK_CONFORM = "○"   # grid 版式：符合划○

# grid 版式里要剔除的非措施格：签署区（送电/断电/拆线）、自由填写项、提示行。
_GRID_PREFIX_RE = re.compile(r"^[（(□\s]+")
_GRID_SKIP_RE = re.compile(
    r"签字|电工证号|年\s*月|时间[：:]|许可证关闭|操作人|申请人|确认人"
    r"|隐患|到期|^送电|^断电|^拆线|^确认时间|^电源接入点$"
)


def _measure_layout(table_xml: str):
    """识别安全措施表的版式，返回 ``(表头行下标, 版式)``；认不出返回 ``(-1, None)``。

    column 版式靠「恰好 4 格 + 末格是『确认人』+ 第 2 格含『安全措施』或『注意事项』」认
    （后者用于射线作业，它的首列是「作业步骤」而非序号）；
    grid 版式靠表头那句「符合划…识别无此项划…」认。
    """
    for index, row in enumerate(_TR_RE.findall(table_xml)):
        cells = _TC_RE.findall(row)
        texts = [_cell_text(cell) for cell in cells]
        if len(cells) == 4 and texts[3] == "确认人" and any(
            hint in texts[1] for hint in ("安全措施", "注意事项")
        ):
            return index, MEASURE_LAYOUT_COLUMN
        if "符合划" in "".join(texts):
            return index, MEASURE_LAYOUT_GRID
    return -1, None


def _grid_item_text(cell_xml: str) -> str:
    """grid 版式：项格里的措施文本；不是措施（签署区/自由填写/提示行）返回空串。"""
    text = _GRID_PREFIX_RE.sub("", _cell_text(cell_xml)).strip()
    if len(text) < 4 or text.endswith("：") or text.startswith("其他"):
        return ""
    return "" if _GRID_SKIP_RE.search(text) else text


def _measure_rows(table_xml: str) -> list:
    """定位「安全措施表」里可判断的措施，返回 ``[(行XML, 记号格下标, 文本, 序号标签)]``。

    列表顺序即判断顺序，也是回填 docx 时的唯一定位依据。

    - column 版式：表头之后**连续**的 4 格行算措施行，取第 2 格为文本、第 3 格为记号格；
      跳过第 2 格为空的行（如射线的空「其他」行），遇到「其他安全措施…」自由填写行即停止。
    - grid 版式：表头之后的每一格都是一项，格内即文本、也是记号格。
    条数由测试锁定（``tests/test_permit_template.py`` 的 ``EXPECTED_COUNTS``）。
    """
    rows = _TR_RE.findall(table_xml)
    header, layout = _measure_layout(table_xml)
    if header < 0:
        return []

    measures = []
    if layout == MEASURE_LAYOUT_COLUMN:
        for row in rows[header + 1:]:
            cells = _TC_RE.findall(row)
            if len(cells) != 4:
                break
            text = _cell_text(cells[1])
            if not text:
                continue
            if _MEASURE_TAIL_RE.match(text):
                break
            measures.append((row, 2, text, _cell_text(cells[0]) or str(len(measures) + 1)))
        return measures

    for row in rows[header + 1:]:
        for index, cell in enumerate(_TC_RE.findall(row)):
            text = _grid_item_text(cell)
            if text:
                measures.append((row, index, text, str(len(measures) + 1)))
    return measures


def list_safety_measures(type_key: str) -> list:
    """该类型模板里的安全措施清单，供 AI 逐条判断。

    返回 [{"index": 1, "seq_label": "1", "text": "…"}, …]；index 从 1 递增，
    与 _measure_rows 的顺序一致，是回填 docx 时的唯一定位依据。
    """
    type_def, _ = get_permit_type(type_key)
    if not type_def:
        raise ValueError(f"未知的作业类型: {type_key}")
    document_xml = _read_document_xml(get_template_path(type_def))
    return [
        {"index": index, "seq_label": label, "text": text}
        for index, (_row, _cell_index, text, label) in enumerate(
            _measure_rows(_table_xml(document_xml)), 1
        )
    ]


def decision_map(measure_decisions: dict) -> dict:
    """把 measure_decisions 转成 {index(int): needed(bool)}。"""
    result = {}
    for item in (measure_decisions or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        result[index] = bool(item.get("needed"))
    return result


def _marked_row(row_xml: str, marks: list, grid: bool) -> str:
    """一次把该行若干格子的记号都写上；``marks`` 是 ``[(格下标, 记号), …]``。

    按位置替换（不靠字符串查找），逐格重扫当前行 XML，故与书写顺序无关。
    grid 版式没有记号专列，记号追加在项格文本之后（``电源接入点确认 ○``）并留一个空格；
    column 版式写进空的「是划√否划×」列，沿用措施格的字号。
    """
    cells = _TC_RE.findall(row_xml)
    for cell_index, mark in marks:
        if not (0 <= cell_index < len(cells)):
            continue
        cell = cells[cell_index]
        if grid:
            rpr = _run_properties(cell)
        else:
            rpr = _run_properties(cells[0]) or (_run_properties(cells[1]) if len(cells) > 1 else "")
        filled = _inject(cell, f" {mark}" if grid else mark, rpr)
        for count, match in enumerate(_TC_RE.finditer(row_xml)):
            if count == cell_index:
                row_xml = row_xml[: match.start()] + filled + row_xml[match.end():]
                break
    return row_xml


def fill_safety_measures(table_xml: str, measure_decisions: dict) -> str:
    """把 AI 判定写成记号：column 版式写 √/×，「符合划○」网格写 ○/×。

    column 版式写进「是划√否划×」列，「确认人」列保持空白留给人工签字；
    grid 版式没有专列，记号追加在项格文本之后。
    两种版式都只写进该格**已有的段落**（不新增段落，否则行高变化、整表会溢出到第二页）。
    """
    wanted = decision_map(measure_decisions)
    if not wanted:
        return table_xml
    _header, layout = _measure_layout(table_xml)
    grid = layout == MEASURE_LAYOUT_GRID
    # grid 版式一行最多承载 4 项，必须先把整行的记号凑齐再一次性替换行：
    # 逐个替换的话，第二次拿原始行文本去表里找已经找不到了。
    batches = []
    for index, (row, cell_index, _text, _label) in enumerate(_measure_rows(table_xml), 1):
        if index not in wanted:
            continue
        mark = (MARK_CONFORM if grid else MARK_CHECK) if wanted[index] else MARK_CROSS
        if batches and batches[-1][0] == row:
            batches[-1][1].append((cell_index, mark))
        else:
            batches.append((row, [(cell_index, mark)]))
    for row, marks in batches:
        table_xml = table_xml.replace(row, _marked_row(row, marks, grid), 1)
    return table_xml


def _rows_to_struct(table_xml: str) -> list:
    """表格 → 结构化行：[[{text, colspan}], ...]，供前端渲染。"""
    rows = []
    for row_xml in _TR_RE.findall(table_xml):
        row = []
        for cell_xml in _TC_RE.findall(row_xml):
            match = _GS_RE.search(cell_xml)
            row.append({
                "text": _cell_text(cell_xml),
                "colspan": int(match.group(1)) if match else 1,
            })
        rows.append(row)
    return rows


def _read_document_xml(path: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _filled_document_xml(type_def: dict, values: dict, config: dict, measure_decisions: dict = None) -> str:
    path = get_template_path(type_def)
    if not os.path.exists(path):
        raise FileNotFoundError(f"许可证模板不存在: {path}")
    document_xml = _read_document_xml(path)
    table_xml = _table_xml(document_xml)
    filled = fill_table_xml(table_xml, resolve_fields(type_def, config), values)
    filled = fill_safety_measures(filled, measure_decisions)
    return document_xml.replace(table_xml, filled, 1)


# ============================================================
# 对外：结构化行 / 生成 docx
# ============================================================

def build_permit_rows(type_key: str, values: dict, measure_decisions: dict = None) -> dict:
    """填值后的许可证结构化内容。

    返回 {permit_type, permit_title, template, rows}
    """
    type_def, config = get_permit_type(type_key)
    if not type_def:
        raise ValueError(f"未知的作业类型: {type_key}")
    document_xml = _filled_document_xml(type_def, values, config, measure_decisions)
    return {
        "permit_type": type_def["name"],
        "permit_title": type_def["title"],
        "template": type_def["template"],
        "rows": _rows_to_struct(_table_xml(document_xml)),
    }


def render_permit_docx(type_key: str, values: dict, out_path: str, measure_decisions: dict = None) -> str:
    """按模板生成填好值的许可证 docx，返回输出路径。"""
    type_def, config = get_permit_type(type_key)
    if not type_def:
        raise ValueError(f"未知的作业类型: {type_key}")

    document_xml = _filled_document_xml(type_def, values, config, measure_decisions)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with zipfile.ZipFile(get_template_path(type_def)) as source:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "word/document.xml":
                    data = document_xml.encode("utf-8")
                target.writestr(item, data)
    return out_path
