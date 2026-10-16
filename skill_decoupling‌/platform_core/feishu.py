"""飞书标准功能库：独立连接配置、消息发送、验证接收、回复与处理确认。

参照原项目的配置页和 env 保存习惯，但直接对接飞书 Open API，不依赖 Gateway。
业务 Schema 不要求 Agent 填写机器人账号；内置 CLI 命令的平台字段由绑定注入。
目录来自用户配置，发送状态来自真实 API 回执，不能把查询成功视为发送成功。"""
import hashlib
import hmac
import json
import os
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .common import Problem, digest, dumps, now, text, uid, url, validate
from .crypto import decrypt_feishu
from .http_client import request


def obj(properties, required=()):
    """构造不允许额外字段的对象 Schema；required 转列表以便 JSON 序列化。"""
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
TARGET = obj({"type": {"enum": ["group", "user"]}, "name": STRING, "id": STRING}, ["type"])
SCHEMAS = {
    "lookup_feishu_directory": obj({"entity_type": {"enum": ["group", "user"]}, "name": {"type": "string"}, "id": {"type": "string"}}, ["entity_type"]),
    "feishu_send": obj({"target": TARGET, "text": {"type": "string", "minLength": 1, "maxLength": 10000}}, ["target", "text"]),
    "feishu_events": obj({"after": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    "feishu_reply": obj({"event_id": STRING, "text": {"type": "string", "minLength": 1, "maxLength": 10000}}, ["event_id", "text"]),
    "feishu_ack": obj({"event_id": STRING, "status": {"enum": ["processed", "ignored"]}}, ["event_id", "status"]),
    "feishu_health": obj({}),
    "feishu_sync_risk_card": obj({"report_id": {**STRING, "description": "同一事件的稳定处置report_id，来自初始化/实际队列；不传event_id、任务ID或自行编造的新ID。"}}, ["report_id"]),
}
DESCRIPTIONS = {
    "lookup_feishu_directory": "在当前飞书连接配置的群/用户目录中查询名称或 ID，不发送消息。",
    "feishu_send": "发送一条飞书文本消息。target.type 为 group/user，填写 name 或 id；账号由平台绑定，不填机器人 App ID。失败或结果未知时不要盲目重发。",
    "feishu_events": "读取当前飞书连接的已验证入站事件，after 为序号游标，不自动确认处理。",
    "feishu_reply": "回复当前连接中的入站消息。使用事件 event_id；成功返回实际消息回执后再 ACK。",
    "feishu_ack": "确认入站事件已处理或忽略。processed 需要该事件已有成功回复回执。",
    "feishu_health": "只读检查当前飞书连接的凭据和飞书 API 连通性，不发送测试消息。",
    "feishu_sync_risk_card": "平台飞书能力：只同步已落库单风险处置的最新卡片，不允许模型构造卡片JSON或选择机器人/收件人。report_id必须来自initialize_disposition或真实队列；首次创建，后续同report_id更新同一CardKit实体/消息，开放、未知、恢复中、结束均可更新。作业范围、收件目标、模板、uuid/sequence、消息关联、签名回调及附件页由平台控制，保持已验证协议；业务Skill不实现发送工具。delivery=delivered才是真实成功，simulated仅测试，pending不是送达；unknown需平台核对，不能强制重发初始告警或改ID绕过。无有效平台绑定/连接/业务范围则拒绝，不回退文本告警。风险状态与处置状态独立显示；更新保留人工操作、材料与审核，不代表人工批准或作业关闭。",
}


class Feishu:
    """管理多个独立飞书连接，持久配置归本模块，Agent 仅持有绑定引用。"""
    ENV_FIELDS = {"app_id": "FEISHU_APP_ID", "app_secret": "FEISHU_APP_SECRET", "verification_token": "FEISHU_VERIFICATION_TOKEN",
                  "encrypt_key": "FEISHU_ENCRYPT_KEY", "api_base": "FEISHU_API_BASE", "groups": "FEISHU_GROUP_MAP", "users": "FEISHU_USER_MAP"}
    PRIVATE = {"app_secret", "verification_token", "encrypt_key"}

    def __init__(self, store):
        """初始化配置/发送互斥锁与短期 token 缓存，数据库回执负责跨重启去重。"""
        self.store = store
        self.lock = threading.RLock()
        self.tokens = {}
        from .risk_cards import RiskCards
        self.cards = RiskCards(self)

    def env_path(self, id):
        """先确认连接存在，再返回此连接独立的 .env 文件路径。"""
        self.store.get("connections", id)
        return self.store.data / "functions" / id / ".env"

    def config(self, id):
        """每次读取本连接 env，让配置页保存后即时生效；不污染进程 os.environ。
        已知字段映射为业务键，群/用户目录解码为对象，未知行由保存逻辑保留。"""
        path = self.env_path(id)
        if not path.exists():
            raise Problem("CONFIG_MISSING", "飞书配置文件不存在", 409)
        entries = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                try:
                    entries[key] = json.loads(value)
                except ValueError:
                    entries[key] = value.strip("\"'")
        result = {key: entries.get(field, {} if key in ("groups", "users") else "") for key, field in self.ENV_FIELDS.items()}
        for field in ("groups", "users"):
            if isinstance(result[field], str):
                result[field] = json.loads(result[field])
        return result

    def public(self, id):
        """返回非敏感配置、密钥是否已配置、env 和 Webhook 路径。
        密钥原值从不回显；前端留空代表保存时保留已有值。"""
        record = self.store.get("connections", id)
        config = self.config(id)
        result = {**record, **{k: v for k, v in config.items() if k not in self.PRIVATE}}
        result.update({k + "_configured": bool(config[k]) for k in self.PRIVATE})
        result["env_file"] = str(self.env_path(id))
        # 回调使用业务连接名称（如 P8），内部 UUID 仅用于存储、Agent 绑定和管理 API。
        # URL 编码支持中文与空格，名称匹配仍区分大小写。
        result["webhook_path"] = "/webhooks/feishu/" + quote(record["name"], safe="")
        return result

    def callback_connection(self, name):
        """将回调路径中的连接名称解析为唯一内部 ID，不把名称当文件路径。

        新配置保证名称唯一；旧数据如有重名则明确拒绝，而不是把消息交给随机账号。
        名称不存在返回 404，停用由 inbound/require 再次校验；不回退到 UUID 路径。
        """
        matches = [connection for connection in self.store.list("connections") if connection["name"] == name]
        if not matches:
            raise Problem("CONNECTION_NOT_FOUND", "回调路径对应的连接名称不存在", 404)
        if len(matches) != 1:
            raise Problem("CONNECTION_NAME_CONFLICT", "连接名称重复，请在配置页面修正", 409)
        return matches[0]["id"]

    def save(self, data, id=None):
        """校验配置，按连接原子写入 .env，并将旧文件备份为 .env.bak。
        敏感字段空值默认保留，clear_secrets 可显式清除；修改后清空 token 缓存。
        保存不测试网络、不发消息，也不启动服务。"""
        with self.lock:
            old = self.config(id) if id else {"api_base": "https://open.feishu.cn", "groups": {}, "users": {}}
            record = self.store.get("connections", id) if id else {}
            # 入站消息可选交给一个已定义 Agent；未设置时保持只接收、不自动执行。
            inbound_agent = data.get("inbound_agent_id", record.get("inbound_agent_id", ""))
            if inbound_agent:
                self.store.get("agents", inbound_agent)
            name = text(data.get("name", record.get("name")), "连接名称", 80)
            # 名称就是回调最后一段，因此不能含路径分隔符、控制字符或重名。
            # 修改名称会同步改变显示的回调地址，既有内部绑定和已存消息仍保留。
            if name in (".", "..") or any(char in name for char in ("/", "\\")) or any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise Problem("CONNECTION_NAME_INVALID", "连接名称不能含斜线、控制字符或路径跳转")
            if any(connection["name"] == name and connection["id"] != id for connection in self.store.list("connections")):
                raise Problem("CONNECTION_NAME_CONFLICT", "连接名称已存在，请使用唯一名称", 409)
            config = dict(old)
            for field in self.ENV_FIELDS:
                if field in data and (field not in self.PRIVATE or data[field]):
                    config[field] = data[field]
            # env 字段类型在保存前检查，避免已保存的错误类型导致后续 Token/签名函数崩溃。
            if any(not isinstance(config.get(field, ""), str) for field in self.ENV_FIELDS if field not in ("groups", "users")):
                raise Problem("PARAMETER_INVALID", "飞书凭据与 API 地址必须为字符串")
            for field in self.PRIVATE:
                if field in data.get("clear_secrets", []):
                    config[field] = ""
            text(config.get("app_id"), "App ID", 120)
            url(config.get("api_base"))
            for kind in ("groups", "users"):
                mapping = config.get(kind, {})
                if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, dict) or not v.get("name") for k, v in mapping.items()):
                    raise Problem("PARAMETER_INVALID", "群/用户目录必须是 ID → {name, description...} 映射")
            id = id or uid()
            path = self.store.data / "functions" / id / ".env"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["# 本连接独立配置；由飞书配置页写入。"]
            known = set(self.ENV_FIELDS.values())
            preserved = [line for line in existing_lines if line.split("=", 1)[0].strip() not in known]
            output = "\n".join(preserved + [field + "=" + dumps(config.get(key, "")) for key, field in self.ENV_FIELDS.items()]) + "\n"
            temp = path.with_name(".env.tmp")
            temp.write_text(output, encoding="utf-8")
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            if path.exists():
                shutil.copy2(path, path.with_name(".env.bak"))
            os.replace(temp, path)
            self.store.put("connections", {"name": name, "enabled": bool(data.get("enabled", record.get("enabled", True))),
                                           "kind": "feishu", "inbound_agent_id": inbound_agent}, id)
            self.tokens.pop(id, None)
            return self.public(id)

    def require(self, id):
        """确认连接存在且启用，返回其配置；停用连接不删除消息和回执。"""
        record = self.store.get("connections", id)
        if not record["enabled"]:
            raise Problem("CONNECTION_DISABLED", "飞书连接已停用", 409)
        return self.config(id)

    def token(self, id):
        """取得 tenant_access_token 并按凭据指纹缓存，提前 60 秒失效。
        仅成功认证后缓存；密钥缺失或平台拒绝时返回独立错误，不泄露返回原文。"""
        c = self.require(id)
        if not c.get("app_secret"):
            raise Problem("CONFIG_MISSING", "请在飞书配置页填写 App Secret", 409)
        key = digest([c["app_id"], c["app_secret"], c["api_base"]])
        with self.lock:
            cached = self.tokens.get(id)
            if cached and cached[0] == key and cached[2] > time.time():
                return cached[1]
            result = request(c["api_base"].rstrip("/") + "/open-apis/auth/v3/tenant_access_token/internal",
                             {"app_id": c["app_id"], "app_secret": c["app_secret"]})
            if result.get("code") != 0 or not result.get("tenant_access_token"):
                raise Problem("FEISHU_AUTH_FAILED", "飞书凭据验证失败", 502, {"platform_code": result.get("code")})
            self.tokens[id] = (key, result["tenant_access_token"], time.time() + max(1, int(result.get("expire", 3600)) - 60))
            return result["tenant_access_token"]

    def resolve(self, id, target):
        """从本连接的群/用户目录解析唯一目标，返回实际 ID 与 receive_id_type。
        必须在 name/id 中选一项，重名要求显式 ID；不存在时禁止猜测发送。"""
        c = self.require(id)
        validate(target, TARGET)
        kind = target["type"]
        mapping = c["groups" if kind == "group" else "users"]
        if bool(target.get("id")) == bool(target.get("name")):
            raise Problem("PARAMETER_INVALID", "目标需选择 name 或 id 其中一项")
        candidates = [(k, v) for k, v in mapping.items() if (target.get("id") == k or target.get("name") == v.get("name"))]
        if not candidates:
            raise Problem("TARGET_NOT_FOUND", "配置的目录中没有此目标，请在飞书配置页增加映射", 404)
        if len(candidates) != 1:
            raise Problem("TARGET_AMBIGUOUS", "目标名称有歧义，请使用 ID", 409, {"ids": [k for k, _ in candidates]})
        key, meta = candidates[0]
        return {"type": kind, "id": key, "name": meta["name"], "receive_id_type": "chat_id" if kind == "group" else "open_id", "directory_source": "configured"}

    def health(self, id):
        """只读验证凭据和 API 连通性，返回目录数量，不发送测试消息。
        成功取 token 不能证明机器人拥有发消息权限，因此明确标为尚未验证。"""
        self.token(id)
        c = self.config(id)
        return {"healthy": True, "credentials": "valid", "api": "reachable", "directory_source": "configured",
                "groups": len(c["groups"]), "users": len(c["users"]), "message_permission": "not_verified_by_read_only_check"}

    def send(self, id, target, message, request_id, event_id=None):
        """发送新消息或回复事件，并持久保存 request_id 对应的执行回执。
        相同 ID/内容返回旧结果，不重复发送；同 ID/不同内容报冲突。
        网络不确定时保留 unknown，成功必须有飞书 message_id，禁止自动重发。"""
        validate(message, {"type": "string", "minLength": 1, "maxLength": 10000})
        c = self.require(id)
        resolved = self.resolve(id, target) if not event_id else self.store.event(id, event_id)
        fingerprint = digest({"connection": id, "target": target, "text": message, "event": event_id})
        with self.lock:
            old = self.store.receipt(request_id)
            if old:
                if old[0] != fingerprint:
                    raise Problem("IDEMPOTENCY_CONFLICT", "请求 ID 已用于不同消息", 409)
                return dict(old[1], replayed=True)
            token = self.token(id)
            # 发起写操作前持久保存 unknown；即使进程中断，也不能把未完成回执误当成未发送。
            pending = {"status": "unknown", "request_id": request_id, "connection_id": id, "event_id": event_id}
            self.store.save_receipt(request_id, fingerprint, pending)
            if event_id:
                endpoint = "/open-apis/im/v1/messages/" + quote(resolved["message_id"], safe="") + "/reply"
                payload = {"msg_type": "text", "content": dumps({"text": message}), "uuid": request_id[:50]}
            else:
                endpoint = "/open-apis/im/v1/messages?receive_id_type=" + resolved["receive_id_type"]
                payload = {"receive_id": resolved["id"], "msg_type": "text", "content": dumps({"text": message}), "uuid": request_id[:50]}
            try:
                reply = request(c["api_base"].rstrip("/") + endpoint, payload, {"Authorization": "Bearer " + token})
                if reply.get("code") != 0:
                    raise Problem("FEISHU_SEND_FAILED", "飞书拒绝发送，请检查机器人权限和群成员资格", 502, {"platform_code": reply.get("code")})
                message_id = reply.get("data", {}).get("message_id")
                if not message_id:
                    raise Problem("RESULT_UNKNOWN", "发送接口没有给出消息回执", 502)
                result = {**pending, "status": "sent", "message_id": message_id, "target": resolved, "evidence": "platform_api_accepted"}
            except Problem as e:
                result = {**pending, "status": "unknown" if e.code in ("RESULT_UNKNOWN", "SERVICE_UNAVAILABLE", "UPSTREAM_INVALID") else "failed", "error": e.as_dict()}
            self.store.save_receipt(request_id, fingerprint, result)
            return result

    def inbound(self, id, payload, headers, raw):
        """按签名、时间戳、加密信封和 Verification Token 验证飞书回调。
        只保存用户消息事件，机器人消息忽略以防回复循环；按事件 ID 去重。
        返回 challenge 或快速接收回执，不在回调请求中运行模型或发送消息。"""
        c = self.require(id)
        encrypt_key = c.get("encrypt_key", "")
        if encrypt_key:
            stamp, nonce, sig = (headers.get(k, "") for k in ("x-lark-request-timestamp", "x-lark-request-nonce", "x-lark-signature"))
            try:
                recent = abs(time.time() - int(stamp)) <= 300
            except ValueError:
                recent = False
            expected = hashlib.sha256(stamp.encode() + nonce.encode() + encrypt_key.encode() + raw).hexdigest()
            if not recent or not hmac.compare_digest(expected, sig):
                raise Problem("CALLBACK_SIGNATURE_INVALID", "回调签名不正确或已过期", 401)
        if payload.get("encrypt"):
            if not encrypt_key:
                raise Problem("CONFIG_MISSING", "收到加密回调，请配置 Encrypt Key", 401)
            payload = decrypt_feishu(encrypt_key, payload["encrypt"])
        if payload.get("type") == "url_verification" and isinstance(payload.get("challenge"), str):
            # 公网 URL 验证也必须提供当前连接的 Token，不能因为是 challenge 就跳过认证。
            if not c.get("verification_token") or not isinstance(payload.get("token"), str) or not hmac.compare_digest(c["verification_token"], payload["token"]):
                raise Problem("CALLBACK_TOKEN_INVALID", "Verification Token 缺失或不匹配", 401)
            return {"challenge": payload["challenge"]}
        header = payload.get("header", {})
        supplied = header.get("token", payload.get("token", ""))
        if not c.get("verification_token") or not hmac.compare_digest(c["verification_token"], supplied):
            raise Problem("CALLBACK_TOKEN_INVALID", "Verification Token 缺失或不匹配", 401)
        if header.get("event_type", payload.get('event_type')) == "card.action.trigger":
            return self.cards.callback(id, payload)
        if header.get("event_type") != "im.message.receive_v1":
            return {"ok": True, "ignored": True}
        event = payload.get("event", {})
        message = event.get("message", {})
        if event.get("sender", {}).get("sender_type") == "app":
            return {"ok": True, "ignored": True}
        event_id = text(header.get("event_id"), "event_id")
        message_id = text(message.get("message_id"), "message_id")
        chat_id = text(message.get("chat_id"), "chat_id")
        try:
            content = json.loads(message.get("content", "{}"))
        except ValueError:
            content = {"unparsed": True}
        value = {"event_id": event_id, "message_id": message_id, "conversation_id": chat_id,
                 "sender_id": event.get("sender", {}).get("sender_id", {}).get("open_id", ""),
                 "message_type": message.get("message_type", "unknown"), "content": content, "received": now()}
        seq = self.store.add_event(id, event_id, value)
        return {"ok": True, "seq": seq}

    def tools(self):
        """为内置 CLI 生成命令契约目录，追加 connection_ref 和写操作 request_id。
        复制业务 Schema，避免修改 Agent 看到的无平台字段的原始定义。"""
        tools = []
        for name, schema in SCHEMAS.items():
            schema = json.loads(dumps(schema))
            schema["properties"]["connection_ref"] = STRING
            schema["required"].append("connection_ref")
            if name in ("feishu_send", "feishu_reply"):
                schema["properties"]["request_id"] = STRING
                schema["required"].append("request_id")
            tools.append({"name": name, "description": DESCRIPTIONS[name], "inputSchema": schema})
        return tools

    def call(self, name, arguments):
        """验证内置命令参数后按名称分发；账号选择由 connection_ref 决定。
        读取事件不自动确认；processed 需要已有成功回复回执，忽略须显式选择。"""
        tool = next((t for t in self.tools() if t["name"] == name), None)
        if not tool:
            raise Problem("TOOL_NOT_FOUND", "没有此功能", 404)
        validate(arguments, tool["inputSchema"])
        a = dict(arguments)
        id = a.pop("connection_ref")
        self.require(id)
        request_id = a.pop("request_id", "")
        if name == 'feishu_sync_risk_card':
            raise Problem('AGENT_SCOPE_REQUIRED', '卡片需通过已授权Agent绑定调用，或平台受控刷新队列执行', 403)
        if name == "feishu_health":
            return self.health(id)
        if name == "lookup_feishu_directory":
            if not a.get("name") and not a.get("id"):
                config = self.config(id)
                return {"directory_source": "configured", "entries": config["groups" if a["entity_type"] == "group" else "users"]}
            target = {"type": a["entity_type"], **{k: a[k] for k in ("name", "id") if a.get(k)}}
            return self.resolve(id, target)
        if name == "feishu_send":
            return self.send(id, a["target"], a["text"], request_id)
        if name == "feishu_events":
            return {"events": self.store.events(id, a.get("after", 0), a.get("limit", 50))}
        if name == "feishu_reply":
            return self.send(id, {}, a["text"], request_id, a["event_id"])
        if name == "feishu_ack":
            if a["status"] == "processed":
                with self.store.db() as c:
                    replies = [json.loads(r[0]) for r in c.execute("SELECT result FROM receipts")]
                if not any(r.get("event_id") == a["event_id"] and r.get("connection_id") == id and r.get("status") == "sent" for r in replies):
                    raise Problem("ACK_REQUIRES_REPLY", "尚无成功回复回执；如无需回复请明确忽略事件", 409)
            return self.store.ack(id, a["event_id"], a["status"])
