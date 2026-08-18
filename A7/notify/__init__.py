"""A7.notify — 飞书通道适配层（精简版：只发，不拼）。

================================================================================
职责（精简版）
================================================================================

只做一件事：**把调用方准备好的消息正文发到指定位置**。

- 不做消息拼装（标题 / 模板 / 风险等级前缀）  → 由调用方完成
- 不做收件人解析（assignee_role → user_map）  → 由调用方完成
- 不做多收件人聚合 / 失败聚合                  → 由调用方完成
- 不做重试 / 幂等策略                          → 由 Gateway / 调用方完成

================================================================================
调用关系
================================================================================

    P8_agent.notify_feishu(p8_job_id, message)   ← 未来工具（自行拼装消息正文）
            │
            ▼
    A7.notify.send_to_group(text, *, chat_id="...", group_name="...")   ← 群聊（chat_id 或 group_name，二选一）
    A7.notify.send_to_user(text, *, name="...", open_id="...")          ← 单聊（name 或 open_id，二选一）
            │
            ▼
    agents.channel_gateway_client.send_message(...)
            │
            ▼
    OpenClaw Channel Gateway Standalone (127.0.0.1:8787)
            │
            ▼
    Feishu Open API

================================================================================
公开 API（re-export）
================================================================================

    send_to_group(text, *, chat_id=None, group_name=None)
                                                群聊发送；chat_id 直传 或 group_name
                                                反查 GROUP_MAP（互斥必填其一）；
                                                返回 SendMessageResult
    send_to_user(text, *, name=None, open_id=None)
                                                单聊发送；name 反查 USER_MAP.open_id
                                                 或直接传 open_id（互斥必填其一）；
                                                 返回 SendMessageResult

================================================================================
收件人配置（FEISHU_USER_MAP，2026-08-13 模型）
================================================================================

环境变量 ``FEISHU_USER_MAP``（JSON 字符串）：

::

    FEISHU_USER_MAP='{
      "ou_alice_xxx": {"chat_id": "oc_xxx_1", "role": "作业负责人", "name": "张三"},
      "ou_bob_yyy":   {"chat_id": "",         "role": "作业负责人", "name": "李四"}
    }'

- **主键**：open_id（飞书用户唯一 ID；dict 的 key）
- **chat_id**：群聊 ID；空 → 仅配单聊 / 非空 → 可走群发
- **role**：责任岗位（P8 业务层用，本适配层不读）
- **name**：中文姓名（send_to_user 按 name 反查时使用）

调用关系：
    - send_to_group(text, *, chat_id="oc_xxx") 直接用 chat_id 发群
    - send_to_group(text, *, group_name="应急响应群") 按 name 反查 GROUP_MAP 取 chat_id
    - chat_id 与 group_name 互斥必填其一
    - send_to_user(text, *, name="张三") 按 name 反查 USER_MAP 取 open_id
    - send_to_user(text, *, open_id="ou_xxx") 直传 open_id（webhook 回调场景）
    - name 与 open_id 互斥必填其一；单聊始终走 open_id

================================================================================
群聊元数据（FEISHU_GROUP_MAP，2026-08-17 模型）
================================================================================

环境变量 ``FEISHU_GROUP_MAP``（JSON 字符串）— 主键 chat_id，与 USER_MAP 解耦：

::

    FEISHU_GROUP_MAP='{
      "oc_xxx1": {"name": "应急响应群", "description": "P8 告警群"},
      "oc_xxx2": {"name": "日常巡检群", "description": ""}
    }'

- **主键**：chat_id（飞书群 ID）
- **name**：群聊名称（必填；send_to_group 按 name 反查时使用）
- **description**：群聊描述（非必填，可空）

调用关系：
    - send_to_group 按 chat_id 直传 → 不读 GROUP_MAP
    - send_to_group 按 group_name 反查 → 走 GROUP_MAP 取 chat_id
    - USER_MAP 多个 user 共享同一 chat_id 时，名称/描述只需配一次
      （GROUP_MAP 维护同一份元数据，避免 USER_MAP 行间数据冗余/不同步）

旧变量 ``FEISHU_CONVERSATION_MAP`` / ``FEISHU_GROUP_<ROLE>`` 已废弃；
``feishu_config_app`` 每次保存会自动清理。

================================================================================
CLI 命令
================================================================================

参见 ``A7/notify/feishu_sender.py`` 文件头 docstring：

    python -m A7.notify.feishu_sender send_group --text "..." --chat-id oc_xxx
    python -m A7.notify.feishu_sender send_user  --text "..." --name "张三"

================================================================================
配置 UI（feishu_config_app）
================================================================================

``A7/notify/feishu_config_app`` 是独立的 Flask + HTML 配置面板，让业务 / 运维
人员通过浏览器把上述环境变量（``GATEWAY_HOST`` / ``CG_API_KEY`` /
``FEISHU_USER_MAP`` / ``FEISHU_APP_*`` 等）写入 **项目根 .env**，
避免手工编辑出错。

启动：

    python A7/notify/feishu_config_app.py            # 默认 127.0.0.1:5003
    python A7/notify/feishu_config_app.py --port 5003

URL：``http://127.0.0.1:5003/``。

包含两个模块：

1. **表单写入**（§ 7.3.2）— 在 UI 表单填字段 → 保存到 .env
2. **实时监听**（§ 7.3.7）— 启动后台轮询 Gateway，用户在飞书群/单聊里发条消息，
   UI 自动捕获 chat_id / open_id 并一键写入 FEISHU_USER_MAP

注意：本 UI 不属于 P8 主流程；仅供配置期使用。运行时仍由
``channel_gateway_client`` + ``feishu_sender`` 直接读 .env。
"""
# --------------------------------------------------------------------------------
# 公开符号（lazy re-export，PEP 562）
# --------------------------------------------------------------------------------
#
# 用 __getattr__ 而非 eager import，目的是避免与 `python -m A7.notify.feishu_sender`
# 冲突（eager import 会把模块塞进 sys.modules，runpy 跳过后会打 RuntimeWarning）。
# 短路径 `from A7.notify import send_to_group` 仍然可用，只是首次访问时按需加载。

_LAZY_SYMBOLS = {
    "send_to_group",
    "send_to_user",
    "FEISHU_CHANNEL",
    "FEISHU_USER_MAP_KEY",
    "FEISHU_GROUP_MAP_KEY",
}


def __getattr__(name: str):
    if name in _LAZY_SYMBOLS:
        from . import feishu_sender as _adapter
        return getattr(_adapter, name)
    raise AttributeError(f"module 'A7.notify' has no attribute {name!r}")


# feishu_config_app 是独立 Flask 入口；不在 __all__ 中暴露避免与运行时 API 混淆。
# 调用方式：python -m A7.notify.feishu_config_app
try:
    from . import feishu_config_app  # noqa: F401  （可选；缺失不影响主流程）
except Exception:  # pragma: no cover
    feishu_config_app = None  # type: ignore[assignment]

__all__ = [
    # 公开 API（精简版）
    "send_to_group",
    "send_to_user",
    # 业务常量
    "FEISHU_CHANNEL",
    "FEISHU_USER_MAP_KEY",
    "FEISHU_GROUP_MAP_KEY",
]