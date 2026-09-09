"""真实作业许可 DOCX 的轻量解析接口。"""
import base64
import binascii
import io
import re
import zipfile
from xml.etree import ElementTree as ET


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
MAX_DOCX_BYTES = 10 * 1024 * 1024


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _cell_text(cell):
    return _clean("".join(node.text or "" for node in cell.findall(".//w:t", WORD_NS)))


def _document_rows(document_xml):
    root = ET.fromstring(document_xml)
    rows = []
    for table in root.findall(".//w:tbl", WORD_NS):
        for row in table.findall("./w:tr", WORD_NS):
            cells = [_cell_text(cell) for cell in row.findall("./w:tc", WORD_NS)]
            if any(cells):
                rows.append(cells)
    return rows


def _value_after_label(rows, labels):
    for row in rows:
        for index, cell in enumerate(row):
            normalized = cell.rstrip("：:").strip()
            if normalized in labels:
                return _clean(row[index + 1]) if index + 1 < len(row) else ""
            for label in labels:
                match = re.match(rf"^{re.escape(label)}[：:]\s*(.+)$", cell)
                if match:
                    return _clean(match.group(1))
    return ""


def _row_text(rows, prefix):
    for row in rows:
        if row and _clean(row[0]).startswith(prefix):
            return _clean(" ".join(row[1:]) or row[0][len(prefix):])
    return ""


def _selected_measures(rows):
    measures = []
    for row in rows:
        if len(row) < 2 or not re.fullmatch(r"\d+", row[0]):
            continue
        selected = len(row) > 2 and bool(re.search(r"[√✓☑■]", row[2]))
        measures.append({"sequence": int(row[0]), "description": row[1], "selected": selected})
    return measures


def _has_user_value(value):
    value = _clean(value)
    if not value:
        return False
    placeholders = (
        r"^[年月日时分至自\s]+$",
        r"^[□/、，,：:（）()\s]+$",
    )
    return not any(re.fullmatch(pattern, value) for pattern in placeholders)


def _has_time_value(value):
    return len(re.findall(r"\d", _clean(value))) >= 6


def _parse_work_times(value):
    matches = re.findall(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*时\s*(\d{1,2})\s*分",
        _clean(value),
    )
    parsed = [f"{year}-{int(month):02d}-{int(day):02d}T{int(hour):02d}:{int(minute):02d}:00+08:00" for year, month, day, hour, minute in matches]
    return (parsed + ["", ""])[:2]


def parse_docx_bytes(content, filename="作业许可.docx"):
    if len(content) > MAX_DOCX_BYTES:
        raise ValueError("DOCX 文件不能超过 10MB")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise ValueError("文件不是有效的 DOCX 文档")

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ValueError("DOCX 缺少 Word 正文内容") from exc

    rows = _document_rows(document_xml)
    root = ET.fromstring(document_xml)
    all_text = _clean(" ".join(node.text or "" for node in root.findall(".//w:t", WORD_NS)))
    title = next((text for text in ("动火作业许可证", "受限空间许可证", "高处作业许可证", "管线打开", "非常规作业许可证") if text in all_text), "作业许可证")

    work_unit = _value_after_label(rows, {"申请单位", "作业单位"})
    territorial_unit = _value_after_label(rows, {"作业区域所在单位", "属地单位"})
    applicant = _value_after_label(rows, {"申请人", "作业申请人"})
    worker = _value_after_label(rows, {"作业人"})
    guardian = _value_after_label(rows, {"监护人"})
    supervisor = _value_after_label(rows, {"属地监督"})
    work_location = _value_after_label(rows, {"作业地点", "作业区域", "作业地点、部位"})
    hot_work_location = _value_after_label(rows, {"动火部位"})
    job_content = _value_after_label(rows, {"作业内容", "作业内容描述"})
    planned_time = _row_text(rows, "作业时间")
    planned_start, planned_end = _parse_work_times(planned_time)
    related_permits = _value_after_label(rows, {"关联的其他特殊作业、非常规作业"})
    related_permit_numbers = _value_after_label(rows, {"涉及的其他特殊作业、非常规作业许可证编号"})

    personnel = []
    for name, role in ((applicant, "申请人"), (worker, "作业人"), (guardian, "监护人"), (supervisor, "属地监督")):
        if _has_user_value(name) and not any(item["name"] == name for item in personnel):
            personnel.append({"name": name, "role": role})

    measures = _selected_measures(rows)
    inferred_type = {
        "动火作业许可证": "动火作业",
        "受限空间许可证": "受限空间作业",
        "高处作业许可证": "高处作业",
        "管线打开": "管线打开作业",
        "非常规作业许可证": "非常规作业",
    }.get(title, "待识别作业")
    application = {
        "job_type": inferred_type,
        "work_unit": work_unit,
        "applicant_unit": work_unit,
        "territorial_unit": territorial_unit,
        "region": work_location,
        "work_location": work_location,
        "hot_work_location": hot_work_location,
        "job_content": job_content,
        "personnel": personnel,
        "planned_start": planned_start,
        "planned_end": planned_end,
        "planned_time_text": planned_time,
        "related_permits": [value for value in (related_permits, related_permit_numbers) if _has_user_value(value)],
        "safety_measures": [item for item in measures if item["selected"]],
        "safety_measures_catalog": measures,
        "source_document": {"filename": filename, "permit_title": title, "row_count": len(rows)},
        "input_source": "docx",
    }

    missing = []
    for field, label in ((job_content, "作业内容"), (work_location, "作业区域/地点"), (planned_time, "作业时间")):
        if not (_has_time_value(field) if label == "作业时间" else _has_user_value(field)):
            missing.append(label)
    if not personnel:
        missing.append("作业人员")

    populated = sum(_has_user_value(value) for value in (work_unit, territorial_unit, applicant, worker, guardian, supervisor, work_location, hot_work_location, job_content))
    populated += int(_has_time_value(planned_time))
    return {
        "status": "ok",
        "document": application["source_document"],
        "application": application,
        "is_blank": populated == 0,
        "missing_fields": missing,
        "message": "已识别空白作业许可模板" if populated == 0 else "作业许可解析完成",
    }


def handle_parse_docx(handler, data):
    filename = _clean((data or {}).get("filename")) or "作业许可.docx"
    encoded = (data or {}).get("content_base64", "")
    if not filename.lower().endswith(".docx"):
        handler.send_json({"status": "error", "error": "仅支持 .docx 作业许可文件"}, status=400)
        return
    try:
        content = base64.b64decode(encoded, validate=True)
        result = parse_docx_bytes(content, filename)
        handler.send_json(result)
    except (ValueError, binascii.Error, ET.ParseError) as exc:
        handler.send_json({"status": "error", "error": str(exc)}, status=400)
