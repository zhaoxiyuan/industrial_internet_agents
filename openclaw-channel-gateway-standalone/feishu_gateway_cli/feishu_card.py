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
        "type": "card_json",
        "data": "<stringified Card 2.0 JSON>"
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
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

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
    "approve": "处理完成",
    "escalate": "升级",
    "resume": "恢复",
}

_TERMINAL_ACTIONS = {"false_alarm", "approve", "rectify", "reject", "escalate", "resume"}
_FOLLOW_UP_ACTIONS = (
    ("处理完成（归档）", "approve", "primary"),
    ("驳回", "reject", "danger"),
    ("升级", "escalate", "default"),
    ("恢复", "resume", "default"),
)

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
    card_json: Optional[Dict[str, Any]] = None,
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
            # 保留原始正文，供第一次非终态点击后只替换按钮、不改显示内容。
            "card_json": card_json if isinstance(card_json, dict) else existing.get("card_json"),
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
    # send_message(account_id=None) 会由 Gateway 使用 CG_DEFAULT_ACCOUNT_ID；
    # CardKit 创建/更新必须与消息发送使用同一个应用身份，因此这里采用同样默认值。
    resolved_account_id = (account_id or "").strip()
    if not resolved_account_id or resolved_account_id.lower() == "default":
        configured_default = os.environ.get("CG_DEFAULT_ACCOUNT_ID", "").strip()
        if configured_default and configured_default.lower() != "default":
            resolved_account_id = configured_default

    suffix: str = ""
    if resolved_account_id:
        s = re.sub(r"[^a-z0-9]+", "_", resolved_account_id.lower()).strip("_")
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
        f"飞书账号凭证未配置（account_id={resolved_account_id or account_id!r}）。"
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
) -> str:
    """POST /open-apis/cardkit/v1/cards/

    Args:
        card_json: Card 2.0 schema dict（必须含 ``schema: "2.0"``）。
        account_id: 飞书账号 ID；缺省走项目根 .env 默认账号。

    Returns:
        card_id（字符串）。

    Raises:
        FeishuCardkitError: 飞书侧业务错误或网络错误。
    """
    app_id, app_secret, domain = _resolve_account_credentials(account_id)
    token = _get_tenant_access_token(app_id, app_secret, domain)
    url = f"{_domain_base(domain)}/open-apis/cardkit/v1/cards"
    body = {
        "type": "card_json",
        "data": _stringify_card(card_json),
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
        "card": {
            "type": "card_json",
            "data": _stringify_card(card_json),
        },
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
    """检查 alert_id 是否已经收到终态决策。

    ack/handle 是第一阶段动作，不锁死卡片；只有终态动作才阻止再次决策。
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
        if (
            isinstance(record, dict)
            and record.get("alert_id") == alert_id
            and record.get("action") in _TERMINAL_ACTIONS
        ):
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
        # 2026-08-20 新增：卡片 value.job_id 透传（主流程作业 ID；callback 反查用）
        # 旧卡片无该字段 → 返 None（向后兼容；仅走审计 + 视觉替换，不调 CardActionAgent）
        "job_id": (value.get("job_id") or "").strip() or None,
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
    job_id: Optional[str] = None,
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

    visual = {
        "approve": ("green", "处理完成（已归档）"),
        "rectify": ("green", "整改完成（已归档）"),
        "reject": ("carmine", "已驳回"),
        "escalate": ("orange", "已升级"),
        "resume": ("blue", "已恢复"),
        "false_alarm": ("grey", "已标记误报（已归档）"),
    }
    template, status_title = visual.get(action, ("green", f"已处置 · {label}"))
    elements = [
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
        {"tag": "markdown", "content": f"✅ 本告警状态已更新为：**{status_title}**。"},
    ]
    if job_id:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看信息"},
                "type": "default",
                "width": "fill",
                "behaviors": [{"type": "open_url", "default_url": _detail_url(job_id, alert_id)}],
            }],
        })
    return {
        "schema": "2.0",
        "header": {
            "template": template,
            "title": {
                "tag": "plain_text",
                "content": status_title,
            },
        },
        "body": {"elements": elements},
    }


def _detail_url(job_id: Optional[str], alert_id: Optional[str]) -> str:
    """生成“查看信息”跳转地址；公网部署可用 P8_DETAIL_BASE_URL 覆盖。"""
    base = os.environ.get("P8_DETAIL_BASE_URL", "http://127.0.0.1:8080").strip().rstrip("/")
    url = base + "/p8_detail.html"
    if job_id:
        url += "?job_id=" + quote(job_id, safe="")
    if alert_id:
        url += ("&" if "?" in url else "?") + "p8_job_id=" + quote(alert_id, safe="")
    return url


def _button(label: str, action: str, button_type: str, alert_id: str, job_id: str) -> Dict[str, Any]:
    value = json.dumps(
        {"action": action, "alert_id": alert_id, "job_id": job_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": value,
        "width": "fill",
    }


def _build_follow_up_card(
    *,
    original_card: Optional[Dict[str, Any]],
    alert_id: str,
    job_id: str,
) -> Dict[str, Any]:
    """保留原卡内容，只把按钮区替换为第二阶段决策及 URL 跳转按钮。"""
    card = deepcopy(original_card) if isinstance(original_card, dict) else {
        "schema": "2.0",
        "header": {"template": "orange", "title": {"tag": "plain_text", "content": "告警处理中"}},
        "body": {"elements": [{"tag": "markdown", "content": f"**告警 ID**：`{alert_id}`"}]},
    }
    card["schema"] = "2.0"
    body = card.setdefault("body", {})
    elements = body.setdefault("elements", [])
    # 原发送卡的交互区是 column_set；正文等其他元素保持原样。
    elements[:] = [e for e in elements if not (isinstance(e, dict) and e.get("tag") in {"action", "column_set"})]

    columns = []
    for label, action, button_type in _FOLLOW_UP_ACTIONS:
        columns.append({
            "tag": "column", "width": "weighted", "weight": 1,
            "vertical_align": "center",
            "elements": [_button(label, action, button_type, alert_id, job_id)],
        })
    elements.append({"tag": "column_set", "flex_mode": "stretch", "columns": columns})
    # Card 2.0 URL 行为：只跳转，不产生 card.action.trigger 回调。
    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看信息"},
            "type": "default",
            "width": "fill",
            "behaviors": [{"type": "open_url", "default_url": _detail_url(job_id, alert_id)}],
        }],
    })
    return card


# ============================================================
# 段 4: 业务编排（公开 API）— process_card_callback
# ============================================================

def _patch_feishu_message(
    message_id: str,
    card_json: Dict[str, Any],
    *,
    account_id: Optional[str] = None,
) -> None:
    """使用 IM Message Patch 原位更新 inline interactive 卡片。

    这是旧 inline 卡片的兼容路径；新发送的 CardKit 实体卡优先走
    :func:`update_card_entity`。两条路径都保留原 ``message_id``，不会撤回或重发。
    """
    app_id, app_secret, domain = _resolve_account_credentials(account_id)
    token = _get_tenant_access_token(app_id, app_secret, domain)
    url = f"{_domain_base(domain)}/open-apis/im/v1/messages/{message_id}"
    try:
        resp = _cg_session.patch(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"content": _stringify_card(card_json)},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise FeishuCardkitError(
            f"patch message 网络失败: {exc}", http_status=0,
        ) from exc
    try:
        result = resp.json()
    except ValueError as exc:
        raise FeishuCardkitError(
            f"patch message 响应非 JSON: {exc}", http_status=resp.status_code,
        ) from exc
    if resp.status_code != 200 or result.get("code", -1) != 0:
        raise FeishuCardkitError(
            f"patch message 失败: code={result.get('code')} msg={result.get('msg')}",
            code=result.get("code"),
            http_status=resp.status_code,
            details=result,
        )


def _update_card_in_place(
    *,
    alert_id: str,
    message_id: Optional[str],
    processed_card: Dict[str, Any],
) -> None:
    """按发送时保存的 handle 原位更新卡片，绝不撤回或重发。

    CardKit 实体卡使用全量 PUT；历史 inline 卡片或索引缺失时使用消息 PATCH。
    对飞书交互窗口错误 200810 做有限退避，等待 callback 结束后再更新。
    """
    entry = lookup_card_id(alert_id) or {}
    card_id = str(entry.get("card_id") or "").strip()
    tracked_message_id = str(message_id or entry.get("message_id") or "").strip()
    account_id = entry.get("account_id")

    if not card_id:
        if not tracked_message_id:
            logger.warning(
                "[feishu-card] 原位更新跳过: alert_id=%s 无 card_id/message_id",
                alert_id,
            )
            return
        _patch_feishu_message(
            tracked_message_id,
            processed_card,
            account_id=account_id,
        )
        logger.info(
            "[feishu-card] inline 卡片原位 PATCH 成功 alert_id=%s message_id=%s",
            alert_id, tracked_message_id,
        )
        return

    sequence = int(entry.get("sequence") or 0) + 1
    retry_delays = (0.25, 0.75, 1.5, 3.0)
    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            update_card_entity(
                card_id,
                processed_card,
                sequence=sequence,
                account_id=account_id,
            )
            register_card(
                alert_id,
                card_id,
                account_id=account_id,
                message_id=tracked_message_id or None,
                sequence=sequence,
            )
            logger.info(
                "[feishu-card] CardKit 原位更新成功 alert_id=%s card_id=%s "
                "message_id=%s sequence=%d",
                alert_id, card_id, tracked_message_id or None, sequence,
            )
            return
        except FeishuCardkitError as exc:
            if exc.code == 200810 and attempt < len(retry_delays):
                logger.info(
                    "[feishu-card] 卡片仍在交互中，稍后重试 alert_id=%s attempt=%d",
                    alert_id, attempt,
                )
                continue
            raise


def _handle_card_action_with_llm_async(
    *,
    alert_id: Optional[str],
    action: Optional[str],
    button_text: Optional[str],
    operator_open_id: Optional[str],
    operator_name: Optional[str],
    job_id: Optional[str],
    message_id: Optional[str],
    processed_card: Dict[str, Any],
) -> None:
    """按钮点击后异步调 CardActionAgent 决定 P8_job 状态变更（2026-08-20 新增）。

    daemon 线程 fire-and-forget：
    - 失败仅 ``logger.exception``，不影响已返回的 toast 响应
    - ``job_id`` 缺失（旧卡片无 job_id 字段）→ 跳过（向后兼容）
    - CardActionAgent 成功返回后才原位更新卡片，避免视觉结果先于业务状态

    链路：

    ::

        process_card_callback
          └── _handle_card_action_with_llm_async (daemon, 本函数)
                ├── CardActionAgent → apply_card_action → 持久化 P8 状态
                └── _update_card_in_place
                     ├── CardKit PUT（实体卡）
                     └── IM Message PATCH（历史 inline 卡片）
    """
    def _run() -> None:
        # 1. 前置校验：job_id 缺失 → 跳过（向后兼容旧卡片）
        if not job_id:
            logger.info(
                "[feishu-card] card-action-llm 跳过: action.value 缺 job_id"
                " (alert_id=%s action=%s)",
                alert_id, action,
            )
            return
        if not alert_id or not action:
            logger.info(
                "[feishu-card] card-action-llm 跳过: alert_id/action 为空"
                " (job_id=%s)",
                job_id,
            )
            return

        # 2. 构造 user_ctx（最简化；不耦合 chat_reply / 不读 FEISHU_USER_MAP）
        #    说明：CardActionAgent 不需要 chat_reply 路径下的复杂 user_ctx；
        #    operator_name 由飞书 callback payload 直接透传，无需再查表。
        user_ctx = {
            "role": "未识别用户",
            "name": operator_name or "未知",
            "open_id": operator_open_id or "",
        }

        # 3. 拼 user message（LLM 解析 + 工具调用输入）
        msg = (
            f"[card_click] alert_id={alert_id} action={action} "
            f"button_text={button_text or ''} "
            f"operator_open_id={operator_open_id or ''} "
            f"operator_name={operator_name or ''} "
            f"job_id={job_id}"
        )

        # 4. 调 CardActionAgent（失败吞掉，logger.exception）
        try:
            # 延迟 import 避免循环（feishu_card → agents → ...）
            from A7.middleware.p8_card_action_agent import run_card_action_agent
            run_card_action_agent(msg, user_ctx=user_ctx, job_id=job_id)
            logger.info(
                "[feishu-card] card-action-llm 完成 alert_id=%s action=%s job_id=%s",
                alert_id, action, job_id,
            )

            # 保持 P8 原有业务逻辑：Agent/状态机先完成，再把同一张飞书卡片更新为终态。
            # 更新失败只影响卡片展示，不回滚已经落盘的 P8 业务状态。
            entry = lookup_card_id(alert_id) or {}
            next_card = processed_card
            if action in {"ack", "handle"}:
                next_card = _build_follow_up_card(
                    original_card=entry.get("card_json"),
                    alert_id=alert_id,
                    job_id=job_id,
                )
            _update_card_in_place(
                alert_id=alert_id,
                message_id=message_id,
                processed_card=next_card,
            )
        except Exception:
            logger.exception(
                "[feishu-card] card-action 或原位更新失败 alert_id=%s action=%s",
                alert_id, action,
            )

    t = threading.Thread(
        target=_run,
        daemon=True,
        name=f"feishu-card-action-llm-{alert_id or 'unknown'}",
    )
    t.start()


def process_card_callback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理一个飞书 Card 按钮点击 payload，返回回给飞书的完整响应体。

    行为（CardKit 实体卡 + 原位更新）：
        1. ``url_verification`` 挑战 → ``{"challenge": "..."}``
        2. 入参非法（缺 event/action.value）→ ``{"status": "error", "error": "..."}``
        3. 幂等命中（同 alert_id 已处置）→ ``{"toast": {"type": "warning", "content": "..."}}``
        4. 首次成功：写审计日志 + 同步返回 ``{"toast": {"type": "success"}}``
           + daemon 线程执行 P8 CardActionAgent；成功后 PUT/PATCH 原位更新同一张卡。

    Args:
        payload: 飞书 card.action.trigger 回调 payload dict。

    Returns:
        回给飞书的完整响应 dict（可能含 challenge / error / toast）。**只含 toast**，
        不含 card 字段（飞书客户端实时渲染 card 替换会触发 2026072 报错，
        见 docs/飞书卡片教程/，官方只支持 toast 响应）。

    Note:
        更新策略：
            - 新卡片按 cc connect 的协议使用 card_id 引用发送，callback 后走
              PUT /cardkit/v1/cards/{card_id}。
            - 历史 inline 卡片降级走 PATCH /im/v1/messages/{message_id}。
            - 两条路径均保留原 message_id，不撤回、不产生新消息。
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

    # 4. 首次成功：写审计 + 构造终态卡
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
        job_id=info["job_id"],
    )

    # 异步调 CardActionAgent 触发 P8_job 状态变更；完成后原位更新同一张卡。
    # 不阻塞 callback 的 2s 响应窗口，也不再撤回/重发消息。
    _handle_card_action_with_llm_async(
        alert_id=info["alert_id"],
        action=info["action"],
        button_text=info["button_text"],
        operator_open_id=info["operator_open_id"],
        operator_name=info["operator_name"],
        job_id=info["job_id"],
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
