"""
共享工具模块
"""
import json
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"


def extract_output(result: dict) -> str:
    """从 agent 返回结果中提取最终回复内容"""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content:
            return msg.content
    return ""


def make_response(command: str, result: Any, errors: list = None) -> dict:
    """构建标准 JSON 响应"""
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "errors": errors or [],
    }


def make_error(code: str, message: str, details: dict = None,
               recoverable: bool = False, action: str = "") -> dict:
    """构建标准错误响应"""
    return {
        "schema_version": SCHEMA_VERSION,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "recoverable": recoverable,
            "action": action,
        },
    }


def load_json_file(file_path: str) -> dict:
    """加载 JSON 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def echo_json(data: dict, quiet: bool = False) -> None:
    """输出 JSON 到 stdout"""
    if not quiet:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def echo_text(text: str) -> None:
    """输出文本到 stdout"""
    print(text)


def build_context_output(task_id: str, context: dict, completeness: str,
                          missing_fields: list) -> dict:
    """构建 context build 响应"""
    return {
        "task_id": task_id,
        "context": context,
        "completeness": completeness,
        "missing_fields": missing_fields,
    }
