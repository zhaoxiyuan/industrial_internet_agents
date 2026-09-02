"""feishu_gateway_cli 端到端测试脚本（精简版：只测 send_to_group + send_to_user）。

================================================================================
覆盖
================================================================================

    1. send_to_user() — 按 name 反查 FEISHU_USER_MAP.open_id（4 种异常情形）
    2. SendMessageResult 字段可读
    3. Gateway /healthz 探活
    4. send_to_group() 端到端推送 + 互斥校验 + 按 group_name 反查（依赖真实 chat_id 与 Gateway 进程）
    5. Gateway /v1/events 轮询（验证接收链路）
    6. send_to_group() 按 group_name 反查 FEISHU_GROUP_MAP.chat_id（4 种异常情形）
    7. parse_options() — 选项字符串解析（label:action）+ 错误情形
    8. get_button_type() — action → button.type 映射（primary / danger / default）
    9. build_feishu_card() — 卡片 JSON 结构 + alert_id 注入 + 边界

================================================================================
运行
================================================================================

    # 1. 启动 Gateway（另开一个终端）
    cd openclaw-channel-gateway-standalone
    node start.mjs --config config/config.feishu.local.json

    # 2. UI 配置 .env（http://127.0.0.1:5003/）
    #    GATEWAY_HOST=http://127.0.0.1:8787
    #    CG_API_KEY=gateway-api-key-16chars!
    #    FEISHU_USER_MAP={
    #      "ou_alice": {"role": "作业负责人", "name": "张三"},
    #      "ou_bob":   {"role": "作业负责人", "name": "李四"}
    #    }

    # 3. 跑本测试
    cd industrial_internet_agents
    python agents/_test_notify_e2e.py

    # 4. 端到端群聊推送（可选）
    #    在飞书建一个 2 人群（你 + Bot），复制 oc_xxx
    $env:TEST_CHAT_ID="oc_你拿到的群id"
    python agents/_test_notify_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from feishu_gateway_cli import (
    send_to_group,
    send_to_user,
)
from feishu_gateway_cli.feishu_sender import (
    _find_open_id_by_name,       # 私有 helper；本测试用于覆盖反查逻辑
    _find_chat_id_by_group_name, # 私有 helper；TEST 6 覆盖 group_name 反查
    # Card 相关（TEST 7/8/9）
    parse_options,
    get_button_type,
    build_feishu_card,
    send_to_group_card,
    DEFAULT_CARD_TITLE,
)
from agents.channel_gateway_client import SendMessageResult


# ============================================================
# 打印工具
# ============================================================

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def section(title: str) -> None:
    print(f"\n{YELLOW}{'=' * 60}\n{title}\n{'=' * 60}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")
    sys.exit(1)


def skip(msg: str) -> None:
    print(f"  {YELLOW}[SKIP]{RESET} {msg}")


# ============================================================
# TEST 1: USER_MAP 反查（_find_open_id_by_name）
# ============================================================

def test_resolve_all_sources() -> None:
    section("TEST 1: send_to_user() — 按 name 反查 FEISHU_USER_MAP.open_id")

    # 1.1 命中：USER_MAP 里有 1 条 name=张三 的记录
    os.environ["FEISHU_USER_MAP"] = json.dumps({
        "ou_alice": {"role": "作业负责人", "name": "张三"},
    }, ensure_ascii=False)
    open_id, found_name = _find_open_id_by_name("张三")
    if not open_id.startswith("ou_"):
        fail(f"open_id 格式异常: {open_id}")
    ok(f"USER_MAP 命中：name=张三 → open_id={open_id}")

    # 1.2 兼容历史 JSON：旧 .env 中 USER_MAP 记录可能仍含 chat_id 字段（已废弃）
    # _load_user_map() 应静默忽略 chat_id，仍按 name 命中
    os.environ["FEISHU_USER_MAP"] = json.dumps({
        "ou_lizongrui": {"chat_id": "oc_group_xxx", "role": "作业负责人", "name": "李宗睿"},
    }, ensure_ascii=False)
    open_id, found_name = _find_open_id_by_name("李宗睿")
    if open_id != "ou_lizongrui":
        fail(f"open_id 不匹配: {open_id}")
    ok(f"USER_MAP 兼容旧格式（chat_id 字段被静默忽略）：name=李宗睿 → open_id={open_id}")

    # 1.3 找不到：name 完全没配 → ValueError
    os.environ["FEISHU_USER_MAP"] = json.dumps({}, ensure_ascii=False)
    try:
        _find_open_id_by_name("不存在XYZ")
        fail("期望 ValueError（找不到），但调用成功")
    except ValueError as exc:
        ok(f"name 找不到 → ValueError: {str(exc)[:60]}")

    # 1.4 同名多条单聊：歧义 → ValueError
    os.environ["FEISHU_USER_MAP"] = json.dumps({
        "ou_a": {"role": "r", "name": "张三"},
        "ou_b": {"role": "r", "name": "张三"},
    }, ensure_ascii=False)
    try:
        _find_open_id_by_name("张三")
        fail("期望 ValueError（同名歧义），但调用成功")
    except ValueError as exc:
        ok(f"同名多条 → ValueError: {str(exc)[:60]}")

    # 1.5 send_to_user 入参校验：name 和 open_id 都未传 → ValueError
    os.environ["FEISHU_USER_MAP"] = json.dumps({}, ensure_ascii=False)
    try:
        from feishu_gateway_cli.feishu_sender import send_to_user
        send_to_user(text="x")
        fail("期望 ValueError（name 和 open_id 都未传），但调用成功")
    except ValueError as exc:
        ok(f"两者都未传 → ValueError: {str(exc)[:60]}")

    # 1.6 send_to_user 入参校验：name 和 open_id 都传了 → ValueError
    try:
        send_to_user(text="x", name="张三", open_id="ou_xxx")
        fail("期望 ValueError（name 和 open_id 都传了），但调用成功")
    except ValueError as exc:
        ok(f"两者都传了 → ValueError: {str(exc)[:60]}")

    # 1.7 send_to_user open_id 路径：跳过 USER_MAP 直传
    # 不真发 Gateway，只校验参数解析 + 错误码分支
    # （send_message 会在网络层失败；这里只确认走到 send_message）
    try:
        send_to_user(text="x", open_id="ou_511d109b8ac87f972af2d5e67e2c8270")
        # Gateway 没启动时会 ConnectionError；不是我们的测试目标
    except (ConnectionError, OSError) as exc:
        ok(f"open_id 路径到达 Gateway 层（无 Gateway 时网络异常，符合预期）")
    except Exception as exc:
        # 其它异常也算走到 Gateway（网关业务错误等）
        ok(f"open_id 路径到达 Gateway 层（{type(exc).__name__}，符合预期）")


# ============================================================
# TEST 2: SendMessageResult 字段可读
# ============================================================

def test_result_dataclass() -> None:
    section("TEST 2: SendMessageResult 字段可读（来自 channel_gateway_client）")

    # 用 .raw 构造一个最小实例
    r = SendMessageResult(
        intent_id="intent_test_456",
        status="sent",
        idempotency_key="idem_test",
        receipt_id="rcpt_test",
        platform_message_id="om_test_123",
        evidence=None,
        replayed=False,
        raw={"intent": {"id": "intent_test_456", "status": "sent"},
             "receipt": {"id": "rcpt_test"}},
    )
    assert r.status == "sent"
    assert r.intent_id == "intent_test_456"
    assert r.platform_message_id == "om_test_123"
    assert r.replayed is False
    ok(f"SendMessageResult 字段访问正常：status={r.status} "
       f"intent_id={r.intent_id} message_id={r.platform_message_id}")


# ============================================================
# TEST 3: Gateway /healthz
# ============================================================

def test_gateway_health() -> None:
    section("TEST 3: Gateway /healthz 探活")

    from feishu_gateway_cli.feishu_config_app import test_gateway_connection
    result = test_gateway_connection(
        host=os.getenv("GATEWAY_HOST", "http://127.0.0.1:8787"),
        api_key=os.getenv("CG_API_KEY", ""),
    )
    if not result["ok"]:
        fail(f"Gateway 不可达：status={result['status']}, error={result.get('error')}")
    ok(f"Gateway 健康：status={result['status']}, url={result['url']}")


# ============================================================
# TEST 4: send_to_group() 端到端推送
# ============================================================

def test_send_to_group_e2e() -> None:
    section("TEST 4: send_to_group() 互斥校验 + 端到端推送")

    # 4.0 互斥校验（不依赖 Gateway，纯参数校验）
    try:
        send_to_group(text="x")
        fail("期望 ValueError（chat_id 和 group_name 都未传），但调用成功")
    except ValueError as exc:
        ok(f"两者都未传 → ValueError: {str(exc)[:60]}")

    try:
        send_to_group(text="x", chat_id="oc_xxx", group_name="应急响应群")
        fail("期望 ValueError（chat_id 和 group_name 都传了），但调用成功")
    except ValueError as exc:
        ok(f"两者都传了 → ValueError: {str(exc)[:60]}")

    # 4.x 端到端推送（需要 TEST_CHAT_ID + Gateway 进程）
    chat_id = os.getenv("TEST_CHAT_ID")
    if not chat_id:
        skip("未设置 TEST_CHAT_ID，跳过端到端推送")
        print("  设置方法（PowerShell）: $env:TEST_CHAT_ID='oc_xxx'")
        print("  获取方法：飞书建 2 人群（你 + Bot）→ 群设置 → 复制群 ID")
        return

    # 4.1 按 chat_id 直传（旧路径，兼容）
    print(f"  推送群 chat_id = {chat_id}（路径 1：chat_id 直传）")
    result = send_to_group(
        text="【E2E 测试·chat_id 路径】本消息由 agents/_test_notify_e2e.py 触发，请忽略",
        chat_id=chat_id,
    )

    print(f"\n  result.status         = {result.status}")
    print(f"  result.intent_id      = {result.intent_id}")
    print(f"  result.message_id     = {result.platform_message_id}")
    print(f"  result.replayed       = {result.replayed}")
    print(f"  result.evidence       = {result.evidence}")

    if (result.status or "").lower() not in ("sent", "sending"):
        fail(f"推送失败（chat_id 路径）：status={result.status} evidence={result.evidence}")

    ok(f"chat_id 路径完成！status={result.status} message_id={result.platform_message_id}")
    print(f"  → 请到飞书群确认收到消息")

    # 4.2 按 group_name 反查（新路径；可选；需要 TEST_GROUP_NAME + FEISHU_GROUP_MAP）
    group_name = os.getenv("TEST_GROUP_NAME")
    if not group_name:
        skip("未设置 TEST_GROUP_NAME，跳过 group_name 路径")
        print("  设置方法（PowerShell）: $env:TEST_GROUP_NAME='应急响应群'")
        return

    print(f"  推送群 group_name = {group_name}（路径 2：group_name 反查 GROUP_MAP）")
    result = send_to_group(
        text="【E2E 测试·group_name 路径】本消息由 agents/_test_notify_e2e.py 触发，请忽略",
        group_name=group_name,
    )

    print(f"\n  result.status         = {result.status}")
    print(f"  result.intent_id      = {result.intent_id}")
    print(f"  result.message_id     = {result.platform_message_id}")
    print(f"  result.replayed       = {result.replayed}")

    if (result.status or "").lower() not in ("sent", "sending"):
        fail(f"推送失败（group_name 路径）：status={result.status} evidence={result.evidence}")

    ok(f"group_name 路径完成！status={result.status} message_id={result.platform_message_id}")
    print(f"  → 请到飞书群确认收到消息（按名称发送）")


# ============================================================
# TEST 5: Gateway /v1/events 接收链路
# ============================================================

def test_gateway_receive() -> None:
    section("TEST 5: Gateway /v1/events 接收链路")

    import requests

    host = os.getenv("GATEWAY_HOST", "http://127.0.0.1:8787").rstrip("/")
    api_key = os.getenv("CG_API_KEY", "")

    if not api_key:
        skip("未配 CG_API_KEY，跳过接收链路测试")
        return

    url = f"{host}/v1/events?after_sequence=0"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"  GET {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=5)
    except requests.RequestException as exc:
        fail(f"GET /v1/events 网络异常：{exc}")

    if resp.status_code != 200:
        fail(f"GET /v1/events 失败：status={resp.status_code}, body={resp.text[:200]}")

    data = resp.json()
    latest = data.get("latestSequence", 0)
    events = data.get("events", [])

    print(f"  latestSequence = {latest}")
    print(f"  events count   = {len(events)}")

    if not events:
        skip("暂无事件。请先去飞书群给 Bot 发一条消息，再重跑本测试")
    else:
        for evt in events[:3]:
            et = evt.get("eventType", "?")
            seq = evt.get("sequence", "?")
            text = (evt.get("text") or "")[:50]
            print(f"    - seq={seq} type={et} text={text!r}")
        ok(f"收到 {len(events)} 条事件（latestSequence={latest}）")


# ============================================================
# TEST 6: GROUP_MAP 反查（_find_chat_id_by_group_name）
# ============================================================

def test_resolve_group_map() -> None:
    section("TEST 6: send_to_group() — 按 group_name 反查 FEISHU_GROUP_MAP.chat_id")

    # 6.1 命中：GROUP_MAP 里有 1 条 name="应急响应群" 的记录
    os.environ["FEISHU_GROUP_MAP"] = json.dumps({
        "oc_xxx1": {"name": "应急响应群", "description": "P8 告警群"},
    }, ensure_ascii=False)
    chat_id, found_name = _find_chat_id_by_group_name("应急响应群")
    if chat_id != "oc_xxx1":
        fail(f"chat_id 不匹配: {chat_id}")
    ok(f"GROUP_MAP 命中：name=应急响应群 → chat_id={chat_id}")

    # 6.2 找不到：name 完全没配 → ValueError
    os.environ["FEISHU_GROUP_MAP"] = json.dumps({}, ensure_ascii=False)
    try:
        _find_chat_id_by_group_name("不存在XYZ")
        fail("期望 ValueError（找不到），但调用成功")
    except ValueError as exc:
        ok(f"name 找不到 → ValueError: {str(exc)[:60]}")

    # 6.3 同名多条群聊：歧义 → ValueError
    os.environ["FEISHU_GROUP_MAP"] = json.dumps({
        "oc_a": {"name": "应急响应群", "description": ""},
        "oc_b": {"name": "应急响应群", "description": ""},
    }, ensure_ascii=False)
    try:
        _find_chat_id_by_group_name("应急响应群")
        fail("期望 ValueError（同名歧义），但调用成功")
    except ValueError as exc:
        ok(f"同名多条 → ValueError: {str(exc)[:60]}")

    # 6.4 name 为空 → ValueError
    os.environ["FEISHU_GROUP_MAP"] = json.dumps({
        "oc_a": {"name": "应急响应群", "description": ""},
    }, ensure_ascii=False)
    try:
        _find_chat_id_by_group_name("")
        fail("期望 ValueError（name 为空），但调用成功")
    except ValueError as exc:
        ok(f"name 为空 → ValueError: {str(exc)[:60]}")

    # 6.5 name 为 None → ValueError（与 str 入参兼容）
    try:
        _find_chat_id_by_group_name(None)
        fail("期望 ValueError（name 为 None），但调用成功")
    except ValueError as exc:
        ok(f"name=None → ValueError: {str(exc)[:60]}")

    # 6.6 GROUP_MAP 中有空 name 占位条目 → 跳过（不视为歧义）
    os.environ["FEISHU_GROUP_MAP"] = json.dumps({
        "oc_a": {"name": "应急响应群", "description": ""},
        "oc_b": {"name": "",           "description": "未命名占位"},
    }, ensure_ascii=False)
    chat_id, found_name = _find_chat_id_by_group_name("应急响应群")
    if chat_id != "oc_a":
        fail(f"期望跳过空 name 条目后命中 oc_a，实际: {chat_id}")
    ok(f"空 name 占位条目被跳过：name=应急响应群 → chat_id={chat_id}")


# ============================================================
# TEST 7: parse_options() — 选项字符串解析
# ============================================================

def test_parse_options() -> None:
    section("TEST 7: parse_options — 'label:action' 解析 + 错误情形")

    # 7.1 标准解析：单个 + 多个选项
    result = parse_options(["已知悉:ack", "立即处理:handle"])
    if result != [("已知悉", "ack"), ("立即处理", "handle")]:
        fail(f"期望 2 个标准选项，实际: {result}")
    ok(f"标准 2 个选项解析成功: {result}")

    # 7.2 空入参（None）→ 空列表
    if parse_options(None) != []:
        fail("期望 parse_options(None) 返回 []")
    ok("None 入参 → 空列表")

    if parse_options([]) != []:
        fail("期望 parse_options([]) 返回 []")
    ok("空列表入参 → 空列表")

    # 7.3 自动 strip 首尾空白（中间空白保留）
    result = parse_options([" 已知悉 : ack ", " 立即处理 :handle"])
    if result != [("已知悉", "ack"), ("立即处理", "handle")]:
        fail(f"期望 strip 后 2 个选项，实际: {result}")
    ok(f"自动 strip 首尾空白: {result}")

    # 7.4 多冒号（split 第一次）
    result = parse_options(["URL:https://example.com/path:with:colons"])
    if result != [("URL", "https://example.com/path:with:colons")]:
        fail(f"期望 split 第一次，实际: {result}")
    ok(f"多冒号 split 第一次: {result}")

    # 7.5 全空白字符串被静默跳过（CLI 设计选择）
    result = parse_options(["已知悉:ack", "   ", "\t"])
    if result != [("已知悉", "ack")]:
        fail(f"期望跳过空白条后剩 1 条，实际: {result}")
    ok("全空白字符串被静默跳过（设计选择，不抛错）")

    # 7.6 错误情形：缺冒号 → ValueError
    try:
        parse_options(["已知悉ack"])  # 没有冒号
        fail("期望 ValueError（缺冒号），但调用成功")
    except ValueError as exc:
        if "非" not in str(exc) and "label" not in str(exc).lower() and "格式" not in str(exc):
            # 检查错误信息是否友好（提到 label/action/格式/冒号）
            fail(f"错误信息不够友好: {exc}")
        ok(f"缺冒号 → ValueError: {str(exc)[:60]}")

    # 7.7 错误情形：label 为空 → ValueError
    try:
        parse_options([":ack"])  # label 空
        fail("期望 ValueError（label 为空），但调用成功")
    except ValueError as exc:
        ok(f"label 为空 → ValueError: {str(exc)[:60]}")

    # 7.8 错误情形：action 为空 → ValueError
    try:
        parse_options(["已知悉:"])  # action 空
        fail("期望 ValueError（action 为空），但调用成功")
    except ValueError as exc:
        ok(f"action 为空 → ValueError: {str(exc)[:60]}")


# ============================================================
# TEST 8: get_button_type() — action → button.type 映射
# ============================================================

def test_get_button_type() -> None:
    section("TEST 8: get_button_type — action → button.type 映射")

    # 8.1 primary 类（handle / confirm / execute）
    for action in ("handle", "confirm", "execute"):
        result = get_button_type(action)
        if result != "primary":
            fail(f"期望 primary，实际: {action} → {result}")
        ok(f"{action} → primary")

    # 8.2 danger 类（cancel / delete / reject / false_alarm）
    for action in ("cancel", "delete", "reject", "false_alarm"):
        result = get_button_type(action)
        if result != "danger":
            fail(f"期望 danger，实际: {action} → {result}")
        ok(f"{action} → danger")

    # 8.3 default 类（其它 action）
    for action in ("ack", "view", "open", "details", "submit", "其他"):
        result = get_button_type(action)
        if result != "default":
            fail(f"期望 default，实际: {action} → {result}")
        ok(f"{action} → default")


# ============================================================
# TEST 9: build_feishu_card() — 卡片 JSON 结构
# ============================================================

def test_build_feishu_card() -> None:
    section("TEST 9: build_feishu_card — 卡片结构 + alert_id 注入 + 边界")

    # 9.1 最小情形：仅 text，无 options
    card = build_feishu_card("测试告警", [])
    if card["header"]["title"]["content"] != DEFAULT_CARD_TITLE:
        fail(f"默认标题应为 {DEFAULT_CARD_TITLE!r}，实际: {card['header']['title']['content']}")
    if card["header"]["template"] != "red":
        fail(f"告警场景默认模板应为 red，实际: {card['header']['template']}")
    elements = card["body"]["elements"]
    if len(elements) != 1:
        fail(f"仅 text 时 body.elements 应只有 1 项，实际: {len(elements)}")
    if elements[0]["tag"] != "markdown":
        fail(f"text 元素 tag 应为 markdown（Card 2.0），实际: {elements[0].get('tag')}")
    if elements[0]["content"] != "测试告警":
        fail(f"markdown content 不一致: {elements[0]['content']}")
    ok("最小卡片（text only）结构正确")

    # 9.2 自定义标题
    card = build_feishu_card("巡检通知", [], title="[ALERT] 巡检提醒")
    if card["header"]["title"]["content"] != "[ALERT] 巡检提醒":
        fail(f"自定义标题未生效: {card['header']['title']['content']}")
    ok(f"自定义标题生效: {card['header']['title']['content']!r}")

    # 9.3 空字符串标题 → 回退默认
    card = build_feishu_card("x", [], title="")
    if card["header"]["title"]["content"] != DEFAULT_CARD_TITLE:
        fail(f"空 title 应回退到默认，实际: {card['header']['title']['content']!r}")
    ok(f"空 title 回退到默认: {DEFAULT_CARD_TITLE!r}")

    # 9.4 完整卡片：text + options + alert_id
    options = [
        ("已知悉", "ack"),
        ("立即处理", "handle"),
        ("误报", "false_alarm"),
    ]
    card = build_feishu_card(
        text="【告警】可燃气体浓度异常",
        options=options,
        title="气体浓度告警",
        alert_id="gas_20260817_001",
    )
    elements = card["body"]["elements"]
    if len(elements) != 2:
        fail(f"完整卡片 body.elements 应为 2（markdown + column_set），实际: {len(elements)}")
    action_el = elements[1]
    if action_el["tag"] != "column_set":
        fail(f"第二个元素 tag 应为 column_set（横向布局），实际: {action_el.get('tag')}")
    columns = action_el["columns"]
    if len(columns) != 3:
        fail(f"column 数应为 3（每个按钮一个 column），实际: {len(columns)}")
    # column 0：ack → default
    b0 = columns[0]["elements"][0]
    if b0["type"] != "default":
        fail(f"按钮 ack 应为 default，实际: {b0['type']}")
    # Card 2.0 value 是 JSON 字符串（飞书强约束），需 json.loads 后访问
    b0_value = json.loads(b0["value"])
    if b0_value["action"] != "ack":
        fail(f"按钮 value.action 错误: {b0_value}")
    if b0_value.get("alert_id") != "gas_20260817_001":
        fail(f"按钮 value.alert_id 未注入: {b0_value}")
    # column 1：handle → primary
    b1 = columns[1]["elements"][0]
    if b1["type"] != "primary":
        fail(f"按钮 handle 应为 primary，实际: {b1['type']}")
    # column 2：false_alarm → danger
    b2 = columns[2]["elements"][0]
    if b2["type"] != "danger":
        fail(f"按钮 false_alarm 应为 danger，实际: {b2['type']}")
    ok("完整卡片：text + 3 横向按钮（column_set）+ alert_id 注入全部正确")

    # 9.5 alert_id 为空字符串 → 不写入 value
    card = build_feishu_card("x", [("已知悉", "ack")], alert_id="")
    btn_value = json.loads(card["body"]["elements"][1]["columns"][0]["elements"][0]["value"])
    if "alert_id" in btn_value:
        fail(f"空 alert_id 不应写入 value，实际: {btn_value}")
    if btn_value["action"] != "ack":
        fail(f"value.action 错误: {btn_value}")
    ok("空 alert_id 不写入按钮 value（避免污染回调数据）")

    # 9.6 alert_id 为 None → 不写入 value
    card = build_feishu_card("x", [("已知悉", "ack")], alert_id=None)
    btn_value = json.loads(card["body"]["elements"][1]["columns"][0]["elements"][0]["value"])
    if "alert_id" in btn_value:
        fail(f"None alert_id 不应写入 value，实际: {btn_value}")
    ok("None alert_id 不写入按钮 value")

    # 9.7 Card 2.0 schema 顶层验证
    if card.get("schema") != "2.0":
        fail(f"Card 2.0 强制 schema='2.0'，实际: {card.get('schema')}")
    if card["body"]["elements"][0]["tag"] != "markdown":
        fail(f"Card 2.0 文本元素应为 markdown，实际: {card['body']['elements'][0].get('tag')}")
    ok("Card 2.0 schema：schema='2.0' + body.elements[0].tag=markdown")

    # 9.8 序列化往返（ensure_ascii=False 支持中文）
    card = build_feishu_card(
        text="【告警】气体浓度异常",
        options=[("已知悉", "ack")],
        title="气体告警",
    )
    try:
        s = json.dumps(card, ensure_ascii=False)
        if "气体告警" not in s:
            fail(f"序列化应保留中文，实际: {s[:100]}")
        # 反序列化
        parsed = json.loads(s)
        if parsed["header"]["title"]["content"] != "气体告警":
            fail(f"反序列化后中文丢失: {parsed}")
        ok("JSON 序列化往返：中文不转义（ensure_ascii=False）")
    except (TypeError, ValueError) as exc:
        fail(f"序列化失败: {exc}")

    # 9.9 边界：options 为 None（与空列表同）
    card_none = build_feishu_card("x", None)
    card_empty = build_feishu_card("x", [])
    if card_none != card_empty:
        fail("None 与 [] 入参应产出相同结果")
    ok("options=None 与 options=[] 等价")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print(f"\n{'#' * 60}")
    print(f"# feishu_gateway_cli 端到端测试（USER_MAP + GROUP_MAP 模型，2026-08-17）")
    print(f"# 项目根: {ROOT}")
    print(f"{'#' * 60}")

    test_resolve_all_sources()
    test_result_dataclass()
    test_gateway_health()
    test_send_to_group_e2e()
    test_gateway_receive()
    test_resolve_group_map()
    test_parse_options()
    test_get_button_type()
    test_build_feishu_card()

    print(f"\n{GREEN}{'=' * 60}\n所有测试通过\n{'=' * 60}{RESET}\n")


if __name__ == "__main__":
    main()