"""A7/notify — P8 飞书通道适配器（精简版：只发，不拼）。

================================================================================
职责（极简版）
================================================================================

只做一件事：**把调用方准备好的消息正文发到指定位置**。

不负责：
    - 消息拼装（标题 / 模板 / 风险等级前缀）  → 由调用方完成
    - 收件人解析（assignee_role → user_map）  → 由调用方完成
    - 多收件人聚合 / 失败聚合                  → 由调用方完成
    - 重试 / 幂等策略                          → 由 Gateway / 调用方完成

本模块只暴露两个最小函数 + 一个 CLI：

================================================================================
公开函数
================================================================================

    send_to_group(text, *, chat_id=None, group_name=None) -> SendMessageResult
        群聊发送：把 text 发到指定群。chat_id 与 group_name 互斥必填其一：
            - chat_id     直传飞书群 ID（oc_xxx），不读 GROUP_MAP
            - group_name  按 .env 的 FEISHU_GROUP_MAP 反查该 name 的 chat_id

    send_to_user(text, *, name=None, open_id=None) -> SendMessageResult
        单聊发送：把 text 单聊给指定收件人。name 与 open_id 互斥必填其一：
            - name     按 .env 的 FEISHU_USER_MAP 反查该 name 的 open_id
            - open_id  直传飞书用户 open_id（webhook 回调场景），不读 USER_MAP

================================================================================
CLI 命令（在任何项目终端运行）
================================================================================

下面三条是**单行命令**（bash / PowerShell / CMD / Git Bash 通用）：

    # 群聊发送（按 chat_id 直传）
    python -m A7.notify.feishu_sender send_group --text "【告警】可燃气体浓度异常" --chat-id oc_xxxx

    # 群聊发送（按群聊名称反查 GROUP_MAP 拿 chat_id）
    python -m A7.notify.feishu_sender send_group --text "【告警】可燃气体浓度异常" --group-name "应急响应群"

    # 单聊发送（按姓名查 USER_MAP 拿 open_id）
    python -m A7.notify.feishu_sender send_user --text "请确认处置结果" --name "张三"

    # 单聊发送（直接传 open_id，webhook 回调场景；与 --name 互斥）
    python -m A7.notify.feishu_sender send_user --text "已收到" --open-id ou_511d109b8ac87f972af2d5e67e2c8270

    # 群聊发送【交互式 Card】（按 group_name，含按钮选项，可触发回调）
    python -m A7.notify.feishu_sender send_group --group-name "动火作业群" --text "【告警】可燃气体浓度异常" --card --title "⚠️ 气体浓度告警" --alert-id "gas_20260817_001" --option "已知悉:ack" --option "立即处理:handle" --option "误报:false_alarm"

    # 群聊发送【交互式 Card】（按 chat_id 直传，可省略 --option 表示纯文本卡片）
    python -m A7.notify.feishu_sender send_group --chat-id oc_xxxx --text "巡检结果已上传" --card --title "巡检通知"

    # 群聊发送【交互式 Card】（带 alert_id 但不带 option —— 仅作标识，便于后续卡片回调匹配）
    python -m A7.notify.feishu_sender send_group --group-name "动火作业群" --text "收到请回复处理结果" --card --alert-id "job_20260817_001"

注：以上示例均为单行，bash / PowerShell / CMD 直接复制即可执行。
如要换行提升可读性：
    - bash / Git Bash：行尾加 ``\\`` 作为续行
    - PowerShell：行尾加 `` ` `` （反引号）作为续行
    - Windows CMD：不支持多行直接复制，请使用单行版本

退出码：
    0  发送成功（Gateway 接受）
    1  入参错误 / USER_MAP 反查失败 / Gateway 业务错误
    2  其它未捕获异常

================================================================================
依赖
================================================================================

    agents.channel_gateway_client.send_message  HTTP 调用底层
    os.environ["FEISHU_USER_MAP"]              姓名 → open_id 反查（JSON 字符串）

FEISHU_USER_MAP 结构（.env JSON 字符串）：
    {
        "ou_alice_xxx": {"role": "车间主任", "name": "张三"},
        "ou_bob_yyy":   {"role": "车间主任", "name": "李四"}
    }

单聊按 name 查找规则：
    ① 遍历所有记录，匹配 name 字段
    ② 找到 1 条 → 返回 open_id（dict 的 key）。单聊始终走 open_id
       （open_id 是飞书用户的唯一标识）
    ③ 找到 0 条 → 报错（name 未配置）
    ④ 找到多条同名 → 报错（歧义）

注：USER_MAP 历史上曾含 chat_id 字段（2026-08-13 重构遗留），现已移除。
    群聊发送请改用 FEISHU_GROUP_MAP（按 name 反查 chat_id），
    USER_MAP 只承担"人"的索引维度。历史 .env 中残留的 chat_id 字段会被
    _load_user_map() 静默忽略。

FEISHU_GROUP_MAP 结构（.env JSON 字符串）：
    {
        "oc_xxx1": {"name": "应急响应群", "description": "P8 告警群"},
        "oc_xxx2": {"name": "日常巡检群", "description": ""}
    }

群聊按 name 查找规则（与 USER_MAP 同构）：
    ① 遍历所有记录，匹配 name 字段
    ② 找到 1 条 → 返回 chat_id（dict 的 key）
    ③ 找到 0 条 → 报错（name 未配置）
    ④ 找到多条同名 → 报错（歧义）

description 字段：
    - 非必填，可空
    - 仅作元数据描述，发送链路不读

chat_id 在 GROUP_MAP 里的角色：
    - 唯一主键（飞书群 ID）
    - 被 USER_MAP 多个 user 共享时，群聊名称只需要配一次

================================================================================
日志规范
================================================================================

入口 / 出口 / 异常按 [p8-notify] 进入 / 响应 / 异常 格式打印。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Dict, List, Optional, Tuple

from agents.channel_gateway_client import GatewayError, SendMessageResult, send_message


# ============================================================
# 常量
# ============================================================

FEISHU_USER_MAP_KEY: str = "FEISHU_USER_MAP"
FEISHU_GROUP_MAP_KEY: str = "FEISHU_GROUP_MAP"
FEISHU_CHANNEL: str = "feishu"


# ============================================================
# 日志
# ============================================================
# 统一用 basicConfig 配 root logger（幂等；多次调用不会重复加 handler）。
# 子 logger 只设 level；所有日志 propagate 到 root 统一输出，避免重复打印。

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("feishu_sender")
logger.setLevel(logging.INFO)


# ============================================================
# 1. USER_MAP 读取 + 按 name 反查 open_id（私有）
# ============================================================

def _load_user_map() -> Dict[str, Dict[str, str]]:
    """从 .env 读 FEISHU_USER_MAP，返回 {open_id: {role, name}, ...}。

    解析失败 → 空 dict（不打错误；调用方会按"找不到"处理）。

    兼容历史格式：旧 .env 中 USER_MAP 记录可能仍含 chat_id 字段（2026-08-13
    重构遗留），本函数静默忽略 chat_id（不返回，不影响解析）。
    """
    raw = __import__("os").environ.get(FEISHU_USER_MAP_KEY, "").strip()  # noqa: E402
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[p8-notify] FEISHU_USER_MAP 解析失败: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("[p8-notify] FEISHU_USER_MAP 顶层必须是 JSON object")
        return {}
    cleaned: Dict[str, Dict[str, str]] = {}
    for open_id, info in parsed.items():
        if not isinstance(info, dict):
            continue
        cleaned[str(open_id)] = {
            "role": str(info.get("role", "") or "").strip(),
            "name": str(info.get("name", "") or "").strip(),
        }
    return cleaned


def _find_open_id_by_name(name: str) -> Tuple[str, str]:
    """按 name 查 open_id（单聊通道）。

    Returns:
        (open_id, found_name)

    Raises:
        ValueError:
            - name 为空
            - USER_MAP 完全没有此 name
            - 同名存在多条记录（歧义；需通过 open_id 区分）

    说明：
        单聊走 open_id（dict 的 key）。群发请改用 FEISHU_GROUP_MAP +
        send_to_group(group_name=...)。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name 不能为空")

    user_map = _load_user_map()
    matches: list[Tuple[str, str]] = []  # (open_id, found_name)

    for open_id, info in user_map.items():
        if info["name"] == name:
            matches.append((open_id, info["name"]))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"name='{name}' 在 FEISHU_USER_MAP 中存在 {len(matches)} 条同名记录："
            f"{[m[0] for m in matches]}。请通过 open_id 区分或合并后再发。"
        )
    raise ValueError(
        f"FEISHU_USER_MAP 中找不到 name='{name}'。请检查 .env 配置。"
    )


# ============================================================
# 1B. GROUP_MAP 读取 + 按 name 反查 chat_id（私有）
# ============================================================
#
# FEISHU_GROUP_MAP = {chat_id: {name, description}, ...}（主键 chat_id）
# description 可空；name 必填（空 name 记录视为无效丢弃）。
# 与 USER_MAP 的区别：USER_MAP 按 open_id 索引（人），
# GROUP_MAP 按 chat_id 索引（群）；同一 chat_id 可被多个 user 共享，
# 群名/描述只需要配一次。

def _load_group_map() -> Dict[str, Dict[str, str]]:
    """从 .env 读 FEISHU_GROUP_MAP，返回 {chat_id: {name, description}, ...}。

    解析失败 → 空 dict（不打错误；调用方会按"找不到"处理）。
    name 为空的记录视为无效丢弃。
    """
    raw = __import__("os").environ.get(FEISHU_GROUP_MAP_KEY, "").strip()  # noqa: E402
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[p8-notify] FEISHU_GROUP_MAP 解析失败: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("[p8-notify] FEISHU_GROUP_MAP 顶层必须是 JSON object")
        return {}
    cleaned: Dict[str, Dict[str, str]] = {}
    for chat_id, info in parsed.items():
        if not isinstance(info, dict):
            continue
        name = str(info.get("name", "") or "").strip()
        if not name:
            # name 必填；空 name 视为无效丢弃
            continue
        cleaned[str(chat_id).strip()] = {
            "name": name,
            "description": str(info.get("description", "") or "").strip(),
        }
    return cleaned


def _find_chat_id_by_group_name(name: str) -> Tuple[str, str]:
    """按 name 反查 chat_id（群聊通道；镜像 _find_open_id_by_name 模式）。

    Returns:
        (chat_id, found_name)

    Raises:
        ValueError:
            - name 为空
            - GROUP_MAP 完全没有此 name
            - 同名存在多条记录（歧义；需通过 chat_id 区分）
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name 不能为空")

    group_map = _load_group_map()
    matches: list[Tuple[str, str]] = []  # (chat_id, found_name)

    for chat_id, info in group_map.items():
        if info["name"] == name:
            matches.append((chat_id, info["name"]))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"name='{name}' 在 FEISHU_GROUP_MAP 中存在 {len(matches)} 条同名记录："
            f"{[m[0] for m in matches]}。请通过 chat_id 区分或合并后再发。"
        )
    raise ValueError(
        f"FEISHU_GROUP_MAP 中找不到 name='{name}'。请检查 .env 配置。"
    )


# ============================================================
# 2. 公开 API
# ============================================================

def send_to_group(
    text: str,
    *,
    chat_id: Optional[str] = None,
    group_name: Optional[str] = None,
    account_id: Optional[str] = None,
) -> SendMessageResult:
    """群聊发送：把 text 发到指定群。

    支持两种群聊指定方式（**互斥，必填其一**）：

    1. **按 chat_id 直传**：直接使用 chat_id（"oc_xxx"），不读 GROUP_MAP
       适用场景：已知 ID、自动化脚本
       例：send_to_group(text="...", chat_id="oc_xxx")

    2. **按 group_name 反查**：从 .env 的 FEISHU_GROUP_MAP 按 name 反查 chat_id
       适用场景：人工配置场景（人只记得群名）
       例：send_to_group(text="...", group_name="应急响应群")

    Args:
        text: 消息正文（调用方自行拼装）
        chat_id: 飞书群 ID（与 group_name 互斥；二选一）
        group_name: 群聊名称（与 chat_id 互斥；二选一）
        account_id: Gateway 账号 ID（多账号机器人场景；不传走 channel_gateway_client 默认值）

    Returns:
        SendMessageResult

    Raises:
        ValueError:
            - text 为空
            - chat_id 和 group_name 都未传（必填其一）
            - chat_id 和 group_name 都传了（互斥）
            - 按 group_name 反查 GROUP_MAP 失败（找不到 / 同名歧义）
        GatewayError: 网关业务错误
        requests.RequestException: 网络错误

    Examples:
        >>> from A7.notify.feishu_sender import send_to_group
        >>> # 按 chat_id
        >>> send_to_group("【告警】...", chat_id="oc_xxx")
        >>> # 按群聊名称（反查 GROUP_MAP）
        >>> send_to_group("【告警】...", group_name="应急响应群")
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    # 互斥校验
    has_chat_id = bool(chat_id and chat_id.strip())
    has_group_name = bool(group_name and group_name.strip())
    if not has_chat_id and not has_group_name:
        raise ValueError("chat_id 和 group_name 必须传其中一个（互斥）")
    if has_chat_id and has_group_name:
        raise ValueError("chat_id 和 group_name 只能传一个（互斥）")

    if has_group_name:
        # 路径 2：按群聊名称反查 GROUP_MAP
        resolved_chat_id, found_name = _find_chat_id_by_group_name(group_name)
        log_tag = f"group_name={found_name} → chat_id={resolved_chat_id}"
    else:
        # 路径 1：直接用 chat_id（不读 GROUP_MAP）
        resolved_chat_id = chat_id.strip()
        log_tag = f"chat_id={resolved_chat_id}（直传，不走 GROUP_MAP）"

    logger.info(
        "[p8-notify] send_to_group 进入: %s text_len=%d",
        log_tag, len(text),
    )
    try:
        result = send_message(
            text=text,
            channel=FEISHU_CHANNEL,
            conversation_id=resolved_chat_id,
            receive_id_type="chat_id",
            account_id=account_id,
        )
    except Exception as exc:
        logger.exception(
            "[p8-notify] send_to_group 异常: %s err=%s",
            log_tag, exc,
        )
        raise

    logger.info(
        "[p8-notify] send_to_group 响应: %s status=%s "
        "intent_id=%s message_id=%s replayed=%s",
        log_tag, result.status, result.intent_id,
        result.platform_message_id, result.replayed,
    )
    return result


def send_to_user(
    text: str,
    *,
    name: Optional[str] = None,
    open_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> SendMessageResult:
    """单聊发送：把 text 单聊给指定收件人。

    支持两种收件人指定方式（**互斥，必填其一**）：

    1. **按姓名（name）**：从 .env 的 FEISHU_USER_MAP 按 name 反查 open_id
       适用场景：人工配置场景（人只记得名字）
       例：send_to_user(text="...", name="张三")

    2. **按 open_id 直传**：直接使用 open_id，不读 USER_MAP
       适用场景：webhook 回调（已经从 event.sender.sender_id.open_id 拿到 ID）、
                 已知 ID 不想走反查
       例：send_to_user(text="...", open_id="ou_511d109b8ac87f972af2d5e67e2c8270")

    Args:
        text: 消息正文（调用方自行拼装）
        name: 中文姓名（与 open_id 互斥；二选一）
        open_id: 飞书用户 open_id（与 name 互斥；二选一）
        account_id: Gateway 账号 ID（多账号机器人场景；不传走 channel_gateway_client 默认值）

    Returns:
        SendMessageResult

    Raises:
        ValueError:
            - text 为空
            - name 和 open_id 都未传（必填其一）
            - name 和 open_id 都传了（互斥）
            - 按 name 反查 USER_MAP 失败（找不到 / 同名歧义）
        GatewayError: 网关业务错误
        requests.RequestException: 网络错误

    Examples:
        >>> from A7.notify.feishu_sender import send_to_user
        >>> # 按姓名
        >>> send_to_user("请确认处置结果", name="张三")
        >>> # 按 open_id（webhook 回调场景）
        >>> send_to_user("已收到", open_id="ou_511d109b8ac87f972af2d5e67e2c8270")
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    # 互斥校验
    has_name = bool(name and name.strip())
    has_open_id = bool(open_id and open_id.strip())
    if not has_name and not has_open_id:
        raise ValueError("name 和 open_id 必须传其中一个（互斥）")
    if has_name and has_open_id:
        raise ValueError("name 和 open_id 只能传一个（互斥）")

    if has_name:
        # 路径 1：按姓名反查 USER_MAP
        resolved_open_id, found_name = _find_open_id_by_name(name)
        log_tag = f"name={found_name} → open_id={resolved_open_id}"
    else:
        # 路径 2：直接用 open_id（不读 USER_MAP）
        resolved_open_id = open_id.strip()
        found_name = ""
        log_tag = f"open_id={resolved_open_id}（直传，不走 USER_MAP）"

    logger.info(
        "[p8-notify] send_to_user 进入: %s text_len=%d",
        log_tag, len(text),
    )
    try:
        result = send_message(
            text=text,
            channel=FEISHU_CHANNEL,
            conversation_id=resolved_open_id,
            receive_id_type="open_id",
            account_id=account_id,
        )
    except Exception as exc:
        logger.exception(
            "[p8-notify] send_to_user 异常: %s err=%s",
            log_tag, exc,
        )
        raise

    logger.info(
        "[p8-notify] send_to_user 响应: %s status=%s "
        "intent_id=%s message_id=%s replayed=%s",
        log_tag, result.status, result.intent_id,
        result.platform_message_id, result.replayed,
    )
    return result


# ============================================================
# 2B. 飞书交互式卡片（Card）支持（2026-08-17 新增）
# ============================================================
#
# 飞书 Card 是 JSON UI 模板：
#   {
#     "config":  {...},
#     "header":  {"template": "red", "title": {...}},
#     "elements": [
#       {"tag": "div",    "text": {"tag": "lark_md", "content": "..."}},
#       {"tag": "action", "actions": [{"tag": "button", ...}]}
#     ]
#   }
# 发送走 Gateway 的 ``POST /v1/messages/send``（msg_type="interactive"）。
# 网关后端需支持 ``msg_type != "text"``；当前网关硬编码 msg_type="text"，
# 因此卡片功能需配合网关后端升级启用。客户端 API 已就位。

# 默认卡片标题（防止 --title 缺省时空字符串）
DEFAULT_CARD_TITLE: str = "告警通知"
# alert_id 缺省时使用的 JSON key 哨兵（前端按此判断是否写入 value）
_ALERT_ID_KEY: str = "alert_id"


def parse_options(raw_options: Optional[List[str]]) -> List[Tuple[str, str]]:
    """把 CLI 传入的 ``--option "label:action"`` 字符串列表解析成 ``[(label, action), ...]``。

    解析规则：
        - 按首个 ``:`` 分割（``split(":", 1)``）
        - label 与 action 都不允许为空（trim 后）
        - 任何非法项抛 :class:`ValueError`，错误信息明确指出错误项

    Args:
        raw_options: 形如 ``["已知悉:ack", "立即处理:handle"]`` 的字符串列表；
            ``None`` 或 ``[]`` 都视作"无按钮"。

    Returns:
        ``[(label, action), ...]`` 元组列表。

    Raises:
        ValueError: 格式错误（不含 ``:`` / label 空 / action 空）。
    """
    if not raw_options:
        return []
    parsed: List[Tuple[str, str]] = []
    for raw in raw_options:
        item = (raw or "").strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(
                f"非法 option: {item!r}，正确格式应为 '显示文字:回调值'"
            )
        label, action = item.split(":", 1)
        label = label.strip()
        action = action.strip()
        if not label:
            raise ValueError(
                f"非法 option: {raw!r}，label（:前的显示文字）不能为空"
            )
        if not action:
            raise ValueError(
                f"非法 option: {raw!r}，action（:后的回调值）不能为空"
            )
        parsed.append((label, action))
    return parsed


def get_button_type(action: str) -> str:
    """按 action 把按钮样式分桶。

    飞书卡片按钮样式：
        - ``"primary"`` 主操作（蓝色）
        - ``"danger"``  危险操作（红色）
        - ``"default"`` 普通（默认灰）

    分桶规则（可按业务扩展）：
        - ``{"handle", "confirm", "execute"}`` → primary
        - ``{"cancel", "delete", "reject", "false_alarm"}`` → danger
        - 其它 → default
    """
    a = (action or "").strip().lower()
    if a in {"handle", "confirm", "execute"}:
        return "primary"
    if a in {"cancel", "delete", "reject", "false_alarm"}:
        return "danger"
    return "default"


def build_feishu_card(
    text: str,
    options: List[Tuple[str, str]],
    title: str = DEFAULT_CARD_TITLE,
    alert_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建飞书交互式卡片 JSON dict（不调用网络）。

    卡片结构（按飞书 Open API Card 规范）：
        - ``config.wide_screen_mode = True``
        - ``header.template = "red"``（告警主题）
        - ``header.title``: plain_text（默认 "告警通知"）
        - ``elements[0]``: ``div`` + ``lark_md``，正文 text
        - ``elements[1]``: ``action`` + 按钮（仅当 options 非空时追加）
        - 每个按钮 ``value``: ``{"action": "...", "alert_id": "..."}``；
          ``alert_id`` 缺省时**不**写入该字段

    Args:
        text: 卡片正文（飞书 ``lark_md``，支持 Markdown 语法）。
        options: ``[(label, action), ...]``，按 :func:`parse_options` 解析；
            空列表表示"纯展示卡片"，不生成按钮 action 元素。
        title: 卡片标题；空字符串回落到默认 "告警通知"。
        alert_id: 业务 ID；会写入每个按钮的 ``value``，缺省时省略。

    Returns:
        Card JSON dict（可直接 ``json.dumps(..., ensure_ascii=False)``）。

    Raises:
        ValueError: ``text`` 为空。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")

    # title 空白回落默认
    title = (title or "").strip() or DEFAULT_CARD_TITLE
    # alert_id 空白视为 None（不写入 value）
    has_alert_id = bool(alert_id and str(alert_id).strip())
    clean_alert_id = str(alert_id).strip() if has_alert_id else None

    elements: List[Dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": text,
            },
        },
    ]

    # 按钮区（仅当 options 非空时添加）
    if options:
        actions: List[Dict[str, Any]] = []
        for label, action in options:
            btn: Dict[str, Any] = {
                "tag": "button",
                "text": {
                    "tag": "plain_text",
                    "content": label,
                },
                "type": get_button_type(action),
                "value": {"action": action},
            }
            if has_alert_id:
                btn["value"][_ALERT_ID_KEY] = clean_alert_id
            actions.append(btn)
        elements.append({
            "tag": "action",
            "actions": actions,
        })

    card: Dict[str, Any] = {
        "config": {
            "wide_screen_mode": True,
        },
        "header": {
            "template": "red",
            "title": {
                "tag": "plain_text",
                "content": title,
            },
        },
        "elements": elements,
    }
    return card


def send_to_group_card(
    card: Dict[str, Any],
    *,
    chat_id: Optional[str] = None,
    group_name: Optional[str] = None,
    account_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> SendMessageResult:
    """发送飞书交互式卡片到指定群（走 Gateway，msg_type='interactive'）。

    支持两种群聊指定方式（**互斥，必填其一**）：
        1. ``chat_id`` 直传飞书群 ID（不读 GROUP_MAP）
        2. ``group_name`` 按 FEISHU_GROUP_MAP.name 反查 chat_id

    Args:
        card: 飞书 Card JSON dict（来自 :func:`build_feishu_card`）。
        chat_id: 飞书群 ID（与 ``group_name`` 互斥）。
        group_name: 群聊名称（与 ``chat_id`` 互斥）。
        account_id: Gateway 账号 ID（多账号机器人场景；不传走 channel_gateway_client 默认值）。
        idempotency_key: 幂等键；建议业务唯一（如 ``"p8-gas-20260817-001"``）。

    Returns:
        :class:`SendMessageResult`

    Raises:
        ValueError: ``card`` 为空 dict / 互斥校验失败 / 反查失败。
        GatewayError: 网关业务错误。
        requests.RequestException: 网络错误。
    """
    if not card or not isinstance(card, dict):
        raise ValueError("card 不能为空 dict")

    has_chat_id = bool(chat_id and chat_id.strip())
    has_group_name = bool(group_name and group_name.strip())
    if not has_chat_id and not has_group_name:
        raise ValueError("chat_id 和 group_name 必须传其中一个（互斥）")
    if has_chat_id and has_group_name:
        raise ValueError("chat_id 和 group_name 只能传一个（互斥）")

    if has_group_name:
        resolved_chat_id, found_name = _find_chat_id_by_group_name(group_name)
        log_tag = f"group_name={found_name} → chat_id={resolved_chat_id}"
    else:
        resolved_chat_id = chat_id.strip()
        log_tag = f"chat_id={resolved_chat_id}（直传，不走 GROUP_MAP）"

    # Card content 必须是 JSON 字符串（飞书 im/v1/messages 接口约定）
    content_str = json.dumps(card, ensure_ascii=False)

    logger.info(
        "[p8-notify] send_to_group_card 进入: %s card_elements=%d "
        "idempotency_key=%s",
        log_tag, len(card.get("elements", [])), idempotency_key,
    )
    try:
        # Gateway server 端要求 text 非空（即使走 msg_type=interactive）。
        # 这里把 card 内的 div.lark_md.content 作为 text 兜底（飞书侧不会被显示，
        # 只是过 Gateway 校验；真正发给用户的仍是 content 里的 Card 结构）。
        text_fallback = (card.get("elements", [{}])[0].get("text", {}).get("content", "")
                        if isinstance(card, dict) else "")
        result = send_message(
            text=text_fallback,
            channel=FEISHU_CHANNEL,
            conversation_id=resolved_chat_id,
            receive_id_type="chat_id",
            msg_type="interactive",
            content=content_str,
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        logger.exception(
            "[p8-notify] send_to_group_card 异常: %s err=%s",
            log_tag, exc,
        )
        raise

    logger.info(
        "[p8-notify] send_to_group_card 响应: %s status=%s "
        "intent_id=%s message_id=%s replayed=%s",
        log_tag, result.status, result.intent_id,
        result.platform_message_id, result.replayed,
    )
    return result


# ============================================================
# 3. CLI
# ============================================================

def _cli_send_group(args: argparse.Namespace) -> int:
    """send_group 子命令入口：根据 --card 决定走文本还是卡片。"""
    try:
        if args.card:
            # 卡片模式：先把 --option 解析成 [(label, action), ...]
            options = parse_options(args.option)
            card = build_feishu_card(
                text=args.text,
                options=options,
                title=args.title,
                alert_id=args.alert_id,
            )
            result = send_to_group_card(
                card=card,
                chat_id=args.chat_id,
                group_name=args.group_name,
                account_id=args.account_id,
            )
        else:
            # 纯文本模式（与重构前完全一致的行为）
            result = send_to_group(
                text=args.text,
                chat_id=args.chat_id,
                group_name=args.group_name,
                account_id=args.account_id,
            )
    except ValueError as exc:
        print(f"[FAIL] 参数错误: {exc}", file=sys.stderr)
        return 1
    except GatewayError as exc:
        print(f"[FAIL] Gateway 业务错误 [{exc.code}]: {exc.message}",
              file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(
        f"[OK] status={result.status} intent={result.intent_id} "
        f"message_id={result.platform_message_id} replayed={result.replayed} "
        f"msg_type={'interactive' if args.card else 'text'}"
    )
    return 0


def _cli_send_user(args: argparse.Namespace) -> int:
    try:
        result = send_to_user(
            text=args.text,
            name=args.name,
            open_id=args.open_id,
            account_id=args.account_id,
        )
    except ValueError as exc:
        print(f"[FAIL] 参数错误: {exc}", file=sys.stderr)
        return 1
    except GatewayError as exc:
        print(f"[FAIL] Gateway 业务错误 [{exc.code}]: {exc.message}",
              file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(
        f"[OK] status={result.status} intent={result.intent_id} "
        f"message_id={result.platform_message_id} replayed={result.replayed}"
    )
    return 0


def main(argv: Optional[list] = None) -> int:
    """CLI 入口。

    Usage:
        send_group --text "..." --chat-id oc_xxx
        send_user  --text "..." --name "张三"
    """
    parser = argparse.ArgumentParser(
        prog="feishu_sender",
        description="P8 飞书通道适配器（精简版）—— 只发，不拼。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # send_group
    p_grp = sub.add_parser(
        "send_group",
        help="群聊发送（按 chat_id 直传 或 按 group_name 反查 GROUP_MAP）",
    )
    p_grp.add_argument("--text", required=True, help="消息正文")
    grp = p_grp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--chat-id",
                     help="飞书群 ID（oc_xxx；直传，不读 GROUP_MAP）")
    grp.add_argument("--group-name",
                     help="群聊名称（按 GROUP_MAP.name 反查 chat_id）")
    p_grp.add_argument(
        "--account-id",
        default=None,
        help=(
            "Gateway 账号 ID（多账号机器人场景；不传走 channel_gateway_client 默认值）。"
            "当前 Gateway 配置示例：'P8'"
        ),
    )
    # 交互式 Card 相关选项（可选；不加 --card 则保持原有文本行为）
    p_grp.add_argument(
        "--card",
        action="store_true",
        help="以交互式 Card 形式发送（默认 False = 纯文本文本消息）",
    )
    p_grp.add_argument(
        "--title",
        default=DEFAULT_CARD_TITLE,
        help=f"卡片标题（仅 --card 时生效；默认 {DEFAULT_CARD_TITLE!r}）",
    )
    p_grp.add_argument(
        "--alert-id",
        default=None,
        help="业务告警 ID（仅 --card 时生效；会写入按钮 value，便于回调匹配）",
    )
    p_grp.add_argument(
        "--option",
        action="append",
        default=None,
        metavar="LABEL:ACTION",
        help=(
            "Card 按钮选项（可重复；格式 '显示文字:回调值'）。"
            "示例：--option '已知悉:ack' --option '立即处理:handle'。"
            "省略则卡片无按钮。"
        ),
    )
    p_grp.set_defaults(func=_cli_send_group)

    # send_user
    p_usr = sub.add_parser(
        "send_user",
        help="单聊发送（按 name 反查 USER_MAP.open_id，或直接传 open_id）",
    )
    p_usr.add_argument("--text", required=True, help="消息正文")
    grp = p_usr.add_mutually_exclusive_group(required=True)
    grp.add_argument("--name",
                     help="中文姓名（按 USER_MAP.name 反查）")
    grp.add_argument("--open-id",
                     help="飞书用户 open_id（直接使用，跳过 USER_MAP）")
    p_usr.add_argument(
        "--account-id",
        default=None,
        help="Gateway 账号 ID（多账号机器人场景；不传走默认）",
    )
    p_usr.set_defaults(func=_cli_send_user)

    args = parser.parse_args(argv)
    return args.func(args)


# ============================================================
# 4. 导出
# ============================================================

__all__ = [
    # 常量
    "FEISHU_USER_MAP_KEY",
    "FEISHU_GROUP_MAP_KEY",
    "FEISHU_CHANNEL",
    "DEFAULT_CARD_TITLE",
    # 公开 API
    "send_to_group",
    "send_to_user",
    # 卡片 API
    "parse_options",
    "get_button_type",
    "build_feishu_card",
    "send_to_group_card",
    # CLI
    "main",
]


if __name__ == "__main__":
    sys.exit(main())