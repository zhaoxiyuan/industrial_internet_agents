"""JSA（工作前安全分析）：把一次具体作业变成结构化的分析结果。

职责边界：
- 本模块只负责「给模型什么」与「从模型拿什么」——即上下文构造、严格解析、调用与降级；
- **报告排版不在这里**（那是 `jsa_report.py` 的事，它消费本模块产出的结构）；
- 也不碰票面（作业票由 `permit_template.py` 负责），JSA 报告是一份**独立文档**。

依据：办法 art016「作业申请人应当组织开展工作前安全分析…提出作业申请」，
art029 八不准第一条「工作前安全分析未开展不准作业」。办法只要求"开展"、不规定格式，
报告格式以 `data/jsa_templates/JSA分析报告模板.docx` 为准。

生成失败时**不产出任何内容**（source="unavailable"）——JSA 是给人看的判断材料，
塞一段编造的分析比空着危险得多。
"""
import json
import logging
import re
from datetime import datetime, timezone

from .permit_template import list_safety_measures, resolve_fields

logger = logging.getLogger(__name__)

JSA_SOURCE_AI = "ai"
JSA_SOURCE_UNAVAILABLE = "unavailable"

# 报告模板里的风险等级只有三档（用户 2026-10-13 确认）
RISK_LEVELS = ("高", "中", "低")

JSA_TEMPERATURE = 0.2

# 作业类型 → 专项控制措施章节标题。模板里「高处作业及吊装控制措施」是一节，
# 所以高处与吊装共用一个标题；标题由**规则**决定，不让模型自由增减章节。
TYPE_SECTION_TITLES = {
    "hot_work": "动火作业专项控制措施",
    "confined_space": "受限空间作业专项控制措施",
    "line_open": "管线打开（盲板抽堵）专项控制措施",
    "high_work": "高处作业及吊装控制措施",
    "lifting": "高处作业及吊装控制措施",
    "temp_power": "电气安全专项控制措施",
    "earth_work": "动土作业专项控制措施",
    "road_close": "断路作业专项控制措施",
    "radiation": "射线作业专项控制措施",
    "non_routine": "试压作业专项控制措施",
}

# 介质/作业内容里出现这些词，就追加一节 H₂S 专项（模板里就是这么来的）
H2S_SECTION_TITLE = "H₂S专项控制措施"
_H2S_CUES = ("硫化氢", "H2S", "H₂S", "含硫")

# 解析时要一并返回的字段（顺序即报告里的顺序）
_TEXT_FIELDS = ("job_name", "risk_summary", "emergency")
_LIST_FIELDS = ("pre_checks", "final_checklist", "main_risks")


def special_section_titles(values: dict, type_def: dict, related_keys=None) -> list:
    """本次作业需要哪几节专项控制措施——由规则判定，不由模型决定。

    模板里的两节分别是「介质特点触发」（H₂S）与「关联作业类型触发」（高处+吊装），
    这里把这条经验固化成规则：章节结构稳定、可单测，模型只负责填内容。
    """
    titles = []
    keys = [type_def["key"]] + [k for k in (related_keys or []) if k != type_def["key"]]
    for key in keys:
        title = TYPE_SECTION_TITLES.get(key)
        if title and title not in titles:
            titles.append(title)
    if _mentions_h2s(values) and H2S_SECTION_TITLE not in titles:
        titles.insert(0, H2S_SECTION_TITLE)
    return titles


def _mentions_h2s(values: dict) -> bool:
    parts = []
    for value in (values or {}).values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
    text = " ".join(parts)
    upper = text.upper()
    return any(cue in text or cue.upper() in upper for cue in _H2S_CUES)


def risk_options_for_type(type_def: dict) -> list:
    return next((list(field.get("options", [])) for field in resolve_fields(type_def)
                 if field["key"] == "risks"), [])


def _string_items(value, field):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} 必须是非空字符串数组")
    return [item.strip() for item in value]


def build_jsa_context(values: dict, type_def: dict, section_titles=None,
                      related_names=None, max_measures: int = 60) -> list:
    """拼给模型看的「作业基本信息 + 该类型安全措施清单 + 需要输出的章节」。

    字段口径与安全措施判定共用同一份 `resolve_fields`，保证 JSA 与作业票**同源**。
    """
    lines = ["【作业基本信息】"]
    job_content = (values or {}).get("work_content")
    if job_content:
        lines.append(f"作业内容：{job_content}")
    lines.append(f"作业类型：{type_def['name']}")
    for field in resolve_fields(type_def):
        key = field["key"]
        if key in ("work_content", "apply_time"):
            continue          # 作业内容已单列；申请时间是系统时间，与分析无关
        raw = (values or {}).get(key)
        if raw in (None, "", [], {}):
            continue
        if isinstance(raw, dict):
            raw = f"{raw.get('start', '')} 至 {raw.get('end', '')}"
        elif isinstance(raw, (list, tuple)):
            raw = "、".join(str(item) for item in raw)
        lines.append(f"{field.get('form_label') or field['label']}：{raw}")

    if related_names:
        lines.append(f"本作业还涉及的其他作业类型：{'、'.join(related_names)}")

    lines.append("【risk_options 可选值（只能从中选择；无选项时输出空数组）】")
    lines.append(json.dumps(risk_options_for_type(type_def), ensure_ascii=False))
    measures = list_safety_measures(type_def["key"])[:max_measures]
    if measures:
        lines.append("")
        lines.append(f"【{type_def['name']}的安全措施清单（控制措施可取材于此）】")
        lines.extend(f"{index}. {item['text']}" for index, item in enumerate(measures, 1))

    if section_titles:
        lines.append("")
        lines.append("【需要输出的专项控制措施章节（标题原样使用，不要增删）】")
        lines.extend(f"- {title}" for title in section_titles)
    return lines


def parse_jsa_payload(text: str, expected_sections=None, allowed_risks=None) -> dict:
    """解析并严格校验模型返回，任何不合规都抛 ValueError（由调用方转为降级）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[A-Za-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON 对象")
    payload = json.loads(raw[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("返回不是 JSON 对象")

    result = {}
    for field in _TEXT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 缺失或不是字符串")
        result[field] = value.strip()

    for field in _LIST_FIELDS:
        result[field] = _string_items(payload.get(field), field)

    # 风险选项只是给票面预勾用的辅助字段：越界或缺失**不让整份分析降级**——
    # 丢弃越界项、留一条明确告警即可（主体分析比这一栏重要得多）。
    options = payload.get("risk_options")
    if options is None:
        options = []
    if not isinstance(options, list):
        raise ValueError("risk_options 不是数组")
    allowed = set(allowed_risks or [])
    kept, dropped = [], []
    for item in options:
        if isinstance(item, str) and item in allowed:
            kept.append(item)
        else:
            dropped.append(item)
    if dropped:
        logger.warning("risk_options 丢弃了不在票面选项内的值: %s", dropped)
    result["risk_options"] = list(dict.fromkeys(kept))

    steps = payload.get("steps")
    if not isinstance(steps, list) or len(steps) < 4:
        raise ValueError(f"steps 至少 4 条，实际 {len(steps) if isinstance(steps, list) else 'N/A'}")
    parsed_steps = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"steps 第 {index} 条不是对象")
        record = {}
        for key in ("step", "hazard", "consequence", "level", "control"):
            value = step.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"steps 第 {index} 条缺少 {key}")
            record[key] = value.strip()
        if record["level"] not in RISK_LEVELS:
            raise ValueError(f"steps 第 {index} 条的 level「{record['level']}」不在 {RISK_LEVELS}")
        parsed_steps.append(record)
    result["steps"] = parsed_steps

    sections = payload.get("special_sections")
    if sections is None:
        sections = []
    if not isinstance(sections, list):
        raise ValueError("special_sections 不是数组")
    parsed_sections = []
    for index, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            raise ValueError(f"special_sections 第 {index} 节不是对象")
        title = section.get("title")
        items = section.get("items")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"special_sections 第 {index} 节缺少 title 或 items")
        parsed_sections.append({
            "title": title.strip(),
            "items": _string_items(items, "special_sections.items"),
        })
    if expected_sections is not None:
        got = [section["title"] for section in parsed_sections]
        if got != list(expected_sections):
            raise ValueError(f"专项章节与要求不符：期望 {list(expected_sections)}，实际 {got}")
    result["special_sections"] = parsed_sections
    return result


def generate_jsa(job_id: str, type_name: str, context_lines: list, section_titles, allowed_risks) -> dict:
    """调模型生成 JSA；失败时返回 source="unavailable"，**不产出任何内容**。

    `section_titles` / `allowed_risks` 均为**必填**：前者是本次的章节规则，后者是票面的
    风险选项集。早先 `allowed_risks` 默认 `None`，调用点一旦漏传，解析层会当作"没有任何
    合法选项"而拒绝一切输出，让整份分析白跑——改为接口上强制显式传参。
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        from ..model.chat_model import create_chat_model_with_logging
        from ..utils.system_prompt import load_system_prompt
        from langchain_core.messages import HumanMessage, SystemMessage

        model = create_chat_model_with_logging("P1-JSA", job_id, temperature=JSA_TEMPERATURE)
        response = model.invoke([
            SystemMessage(content=load_system_prompt("JSA")),
            HumanMessage(content="\n".join(context_lines)),
        ])
        text = getattr(response, "content", "") or ""
        if isinstance(text, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in text
            )
        payload = parse_jsa_payload(text, section_titles, allowed_risks)
    except Exception as exc:                      # noqa: BLE001 —— 任何失败都降级，不抛给上层
        return {
            "source": JSA_SOURCE_UNAVAILABLE,
            "type_name": type_name,
            "generated_at": generated_at,
            "error": str(exc),
        }
    payload.update({
        "source": JSA_SOURCE_AI,
        "type_name": type_name,
        "generated_at": generated_at,
    })
    return payload
