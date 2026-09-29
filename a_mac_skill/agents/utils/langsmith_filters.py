"""LangSmith 追踪数据过滤器"""

import re
from typing import Any


class LangSmithFilter:
    """LangSmith 追踪数据过滤器"""

    # 敏感字段模式
    SENSITIVE_PATTERNS = [
        (r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]+', '[API_KEY]'),
        (r'token["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]+', '[TOKEN]'),
        (r'password["\']?\s*[:=]\s*["\']?[^\s"\']+', '[PASSWORD]'),
        (r'bearer["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_-]+', '[BEARER_TOKEN]'),
    ]

    @classmethod
    def filter_text(cls, text: str) -> str:
        """
        过滤文本中的敏感信息

        Args:
            text: 原始文本

        Returns:
            过滤后的文本
        """
        if not text:
            return text

        result = text
        for pattern, replacement in cls.SENSITIVE_PATTERNS:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        return result

    @classmethod
    def truncate(cls, text: str, max_length: int = 10000) -> str:
        """
        截断过长文本

        Args:
            text: 原始文本
            max_length: 最大长度

        Returns:
            截断后的文本
        """
        if not text:
            return text
        if len(text) <= max_length:
            return text
        return text[:max_length] + f"\n... [truncated {len(text) - max_length} chars]"

    @classmethod
    def filter_dict(cls, data: dict) -> dict:
        """
        过滤字典中的敏感字段

        Args:
            data: 原始字典

        Returns:
            过滤后的字典
        """
        if not data:
            return data

        result = {}
        sensitive_keys = {'api_key', 'token', 'password', 'secret', 'bearer', 'authorization'}

        for key, value in data.items():
            if any(s in key.lower() for s in sensitive_keys):
                result[key] = '[REDACTED]'
            elif isinstance(value, str):
                result[key] = cls.filter_text(value)
            elif isinstance(value, dict):
                result[key] = cls.filter_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    cls.filter_dict(v) if isinstance(v, dict) else cls.filter_text(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                result[key] = value

        return result