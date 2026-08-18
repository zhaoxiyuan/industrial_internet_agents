"""A7/notify — 飞书通道配置 Web UI（Flask + HTML）。

================================================================================
职责
================================================================================

让业务 / 运维人员通过浏览器把飞书通道相关参数写入项目根 `.env`，供
`agents/channel_gateway_client.py` 和 `A7.notify.feishu_sender` 读取。

可配置的 key：
    GATEWAY_HOST              Channel Gateway 地址（默认 http://127.0.0.1:8787）
    CG_API_KEY                Gateway REST API Key（敏感；UI 中默认遮罩）
    CHANNEL_GATEWAY_API_KEY   同上（兼容命名）
    FEISHU_APP_ID / SECRET    可选；仅在直接调飞书 Open API 时使用
    FEISHU_DOMAIN             feishu (中国站) / lark (国际版)
    FEISHU_CONVERSATION_MAP   JSON 字符串；责任岗位 → 飞书 chat_id 映射
    FEISHU_GROUP_<ROLE>       单条岗位覆盖（动态表）

================================================================================
与现有 .env 关系
================================================================================

读取 / 写入的目标 = **项目根 .env**（与 channel_gateway_client._ENV_PATH 一致），
不是 agent_config/.env（A5 LLM/VL 配置专属）。原因：

- `agents/channel_gateway_client.py:101-104` 显式从 `_PROJECT_ROOT/.env` 加载；
  写到此处 channel_gateway_client 下次启动立即生效。
- A5/A6 的 LLM/VL 配置仍在 `agent_config/.env` 里；本 UI **不动**那个文件。

写入策略（不破坏既有内容）：
    1. 解析 .env 为 (key, value, comment_lines) 三元组列表（保留注释 + 空行 + 顺序）
    2. 对 UI 涉及的 key 做 in-place 更新；新增 append 到末尾（带 section header）
    3. 其他 key（OPENAI_API_KEY 等）原样保留
    4. 写前自动备份 .env.bak
    5. tmp 文件 + os.replace 原子替换

================================================================================
启动
================================================================================

    # 默认 5003 端口（避开 A5=5001 / A6=5002 / AgentConfig=5000）
    python A7/notify/feishu_config_app.py

    # 自定义端口
    python A7/notify/feishu_config_app.py --port 5003 --host 127.0.0.1

================================================================================
API
================================================================================

    GET  /                              飞书配置 UI 页面
    GET  /api/feishu/config             读取当前 .env 中飞书相关 key（API key 遮罩）
    POST /api/feishu/config             保存到 .env（原子写 + 自动备份）
    POST /api/feishu/test/gateway       测试 Gateway /healthz
    POST /api/feishu/test/resolve       给定 assignee_role，预演 conversation_id 解析

================================================================================
日志规范（与 CLAUDE.md 对齐）
================================================================================

所有入口 / 出口 / 异常按 [METHOD] [path] 进入/响应/异常 格式打印。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request, send_from_directory


# ============================================================
# 路径常量
# ============================================================

# A7/notify/feishu_config_app.py → A7/notify/ → A7/ → 项目根
NOTIFY_DIR: Path = Path(__file__).resolve().parent
A7_DIR: Path = NOTIFY_DIR.parent
PROJECT_ROOT: Path = A7_DIR.parent
ENV_FILE: Path = PROJECT_ROOT / ".env"
ENV_BACKUP_FILE: Path = PROJECT_ROOT / ".env.bak"
TEMPLATES_DIR: Path = NOTIFY_DIR / "templates"

# 把项目根加进 sys.path，便于 `from A7.notify import ...`
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Gateway 子进程路径常量（start_gateway 模块已定义）
# 同时支持两种启动方式:
#   python A7/notify/feishu_config_app.py   (脚本方式 — 项目根在 cwd)
#   python -m A7.notify.feishu_config_app   (模块方式 — 推荐)
try:
    from .start_gateway import GATEWAY_DIR  # type: ignore[import-not-found]
except ImportError:
    # 脚本方式运行时没有父包,把本文件所在目录临时加进 sys.path
    if str(NOTIFY_DIR) not in sys.path:
        sys.path.insert(0, str(NOTIFY_DIR))
    from start_gateway import GATEWAY_DIR  # type: ignore[no-redef]

# 多账户：Gateway 子进程的相关路径
GATEWAY_ENV_FILE: Path = GATEWAY_DIR / ".env"
GATEWAY_ENV_BACKUP_FILE: Path = GATEWAY_DIR / ".env.bak"
FEISHU_CONFIG_FILE: Path = GATEWAY_DIR / "config" / "config.feishu.local.json"
FEISHU_CONFIG_BACKUP_FILE: Path = GATEWAY_DIR / "config" / "config.feishu.local.json.bak"


# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("a7-feishu-config")


# ============================================================
# 业务常量
# ============================================================

# ====== 受管 key 集合 ======

# 受 UI 直接管理的 key（按顺序展示）
# 2026-08-13 重构：删除 FEISHU_CONVERSATION_MAP / FEISHU_GROUP_<ROLE>
# 统一为 FEISHU_USER_MAP（key=open_id，value={chat_id, role, name}）
MANAGED_KEYS_ORDER: List[str] = [
    "GATEWAY_HOST",
    "CG_API_KEY",
    "CHANNEL_GATEWAY_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_DOMAIN",
    "FEISHU_USER_MAP",
    "FEISHU_GROUP_MAP",
]

# 敏感 key（读取时遮罩；保存时如果用户没改则保留原值）
SENSITIVE_KEYS: set[str] = {
    "CG_API_KEY",
    "CHANNEL_GATEWAY_API_KEY",
    "FEISHU_APP_SECRET",
}

# 默认值（用于"未配置时填什么"）
DEFAULTS: Dict[str, str] = {
    "GATEWAY_HOST": "http://127.0.0.1:8787",
    "FEISHU_DOMAIN": "feishu",
}


# ====== 受管 key 子集 ======

# FEISHU_USER_MAP：key=open_id (ou_xxx)，value={chat_id, role, name}
# FEISHU_GROUP_MAP：key=chat_id (oc_xxx)，value={name, description}
# - 两者解耦：USER_MAP 索引"人"，GROUP_MAP 索引"群"
# - 同一 chat_id 可被多个 user 共享，群名/描述在 GROUP_MAP 维护一次即可
# 旧 FEISHU_GROUP_<ROLE> 已废弃（保留正则用于 .env 清理，但不展示在 UI）
FEISHU_GROUP_PREFIX: str = "FEISHU_GROUP_"
FEISHU_GROUP_PATTERN: re.Pattern = re.compile(r"^FEISHU_GROUP_(.+)$")
FEISHU_USER_MAP_KEY: str = "FEISHU_USER_MAP"
FEISHU_GROUP_MAP_KEY: str = "FEISHU_GROUP_MAP"


# ====== 多账户（多飞书 App 凭据） ======
# 2026-08-17 新增：Channel Gateway Node 端原生支持 accounts.<accountId>
# 这里把多账户信息统一存到项目根 .env 的 FEISHU_ACCOUNTS JSON 中,
# 同时把每个账户展开成 FEISHU_<ACCT_ID_UPPER>_<FIELD> 写到 gateway .env,
# 并重建 config.feishu.local.json 的 channels.feishu.accounts 块。

# UI source-of-truth JSON key（项目根 .env，不含 secrets）
FEISHU_ACCOUNTS_KEY: str = "FEISHU_ACCOUNTS"

# Per-account env var 后缀规范（gateway .env + config JSON 都引用 ${FEISHU_<ACCT>_<SUFFIX>}）
# (env_var_suffix, ui_field_key, sensitive)
ACCOUNT_FIELD_SPECS: List[Tuple[str, str, bool]] = [
    ("APP_ID",             "app_id",             False),
    ("APP_SECRET",         "app_secret",         True),
    ("VERIFICATION_TOKEN", "verification_token", True),
    ("DOMAIN",             "domain",             False),
]

# account_id 字符集规则（前端 + 后端双重校验）
ACCOUNT_ID_PATTERN: re.Pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
ACCOUNT_ID_MAX_LEN: int = 64

# Per-account 网关参数硬编码（沿用现有 default 配置；UI 不暴露）
DEFAULT_ACCOUNT_BLOCK: Dict[str, Any] = {
    "maxSkewSeconds": 300,
    "timeoutMs": 15000,
    "maxResponseBytes": 1048576,
}


# ============================================================
# .env 解析 / 序列化
# ============================================================

def parse_env_file(env_path: Path) -> List[Tuple[str, str, List[str]]]:
    """把 .env 解析为 [(key, value, [comment_lines_before])] 列表。

    保留：
        - 空行（占位）
        - 注释行（以 # 开头）
        - key 顺序（按文件出现顺序）
        - 引号（'...'/ "..."）剥除

    注意：本函数只解析扁平结构（KEY=VALUE），不处理 export 前缀。
    """
    out: List[Tuple[str, str, List[str]]] = []
    if not env_path.exists():
        return out

    pending_comments: List[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            # 空行不绑定 key；忽略（避免破坏结构）
            continue
        if line.startswith("#"):
            pending_comments.append(raw)
            continue
        if "=" not in line:
            # 非 KEY=VALUE（如裸文本）；视为注释
            pending_comments.append(raw)
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        # 剥外层引号 + 反转义（与 serialize_env_file 对账）
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        elif v.startswith("'") and v.endswith("'"):
            v = v[1:-1]
        out.append((k, v, pending_comments))
        pending_comments = []
    return out


def serialize_env_file(entries: List[Tuple[str, str, List[str]]]) -> str:
    """把 (key, value, [comment_lines]) 列表反向序列化为 .env 文本。

    规则：
        - 每个 entry 的 comments 写在该 key 上面（保留 # 前缀）
        - 值如果含空格 / 特殊字符 → 用双引号包
        - 否则直接写
    """
    lines: List[str] = []
    for k, v, comments in entries:
        for c in comments:
            lines.append(c)
        # 安全引号：含空格 / # / = / \n / " / ' → 加引号
        needs_quote = bool(re.search(r'[ \t#="\n\'\\\\]', v))
        if needs_quote:
            # 转义内嵌的 " 和 \
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k}="{escaped}"')
        else:
            lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


# ============================================================
# 多账户：辅助函数
# ============================================================

def _account_id_to_env_suffix(account_id: str) -> str:
    """account_id → UPPER_SNAKE_CASE,用于 env var 后缀。

    规则:lower → 非 [a-z0-9] 替换为 '_' → strip('_') → upper。
    - 'default' → 'DEFAULT'
    - 'alerting-bot' → 'ALERTING_BOT'
    - 'Ops.Tech' → 'OPS_TECH'
    - '' / '_' → ''
    """
    return re.sub(r"[^a-z0-9]+", "_", account_id.strip().lower()).strip("_").upper()


def _validate_account_id(account_id: str) -> Optional[str]:
    """校验 account_id 合法性;返回 None = 合法,否则返回错误信息。

    规则:1~64 字符,首字符字母,后续字母/数字/_/-。
    """
    if not account_id:
        return "account_id 不能为空"
    if len(account_id) > ACCOUNT_ID_MAX_LEN:
        return f"account_id 长度不能超过 {ACCOUNT_ID_MAX_LEN} 字符"
    if not ACCOUNT_ID_PATTERN.match(account_id):
        return "account_id 必须以字母开头,仅含字母/数字/下划线/短横线"
    return None


def _account_env_key(account_id: str, suffix: str) -> str:
    """生成 FEISHU_<ACCT_ID_UPPER>_<SUFFIX> 形式的环境变量名。

    例:('default', 'APP_ID') → 'FEISHU_DEFAULT_APP_ID'
    """
    s = _account_id_to_env_suffix(account_id)
    if not s:
        raise ValueError(f"account_id={account_id!r} 无法生成合法的 env 后缀")
    return f"FEISHU_{s}_{suffix}"


def _is_account_env_key(key: str) -> Optional[Tuple[str, str]]:
    """判断 .env key 是否为多账户 env var；若是,返回 (account_id, suffix)。

    否则返回 None。
    """
    if not key.startswith("FEISHU_"):
        return None
    rest = key[len("FEISHU_"):]
    # 后缀必须是 ACCOUNT_FIELD_SPECS 里的某个
    matched_suffix: Optional[str] = None
    for suffix, _, _ in ACCOUNT_FIELD_SPECS:
        if rest.endswith("_" + suffix):
            matched_suffix = suffix
            middle = rest[: -len("_" + suffix)]
            break
    if matched_suffix is None:
        return None
    # middle 必须能反向映射成合法 account_id（UPPER_SNAKE_CASE → lower-kebab-case）
    # 这里只做"原始 suffix 截断 + 原样存"；UI 显示时由 _suffix_to_account_id 还原
    # 但因为 account_id 是 lower-kebab-case，归一时会把 _ → - 仅在尾部合并；
    # 这里我们直接以 middle 原样小写作为 account_id 候选
    return middle.lower(), matched_suffix


def _read_simple_env_file(path: Path) -> Dict[str, str]:
    """读 .env → {key: raw_value} dict（不保留 comments/顺序）。

    用于 gateway .env 等"附属配置文件"读写。空行 / 注释行跳过。
    值同样剥外层单/双引号（与 parse_env_file 一致）。
    """
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        elif v.startswith("'") and v.endswith("'"):
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _write_simple_env_file(
    path: Path, kv: Dict[str, str], *, backup: bool = True
) -> Optional[str]:
    """写 .env 简单格式（KEY=VALUE,按 key 排序）。

    - 仅保留传入的 kv,不在 .env 现有但未传入的键原样保留。
      （调用方先 read + merge + write,实现"重写账户 vars、保留其他 vars"）
    - 写前自动备份 .env.bak,atomic write（tmp + os.replace）。
    - 返回 backup 路径或 None。

    Args:
        path: 目标 .env 路径
        kv: 要写入的全部 {key: value}（含所有非账户 vars 和账户 vars）
        backup: 是否备份（默认 True）
    """
    backup_path: Optional[str] = None
    if backup and path.exists():
        backup_path = str(path.with_suffix(path.suffix + ".bak"))
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError as exc:
            logger.warning("备份 %s 失败（继续写）: %s", path, exc)
            backup_path = None

    lines: List[str] = []
    for k in sorted(kv.keys()):
        v = kv[k]
        needs_quote = bool(re.search(r'[ \t#="\n\'\\\\]', v))
        if needs_quote:
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k}="{escaped}"')
        else:
            lines.append(f"{k}={v}")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return backup_path


def _load_accounts_from_env(
    env_path: Path,
) -> Dict[str, Dict[str, str]]:
    """从项目根 .env 解析 FEISHU_ACCOUNTS JSON → {account_id: meta}。

    meta = {description, domain, app_id, verification_token} (不含 secrets;
    secrets 由 _load_gateway_account_env 从 gateway .env 提供)。

    容错:JSON 损坏 / 不存在 → 返回 {}。
    """
    if not env_path.exists():
        return {}
    kv = _read_simple_env_file(env_path)
    raw = kv.get(FEISHU_ACCOUNTS_KEY, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("FEISHU_ACCOUNTS JSON 解析失败: %r", raw[:80])
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for account_id, meta in parsed.items():
        if not isinstance(account_id, str) or not isinstance(meta, dict):
            continue
        out[account_id] = {
            "description": str(meta.get("description", "") or ""),
            "domain": str(meta.get("domain", "feishu") or "feishu"),
            "app_id": str(meta.get("app_id", "") or ""),
            "verification_token": str(meta.get("verification_token", "") or ""),
        }
    return out


def _load_gateway_account_env(account_id: str) -> Dict[str, str]:
    """从 gateway .env 读 FEISHU_<SUCC>_<FIELD> 四个变量 → 字典 {ui_field_key, raw_value}。

    缺哪个返哪个(空字符串);不存在 gateway .env 视为全部空。
    """
    if not GATEWAY_ENV_FILE.exists():
        return {}
    kv = _read_simple_env_file(GATEWAY_ENV_FILE)
    out: Dict[str, str] = {}
    for env_suffix, ui_key, _ in ACCOUNT_FIELD_SPECS:
        env_key = _account_env_key(account_id, env_suffix)
        out[ui_key] = kv.get(env_key, "")
    return out


def _build_gateway_env_kv(
    accounts: List[Dict[str, str]],
) -> Dict[str, str]:
    """把 accounts 列表展开成 {FEISHU_<SUCC>_<FIELD>: value} 字典。

    仅含多账户相关的 4N 个键;调用方负责与原 gateway .env 中其他键合并。
    """
    out: Dict[str, str] = {}
    for acc in accounts:
        aid = acc["account_id"]
        for env_suffix, ui_key, _ in ACCOUNT_FIELD_SPECS:
            v = acc.get(ui_key, "")
            if v == "":
                continue  # 空值不写入,避免 gateway 启动报 CONFIG_ENV_MISSING
            out[_account_env_key(aid, env_suffix)] = v
    return out


def _upsert_gateway_accounts_env(
    new_accounts: List[Dict[str, str]],
    *,
    deleted_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """重写 gateway .env 中的多账户键。

    行为:
        1. 读 gateway .env 全部 {key: value}
        2. 删除所有现有的 FEISHU_<SUCC>_<FIELD>（N*4 个）
        3. 删除 deleted_ids（兼容旧名 / 多余 ID）的 4 个键
        4. 用 new_accounts 重建账户键
        5. 写回（其他非账户 vars 原样保留;写前自动备份 .env.bak）

    Returns:
        {"ok": True, "backup": "...", "written_keys": [...], "removed_keys": [...]}
    """
    deleted_ids = deleted_ids or []
    existing_kv = _read_simple_env_file(GATEWAY_ENV_FILE)
    # 清掉现有的多账户键
    removed_keys: List[str] = []
    for k in list(existing_kv.keys()):
        if _is_account_env_key(k) is not None:
            existing_kv.pop(k, None)
            removed_keys.append(k)
    # 清掉被显式删除的 ID 的键（通常上面已清,这里做幂等兜底）
    for did in deleted_ids:
        for env_suffix, _, _ in ACCOUNT_FIELD_SPECS:
            env_key = _account_env_key(did, env_suffix)
            if env_key in existing_kv:
                existing_kv.pop(env_key, None)
                if env_key not in removed_keys:
                    removed_keys.append(env_key)
    # 重建账户键
    new_kv = _build_gateway_env_kv(new_accounts)
    existing_kv.update(new_kv)
    written_keys = sorted(new_kv.keys())
    backup_path = _write_simple_env_file(GATEWAY_ENV_FILE, existing_kv)
    return {
        "ok": True,
        "backup": backup_path,
        "written_keys": written_keys,
        "removed_keys": sorted(removed_keys),
    }


def _upsert_feishu_config_accounts(
    new_accounts: List[Dict[str, str]],
) -> Dict[str, Any]:
    """重写 config.feishu.local.json 的 channels.feishu.accounts 块。

    行为:
        1. 读现有 JSON（若不存在则用空 dict）
        2. 整体替换 channels.feishu.accounts（避免遗漏旧 key）
        3. 顶层 server/storage/delivery/其他 channels 原样保留
        4. 写前备份 .bak,atomic write

    每个账户的 JSON 块用 ${FEISHU_<SUCC>_<FIELD>} 引用 gateway .env。
    """
    cfg: Dict[str, Any] = {}
    if FEISHU_CONFIG_FILE.exists():
        try:
            cfg = json.loads(FEISHU_CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                cfg = {}
        except json.JSONDecodeError:
            logger.warning("config.feishu.local.json JSON 损坏, 重置为空 dict")
            cfg = {}

    # 保留顶层其他字段
    channels = cfg.get("channels") or {}
    if not isinstance(channels, dict):
        channels = {}

    # 整体重建 feishu.accounts
    new_accounts_block: Dict[str, Any] = {}
    for acc in new_accounts:
        aid = acc["account_id"]
        # 校验:account_id 必须能生成合法 env 后缀
        if not _account_id_to_env_suffix(aid):
            logger.warning(
                "跳过非法 account_id=%r（无法生成 env 后缀）", aid,
            )
            continue
        try:
            domain_key = _account_env_key(aid, "DOMAIN")
            app_id_key = _account_env_key(aid, "APP_ID")
            app_secret_key = _account_env_key(aid, "APP_SECRET")
            token_key = _account_env_key(aid, "VERIFICATION_TOKEN")
        except ValueError:
            continue
        block = dict(DEFAULT_ACCOUNT_BLOCK)  # copy 默认网关参数
        block["domain"] = "${" + domain_key + "}"
        block["appId"] = "${" + app_id_key + "}"
        block["appSecret"] = "${" + app_secret_key + "}"
        block["verificationToken"] = "${" + token_key + "}"
        new_accounts_block[aid] = block

    # 保留其他 channel 配置（loopback / generic 等）,仅替换 feishu.accounts
    channels["feishu"] = {
        **({"enabled": True} if "feishu" not in channels else {}),
        "enabled": True,
        "accounts": new_accounts_block,
    }
    cfg["channels"] = channels

    # 备份
    backup_path: Optional[str] = None
    if FEISHU_CONFIG_FILE.exists():
        backup_path = str(FEISHU_CONFIG_BACKUP_FILE)
        try:
            shutil.copy2(FEISHU_CONFIG_FILE, FEISHU_CONFIG_BACKUP_FILE)
        except OSError as exc:
            logger.warning("备份 %s 失败（继续写）: %s", FEISHU_CONFIG_FILE, exc)
            backup_path = None

    # atomic write
    tmp_path = FEISHU_CONFIG_FILE.with_suffix(FEISHU_CONFIG_FILE.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, FEISHU_CONFIG_FILE)
    return {
        "ok": True,
        "backup": backup_path,
        "accounts_written": sorted(new_accounts_block.keys()),
    }


def _resolve_account_secrets(
    accounts: List[Dict[str, str]],
) -> None:
    """就地处理 accounts 列表中的 `***` 占位符 → 从 gateway .env 读回原值。

    仅对敏感字段（app_secret / verification_token）生效。
    输入 accounts 形如:[{account_id, app_secret, verification_token, ...}]
    """
    if not GATEWAY_ENV_FILE.exists():
        return
    for acc in accounts:
        gw = _load_gateway_account_env(acc["account_id"])
        for _, ui_key, sensitive in ACCOUNT_FIELD_SPECS:
            if not sensitive:
                continue
            if acc.get(ui_key, "") == "***":
                acc[ui_key] = gw.get(ui_key, "")


def get_managed_view() -> Dict[str, Any]:
    """从 .env 读出 UI 关心的字段视图。

    2026-08-13 重构：返回 user_map（[{open_id, role, name}]）替代旧的
    FEISHU_GROUP_<ROLE> 列表。
    2026-08-17 清理：USER_MAP 移除 chat_id 字段（一直未消费；群发改走 GROUP_MAP）。

    Returns:
        {
            "managed": {  # 受管 key
                "GATEWAY_HOST": {"value": "...", "sensitive": False, ...},
                "FEISHU_USER_MAP": {"value": "{...JSON...}", "sensitive": False, ...},
                ...
            },
            "user_map": [  # 解析后的 FEISHU_USER_MAP 条目（按 open_id 排序）
                {"open_id": "ou_xxx", "role": "车间主任", "name": "张三"},
                ...
            ],
            "group_map": [  # 解析后的 FEISHU_GROUP_MAP 条目（按 chat_id 排序）
                {"chat_id": "oc_xxx", "name": "应急响应群", "description": "P8 告警群"},
                ...
            ],
            "legacy_group_overrides": [  # 旧 FEISHU_GROUP_<ROLE>（仅用于清理提示）
                {"key": "FEISHU_GROUP_HSE", "role": "HSE", "value": "oc_xxx"},
            ],
            "env_path": "/abs/path/to/.env",
            "env_exists": True,
        }
    """
    entries = parse_env_file(ENV_FILE)
    kv: Dict[str, str] = {k: v for k, v, _ in entries}

    managed: Dict[str, Dict[str, Any]] = {}
    for key in MANAGED_KEYS_ORDER:
        raw = kv.get(key, "")
        sensitive = key in SENSITIVE_KEYS
        managed[key] = {
            "value": ("***" if sensitive and raw else raw),
            "sensitive": sensitive,
            "has_value": bool(raw),
            "default": DEFAULTS.get(key, ""),
        }

    # 解析 FEISHU_USER_MAP → 条目数组（按 open_id 排序保证稳定）
    # 2026-08-17 清理：USER_MAP 移除 chat_id 字段（历史残留，仍可被静默忽略）
    user_map: List[Dict[str, str]] = []
    raw_user_map = kv.get(FEISHU_USER_MAP_KEY, "").strip()
    if raw_user_map:
        try:
            parsed = json.loads(raw_user_map)
            if isinstance(parsed, dict):
                for open_id, info in parsed.items():
                    if not isinstance(info, dict):
                        continue
                    user_map.append({
                        "open_id": open_id,
                        "role": str(info.get("role", "") or "").strip(),
                        "name": str(info.get("name", "") or "").strip(),
                    })
        except json.JSONDecodeError:
            pass  # 留空数组；前端显示解析失败
    user_map.sort(key=lambda x: x["open_id"])

    # 解析 FEISHU_GROUP_MAP → 条目数组（按 chat_id 排序保证稳定）
    # 注：name 允许为空（占位条目），来自 Gateway 监听捕获但用户尚未命名的群
    group_map: List[Dict[str, str]] = []
    raw_group_map = kv.get(FEISHU_GROUP_MAP_KEY, "").strip()
    if raw_group_map:
        try:
            parsed_gm = json.loads(raw_group_map)
            if isinstance(parsed_gm, dict):
                for chat_id, info in parsed_gm.items():
                    if not isinstance(info, dict):
                        continue
                    chat_id_s = str(chat_id).strip()
                    if not chat_id_s:
                        continue
                    name = str(info.get("name", "") or "").strip()
                    description = str(info.get("description", "") or "").strip()
                    group_map.append({
                        "chat_id": chat_id_s,
                        "name": name,
                        "description": description,
                        "is_unnamed": (not name),  # 前端用来显示"未命名（点击编辑）"
                    })
        except json.JSONDecodeError:
            pass  # 留空数组；前端显示解析失败
    group_map.sort(key=lambda x: x["chat_id"])

    # 旧 FEISHU_GROUP_<ROLE> 残留（用于清理提示）
    legacy_group_overrides: List[Dict[str, str]] = []
    for k, v, _ in entries:
        m = FEISHU_GROUP_PATTERN.match(k)
        if m:
            legacy_group_overrides.append({
                "key": k,
                "role": m.group(1),
                "value": v,
            })
    legacy_group_overrides.sort(key=lambda x: x["key"])

    # 多账户（FEISHU_ACCOUNTS JSON + gateway .env 中的 secrets）
    # 合并两份数据:UI 字段(描述/domain/app_id/verification_token)来自 FEISHU_ACCOUNTS JSON,
    # 敏感字段(app_secret/verification_token 真值)来自 gateway .env。
    accounts_dict = _load_accounts_from_env(ENV_FILE)
    accounts_list: List[Dict[str, Any]] = []
    for aid in sorted(accounts_dict.keys()):
        meta = accounts_dict[aid]
        gw = _load_gateway_account_env(aid)
        app_secret_val = gw.get("app_secret", "")
        token_val = gw.get("verification_token", "")
        accounts_list.append({
            "account_id": aid,
            "description": meta.get("description", ""),
            "domain": meta.get("domain", "feishu"),
            "app_id": {
                "value": meta.get("app_id", ""),
                "has_value": bool(meta.get("app_id", "")),
            },
            "app_secret": {
                "value": ("***" if app_secret_val else ""),
                "sensitive": True,
                "has_value": bool(app_secret_val),
            },
            "verification_token": {
                "value": ("***" if token_val else ""),
                "sensitive": True,
                "has_value": bool(token_val),
            },
        })

    return {
        "managed": managed,
        "user_map": user_map,
        "group_map": group_map,
        "legacy_group_overrides": legacy_group_overrides,
        "accounts": accounts_list,
        "env_path": str(ENV_FILE),
        "env_exists": ENV_FILE.exists(),
    }


def upsert_env_entries(new_entries: List[Tuple[str, str]]) -> Dict[str, Any]:
    """把 (key, value) 列表 in-place 更新到 .env（保留其他 key + 注释 + 顺序）。

    行为：
        1. 读现有 entries
        2. 对每个 (k, v)：存在则更新 value + 清空其 comments；不存在则 append 到末尾
           （新追加的行带 section header 注释）
        3. 写 .env.bak 备份
        4. tmp 文件 + os.replace 原子写

    Args:
        new_entries: [(key, value), ...]；value="" 表示"删除该 key"

    Returns:
        {"ok": True, "updated": ["k1", "k2"], "removed": ["k3"], "backup": "...path..."}
    """
    entries = parse_env_file(ENV_FILE)
    new_map: Dict[str, str] = dict(new_entries)

    # 标记要保留的 key 的最新位置
    existing_keys = {k for k, _, _ in entries}
    updated: List[str] = []
    removed: List[str] = []

    # in-place 更新
    new_entries_list: List[Tuple[str, str, List[str]]] = []
    for k, v, comments in entries:
        if k in new_map:
            new_val = new_map[k]
            if new_val == "":
                # 删除
                removed.append(k)
                continue
            new_entries_list.append((k, new_val, []))   # 改值时丢掉旧 comments；下一轮重建
            updated.append(k)
        else:
            new_entries_list.append((k, v, comments))

    # 追加新 key（不在 existing_keys 里的）
    if new_entries_list:
        last_comments = new_entries_list[-1][2] if new_entries_list[-1][2] else []
    else:
        last_comments = []
    appended_any = False
    for k, v in new_entries:
        if k not in existing_keys and v != "":
            if not appended_any:
                # 首次 append 前插一个 section header
                new_entries_list.append((
                    k, v,
                    [
                        "",
                        "# ============================================================",
                        "# A7/notify 飞书通道配置（由 feishu_config_app.py 写入）",
                        "# ============================================================",
                        "",
                    ],
                ))
                appended_any = True
            else:
                new_entries_list.append((k, v, []))
            updated.append(k)

    # 备份
    backup_path: Optional[str] = None
    if ENV_FILE.exists():
        backup_path = str(ENV_BACKUP_FILE)
        try:
            shutil.copy2(ENV_FILE, ENV_BACKUP_FILE)
        except OSError as exc:
            logger.warning("备份 .env 失败（继续写）: %s", exc)
            backup_path = None

    # 原子写
    tmp_path = ENV_FILE.with_suffix(".env.tmp")
    tmp_path.write_text(serialize_env_file(new_entries_list), encoding="utf-8")
    os.replace(tmp_path, ENV_FILE)

    return {
        "ok": True,
        "updated": sorted(set(updated)),
        "removed": sorted(set(removed)),
        "backup": backup_path,
    }


# ============================================================
# 测试：Gateway /healthz + resolve conversation_id
# ============================================================

def _do_request(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
                json_body: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Tuple[int, Any]:
    """统一 HTTP 封装：网络异常 → (0, error_dict)；HTTP 错误 → (status, body)。"""
    try:
        resp = requests.request(
            method=method, url=url,
            headers=headers or {},
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"raw_text": resp.text[:300]}


def test_gateway_connection(host: str, api_key: str) -> Dict[str, Any]:
    """GET /healthz 探活。

    Args:
        host: Gateway 地址（含 scheme，可不带尾斜杠）
        api_key: Gateway API Key；为空则尝试 /healthz（无需 auth）

    Returns:
        {"ok": bool, "status": int|None, "data": dict|None, "error": str|None}
    """
    host = (host or DEFAULTS["GATEWAY_HOST"]).rstrip("/")
    url = f"{host}/healthz"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    status, data = _do_request("GET", url, headers=headers)
    return {
        "ok": status == 200,
        "status": status,
        "data": data,
        "error": None if status == 200 else (data if isinstance(data, dict) else {"raw": data}),
        "url": url,
    }


def test_resolve_conversation_id(
    assignee_role: str,
    user_map_json: str,
) -> Dict[str, Any]:
    """预演 USER_MAP 解析（不调用 gateway；纯本地）。

    2026-08-13 重构：FEISHU_USER_MAP 顶层是 {open_id: {role, name}}；
    同一 role 可对应多个收件人 → 返回 recipients 列表。
    2026-08-17 清理：USER_MAP 移除 chat_id 字段，所有收件人 receive_id_type
    固定为 open_id（单聊）；群发请改用 FEISHU_GROUP_MAP + send_to_group。

    Args:
        assignee_role: 责任岗位（如"车间主任"）
        user_map_json: FEISHU_USER_MAP 的 JSON 字符串（可能不合法）

    Returns:
        {
            "ok": bool,
            "source": "USER_MAP"|"DEV_FALLBACK",
            "map_valid_json": bool,
            "map_error": Optional[str],
            "recipients": [
                {"open_id": "ou_xxx", "name": "张三",
                 "receive_id_type": "open_id"},
                ...
            ],
            "count": int,
        }
    """
    # 1. 校验 user_map_json 是否合法
    map_valid = True
    map_error: Optional[str] = None
    parsed_map: Dict[str, Any] = {}
    if user_map_json.strip():
        try:
            parsed_map = json.loads(user_map_json)
            if not isinstance(parsed_map, dict):
                map_valid = False
                map_error = "顶层必须是 JSON object（{open_id: {role, name}, ...}）"
                parsed_map = {}
        except json.JSONDecodeError as exc:
            map_valid = False
            map_error = str(exc)
            parsed_map = {}

    # 2. 遍历 user_map，匹配 role
    recipients: List[Dict[str, str]] = []
    for open_id, info in parsed_map.items():
        if not isinstance(info, dict):
            continue
        if str(info.get("role", "") or "").strip() != assignee_role:
            continue
        name = str(info.get("name", "") or "").strip()
        recipients.append({
            "open_id": open_id,
            "name": name,
            "receive_id_type": "open_id",
        })

    if recipients:
        return {
            "ok": True,
            "source": "USER_MAP",
            "map_valid_json": map_valid,
            "map_error": map_error,
            "recipients": recipients,
            "count": len(recipients),
        }

    # 3. 兜底（DEV_FALLBACK）：未配置任何收件人 → 返回占位符
    placeholder = f"feishu_dev_{assignee_role}"
    return {
        "ok": True,
        "source": "DEV_FALLBACK",
        "map_valid_json": map_valid,
        "map_error": map_error,
        "recipients": [{
            "open_id": "",
            "name": "",
            "receive_id_type": "open_id",
            "_fallback_placeholder": placeholder,
        }],
        "count": 0,
    }


# ============================================================
# 实时监听：GatewayListener（捕获 chat_id / open_id）
# ============================================================
#
# 业务场景：用户配置好 ① Gateway + ② 飞书凭据后，想知道"我群里/跟 Bot 单聊的
# chat_id / open_id 是多少"——传统方式是手动查 gateway-state.json。本类自动
# 轮询 Gateway /v1/events，把新事件中的 chat_id / open_id 实时呈现到 UI。
#
# 设计要点：
#   - 后台 daemon 线程（不在主流程阻塞）
#   - 线程安全：_LISTENERS_LOCK 保护全局字典
#   - 限长缓冲：最多保留 50 条事件（防止内存膨胀）
#   - 单实例：start 时若已有活跃 listener，自动 stop 旧的（避免重复轮询）
#   - 优雅关闭：stop 信号通过 threading.Event 传递
#   - 前置检查：start 前检查 GATEWAY_HOST + CG_API_KEY 已配（无则拒绝）

class GatewayListener:
    """后台轮询 Gateway /v1/events 的线程封装。

    每个 listener 持有一个 daemon 线程，按 poll_interval 秒 GET 一次
    Gateway；新事件追加到 self.events。UI 通过 /listener/status 拉取。
    """

    # 单实例：最多同时存在的 listener 数（多余 start 会替换旧的）
    MAX_EVENTS_BUFFER = 50

    def __init__(
        self,
        listener_id: str,
        host: str,
        api_key: str,
        poll_interval: float = 2.0,
        idle_timeout_sec: float = 600.0,
    ) -> None:
        self.listener_id = listener_id
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.idle_timeout_sec = idle_timeout_sec

        self.started_at: float = time.time()
        self.last_poll_at: Optional[float] = None
        self.last_sequence: int = 0
        self.events: List[Dict[str, Any]] = []
        self.status: str = "starting"   # starting | listening | stopped | error
        self.error: Optional[str] = None
        self.poll_count: int = 0
        self.new_count: int = 0         # 累计新捕获事件数

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()   # 保护 self.events / self.last_sequence

    # ---- 生命周期 ----

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"gw-listener-{self.listener_id}",
        )
        self._thread.start()

    def stop(self, reason: str = "user_stop") -> None:
        with self._lock:
            if self.status in ("stopped", "error"):
                return
            self.status = "stopped"
            self.error = reason
        self._stop_event.set()

    def is_active(self) -> bool:
        with self._lock:
            return self.status in ("starting", "listening")

    # ---- 查询快照（线程安全）----

    def snapshot(self) -> Dict[str, Any]:
        """返回 UI 友好的视图。"""
        with self._lock:
            return {
                "listener_id": self.listener_id,
                "status": self.status,
                "error": self.error,
                "started_at": self.started_at,
                "uptime_sec": time.time() - self.started_at,
                "last_poll_at": self.last_poll_at,
                "poll_interval_sec": self.poll_interval,
                "poll_count": self.poll_count,
                "new_count": self.new_count,
                "last_sequence": self.last_sequence,
                "events": list(self.events),  # 拷贝避免外部修改
            }

    # ---- 内部：主循环 ----

    def _run(self) -> None:
        try:
            with self._lock:
                self.status = "listening"
            while not self._stop_event.is_set():
                # idle 超时检查
                if time.time() - self.started_at > self.idle_timeout_sec:
                    self.stop(reason="idle_timeout")
                    break
                self._poll_once()
                # 等待 poll_interval 或 stop 信号
                if self._stop_event.wait(self.poll_interval):
                    break
        except Exception as exc:  # 守护线程不应抛出
            logger.exception("GatewayListener %s 异常退出", self.listener_id)
            with self._lock:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"

    def _poll_once(self) -> None:
        url = f"{self.host}/v1/events?after_sequence={self.last_sequence}"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        status, data = _do_request("GET", url, headers=headers, timeout=5.0)
        with self._lock:
            self.last_poll_at = time.time()
            self.poll_count += 1
            if status != 200:
                # 401 / 网络异常 — 记录但保持 listening（不退出，下次重试）
                self.error = f"poll_status={status} body={data if isinstance(data, dict) else {'raw': str(data)[:200]}}"
                return
            self.error = None
            events = (data or {}).get("events", []) or []
            latest_seq = (data or {}).get("latestSequence", 0)
            for evt in events:
                seq = evt.get("sequence", 0)
                if seq and seq <= self.last_sequence:
                    continue
                normalized = self._normalize_event(evt)
                self.events.append(normalized)
                self.new_count += 1
                if seq:
                    self.last_sequence = max(self.last_sequence, seq)
            if latest_seq and latest_seq > self.last_sequence:
                self.last_sequence = latest_seq
            # 截断缓冲
            if len(self.events) > self.MAX_EVENTS_BUFFER:
                self.events = self.events[-self.MAX_EVENTS_BUFFER:]

    @staticmethod
    def _normalize_event(evt: Dict[str, Any]) -> Dict[str, Any]:
        """把 Gateway 原始事件归一化为 UI 友好结构。

        字段约定（与 polling_test.py 对齐）：
            sender.id         -> open_id  (ou_xxx)
            conversation.id   -> chat_id  (oc_xxx)
            conversation.type -> "direct" | "group"
        """
        sender = evt.get("sender") or {}
        conv = evt.get("conversation") or {}
        # text 可能为 list（[{"text": "..."}]）或 str
        raw_text = evt.get("text") or ""
        if isinstance(raw_text, list):
            text = "".join(
                (seg.get("text", "") if isinstance(seg, dict) else str(seg))
                for seg in raw_text
            )
        else:
            text = str(raw_text)
        return {
            "sequence": evt.get("sequence"),
            "event_type": evt.get("eventType") or evt.get("type"),
            "timestamp": evt.get("timestamp") or evt.get("createTime") or evt.get("create_time"),
            "text": text[:200],
            "open_id": sender.get("id") or sender.get("openId") or sender.get("open_id"),
            "sender_name": sender.get("name") or sender.get("displayName"),
            "sender_type": sender.get("type"),
            "chat_id": conv.get("id") or conv.get("chatId") or conv.get("chat_id"),
            "conversation_type": conv.get("type"),
        }


# 全局 listener 注册表
_LISTENERS: Dict[str, GatewayListener] = {}
_LISTENERS_LOCK = threading.Lock()


def get_active_listener() -> Optional[GatewayListener]:
    """返回当前唯一的活跃 listener（若没有返回 None）。"""
    with _LISTENERS_LOCK:
        for lst in _LISTENERS.values():
            if lst.is_active():
                return lst
    return None


def stop_all_listeners(reason: str = "shutdown") -> None:
    with _LISTENERS_LOCK:
        for lst in list(_LISTENERS.values()):
            lst.stop(reason=reason)


def _role_to_env_key(role: str) -> str:
    """role 名 → FEISHU_GROUP_<...> 后缀。

    例: "HSE 经理" → "HSE"; "作业负责人" → ""
    规则：保留 ASCII 字母/数字/下划线，其余剔除。
    """
    return re.sub(r"[^A-Za-z0-9_]+", "", role) or "ROLE"


# ============================================================
# Flask app
# ============================================================

app = Flask(__name__, static_folder=None)


# ---- 全局错误处理 ----

@app.errorhandler(400)
def _e400(e):
    return jsonify({"ok": False, "error": str(e) or "Bad Request"}), 400


@app.errorhandler(404)
def _e404(e):
    return jsonify({"ok": False, "error": "Not Found"}), 404


@app.errorhandler(500)
def _e500(e):
    import traceback
    logger.error("500: %s\n%s", e, traceback.format_exc())
    return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500


@app.errorhandler(Exception)
def _eall(e):
    import traceback
    logger.error("未捕获异常: %s\n%s", type(e).__name__, traceback.format_exc())
    return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ---- 路由 ----

@app.route("/")
def index():
    """GET / → 飞书配置 UI 页面。"""
    logger.info("[GET] / 进入: render feishu_config.html")
    try:
        html = (TEMPLATES_DIR / "feishu_config.html").read_text(encoding="utf-8")
        return html
    except FileNotFoundError as exc:
        logger.exception("[GET] / 异常: %s", exc)
        return f"<h1>模板文件缺失: {TEMPLATES_DIR / 'feishu_config.html'}</h1>", 500


@app.route("/api/feishu/config", methods=["GET"])
def api_get_config():
    """GET /api/feishu/config → 返回当前 .env 中飞书相关字段（API key 遮罩）。"""
    logger.info("[GET] /api/feishu/config 进入")
    try:
        view = get_managed_view()
        logger.info(
            "[GET] /api/feishu/config 响应: env_exists=%s managed_keys=%d user_map=%d legacy=%d",
            view["env_exists"], len(view["managed"]),
            len(view["user_map"]), len(view["legacy_group_overrides"]),
        )
        return jsonify({"ok": True, "view": view})
    except Exception as exc:
        logger.exception("[GET] /api/feishu/config 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/feishu/config", methods=["POST"])
def api_save_config():
    """POST /api/feishu/config → 把表单内容写入 .env。

    2026-08-13 重构：表单提交 user_map（[{open_id, role, name, _delete}]）
    替代旧的 group_overrides + FEISHU_CONVERSATION_MAP。
    2026-08-17 清理：USER_MAP 移除 chat_id 字段。

    Body:
        {
            "managed": {"GATEWAY_HOST": "...", "CG_API_KEY": "***|新值", ...},
            "user_map": [
                {"open_id": "ou_xxx", "role": "车间主任",
                 "name": "张三", "_delete": false},
                ...
            ],
            "group_map": [...],
            "accounts":  [...],
        }

    安全规则：
        - managed.* 中 "***" 视为"保留原值"，不写盘
        - managed.* 空字符串视为"删除该 key"
        - user_map 每行 {_delete:true} 时从 dict 删除该 open_id
        - group_map 每行 {_delete:true} 时从 dict 删除该 chat_id
        - 自动清理旧 FEISHU_CONVERSATION_MAP / FEISHU_GROUP_<ROLE> 残留
        - accounts 多账户同步写 3 处（项目根 .env + gateway .env + config.feishu.local.json）
    """
    logger.info("[POST] /api/feishu/config 进入")
    try:
        body = request.get_json(force=True) or {}
        managed_in: Dict[str, str] = body.get("managed") or {}
        user_map_in: List[Dict[str, Any]] = body.get("user_map") or []
        group_map_in: List[Dict[str, Any]] = body.get("group_map") or []
        accounts_in: List[Dict[str, Any]] = body.get("accounts") or []

        # 1. 处理 managed（敏感字段"***"→保留原值；空→删除）
        existing = {k: v for k, v, _ in parse_env_file(ENV_FILE)}
        new_entries: List[Tuple[str, str]] = []
        for key in MANAGED_KEYS_ORDER:
            user_val = (managed_in.get(key) or "").strip()
            if key in SENSITIVE_KEYS and user_val == "***":
                # 保留原值
                if key in existing and existing[key]:
                    new_entries.append((key, existing[key]))
                # 否则跳过（不写入空 key）
                continue
            if user_val == "":
                # 显式删除
                new_entries.append((key, ""))
                continue
            new_entries.append((key, user_val))

        # 2. 处理 user_map（动态行）
        # 起始：从现有 .env 读取的 dict
        existing_raw = existing.get(FEISHU_USER_MAP_KEY, "").strip()
        try:
            user_map_dict: Dict[str, Dict[str, str]] = (
                json.loads(existing_raw) if existing_raw else {}
            )
            if not isinstance(user_map_dict, dict):
                user_map_dict = {}
        except json.JSONDecodeError:
            user_map_dict = {}

        for row in user_map_in:
            open_id = (row.get("open_id") or "").strip()
            delete = bool(row.get("_delete"))
            if not open_id:
                continue
            if delete:
                user_map_dict.pop(open_id, None)
                continue
            role = (row.get("role") or "").strip()
            name = (row.get("name") or "").strip()
            if not role:
                # role 缺失视为无效 → 跳过（不写盘；但也不删）
                continue
            user_map_dict[open_id] = {
                "role": role,
                "name": name,
            }

        new_user_map_value = json.dumps(
            user_map_dict, ensure_ascii=False, separators=(",", ":")
        )
        new_entries.append((FEISHU_USER_MAP_KEY, new_user_map_value))

        # 2B. 处理 group_map（动态行；主键 chat_id）
        # 起始：从现有 .env 读取的 dict
        existing_gm_raw = existing.get(FEISHU_GROUP_MAP_KEY, "").strip()
        try:
            group_map_dict: Dict[str, Dict[str, str]] = (
                json.loads(existing_gm_raw) if existing_gm_raw else {}
            )
            if not isinstance(group_map_dict, dict):
                group_map_dict = {}
        except json.JSONDecodeError:
            group_map_dict = {}

        for row in group_map_in:
            chat_id = (row.get("chat_id") or "").strip()
            delete = bool(row.get("_delete"))
            if not chat_id:
                continue
            if delete:
                group_map_dict.pop(chat_id, None)
                continue
            name = (row.get("name") or "").strip()
            description = (row.get("description") or "").strip()
            if not name:
                # name 必填；缺失则跳过该行（不写盘；但也不删）
                continue
            group_map_dict[chat_id] = {
                "name": name,
                "description": description,
            }

        new_group_map_value = json.dumps(
            group_map_dict, ensure_ascii=False, separators=(",", ":")
        )
        new_entries.append((FEISHU_GROUP_MAP_KEY, new_group_map_value))

        # 2C. 处理多账户（FEISHU_ACCOUNTS + gateway .env + config.feishu.local.json）
        validated_accounts: List[Dict[str, str]] = []
        seen_ids: set[str] = set()
        account_errors: List[str] = []
        for idx, row in enumerate(accounts_in):
            aid = (row.get("account_id") or "").strip()
            if not aid:
                continue  # 空主键行:跳过(UI 已拦截,这里再保底)
            if aid in seen_ids:
                account_errors.append(f"row[{idx}] account_id={aid} 重复")
                continue
            err = _validate_account_id(aid)
            if err is not None:
                account_errors.append(f"row[{idx}] account_id={aid}: {err}")
                continue
            seen_ids.add(aid)
            validated_accounts.append({
                "account_id": aid,
                "description": (row.get("description") or "").strip(),
                "domain": ((row.get("domain") or "feishu").strip() or "feishu"),
                "app_id": (row.get("app_id") or "").strip(),
                "app_secret": row.get("app_secret", "") or "",
                "verification_token": row.get("verification_token", "") or "",
            })
        if account_errors:
            return jsonify({
                "ok": False,
                "error": "accounts validation: " + "; ".join(account_errors),
            }), 400

        # 处理 "***" 占位符 → 从 gateway .env 读回原值
        _resolve_account_secrets(validated_accounts)

        # 写 FEISHU_ACCOUNTS JSON(不含 secrets) → .env
        accounts_json: Dict[str, Dict[str, str]] = {}
        for acc in validated_accounts:
            accounts_json[acc["account_id"]] = {
                "description": acc["description"],
                "domain": acc["domain"],
                "app_id": acc["app_id"],
                # 仅为 UI 展示完整性记录;真实 secrets 在 gateway .env
                "verification_token": acc["verification_token"],
            }
        if accounts_json:
            new_entries.append((
                FEISHU_ACCOUNTS_KEY,
                json.dumps(accounts_json, ensure_ascii=False, separators=(",", ":")),
            ))
        else:
            new_entries.append((FEISHU_ACCOUNTS_KEY, ""))

        # 兼容旧字段:有 default 账户 → 回写顶层 FEISHU_APP_ID/SECRET/DOMAIN(平滑迁移)
        # 无 accounts → 清空旧字段(避免幽灵配置)
        default_acc = next(
            (a for a in validated_accounts if a["account_id"] == "default"), None,
        )
        if default_acc is not None:
            if default_acc["app_id"]:
                new_entries.append(("FEISHU_APP_ID", default_acc["app_id"]))
            if default_acc["app_secret"]:
                new_entries.append(("FEISHU_APP_SECRET", default_acc["app_secret"]))
            new_entries.append(("FEISHU_DOMAIN", default_acc["domain"]))
        elif not validated_accounts:
            new_entries.extend([
                ("FEISHU_APP_ID", ""),
                ("FEISHU_APP_SECRET", ""),
                ("FEISHU_DOMAIN", ""),
            ])

        # 3. 顺手清理旧的 FEISHU_CONVERSATION_MAP / FEISHU_GROUP_<ROLE>
        if "FEISHU_CONVERSATION_MAP" in existing:
            new_entries.append(("FEISHU_CONVERSATION_MAP", ""))
        for k in existing:
            if FEISHU_GROUP_PATTERN.match(k):
                new_entries.append((k, ""))

        # 4. 写盘（项目根 .env）
        result = upsert_env_entries(new_entries)

        # 5. 同步写 gateway .env 与 config.feishu.local.json（多账户运行时配置）
        gateway_result: Optional[Dict[str, Any]] = None
        config_result: Optional[Dict[str, Any]] = None
        try:
            gateway_result = _upsert_gateway_accounts_env(validated_accounts)
        except Exception as gw_exc:
            logger.exception(
                "[POST] /api/feishu/config: 写 gateway .env 失败: %s", gw_exc,
            )
            return jsonify({
                "ok": False,
                "error": f"accounts pass 写 gateway .env 失败: {gw_exc}。"
                          "已写入项目根 .env,但 gateway 配置未同步;请重试保存。",
            }), 500
        try:
            config_result = _upsert_feishu_config_accounts(validated_accounts)
        except Exception as cfg_exc:
            logger.exception(
                "[POST] /api/feishu/config: 写 config.feishu.local.json 失败: %s", cfg_exc,
            )
            return jsonify({
                "ok": False,
                "error": f"accounts pass 写 config.feishu.local.json 失败: {cfg_exc}。"
                          "已写入项目根 .env 和 gateway .env,但 JSON 配置未同步;请重试保存。",
            }), 500

        logger.info(
            "[POST] /api/feishu/config 响应: updated=%s removed=%s backup=%s "
            "user_map_size=%d accounts_size=%d gateway_backup=%s config_backup=%s",
            result["updated"], result["removed"], result["backup"],
            len(user_map_dict), len(validated_accounts),
            gateway_result["backup"], config_result["backup"],
        )
        return jsonify({
            "ok": True,
            **result,
            "user_map_size": len(user_map_dict),
            "accounts_size": len(validated_accounts),
            "gateway_backup": gateway_result["backup"],
            "config_backup": config_result["backup"],
            "gateway_written_keys": gateway_result["written_keys"],
        })

    except Exception as exc:
        logger.exception("[POST] /api/feishu/config 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/feishu/test/gateway", methods=["POST"])
def api_test_gateway():
    """POST /api/feishu/test/gateway → GET Gateway /healthz。

    Body:
        {"host": "http://...", "api_key": "..."|""}
    """
    logger.info("[POST] /api/feishu/test/gateway 进入")
    try:
        body = request.get_json(force=True) or {}
        host = (body.get("host") or "").strip() or DEFAULTS["GATEWAY_HOST"]
        api_key = (body.get("api_key") or "").strip()
        result = test_gateway_connection(host, api_key)
        log_fn = logger.info if result["ok"] else logger.warning
        log_fn(
            "[POST] /api/feishu/test/gateway 响应: ok=%s status=%s url=%s",
            result["ok"], result["status"], result["url"],
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("[POST] /api/feishu/test/gateway 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/feishu/test/resolve", methods=["POST"])
def api_test_resolve():
    """POST /api/feishu/test/resolve → 预演 USER_MAP 解析（不调 gateway）。

    Body（2026-08-13 重构 / 2026-08-17 清理 chat_id）：
        {
            "assignee_role": "车间主任",
            "user_map_json": "{\"ou_xxx\": {\"role\": \"车间主任\", \"name\": \"张三\"}}"
        }
    """
    logger.info("[POST] /api/feishu/test/resolve 进入")
    try:
        body = request.get_json(force=True) or {}
        role = (body.get("assignee_role") or "").strip()
        map_json = (body.get("user_map_json") or "").strip()
        result = test_resolve_conversation_id(role, map_json)
        logger.info(
            "[POST] /api/feishu/test/resolve 响应: role=%s source=%s count=%d",
            role, result["source"], result["count"],
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("[POST] /api/feishu/test/resolve 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


# ============================================================
# 路由：实时监听（chat_id / open_id 捕获）
# ============================================================

@app.route("/api/feishu/listener/start", methods=["POST"])
def api_listener_start():
    """POST /api/feishu/listener/start → 启动后台轮询 Gateway /v1events。

    前置条件：
        ① GATEWAY_HOST 已配
        ② CG_API_KEY 已配
    （FEISHU_APP_* 仅在直发飞书时才需要，监听只需要 Gateway 鉴权）

    Body（可选）：
        {"poll_interval_sec": 2.0, "idle_timeout_sec": 600}
    """
    logger.info("[POST] /api/feishu/listener/start 进入")
    try:
        # 1. 前置检查：① ② 必须已配
        existing = {k: v for k, v, _ in parse_env_file(ENV_FILE)}
        host = existing.get("GATEWAY_HOST", "").strip()
        api_key = existing.get("CG_API_KEY", "").strip() or existing.get("CHANNEL_GATEWAY_API_KEY", "").strip()
        missing = []
        if not host:
            missing.append("GATEWAY_HOST")
        if not api_key:
            missing.append("CG_API_KEY")
        if missing:
            logger.warning("[POST] /api/feishu/listener/start 拒绝: 缺前置配置 %s", missing)
            return jsonify({
                "ok": False,
                "error": f"前置条件未满足：缺少 {', '.join(missing)}。请先在 UI ① 区配置并保存。",
                "missing": missing,
            }), 400

        # 2. 解析可选参数
        body = request.get_json(force=True, silent=True) or {}
        poll_interval = float(body.get("poll_interval_sec") or 2.0)
        idle_timeout = float(body.get("idle_timeout_sec") or 600.0)
        poll_interval = max(1.0, min(poll_interval, 30.0))      # 1~30s
        idle_timeout = max(60.0, min(idle_timeout, 3600.0))    # 1min~1h

        # 3. 单实例：先停旧的
        old = get_active_listener()
        if old:
            old.stop(reason="replaced_by_new")

        # 4. 创建并启动
        listener_id = uuid.uuid4().hex[:12]
        listener = GatewayListener(
            listener_id=listener_id,
            host=host,
            api_key=api_key,
            poll_interval=poll_interval,
            idle_timeout_sec=idle_timeout,
        )
        with _LISTENERS_LOCK:
            _LISTENERS[listener_id] = listener
        listener.start()

        logger.info(
            "[POST] /api/feishu/listener/start 响应: listener_id=%s poll_interval=%.1fs",
            listener_id, poll_interval,
        )
        return jsonify({
            "ok": True,
            "listener_id": listener_id,
            "snapshot": listener.snapshot(),
            "instruction": (
                "监听已启动。请到飞书群里（或跟 Bot 单聊）发一条消息，"
                "本服务会自动捕获 chat_id / open_id。"
                "捕获完成后，刷新本卡片或点『刷新事件列表』查看。"
            ),
        })
    except Exception as exc:
        logger.exception("[POST] /api/feishu/listener/start 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/feishu/listener/status", methods=["GET"])
def api_listener_status():
    """GET /api/feishu/listener/status → 返回当前活跃 listener 的快照。"""
    logger.info("[GET] /api/feishu/listener/status 进入")
    try:
        listener = get_active_listener()
        if listener is None:
            # 尝试返回最近的（即使已停）
            with _LISTENERS_LOCK:
                recent = list(_LISTENERS.values())
            recent.sort(key=lambda x: x.started_at, reverse=True)
            if recent:
                snap = recent[0].snapshot()
                snap["active"] = False
                logger.info(
                    "[GET] /api/feishu/listener/status 响应: 无活跃, 最近=%s status=%s events=%d",
                    snap["listener_id"], snap["status"], len(snap["events"]),
                )
                return jsonify({"ok": True, "active": False, "snapshot": snap})
            logger.info("[GET] /api/feishu/listener/status 响应: 无 listener")
            return jsonify({"ok": True, "active": False, "snapshot": None})

        snap = listener.snapshot()
        snap["active"] = True
        logger.info(
            "[GET] /api/feishu/listener/status 响应: listener_id=%s status=%s events=%d new=%d",
            snap["listener_id"], snap["status"], len(snap["events"]), snap["new_count"],
        )
        return jsonify({"ok": True, "active": True, "snapshot": snap})
    except Exception as exc:
        logger.exception("[GET] /api/feishu/listener/status 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/feishu/listener/stop", methods=["POST"])
def api_listener_stop():
    """POST /api/feishu/listener/stop → 停止当前活跃 listener。

    Body（可选）：
        {"listener_id": "..."}   # 不传则停当前活跃
    """
    logger.info("[POST] /api/feishu/listener/stop 进入")
    try:
        body = request.get_json(force=True, silent=True) or {}
        target_id = (body.get("listener_id") or "").strip()
        stopped = []
        with _LISTENERS_LOCK:
            for lid, lst in list(_LISTENERS.items()):
                if target_id and lid != target_id:
                    continue
                if lst.is_active():
                    lst.stop(reason="user_stop")
                    stopped.append(lid)
        logger.info("[POST] /api/feishu/listener/stop 响应: stopped=%s", stopped)
        return jsonify({"ok": True, "stopped": stopped})
    except Exception as exc:
        logger.exception("[POST] /api/feishu/listener/stop 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.route("/api/feishu/config/delete", methods=["POST"])
def api_delete_config_item():
    """POST /api/feishu/config/delete → 按主键删除单条 USER_MAP / GROUP_MAP / 账户 配置。

    Body：
        {
            "type": "user" | "group" | "account",
            "key":  "ou_xxx" | "oc_xxx" | "<account_id>"
        }

    行为：
        - user / group: 直接调 upsert_env_entries 写盘 .env（备份 .env.bak）
        - account: 同时改三处（FEISHU_ACCOUNTS JSON + gateway .env per-account vars +
                 config.feishu.local.json accounts 块）
        - 主键不存在 → 视为成功（幂等），返回 deleted=False
        - 不级联：删 GROUP_MAP 一行时，USER_MAP 中共享 chat_id 的 user 不动
          （群名显示列会自动变空 — 前端 lookupGroupMapByChatId 返回 null）

    写入约束：
        - 单独的 delete 操作，不与表单 save 混合；前端 + 后端双重校验
        - type 取值严格 white-list（避免任意写盘）
    """
    logger.info("[POST] /api/feishu/config/delete 进入")
    try:
        body = request.get_json(force=True, silent=True) or {}
        type_ = (body.get("type") or "").strip()
        key = (body.get("key") or "").strip()

        if type_ not in ("user", "group", "account"):
            return jsonify({
                "ok": False,
                "error": "type 必须为 'user' / 'group' / 'account'",
            }), 400
        if not key:
            return jsonify({"ok": False, "error": "key 不能为空"}), 400

        # ===== 账户删除分支 =====
        if type_ == "account":
            err = _validate_account_id(key)
            if err is not None:
                return jsonify({"ok": False, "error": err}), 400
            accounts_dict = _load_accounts_from_env(ENV_FILE)
            if key not in accounts_dict:
                logger.info(
                    "[POST] /api/feishu/config/delete: account_id=%s 不存在（幂等跳过）",
                    key,
                )
                return jsonify({
                    "ok": True,
                    "deleted": False,
                    "type": type_,
                    "key": key,
                    "message": "account_id 不存在；幂等跳过",
                    "remaining_size": len(accounts_dict),
                })
            accounts_dict.pop(key)
            # 1. 重写 FEISHU_ACCOUNTS JSON
            if accounts_dict:
                upsert_env_entries([(
                    FEISHU_ACCOUNTS_KEY,
                    json.dumps(accounts_dict, ensure_ascii=False, separators=(",", ":")),
                )])
            else:
                upsert_env_entries([(FEISHU_ACCOUNTS_KEY, "")])
            # 2. 重建剩余账户列表(用于 gateway .env + config JSON)
            remaining_accounts: List[Dict[str, str]] = []
            for aid, meta in accounts_dict.items():
                gw = _load_gateway_account_env(aid)
                remaining_accounts.append({
                    "account_id": aid,
                    "description": meta.get("description", ""),
                    "domain": meta.get("domain", "feishu"),
                    "app_id": meta.get("app_id", ""),
                    "app_secret": gw.get("app_secret", ""),
                    "verification_token": gw.get("verification_token", ""),
                })
            # 3. 同步 gateway .env（删除 deleted_ids 的 4 个键 + 重写剩余）
            gw_result = _upsert_gateway_accounts_env(
                remaining_accounts, deleted_ids=[key],
            )
            # 4. 同步 config.feishu.local.json accounts 块
            cfg_result = _upsert_feishu_config_accounts(remaining_accounts)
            # 5. 若删的是 default 账户,清掉顶层旧字段
            if key == "default":
                upsert_env_entries([
                    ("FEISHU_APP_ID", ""),
                    ("FEISHU_APP_SECRET", ""),
                    ("FEISHU_DOMAIN", ""),
                ])
            logger.info(
                "[POST] /api/feishu/config/delete 响应: account_id=%s 已删除；"
                "剩 %d 个,gateway_backup=%s config_backup=%s",
                key, len(accounts_dict),
                gw_result["backup"], cfg_result["backup"],
            )
            return jsonify({
                "ok": True,
                "deleted": True,
                "type": type_,
                "key": key,
                "remaining_size": len(accounts_dict),
                "gateway_backup": gw_result["backup"],
                "config_backup": cfg_result["backup"],
            })

        env_key = FEISHU_USER_MAP_KEY if type_ == "user" else FEISHU_GROUP_MAP_KEY
        existing = {k: v for k, v, _ in parse_env_file(ENV_FILE)}
        raw = existing.get(env_key, "").strip()
        cur_dict: Dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    cur_dict = parsed
            except json.JSONDecodeError:
                # .env 里 JSON 已损坏 → 不动它（让用户在 UI 里重新保存）
                logger.warning(
                    "[POST] /api/feishu/config/delete: %s JSON 解析失败，跳过",
                    env_key,
                )
                return jsonify({
                    "ok": False,
                    "error": f"{env_key} 当前值不是合法 dict，无法定位 key",
                }), 500

        if key not in cur_dict:
            logger.info(
                "[POST] /api/feishu/config/delete: type=%s key=%s 不存在（幂等跳过）",
                type_, key,
            )
            return jsonify({
                "ok": True,
                "deleted": False,
                "type": type_,
                "key": key,
                "message": "key 不存在；幂等跳过",
            })

        cur_dict.pop(key, None)
        new_value = json.dumps(cur_dict, ensure_ascii=False, separators=(",", ":"))
        result = upsert_env_entries([(env_key, new_value)])
        logger.info(
            "[POST] /api/feishu/config/delete 响应: type=%s key=%s 已删除；%s 现 %d 条",
            type_, key, env_key, len(cur_dict),
        )
        return jsonify({
            "ok": True,
            "deleted": True,
            "type": type_,
            "key": key,
            "remaining_size": len(cur_dict),
            "updated": result["updated"],
            "backup": result["backup"],
        })
    except Exception as exc:
        logger.exception("[POST] /api/feishu/config/delete 异常: %s", exc)
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="A7/notify 飞书通道配置 UI")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=5003, help="监听端口（默认 5003；避开 A5=5001/A6=5002/AgentConfig=5000）")
    args = parser.parse_args()

    print("=" * 60)
    print("A7/notify 飞书通道配置 UI")
    print("=" * 60)
    print(f"  监听地址:   http://{args.host}:{args.port}")
    print(f"  目标 .env:  {ENV_FILE}")
    print(f"  备份文件:   {ENV_BACKUP_FILE}")
    print("=" * 60)

    # 检查依赖（避免启动后才报）
    try:
        import flask  # noqa: F401
    except ImportError:
        print("[错误] 缺少依赖 flask，请运行: uv pip install flask")
        sys.exit(1)

    app.run(host=args.host, port=args.port, debug=False, threaded=True)

    # 进程退出前停掉所有 listener 线程
    stop_all_listeners(reason="process_exit")


__all__ = [
    # 路径常量
    "ENV_FILE",
    "ENV_BACKUP_FILE",
    "TEMPLATES_DIR",
    # 业务常量
    "MANAGED_KEYS_ORDER",
    "SENSITIVE_KEYS",
    "DEFAULTS",
    "FEISHU_GROUP_PREFIX",
    # .env 解析 / 序列化
    "parse_env_file",
    "serialize_env_file",
    "get_managed_view",
    "upsert_env_entries",
    # 测试 / 解析
    "test_gateway_connection",
    "test_resolve_conversation_id",
    # 实时监听
    "GatewayListener",
    "get_active_listener",
    "stop_all_listeners",
    # Flask app
    "app",
    "main",
]


if __name__ == "__main__":
    main()