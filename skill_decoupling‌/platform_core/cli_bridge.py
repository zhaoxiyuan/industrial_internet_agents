"""Runtime 到真实 CLI 进程的桥接；不拼接 shell，不将密钥放进命令行。

服务签发短期、一次性的调用票据，绑定真实 Agent、工具和参数。
子进程通过标准输入获取票据，经 HTTP 返回平台完成授权调用。
CLI 无权直接写数据库；凭据和业务幂等由平台业务层维护。
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time

from .common import Problem, digest, dumps


class CliBridge:
    """签发和核销能力票据，统一记录真实命令执行结果。"""

    def __init__(self, service):
        """票据仅驻留本次平台进程；重启后自动失效，不保存到公开配置。"""
        self.service = service
        self.endpoint = ""
        self.lock = threading.Lock()
        self.tickets = {}

    def invoke(self, agent_id, item, arguments, run_id):
        """以 argv 启动当前 Python 的 CLI，限制运行时间并校验 stdout JSON。

        一次票据只允许执行固定调用；超时不自动重试，避免重复发送消息。
        子进程 stderr 不向模型公开，异常只报告稳定且脱敏的错误。
        """
        if not self.endpoint:
            raise Problem("CLI_UNAVAILABLE", "平台 HTTP 服务尚未启动", 409)
        ticket = secrets.token_urlsafe(32)
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items() if v["expires"] > time.monotonic()}
            self.tickets[ticket] = {"agent_id": agent_id, "item": item, "fingerprint": digest(arguments),
                                    "run_id": run_id, "expires": time.monotonic() + 65}
        # 固定 CLI 文件与命令；模型只能提供已通过契约校验的 JSON 参数。
        try:
            process = subprocess.run([sys.executable, str(self.service.root / "cli.py"),
                                      "--endpoint", self.endpoint, "invoke"],
                                     input=dumps({"ticket": ticket, "arguments": arguments}),
                                     capture_output=True, text=True, encoding="utf-8", timeout=60,
                                     env={**os.environ, "PYTHONUTF8": "1"},
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value = json.loads(process.stdout)
            if not isinstance(value, dict) or "ok" not in value:
                raise ValueError()
            if not value["ok"] and value.get("error"):
                error = value["error"]
                raise Problem(error.get("code", "CLI_FAILED"), error.get("message", "CLI 执行失败"), 409)
            return value
        except subprocess.TimeoutExpired:
            raise Problem("CLI_TIMEOUT", "命令等待超时，外部动作结果需核对", 504)
        except (ValueError, OSError):
            raise Problem("CLI_INVALID_RESPONSE", "CLI 未返回有效 JSON 回执", 502)
        finally:
            with self.lock:
                self.tickets.pop(ticket, None)

    def consume(self, ticket, arguments):
        """HTTP 入口原子核销票据后再执行，拒绝重放与参数替换。"""
        with self.lock:
            grant = self.tickets.pop(ticket, None)
        if not grant or grant["expires"] <= time.monotonic() or grant["fingerprint"] != digest(arguments):
            raise Problem("CLI_AUTH_FAILED", "调用票据无效、过期或参数不匹配", 401)
        return self.service.agents.dispatch(grant["agent_id"], grant["item"], arguments, grant["run_id"])
