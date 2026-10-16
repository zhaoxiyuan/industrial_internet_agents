import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import ToolError


class Client:
    def __init__(self, server, timeout=10):
        self.server, self.timeout = server.rstrip("/"), timeout

    def call(self, path, method="GET", body=None, params=None):
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.server + path + ("?" + query if query else "")
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if body is not None else None
        try:
            with urlopen(Request(url, data=data, method=method, headers={"Content-Type": "application/json"}), timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            try: error = json.load(exc).get("error", {})
            except Exception: error = {}
            raise ToolError(error.get("message", f"HTTP {exc.code}"), exc.code, error.get("code", "http_error"))
        except (URLError, TimeoutError, OSError) as exc:
            raise ToolError(str(exc), 503, "unreachable")
