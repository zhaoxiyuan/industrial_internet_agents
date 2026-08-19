"""一次性探测 cardkit + im(card_id) 链路是否可用。

按 docs/飞书卡片教程/创建卡片 body 格式：
  {"type": "card_json", "data": "<Card 2.0 JSON 字符串>"}  (FLAT)
"""
import os, sys, json, requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

APP_ID = os.environ["FEISHU_P8_APP_ID"]
APP_SECRET = os.environ["FEISHU_P8_APP_SECRET"]
DOMAIN = os.environ.get("FEISHU_P8_DOMAIN", "feishu")
BASE = "https://open.feishu.cn" if DOMAIN == "feishu" else f"https://open.{DOMAIN}.cn"
CHAT_ID = "oc_d10e7b407369327a538c1204f7817499"

# 1. tenant_access_token
r = requests.post(
    f"{BASE}/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET},
)
tok = r.json().get("tenant_access_token")
print(f"[1] token code={r.json().get('code')}, has_token={bool(tok)}")
if not tok:
    print(f"    full: {json.dumps(r.json(), ensure_ascii=False)[:300]}")
    sys.exit(1)
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=utf-8"}

# 2. cardkit create (FLAT body per docs)
card = {
    "schema": "2.0",
    "header": {
        "template": "red",
        "title": {"tag": "plain_text", "content": "🔍 cardkit 探测"},
    },
    "body": {
        "elements": [
            {"tag": "markdown", "content": "**测试**：cardkit + im(card_id) 链路"},
            {"tag": "hr"},
            {"tag": "markdown", "content": "alert_id=probe_cardkit_001"},
            # Card 2.0 不支持 tag:"action" 容器，按钮直接放 elements
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "已知悉"},
                "type": "primary",
                "value": json.dumps(
                    {"action": "ack", "alert_id": "probe_cardkit_001"},
                    separators=(",", ":"),
                ),
            },
        ]
    },
}
data_str = json.dumps(card, ensure_ascii=False)
# 尝试 1：FLAT body（per docs/飞书卡片教程/创建卡片）
body_create_flat = {"type": "card_json", "data": data_str}
r = requests.post(f"{BASE}/open-apis/cardkit/v1/cards/", headers=H, json=body_create_flat)
resp = r.json()
print(f"[2a] cardkit create (FLAT) code={resp.get('code')} msg={resp.get('msg')[:80] if resp.get('msg') else ''}")
card_id_flat = resp.get("data", {}).get("card_id") if resp.get("code") == 0 else None

# 尝试 2：NESTED body（per fancy-splashing-island.md 计划 / 全量更新卡片.md 风格）
body_create_nested = {"card": {"type": "card_json", "data": data_str}}
r2 = requests.post(f"{BASE}/open-apis/cardkit/v1/cards/", headers=H, json=body_create_nested)
resp2 = r2.json()
print(f"[2b] cardkit create (NESTED) code={resp2.get('code')} msg={resp2.get('msg')[:80] if resp2.get('msg') else ''}")
card_id_nested = resp2.get("data", {}).get("card_id") if resp2.get("code") == 0 else None

# 尝试 3：FLAT + update_multi=true（doc 要求必须 true）
card_with_multi = {**card, "update_multi": True}
data_str_3 = json.dumps(card_with_multi, ensure_ascii=False)
body_create_v3 = {"type": "card_json", "data": data_str_3}
r3 = requests.post(f"{BASE}/open-apis/cardkit/v1/cards/", headers=H, json=body_create_v3)
resp3 = r3.json()
print(f"[2c] cardkit create (FLAT + update_multi=true) code={resp3.get('code')} msg={resp3.get('msg')[:80] if resp3.get('msg') else ''}")
card_id_v3 = resp3.get("data", {}).get("card_id") if resp3.get("code") == 0 else None

# 用 FLAT 的 card_id 继续（已成功）
card_id = card_id_flat or card_id_nested or card_id_v3
if not card_id:
    print("所有格式都失败，无法继续")
    sys.exit(1)

# 3. im send with card_id ref
im_body = {
    "receive_id": CHAT_ID,
    "msg_type": "interactive",
    "content": json.dumps({"card_id": card_id}, ensure_ascii=False),
}
r = requests.post(
    f"{BASE}/open-apis/im/v1/messages?receive_id_type=chat_id",
    headers=H,
    json=im_body,
)
resp = r.json()
print(f"[3] im send code={resp.get('code')} msg={resp.get('msg')}")
if resp.get("code") != 0:
    print(f"    full: {json.dumps(resp, ensure_ascii=False)[:400]}")
    sys.exit(1)
print(f"    message_id={resp['data'].get('message_id')}")
print("\n✅ cardkit + im(card_id) 链路成功")
print(f"   card_id={card_id}")
print(f"   message_id={resp['data'].get('message_id')}")
print("\n去飞书群看是否正常显示红色探测卡片，告诉我。")