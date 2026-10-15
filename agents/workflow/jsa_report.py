"""JSA 报告渲染：把结构化分析结果填进 docx 模板，产出可打印的报告。

模板 `data/jsa_templates/JSA分析报告模板.docx` 是一份**已经填好的样例报告**——
它给我们的价值是**样式**（Heading1 / Heading2 / ListBullet / 表格边框）与结构骨架，
不是「等着填空的格子」。所以渲染做法是：

    **用模板的块当原型，按实际数据重建整份正文**，而不是往样例里塞值。

样例内容必须整份换掉，否则「含硫化氢天然气」「DN600」这些会留在别人的报告里。

重构后的正文顺序（章节号**动态**——专项章节有几节由 JSA 决定）：

    H1  {作业名称}JSA分析报告
    一、作业基本信息         一段，用 <w:br/> 分行
    二、作业风险概述         一段
    三、作业前安全确认要求    ListBullet × N
    四、JSA风险分析          表格：表头 + 每步一行
    五…N、{专项}控制措施      每节一个 H2 + 编号叙述段
    N+1、应急处置要求         一段
    N+2、作业前最终确认清单    □ × N

与许可证渲染一样走 zipfile + 正则改写，不引入 python-docx。
"""
import os
import re
import zipfile

from .permit_template import _escape

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_PATH = os.path.join(ROOT, "data", "jsa_templates", "JSA分析报告模板.docx")

_BLOCK_RE = re.compile(r"<w:tbl>.*?</w:tbl>|<w:p\b.*?</w:p>|<w:p\b[^>]*/>", re.S)
_ROW_RE = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
_CELL_RE = re.compile(r"<w:tc>.*?</w:tc>", re.S)
_PARA_RE = re.compile(r"<w:p\b.*?</w:p>|<w:p\b[^>]*/>", re.S)
_PPR_RE = re.compile(r"<w:pPr>.*?</w:pPr>", re.S)
_RPR_RE = re.compile(r"<w:rPr>.*?</w:rPr>", re.S)
_STYLE_RE = re.compile(r'<w:pStyle w:val="([^"]+)"')
_TEXT_RE = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")

_CN_DIGITS = "一二三四五六七八九十"


def _cn(number: int) -> str:
    return _CN_DIGITS[number - 1] if 1 <= number <= 10 else str(number)


def _plain(block_xml: str) -> str:
    return "".join(_TEXT_RE.findall(block_xml))


def _run_properties(paragraph_xml: str) -> str:
    """取段落里第一个 run 的 rPr（要先剥掉 pPr——段落标记自己也有 rPr）。"""
    without_ppr = _PPR_RE.sub("", paragraph_xml, count=1)
    found = _RPR_RE.search(without_ppr)
    return found.group(0) if found else ""


def _set_paragraph_text(paragraph_xml: str, text: str) -> str:
    """保留段落 pPr 与首个 run 的 rPr，把正文换成 text。"""
    opening = re.match(r"<w:p(?:\s[^>]*)?>", paragraph_xml)
    head = (opening.group(0) if opening else "<w:p>") + (
        _PPR_RE.search(paragraph_xml).group(0) if _PPR_RE.search(paragraph_xml) else ""
    )
    run = (
        f"<w:r>{_run_properties(paragraph_xml)}"
        f'<w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'
    )
    return f"{head}{run}</w:p>"


def _set_multiline_paragraph(paragraph_xml: str, lines: list) -> str:
    """一个段落里用 <w:br/> 分行（模板的「作业基本信息」就是这种写法）。"""
    opening = re.match(r"<w:p(?:\s[^>]*)?>", paragraph_xml)
    head = (opening.group(0) if opening else "<w:p>") + (
        _PPR_RE.search(paragraph_xml).group(0) if _PPR_RE.search(paragraph_xml) else ""
    )
    pieces = []
    for index, line in enumerate(lines):
        if index:
            pieces.append("<w:br/>")
        pieces.append(f'<w:t xml:space="preserve">{_escape(line)}</w:t>')
    run = f"<w:r>{_run_properties(paragraph_xml)}{''.join(pieces)}</w:r>"
    return f"{head}{run}</w:p>"


def _set_cell_text(cell_xml: str, text: str) -> str:
    paragraph = _PARA_RE.search(cell_xml)
    if not paragraph:
        run = f'<w:r><w:t xml:space="preserve">{_escape(text)}</w:t></w:r>'
        return cell_xml.replace("</w:tc>", f"<w:p>{run}</w:p></w:tc>", 1)
    return cell_xml.replace(
        paragraph.group(0), _set_paragraph_text(paragraph.group(0), text), 1
    )


def _build_table_row(row_proto: str, texts: list) -> str:
    """按原型复制一行，逐格填新值（列数不一致时多出的值丢弃、缺的留空）。"""
    cells = _CELL_RE.findall(row_proto)
    row = row_proto
    for index, cell in enumerate(cells):
        text = texts[index] if index < len(texts) else ""
        row = row.replace(cell, _set_cell_text(cell, text), 1)
    return row


def _numbered_sentence(items: list) -> str:
    """专项章节的正文在模板里是一整段编号叙述：`1. … 2. …`。"""
    return " ".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def extract_prototypes(body: str) -> dict:
    """从模板正文里取出各类「原型块」——渲染时按需复制它们。"""
    blocks = _BLOCK_RE.findall(body)
    prototypes = {"h1": None, "h2": None, "bullet": None, "body": None, "checklist": None}
    for block in blocks:
        if block.startswith("<w:tbl"):
            prototypes.setdefault("tables", []).append(block)
            continue
        style = _STYLE_RE.search(block)
        name = style.group(1) if style else ""
        text = _plain(block).strip()
        if name == "Heading1" and prototypes["h1"] is None:
            prototypes["h1"] = block
        elif name == "Heading2" and prototypes["h2"] is None:
            prototypes["h2"] = block
        elif name == "ListBullet" and prototypes["bullet"] is None:
            prototypes["bullet"] = block
        elif not name and text.startswith("□") and prototypes["checklist"] is None:
            prototypes["checklist"] = block
        elif not name and prototypes["body"] is None:
            prototypes["body"] = block

    tables = prototypes.get("tables") or []
    if not tables:
        raise ValueError("JSA 模板里没有找到分析表格")
    table = tables[0]
    rows = _ROW_RE.findall(table)
    if len(rows) < 2:
        raise ValueError("JSA 模板的分析表格缺少数据行")
    prototypes["table_open"] = table[: table.index(rows[0])]
    prototypes["table_head"] = rows[0]
    prototypes["table_row"] = rows[1]
    prototypes["table_close"] = table[table.rindex(rows[-1]) + len(rows[-1]):]
    return prototypes


def build_info_lines(payload: dict, values: dict, type_def: dict, related=None) -> list:
    """「一、作业基本信息」的若干行——取不到的行直接不写，不编造。"""
    lines = []

    def add(label, value):
        if value:
            lines.append(f"{label}：{value}")

    add("作业名称", payload.get("job_name") or (values or {}).get("work_content"))
    add("作业区域", (values or {}).get("work_place"))
    add("管道规格", (values or {}).get("blind_spec") or (values or {}).get("pipe_spec"))
    add("运行压力", (values or {}).get("op_pressure"))
    add("介质", (values or {}).get("medium") or (values or {}).get("space_medium"))
    add("作业高度", (values or {}).get("work_height"))
    names = [type_def["name"]] + [item["name"] for item in (related or [])]
    add("作业类型", "、".join(dict.fromkeys(names)))
    add("主要风险", "、".join(payload.get("main_risks") or []))
    return lines


def build_document_body(payload: dict, values: dict, type_def: dict, related=None,
                        prototypes: dict = None) -> str:
    """按数据重建整份正文（不含 sectPr）。"""
    prototypes = prototypes or extract_prototypes(_template_body())
    steps = payload.get("steps") or []
    if not steps:
        raise ValueError("JSA 结果里没有作业步骤，无法渲染报告")

    title = f"{payload.get('job_name') or (values or {}).get('work_content') or '作业'}JSA分析报告"
    parts = [_set_paragraph_text(prototypes["h1"], title)]

    parts.append(_set_paragraph_text(prototypes["h2"], "一、作业基本信息"))
    parts.append(_set_multiline_paragraph(
        prototypes["body"], build_info_lines(payload, values, type_def, related)
    ))

    parts.append(_set_paragraph_text(prototypes["h2"], "二、作业风险概述"))
    parts.append(_set_paragraph_text(prototypes["body"], payload.get("risk_summary", "")))

    parts.append(_set_paragraph_text(prototypes["h2"], "三、作业前安全确认要求"))
    for item in payload.get("pre_checks") or []:
        parts.append(_set_paragraph_text(prototypes["bullet"], item))

    parts.append(_set_paragraph_text(prototypes["h2"], "四、JSA风险分析"))
    rows = [prototypes["table_head"]]
    for step in steps:
        rows.append(_build_table_row(prototypes["table_row"], [
            step.get("step", ""), step.get("hazard", ""), step.get("consequence", ""),
            step.get("level", ""), step.get("control", ""),
        ]))
    parts.append(prototypes["table_open"] + "".join(rows) + prototypes["table_close"])

    # 专项章节有几节是 JSA 决定的，后面的章号跟着顺延
    number = 5
    for section in payload.get("special_sections") or []:
        parts.append(_set_paragraph_text(
            prototypes["h2"], f"{_cn(number)}、{section.get('title', '')}"
        ))
        parts.append(_set_paragraph_text(
            prototypes["body"], _numbered_sentence(section.get("items") or [])
        ))
        number += 1

    parts.append(_set_paragraph_text(prototypes["h2"], f"{_cn(number)}、应急处置要求"))
    parts.append(_set_paragraph_text(prototypes["body"], payload.get("emergency", "")))
    number += 1

    parts.append(_set_paragraph_text(prototypes["h2"], f"{_cn(number)}、作业前最终确认清单"))
    for item in payload.get("final_checklist") or []:
        parts.append(_set_paragraph_text(prototypes["checklist"], f"□ {item}"))

    return "".join(parts)


def _template_body() -> str:
    document_xml = _read_document_xml(TEMPLATE_PATH)
    return re.search(r"<w:body>(.*)</w:body>", document_xml, re.S).group(1)


def _read_document_xml(path: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def render_jsa_report(payload: dict, values: dict, type_def: dict, out_path: str,
                      related=None) -> str:
    """按模板生成 JSA 报告 docx，返回输出路径。"""
    if payload.get("source") != "ai":
        raise ValueError("JSA 分析未成功生成，不能渲染空报告")
    document_xml = _read_document_xml(TEMPLATE_PATH)
    body_match = re.search(r"<w:body>(.*)</w:body>", document_xml, re.S)
    body = body_match.group(1)
    # 正文之后通常还挂着 sectPr（页面设置），必须原样保留
    tail = re.search(r"<w:sectPr\b.*?</w:sectPr>", body, re.S)
    new_body = build_document_body(payload, values, type_def, related) + (tail.group(0) if tail else "")
    new_document = (
        document_xml[: body_match.start(1)]
        + new_body
        + document_xml[body_match.end(1):]
    )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(TEMPLATE_PATH) as source:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "word/document.xml":
                    data = new_document.encode("utf-8")
                target.writestr(item, data)
    return out_path
