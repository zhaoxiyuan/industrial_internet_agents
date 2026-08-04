"""
A5 智能体配置加载模块
====================

职责:
  - 从 .env 读取模型 API 等敏感信息(用 python-dotenv)
  - 工厂函数 create_llm() / create_vl_llm() 根据协议类型返回对应 ChatModel
  - 统一管理超时、温度、max_tokens 等参数
  - 不在文件里直接硬编码任何 key

支持的协议(只有两类):
  - OpenAI / OpenAI 兼容   → langchain_openai.ChatOpenAI(/chat/completions 协议)
  - Anthropic Claude       → langchain_anthropic.ChatAnthropic(Messages API)

无论 .env 里写的是 OpenAI、DeepSeek、DashScope、火山方舟、GLM、Kimi 还是
OneAPI 转发到 Claude,只要它暴露的是 OpenAI Chat Completions 形态,
就用 ChatOpenAI;只有真的直连 Anthropic 时才用 ChatAnthropic。

向后兼容:
  - 历史 .env 可能用的是 A5_LLM_PROVIDER 字段(值可能是 dashscope / openai / ...)，
    本文件会自动映射:仅当值是 'anthropic' 时走 Anthropic,其它一律走 OpenAI 兼容。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import find_dotenv, load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

# ============================================================
# .env 加载
# ============================================================

def _load_env() -> None:
    here = Path(__file__).resolve().parent
    local_env = here / ".env"
    if local_env.exists():
        load_dotenv(local_env, override=True)
    else:
        env_path = find_dotenv(filename=".env", raise_error_if_not_found=False)
        if env_path:
            load_dotenv(env_path, override=False)
        else:
            load_dotenv(override=False)


_load_env()


# ============================================================
# 工具函数
# ============================================================

def _get_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"[A5 配置错误] 环境变量 {name} 未设置。"
            f"请在 {Path(__file__).resolve().parent}/.env 中配置。"
        )
    return value


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


# ============================================================
# 协议常量 + 判定
# ============================================================

PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"
VALID_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)


def resolve_protocol(protocol_var: str, legacy_provider_var: Optional[str] = None) -> str:
    """
    判定协议:
      1) 优先读 *_PROTOCOL(新字段,值 'openai' / 'anthropic')
      2) 否则读 *_PROVIDER(旧字段),仅当值是 'anthropic' 时返回 anthropic,其它一律 openai
      3) 都没设则默认 openai
    """
    p = os.getenv(protocol_var, "").strip().lower()
    if p in VALID_PROTOCOLS:
        return p
    if legacy_provider_var:
        legacy = os.getenv(legacy_provider_var, "").strip().lower()
        if legacy == "anthropic":
            return PROTOCOL_ANTHROPIC
    return PROTOCOL_OPENAI


# ============================================================
# 工厂
# ============================================================

def _build_chat_model(
    *,
    protocol: str,
    model: str,
    api_key: str,
    base_url: Optional[str],
    temperature: float,
    max_tokens: int,
    timeout: float,
    extra: Optional[Dict[str, Any]] = None,
) -> Any:
    """根据协议构造对应 ChatModel。"""
    if protocol == PROTOCOL_ANTHROPIC:
        kwargs: Dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "timeout": timeout,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if base_url:
            kwargs["anthropic_api_url"] = base_url
        if extra:
            kwargs.update(extra)
        return ChatAnthropic(**kwargs)

    # 默认 OpenAI / OpenAI 兼容
    openai_kwargs: Dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url or None,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    if extra:
        openai_kwargs.update(extra)
    return ChatOpenAI(**openai_kwargs)


def create_llm(
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: float = 30.0,
    **extra: Any,
):
    """
    创建 A5 推理用的主 LLM。

    根据 A5_LLM_PROTOCOL(向后兼容 A5_LLM_PROVIDER)选择:
      - "openai"     → ChatOpenAI(OpenAI Chat Completions 协议)
      - "anthropic"  → ChatAnthropic(Messages API)
    """
    protocol = resolve_protocol("A5_LLM_PROTOCOL", "A5_LLM_PROVIDER")
    return _build_chat_model(
        protocol=protocol,
        model=model or os.getenv("A5_LLM_MODEL", "qwen-plus"),
        api_key=_get_required("A5_LLM_API_KEY"),
        base_url=os.getenv("A5_LLM_BASE_URL") or None,
        temperature=temperature if temperature is not None else _get_float("A5_LLM_TEMPERATURE", 0.2),
        max_tokens=max_tokens if max_tokens is not None else _get_int("A5_LLM_MAX_TOKENS", 2048),
        timeout=timeout,
        extra=extra or None,
    )


def create_vl_llm(
    *,
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    timeout: float = 60.0,
    **extra: Any,
):
    """创建 VL 模型(图像语义)。支持 OpenAI 与 Anthropic 协议。"""
    protocol = resolve_protocol("A5_VL_PROTOCOL", "A5_VL_PROVIDER")
    api_key = (
        os.getenv("A5_VL_API_KEY")
        or os.getenv("A5_LLM_API_KEY")
        or _get_required("A5_VL_API_KEY")
    )
    return _build_chat_model(
        protocol=protocol,
        model=model or os.getenv("A5_VL_MODEL", "qwen-vl-max"),
        api_key=api_key,
        base_url=os.getenv("A5_VL_BASE_URL") or None,
        temperature=temperature,
        max_tokens=max_tokens if max_tokens is not None else _get_int("A5_LLM_MAX_TOKENS", 2048),
        timeout=timeout,
        extra=extra or None,
    )


# ============================================================
# 公开配置项
# ============================================================

class A5Config:
    """A5 智能体的运行配置(从 .env 读取)。"""

    LLM_PROTOCOL: str = resolve_protocol("A5_LLM_PROTOCOL", "A5_LLM_PROVIDER")
    VL_PROTOCOL:  str = resolve_protocol("A5_VL_PROTOCOL",  "A5_VL_PROVIDER")
    LLM_MODEL: str = os.getenv("A5_LLM_MODEL", "qwen-plus")
    VL_MODEL:  str = os.getenv("A5_VL_MODEL",  "qwen-vl-max")

    USE_MOCK_VL: bool = _get_bool("A5_USE_MOCK_VL", True)
    DEBUG:       bool = _get_bool("A5_DEBUG", False)

    DECISION_CYCLE_SEC:        float = _get_float("A5_DECISION_CYCLE_SEC", 1.0)
    VL_TRIGGER_THRESHOLD_SEC:  float = _get_float("A5_VL_TRIGGER_THRESHOLD_SEC", 3.0)


config = A5Config()


__all__ = [
    "PROTOCOL_OPENAI",
    "PROTOCOL_ANTHROPIC",
    "VALID_PROTOCOLS",
    "resolve_protocol",
    "create_llm",
    "create_vl_llm",
    "config",
    "A5Config",
]
