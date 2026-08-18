"""P8 飞书交互式 Card 全链路（V5 — cardkit 官方路径）。

================================================================================
职责（V5 整合版）
================================================================================

本模块整合 P8 飞书卡片全链路涉及的所有能力，分 5 段：

  段 1: 持久化层（card_index）
        data/feishu_card_index.json 存储 alert_id → card_id 映射，
        用于 callback 时反查「按了哪个 alert 对应的卡片」。

  段 2: cardkit 客户端（create_card_entity / update_card_entity）
        业务端直接调飞书 cardkit OpenAPI（不绕 Gateway）：
            POST   /open-apis/cardkit/v1/cards/        （创建卡片实体）
            PUT    /open-apis/cardkit/v1/cards/{id}    （全量更新；仅支持 Card 2.0）
            POST   /open-apis/auth/v3/tenant_access_token/internal
        tenant_access_token 按 (app_id, domain) 进程内缓存。

  段 3: Card 2.0 schema 构建（_build_processed_card）
        callback 命中后构造「已处置」绿卡（Card 2.0 schema）。

  段 4: 业务编排（process_card_callback）
        飞书 card.action.trigger 回调的业务逻辑：
            1. url_verification 挑战 → {challenge}
            2. 入参非法 → {status: error}
            3. 幂等命中（同 alert_id 已处置）→ {toast: warning}
            4. 首次成功：写审计 + 同步返回 {toast: success} + daemon 线程异步调
               cardkit 更新卡片（飞书 callback 2s 超时下不阻塞响应）。

  段 5: HTTP 入口（handle_card_callback）
        POST /api/feishu/card-callback：把 process_card_callback 的结果
        按 HTTP 状态码分流回写给飞书（url_verification/error/业务响应）。

================================================================================
飞书 Card 2.0 规范（参考 docs/飞书卡片教程/）
================================================================================

Card 2.0 schema（cardkit 创建 / 更新接口仅支持此结构）：
    {
        "schema": "2.0",
        "header": {"template": "red|green|...", "title": {"tag": "plain_text", "content": "..."}},
        "body":   {"elements": [
            {"tag": "markdown", "content": "..."},
            {"tag": "hr"},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "..."},
                 "type": "primary|default|danger",
                 "value": "<JSON 字符串（Card 2.0 强制 string）>"}
            ]}
        ]}
    }

cardkit 接口（教程：docs/飞书卡片教程/全量更新卡片.md）：
    POST /open-apis/cardkit/v1/cards/
    body = {
        "card": {"type": "card_json", "data": "<stringified Card 2.0 JSON>"},
        "uuid": "<可选幂等>"
    }
    resp = {"code": 0, "msg": "success", "data": {"card_id": "..."}}

    PUT /open-apis/cardkit/v1/cards/{card_id}
    body = {
        "card": {"type": "card_json", "data": "<stringified Card 2.0 JSON>"},
        "uuid": "<可选幂等>",
        "sequence": <int32, 必须严格递增>
    }

飞书侧回调响应协议（按官方，仅支持 toast；不能实时返回 card 替换）：
    {"toast": {"type": "success|warning|error", "content": "..."}}
    卡片替换必须由业务端后续调 PUT cardkit 全量更新（callback 期间错误码 200810）。

================================================================================
约束（CLAUDE.md / 安全）
================================================================================

    - 入参校验严格：缺 event / action.value.action → 400
    - api_key 字段（如果飞书带 X-Lark-Request-Token）走脱敏日志
    - 写审计日志时不在 logger 里 dump 整个 payload（含用户姓名）
    - cardkit 调飞书 OpenAPI：所有请求体严格按 schema；错误响应不抛裸 Exception
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger("server")

# ============================================================
# 路径常量 + .env 加载
# ============================================================

_ROOT = Path(__file__).resolve().parent.parent.parent  # 项目根
_AUDIT_LOG = _ROOT / "data" / "card_callbacks.jsonl"
_CARD_INDEX = _ROOT / "data" / "feishu_card_index.json"

for p in (_AUDIT_LOG, _CARD_INDEX):
    p.parent.mkdir(parents=True, exist_ok=True)

# 项目根 .env 一次性加载（FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN）
_ENV_PATH = _ROOT / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)
else:
    load_dotenv(override=False)

# 线程安全写（飞书可能在短时间 POST 多条；cardkit 也可能并发调）
_audit_lock = threading.Lock()
_index_lock = threading.Lock()
_token_lock = threading.Lock()

# action → 中文标签兜底（button_text 缺失时使用）
_ACTION_LABELS: Dict[str, str] = {
    "handle": "立即处理",
    "ack": "已知悉",
    "false_alarm": "误报",
    "confirm": "确认",
    "execute": "执行",
    "cancel": "取消",
    "reject": "驳回",
    "delete": "删除",
}

# 模块级 requests.Session（连接复用，与 channel_gateway_client.py 同模式）
_cg_session = requests.Session()
_cg_session.headers.update({
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "industrial-internet-agents/1.0 (feishu-card)",
})


# ============================================================
# 段 1: 持久化层（card_index）— alert_id → card_id 映射
# ============================================================

def _load_index() -> Dict[str, Dict[str, Any]]:
    """读 data/feishu_card_index.json 全量内容；不存在/解析失败返回 {}。"""
    if not _CARD_INDEX.exists():
        return {}
    try:
        with _CARD_INDEX.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(data: Dict[str, Dict[str, Any]]) -> None:
    """atomic write：写 .tmp → os.replace。"""
    tmp = _CARD_INDEX.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, _CARD_INDEX)


def register_card(
    alert_id: str,
    card_id: str,
    *,
    account_id: Optional[str] = None,
    message_id: Optional[str] = None,
    sequence: Optional[int] = None,
) -> Dict[str, Any]:
    """注册 alert_id → card_id 映射；写入 data/feishu_card_index.json（原子写）。

    同一 alert_id 二次注册会**覆盖**（保留 sequence / created_at 之外的字段）。
    """
    if not alert_id or not card_id:
        raise ValueError("alert_id 和 card_id 必必填")
    with _index_lock:
        data = _load_index()
        existing = data.get(alert_id, {})
        entry = {
            "card_id": card_id,
            "account_id": account_id or existing.get("account_id"),
            "message_id": message_id or existing.get("message_id"),
            "sequence": int(sequence) if sequence is not None else int(existing.get("sequence", 0)),
            "created_at": existing.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        data[alert_id] = entry
        _save_index(data)
    logger.info(
        "[feishu-card] register_card: alert_id=%s card_id=%s sequence=%d",
        alert_id, card_id, entry["sequence"],
    )
    return entry


def lookup_card_id(alert_id: str) -> Optional[Dict[str, Any]]:
    """按 alert_id 反查 {card_id, account_id, message_id, sequence, ...}；未命中返回 None。"""
    if not alert_id:
        return None
    with _index_lock:
        data = _load_index()
    entry = data.get(alert_id)
    if not isinstance(entry, dict):
        return None
    return entry


def evict_card(alert_id: str) -> bool:
    """删除一条映射（用于测试 / 14 天过期清理）；返回是否实际删除。"""
    if not alert_id:
        return False
    with _index_lock:
        data = _load_index()
        if alert_id not in data:
            return False
        del data[alert_id]
        _save_index(data)
    logger.info("[feishu-card] evict_card: alert_id=%s", alert_id)
    return True


# ============================================================
# 段 2: cardkit 客户端 — 直接调飞书 OpenAPI（不走 Gateway）
# ============================================================

class FeishuCardkitError(Exception):
    """飞书 cardkit / auth API 业务错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.request_id = request_id
        self.details = details or {}


def _domain_base(domain: str) -> str:
    return "https://open.larksuite.com" if domain == "lark" else "https://open.feishu.cn"


def _resolve_account_credentials(
    account_id: Optional[str],
) -> tuple[str, str, str]:
    """根据 account_id 取 (app_id, app_secret, domain)。

    三级 fallback（2026-08-18 多账户适配）：
        1. 账户级 env var：``FEISHU_<UPPER>_APP_ID / _APP_SECRET / _DOMAIN``
           （按 ``_account_id_to_env_suffix(account_id)`` 生成；与 feishu_config_app.py
           写入 gateway/.env 时同格式；account_id='P8' → 'FEISHU_P8_APP_SECRET'）
        2. 顶层 env var：``FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN``
           （兼容 feishu_config_app 在存在 default 账户时回写的情况）
        3. 顶层字段为空时，错误信息明确提示"账户级未配置，请到
           feishu_config_app UI 补 app_secret 或在 .env 加 FEISHU_<UPPER>_APP_SECRET"。

    Args:
        account_id: 飞书账号 ID；None / 'default' → 走顶层 env var。

    Returns:
        ``(app_id, app_secret, domain)`` 元组。

    Raises:
        FeishuCardkitError: 三级查找都拿不到 app_id 或 app_secret 时抛 503。
    """
    # 账户级 env var key（按 feishu_config_app._account_id_to_env_suffix 规则）
    suffix: str = ""
    if account_id:
        s = re.sub(r"[^a-z0-9]+", "_", account_id.strip().lower()).strip("_")
        suffix = s.upper() if s else ""

    if suffix:
        acct_app_id = os.environ.get(f"FEISHU_{suffix}_APP_ID", "").strip()
        acct_app_secret = os.environ.get(f"FEISHU_{suffix}_APP_SECRET", "").strip()
        acct_domain = os.environ.get(f"FEISHU_{suffix}_DOMAIN", "").strip()
        if acct_app_id and acct_app_secret:
            return acct_app_id, acct_app_secret, acct_domain or "feishu"

    # 顶层 fallback
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    domain = os.environ.get("FEISHU_DOMAIN", "feishu").strip() or "feishu"
    if app_id and app_secret:
        return app_id, app_secret, domain

    raise FeishuCardkitError(
        f"飞书账号凭证未配置（account_id={account_id!r}）。"
        "三级 fallback 都没找到 app_id + app_secret："
        f"账户级 FEISHU_{suffix or '<UPPER>'}_APP_ID/SECRET、"
        "顶层 FEISHU_APP_ID/SECRET。"
        "请到 feishu_config_app UI 配置 app_secret，"
        "或在 .env 手动加 FEISHU_<UPPER>_APP_SECRET。",
        http_status=503,
    )


# tenant_access_token 缓存：{(app_id, domain): (token, expires_at_unix_ms)}
_token_cache: Dict[tuple, tuple] = {}
_TOKEN_SAFETY_MARGIN_S = 60  # 剩余有效期 < 60s 视为过期


def _get_tenant_access_token(
    app_id: str,
    app_secret: str,
    domain: str = "feishu",
) -> str:
    """POST /open-apis/auth/v3/tenant_access_token/internal。

    按 (app_id, domain) 进程内缓存；剩余有效期 < 60s 时刷新。
    教程：docs/飞书卡片教程/自建应用获取token.md
    """
    cache_key = (app_id, domain)
    now = time.time()
    with _token_lock:
        cached = _token_cache.get(cache_key)
        if cached and cached[1] > now + _TOKEN_SAFETY_MARGIN_S:
            return cached[0]

    url = f"{_domain_base(domain)}/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = _cg_session.post(
            url,
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise FeishuCardkitError(
            f"token 请求网络失败: {exc}", http_status=0,
        ) from exc

    if resp.status_code != 200:
        raise FeishuCardkitError(
            f"token HTTP 失败 status={resp.status_code}: {resp.text[:200]}",
            http_status=resp.status_code,
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise FeishuCardkitError(f"token 响应非 JSON: {exc}") from exc

    if body.get("code", -1) != 0:
        raise FeishuCardkitError(
            f"token 业务失败: code={body.get('code')} msg={body.get('msg')}",
            code=body.get("code"),
            http_status=resp.status_code,
            details=body,
        )

    token = body.get("tenant_access_token")
    expire = int(body.get("expire") or 7200)
    if not token:
        raise FeishuCardkitError(
            f"token 响应缺 tenant_access_token: {body}",
            http_status=resp.status_code,
            details=body,
        )

    with _token_lock:
        _token_cache[cache_key] = (token, now + expire)
    logger.info(
        "[feishu-card] tenant_token  获取成功 app_id=%s expire=%ds",
        app_id, expire,
    )
    return token


def _stringify_card(card_json: Dict[str, Any]) -> str:
    """把 Card 2.0 dict 序列化为 stringified JSON（cardkit 接口约定）。"""
    return json.dumps(card_json, ensure_ascii=False, separators=(",", ":"))


def create_card_entity(
    card_json: Dict[str, Any],
    *,
    account_id: Optional[str] = None,
    op_uuid: Optional[str] = None,
) -> str:
    """POST /open-apis/cardkit/v1/cards/

    Args:
        card_json: Card 2.0 schema dict（必须含 ``schema: "2.0"``）。
        account_id: 飞书账号 ID；缺省走项目根 .env 默认账号。
        op_uuid: 幂等 UUID；缺省自动生成。

    Returns:
        card_id（字符串）。

    Raises:
        FeishuCardkitError: 飞书侧业务错误或网络错误。
    """
    app_id, app_secret, domain = _resolve_account_credentials(account_id)
    token = _get_tenant_access_token(app_id, app_secret, domain)
    uuid_str = op_uuid or _uuid.uuid4().hex

    url = f"{_domain_base(domain)}/open-apis/cardkit/v1/cards/"
    body = {
        "type": "card_json",
        "data": _stringify_card(card_json),
        "uuid": uuid_str,
    }
    try:
        resp = _cg_session.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise FeishuCardkitError(
            f"create_card_entity 网络失败: {exc}", http_status=0,
        ) from exc

    if resp.status_code != 200:
        raise FeishuCardkitError(
            f"create_card_entity HTTP 失败 status={resp.status_code}: {resp.text[:200]}",
            http_status=resp.status_code,
        )
    try:
        result = resp.json()
    except ValueError as exc:
        raise FeishuCardkitError(f"create_card_entity 响应非 JSON: {exc}") from exc

    if result.get("code", -1) != 0:
        # 业务失败：清 token 缓存（可能是 token 失效）
        _token_cache.pop((app_id, domain), None)
        raise FeishuCardkitError(
            f"create_card_entity 业务失败: code={result.get('code')} msg={result.get('msg')}",
            code=result.get("code"),
            http_status=resp.status_code,
            details=result,
        )

    card_id = (result.get("data") or {}).get("card_id")
    if not card_id:
        raise FeishuCardkitError(
            f"create_card_entity 响应缺 card_id: {result}",
            http_status=resp.status_code,
            details=result,
        )
    logger.info(
        "[feishu-card] create_card_entity 成功 app_id=%s card_id=%s",
        app_id, card_id,
    )
    return str(card_id)


def update_card_entity(
    card_id: str,
    card_json: Dict[str, Any],
    *,
    sequence: int = 1,
    op_uuid: Optional[str] = None,
    account_id: Optional[str] = None,
) -> None:
    """PUT /open-apis/cardkit/v1/cards/{card_id}

    Args:
        card_id: 飞书卡片实体 ID。
        card_json: Card 2.0 schema dict（必须含 ``schema: "2.0"``）。
        sequence: 操作序号（int32；必须严格递增）。
        op_uuid: 幂等 UUID；缺省自动生成。
        account_id: 飞书账号 ID；缺省走项目根 .env 默认账号。

    Raises:
        FeishuCardkitError: 飞书侧业务错误或网络错误。
    """
    if not card_id:
        raise ValueError("card_id 必必填")
    if sequence < 1:
        raise ValueError("sequence 必须 >= 1（int32 范围）")
    app_id, app_secret, domain = _resolve_account_credentials(account_id)
    token = _get_tenant_access_token(app_id, app_secret, domain)
    uuid_str = op_uuid or _uuid.uuid4().hex

    url = f"{_domain_base(domain)}/open-apis/cardkit/v1/cards/{card_id}"
    body = {
        "type": "card_json",
        "data": _stringify_card(card_json),
        "uuid": uuid_str,
        "sequence": int(sequence),
    }
    try:
        resp = _cg_session.put(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise FeishuCardkitError(
            f"update_card_entity 网络失败: {exc}", http_status=0,
        ) from exc

    if resp.status_code != 200:
        raise FeishuCardkitError(
            f"update_card_entity HTTP 失败 status={resp.status_code}: {resp.text[:200]}",
            http_status=resp.status_code,
        )
    try:
        result = resp.json()
    except ValueError as exc:
        raise FeishuCardkitError(f"update_card_entity 响应非 JSON: {exc}") from exc

    if result.get("code", -1) != 0:
        _token_cache.pop((app_id, domain), None)
        raise FeishuCardkitError(
            f"update_card_entity 业务失败: code={result.get('code')} msg={result.get('msg')}",
            code=result.get("code"),
            http_status=resp.status_code,
            details=result,
        )
    logger.info(
        "[feishu-card] update_card_entity 成功 card_id=%s sequence=%d",
        card_id, sequence,
    )


# ============================================================
# 段 3: Card 2.0 schema 构建 — 已处置绿卡
# ============================================================

def _lookup_user_name(open_id: Optional[str]) -> Optional[str]:
    """按 open_id 从 .env FEISHU_USER_MAP 反查中文名。

    FEISHU_USER_MAP 形态：``{"ou_xxx": {"role": "...", "name": "李宗睿"}}``。
    """
    if not open_id:
        return None
    raw = os.environ.get("FEISHU_USER_MAP", "").strip()
    if not raw:
        return None
    try:
        user_map = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(user_map, dict):
        return None
    entry = user_map.get(open_id)
    if isinstance(entry, dict):
        name = (entry.get("name") or "").strip()
        return name or None
    return None


def _action_label(action: Optional[str]) -> str:
    """action → 中文显示标签。未知 action 原样返回。"""
    if not action:
        return ""
    return _ACTION_LABELS.get(action.strip().lower(), action.strip())


def _check_already_processed(alert_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """检查 alert_id 是否已被处置过（同 alert_id 二次点击返回 warning toast）。

    按 audit log 倒序扫描（最新在前），找到该 alert_id 的最早一条记录。
    """
    if not alert_id:
        return None
    if not _AUDIT_LOG.exists():
        return None
    try:
        with _AUDIT_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("alert_id") == alert_id:
            return record
    return None


def _write_audit(record: Dict[str, Any]) -> None:
    """追加一条 JSONL 审计记录（线程安全）。"""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _audit_lock:
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _extract_action(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从飞书 Card 回调 payload 中抽取业务字段。

    Returns:
        dict 含 action / alert_id / button_text / operator_open_id / operator_name
        / open_chat_id / message_id；
        缺失关键字段（action）返回 None。
    """
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    action_obj = event.get("action")
    if not isinstance(action_obj, dict):
        return None
    # 2026-08-18：飞书实际发送的 `action.value` 形态：
    # 1) dict 直读（罕见，部分旧版本）
    # 2) 单层 JSON 字符串："{\"action\":\"handle\",...}"
    # 3) 双层 JSON 字符串（实采样本 2026-08-18 10:46:15 ngrok）：
    #    '"{\"action\":\"handle\",\"alert_id\":\"gas_v5_20260818_005\"}"'
    #    即 value 字段本身是 JSON-encoded 的字符串，里面再包一层 JSON。
    # 兼容三种形态：dict 直读 / string 单层 / string 双层。
    raw_value = action_obj.get("value")
    value: Optional[Dict[str, Any]] = None
    if isinstance(raw_value, dict):
        value = raw_value
    elif isinstance(raw_value, str) and raw_value.strip():
        candidate = raw_value
        # 最多剥两层 JSON 编码，直到变成 dict 或非 string 为止
        for _ in range(2):
            if not isinstance(candidate, str):
                break
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                candidate = None
                break
        if isinstance(candidate, dict):
            value = candidate
    if not isinstance(value, dict):
        return None
    action = value.get("action")
    if not isinstance(action, str) or not action.strip():
        return None
    action = action.strip()

    operator = event.get("operator") or {}
    open_id = (operator.get("open_id") or "").strip() or None

    # button_text 兜底：先看 action.text.content，没有则按 action 查中文标签
    raw_button_text = (action_obj.get("text") or {}).get("content", "").strip()
    button_text = raw_button_text or _action_label(action)

    # operator_name 兜底：先看 operator.user_name，没有则按 open_id 反查 USER_MAP
    raw_user_name = (operator.get("user_name") or "").strip()
    operator_name = raw_user_name or _lookup_user_name(open_id)

    return {
        "action": action,
        "alert_id": (value.get("alert_id") or "").strip() or None,
        "button_text": button_text or None,
        "operator_open_id": open_id,
        "operator_name": operator_name or None,
        "open_chat_id": ((event.get("context") or {}).get("open_chat_id") or "").strip() or None,
        "message_id": ((event.get("context") or {}).get("open_message_id") or "").strip() or None,
    }


def _build_processed_card(
    alert_id: Optional[str],
    action: str,
    button_text: str,
    operator_name: Optional[str],
    processed_at: str,
) -> Dict[str, Any]:
    """生成「已处置」替换卡（Card 2.0 schema）—— 群里所有人看到的卡片会被替换成这个。

    Card 2.0 schema 强制要求：
        - 顶层 ``schema: "2.0"``
        - elements 必须在 ``body.elements`` 下
        - 文本块用 ``tag: "markdown"``（不是 Card 1.0 的 ``div + lark_md``）
    """
    label = button_text or _action_label(action) or action
    operator_display = operator_name or "未知"
    alert_display = alert_id or "未知告警"

    return {
        "schema": "2.0",
        "header": {
            "template": "green",  # 绿色=已处置
            "title": {
                "tag": "plain_text",
                "content": f"已处置 · {label}",
            },
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**告警 ID**：`{alert_display}`\n"
                        f"**处理结果**：{label}\n"
                        f"**处理人**：{operator_display}\n"
                        f"**处理时间**：{processed_at}"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": (
                        "✅ 本告警已处置完毕，群内卡片已锁定。"
                        "如需重新发起处置，请联系值班管理员。"
                    ),
                },
            ],
        },
    }


# ============================================================
# 段 4: 业务编排（公开 API）— process_card_callback
# ============================================================

def _delete_feishu_message(message_id: str, account_id: Optional[str]) -> bool:
    """直接调飞书 ``DELETE /open-apis/im/v1/messages/{message_id}`` 删除指定消息。

    用于方案 A「删除原卡片 + 重发新卡片」流程：先删掉群里的红色告警卡，
    再在同一群重发绿色"已处置"卡，达成视觉替换（避开不稳定实时 card 替换）。

    Returns:
        True=删除成功；False=失败（会打 logger.exception，不抛异常）。
    """
    try:
        # 默认走 P8 账号（项目根 .env 配的是 FEISHU_P8_* 账户级 env var，
        # 没有顶层 FEISHU_APP_ID/SECRET；显式传 "P8" 命中三级 fallback 第一档）
        app_id, app_secret, domain = _resolve_account_credentials(account_id or "P8")
        token = _get_tenant_access_token(app_id, app_secret, domain)
        url = f"{_domain_base(domain)}/open-apis/im/v1/messages/{message_id}"
        resp = _cg_session.delete(url, headers={"Authorization": f"Bearer {token}"})
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:200]}
        if resp.status_code == 200 and body.get("code") in (0, None):
            logger.info(
                "[feishu-card] 删除消息成功 message_id=%s account_id=%s",
                message_id, account_id,
            )
            return True
        logger.warning(
            "[feishu-card] 删除消息失败 message_id=%s status=%s code=%s msg=%s",
            message_id, resp.status_code, body.get("code"), body.get("msg"),
        )
        return False
    except Exception:
        logger.exception("[feishu-card] 删除消息异常 message_id=%s", message_id)
        return False


def _replace_card_async(
    *,
    alert_id: Optional[str],
    chat_id: Optional[str],
    message_id: Optional[str],
    processed_card: Dict[str, Any],
    account_id: Optional[str] = None,
) -> None:
    """方案 A 异步替换卡片：先删除原卡片消息，再在同一群重发"已处置"卡。

    流程（替代之前的 cardkit update_card_entity —— 在我们 P8 app 上 im/v1/messages
    对 cardkit 创建的卡片引用返回 200621，所以 cardkit+im 官方路径不可用；
    此外飞书 callback 实时返回 card 让客户端替换的协议会触发 2026072 报错）。

    daemon 线程 fire-and-forget：失败仅写 logger.exception，不影响已返回的 toast 响应。
    """
    def _run() -> None:
        # 1) 删除原卡片（message_id 缺失时跳过；通常 callback payload 必带）
        if message_id:
            _delete_feishu_message(message_id, account_id)
        else:
            logger.warning(
                "[feishu-card] 重发跳过删除: alert_id=%s message_id 为空",
                alert_id,
            )

        # 2) 重发"已处置"卡（chat_id 缺失时跳过）
        if not chat_id:
            logger.warning(
                "[feishu-card] 重发跳过: alert_id=%s chat_id 为空", alert_id,
            )
            return
        try:
            # 延迟 import 避免循环依赖
            from agents.channel_gateway_client import send_message

            # Gateway 校验 text 非空：取 processed_card 第一段 markdown 兜底
            body_elems = (processed_card.get("body") or {}).get("elements") or []
            text_for_gateway = "[已处置]"
            if isinstance(body_elems, list) and body_elems:
                first_el = body_elems[0]
                if isinstance(first_el, dict):
                    text_for_gateway = str(first_el.get("content", "") or "")[:200] or "[已处置]"

            content_str = json.dumps(processed_card, ensure_ascii=False)
            result = send_message(
                text=text_for_gateway,
                channel="feishu",
                account_id=account_id or "P8",
                conversation_id=chat_id,
                receive_id_type="chat_id",
                msg_type="interactive",
                content=content_str,
                idempotency_key=f"replaced-{alert_id}" if alert_id else None,
            )
            logger.info(
                "[feishu-card] 重发已处置卡成功 alert_id=%s chat_id=%s "
                "new_message_id=%s status=%s",
                alert_id, chat_id,
                getattr(result, "platform_message_id", None),
                getattr(result, "status", None),
            )
        except Exception:
            logger.exception("[feishu-card] 重发已处置卡失败 alert_id=%s", alert_id)

    t = threading.Thread(
        target=_run,
        daemon=True,
        name=f"feishu-card-replace-{alert_id or 'unknown'}",
    )
    t.start()


def process_card_callback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理一个飞书 Card 按钮点击 payload，返回回给飞书的完整响应体。

    V5 行为（方案 A：删除 + 重发）：
        1. ``url_verification`` 挑战 → ``{"challenge": "..."}``
        2. 入参非法（缺 event/action.value）→ ``{"status": "error", "error": "..."}``
        3. 幂等命中（同 alert_id 已处置）→ ``{"toast": {"type": "warning", "content": "..."}}``
        4. 首次成功：写审计日志 + 同步返回 ``{"toast": {"type": "success"}}``
           + daemon 线程**异步**删原卡 + 重发绿色"已处置"卡（飞书 callback 2s 内必须返回）。

    Args:
        payload: 飞书 card.action.trigger 回调 payload dict。

    Returns:
        回给飞书的完整响应 dict（可能含 challenge / error / toast）。**只含 toast**，
        不含 card 字段（飞书客户端实时渲染 card 替换会触发 2026072 报错，
        见 docs/飞书卡片教程/，官方只支持 toast 响应）。

    Note:
        为什么不调 cardkit 全量更新（PUT /cardkit/v1/cards/{card_id}）：
            - 需要 send_to_group_card 走 cardkit create + im(card_id) 渲染链路，
              但 P8 app 实测 im 对 cardkit 创建卡片返回 200621 "parse card json err"，
              此官方路径在生产不可用。
            - cardkit update 只能改 cardkit 创建的卡片，改不了 inline 渲染的消息。
        为什么不用 callback 实时返回 card 字段：
            - 飞书客户端渲染会失败（2026072），用户看到"出错了"+ 卡片无变化。
        方案 A（删除 + 重发）：
            - DELETE /im/v1/messages/{message_id} 删原卡片
            - POST /im/v1/messages 用 inline Card 2.0 重发绿色"已处置"卡
            - 用户感知 = "样式变绿 + 按钮消失 + 显示处理人/决策"。
    """
    # 0. 入参兜底：非 dict / None 一律视为非法（飞书侧不应触发，防御性）
    if not isinstance(payload, dict):
        return {
            "status": "error",
            "error": "payload must be a JSON object",
        }

    # 1. url_verification 挑战（开发者后台首次配置回调 URL 时）
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # 2. 解析业务字段（缺 event / action.value.action → 返回 error）
    info = _extract_action(payload)
    if info is None:
        return {
            "status": "error",
            "error": "missing event.action.value.action",
        }

    # 3. 幂等检查（同 alert_id 已处置 → 拒绝 + 不替换卡）
    alert_id = info["alert_id"]
    existing = _check_already_processed(alert_id)
    if existing is not None:
        return {
            "toast": {
                "type": "warning",
                "content": (
                    f"该告警已被 {existing.get('operator_name') or '他人'} "
                    f"于 {existing.get('received_at') or '之前'} 处置"
                    f"（{_action_label(existing.get('action')) or existing.get('action')}）"
                ),
            },
        }

    # 4. 首次成功：写审计 + 构造替换卡（异步：删除原卡 + 重发新卡）
    received_at = datetime.now().isoformat(timespec="seconds")
    record = {
        "received_at": received_at,
        "event_id": (payload.get("header") or {}).get("event_id"),
        "event_type": (payload.get("header") or {}).get("event_type"),
        **info,
    }
    _write_audit(record)

    processed_card = _build_processed_card(
        alert_id=info["alert_id"],
        action=info["action"],
        button_text=info["button_text"] or "",
        operator_name=info["operator_name"],
        processed_at=received_at,
    )

    # 异步执行方案 A（不阻塞 callback 响应；飞书 2s 超时）。
    # 失败仅写 logger.exception，**不影响**已返回的 toast 响应。
    _replace_card_async(
        alert_id=info["alert_id"],
        chat_id=info["open_chat_id"],
        message_id=info["message_id"],
        processed_card=processed_card,
    )

    # 同步仅返回 toast（不返回 card 字段 —— 飞书渲染会 2026072）。
    return {
        "toast": {
            "type": "success",
            "content": (
                f"已记录您的处置"
                f"（{info['button_text'] or _action_label(info['action'])}）"
            ),
        },
    }


# ============================================================
# 段 5: HTTP 入口 — handle_card_callback
# ============================================================

def handle_card_callback(handler, data: Dict[str, Any]) -> None:
    """POST /api/feishu/card-callback：飞书 Card 按钮点击回调（V5 cardkit 路径）。

    业务流程委托给 :func:`process_card_callback`，本函数只负责日志 + HTTP 序列化。

    HTTP 状态码映射：
        - url_verification 挑战 → 200 + ``{"challenge": ...}``
        - 入参非法 → 400 + ``{"status": "error", "error": "..."}``
        - 业务响应（toast） → 200 + ``{"toast": ...}``
    """
    logger.info(
        "[POST] /api/feishu/card-callback 进入: schema=%s type=%s",
        data.get("schema"), data.get("type"),
    )

    reply = process_card_callback(data)

    if "challenge" in reply:
        logger.info(
            "[POST] /api/feishu/card-callback url_verification 挑战: challenge=%s",
            str(reply["challenge"])[:32],
        )
        handler.send_json(reply)
        return

    if reply.get("status") == "error":
        logger.warning(
            "[POST] /api/feishu/card-callback 参数错误: event 或 action.value 缺失 | payload=%s",
            str(data)[:600],
        )
        handler.send_json(reply, status=400)
        return

    info = _extract_action(data) or {}
    toast = reply.get("toast") or {}
    if toast.get("type") == "warning":
        logger.info(
            "[POST] /api/feishu/card-callback 幂等拦截: alert_id=%s",
            info.get("alert_id"),
        )
    elif toast.get("type") == "success":
        logger.info(
            "[POST] /api/feishu/card-callback 响应: action=%s alert_id=%s "
            "operator_open_id=%s button_text=%s",
            info.get("action"), info.get("alert_id"),
            info.get("operator_open_id"), info.get("button_text"),
        )

    handler.send_json(reply)


def handle_card_callback_list(handler) -> None:
    """GET /api/feishu/card-callbacks：列出最近 N 条审计记录（默认 50 条）。"""
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query)
    try:
        limit = int(qs.get("limit", ["50"])[0])
    except (ValueError, TypeError):
        limit = 50
    limit = max(1, min(500, limit))

    records: list = []
    if _AUDIT_LOG.exists():
        with _AUDIT_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-limit:][::-1]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    logger.info(
        "[GET] /api/feishu/card-callbacks 响应: count=%d limit=%d",
        len(records), limit,
    )
    handler.send_json({"status": "ok", "count": len(records), "records": records})


# ============================================================
# 导出
# ============================================================

__all__ = [
    # 段 1: card_index
    "register_card",
    "lookup_card_id",
    "evict_card",
    # 段 2: cardkit
    "FeishuCardkitError",
    "create_card_entity",
    "update_card_entity",
    # 段 3 / 4: callback 业务编排
    "process_card_callback",
    "handle_card_callback",
    "handle_card_callback_list",
    # 内部工具（test 用）
    "_build_processed_card",
]