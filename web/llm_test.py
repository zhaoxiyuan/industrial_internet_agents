"""
LLM 连通性测试
"""
import requests
import logging

logger = logging.getLogger("server")


def test_llm_connection(data):
    """测试 LLM 连通性，返回 ok + reply 或 ok=False + error"""
    protocol = (data.get("protocol") or "openai").strip().lower()
    base_url = (data.get("base_url") or "").strip()
    api_key  = (data.get("api_key")  or "").strip()
    model    = (data.get("model")    or "").strip()
    if not base_url or not api_key or not model:
        return {"ok": False, "error": "base_url、api_key、model 均不能为空"}
    try:
        if protocol == "anthropic":
            url = base_url.rstrip("/") + "/v1/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {"messages": [{"role": "user", "content": "请只回复:OK"}], "max_tokens": 20}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
        else:
            url = base_url.rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"model": model, "messages": [{"role": "user", "content": "请只回复:OK"}], "max_tokens": 20}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        result = resp.json()
        # 提取回复
        reply = None
        if protocol == "anthropic":
            for block in (result.get("content") or []):
                if isinstance(block, dict) and block.get("type") == "text":
                    reply = block.get("text", "").strip()
        else:
            choices = result.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                reply = msg.get("content", "").strip()
        return {"ok": True, "reply": reply or "(空)", "protocol": protocol.upper()}
    except requests.RequestException as e:
        return {"ok": False, "error": f"网络异常: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
