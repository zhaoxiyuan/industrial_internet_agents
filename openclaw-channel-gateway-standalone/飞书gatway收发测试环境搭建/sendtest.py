"""
OpenClaw Channel Gateway Standalone - Feishu Agent (Python)
===========================================================

功能：
- 从 .env 加载飞书凭证
- 获取 / 缓存 tenant_access_token
- 发送文本消息（单聊 / 群聊 / 回复）
- 定义智能体框架（handle_inbound 待扩展）

依赖：pip install python-dotenv requests

--------------------------------------------------------------------------------
快速开始
--------------------------------------------------------------------------------

1. 安装依赖：
   pip install python-dotenv requests

2. 配置 .env（参考 .env.example）：
   FEISHU_APP_ID=cli_xxxxxxxxxxxxx
   FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   FEISHU_DOMAIN=feishu      # feishu | lark（国际版）

3. 获取 receive_id：
   - 单聊：receive_id_type = "open_id"，receive_id = 对方的 open_id
   - 群聊：receive_id_type = "chat_id"，receive_id = 群的 chat_id
   - 回复：receive_id_type = "open_id" / "chat_id"，reply_to_id = 要回复的消息 ID

   获取 open_id 的方法：
   - 让目标用户给 Bot 发一条消息，Bot 日志中会打印 sender.open_id
   - 或在飞书开放平台 → 事件与回调 → 查看消息事件的请求日志

4. 运行：
   python agent.py

--------------------------------------------------------------------------------
飞书开放平台文档
--------------------------------------------------------------------------------
- 发送消息：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/im-v1/message/create
- 获取 Token：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/auth-v3/tenant_access_token/internal
- 消息类型：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/im-v1/message/create#6368a936

--------------------------------------------------------------------------------
ID 类型说明
--------------------------------------------------------------------------------
| ID 类型       | 格式                      | 用途                        |
|---------------|---------------------------|-----------------------------|
| open_id       | ou_xxx                    | 用户的唯一标识，单聊时使用    |
| chat_id       | oc_xxx                    | 群会话标识，群聊时使用        |
| message_id    | om_xxx                    | 消息唯一 ID，用于回复        |
| tenant_key    | 企业标识                   | 多租户场景使用               |

receive_id_type 对照：
- 单聊（p2p）：receive_id_type = "open_id"，receive_id = 对方 open_id
- 群聊（group）：receive_id_type = "chat_id"，receive_id = 群 chat_id
- 回复消息：reply_to_id = 要回复的消息 message_id（om_xxx）

--------------------------------------------------------------------------------
"""

import os
import json
import time
import requests
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 1. 配置加载
# ============================================================

load_dotenv()

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "feishu")  # "feishu" | "lark"


# ============================================================
# 2. 飞书 API 基础
# ============================================================

def feishu_domain_base(domain: str = "feishu") -> str:
    """
    返回飞书 API 域名

    Args:
        domain: "feishu"（中国站）或 "lark"（国际版）

    Returns:
        API 基础 URL
    """
    if domain == "lark":
        return "https://open.larksuite.com"
    return "https://open.feishu.cn"


def get_tenant_access_token(
    app_id: str,
    app_secret: str,
    domain: str = "feishu",
) -> str:
    """
    获取 tenant_access_token（企业自建应用用此 Token）

    文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/auth-v3/tenant_access_token/internal

    注意：
    - Token 有效期 2 小时，需要自行缓存
    - 失败时飞书返回 code != 0，需清理缓存重新获取

    Args:
        app_id: 飞书应用的 App ID（cli_xxx）
        app_secret: 飞书应用的 App Secret
        domain: "feishu" | "lark"

    Returns:
        tenant_access_token 字符串

    Raises:
        RuntimeError: 当 app_id/app_secret 无效，或飞书返回错误时
    """
    base = feishu_domain_base(domain)
    url = f"{base}/open-apis/auth/v3/tenant_access_token/internal"

    response = requests.post(
        url,
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

    return data["tenant_access_token"]


def send_text_message(
    token: str,
    receive_id: str,
    text: str,
    domain: str = "feishu",
    receive_id_type: str = "open_id",
    reply_to_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """
    发送文本消息

    支持三种模式：
    1. 发送给用户/群（receive_id）
    2. 回复指定消息（reply_to_id）
    3. 在线程中回复（reply_to_id + thread_id）

    文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/im-v1/message/create

    Args:
        token: tenant_access_token
        receive_id: 接收方 ID（open_id / chat_id / union_id / user_id）
        text: 消息文本内容
        domain: "feishu" | "lark"
        receive_id_type: 接收方 ID 类型，可选：
            - "open_id"    （单聊，推荐）
            - "chat_id"    （群聊）
            - "union_id"   （跨应用用户）
            - "user_id"    （应用内用户）
        reply_to_id: 要回复的消息 ID（message_id，om_xxx），设为则触发回复模式
        thread_id: 线程 ID，reply_to_id + thread_id 会在线程中回复

    Returns:
        飞书返回的 data 字段，通常包含 message_id

    Raises:
        RuntimeError: 飞书返回 code != 0 时
    """
    base = feishu_domain_base(domain)
    content = json.dumps({"text": text})

    # 回复模式
    if reply_to_id:
        # 回复消息：POST /open-apis/im/v1/messages/{reply_to_id}/reply
        url = f"{base}/open-apis/im/v1/messages/{reply_to_id}/reply"
        body = {"msg_type": "text", "content": content}
        if thread_id:
            body["reply_in_thread"] = True
    else:
        # 主动发送：POST /open-apis/im/v1/messages?receive_id_type=xxx
        url = f"{base}/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        body = {"receive_id": receive_id, "msg_type": "text", "content": content}

    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
    }

    response = requests.post(url, headers=headers, json=body, timeout=15)
    response.raise_for_status()
    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(f"发送消息失败: {data}")

    return data.get("data", {})


# ============================================================
# 3. 智能体定义
# ============================================================

@dataclass
class AgentConfig:
    """飞书智能体配置"""
    name: str = "feishu-agent"
    model: str = "claude"
    callback_url: Optional[str] = None  # Agent 回调地址（当作为 Channel Gateway 的下游时）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_domain: str = "feishu"


@dataclass
class FeishuAgent:
    """
    飞书智能体

    封装：
    - tenant_access_token 自动获取与缓存（2 小时有效期）
    - 文本消息发送
    - 入站事件处理（handle_inbound 待扩展）

    使用示例：
        agent = FeishuAgent(
            feishu_app_id="cli_xxx",
            feishu_app_secret="xxx",
        )

        # 发送消息
        result = agent.send_text(
            receive_id="ou_xxx",      # open_id（单聊）或 chat_id（群聊）
            text="你好！",
            receive_id_type="open_id", # 单聊用 open_id
        )
        print(result["message_id"])

        # 回复消息
        agent.send_text(
            receive_id="ou_xxx",
            text="这是回复",
            reply_to_id="om_xxx",      # 要回复的消息 ID
        )
    """
    config: AgentConfig
    _token: Optional[str] = field(default=None, init=False, repr=False)
    _token_expires_at: float = field(default=0, init=False, repr=False)

    def ensure_token(self) -> str:
        """
        获取有效的 tenant_access_token

        内部维护缓存，在过期前 60 秒自动刷新
        """
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        self._token = get_tenant_access_token(
            self.config.feishu_app_id,
            self.config.feishu_app_secret,
            self.config.feishu_domain,
        )
        # 飞书 token 有效期 2 小时（7200 秒）
        self._token_expires_at = time.time() + 7200
        return self._token

    def send_text(
        self,
        receive_id: str,
        text: str,
        receive_id_type: str = "open_id",
        reply_to_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        """
        发送文本消息

        详见 send_text_message 文档
        """
        token = self.ensure_token()
        return send_text_message(
            token=token,
            receive_id=receive_id,
            text=text,
            domain=self.config.feishu_domain,
            receive_id_type=receive_id_type,
            reply_to_id=reply_to_id,
            thread_id=thread_id,
        )

    def handle_inbound(self, event: dict) -> dict:
        """
        处理入站事件（Channel Gateway 回调）

        当作为 Channel Gateway 的下游 Agent 时，此方法会被网关调用。
        目前仅打印日志，业务逻辑需根据场景扩展。

        Args:
            event: 网关标准化后的事件对象，结构如下：
                {
                  "id": "evt_xxx",
                  "sequence": 1,
                  "session": { "key": "cg:v1:feishu:..." },
                  "message": { "type": "text", "text": "用户消息" },
                  "sender": { "id": "ou_xxx", "type": "user", "name": "张三" },
                  "conversation": { "id": "oc_xxx", "type": "direct" },
                  ...
                }

        Returns:
            响应对象：
                {
                  "ack": True,                          # 表示已处理
                  "messages": [                         # 可选：要发送的消息列表
                    { "text": "Agent 回复" }
                  ]
                }
        """
        print(f"[FeishuAgent] 收到事件:")
        print(f"  session.key : {event.get('session', {}).get('key')}")
        print(f"  sender      : {event.get('sender', {}).get('name')} ({event.get('sender', {}).get('id')})")
        print(f"  message     : {event.get('message', {}).get('text')}")
        print(f"  conversation: {event.get('conversation', {}).get('id')} ({event.get('conversation', {}).get('type')})")
        return {"ack": True, "messages": []}


# ============================================================
# 4. 工厂函数
# ============================================================

def create_feishu_agent() -> FeishuAgent:
    """
    从环境变量创建飞书智能体

    环境变量：
        FEISHU_APP_ID     - 飞书 App ID
        FEISHU_APP_SECRET - 飞书 App Secret
        FEISHU_DOMAIN     - "feishu"（中国站）或 "lark"（国际版）

    异常：
        RuntimeError: 凭证未配置时抛出
    """
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        raise RuntimeError(
            "请在 .env 中配置飞书凭证：\n"
            "  FEISHU_APP_ID=cli_xxxxxxxxxxxxx\n"
            "  FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
            "  FEISHU_DOMAIN=feishu  # 或 lark"
        )

    config = AgentConfig(
        feishu_app_id=FEISHU_APP_ID,
        feishu_app_secret=FEISHU_APP_SECRET,
        feishu_domain=FEISHU_DOMAIN,
    )
    return FeishuAgent(config=config)


# ============================================================
# 5. 测试代码
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("飞书智能体 - 发送测试消息")
    print("=" * 60)

    # -------------------- 凭证检查 --------------------
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("[错误] 未找到飞书凭证")
        print()
        print("请在 .env 中配置：")
        print("  FEISHU_APP_ID=cli_xxxxxxxxxxxxx")
        print("  FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        print("  FEISHU_DOMAIN=feishu  # 或 lark")
        exit(1)

    print(f"App ID : {FEISHU_APP_ID}")
    print(f"Domain : {FEISHU_DOMAIN}")
    print()

    # -------------------- 创建 Agent --------------------
    agent = create_feishu_agent()

    # -------------------- 获取 Token --------------------
    print("[1] 获取 tenant_access_token ...")
    token = agent.ensure_token()
    print(f"    [OK] token 获取成功，长度: {len(token)}")
    print()

    # -------------------- 发送消息 --------------------
    # ---------------------------------------------------------------
    # 重要：修改以下两个变量为你的目标用户
    # ---------------------------------------------------------------
    #
    # 获取 receive_id 的方法：
    #
    #   单聊（p2p）：receive_id_type = "open_id"
    #     - 让用户给 Bot 发一条消息，在 Bot 日志中找 sender.open_id
    #     - 格式：ou_xxx
    #
    #   群聊（group）：receive_id_type = "chat_id"
    #     - 在飞书群设置中查看群 ID
    #     - 格式：oc_xxx
    #
    #   回复消息：额外传 reply_to_id（message_id，om_xxx）
    #     - 从消息事件中获取 message_id
    #     - reply_to_id = 要回复的那条消息的 message_id
    # ---------------------------------------------------------------

    TEST_RECEIVE_ID = "ou_511d109b8ac87f972af2d5e67e2c8270"   # TODO: 替换为你的 open_id / chat_id
    TEST_RECEIVE_TYPE = "open_id"                              # open_id（单聊）/ chat_id（群聊）
    TEST_TEXT = "你好！这是一条来自 Python Feishu Agent 的测试消息。"

    print("[2] 发送文本消息 ...")
    print(f"    receive_id : {TEST_RECEIVE_ID}")
    print(f"    receive_type: {TEST_RECEIVE_TYPE}")
    print(f"    text        : {TEST_TEXT}")
    print()

    try:
        result = agent.send_text(
            receive_id=TEST_RECEIVE_ID,
            text=TEST_TEXT,
            receive_id_type=TEST_RECEIVE_TYPE,
        )
        message_id = result.get("message_id") or result.get("data", {}).get("message_id", "N/A")
        print(f"    [OK] 发送成功！message_id: {message_id}")
    except Exception as e:
        print(f"    [错误] {e}")
