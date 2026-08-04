"""
A5 智能体 — 配置管理 Web UI 后端 (Flask)
========================================

常用命令:
    # 启动(默认 5000 端口)
    python app.py

    # 指定端口
    python app.py --port 8080

    # 允许局域网访问
    python app.py --host 0.0.0.0 --port 5000

    # 停止已运行的服务
    python app.py --stop

    # 打开浏览器
    http://127.0.0.1:5000

职责:
  - GET  /                → 返回 index.html
  - GET  /api/active      → 读取 .env 中"激活"的配置(LLM+VL)
  - GET  /api/configs     → 列出所有"已保存"的命名配置
  - POST /api/configs     → 把当前表单内容"保存为命名配置"并立即激活
  - POST /api/configs/<n>/activate  → 把名为 n 的配置激活(写入 .env)
  - DELETE /api/configs/<n>         → 删除名为 n 的命名配置
  - POST /api/test/llm    → 文本连通性测试
  - POST /api/test/vl     → 发图连通性测试

⚠  本文件现在直接读写 .env(A5 唯一一处)。后续其它任何模块都不应直接修改 .env。
   所有"激活 / 保存"操作都通过本服务提供的 API 进行,以保证索引文件一致。
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request, send_from_directory

# ============================================================
# 路径常量
# ============================================================

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"               # 当前激活配置(flat)
CONFIGS_FILE = HERE / "saved_configs.json"   # 所有命名配置

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("a5-config")


# ============================================================
# 字段 schema(LLM / VL 共用结构,值不同)
# ============================================================
# 用 protocol 取代旧的 provider,值只能为 'openai' / 'anthropic'。
# 'openai' 同时覆盖 OpenAI 及所有 Chat-Completions 兼容服务(DeepSeek/DashScope/GLM/Kimi/vLLM 等)。

LLM_FIELDS = ["protocol", "base_url", "api_key", "model", "temperature", "max_tokens"]
VL_FIELDS  = ["protocol", "base_url", "api_key", "model"]
ALL_FIELDS = sorted(set(LLM_FIELDS + VL_FIELDS))

ENV_VAR_NAMES = {
    # 主 LLM
    "protocol":    "A5_LLM_PROTOCOL",
    "base_url":    "A5_LLM_BASE_URL",
    "api_key":     "A5_LLM_API_KEY",
    "model":       "A5_LLM_MODEL",
    "temperature": "A5_LLM_TEMPERATURE",
    "max_tokens":  "A5_LLM_MAX_TOKENS",
    # VL
}
# 重新映射:VL 用 VL_ 前缀
VL_ENV_VAR_NAMES = {k: v.replace("A5_LLM_", "A5_VL_") for k, v in ENV_VAR_NAMES.items() if v.startswith("A5_LLM_")}

# 协议预设(选择后自动填 base_url — 只填最常用默认,用户可按需改)
PROTOCOL_PRESETS: Dict[str, str] = {
    # OpenAI 兼容协议:不做预设(各家 base_url 差异太大)
    "openai":      "",
    # Anthropic Claude(Messages API)
    "anthropic":   "https://api.anthropic.com",
}
# 常用厂商 base_url 速查 — 按协议分类
# OpenAI 协议: URL 需包含 /v1 前缀,后端会 append /chat/completions
# Anthropic 协议: URL 不含 /v1,后端会 append /v1/messages
PROVIDER_HINTS: Dict[str, Dict[str, str]] = {
    "openai": {
        "MiniMax 中国站":       "https://api.minimaxi.com/v1",
        "MiniMax 国际站":       "https://api.minimax.chat/v1",
        "阿里百炼 DashScope":    "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DeepSeek":             "https://api.deepseek.com/v1",
        "火山方舟 Volcengine":   "https://ark.cn-beijing.volces.com/api/v3",
        "智谱 GLM":             "https://open.bigmodel.cn/api/paas/v4",
        "月之暗面 Kimi":         "https://api.moonshot.cn/v1",
        "OpenAI 官方":           "https://api.openai.com/v1",
        "Ollama 本地":           "http://localhost:11434/v1",
        "vLLM / OneAPI / 自建":  "",
    },
    "anthropic": {
        "Anthropic 官方":               "https://api.anthropic.com",
        "DeepSeek":                     "https://api.deepseek.com/anthropic",
        "阿里百炼 DashScope":             "https://dashscope.aliyuncs.com/apps/anthropic",
        "智谱 GLM (Z.AI)":              "https://api.z.ai/api/anthropic",
        "月之暗面 Kimi":                  "https://api.moonshot.ai/anthropic",
        "MiniMax 官方":                  "https://api.minimax.io/anthropic",
        "火山方舟 Coding Plan":           "https://ark.cn-beijing.volces.com/api/coding",
        "OpenRouter":                    "https://openrouter.ai/api",
        "OneAPI / New-API 转发":          "",
    },
}

# ============================================================
# .env 读写
# ============================================================

def read_env() -> Dict[str, str]:
    """读 .env 为 flat dict,无文件返回空 dict。自动忽略行内注释与空行。"""
    out: Dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        # 去掉外层包裹引号
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _normalize_config(cfg: Dict[str, str]) -> Dict[str, str]:
    """
    把可能来自旧格式(config 含 'provider' 而非 'protocol')的数据归一化。
    新格式始终用 'protocol' 字段。
    """
    c = dict(cfg)
    # 将旧 'provider' 字段映射为新 'protocol'
    if "protocol" not in c or not c.get("protocol"):
        old_provider = (c.get("provider") or c.get("A5_LLM_PROVIDER") or "").strip().lower()
        c["protocol"] = "anthropic" if old_provider == "anthropic" else "openai"
    # 删除旧 key 避免写入 .env 时污染
    c.pop("provider", None)
    return c


def write_env(llm: Dict[str, str], vl: Dict[str, str], active_name: str = "") -> None:
    """把 LLM + VL 当前值写回 .env(覆盖同名变量,写入前自动归一化)。"""
    llm = _normalize_config(llm)
    vl  = _normalize_config(vl)

    lines: List[str] = []
    lines.append("# ============================================================")
    lines.append("# A5 智能体 — 当前激活的模型配置")
    if active_name:
        lines.append(f"# 激活的配置名: {active_name}")
    lines.append("# 由 智能体配置/app.py Web UI 写入,请勿手工编辑")
    lines.append("# ============================================================")
    lines.append("")

    # ---- LLM ----
    lines.append("# ===== 主推理 LLM =====")
    for field in LLM_FIELDS:
        var = ENV_VAR_NAMES[field]
        val = (llm.get(field) or "").strip()
        if val:
            lines.append(f'{var}="{val}"')
    lines.append("")

    # ---- VL ----
    lines.append("# ===== 视觉模型 VL =====")
    for field in VL_FIELDS:
        var = VL_ENV_VAR_NAMES[field]
        val = (vl.get(field) or "").strip()
        if val:
            lines.append(f'{var}="{val}"')
    lines.append("")

    # ---- 运行控制 ----
    lines.append("# ===== 运行控制 =====")
    runtime_defaults = {
        "A5_DEBUG": "false",
        "A5_USE_MOCK_VL": "true",
        "A5_DECISION_CYCLE_SEC": "1.0",
        "A5_VL_TRIGGER_THRESHOLD_SEC": "3.0",
    }
    current = read_env()
    for k in runtime_defaults:
        val = current.get(k, "") or runtime_defaults[k]
        lines.append(f'{k}="{val}"')
    lines.append("")

    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")


def active_to_ui() -> Dict[str, Any]:
    """把当前 .env 转成 UI 期望的 {llm:{...}, vl:{...}} 结构"""
    env = read_env()
    # ----- LLM -----
    llm: Dict[str, str] = {}
    for f in LLM_FIELDS:
        if f == "protocol":
            p = env.get("A5_LLM_PROTOCOL", "").strip().lower()
            if p in DEFAULT_PROTOCOLS:
                llm[f] = p
            else:
                # 向后兼容老 .env 的 A5_LLM_PROVIDER
                legacy = env.get("A5_LLM_PROVIDER", "").strip().lower()
                llm[f] = "anthropic" if legacy == "anthropic" else "openai"
        else:
            llm[f] = env.get(ENV_VAR_NAMES[f], "")
    # ----- VL -----
    vl: Dict[str, str] = {}
    for f in VL_FIELDS:
        if f == "protocol":
            p = env.get("A5_VL_PROTOCOL", "").strip().lower()
            if p in DEFAULT_PROTOCOLS:
                vl[f] = p
            else:
                legacy = env.get("A5_VL_PROVIDER", "").strip().lower()
                vl[f] = "anthropic" if legacy == "anthropic" else "openai"
        else:
            vl[f] = env.get(VL_ENV_VAR_NAMES[f], "")
    return {"llm": llm, "vl": vl}


# ============================================================
# saved_configs.json 读写
# ============================================================

def list_saved() -> Dict[str, Any]:
    """读 saved_configs.json,文件不存在返回 {"active":"","configs":{}}"""
    active = ""
    cfgs: Dict[str, Dict] = {}
    if CONFIGS_FILE.exists():
        try:
            data = json.loads(CONFIGS_FILE.read_text(encoding="utf-8"))
            active = data.get("active", "")
            cfgs = data.get("configs", {})
        except Exception as e:
            log.warning("saved_configs.json 解析失败: %s,重置", e)
    return {"active": active, "configs": cfgs}


def write_saved(active: str, configs: Dict[str, Dict]) -> None:
    CONFIGS_FILE.write_text(
        json.dumps({"active": active, "configs": configs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 测试 API — 按协议分发
# ============================================================

def _normalize_protocol(p: str) -> str:
    p = (p or "").strip().lower()
    if p not in DEFAULT_PROTOCOLS:
        return "openai"
    return p


def _check_required(base_url: str, api_key: str, model: str) -> Optional[Tuple[int, Any]]:
    if not base_url:
        return 400, {"error": "base_url 为空"}
    if not api_key:
        return 401, {"error": "api_key 为空"}
    if not model:
        return 400, {"error": "model 为空"}
    return None


def _post_openai(base_url: str, api_key: str, model: str, body: Dict[str, Any], timeout: int) -> Tuple[int, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {**body, "model": model}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        return 0, {"error": f"网络异常: {e.__class__.__name__}: {e}"}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"raw_text": resp.text}


def _post_anthropic(base_url: str, api_key: str, model: str, body: Dict[str, Any], timeout: int) -> Tuple[int, Any]:
    """POST /v1/messages (Anthropic Messages API)。"""
    url = base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {**body, "model": model}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        return 0, {"error": f"网络异常: {e.__class__.__name__}: {e}"}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"raw_text": resp.text}


def _post_chat(protocol: str, base_url: str, api_key: str, model: str, body: Dict[str, Any], timeout: int = 60) -> Tuple[int, Any]:
    """按协议分发到对应的厂商 API。"""
    err = _check_required(base_url, api_key, model)
    if err:
        return err
    p = _normalize_protocol(protocol)
    if p == "anthropic":
        return _post_anthropic(base_url, api_key, model, body, timeout)
    return _post_openai(base_url, api_key, model, body, timeout)


def _extract_reply(protocol: str, data: Any) -> Optional[str]:
    """从响应中提取文本回复。"""
    if not isinstance(data, dict):
        return None
    p = _normalize_protocol(protocol)
    if p == "anthropic":
        contents = data.get("content") or []
        parts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip() or None
    # OpenAI
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text").strip() or None
        if isinstance(content, str):
            return content.strip() or None
    return None


# ============================================================
# Flask
# ============================================================

app = Flask(__name__, static_folder=None)

# ── 全局错误处理:确保任何错误都返回 JSON,绝不返回 HTML ──

@app.errorhandler(400)
def _e400(e):
    return jsonify({"ok": False, "error": str(e) or "Bad Request"}), 400

@app.errorhandler(404)
def _e404(e):
    return jsonify({"ok": False, "error": "Not Found"}), 404

@app.errorhandler(405)
def _e405(e):
    return jsonify({"ok": False, "error": "Method Not Allowed"}), 405

@app.errorhandler(500)
def _e500(e):
    import traceback
    log.error("500: %s\n%s", e, traceback.format_exc())
    return jsonify({"ok": False, "error": f"服务器内部错误: {e}"}), 500

@app.errorhandler(Exception)
def _eall(e):
    import traceback
    log.error("未捕获异常: %s\n%s", e, traceback.format_exc())
    return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/api/protocol-presets", methods=["GET"])
def get_protocol_presets():
    return jsonify(PROTOCOL_PRESETS)


@app.route("/api/provider-hints", methods=["GET"])
def get_provider_hints():
    proto = (request.args.get("protocol") or "openai").strip().lower()
    if proto not in PROVIDER_HINTS:
        proto = "openai"
    return jsonify(PROVIDER_HINTS[proto])


@app.route("/api/active", methods=["GET"])
def api_active():
    ui = active_to_ui()
    saved = list_saved()
    return jsonify({
        "llm":       ui["llm"],
        "vl":        ui["vl"],
        "active_name": saved["active"],
        "saved":     saved["configs"],
    })


@app.route("/api/configs", methods=["GET"])
def api_list_configs():
    return jsonify(list_saved())


@app.route("/api/configs", methods=["POST"])
def api_save_config():
    """
    Body: {name, llm:{...}, vl:{...}}
    保存到 saved_configs.json + 写入 .env 设为激活。
    """
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "名称不能为空"}), 400
    llm = _normalize_config(body.get("llm") or {})
    vl  = _normalize_config(body.get("vl")  or {})

    saved = list_saved()
    saved["configs"][name] = {
        "llm": llm,
        "vl":  vl,
        "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    saved["active"] = name
    write_saved(saved["active"], saved["configs"])
    write_env(llm, vl, active_name=name)
    log.info("保存并激活: %s", name)
    return jsonify({"ok": True, "active": name, "saved": saved["configs"]})


@app.route("/api/configs/<name>/activate", methods=["POST"])
def api_activate(name: str):
    saved = list_saved()
    if name not in saved["configs"]:
        return jsonify({"ok": False, "error": f"未找到配置: {name}"}), 404
    cfg = saved["configs"][name]
    llm = _normalize_config(cfg.get("llm") or {})
    vl  = _normalize_config(cfg.get("vl")  or {})
    saved["configs"][name] = {"llm": llm, "vl": vl, "saved_at": cfg.get("saved_at", "")}
    saved["active"] = name
    write_saved(saved["active"], saved["configs"])
    write_env(llm, vl, active_name=name)
    log.info("激活: %s", name)
    return jsonify({"ok": True, "active": name})


@app.route("/api/configs/<name>", methods=["DELETE"])
def api_delete_config(name: str):
    saved = list_saved()
    if name not in saved["configs"]:
        return jsonify({"ok": False, "error": "未找到"}), 404
    saved["configs"].pop(name, None)
    if saved["active"] == name:
        saved["active"] = ""
    write_saved(saved["active"], saved["configs"])
    log.info("删除: %s", name)
    return jsonify({"ok": True, "active": saved["active"]})


@app.route("/api/test/llm", methods=["POST"])
def api_test_llm():
    body = request.get_json(force=True) or {}
    protocol = _normalize_protocol(body.get("protocol"))
    base_url = (body.get("base_url") or "").strip()
    api_key  = (body.get("api_key")  or "").strip()
    model    = (body.get("model")    or "").strip()

    if protocol == "anthropic":
        body_payload = {
            "messages": [{"role": "user", "content": "请只回复:OK"}],
            "max_tokens": 20,
        }
    else:
        body_payload = {
            "messages": [{"role": "user", "content": "请只回复:OK"}],
            "max_tokens": 20,
            "temperature": 0.0,
        }

    status, data = _post_chat(protocol, base_url, api_key, model, body_payload, timeout=30)
    reply = _extract_reply(protocol, data) if isinstance(data, dict) else None
    if status == 200 and reply is not None:
        return jsonify({"ok": True, "reply": reply, "protocol": protocol})
    if status == 200:
        return jsonify({"ok": True, "reply": json.dumps(data, ensure_ascii=False)[:200], "protocol": protocol})
    return jsonify({"ok": False, "status": status, "protocol": protocol, "error": data}), status or 500


@app.route("/api/test/vl", methods=["POST"])
def api_test_vl():
    """
    form-data:
      protocol, base_url, api_key, model, prompt, file(图片)
    """
    protocol = _normalize_protocol(request.form.get("protocol"))
    base_url = (request.form.get("base_url") or "").strip()
    api_key  = (request.form.get("api_key")  or "").strip()
    model    = (request.form.get("model")    or "").strip()
    prompt   = (request.form.get("prompt")   or "请用中文用一句话描述这张图片。").strip()

    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "请上传图片"}), 400

    b64 = base64.b64encode(file.read()).decode("ascii")
    mime = file.content_type or "image/png"

    if protocol == "anthropic":
        content_blocks = [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text",  "text": prompt},
        ]
        body_payload = {
            "messages": [{"role": "user", "content": content_blocks}],
            "max_tokens": 200,
        }
    else:
        image_url = f"data:{mime};base64,{b64}"
        body_payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text",      "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "max_tokens": 200,
            "temperature": 0.0,
        }

    status, data = _post_chat(protocol, base_url, api_key, model, body_payload, timeout=60)
    reply = _extract_reply(protocol, data) if isinstance(data, dict) else None
    if status == 200 and reply is not None:
        return jsonify({"ok": True, "reply": reply, "protocol": protocol})
    if status == 200:
        return jsonify({"ok": True, "reply": json.dumps(data, ensure_ascii=False)[:200], "protocol": protocol})
    return jsonify({"ok": False, "status": status, "protocol": protocol, "error": data}), status or 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "env_exists": ENV_FILE.exists()})


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    import argparse, os, signal, socket, sys, subprocess
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    p.add_argument("--stop", action="store_true", help="停止占用指定端口的服务进程")
    args = p.parse_args()

    # ── 一键停止 ──
    if args.stop:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True
        )
        killed = 0
        for line in result.stdout.splitlines():
            if f":{args.port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = int(parts[-1])
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                    print(f"  已终止 PID {pid}")
                except Exception as e:
                    print(f"  无法终止 PID {pid}: {e}")
        if killed == 0:
            print(f"  端口 {args.port} 上没有运行中的服务。")
        sys.exit(0)

    # ── 端口占用检测 ──
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((args.host, args.port))
        sock.close()
    except OSError:
        print(f"\n  [WARN] 端口 {args.port} 已被占用,可能已有实例在运行。")
        print(f"  解决方法:")
        print(f"    1) 打开浏览器 http://{args.host}:{args.port} 直接用已有的")
        print(f"    2) 换端口: python app.py --port 5001")
        sys.exit(1)

    print("=" * 60)
    print("  A5 智能体配置中心")
    print(f"  打开浏览器: http://{args.host}:{args.port}")
    print(f"  .env 路径:  {ENV_FILE}")
    print(f"  配置索引:  {CONFIGS_FILE}")
    print("=" * 60)
    app.run(host=args.host, port=args.port, debug=args.debug)
