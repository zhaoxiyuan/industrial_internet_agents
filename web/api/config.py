"""
配置和提示词 API
"""
import os
import logging

from agents.model.config import get_settings, get_llm_params

logger = logging.getLogger("server")

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")



def load_env_config():
    """读取 .env 完整配置"""
    config = {"api_key": "", "base_url": "", "model": "",
              "protocol": "openai", "temperature": "", "max_tokens": ""}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        key, val = line.split("=", 1)
                        val = val.strip().strip('"').strip("'")
                        if key == "OPENAI_API_KEY":
                            config["api_key"] = val
                        elif key == "OPENAI_BASE_URL":
                            config["base_url"] = val
                        elif key == "OPENAI_MODEL":
                            config["model"] = val
                        elif key == "OPENAI_PROVIDER":
                            config["protocol"] = val
                        elif key == "OPENAI_TEMPERATURE":
                            config["temperature"] = val
                        elif key == "OPENAI_MAX_TOKENS":
                            config["max_tokens"] = val
    return config


def save_env_config(data):
    """保存完整配置到 .env"""
    lines = [
        "# OpenAI Configuration",
        f"OPENAI_API_KEY={data.get('api_key', '')}",
        f"OPENAI_BASE_URL={data.get('base_url', '')}",
        f"OPENAI_MODEL={data.get('model', '')}",
        f"OPENAI_PROVIDER={data.get('protocol', 'openai')}",
        "MODEL_PROVIDER=openai",
    ]
    temp = data.get("temperature", "").strip()
    maxt = data.get("max_tokens", "").strip()
    if temp:
        lines.append(f"OPENAI_TEMPERATURE={temp}")
    if maxt:
        lines.append(f"OPENAI_MAX_TOKENS={maxt}")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def handle_config_get(handler):
    config = load_env_config()
    logger.info(f'[GET] /api/config 响应: config={{"api_key": "***", "base_url": "{config.get("base_url")}", "model": "{config.get("model")}"}}')
    handler.send_json(config)


def handle_config_post(handler, data):
    logger.info(f"[POST] /api/config 进入: data={{k:v for k,v in data.items() if k != 'api_key'}}")
    save_env_config(data)
    logger.info(f"[POST] /api/config 响应: status=ok")
    handler.send_json({"status": "ok"})


def handle_test_llm_post(handler, data):
    from web.llm_test import test_llm_connection
    logger.info(f"[POST] /api/test/llm 进入: protocol={data.get('protocol')}, base_url={data.get('base_url')}, model={data.get('model')}")
    result = test_llm_connection(data)
    logger.info(f"[POST] /api/test/llm 响应: ok={result.get('ok')}")
    handler.send_json(result)


def handle_prompt_get(handler, stage):
    from agents.utils.system_prompt import load_system_prompt
    prompt = load_system_prompt(stage)
    logger.info(f"[GET] /api/prompt/{stage} 响应: content_length={len(prompt)}")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    body = prompt.encode("utf-8")
    handler.send_header("Content-Length", len(body))
    handler.end_headers()
    handler.wfile.write(body)


def handle_prompt_post(handler, stage, data):
    from agents.utils.system_prompt import save_system_prompt
    logger.info(f"[POST] /api/prompt/{stage} 进入: content_length={len(data.get('content', ''))}")
    save_system_prompt(stage, data.get("content", ""))
    logger.info(f"[POST] /api/prompt/{stage} 响应: status=ok")
    handler.send_json({"status": "ok"})