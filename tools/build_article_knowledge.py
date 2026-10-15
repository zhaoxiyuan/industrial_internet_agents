#!/usr/bin/env python3
"""生成「办法知识库」：data/knowledge/permit_articles.json

两大部分
--------
1. **正文条款（187 条）**——判定的**依据**
   源文档用 Word 中文自动编号，条号在 textutil 转换中丢失，但通过 editor_sdk
   读取段落属性可拿到（``is_numbering`` / ``num_id`` / ``numbering_level``）。
   快照见 ``data/knowledge/_snapshot_doc_structure.json``，共 **187** 个编号段落，
   ``num_id`` 全为 ``000082``、1 级、连续 → 按序机械回填 ``article_no``，**无需人工锚点**。

   条款全文用**双锚点法**取：第 N 条的首字在语料中定位为 start_N，
   则第 N 条全文 = ``corpus[start_N : start_{N+1}]``。这样**条下的列表子项会一并收入**，
   修掉了旧版「32 条以『：』结尾、列表体整片缺失」的问题。

2. **附录 1 措施清单（待判对象）**——判定的**对象**
   10 张许可证表格，表头为「序号 / 安全措施 / 是划√否划× / 确认人」。
   按措施文本与 ``permit_templates.json`` 的交集识别每张表属于哪个作业类型。

语义约定
--------
    √  涉及   → 人工确认签字
    ×  不涉及 → 人工可忽略（附录 1 备注 2：不涉及的用斜划线「/」划除）
    空 AI 判不了 → 留空，操作员现场填

用法
----
    /Users/dyjyyds/miniconda3/envs/proj01/bin/python tools/build_article_knowledge.py
"""
import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents.workflow import list_permit_types, list_safety_measures  # noqa: E402

CORPUS_PATH = os.path.join(ROOT, "data", "knowledge", "permit_standard_full.txt")
SNAPSHOT_PATH = os.path.join(ROOT, "data", "knowledge", "_snapshot_doc_structure.json")
# 允许用环境变量覆盖输出路径：测试据此复跑到临时文件、与提交版本逐字节比对，而不污染工作区。
OUTPUT_PATH = os.environ.get(
    "KB_OUTPUT_PATH", os.path.join(ROOT, "data", "knowledge", "permit_articles.json")
)
# 产物里要记「生成日期」，但那会让复跑结果跨天必然漂移（幂等只在同一天成立）。
# 允许用环境变量钉住这一天：测试注回提交版本那一天，验的才是「内容没漂」。
UPDATED_AT = os.environ.get("KB_UPDATED_AT") or date.today().isoformat()

CHAPTER_HEAD = re.compile(r"^[ \t\u3000]*第([一二三四五六七八九十]+)章[ \t\u3000]*([^\n]{0,30})$", re.M)
BARE_CHAPTER = re.compile(
    r"^[ \t\u3000]*([\u4e00-\u9fa5（）()·、]{2,20}作业安全要求|通用要求|总则)[ \t\u3000]*$", re.M
)
APPENDIX_HEAD = re.compile(r"^[ \t\u3000]*附录\s*([0-9１-９])", re.M)
# 节标题（D1）：落在两条之间、没有条号，会被双锚点法切进上一条
SECTION_HEAD = re.compile(r"^[ \t\u3000]*第([一二三四五六七八九十]+)节[ \t\u3000]*([^\n]{0,30})$", re.M)

# 情态动词：命中即说明这句是「要求」，不能当「情形」
MODAL = re.compile(r"应当|应|不得|严禁|不应|必须|禁止|不准")
# 定义 / 分级词
DEFINITION = re.compile(r"是指|称为")
LEVELING = re.compile(r"分为.{0,8}级|级别划分为")
# 情形触发词
SCEN_CUE = re.compile(
    r"凡在|在.{0,30}(?:上|内|中|时|处|场所|边|附近|期间)|进入|使用|遇|"
    r"特级|一级|二级|拆除|涉及|对于|高处|受限空间|油气罐区|沟（坑）|"
    r"动火前|作业前|作业时|情况下"
)
IGNORED = re.compile(r"[\s，。；:：、,.;]+")


def norm(s):
    return IGNORED.sub("", s or "")


def chapter_ranges(corpus):
    heads = []
    for m in CHAPTER_HEAD.finditer(corpus):
        heads.append((m.start(), m.group(2).strip()))
    covered = {p for p, _ in heads}
    for m in BARE_CHAPTER.finditer(corpus):
        if m.start() not in covered:
            heads.append((m.start(), m.group(1).strip()))
    heads.sort()
    appendix = min((m.start() for m in APPENDIX_HEAD.finditer(corpus)), default=len(corpus))
    out = []
    for i, (pos, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else appendix
        out.append((name, pos, end))
    # 章 + 节标题的位置，供 D1 切分时截断点使用
    head_pos = [p for p, _ in heads] + [m.start() for m in SECTION_HEAD.finditer(corpus)]
    return out, appendix, sorted(set(head_pos))


def locate(corpus, preview, maxlen=24):
    """用 preview 前缀在语料里唯一定位；歧义或失败返回 None。"""
    if not preview:
        return None
    for k in range(maxlen, 7, -4):
        s = preview[:k]
        if len(s) < 8:
            break
        if corpus.count(s) == 1:
            return corpus.find(s)
    return None


# 引导句（含「下列/以下/如下」或后面跟列表），仅用于在子项上挂 lead 引用
LEAD_KW = re.compile(r"下列|以下|如下")
# 条件清单的唯一可靠标志：「…之一的」/「情况之一」/「条件之一」。
# 「…以下要求：」「…下列规定：」是**要求清单**，子项本身就是要求，不能转 when
COND_LEAD_RE = re.compile(r"之一的|情况之一|条件之一")
# 引导句里的「动作」：情态动词之后的部分，作为子项共享的 requires
ACTION_RE = re.compile(r"(应当|应|不得|严禁|不应|禁止|必须)\s*([^：:。；]{4,60})")
# R3：art050/051 的引导句是「符合以下条件之一的，属于特级动火作业」——结论不是动作而是判定，
# 补一类「属于/为 …级」结论词，否则这两条会退回路径 B、子项拿不到条件语义
LEVEL_CONC_RE = re.compile(r"(属于|为)\s*([^：:。；]{2,30})")


def split_scenarios(text):
    """按「；」拆片段，识别 when（情形）与 requires（要求）。

    D3：先试识别「引导句 + 列表」结构（如第124条「有下列十种情况之一的，禁止吊装作业：…」）。
    命中则把引导句里的**动作**提为共享 requires，每个列表子项转 when —— 子项本质是条件，
    此前因 when=None 被误当作无条件的通用要求。

    判据保守：引导句必须含 下列/以下/如下，且必须能提取出动作；否则退回逐片判定，只挂 lead。
    """
    flat = text
    lead = is_cond = action = None
    m = re.match(r"^(.{6,120}?)[：:]", flat)
    if m:
        preview = [s for s in re.split(r"[；;]", flat[m.end():]) if s.strip()]
        # 条件清单的唯一可靠标志是「…之一的」/「情况之一」/「条件之一」；
        # 「…以下要求：」「…下列规定：」是要求清单，其子项本身就是要求，不能转 when
        is_cond = bool(COND_LEAD_RE.search(m.group(1)))
        # 「…之一的：」出现即确系条件清单，**哪怕后面只有一个子项**也按「引导句 + 子项」
        # 处理（如 art051「属于一级动火作业：在…进行的动火作业」只有 1 项），
        # 否则子项会退回「无条件的通用要求」而丢掉条件语义。普通冒号仍要求 ≥2 项才算列表。
        if len(preview) >= 2 or is_cond:
            lead = m.group(0)
            if is_cond:
                am = ACTION_RE.search(m.group(1)) or LEVEL_CONC_RE.search(m.group(1))
                if am:
                    action = re.sub(r"[，,]?即[“”\"']*.{0,12}[“”\"']*", "",
                                    (am.group(1) + am.group(2))).strip(" 、")
            body = flat[m.end():]
        else:
            body = flat
    else:
        body = flat

    out = []
    for seg in re.split(r"[；;]", body):
        seg = seg.strip()
        if not seg:
            continue
        # 路径 A（仅条件清单）：子项是情形，引导句里的动作是要求
        if lead and is_cond and action:
            when = re.sub(r"(不吊|不准作业|不得作业)\s*。?$", "", seg)
            when = re.sub(r"的\s*。?$", "", when).strip()
            when = when.rstrip("。").strip()  # 情形是短语，不应带句末句号
            item = {"when": when or seg, "requires": action,
                    "conditional": True, "lead": lead, "from_lead": True}
            if when and when != seg:
                item["raw"] = seg
            out.append(item)
            continue
        # 路径 B（要求清单 / 常规片段）：子项原文**必须**留在 requires，lead 仅作引用
        mm = re.match(r"^(.{4,44}?)[，,](.{4,})$", seg)
        when = mm.group(1) if mm else None
        requires = mm.group(2) if mm else seg
        ok = bool(when and SCEN_CUE.search(when)
                  and not MODAL.search(when)
                  and not DEFINITION.search(when)
                  and "。" not in when
                  and not re.search(r"负责|下列|只可", when))
        item = {"when": when if ok else None, "requires": requires, "conditional": ok}
        if lead:
            item["lead"] = lead
        out.append(item)
    if not out:
        item = {"when": None, "requires": text, "conditional": False}
        if lead:
            item["lead"] = lead
        out = [item]
    return out


def category_of(text, scenarios):
    head = text[:40]
    if DEFINITION.search(head):
        return "definition"
    if LEVELING.search(text[:60]):
        return "level"
    return "conditional" if any(s["conditional"] for s in scenarios) else "general"


def parse_appendix_levels(corpus):
    """补录附录 2（动火作业等级划分）/ 附录 3（特殊情况下的高处作业分级）。

    两者在源文档里是纯文本段落（非表格），结构规整：
      附录 2：一~四（4 个业务系统）→（一）特级/（二）一级/（三）二级 → 1. 2. 3. …
      附录 3：一 →（一）（二）（三）→ 1. 2. 3. …
    输出扁平条目，每条带 appendix / system / level，便于按「作业等级」检索判定依据。
    """
    marks = [(m.start(), m.group(1), m.group(2))
             for m in re.finditer(r"^[ \t\u3000]*附录\s*([0-9１-９])\s*([^\n]{0,40})", corpus, re.M)]
    pos = {n: p for p, n, _ in marks}
    if "2" not in pos:
        return []

    ends = {"2": pos.get("3", len(corpus)), "3": len(corpus)}
    out = []

    # ---- 附录 2：4 个业务系统 × 3 个等级 ----
    seg = corpus[pos["2"]:ends["2"]]
    sys_blocks = re.split(r"^([一二三四])、\s*(.+?系统)动火作业等级划分\s*$", seg, flags=re.M)
    # split 结果：[前置, 序号, 名称, 内容, 序号, 名称, 内容, ...]
    for i in range(1, len(sys_blocks) - 2, 3):
        sname = sys_blocks[i + 1].strip()
        body = sys_blocks[i + 2]
        lv_blocks = re.split(r"^（([一二三四])）\s*(特级|一级|二级)动火作业\s*$", body, flags=re.M)
        for j in range(1, len(lv_blocks) - 2, 3):
            level = lv_blocks[j + 1].strip()
            lbody = lv_blocks[j + 2]
            items = [{"seq": k, "text": t.strip()}
                     for k, (_, t) in enumerate(
                         re.findall(r"^\s*(\d+)[.、]\s*(.+?)$", lbody, re.M), 1)]
            if items:
                out.append({"appendix": "附录2", "topic": "动火作业等级划分",
                            "system": sname, "level": level, "items": items})

    # ---- 附录 3：先按「一、/二、」切大项 ----
    #  一、特殊情况下的高处作业分级 →（一）（二）（三）三个等级
    #  二、高处作业的升级管理 → 说明句 + 9 条危险因素（N2：此前只切了「一、」）
    if "3" in pos:
        seg3 = corpus[pos["3"]:ends["3"]]
        parts = re.split(r"^([一二三四])、(.{0,40})$", seg3, flags=re.M)
        for i in range(1, len(parts) - 2, 3):
            title = parts[i + 1].strip()
            lbody = parts[i + 2]
            if "升级管理" in title:
                lines = [l.strip().rstrip("；;") for l in lbody.split("\n")]
                items_txt = [l for l in lines
                             if len(l) > 6 and "如存在" not in l and not l.startswith("PAGE")]
                rec = {"appendix": "附录3", "topic": title, "system": None,
                       "level": "升级管理（在原等级上升级，最高Ⅳ级）",
                       "items": [{"seq": k, "text": t} for k, t in enumerate(items_txt, 1)]}
                # R2：规则句原文（说明「升级」如何触发）必须逐字保留，此前被过滤掉
                rule = next((l for l in lines if "如存在" in l), None)
                if rule:
                    rec["note"] = rule
                out.append(rec)
                continue
            lv_blocks = re.split(r"^（([一二三四])）\s*(.+?)\s*$", lbody, flags=re.M)
            for j in range(1, len(lv_blocks) - 2, 3):
                head = lv_blocks[j + 1].strip()
                gbody = lv_blocks[j + 2]
                m = re.search(r"(Ⅰ|Ⅱ|Ⅲ|Ⅳ)[、，,]?(?:Ⅲ|Ⅳ)?级", head)
                level = m.group(0) if m else head[:12]
                items = re.findall(r"^\s*(\d+)[.、]\s*(.+?)$", gbody, re.M)
                if not items:  # （一）这类整段即一条条件
                    items = [(1, head)]
                out.append({"appendix": "附录3", "topic": "特殊情况下的高处作业分级",
                            "system": None, "level": level,
                            "items": [{"seq": int(s), "text": t.strip()} for s, t in items]})
    return out


def build_measures(snapshot):
    """从附录 1 的 10 张表里抽措施清单，并识别所属作业类型。"""
    types = {t["key"]: t["name"] for t in list_permit_types()}
    known = {k: [_match_norm(m["text"]) for m in list_safety_measures(k)] for k in types}

    measures, unmatched = [], []
    for t in snapshot["tables"]:
        rows = {}
        for c in t["cells"]:
            rows.setdefault(c["row"], {})[c["col"]] = c["text"]

        # ---- 三种表结构 ----
        # A. 通用：「序号 / 安全措施 / 是划√否划× / 确认人」
        # B. 射线：「作业步骤 / 注意事项 / 是否落实 / 确认人」
        # C. 临时用电：「符合划○，识别无此项划×」的 N 列网格（无序号，一格一项）
        head_row, mode = None, None
        for r, cells in sorted(rows.items()):
            joined = "".join(cells.values())
            if "序号" in joined and "安全措施" in joined:
                head_row, mode = r, "A"
                break
            if "作业步骤" in joined and ("注意事项" in joined or "措施" in joined):
                head_row, mode = r, "B"
                break
            if "符合划" in joined:
                head_row, mode = r, "C"
                break
        if head_row is None:
            continue

        items = []
        # D4(a) 与渲染端 agents/workflow/permit_template.py 的 _MEASURE_TAIL_RE 口径对齐：
        # 「其他/其它安全措施：…编制人（签字）」是自由填写行，不是待判措施
        TAIL = re.compile(r"^(?:其他|其它)安全措施")
        if mode == "A":
            for r in sorted(rows):
                if r <= head_row:
                    continue
                cells = rows[r]
                seq = cells.get(1, "").strip()
                text = cells.get(2, "").strip()
                if len(text) < 6 or not re.fullmatch(r"\d{1,2}", seq):
                    continue
                if TAIL.match(text):
                    continue
                items.append({"seq": int(seq), "text": text})
        elif mode == "B":
            seq = 0
            for r in sorted(rows):
                if r <= head_row:
                    continue
                cells = rows[r]
                text = (cells.get(2) or "").strip()
                if len(text) < 6:
                    continue
                seq += 1
                items.append({"seq": seq, "text": text, "phase": (cells.get(1) or "").strip()})
        else:  # C 网格：说明行之后的每个非空格都是一项
            # D4(b) 签署区（送电/关闭栏）不是 ○ 网格项，与 SKIP 一并剔除
            SKIP = re.compile(
                r"签字|电工证号|年\s*月|时间[：:]|许可证关闭|操作人|申请人|确认人"
                r"|隐患|到期|^送电|^断电|^拆线|^确认时间|^电源接入点$"
            )
            seq = 0
            for r in sorted(rows):
                if r <= head_row:
                    continue
                for col in sorted(rows[r]):
                    text = rows[r][col].strip()
                    text = re.sub(r"^[（(□\s]+", "", text).strip()
                    if len(text) < 4:
                        continue
                    if SKIP.search(text) or text.endswith("：") or text.startswith("其他"):
                        continue
                    seq += 1
                    # D4(c) 保留原文，不改写
                    items.append({"seq": seq, "text": text, "layout": "grid"})
        if not items:
            continue

        nt = [_match_norm(i["text"]) for i in items]
        best, best_hit = None, 0
        for key, klist in known.items():
            if not klist:
                continue
            hit = sum(1 for k in klist if any(k == n or (len(k) > 8 and k in n) for n in nt))
            if hit > best_hit:
                best, best_hit = key, hit
        # 兜底：临时用电在 permit_templates 里无措施表（○ 网格），交集法判不出，按用词判定
        if best is None:
            joined = " ".join(i["text"] for i in items)
            if re.search(r"用电|配电|漏电|电缆|送电|一机一闸", joined):
                best = "temp_power"

        # 挂条文依据（仅在 MEASURE_REFS 里配置过的措施才有；未配置的 refs 为空）
        ref_map = MEASURE_REFS.get(best, {}) if best else {}
        for it in items:
            it["refs"] = ref_map.get(it["seq"], [])

        tmpl_count = len(known.get(best, [])) if best else 0
        measures.append({
            "table_id": t["table_id"],
            "table_mode": mode,
            "type_key": best,
            "type_name": types.get(best) if best else None,
            "match_hits": best_hit,
            "count": len(items),
            "template_count": tmpl_count,
            "extra": len(items) - best_hit,
            "items": items,
        })
        if best is None:
            unmatched.append(t["table_id"])
    return measures, unmatched


# ---------------------------------------------------------------------------
# 条款 ↔ 措施映射
#
# key = (type_key, 措施 seq)，value = 相关正文条号列表。
# 判据：措施是票面「检查项」，条款是规范「要求」，二者用词不同、无法靠文本相似度匹配
# （实测全库相似度仅 0.10~0.41 且经常误配），故此处为**人工语义判定**结果。
#
# 用途：判定层把条文原文作为「依据」附给模型，让它按办法判而不是凭常识。
# 例：动火#7 乙炔瓶直立 ← 第67条「使用气焊（割）动火作业时，乙炔瓶应当直立放置」
# ---------------------------------------------------------------------------
MEASURE_REFS = {
    # K2（2026-10-12）：补齐 8 条 —— #1/#6→58（吹扫置换 + 防火隔绝）、#3→56（孔洞窨井地沟封盖）、
    # #5→66（高处动火防火花溅落）、#8→71（现场配备消防器材）、#10/#11→62（动火点周围距离内禁止作业）、
    # #12→[15, 36]（作业风险评估 + 交叉作业指定专人协调）。动火 15 条至此全覆盖。
    "hot_work": {1: [58], 2: [57], 3: [56], 4: [61], 5: [66], 6: [58], 7: [67], 8: [71],
                 9: [68], 10: [62], 11: [62], 12: [15, 36], 13: [59], 14: [70], 15: [17]},
    # K1：第四轮校验修正 1 条错配（受限空间#13 应急冲洗水 → 87，原 82 是「个体防护装备」）
    # K2（2026-10-12）：补齐 11 条。37 = 能量隔离/倒空置换，73 = 安全隔离（加盲板），75 = 通风（含
    # "不应充纯氧气或富氧空气"），76 = 气体检测合格，82 = 个体防护装备，86 = 监护与联络，87 = 出入口
    # 畅通/警示标志/应急物资，144 = 手持电动工具。#10（停止扬尘）办法中无对应条文 → 不挂。
    # 第 4 轮裁决（2026-10-12）：#3 由通用第 37 条改挂本类型章**第 74 条**（逐字含"中和或者置换"）；
    # #9 由跨章第 144 条改挂 **[43, 81]**（43 逐字含"手持电动工具…符合作业安全要求"、81 含"应当使用防爆工具"）。
    "confined_space": {1: [73], 2: [37], 3: [74], 4: [75], 5: [75], 6: [76], 7: [77], 8: [87],
                       9: [43, 81], 11: [82], 12: [87], 13: [87], 14: [86], 15: [17]},
    # N3：第三轮校验修正 3 条错配（#3 防爆工具→99、#6 防烫→100、#8 同一管道→99）
    "line_open": {1: [95], 2: [100], 3: [99], 4: [99], 5: [100],
                  6: [100], 7: [100], 8: [99], 9: [17], 10: [98], 11: [92]},
    # K2（2026-10-12）：补齐 13 条。103 = 身体条件，104 = 安全标志/工具/30米通信，105 = 作业平台类，
    # 106 = 安全带安全绳，107 = 恶劣天气，108 = 高处动火防火隔离 + 邻近放空管线防护，111 = 舷（岛）外作业，
    # 112 = 不牢固结构件/工具袋，33 = 设置警戒线。#8（轻型棚承重梁柱）办法中无对应条文 → 不挂。
    "high_work": {1: [103, 112], 2: [108], 3: [104], 4: [106], 5: [104], 6: [105], 7: [105],
                  9: [112], 10: [106], 11: [107], 12: [108, 66], 13: [108], 14: [109], 15: [110],
                  16: [111], 17: [42], 18: [33], 19: [107], 20: [17]},
    # K2（2026-10-12）：补齐 14 条。118 = 安全距离/路径/沟坑/输电线路，123 = 作业前机具检查+锚点+恶劣天气，
    # 119 = 指挥人员（联络信号、核实吊重、捆绑衬垫），121 = 操作人员（支垫不牢不起吊），
    # 124 = 十不吊，32/33 = 资格证书 + 个体防护装备。
    # #15（地下电缆/盖板）、#17（上方管线防护）、#20（排气管阻火器）办法中确无对应条文 → 不挂。
    # 第 4 轮裁决（2026-10-12）：#18 并入通用第 33 条（"设置警戒线"）→ [33, 122]；
    # #14、#21 去掉与细则条信息等价的概括条（"十不吊"第 124 条）→ 分别只留 [119] / [123]。
    "lifting": {1: [117], 2: [117], 3: [32, 33], 4: [123], 5: [119], 6: [123], 7: [118],
                8: [118], 9: [118], 10: [118], 11: [123], 12: [123], 13: [121, 123],
                14: [119], 16: [119], 18: [33, 122], 19: [118], 21: [123], 22: [17]},
    # 临时用电 ○ 网格接入（2026-10-12）：模板侧解析器已支持 3 列网格，该类型变可达，故逐条补映射。
    # 全部只挂**本类型章内**的第 125~146 条（R1 / R5：优先本类型章专属条，不跨章）。
    # 逐条取自原文分句（R3）：
    #   2 上锁点 ← 137「总（分）配电箱使用时应当上锁」+ 141「上级开关应当断电、加锁并加挂安全警示标牌」
    #   3 可燃气体检测分析 ← 126（确需接临时电源时应当对周围环境进行可燃气体检测分析）
    #   4 配电箱 ← 136（设置端正、牢固；箱内整洁、接地良好）+ 137（电压标识、危险标识、编号和防雨措施）
    #   6/10/11/21 是「临时用电设备/线路」的通用合规项 ← 134（按供电电压等级和容量正确配置…并接地良好）；
    #     10 另挂 131（5 台以上或总容量 50 千瓦以上）说明「数量、功率」的口径，21 另挂 136 的「接地良好」分句
    #   7/9/17 手持与移动电动工具 ← 144（第 7 条整条；第 9 条取其「外观完好」分句；第 17 条取其「必须使用工业安全插座、插头」分句）
    #   12 一机一闸一漏 ← 139 逐字「临时用电设施应当做到“一机一闸一保护”」
    #   13 漏电电流 15mA/0.1s ← 139（RCD 选型安装）+ 144（额定漏电动作电流不大于 15mA、动作时间不大于 0.1s）
    #   14/15/18 电缆敷设 ← 133（沿墙/地面敷设的警告标志、穿越道路防机械损伤、埋地走向标志与 0.7 米、防护套管）；
    #     14 另挂 135 的「线路应当有良好的绝缘」分句
    #   16 架空线路 ← 132（专用电杆或支架、弧垂与地面距离、绝缘铜芯线）
    #   8 用电设备与气瓶/动火点的间距 ← 138（距配电箱、开关及电焊机等电气设备 15 米范围内不应存放易燃易爆危险物品）
    #   20 防爆区域 ← 127（火灾爆炸危险场所应使用相应防爆等级的电气元件，并采取相应防爆安全措施）
    # **不挂的 4 条**（正文确无对口条文，不硬凑）：#1 电源接入点确认、#19 移动式电缆盘、
    #   #22 雨天潮湿天停电已交代、#23 受限空间。
    "temp_power": {2: [137, 141], 3: [126], 4: [136, 137], 5: [128], 6: [134], 7: [144],
                   8: [138], 9: [144], 10: [131, 134], 11: [134], 12: [139], 13: [139, 144],
                   14: [133, 135], 15: [133], 16: [132], 17: [144], 18: [133], 20: [127],
                   21: [134, 136]},
    # 复审微调：动土#10「A 佩戴防护器具；B 可燃气体检测仪；C 有毒介质检测仪」——
    # 第 38 条覆盖 B/C，第 33 条（正确佩戴个体防护装备）覆盖 A，故并列
    # K2（2026-10-12）：补齐 5 条。149 = 地下隐蔽设施（电缆/管线），150 = 现场交底（含埋地电缆、
    # 通信线路），151/155 = 支撑牢固 + 防滑坡塌方，158 = 上下梯子/快速进出，159 = 监护与生产人员联系，
    # 13 = 监护人职责。#4（按图划线立桩）、#8（报交通/消防部门）办法中无对应条文 → 不挂。
    "earth_work": {1: [149], 2: [150], 3: [160], 5: [151, 155], 6: [158], 7: [150, 159],
                   9: [42], 10: [33, 38], 11: [154], 12: [13], 13: [17]},
    # K2（2026-10-12）：补齐 5 条。165 = 制定交通组织方案，168 = 交通警示标志与设施，
    # 166/167 = 影响消防通道须报告消防主管部门 / 施工中异常须报告，33 = 个体防护装备，13 = 监护人职责。
    "road_close": {1: [165], 2: [168], 3: [169], 4: [166, 167], 5: [33], 6: [13], 7: [17]},
    # K2（2026-10-12）：补齐 3 条。174 = 作业前检查（资格/剂量/剂量片/装置/警示设施），
    # 177/180 = 停电或无关人员进入时停源 + 卡源应急处置，178 = 作业完毕确认源归位。
    # #1（踏勘现场 + 专用电源上锁挂牌 + 防坠落）是混合项，办法中无对应条文 → 不挂。
    "radiation": {2: [174], 3: [177], 4: [177, 180], 5: [178]},
    # K2（2026-10-12）：补齐 9 条。37 = 消除残余能量，33 = 个体防护装备/警戒线，43 = 工器具检查+清理现场，
    # 8/15 = 安全技术交底 + 风险评估与作业方案，42 = 现场照明，70 = 防爆摄录设备，13 = 监护人职责。
    # #5（紧固试压管线）、#6（检漏人员站位）属试压专项操作，办法中无对应条文 → 不挂。
    # 第 4 轮裁决（2026-10-12）：#12 撤掉**特级动火专属**的第 70 条（措辞是从特级动火票 #14 移植来的，
    # 不能反推条文适用），改为 [34, 46]（两条都明确覆盖"非常规作业的视频监控"）；
    # #8 去掉与前两条重叠 0.55 的第 8 条并补交底条 → [15, 40]；#7 并入第 43 条（含 GB 2894 安全警示标志）。
    "non_routine": {1: [37], 2: [37], 3: [33], 4: [43], 7: [33, 43], 8: [15, 40], 9: [33],
                    10: [42], 11: [43], 12: [34, 46], 13: [13]},
}

# 每次修改 MEASURE_REFS 都要同步更新这里：运维一眼能看出"这份库的映射换过没有"，
# 不必去比对 measure_refs 的 SHA 指纹。（R2）
MAPPING_UPDATED_AT = "2026-10-12"

# 已人工看原文复核、确认「现状正确」的映射：自检时标注出来，不再当待办刷。
# 这些都是扫描的跨章节误报（措辞重叠导致"更优"），语义上现挂的条文才是对的。
MAPPING_REVIEWED = {
    ("lifting", 21),        # 露天风力 ← 123「六级及以上大风不应露天吊装」，扫描指到 37（设备处理）
    ("earth_work", 10),     # 个体防护装备 ← 33/38，扫描指到管线打开章 100
    ("road_close", 5),      # 个体防护装备 ← 33（通用条），扫描指到管线打开章 100
}
# 注：临电 #6「焊接设备」/ #20「防爆区域」曾因同类跨章误报登记在此，
# 但它们的措施文本只有 4 字（3 个二元组），误差来自样本过少而非真跨章；
# 加入 `_AUDIT_MIN_BIGRAMS` 门槛后已由"文本过短不参与比对"自动排除，无需再白名单。

_NORM_RE = re.compile(r"[\s，。；：、,.;:（）()【】\[\]“”\"'·\-]+")

# 措施过短时（如临电网格的项名「防爆区域」仅 4 字 / 3 个二元组）覆盖率的分母太小，
# "巧合命中"就能把任何一个含该二字的条文顶到 1.00 → 判据失去意义，这类措施不参与比对。
_AUDIT_MIN_BIGRAMS = 6


def _match_norm(text):
    """度量比对专用归一化：去空白与标点，**含括号**。

    必须比 `norm()` 更狠一档：编辑器结构快照把 docx 里的符号字符（``<w:sym>``，
    就是勾选框）渲染成「(」，而运行时解析器读 XML 拿不到这些字符。不统一就会把
    「漏电电流：(15mA」和「漏电电流：15mA」判成两条不同措施，自检虚报 extra。
    """
    return _NORM_RE.sub("", text or "")


def _bigrams(text):
    """去标点后的字符二元组集合（与判定层 _basis_coverage 同一口径）。"""
    s = _NORM_RE.sub("", text or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def report_mapping_health(measures, entries):
    """映射健康度自检：打印「无依据清单」与「疑似错配」，只提示、不阻断构建。

    这两项都是提升判定质量最直接的抓手：

    - **无 refs 的措施** → 判定时没有条文依据可查，模型只能凭常识判；
    - **覆盖率 0 或显著低于全库最优条文** → 疑似条号挂错。注意这条扫描
      **会跨章节误报**（"警示"字样会把吊装项指到射线章），所以它只能提示
      "这里可能有问题"，给不出正确答案，必须人工看原文。
    """
    article_bigrams = {e["article_no"]: _bigrams(e["text"]) for e in entries}

    def cover(mb, no):
        ab = article_bigrams.get(no)
        return len(mb & ab) / len(mb) if mb and ab else 0.0

    print("\n  ── 映射健康度自检 ──")

    missing = {}
    for m in measures:
        seqs = [it["seq"] for it in m["items"] if not it.get("refs")]
        if seqs:
            missing[m["type_name"]] = seqs
    print(f"  无条文依据的措施 {sum(len(v) for v in missing.values())} 条"
          f"（补映射是提升判定质量最直接的一环）：")
    for name, seqs in missing.items():
        print(f"    {name:<20}{len(seqs):>3} 条   #{' #'.join(map(str, seqs))}")

    suspicious = []
    skipped_short = []
    for m in measures:
        for it in m["items"]:
            refs = it.get("refs") or []
            if not refs:
                continue
            mb = _bigrams(it["text"])
            if len(mb) < _AUDIT_MIN_BIGRAMS:
                skipped_short.append(f"{m['type_name']}#{it['seq']}")
                continue
            # 多引用时取其中最高的：互补引用（各覆盖措施半句）是正常形态，不该报成异常
            cur = max(cover(mb, no) for no in refs)
            best_no, best = refs[0], cur
            for n2 in article_bigrams:
                c = cover(mb, n2)
                if c > best:
                    best_no, best = n2, c
            # 只在「存在够格的更优条文」时才告警，避免对本来就难匹配的复合措施刷噪音
            if cur == 0 or (best >= 0.45 and best - cur > 0.25):
                suspicious.append((m["type_key"], m["type_name"], it["seq"], refs, cur, best_no, best))
    if suspicious:
        print(f"  ⚠ 疑似映射异常 {len(suspicious)} 处（须人工看原文；该扫描会跨章节误报）：")
        for key, name, seq, refs, cur, bno, best in suspicious:
            mark = "   ← 已人工复核，维持" if (key, seq) in MAPPING_REVIEWED else ""
            shown = "/".join(f"第{no}条" for no in refs)
            print(f"    {name} #{seq} → {shown} 最高覆盖 {cur:.2f}；全库最优 第{bno}条 {best:.2f}{mark}")
    else:
        print("  ✓ 未发现覆盖率异常的映射")
    if skipped_short:
        print(f"  （{len(skipped_short)} 条措施文本过短（<{_AUDIT_MIN_BIGRAMS} 个二元组）未参与比对："
              f"{'、'.join(skipped_short)}）")


def _own_line(corpus, p, t):
    """t 在语料位置 p 处是否独占一行（允许行首有空白）。"""
    line_start = corpus.rfind("\n", 0, p) + 1
    if corpus[line_start:p].strip():
        return False
    after = corpus[p + len(t):]
    j = 0
    while j < len(after) and after[j] in " \t\u3000":
        j += 1
    return j >= len(after) or after[j] == "\n"


def build_outline(corpus, ranges, starts, snapshot=None, appendix_start=None):
    """N5：章/节大纲。

    D1 把标题行从正文剥掉后，这些行不再属于任何条目，「作业前准备 / 作业过程」这层
    语境就丢了。这里把标题位置与条号对齐，供下游还原层级。
    """
    marks = [(pos, "chapter", name, i + 1) for i, (name, pos, _) in enumerate(ranges)]
    marks += [(m.start(), "section", m.group(0).strip(), None)
              for m in SECTION_HEAD.finditer(corpus)]
    # R4：源文档里部分节标题无编号（如「通用安全要求」「动火分类与分级」），
    # SECTION_HEAD 的「第X节」拿不到 → 用快照 headings 里的**正文节标题**兜底。
    # 严格限定：① 快照 level==2（节级）；② 落在正文区（附录之前），把附录表格标题
    # 「附录1 / XXX许可证（推荐样式） / 编号： / 一、…等级划分 /（二）一级动火作业」
    # 之类结构性噪声挡在外面；③ 必须**独占一行**，排除正文句子里顺带出现的同名短语
    # （如第28条里的「作业许可证推荐样式参见附录1」）；④ 按规范化文本去重，不受
    # 「离上一条太近」影响（「动火分类与分级」紧跟在章标题下一行，按距离去重会被误删）。
    limit = appendix_start if appendix_start is not None else len(corpus)
    known_lines = {norm(n) for _, _, n, _ in marks}
    for h in (snapshot or {}).get("headings", []):
        if h.get("level") != 2:
            continue
        t = (h.get("text") or "").strip()
        if not t or len(t) > 20 or "附录" in t:
            continue
        if re.match(r"^第[一二三四五六七八九十]+[章节]", t) or norm(t) in known_lines:
            continue
        p = corpus.find(t)
        if p < 0 or p >= limit or not _own_line(corpus, p, t):
            continue
        if any(abs(p - k) < 3 for k, *_ in marks):
            continue
        marks.append((p, "section", t, None))
        known_lines.add(norm(t))
    marks.sort(key=lambda x: x[0])
    out = []
    for pos, kind, name, chap_no in marks:
        nxt = next((i + 1 for i, s in enumerate(starts) if s is not None and s > pos), None)
        item = {"kind": kind, "name": name, "start_article_no": nxt}
        if chap_no:
            item["chapter_no"] = chap_no
        out.append(item)
    return out


def main():
    for p, hint in ((CORPUS_PATH, "语料"), (SNAPSHOT_PATH, "结构快照")):
        if not os.path.exists(p):
            sys.exit(f"缺少{hint}：{p}")

    with open(CORPUS_PATH, encoding="utf-8") as fh:
        corpus = fh.read().replace("\x0c", "\n")
    with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
        snap = json.load(fh)

    ranges, appendix_start, head_pos = chapter_ranges(corpus)

    def chapter_of(pos):
        name = ranges[0][0] if ranges else "（未知章）"
        for n, s, e in ranges:
            if s <= pos < e:
                return n
            if pos >= e:
                name = n
        return name

    arts = snap["numbering_paragraphs"]
    starts = [locate(corpus, a["preview"]) for a in arts]

    entries, problems = [], []
    for i, a in enumerate(arts):
        s = starts[i]
        if s is None:
            # 空编号段落（只有条号、无正文，位于正文最末）：保留条目以对齐真实条号
            if a.get("end_index", 0) - a.get("start_index", 0) <= 1:
                entries.append({
                    "id": f"art{i+1:03d}",
                    "article_no": i + 1,
                    "chapter": chapter_of(a.get("start_index", 0)),
                    "text": "",
                    "scenarios": [],
                    "category": "empty",
                    "phantom": True,
                    "locator": f"正文第{i+1}条（源文档空编号段，忠实保留）",
                })
                continue
            problems.append(f"第{i+1}条 无法在语料中定位（preview 为空或歧义）")
            continue
        nxt = next((x for x in starts[i + 1:] if x is not None and x > s), appendix_start)
        # D1：章/节标题没有条号，落在两条之间会被切进本条 → 截断到「下一条 / 下一个标题」取小者
        later_heads = [h for h in head_pos if s < h < nxt]
        if later_heads:
            nxt = later_heads[0]
        text = re.sub(r"\n{2,}", "\n", corpus[s:nxt].strip()).strip()
        scenarios = split_scenarios(text.replace("\n", ""))
        entries.append({
            "id": f"art{i+1:03d}",
            "article_no": i + 1,
            "chapter": chapter_of(s),
            "text": text,
            "scenarios": scenarios,
            "category": category_of(text, scenarios),
            "locator": f"正文第{i+1}条",
        })

    measures, unmatched = build_measures(snap)
    if unmatched:
        problems.append(f"以下表格未能识别作业类型：{unmatched}")

    # D7：附录 1 的两条填表规则原文（「√ 涉及 / × 不涉及」的规范依据）
    def find_rule(kw):
        for line in corpus.split("\n"):
            if kw in line:
                return line.strip()
        return None

    levels = parse_appendix_levels(corpus)
    filling_rules = [r for r in (find_rule("如需采取栏中所列措施"),
                                 find_rule("不涉及的，用斜划线")) if r]

    # 映射校验：配置的条号必须在正文里真实存在
    valid_no = {e["article_no"] for e in entries}
    bad_refs = [(k, s, r) for k, m in MEASURE_REFS.items() for s, rs in m.items()
                for r in rs if r not in valid_no]
    if bad_refs:
        problems.append(f"条款映射指向不存在的条号：{bad_refs}")
    # R5：MEASURE_REFS 以 (type_key, seq) 索引，措施条数/顺序一变即静默错配 → 显式校验序号存在，
    # 并把解析到的措施原文写进 JSON（mapped_measure_text），供审查与锁定测试比对。
    seq_by_type = {m["type_key"]: {it["seq"] for it in m["items"]} for m in measures}
    text_by_type = {m["type_key"]: {it["seq"]: it["text"] for it in m["items"]} for m in measures}
    bad_seq = [(k, s) for k, mm in MEASURE_REFS.items() for s in mm
               if s not in seq_by_type.get(k, set())]
    if bad_seq:
        problems.append(f"条款映射指向不存在的措施序号 (type_key, seq)：{bad_seq}")
    mapped_measure_text = {
        k: {str(s): text_by_type.get(k, {}).get(s) for s in sorted(mm)}
        for k, mm in MEASURE_REFS.items() if k in text_by_type
    }
    mapped = sum(1 for m in measures for it in m["items"] if it.get("refs"))

    cats = {}
    for e in entries:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    cond = cats.get("conditional", 0)
    # D3 自检：引导句命中数与动作提取成功数（动作提取失败的条目不转换，仅挂 lead）
    lead_all = sum(1 for e in entries for s in e["scenarios"] if s.get("lead"))
    # R1：只统计「路径 A 真条件转换」，避免把路径 B 的普通情形判定算进来导致数字虚高
    lead_ok = sum(1 for e in entries for s in e["scenarios"] if s.get("from_lead"))
    payload = {
        "version": 4,
        "updated_at": UPDATED_AT,
        "mapping_updated_at": MAPPING_UPDATED_AT,
        "source_document": snap["source_document"],
        "corpus": os.path.relpath(CORPUS_PATH, ROOT),
        "snapshot": os.path.relpath(SNAPSHOT_PATH, ROOT),
        "description": (
            "办法知识库：①正文 187 条（判定依据，每条含从原文拆出的适用情形）"
            "②附录 1 措施清单（判定对象，即票面上要打勾/打叉的项）。"
        ),
        "semantics": {
            "check": "√ 涉及该措施 → 人工确认签字",
            "cross": "× 不涉及 → 人工可忽略（附录 1 备注 2：不涉及的用斜划线「/」划除）",
            "conform": "○ 该项要求已符合 → 人工确认签字"
                       "（临时用电表头原文「符合划‘○’，识别无此项划‘×’」，× 即现场识别无此项）",
            "blank": "留空 → AI 判不了，由操作员现场填",
        },
        "stats": {
            "articles": len(entries),
            "conditional": cond,
            "non_conditional": len(entries) - cond,
            "by_category": cats,
            "measure_tables": len(measures),
            "measure_items": sum(m["count"] for m in measures),
            "appendix_level_groups": len(levels),
            "appendix_level_items": sum(len(g["items"]) for g in levels),
            "scenarios": sum(len(e["scenarios"]) for e in entries),
            "scenarios_with_lead": lead_all,
            "scenarios_lead_resolved": lead_ok,
            "measures_mapped": mapped,
        },
        "articles": entries,
        "measures": measures,
        "appendix_levels": levels,
        "outline": build_outline(corpus, ranges, starts, snap, appendix_start),
        "filling_rules": filling_rules,
        # R5：条款↔措施映射解析后的措施原文快照（键 = type_key → seq → 措施原文），
        # 供审查核对、并由 tests/test_article_knowledge.py 锁定，防措施改序后静默错配。
        "measure_refs": mapped_measure_text,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"已生成 {os.path.relpath(OUTPUT_PATH, ROOT)}")
    print(f"  正文条款 {len(entries)} 条（情形触发 {cond} / 通用 {len(entries)-cond}）")
    print(f"  附录措施表 {len(measures)} 张 / 措施 {payload['stats']['measure_items']} 条")
    print(f"  条款↔措施映射：{mapped} 条措施已挂条号（映射配置 {sum(len(m) for m in MEASURE_REFS.values())} 条）")
    print("  措施表归属（命中 = 与模板措施逐字匹配到的条数）：")
    for m in measures:
        print(f"    {m['table_id']} {str(m['type_name']):<16}{m['count']:>4} 条  "
              f"模板 {m['template_count']:>3}  命中 {m['match_hits']:>3}  额外 {m['extra']}")
    print(f"  附录 2/3 等级划分：{len(levels)} 组 / "
          f"{sum(len(g['items']) for g in levels)} 条")
    for g in levels:
        print(f"    {g['appendix']} {str(g['system'] or g['topic'])[:20]:<22}{g['level']:<8}{len(g['items'])} 条")
    print(f"\n  D3 引导句：命中 {lead_all} 片，动作提取成功 {lead_ok} 片")
    unresolved = {e["article_no"] for e in entries
                  for s in e["scenarios"] if s.get("lead") and not s["conditional"]}
    if unresolved:
        print(f"  未解析（仅挂 lead，未转条件）的条目：{sorted(unresolved)}")
    print()
    if problems:
        print("  ⚠ 问题：")
        for p in problems:
            print("   -", p)
    else:
        print("  条号回填完整、表格类型识别成功 ✓")

    report_mapping_health(measures, entries)


if __name__ == "__main__":
    main()
