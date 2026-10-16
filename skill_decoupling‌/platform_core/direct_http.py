"""外部 HTTP 能力注册与调用；管理员声明地址，模型只提供业务参数。

本模块不实现 MCP，不启动服务。绑定记录保存契约版本，改动后需重新确认。
请求幂等标识由平台注入；是否真正支持幂等由外部服务契约决定。
"""
from .common import Problem, check_schema, digest, text, uid, url, validate
from .http_client import request


class DirectHttp:
    """维护外部直连定义，使用统一 HTTP 客户端和私有凭据引用。"""

    def __init__(self, store):
        """仅引用平台存储，不共享可变 HTTP 会话。"""
        self.store = store

    def public(self, row):
        """前端可见契约与令牌配置状态，不回显凭据引用或值。"""
        return {k: v for k, v in row.items() if k != "secret_ref"} | {
            "token_configured": bool(self.store.secret(row["secret_ref"]))}

    def save(self, data, id=None, preview=False):
        """登记单个 JSON HTTP 操作；路径和方法固定，不接受模型动态 URL。"""
        old = self.store.get("http_services", id) if id else {}
        id = id or uid()
        method = data.get("method", old.get("method", "POST"))
        if method not in ("GET", "POST"):
            raise Problem("PARAMETER_INVALID", "直连当前仅支持 GET/POST")
        schema = data.get("input_schema", old.get("input_schema", {"type": "object", "properties": {}, "additionalProperties": False}))
        output = data.get("output_schema", old.get("output_schema", {"type": "object", "properties": {}, "additionalProperties": True}))
        check_schema(schema)
        check_schema(output)
        timeout = int(data.get("timeout", old.get("timeout", 15)))
        if not 1 <= timeout <= 30:
            raise Problem("PARAMETER_INVALID", "HTTP 超时需在 1–30 秒之间")
        row = {"name": text(data.get("name", old.get("name")), "能力名称", 80),
               "description": text(data.get("description", old.get("description", "HTTP 外部能力")), "用途", 2000),
               "endpoint": url(data.get("endpoint", old.get("endpoint"))), "method": method,
               "input_schema": schema, "output_schema": output, "timeout": timeout,
               "enabled": bool(data.get("enabled", old.get("enabled", True))), "secret_ref": "direct:" + id,
               "revision": old.get("revision", 0) + 1}
        if preview: return {k:v for k,v in row.items() if k != "secret_ref"}
        if data.get("token"):
            self.store.secret(row["secret_ref"], data["token"])
        if data.get("clear_token"):
            self.store.secret(row["secret_ref"], "")
        return self.public(self.store.put("http_services", row, id))

    def call(self, id, arguments, revision, run_id):
        """重新校验启用状态与契约，GET 映射查询参数、POST 映射 JSON body。"""
        row = self.store.get("http_services", id)
        if not row["enabled"] or row["revision"] != revision:
            raise Problem("BINDING_STALE", "直连能力已停用或修订，请重新确认绑定", 409)
        validate(arguments, row["input_schema"])
        token = self.store.secret(row["secret_ref"])
        headers = {"Idempotency-Key": digest([run_id, id, arguments])}
        if token:
            headers["Authorization"] = "Bearer " + token
        endpoint, payload = row["endpoint"], arguments
        if row["method"] == "GET":
            from urllib.parse import urlencode
            if any(isinstance(v, (dict, list)) for v in arguments.values()):
                raise Problem("PARAMETER_INVALID", "GET 查询参数仅支持标量")
            endpoint += ("&" if "?" in endpoint else "?") + urlencode(arguments)
            payload = None
        value = request(endpoint, payload, headers, method=row["method"], timeout=row["timeout"])
        validate(value, row["output_schema"])
        return value
