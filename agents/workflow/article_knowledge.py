"""办法知识库（``data/knowledge/permit_articles.json``）的只读访问层。

P1 安全措施判定的**条文依据**来源：

- 按作业类型取附录 1 措施清单（每条含 ``refs`` 条号）
- 按条号取正文全文（含从原文拆出的适用情形 ``scenarios``）
- 把运行时的措施清单与知识库里同类型措施表**对齐**（条数相同 + 逐条文本高度一致）

设计口径：知识库是**增强**而非依赖。文件缺失/解析失败/对不齐时一律返回 ``None``，
调用方退回「无知识库」的老判定逻辑，绝不硬套可能错位的依据。

对齐只认「条数相同且逐条文本高度一致」——手动填报路径的措施取自模板，与附录 1 逐字一致，
故能对齐；上传 DOCX 的自带措施文本不同，自然对不齐并退回老逻辑。
"""
import difflib
import json
import os
import re
import threading

_LOCK = threading.Lock()
_CACHE = None
_CACHE_KEY = None  # (绝对路径, mtime_ns, size)：文件一变即自动失效

# 相似度判定的归一化：去掉空白与标点，避免因排版差异（空格、括号、顿号）误判为「对不齐」
_DROP_RE = re.compile(r"[\s，。；：、,.;:（）()【】\[\]“”\"'·\-]+")
_SIMILAR_THRESHOLD = 0.85


def get_knowledge_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "knowledge", "permit_articles.json")


def _fingerprint(path: str):
    """文件指纹 ``(绝对路径, mtime_ns, size)``；取不到（不存在/无权限）返回 ``None``。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), st.st_mtime_ns, st.st_size)


def load_knowledge(path: str = None):
    """加载并缓存知识库；任何失败返回 ``None``（不抛错，交由调用方兜底）。

    缓存键是 ``(路径, mtime, size)`` 而非路径本身：知识库重新生成后，
    长跑的服务在下一次调用就会自动改用新库，无需重启（A1）。
    """
    global _CACHE, _CACHE_KEY
    target = path or get_knowledge_path()
    key = _fingerprint(target)
    if key is None:
        return None
    with _LOCK:
        if _CACHE is not None and _CACHE_KEY == key:
            return _CACHE
        try:
            with open(target, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # 读坏时不写入缓存，下次调用会重试（宁可每次报错，也不悄悄沿用旧库）
            return None
        _CACHE, _CACHE_KEY = data, key
        return data


def clear_cache():
    """清空缓存（测试用；正常情况下文件变更会自动失效，无需手动调用）。"""
    global _CACHE, _CACHE_KEY
    with _LOCK:
        _CACHE, _CACHE_KEY = None, None


def measure_table(type_key: str, kb: dict = None):
    """取某作业类型的附录 1 措施表；无则 ``None``。"""
    kb = kb if kb is not None else load_knowledge()
    if not kb:
        return None
    for table in kb.get("measures") or []:
        if table.get("type_key") == type_key:
            return table
    return None


def measure_items(type_key: str, kb: dict = None) -> list:
    table = measure_table(type_key, kb)
    return list(table.get("items") or []) if table else []


def article_index(kb: dict = None) -> dict:
    """``{article_no: article}``。"""
    kb = kb if kb is not None else load_knowledge()
    if not kb:
        return {}
    return {a["article_no"]: a for a in kb.get("articles") or []}


def basis_texts(type_key: str, seq: int, kb: dict = None) -> list:
    """某条措施的条文依据 ``[{"article_no", "text", "scenarios"}]``；无则空表。"""
    kb = kb if kb is not None else load_knowledge()
    if not kb:
        return []
    items = measure_items(type_key, kb)
    refs = next((it.get("refs") for it in items if it.get("seq") == seq), None)
    if not refs:
        return []
    index = article_index(kb)
    out = []
    for no in refs:
        art = index.get(no)
        if art:
            out.append({
                "article_no": no,
                "text": art.get("text", ""),
                "scenarios": art.get("scenarios") or [],
            })
    return out


def normalize_text(text: str) -> str:
    """去掉空白与标点，供文本比对使用（归一化口径只此一处，判定层复用）。"""
    return _DROP_RE.sub("", text or "")


def text_similar(a: str, b: str, threshold: float = _SIMILAR_THRESHOLD) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return False
    if min(len(na), len(nb)) / max(len(na), len(nb)) < 0.8:
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= threshold


def align_measures(type_key: str, measures: list, kb: dict = None):
    """把运行时措施清单与知识库同类型措施表按位置对齐。

    返回知识库 items（与 ``measures`` 等长、位置一一对应）；对不齐返回 ``None``。
    """
    items = measure_items(type_key, kb)
    if not items or len(items) != len(measures):
        return None
    for kb_item, runtime in zip(items, measures):
        if not text_similar(kb_item.get("text", ""), runtime.get("text", "")):
            return None
    return items
