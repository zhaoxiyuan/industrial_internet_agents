"""
OpenClaw Channel Gateway - REST 轮询模式接收飞书消息
=====================================================

功能：
- 轮询 Channel Gateway 的 /v1/events 接口
- 接收并打印标准化后的飞书事件消息

依赖：pip install python-dotenv requests

--------------------------------------------------------------------------------
重要前提：公网可达性
--------------------------------------------------------------------------------

本轮询客户端依赖 Channel Gateway 的 /v1/events REST 接口，
因此 Gateway 必须能被飞书服务器访问到。

本地运行（127.0.0.1）的限制：
- 只有本机可以访问 Gateway
- 飞书服务器无法将 Webhook 事件推送过来
- 轮询会一直返回空事件

解决方案（二选一）：

  方案 A：使用 ngrok 将本地端口暴露到公网（快速测试）
    1. 安装 ngrok 并注册
    2. 运行：ngrok http 8787
    3. 复制生成的公网 URL（如 https://abc123.ngrok.io）
    4. 在飞书开放平台配置 Webhook 订阅 URL：
       https://abc123.ngrok.io/webhooks/feishu/default

  方案 B：部署到公网服务器（生产环境）
    将整个项目部署到有公网 IP 的服务器（阿里云、腾讯云等），
    然后在飞书配置对应的公网 URL。

--------------------------------------------------------------------------------
快速开始
--------------------------------------------------------------------------------

1. 安装依赖：
   pip install python-dotenv requests

2. 确保 Channel Gateway 已启动：
   node start.mjs --config config/config.feishu.local.json

3. 确保 Gateway 公网可达（见上文"重要前提"）

4. 运行本脚本：
   python polling_test.py

--------------------------------------------------------------------------------
REST 轮询接口说明
--------------------------------------------------------------------------------
- 轮询地址：GET http://127.0.0.1:8787/v1/events?after_sequence=0&limit=100
- 认证方式：Authorization: Bearer <CHANNEL_GATEWAY_API_KEY>
- 返回内容：标准化后的事件列表

响应格式：
  {
    "events": [
      {
        "id": "evt_xxx",
        "sequence": 1,
        "session": { "key": "cg:v1:feishu:..." },
        "message": { "type": "text", "text": "用户消息" },
        "sender": { "id": "ou_xxx", "type": "user", "name": "张三" },
        "conversation": { "id": "oc_xxx", "type": "direct" },
        "occurredAt": "2026-08-12T10:00:00.000Z",
        ...
      },
      ...
    ],
    "latestSequence": 42
  }

轮询策略：
- after_sequence: 已接收的最大 sequence，下次轮询传入 lastSequence+1
  （传入 0 表示获取所有未处理事件，通常用于首次启动）
- limit: 每次最多返回事件数，默认 100
- 无新事件时返回空 events 数组

--------------------------------------------------------------------------------
"""

import os
import json
import time
import requests
from dotenv import load_dotenv


# ============================================================
# 1. 配置加载
# ============================================================

load_dotenv()

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "http://127.0.0.1:8787")
GATEWAY_API_KEY = os.getenv("CG_API_KEY", "")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1"))  # 轮询间隔（秒）
BATCH_SIZE = int(os.getenv("POLL_BATCH_SIZE", "100"))  # 每次轮询获取的最大事件数


# ============================================================
# 2. REST 轮询获取事件
# ============================================================

def poll_events(after_sequence: int = 0, limit: int = 100) -> tuple[list[dict], int]:
    """
    从 Channel Gateway 轮询事件

    Args:
        after_sequence: 只获取 sequence > after_sequence 的事件
                       首次调用传 0 获取所有未处理事件
        limit: 每次最多返回的事件数

    Returns:
        (events, latest_sequence) 元组
        - events: 事件列表
        - latest_sequence: 本次返回中最大的 sequence（可用于下次轮询）

    Raises:
        RuntimeError: 请求失败或返回错误时
    """
    if not GATEWAY_API_KEY:
        raise RuntimeError(
            "请在 .env 中配置 CHANNEL_GATEWAY_API_KEY（或 CG_API_KEY）\n"
            "  CG_API_KEY=gateway-api-key-16chars!"
        )

    url = f"{GATEWAY_HOST}/v1/events"
    params = {
        "after_sequence": after_sequence,
        "limit": limit,
    }
    headers = {
        "Authorization": f"Bearer {GATEWAY_API_KEY}",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"轮询请求失败: {e}")

    data = response.json()

    # 检查业务错误
    if data.get("error"):
        raise RuntimeError(f"Gateway 返回错误: {data}")

    events = data.get("events", [])
    latest_seq = data.get("latestSequence", after_sequence)

    return events, latest_seq


def format_event(event: dict) -> str:
    """
    将事件格式化为易读的字符串

    Args:
        event: 标准化后的事件对象

    Returns:
        格式化的字符串
    """
    msg_type = event.get("message", {}).get("type", "unknown")
    msg_text = event.get("message", {}).get("text", "")
    sender_name = event.get("sender", {}).get("name", "未知用户")
    sender_id = event.get("sender", {}).get("id", "N/A")
    session_key = event.get("session", {}).get("key", "N/A")
    conv_type = event.get("conversation", {}).get("type", "N/A")
    conv_id = event.get("conversation", {}).get("id", "N/A")
    occurred_at = event.get("occurredAt", "N/A")
    event_id = event.get("id", "N/A")

    lines = [
        f"─" * 60,
        f"事件ID  : {event_id}",
        f"时间    : {occurred_at}",
        f"会话键  : {session_key}",
        f"会话类型: {conv_type} ({conv_id})",
        f"发送者  : {sender_name} ({sender_id})",
        f"消息类型: {msg_type}",
        f"消息内容: {msg_text}",
        f"原始数据: {json.dumps(event, ensure_ascii=False, indent=2)}",
        f"─" * 60,
    ]
    return "\n".join(lines)


# ============================================================
# 3. 轮询循环
# ============================================================

def run_polling_loop(initial_sequence: int = 0):
    """
    启动轮询循环，持续获取并打印事件

    Args:
        initial_sequence: 起始 sequence（传 0 获取所有未处理事件）
    """
    current_sequence = initial_sequence
    consecutive_empty = 0  # 连续空轮询次数

    print("=" * 60)
    print("Channel Gateway 轮询模式 - 启动")
    print("=" * 60)
    print(f"Gateway 地址 : {GATEWAY_HOST}")
    print(f"轮询间隔    : {POLL_INTERVAL} 秒")
    print(f"批次大小    : {BATCH_SIZE}")
    print(f"起始序列    : {initial_sequence}")
    print("=" * 60)
    print()
    print("等待消息事件...")
    print()

    while True:
        try:
            events, latest_seq = poll_events(
                after_sequence=current_sequence,
                limit=BATCH_SIZE,
            )

            if events:
                consecutive_empty = 0
                # 更新当前序列为返回中最大的 sequence
                current_sequence = max(
                    (e.get("sequence", current_sequence) for e in events),
                    default=current_sequence,
                )
                # 或者直接用 latest_seq（更准确）
                # current_sequence = latest_seq

                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                      f"收到 {len(events)} 个事件:")
                print()

                for event in events:
                    print(format_event(event))
                    print()

                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                      f"已处理 {len(events)} 个事件，下一序列: {current_sequence}")
                print()

            else:
                consecutive_empty += 1
                if consecutive_empty <= 3 or consecutive_empty % 10 == 0:
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                          f"无新事件（已连续 {consecutive_empty} 次空轮询）")

        except RuntimeError as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"[错误] {e}")
            print()

        except KeyboardInterrupt:
            print()
            print("=" * 60)
            print("收到中断信号，退出轮询")
            print("=" * 60)
            break

        # 无新事件时使用配置的间隔，有新事件时短暂休息避免 busy loop
        sleep_time = POLL_INTERVAL if consecutive_empty > 0 else 0.5
        time.sleep(sleep_time)


# ============================================================
# 4. 获取初始 sequence
# ============================================================

def get_initial_sequence() -> int:
    """
    尝试从 Gateway 获取当前最大 sequence

    Returns:
        当前最大的 sequence（如果获取失败返回 0）
    """
    try:
        # 传入一个很大的 after_sequence，Gateway 会返回空列表但包含 latestSequence
        _, latest = poll_events(after_sequence=999999999, limit=1)
        return latest if latest < 999999999 else 0
    except Exception:
        return 0


# ============================================================
# 5. 主入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Channel Gateway REST 轮询客户端")
    print("=" * 60)
    print()

    # -------------------- 凭证检查 --------------------
    if not GATEWAY_API_KEY:
        print("[错误] 未找到 Gateway API Key")
        print()
        print("请在 .env 中配置：")
        print("  CG_API_KEY=gateway-api-key-16chars!")
        print()
        print("或者设置环境变量：")
        print("  export CG_API_KEY=gateway-api-key-16chars!  # Linux/macOS")
        print("  set CG_API_KEY=gateway-api-key-16chars!    # Windows CMD")
        print("  $env:CG_API_KEY='gateway-api-key-16chars!'  # Windows PowerShell")
        exit(1)

    print(f"[配置] Gateway: {GATEWAY_HOST}")
    print(f"[配置] API Key: {'*' * len(GATEWAY_API_KEY) if len(GATEWAY_API_KEY) > 8 else '***'}")
    print(f"[配置] 轮询间隔: {POLL_INTERVAL}s")
    print()

    # -------------------- 获取初始序列号 --------------------
    print("[*] 尝试获取当前最大序列号...")
    init_seq = get_initial_sequence()
    print(f"    当前最大序列号: {init_seq}")
    print()

    # -------------------- 启动轮询 --------------------
    run_polling_loop(initial_sequence=init_seq)
