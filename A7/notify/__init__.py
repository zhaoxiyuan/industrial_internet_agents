"""A7.notify — P8 通道适配层（Feishu adapter for P8 disposition notifications）。

================================================================================
职责
================================================================================

把 P8 业务语义（P8_job / PushMessage / assignee_role / idempotency）翻译成飞书
通道调用，并通过 OpenClaw Channel Gateway 发出。

仅在 channel=PUSH 路径使用；channel=HITL 走人工中断，不调本层。

================================================================================
调用关系
================================================================================

    P8_agent.notify_feishu(p8_job_id, message)   ← 未来工具
            │
            ▼
    A7.notify.send_feishu(p8_job, job_id)
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

高层：
    send_feishu(p8_job, job_id)            推送主入口；返回 FeishuNotifyResult
    get_conversation_id(assignee_role)     把责任岗位解析为飞书 conversation_id（向后兼容，返回首个匹配）
    resolve_recipients(assignee_role)      把责任岗位解析为收件人列表（USER_MAP 多对一）
    get_user_map()                         直接读取 FEISHU_USER_MAP（原始 dict）

数据类：
    FeishuNotifyResult                     推送结果（status / message_id / error / recipients / ...）
    RecipientInfo                          单个收件人信息（open_id, chat_id, name, role, receive_id_type）
    RecipientStatus                        单个收件人推送结果（status / message_id / error）

异常：
    FeishuNotifyError                      基础异常
    FeishuNotifyConfigurationError         配置缺失（strict=True 时）

业务常量：
    RISK_DEFAULT_ASSIGNEE                  RiskLevel → 默认责任岗位
    RISK_PUSH_PREFIX                       RiskLevel → 飞书消息前缀（含 emoji）
    RISK_RESPONSE_TIME_HOURS               RiskLevel → 响应时限（小时）
    SEPARATOR                              飞书消息正文分隔线
    FEISHU_CHANNEL                         飞书通道名（适配器固定）
    DEFAULT_RECEIVE_ID_TYPE                飞书 receive_id_type 默认值（chat_id）
    DEV_PLACEHOLDER_PREFIX                 开发 / Sandbox 占位符前缀
    FEISHU_USER_MAP_KEY                    环境变量名（"FEISHU_USER_MAP"）

================================================================================
收件人配置（FEISHU_USER_MAP，2026-08-13 重构）
================================================================================

按 USER_MAP 数据模型解析：

环境变量 ``FEISHU_USER_MAP``（JSON 字符串）：
::

    FEISHU_USER_MAP='{
      "ou_alice_xxx": {"chat_id": "oc_xxx_1", "role": "作业负责人", "name": "张三"},
      "ou_bob_yyy":   {"chat_id": "",         "role": "作业负责人", "name": "李四"}
    }'

- **主键**：open_id（飞书用户唯一 ID）
- **chat_id**：群聊 ID；空 → 走 open_id 单聊
- **role**：责任岗位（与 P8Job.assignee_role 匹配）
- **name**：中文姓名（手动填；人只记得自己叫什么）
- **同一 role 可对应多人** → P8 推送时群发

旧变量 ``FEISHU_CONVERSATION_MAP`` / ``FEISHU_GROUP_<ROLE>`` 已废弃；
新 UI 每次保存会自动清理。

================================================================================
失败语义（与 docs § 8.3 对齐）
================================================================================

飞书推送失败 → 仅记日志 + 返回 ``FeishuNotifyResult(status="failed", ...)``；
**绝不抛异常**。LLM 看到 ``status="failed"`` 后自行决定是否重试或换通道。

================================================================================
配置 UI（feishu_config_app，docs § 7.3）
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
``channel_gateway_client`` + ``p8_feishu_adapter`` 直接读 .env。
"""
from .p8_feishu_adapter import (
    # 公开 API
    send_feishu,
    get_conversation_id,
    resolve_recipients,
    get_user_map,
    # 数据类
    FeishuNotifyResult,
    RecipientInfo,
    RecipientStatus,
    # 异常
    FeishuNotifyError,
    FeishuNotifyConfigurationError,
    # 业务常量
    RISK_DEFAULT_ASSIGNEE,
    RISK_PUSH_PREFIX,
    RISK_RESPONSE_TIME_HOURS,
    SEPARATOR,
    FEISHU_CHANNEL,
    DEFAULT_RECEIVE_ID_TYPE,
    DEV_PLACEHOLDER_PREFIX,
    FEISHU_USER_MAP_KEY,
)

# feishu_config_app 是独立 Flask 入口；不在 __all__ 中暴露避免与运行时 API 混淆。
# 调用方式：python -m A7.notify.feishu_config_app
try:
    from . import feishu_config_app  # noqa: F401  （可选；缺失不影响主流程）
except Exception:  # pragma: no cover
    feishu_config_app = None  # type: ignore[assignment]

__all__ = [
    # 公开 API
    "send_feishu",
    "get_conversation_id",
    "resolve_recipients",
    "get_user_map",
    # 数据类
    "FeishuNotifyResult",
    "RecipientInfo",
    "RecipientStatus",
    # 异常
    "FeishuNotifyError",
    "FeishuNotifyConfigurationError",
    # 业务常量
    "RISK_DEFAULT_ASSIGNEE",
    "RISK_PUSH_PREFIX",
    "RISK_RESPONSE_TIME_HOURS",
    "SEPARATOR",
    "FEISHU_CHANNEL",
    "DEFAULT_RECEIVE_ID_TYPE",
    "DEV_PLACEHOLDER_PREFIX",
    "FEISHU_USER_MAP_KEY",
]