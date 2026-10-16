"""统一远程 HTTP 请求：JSON 编解码、超时、响应限制和脱敏错误。

禁用自动重定向，防止 Bearer token 被带到另一个地址；本机服务绕过系统代理。
POST 超时不能推断写入失败，因此向上层返回结果未知而不是自动重试。"""
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .common import Problem, dumps, url


class NoRedirect(HTTPRedirectHandler):
    """禁止 urllib 自动跟随 30x，确保认证头不随跳转传播。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """返回 None，让 urllib 把跳转作为 HTTPError 交给统一错误处理。"""
        return None


def request(endpoint, payload=None, headers=None, method="POST", timeout=15, raw=False):
    """发送单次请求，默认 POST JSON；payload=None 不发送请求体。
    raw=True 返回字节、响应头和 HTTP 状态供 MCP 解析 SSE，否则返回 JSON。
    限制响应为 8MB，超时由调用方指定，错误响应只公开状态码。"""
    url(endpoint)
    headers = dict(headers or {})
    if payload is not None:
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = Request(endpoint, data=dumps(payload).encode() if payload is not None else None,
                  headers=headers, method=method)
    # 只对 loopback 绕过系统代理，保证本地 MCP 和测试服务不会被代理转发。
    from urllib.parse import urlsplit
    local = urlsplit(endpoint).hostname in ("127.0.0.1", "localhost", "::1")
    opener = build_opener(*([ProxyHandler({})] if local else []), NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as reply:
            data = reply.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise Problem("RESPONSE_LIMIT", "服务响应过大", 502)
            if raw:
                return data, dict(reply.headers), reply.status
            return json.loads(data) if data else {}
    except HTTPError as e:
        # 错误响应可能包含凭据或模型输入，仅公开状态码，不回显远端原始请求/响应。
        code = "AUTH_FAILED" if e.code in (401, 403) else "UPSTREAM_ERROR"
        raise Problem(code, f"远端返回 HTTP {e.code}", 502, {"http_status": e.code})
    except (TimeoutError, socket.timeout):
        raise Problem("RESULT_UNKNOWN" if method == "POST" else "SERVICE_UNAVAILABLE", "网络请求超时，尚不能确认结果", 504)
    except URLError:
        raise Problem("SERVICE_UNAVAILABLE", "无法连接服务，请检查地址与网络", 503)
    except (ValueError, UnicodeDecodeError):
        raise Problem("UPSTREAM_INVALID", "服务没有返回有效 JSON", 502)
