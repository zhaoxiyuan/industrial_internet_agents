"""
P1: 作业预约、JSA分析与作业票
Permit Agent - 处理作业申请、JSA分析和作业票生成
支持 HumanInTheLoop - P1 阶段末统一审批
"""
import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, TypedDict, Dict
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .model.chat_model import create_chat_model_with_logging
from .model.config import get_llm_params
from .utils.agent_utils import extract_output
from .utils.logging_handler import AgentLoggingCallback, get_logging_callback, push_websocket_log, get_agent_config
from .utils.response_utils import make_response, make_error, SCHEMA_VERSION
from .utils.system_prompt import load_system_prompt
from .utils import add_job_log

# 配置日志
logger = logging.getLogger("p1_permit_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


_p1_job_context = ContextVar("p1_job_id", default="*")


def _infer_job_type(application: dict) -> str:
    explicit = application.get("job_type") or application.get("permit_type")
    if explicit:
        return str(explicit)
    text = str(application.get("job_content", ""))
    aliases = (("受限空间", "受限空间作业"), ("高空", "高处作业"), ("高处", "高处作业"), ("动火", "动火作业"), ("吊装", "吊装作业"), ("临时用电", "临时用电作业"), ("管线打开", "管线打开作业"))
    return next((name for keyword, name in aliases if keyword in text), "非常规作业")


def _build_permit_content(application: dict) -> tuple:
    """把已持久化的实际申请整理为审批可读的作业票内容（不含 JSA）。

    **JSA 不在这里造**：那是 `_build_jsa_result` 的事——它走分析链路（模型 + 严格校验），
    并保证"取不到就空着"。早先这里按作业类型塞一份写死的 hazards 冒充 JSA 结果，
    既与本次作业无关，也会和真分析打架，已移除。
    """
    job_type = _infer_job_type(application)
    missing = []
    for field in ("job_content", "region", "planned_start", "planned_end"):
        if not application.get(field):
            missing.append(field)
    if not application.get("personnel"):
        missing.append("personnel")
    if not application.get("equipment"):
        missing.append("equipment")
    if not application.get("job_level"):
        missing.append("job_level")
    for person in application.get("personnel") or []:
        if isinstance(person, dict) and not (person.get("qualifications") or person.get("qualification")):
            missing.append(f"personnel_{person.get('name') or 'unknown'}_qualifications")

    permit_content = {
        "job_type": job_type,
        "job_level": application.get("job_level", ""),
        "job_content": application.get("job_content", ""),
        "region": application.get("region", ""),
        "work_unit": application.get("work_unit") or application.get("applicant_unit", ""),
        "territorial_unit": application.get("territorial_unit", ""),
        "work_location": application.get("work_location") or application.get("region", ""),
        "equipment": application.get("equipment", []),
        "medium": application.get("medium", ""),
        "personnel": application.get("personnel", []),
        "planned_start": application.get("planned_start", ""),
        "planned_end": application.get("planned_end", ""),
        "related_permits": application.get("related_permits", []),
        "attachments": application.get("attachments", []),
        "gas_detection": application.get("gas_detection", []),
        "hazards": [],      # 由 JSA 分析结果填（见 _build_jsa_result）
        "measures": [],
        "missing_fields": missing,
    }
    return permit_content, missing


def _jsa_values(application: dict, values: dict = None) -> dict:
    """JSA 与作业票**共用同一份填报值**。

    手动填报路径直接有 `permit_fields`；上传 DOCX 路径没有，就从解析出的申请里映射
    常用字段（作业内容/地点/介质/时间），映射不到的字段 JSA 报告里就不写——
    不编造申请里没有的信息。
    """
    mapped = {
        "work_content": application.get("job_content") or application.get("work_content") or "",
        "work_place": application.get("work_location") or application.get("hot_work_location") or application.get("work_place") or application.get("region") or "",
        "medium": application.get("medium", ""),
        "work_height": application.get("work_height", ""),
        "work_people": "、".join(
            person.get("name") for person in (application.get("personnel") or [])
            if isinstance(person, dict) and person.get("name")
        ),
    }
    if application.get("planned_start") or application.get("planned_end"):
        mapped["work_time"] = {
            "start": application.get("planned_start", ""),
            "end": application.get("planned_end", ""),
        }
    mapped.update(application.get("permit_fields") or {})
    mapped.update(values or {})
    return {key: value for key, value in mapped.items() if value}


def _build_jsa_result(application: dict, values: dict, type_def: dict, job_id: str,
                      related=None) -> dict:
    """跑一次真正的 JSA 分析（模型 + 严格校验），失败即明确降级。

    专项章节标题由**规则**决定（介质是否含硫、涉及哪些作业类型），不让模型自由增减章节。
    成功时顺带把 `steps` 映射出一份 `hazards`，兼容前端与下游既有的展示口径。
    """
    from .workflow.jsa_analyze import (
        JSA_SOURCE_AI,
        build_jsa_context,
        generate_jsa,
        special_section_titles,
        risk_options_for_type,
    )

    try:
        jsa_values = _jsa_values(application, values)
        related = related if related is not None else _related_permit_types(jsa_values, type_def["key"])
        titles = special_section_titles(jsa_values, type_def, [item["key"] for item in related])
        result = generate_jsa(
            job_id,
            type_def["name"],
            build_jsa_context(jsa_values, type_def, titles, [item["name"] for item in related]),
            titles,
            risk_options_for_type(type_def),
        )
        if result.get("source") == JSA_SOURCE_AI:
            result["hazards"] = [
                {
                    "id": f"H-{index:03d}",
                    "description": step["hazard"],
                    "severity": step["level"],
                    "measures": [step["control"]],
                    "step": step["step"],
                    "consequence": step["consequence"],
                }
                for index, step in enumerate(result["steps"], 1)
            ]
            result["report_path"] = _render_jsa_report_safely(
                result, jsa_values, type_def, job_id, related
            )
        return result
    except Exception as exc:
        logger.warning(f"[P1] JSA 分析失败: job_id={job_id} {exc}")
        return {"source": "unavailable", "type_name": type_def.get("name", ""),
                "generated_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)}


def _render_jsa_report_safely(payload: dict, values: dict, type_def: dict, job_id: str,
                              related=None) -> str:
    """渲染 JSA 报告 docx；**渲染失败不能影响出票**，只记原因并留空路径。"""
    import os
    from .workflow import get_job_dir
    from .workflow.jsa_report import render_jsa_report

    try:
        out_path = os.path.join(get_job_dir(job_id), "jsa_report.docx")
        return render_jsa_report(payload, values, type_def, out_path, related)
    except Exception as exc:                      # noqa: BLE001
        logger.warning(f"[P1] JSA 报告渲染失败: job_id={job_id} {exc}")
        return ""


def _jsa_measures_context(context: list, jsa: dict) -> list:
    if jsa.get("source") != "ai":
        return list(context)
    return [*context, "【JSA 已识别的作业步骤、危害、后果及控制措施】",
            json.dumps(jsa.get("steps", []), ensure_ascii=False)]


def _merge_jsa_risks(values: dict, type_def: dict, jsa: dict) -> dict:
    from .workflow.jsa_analyze import risk_options_for_type

    merged = dict(values)
    allowed = risk_options_for_type(type_def)
    if not allowed or jsa.get("source") != "ai":
        return merged
    picked = merged.get("risks") or []
    if isinstance(picked, str):
        picked = [picked]
    merged["risks"] = list(dict.fromkeys([*picked, *(
        item for item in jsa.get("risk_options", []) if item in allowed
    )]))
    return merged


def _catalog_to_measures(catalog: list) -> list:
    """把上传 DOCX 解析出的措施目录转成与模板同形的清单（供 AI 判断）。

    与模板口径一致：跳过空白项与结尾的「其他安全措施…」自由填写行，
    index 按**可判断措施**从 1 重排。
    """
    measures = []
    for item in catalog or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("description") or "").strip()
        if not text or text.startswith(("其他安全措施", "其它安全措施")):
            continue
        measures.append({
            "index": len(measures) + 1,
            "seq_label": str(item.get("sequence") or len(measures) + 1),
            "text": text,
        })
    return measures


def _annotate_catalog(catalog: list, measure_decisions: dict) -> list:
    """把 AI 判定按顺序写回措施目录，便于审批弹窗逐条对照原人工勾选。"""
    from .workflow import decision_map

    decided = decision_map(measure_decisions)
    annotated = []
    position = 0
    for item in catalog or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        text = str(item.get("description") or "").strip()
        if text and not text.startswith(("其他安全措施", "其它安全措施")):
            position += 1
            row["index"] = position
            row["ai_needed"] = decided.get(position)
        annotated.append(row)
    return annotated


def _docx_measures_context(application: dict) -> list:
    """真实 DOCX 路径：把已解析出的申请字段整理成给模型看的上下文。"""
    lines = []
    for label, value in (
        ("作业类型", application.get("job_type")),
        ("作业内容", application.get("job_content")),
        ("作业区域", application.get("region")),
        ("作业地点", application.get("work_location") or application.get("hot_work_location")),
        ("作业等级", application.get("job_level")),
        ("计划开始", application.get("planned_start")),
        ("计划结束", application.get("planned_end")),
    ):
        if value:
            lines.append(f"{label}：{value}")
    equipment = application.get("equipment")
    if equipment:
        lines.append("设备：" + "、".join(str(x) for x in equipment))
    if application.get("medium"):
        lines.append(f"介质：{application['medium']}")
    names = [
        person.get("name") for person in (application.get("personnel") or [])
        if isinstance(person, dict) and person.get("name")
    ]
    if names:
        lines.append("作业人员：" + "、".join(names))
    return lines


def _build_docx_permit_result(application: dict, job_id: str, approved_jsa=None) -> dict:
    """基于真实 DOCX 的已解析字段生成 P1 结果，禁止混入工具中的固定 Mock 内容。"""
    from .workflow import get_permit_type

    permit_content, missing = _build_permit_content(application)
    selected_measures = application.get("safety_measures") or []
    measure_catalog = application.get("safety_measures_catalog") or []

    # 上传 DOCX 的票里没有表单填报值（permit_fields），JSA 就从解析出的申请里取常用字段；
    # 认不出作业类型时不硬猜——直接降级为空分析，等人补类型。
    job_type_name = _infer_job_type(application)
    type_def, _ = get_permit_type(job_type_name)
    jsa_result = approved_jsa if approved_jsa is not None else (
        _build_jsa_result(application, None, type_def, job_id)
        if type_def
        else {
            "source": "unavailable",
            "type_name": job_type_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": f"未知的作业类型: {job_type_name or '(空)'}",
        }
    )
    # 安全措施由 AI 重新判定；上传票里原有的人工勾选（selected）保留在目录里备查，
    # 由 ai_needed 并列给出 AI 的判断，便于人工对照复核。
    measures = _catalog_to_measures(measure_catalog)
    measure_decisions = _decide_safety_measures(
        job_id, _infer_job_type(application), _jsa_measures_context(_docx_measures_context(application), jsa_result), measures
    )

    permit_content.update({
        "input_source": "docx",
        "source_document": application.get("source_document", {}),
        "hot_work_location": application.get("hot_work_location", ""),
        "document_safety_measures": selected_measures,
        "safety_measures_catalog": _annotate_catalog(measure_catalog, measure_decisions),
        "measure_decisions": measure_decisions,
    })
    document_measure_text = [
        item.get("description", "") if isinstance(item, dict) else str(item)
        for item in selected_measures
    ]
    permit_content["measures"] = list(dict.fromkeys(
        [*permit_content.get("measures", []), *filter(None, document_measure_text)]
    ))

    if jsa_result.get("hazards"):
        permit_content["hazards"] = jsa_result["hazards"]
        permit_content["measures"] = [
            measure for hazard in jsa_result["hazards"] for measure in hazard["measures"]
        ]
    jsa_result["document_safety_measures"] = selected_measures
    return {
        "task_id": f"TASK-{job_id}",
        "permit_draft_id": f"PD-{job_id}",
        "jsa_result": jsa_result,
        "permit_content": permit_content,
        "missing_fields": missing,
        "data_origin": {
            "application": "uploaded_docx",
            "jsa_result": jsa_result.get("source"),
            "safety_measures": "ai_decision" if measure_decisions.get("source") == _MEASURES_SOURCE_AI
                               else measure_decisions.get("source"),
        },
    }


def _missing_manual_fields(type_def: dict, values: dict) -> list:
    """手动填报时，列出必填但未填的字段（用表单标签，便于提示操作员）。"""
    from .workflow import resolve_fields

    missing = []
    for field in resolve_fields(type_def):
        if not field.get("required"):
            continue
        raw = (values or {}).get(field["key"])
        # 布尔字段的 False 是有效取值（相当于「否」），不算未填。
        if raw is None or raw == "" or raw == []:
            missing.append(field.get("form_label") or field["label"])
    return missing


def _manual_application_view(application: dict, values: dict) -> dict:
    """把许可证字段映射成下游沿用的申请字段，避免同一个值在表单里填两遍。"""
    view = dict(application)
    work_content = values.get("work_content") or application.get("job_content", "")
    if work_content:
        view["job_content"] = work_content
    area_unit = values.get("area_unit") or application.get("region", "")
    if area_unit:
        view["region"] = area_unit
    work_time = values.get("work_time")
    if isinstance(work_time, dict):
        view["planned_start"] = work_time.get("start") or ""
        view["planned_end"] = work_time.get("end") or ""
    work_people = values.get("work_people")
    if work_people:
        existing = application.get("personnel") or []
        first = dict(existing[0]) if existing and isinstance(existing[0], dict) else {}
        first["name"] = work_people
        view["personnel"] = [first, *existing[1:]]
    return view


# ============================================================
# AI 安全措施判定
# ============================================================

# 逐条判断属于分类任务，需要稳定输出，因此不复用全局温度（默认 0.7）。
# 注意：服务端处于思考模式时 temperature 不生效（DeepSeek 的 thinking 默认开启），
# 该常量当前不起实际作用，保留仅为表达意图。
MEASURES_TEMPERATURE = 0.0

# 措施判定的思考强度。服务端默认 thinking=enabled 且 effort=high，实测真实判定会消耗
# 数千 thinking token（占输出 90% 以上），单次 30~130 秒、抖动极大；改成 low 后 3 次实测
# 平均 27.9 秒且判定逐条一致（详见 docs/update/update_2026-10-13.md 第十一节）。
MEASURES_REASONING_EFFORT = "low"
_MEASURES_SOURCE_AI = "ai"
_MEASURES_SOURCE_NO_MEASURES = "no-measures"
_MEASURES_SOURCE_UNAVAILABLE = "unavailable"
_TRUE_LITERALS = ("true", "1", "是", "yes")
_FALSE_LITERALS = ("false", "0", "否", "no")


def _coerce_needed(value):
    """把模型返回的布尔值规范化；无法判定的返回 None（视为非法）。

    宽松接受 true/false 的字符串与 1/0 —— 各家模型对 JSON 布尔的遵循度不一致，
    但这几种写法语义无歧义；其余一律判非法。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_LITERALS:
            return True
        if text in _FALSE_LITERALS:
            return False
    return None


def _measures_user_message(type_name: str, context_lines: list, measures: list,
                           kb: dict = None, kb_items: list = None) -> str:
    """构造判定用的用户消息。

    给了与 measures 等长的 kb_items 时，逐条附上知识库里的**条文依据**
    （压成「适用情形 → 要求」一行），让模型按办法判定而不是凭常识。
    """
    lines = [f"作业类型：{type_name}", "", "本次作业申请信息："]
    lines.extend(f"- {line}" for line in context_lines if line)
    lines.extend(["", f"安全措施清单（共 {len(measures)} 条）："])
    articles = {a["article_no"]: a for a in (kb or {}).get("articles") or []}
    for pos, item in enumerate(measures):
        lines.append(f"{item['index']}. {item['text']}")
        kb_item = kb_items[pos] if kb_items else None
        if not kb_item:
            continue
        for no in kb_item.get("refs") or []:
            article = articles.get(no)
            if article:
                lines.append(f"   〖依据·第{no}条〗{_basis_excerpt(article, item['text'])}")
    return "\n".join(lines)


def _basis_coverage(measure_text: str, snippet: str) -> float:
    """措施文本的二元组有多大比例出现在条文片段里。

    与知识库校验（K1/K2 的全库最优条文扫描）用同一口径，便于互相印证。
    """
    from .workflow.article_knowledge import normalize_text

    m = normalize_text(measure_text)
    if len(m) < 2:
        return 0.0
    s = normalize_text(snippet)
    mb = {m[i:i + 2] for i in range(len(m) - 1)}
    sb = {s[i:i + 2] for i in range(len(s) - 1)}
    return len(mb & sb) / len(mb) if mb else 0.0


def _scenario_lines(article: dict) -> list:
    """把正文的适用情形压成「情形 → 要求」若干行。"""
    lines = []
    for scen in article.get("scenarios") or []:
        when = (scen.get("when") or "").strip()
        requires = (scen.get("requires") or "").strip()
        line = f"{when} → {requires}" if when else requires
        if line:
            lines.append(line)
    return lines


def _basis_excerpt(article: dict, measure_text: str = "", limit: int = 320) -> str:
    """取条文作为判定依据。

    优先**逐字返回原文**：187 条正文里 94% 不超过 limit，原文信息最全，也不受情形
    切分误差影响（第 117 条的情形就漏掉了主旨句「以下情况应当编制吊装作业方案」）。
    只有原文超长时，才退化为「主旨句 + 按相关性排入的情形片段」——固定取前几片
    会让关键子句落在窗口外，而提示词写着「有依据以条文为准」，模型会以为自己拿全了。
    """
    raw = re.sub(r"\s+", " ", (article.get("text") or "")).strip()
    if len(raw) <= limit:
        return raw

    candidates = []
    head = re.split(r"[。；]", raw, maxsplit=1)[0].strip()
    if head:
        candidates.append(head + "。")
    lines = _scenario_lines(article)
    if lines:
        candidates.extend(sorted(lines, key=lambda ln: _basis_coverage(measure_text, ln), reverse=True)
                          if measure_text else lines)

    # 按相关性依次纳入直到接近上限，而不是固定取 2 片
    picked = []
    for piece in candidates:
        if not piece or piece in "；".join(picked):
            continue
        if picked and len("；".join(picked) + "；" + piece) > limit:
            continue
        picked.append(piece)
    text = "；".join(picked) or raw
    return text if len(text) <= limit else text[:limit] + "…"


def _resolve_permit_key(type_name: str):
    """作业类型名（中文名或英文 key）→ 模板/知识库用的 type_key；未知返回 None。"""
    if not type_name:
        return None
    try:
        from .workflow import list_permit_types
        for type_def in list_permit_types():
            if type_name in (type_def.get("name"), type_def.get("key")):
                return type_def.get("key")
    except Exception:
        logger.warning("[P1] 解析作业类型 key 失败", exc_info=True)
    return None


def _knowledge_items(type_name: str, measures: list):
    """取与本次措施**逐条对齐**的知识库条目，供逐条附条文依据；对不齐返回 ``None``。

    对不齐的典型情形：知识库缺失、作业类型未识别、上传 DOCX 自带清单与附录 1 内容不同。
    此时调用方退回「无条文依据」的判定，绝不硬套可能错位的依据。
    """
    key = _resolve_permit_key(type_name)
    if not key:
        return None
    try:
        from .workflow import align_measures, load_knowledge
        kb = load_knowledge()
        if not kb:
            return None
        return align_measures(key, measures, kb)
    except Exception:
        logger.warning("[P1] 知识库加载/对齐失败，退回无依据判定", exc_info=True)
        return None


def _decide_safety_measures(job_id: str, type_name: str, context_lines: list, measures: list) -> dict:
    """判定安全措施对本次作业是否需要（√ 需要 / × 不涉及）。

    有知识库且措施能与附录 1 逐条对齐时，逐条附上办法条文依据，让模型按条文判而不是凭常识；
    对不齐（知识库缺失 / 类型未识别 / 上传 DOCX 自带清单）则退回无依据判定。

    返回 {"source", "mode"?, "model"?, "decided_at", "items": [{"index", "needed", "origin"}], "error"?}。
    判定失败时 source 为 "unavailable" 且 items 为空 —— 调用方据此**不写任何勾**，
    退回人工逐条勾选：安全措施打错叉的后果比"没打勾"严重得多，宁可保守。
    """
    decided_at = datetime.now(timezone.utc).isoformat()
    if not measures:
        return {"source": _MEASURES_SOURCE_NO_MEASURES, "decided_at": decided_at, "items": []}

    kb_items = _knowledge_items(type_name, measures)
    grounded = kb_items is not None
    mode = "knowledge" if grounded else "full"
    # 统一按 1..N 编号送模型（解析器要求序号连续不重不漏），判定后映射回原措施序号
    view = [{"index": pos, "text": m["text"]} for pos, m in enumerate(measures, 1)]

    try:
        from .model.config import get_settings
        from .workflow import load_knowledge
        model = create_chat_model_with_logging(
            "P1-MEASURES", job_id,
            temperature=MEASURES_TEMPERATURE,
            reasoning_effort=MEASURES_REASONING_EFFORT,
        )
        response = model.invoke([
            SystemMessage(content=load_system_prompt("P1_MEASURES")),
            HumanMessage(content=_measures_user_message(
                type_name, context_lines, view,
                load_knowledge() if grounded else None,
                kb_items if grounded else None,
            )),
        ])
        text = getattr(response, "content", "") or ""
        if isinstance(text, list):
            text = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in text)
        parsed = _parse_measure_decisions(text, len(measures))
    except Exception as exc:
        logger.exception(f"[P1] 安全措施 AI 判定失败: job_id={job_id}")
        push_websocket_log(
            job_id, "ERROR", "AGENT",
            f"[P1] 安全措施 AI 判定失败，已退回人工勾选：{exc}"
        )
        return {
            "source": _MEASURES_SOURCE_UNAVAILABLE,
            "mode": mode,
            "decided_at": decided_at,
            "error": str(exc),
            "items": [],
        }

    # 序号映射回原措施（送模型时按 1..N 重编号）
    items = [{"index": measures[d["index"] - 1]["index"],
              "needed": d["needed"], "origin": "ai"} for d in parsed]
    items.sort(key=lambda x: x["index"])

    needed_count = sum(1 for item in items if item["needed"])
    grounded_note = "（逐条附办法条文依据）" if grounded else "（无条文依据）"
    logger.info(
        f"[P1] 安全措施判定完成: job_id={job_id} 共 {len(items)} 条{grounded_note}，需 {needed_count} 条"
    )
    push_websocket_log(
        job_id, "INFO", "AGENT",
        f"[P1] 安全措施判定完成：共 {len(items)} 条措施已逐条判定{grounded_note}，"
        f"判定需要 {needed_count} 条，待人工复核签字"
    )
    return {
        "source": _MEASURES_SOURCE_AI,
        "mode": mode,
        "model": get_settings().OPENAI_MODEL,
        "decided_at": decided_at,
        "items": items,
    }


def _parse_measure_decisions(text: str, expected_count: int) -> list:
    """解析并严格校验模型返回，任何不合规都抛 ValueError（由调用方转为兜底）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[A-Za-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON 对象")
    payload = json.loads(raw[start:end + 1])
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("返回里缺少 items 数组")
    if len(items) != expected_count:
        raise ValueError(f"items 条数 {len(items)} 与措施条数 {expected_count} 不一致")

    parsed = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("items 元素不是对象")
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            raise ValueError(f"index 非法: {item.get('index')!r}")
        needed = _coerce_needed(item.get("needed"))
        if needed is None:
            raise ValueError(f"index={index} 的 needed 非法: {item.get('needed')!r}")
        if index in parsed:
            raise ValueError(f"index={index} 重复")
        parsed[index] = needed
    if set(parsed) != set(range(1, expected_count + 1)):
        raise ValueError("index 未覆盖全部措施序号")
    return [{"index": index, "needed": parsed[index]} for index in sorted(parsed)]


def _manual_measures_context(application: dict, values: dict, type_def: dict) -> list:
    """手动填报路径：把表单填写值按中文标签整理成给模型看的上下文。"""
    from .workflow import resolve_fields

    view = _manual_application_view(application, values)
    lines = []
    if view.get("job_content"):
        lines.append(f"作业内容：{view['job_content']}")
    if view.get("region"):
        lines.append(f"作业区域：{view['region']}")
    if view.get("planned_start") or view.get("planned_end"):
        lines.append(f"作业时间：{view.get('planned_start', '')} 至 {view.get('planned_end', '')}")
    for field in resolve_fields(type_def):
        raw = values.get(field["key"])
        if raw in (None, "", [], {}):
            continue
        if field["key"] in ("work_content", "area_unit", "work_time"):
            continue  # 已在上面写过
        if isinstance(raw, dict):
            raw = f"{raw.get('start', '')} 至 {raw.get('end', '')}"
        elif isinstance(raw, (list, tuple)):
            raw = "、".join(str(item) for item in raw)
        lines.append(f"{field.get('form_label') or field['label']}：{raw}")
    return lines


def _related_permit_types(values: dict, main_key: str) -> list:
    """把表单勾选的「关联的其他作业类型」解析成结构化条目。

    只登记与取措施，**不做 √/× 判定**——本次票面只有主类型那一套措施；
    关联类型的措施取出来是为后续 JSA 报告准备（决定要覆盖哪些专项章节）。

    认不出的名字、与主类型相同的、重复的，一律跳过（不抛错）。
    """
    from .workflow import get_permit_type, list_safety_measures

    picked = values.get("related_works")
    if isinstance(picked, str):
        picked = [picked]
    related, seen = [], set()
    for name in picked or []:
        other, _config = get_permit_type(str(name).strip())
        if not other or other["key"] == main_key or other["key"] in seen:
            continue
        seen.add(other["key"])
        related.append({
            "key": other["key"],
            "name": other["name"],
            "measures": [item["text"] for item in list_safety_measures(other["key"])],
        })
    return related


def _build_manual_permit_result(application: dict, job_id: str, approved_jsa=None) -> dict:
    """按操作员选定的作业类型与填写值生成许可证，禁止混入工具中的固定 Mock 内容。"""
    from .workflow import (
        build_permit_rows,
        decision_map,
        get_permit_type,
        list_safety_measures,
        resolve_fields,
    )

    permit_type = application.get("permit_type") or ""
    type_def, _ = get_permit_type(permit_type)
    if not type_def:
        raise ValueError(f"未知的作业类型: {permit_type or '(空)'}")

    values = dict(application.get("permit_fields") or {})
    # 作业申请时间由系统填（提交那一刻）；表单上该字段是只读的，用户改不了。
    if any(field.get("auto") == "now" for field in resolve_fields(type_def)):
        values.setdefault("apply_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
    permit_content, _ = _build_permit_content(_manual_application_view(application, values))

    # 本次作业还涉及的其他作业类型：只登记、不判措施（票面上只有主类型那一套）。
    related = _related_permit_types(values, type_def["key"])
    # JSA：与作业票同源（同一份 values），专项章节由规则判定；失败只降级、不塞假数据。
    jsa_result = approved_jsa if approved_jsa is not None else _build_jsa_result(application, values, type_def, job_id, related)
    values = _merge_jsa_risks(values, type_def, jsa_result)
    # 安全措施改由 AI 逐条判定（√ 需要 / × 不涉及），人工只负责复核并签字。
    measures = list_safety_measures(type_def["key"])
    measure_decisions = _decide_safety_measures(
        job_id, type_def["name"], _jsa_measures_context(_manual_measures_context(application, values, type_def), jsa_result), measures
    )
    decided = decision_map(measure_decisions)

    permit_data = build_permit_rows(type_def["key"], values, measure_decisions)
    # 手动填报的缺项只看该类型表单的必填项，不掺入表单未采集的下游字段。
    missing = _missing_manual_fields(type_def, values)
    permit_content.update({
        "input_source": "manual",
        "permit_type": type_def["name"],
        "permit_title": permit_data["permit_title"],
        "permit_rows": permit_data["rows"],
        "permit_values": values,
        "job_type": type_def["name"],
        "missing_fields": missing,
        "measure_decisions": measure_decisions,
        "safety_measures": [
            {**item, "needed": decided.get(item["index"])} for item in measures
        ],
        "related_permit_types": related,
        "hazards": jsa_result.get("hazards", []),
        "measures": [
            measure
            for hazard in jsa_result.get("hazards", [])
            for measure in hazard["measures"]
        ],
    })

    return {
        "task_id": f"TASK-{job_id}",
        "permit_draft_id": f"PD-{job_id}",
        "jsa_result": jsa_result,
        "permit_content": permit_content,
        "missing_fields": missing,
        "data_origin": {
            "application": "manual_form",
            "jsa_result": jsa_result.get("source"),
            "safety_measures": "ai_decision" if measure_decisions.get("source") == _MEASURES_SOURCE_AI
                               else measure_decisions.get("source"),
            "related_permit_types": "manual_form",
        },
    }


def _push_p1_tool_log(level: str, message: str, data: dict = None):
    """将 P1 工具日志绑定到当前作业，避免通配日志串到其他作业窗口。"""
    push_websocket_log(_p1_job_context.get(), level, "TOOL", message, data)


# ============================================================
# Agent State Schema
# ============================================================

class PermitAgentState(TypedDict, total=False):
    """P1 Agent 内部状态"""
    messages: list



# ============================================================
# 工具定义
# ============================================================

@tool(description="提交作业申请，返回作业票草稿。当用户提供作业申请信息时触发。")
def permit_submit(application: str) -> str:
    """
    提交作业申请，返回作业票草稿。

    参数:
        application: 作业申请 JSON 字符串，包含 job_content, region, equipment,
                    personnel, planned_start, planned_end 等字段
    返回:
        标准 JSON 响应，包含 task_id, permit_draft_id, status, missing_fields
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> permit_submit 工具入口", {"application": application[:200] + "..." if len(application) > 200 else application})
    logger.info(f"[permit_submit] >>> 工具入口: application={application[:200]}...")
    try:
        data = json.loads(application)
    except json.JSONDecodeError:
        logger.warning(f"[permit_submit] !!! JSON 解析失败")
        _push_p1_tool_log("ERROR", "!!! permit_submit JSON解析失败")
        return json.dumps(make_error(
            code="PERMIT_INVALID",
            message="无效的 JSON 格式",
            recoverable=False
        ), ensure_ascii=False)

    # 提取基本信息
    job_content = data.get("job_content", "")
    region = data.get("region", "")
    equipment = data.get("equipment", [])
    personnel = data.get("personnel", [])

    # 生成 task_id
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    task_id = f"TASK-{job_content[:4].upper()}-{region[:2]}-{timestamp}"

    # 生成 permit_draft_id
    permit_draft_id = f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # 检查缺失字段
    missing_fields = []
    if not job_content:
        missing_fields.append("job_content")
    if not region:
        missing_fields.append("region")
    if not equipment:
        missing_fields.append("equipment")
    if not personnel:
        missing_fields.append("personnel")

    # 检查人员资质
    for p in personnel:
        quals = p.get("qualifications", [])
        if not quals:
            missing_fields.append(f"personnel_{p.get('name', 'unknown')}_qualifications")

    result = {
        "task_id": task_id,
        "permit_draft_id": permit_draft_id,
        "status": "draft",
        "missing_fields": missing_fields,
        "jsa_complete": len(missing_fields) == 0,
    }

    logger.info(f"[permit_submit] <<< 工具出口: task_id={task_id}, permit_draft_id={permit_draft_id}, missing_fields_count={len(missing_fields)}")
    _push_p1_tool_log("INFO", f"<<< permit_submit 工具出口", {"task_id": task_id, "permit_draft_id": permit_draft_id, "missing_fields_count": len(missing_fields)})
    return json.dumps(make_response("permit submit", result), ensure_ascii=False)


@tool(description="分析JSA，识别危害因素和对应措施。当用户请求分析JSA时触发。")
def jsa_analyze(task_id: str) -> str:
    """回读本次作业的 JSA 分析结果（不重复生成、不造数据）。

    JSA 与作业申请**同时生成**：`permit_submit` 之后 P1 会走分析链路产出结果并落盘。
    本工具只把那份结果取出来，保证同一个作业不出现两份不一致的分析——早先这里返回的是
    一段与作业内容无关的写死危害（被测出 P1 阶段的 jsa_result 全是假数据），已移除。

    参数:
        task_id: 任务唯一标识（形如 TASK-<job_id>）
    返回:
        标准 JSON 响应，包含 steps / hazards / main_risks 等分析结果
    """
    import json
    from .workflow import get_stage_result_path, read_json_file

    _push_p1_tool_log("INFO", ">>> jsa_analyze 工具入口", {"task_id": task_id})
    logger.info(f"[jsa_analyze] >>> 工具入口: task_id={task_id}")
    if not task_id:
        logger.warning("[jsa_analyze] !!! task_id 为空")
        _push_p1_tool_log("ERROR", "!!! jsa_analyze task_id为空")
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    job_id = task_id[5:] if task_id.startswith("TASK-") else task_id
    stored = read_json_file(get_stage_result_path(job_id, "p1")) or {}
    jsa_result = stored.get("jsa_result") or {}
    if not jsa_result:
        _push_p1_tool_log("WARNING", "本次作业还没有 JSA 结果：请先提交作业申请", {"job_id": job_id})
        return json.dumps(make_error(
            code="JSA_NOT_FOUND",
            message="本次作业还没有 JSA 分析结果：JSA 随作业申请一并生成，请先调用 permit_submit",
            recoverable=True
        ), ensure_ascii=False)

    if jsa_result.get("source") == "unavailable":
        _push_p1_tool_log(
            "ERROR",
            f"JSA 生成失败（已降级，不提供内容）: {jsa_result.get('error', '')}",
            {"job_id": job_id},
        )
        return json.dumps(make_error(
            code="JSA_UNAVAILABLE",
            message=f"JSA 分析未能生成：{jsa_result.get('error', '未知原因')}",
            recoverable=True
        ), ensure_ascii=False)

    steps = jsa_result.get("steps") or jsa_result.get("hazards") or []
    _push_p1_tool_log("INFO", f"JSA 回读完成: 共 {len(steps)} 条分析", {
        "job_id": job_id, "steps": len(steps), "source": jsa_result.get("source"),
    })
    return json.dumps(make_response("permit analyze-jsa", jsa_result), ensure_ascii=False)


@tool(description="生成作业票草稿，包含缺失项提示。当用户请求生成作业票草稿时触发。")
def permit_generate_draft(task_id: str) -> str:
    """
    生成作业票草稿。

    参数:
        task_id: 任务唯一标识
    返回:
        标准 JSON 响应，包含 permit_draft_id, content, missing_fields
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> permit_generate_draft 工具入口", {"task_id": task_id})
    logger.info(f"[permit_generate_draft] >>> 工具入口: task_id={task_id}")
    if not task_id:
        logger.warning(f"[permit_generate_draft] !!! task_id 为空")
        _push_p1_tool_log("ERROR", "!!! permit_generate_draft task_id为空")
        return json.dumps(make_error(
            code="TASK_NOT_FOUND",
            message="task_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "permit_draft_id": f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "content": {
            "task_id": task_id,
            "job_content": "受限空间作业",
            "region": "炼油厂区01",
            "equipment": ["反应器R-101", "管道P-205"],
            "medium": "原油",
            "personnel": [{"name": "张三", "badge_id": "P-101"}],
            "hazards": ["有毒有害气体", "高温烫伤", "人员误入"],
            "measures": ["气体检测", "通风", "警戒标识"]
        },
        "missing_fields": ["personnel_qualifications", "emergency_plan"],
        "requires_approval": True
    }

    logger.info(f"[permit_generate_draft] <<< 工具出口: permit_draft_id={result['permit_draft_id']}, missing_fields_count={len(result['missing_fields'])}")
    _push_p1_tool_log("INFO", f"<<< permit_generate_draft 工具出口", {"permit_draft_id": result['permit_draft_id'], "missing_fields_count": len(result['missing_fields'])})
    return json.dumps(make_response("permit generate-draft", result), ensure_ascii=False)


@tool(description="查询作业票状态。当用户查询作业票状态时触发。")
def permit_check(permit_id: str) -> str:
    """
    查询作业票状态。

    参数:
        permit_id: 作业票ID
    返回:
        标准 JSON 响应，包含 permit_id, status, task_id 等
    """
    import json
    from datetime import datetime, timezone

    _push_p1_tool_log("INFO", f">>> permit_check 工具入口", {"permit_id": permit_id})
    logger.info(f"[permit_check] >>> 工具入口: permit_id={permit_id}")
    if not permit_id:
        logger.warning(f"[permit_check] !!! permit_id 为空")
        _push_p1_tool_log("ERROR", "!!! permit_check permit_id为空")
        return json.dumps(make_error(
            code="PERMIT_NOT_FOUND",
            message="permit_id 不能为空",
            recoverable=False
        ), ensure_ascii=False)

    result = {
        "permit_id": permit_id,
        "status": "draft",
        "task_id": f"TASK-{permit_id.replace('PD-', '')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": None,
        "approved_at": None
    }

    logger.info(f"[permit_check] <<< 工具出口: permit_id={permit_id}, status={result['status']}")
    _push_p1_tool_log("INFO", f"<<< permit_check 工具出口", {"permit_id": permit_id, "status": result['status']})
    return json.dumps(make_response("permit check", result), ensure_ascii=False)


# ============================================================
# Agent 工厂 (HITL Enabled)
# ============================================================

# Agent 层级 Checkpointer - 用于 Agent 内部中断
_permit_checkpointer = MemorySaver()

# 全局 Agent 注册表 - 按 thread_id 缓存 Agent 实例，支持中断恢复
_agent_registry: Dict[str, Any] = {}


def create_permit_agent(job_id: str = "*"):
    """创建 P1 作业许可 Agent（基础版本，无 HITL）"""
    logger.info("[create_permit_agent] 创建 P1 Permit Agent（无 HITL）")
    llm = create_chat_model_with_logging("P1", job_id)
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]
    return create_agent(model=llm, tools=tools, system_prompt=load_system_prompt("P1"))


def create_permit_agent_with_hitl(thread_id: str = "default"):
    """创建 P1 作业许可 Agent。

    工具调用阶段不再逐个中断；P1 全部准备工作完成后，由
    ``_process_p1_result`` 统一产生一次最终人工审批。

    Args:
        thread_id: 线程ID，用于注册表管理
    """
    # 复用注册表中的 Agent（支持同一 thread_id 的中断恢复）
    if thread_id in _agent_registry:
        logger.info(f"[create_permit_agent_with_hitl] 复用已有 Agent: thread_id={thread_id}")
        return _agent_registry[thread_id]

    logger.info(f"[create_permit_agent_with_hitl] 创建新 Agent: thread_id={thread_id}")
    llm = create_chat_model_with_logging("P1", thread_id)
    tools = [permit_submit, jsa_analyze, permit_generate_draft, permit_check]

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=load_system_prompt("P1"),
        checkpointer=_permit_checkpointer,
    )

    # 注册到全局表
    _agent_registry[thread_id] = agent
    logger.info(f"[create_permit_agent_with_hitl] Agent 已注册: thread_id={thread_id}")
    return agent


def run_permit_agent_with_hitl(
    message: str | None,
    thread_id: str = "default",
    resume: bool = False,
    decision: str = "approve",
    notes: str = "",
) -> dict:
    """运行 P1 作业许可 Agent。

    新作业不做逐工具中断；resume 分支仅用于兼容已经产生的旧 checkpoint。

    Args:
        message: 输入消息
        thread_id: 线程ID（用于 checkpoint 恢复）
        resume: 是否从中断点恢复
        decision: 人工决定（approve/reject）
        notes: 人工决定备注

    Returns:
        包含 {"result": ..., "interrupted": bool, "next": list}
    """
    push_websocket_log(thread_id, "INFO", "AGENT", f">>> P1 Agent 入口", {"message": message[:100] + "..." if message and len(message) > 100 else message, "resume": resume})
    logger.info(f"[run_permit_agent_with_hitl] >>> Agent 入口: thread_id={thread_id}, message={message[:100] if message else 'None'}, resume={resume}")

    # 首次和恢复始终复用同一个 HITL Agent/checkpoint，工具不会预执行或重跑。
    agent = create_permit_agent_with_hitl(thread_id)
    hitl_config = get_agent_config(thread_id, "P1-HITL", get_llm_params())

    if resume:
        state = agent.get_state(hitl_config)
        if not state or not state.next:
            raise RuntimeError("P1 HITL checkpoint 不存在或已失效，无法从中断点继续")

        # 一个模型响应可能同时提出多个工具调用，阶段级确认对本批请求统一处理。
        action_count = 1
        interrupts = getattr(state, "interrupts", ()) or ()
        if interrupts:
            interrupt_value = getattr(interrupts[-1], "value", {}) or {}
            action_count = max(1, len(interrupt_value.get("action_requests", [])))
        decision_type = decision if decision in {"approve", "reject"} else "approve"
        decisions = [
            {"type": decision_type, **({"message": notes} if notes else {})}
            for _ in range(action_count)
        ]
        logger.info(
            f"[run_permit_agent_with_hitl] 从 checkpoint 继续: "
            f"thread_id={thread_id}, decision={decision_type}, actions={action_count}"
        )
        push_websocket_log(
            thread_id,
            "INFO",
            "AGENT",
            "P1 人工确认完成，从中断点继续",
            {"decision": decision_type, "actions": action_count},
        )
        context_token = _p1_job_context.set(thread_id)
        try:
            result = agent.invoke(Command(resume={"decisions": decisions}), hitl_config)
        finally:
            _p1_job_context.reset(context_token)
    else:
        if not message:
            raise ValueError("P1 首次执行时 message 不能为空")
        logger.info(f"[run_permit_agent_with_hitl] 正常执行新消息")
        push_websocket_log(thread_id, "INFO", "AGENT", f"正常执行新消息")
        context_token = _p1_job_context.set(thread_id)
        try:
            result = agent.invoke({"messages": [HumanMessage(content=message)]}, hitl_config)
        finally:
            _p1_job_context.reset(context_token)

    # 检查是否中断
    final_state = agent.get_state(hitl_config)
    interrupted = bool(final_state.next)

    if interrupted:
        logger.info(f"[run_permit_agent_with_hitl] !!! Agent 被中断: next={list(final_state.next)}")
        push_websocket_log(thread_id, "WARNING", "AGENT", f"!!! Agent 被中断", {"next_tools": list(final_state.next)})
    else:
        logger.info(f"[run_permit_agent_with_hitl] <<< Agent 执行完成")
        push_websocket_log(thread_id, "INFO", "AGENT", f"<<< Agent 执行完成")

    return {
        "result": extract_output(result) if not interrupted else None,
        "interrupted": interrupted,
        "next": list(final_state.next) if final_state.next else []
    }


def is_agent_interrupted(thread_id: str) -> bool:
    """检查指定 thread_id 的 Agent 是否处于中断状态"""
    if thread_id not in _agent_registry:
        return False
    agent = _agent_registry[thread_id]
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    return bool(state and state.next)


def get_agent_next_tools(thread_id: str) -> list:
    """获取 Agent 下一个待执行工具"""
    if thread_id not in _agent_registry:
        return []
    agent = _agent_registry[thread_id]
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    return list(state.next) if state and state.next else []


def clear_agent_registry(thread_id: str = None):
    """清除 Agent 注册表"""
    global _agent_registry
    if thread_id:
        _agent_registry.pop(thread_id, None)
    else:
        _agent_registry = {}


def reset_permit_execution(thread_id: str):
    """丢弃被否决的 P1 中断点，使下一次重试成为一次全新的执行。"""
    clear_agent_registry(thread_id)
    _permit_checkpointer.delete_thread(thread_id)


def run_permit_agent(message: str) -> str:
    """运行 P1 作业许可 Agent"""
    agent = create_permit_agent()
    agent_config = get_agent_config("default", "P1", get_llm_params())
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, agent_config)
    return extract_output(result)


def permit_demo(message: str, history: list = None) -> str:
    """Gradio ChatInterface 兼容格式"""
    return run_permit_agent(message)


# ============================================================
# 阶段执行入口
# ============================================================

def execute_stage(
    job_id: str,
    resume: bool = False,
    decision: str = "approve",
    notes: str = "",
) -> dict:
    """P1 阶段执行入口：作业预约、JSA分析与作业票

    支持 HumanInTheLoop 中断恢复
    """
    import json
    from datetime import datetime, timezone

    from .workflow import get_job_dir, get_stage_result_path, read_json_file, write_json_file
    from .utils import get_stage_logger

    log = get_stage_logger("P1")
    log.log_enter(job_id, {"resume": resume})
    result_file = get_stage_result_path(job_id, "p1")
    existing_result = read_json_file(result_file)

    result = {
        "job_id": job_id,
        "stage": "P1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": False,
    }

    app_file = get_job_dir(job_id) + "/application.json"
    application = read_json_file(app_file).get("application", {})

    if not application:
        logger.warning(f"[P1] !!! 作业申请为空: job_id={job_id}")
        result = {"error": "No application found", "completed": False}
        log.log_exit(job_id, result)
        return result

    # 两次审批共享 P1，但各有独立审批 ID。批准事实持久化后才允许出票。
    from .workflow import get_permit_type
    import hashlib
    import uuid

    fingerprint = hashlib.sha256(json.dumps(application, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    same_application = existing_result.get("jsa_application_fingerprint") == fingerprint
    approved = same_application and existing_result.get("jsa_approval", {}).get("decision") == "approve" and not existing_result.get("rejected")
    try:
        if approved:
            jsa = existing_result.get("jsa_result") or {}
            if jsa.get("source") != "ai" or not jsa.get("steps"):
                raise ValueError("已批准的 JSA 内容不可用，请重新进行 JSA 分析")
            result.update({"jsa_application_fingerprint": fingerprint,
                           "jsa_approval": existing_result["jsa_approval"],
                           "jsa_result": jsa,
                           "jsa_info_lines": existing_result.get("jsa_info_lines", []),
                           "p1_phase": "permit_review"})
            builder = _build_manual_permit_result if application.get("input_source") == "manual" else _build_docx_permit_result
            push_websocket_log(job_id, "INFO", "AGENT", "[P1] JSA 已批准，开始判定措施并生成作业票")
            return _process_p1_result(job_id, builder(application, job_id, approved_jsa=jsa), result)

        if same_application and existing_result.get("p1_phase") == "jsa_review" and not existing_result.get("rejected"):
            return existing_result

        type_def, _ = get_permit_type(application.get("permit_type") or _infer_job_type(application))
        if not type_def and application.get("input_source") == "manual":
            raise ValueError(f"未知的作业类型: {application.get('permit_type', '')}")
        values = _jsa_values(application, application.get("permit_fields"))
        related = _related_permit_types(values, type_def["key"]) if type_def else []
        jsa = (_build_jsa_result(application, values, type_def, job_id, related) if type_def else
               {"source": "unavailable", "error": "作业类型未识别，无法生成 JSA 报告"})
        from .workflow.jsa_report import build_info_lines
        result.update({
            "p1_phase": "jsa_review",
            "jsa_application_fingerprint": fingerprint,
            "jsa_result": jsa,
            "jsa_info_lines": build_info_lines(jsa, values, type_def, related) if type_def else [],
            "pending_confirmation": {
                "type": "jsa_approval", "approval_id": uuid.uuid4().hex,
                "message": "请先审核 JSA 分析报告；批准后才会判定安全措施并生成作业票",
            },
        })
        # 首次仅保存分析，清掉旧一轮草稿的可见数据，不能提前构建票面。
        write_json_file(get_job_dir(job_id) + "/permit.json", {
            "application": application, "jsa_result": jsa, "p1_phase": "jsa_review",
        })
        write_json_file(result_file, result)
        add_job_log(job_id, {"action": "jsa_pending_approval", "message": "JSA 分析结束，等待独立审批"})
        log.log_exit(job_id, result)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        write_json_file(result_file, result)
        log.log_error(job_id, exc)
        return result


def _process_p1_result(job_id: str, result_data: dict, existing_result: dict) -> dict:
    """处理 P1 执行结果，提取并保存作业票数据"""
    from datetime import datetime, timezone
    from .workflow import (
        get_job_dir,
        get_permit_type,
        get_stage_result_path,
        read_json_file,
        write_json_file,
    )

    result = existing_result.copy() if existing_result else {}
    for stale_key in ("rejected", "decision", "error"):
        result.pop(stale_key, None)
    result["job_id"] = job_id
    result["stage"] = "P1"
    result["completed"] = True
    result["completed_at"] = datetime.now(timezone.utc).isoformat()

    try:
        submit_data = None

        if isinstance(result_data, dict):
            if "result" in result_data and isinstance(result_data["result"], dict):
                submit_data = result_data["result"]
            elif "task_id" in result_data or "permit_draft_id" in result_data:
                submit_data = result_data
            elif "schema_version" in result_data:
                submit_data = result_data
        elif isinstance(result_data, str):
            try:
                parsed = json.loads(result_data)
                if isinstance(parsed, dict):
                    if "result" in parsed and isinstance(parsed["result"], dict):
                        submit_data = parsed["result"]
                    else:
                        submit_data = parsed
            except json.JSONDecodeError:
                logger.warning(f"[_process_p1_result] 无法解析 JSON，尝试从文本提取: {result_data[:200]}")
                submit_data = {}

        if submit_data:
            result["task_id"] = submit_data.get("task_id", "")
            result["permit_draft_id"] = submit_data.get("permit_draft_id", "")
            result["jsa_result"] = submit_data.get("jsa_result", {})
            result["permit_content"] = submit_data.get("permit_content", {})
            result["missing_fields"] = submit_data.get("missing_fields", [])
            result["data_origin"] = submit_data.get("data_origin", {})
            logger.info(f"[_process_p1_result] 成功提取数据: task_id={result.get('task_id')}, permit_draft_id={result.get('permit_draft_id')}")
        else:
            logger.warning(f"[_process_p1_result] 无法提取 submit 数据，result_data={result_data}")

        app_file = get_job_dir(job_id) + "/application.json"
        application = read_json_file(app_file).get("application", {})
        fallback_content, fallback_missing = _build_permit_content(application)
        result["task_id"] = result.get("task_id") or f"TASK-{job_id}"
        result["permit_draft_id"] = result.get("permit_draft_id") or f"PD-{job_id}"
        result["permit_content"] = result.get("permit_content") or fallback_content
        # 工具结果缺失时复用完整链路，保证 JSA → 措施判定 → 票面顺序一致。
        if not result.get("jsa_result"):
            rebuilt = (_build_manual_permit_result(application, job_id)
                       if application.get("permit_type")
                       else _build_docx_permit_result(application, job_id))
            result.update(rebuilt)
        # 手动填报的缺项由该类型的必填项决定，不被下游字段的兜底清单覆盖。
        if (result.get("permit_content") or {}).get("input_source") != "manual":
            result["missing_fields"] = result.get("missing_fields") or fallback_missing

        permit_data = {
            "task_id": result.get("task_id"),
            "permit_draft_id": result.get("permit_draft_id"),
            "application": application,
            "jsa_result": result.get("jsa_result"),
            "permit_content": result.get("permit_content"),
            "data_origin": result.get("data_origin", {}),
            "p1_phase": result.get("p1_phase"),
            "jsa_approval": result.get("jsa_approval"),
            "saved_at": datetime.now(timezone.utc).isoformat()
        }
        output_path = get_job_dir(job_id) + "/permit.json"
        write_json_file(output_path, permit_data)
        result["permit_file"] = output_path

    except Exception as e:
        result["error"] = str(e)

    # JSA 审批后生成的票面进入第二次审批。
    if not result.get("error"):
        import uuid
        result["pending_confirmation"] = {
            "type": "permit_final_approval",
            "approval_id": uuid.uuid4().hex,
            "fields": result.get("missing_fields", []),
            "message": "P1 作业申请、JSA 与作业票草稿已生成，请进行最终审批",
        }

    write_json_file(get_stage_result_path(job_id, "p1"), result)
    add_job_log(job_id, {
        "action": "execute_p1",
        "result": "success" if result["completed"] else "failed"
    })

    return result
