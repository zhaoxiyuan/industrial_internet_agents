# P8P9/web_server.py — 轻量 Flask 路由（§6.3 API + §6.5 下载）
#
# 启动：python -m P8P9.web_server [--port 8089]
#
# CLAUDE.md 规范：所有端点入口/出口/异常日志。
#
# 路由：
#   POST /api/closure/jobs/<job_id>/initialize          → agent_interface.initialize_job_for_agent
#   POST /api/closure/jobs/<job_id>/bind-card           → agent_interface.bind_card_for_agent
#   POST /api/closure/jobs/<job_id>/acknowledge         → business_actions.acknowledge_disposition
#   POST /api/closure/jobs/<job_id>/submit-materials    → business_actions.submit_rectification_materials
#   POST /api/closure/jobs/<job_id>/relinquish          → business_actions.relinquish_job
#   POST /api/closure/jobs/<job_id>/events/<rid>/escalate  → business_actions.escalate_risk
#   POST /api/closure/jobs/<job_id>/events/<rid>/downgrade → business_actions.downgrade_risk
#   POST /api/closure/jobs/<job_id>/record-review       → business_actions.record_closure_review
#   POST /api/closure/jobs/<job_id>/close               → business_actions.record_closure_review(approved)
#   GET  /api/closure/jobs/<job_id>                     → agent_interface.get_state_for_agent
#   GET  /dl/<token>                                    → ClosureLinkService.consume_attachment_link → 302

from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

# 把项目根加入 sys.path（方便 web_server.py 单独运行）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from flask import Flask, jsonify, request, redirect  # type: ignore
except ImportError:
    raise ImportError("需要 flask：`pip install flask`")

from P8P9 import agent_interface, business_actions
from P8P9.services.card_render import (
    send_event_card_for_web, card_callback_context, start_card_update_worker,
)
# 2026-09-17：强制 import audit_scheduler 以触发 _register_self()，
# 否则 business_actions._audit_scheduler 永远 None → submit_rectification_materials
# 末尾的 _trigger_audit(job_id) 静默 return，P9 真审核 agent 不会被调用。
# callback_router.py 已 import，但 web_server 单独启动时（pytest / 直接 import）这条
# 链路断开。显式 import 兜底。
from P8P9.services import audit_scheduler as _audit_scheduler_module  # noqa: F401
from P8P9.links import (
    ClosureLinkService,
    LinkInvalid, LinkExpired, LinkExhausted, LinkActorMismatch,
)
from P8P9.state_machine import (
    StateNotFound, IllegalTransition, VersionConflict,
    BusinessConsistencyViolation, CardinalityExceeded, InputValidationError,
)


logger = logging.getLogger("P8P9.web_server")


# ─── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__)

# 注册 upload Blueprint(2026-09-20 方案 B)
from P8P9.upload_routes import upload_bp
app.register_blueprint(upload_bp)


# ─── 健康检查(Docker HEALTHCHECK / Nginx upstream 用)───────────────────────────
# 2026-09-20:为 Docker 部署新增;不动业务路由
@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})


def _log_entry(method: str, path: str, **params: Any) -> None:
    safe = {k: v for k, v in params.items() if k not in ("api_key", "token")}
    logger.info(f"[{method}] {path} 进入: {safe}")


def _log_payload(label: str, payload: Any) -> None:
    """记录回调结构，但绝不把飞书校验 token 写入日志。"""
    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: ("***" if k.lower() in {"token", "app_secret", "signature", "encrypt"}
                        else redact(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value
    try:
        s = json.dumps(redact(payload), ensure_ascii=False)[:2000]
    except Exception:
        s = "<unserializable>"
    logger.info(f"[DEBUG] {label} payload={s}")


def _log_exit(method: str, path: str, status: int, data: Any) -> None:
    if isinstance(data, dict):
        keys = list(data.keys())[:6]
        summary = f"keys={keys}"
    else:
        summary = str(data)[:80]
    logger.info(f"[{method}] {path} 响应 [{status}]: {summary}")


def _log_error(method: str, path: str, err: Exception) -> None:
    logger.exception(f"[{method}] {path} 异常: {err}")


def _make_actor(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "open_id": payload.get("open_id", "unknown"),
        "name": payload.get("name", ""),
    }


def _job_required(payload: Dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    if not job_id:
        raise InputValidationError("body 缺少 job_id")
    return job_id


# ─── POST /feishu/card/callback（飞书群按钮回调 ingress） ─────────────────

# 飞书群卡片按钮（callback / form_action）点击后，平台会 POST 到业务方配置的回调 URL。
# 这里直接接收 + 调 services.callback_router.route_card_callback 处理。
#
# 飞书开放平台 → 应用 → 事件订阅 / 卡片回调 URL：需配 http://<公网域名>/feishu/card/callback
# 本机测试可用 ngrok 暴露：
#   ngrok http 8089
#   → 把 ngrok 分配的 https://<id>.ngrok.io/feishu/card/callback 配到飞书
@app.post("/feishu/card/callback")
def feishu_card_callback():
    """飞书群卡片按钮回调入口。

    Payload 结构见 services.callback_router.route_card_callback 文档。
    返回 {"status": "ok"} 让飞书确认收到；业务异常返回 200 + {"status": "error", ...}
    （飞书会渲染成 toast）。
    """
    _log_entry("POST", "/feishu/card/callback")
    payload = request.get_json(silent=True) or {}
    _log_payload("/feishu/card/callback", payload)
    try:
        from P8P9.services.callback_router import (
            route_card_callback,
            InvalidAction, InvalidOperator, CallbackError,
        )
        with card_callback_context():
            result = route_card_callback(payload)
        _log_exit("POST", "/feishu/card/callback", 200, {"status": "ok", "result_keys": list(result.keys()) if isinstance(result, dict) else None})
        response_body: Dict[str, Any] = {
            "toast": {"type": "success", "content": "已记录您的处置"},
        }
        # 同步响应给点击者，防止客户端在回调结束时恢复旧卡片；群内 CardKit
        # 实体的持久更新由后台队列完成。回调卡片必须是 raw + 对象，不是 card_json。
        if isinstance(result, dict) and isinstance(result.get("_card_json"), dict):
            response_body["card"] = {"type": "raw", "data": result["_card_json"]}
        return jsonify(response_body), 200
    except InvalidAction as e:
        msg = str(e)
        logger.warning(f"callback invalid_action: {msg}")
        # 2026-09-17 修复：飞书 Card 2.0 callback 响应只允许 toast + card 两个顶层 key。
        # 多余字段（status/error）会被飞书 reject 并报 200672「响应体格式错误」。
        return jsonify({
            "toast": {"type": "error", "content": msg},
        }), 200
    except InvalidOperator as e:
        msg = str(e)
        logger.warning(f"callback invalid_operator: {msg}")
        return jsonify({
            "toast": {"type": "error", "content": msg},
        }), 200
    except CallbackError as e:
        msg = str(e)
        logger.warning(f"callback business error: {msg}")
        return jsonify({
            "toast": {"type": "warning", "content": msg},
        }), 200
    except Exception as e:
        logger.exception(f"feishu callback 未捕获异常：{e}")
        return jsonify({
            "toast": {"type": "error", "content": f"服务异常：{e}"},
        }), 200


# ─── POST /api/closure/jobs/<job_id>/initialize ───────────────────────────────

@app.post("/api/closure/jobs/<job_id>/initialize")
def api_initialize(job_id: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/initialize", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    actor = payload.get("actor", "agent")
    events = payload.get("events")
    try:
        result = agent_interface.initialize_job_for_agent(
            job_id, actor=actor, events=events,
        )
        # 自动 send_all_open_closure_cards（如已绑 chat_id）
        chat_id = payload.get("chat_id")
        if chat_id:
            from P8P9.services.card_render import send_all_open_closure_cards
            send_all_open_closure_cards(
                job_id, actor=actor, chat_id=chat_id,
                group_name=payload.get("group_name"),
                account_id=payload.get("account_id"),
            )
        _log_exit("POST", f"/api/closure/jobs/{job_id}/initialize", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except InputValidationError as e:
        _log_error("POST", f"/api/closure/jobs/{job_id}/initialize", e)
        return jsonify({"status": "error", "error": str(e)}), 400


@app.post("/api/closure/jobs/<job_id>/bind-card")
def api_bind_card(job_id: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/bind-card", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = agent_interface.bind_card_for_agent(
            job_id,
            chat_id=payload.get("chat_id"),
            actor=actor,
            group_name=payload.get("group_name"),
            account_id=payload.get("account_id"),
        )
        _log_exit("POST", f"/api/closure/jobs/{job_id}/bind-card", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except StateNotFound as e:
        return jsonify({"status": "error", "error": str(e)}), 404
    except InputValidationError as e:
        return jsonify({"status": "error", "error": str(e)}), 400


# ─── POST /api/closure/jobs/<job_id>/acknowledge ─────────────────────────────

@app.post("/api/closure/jobs/<job_id>/acknowledge")
def api_acknowledge(job_id: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/acknowledge", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = business_actions.acknowledge_disposition(
            job_id,
            actor=actor,
            expected_version=int(payload.get("expected_version", 0)),
        )
        # 触发 web 自动卡片刷新（MEMORY.md 约束）
        for ev in (result.get("events") or []):
            send_event_card_for_web(job_id, ev.get("risk_event_id"), actor="web-refresh")
        _log_exit("POST", f"/api/closure/jobs/{job_id}/acknowledge", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except (IllegalTransition, VersionConflict, BusinessConsistencyViolation, InputValidationError) as e:
        return jsonify({"status": "error", "error": str(e)}), 400


# ─── POST /api/closure/jobs/<job_id>/submit-materials ────────────────────────

@app.post("/api/closure/jobs/<job_id>/submit-materials")
def api_submit_materials(job_id: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/submit-materials", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = business_actions.submit_rectification_materials(
            job_id,
            review_text=payload.get("review_text", ""),
            submissions=payload.get("submissions", []),
            actor=actor,
            expected_version=int(payload.get("expected_version", 0)),
            event_ids=payload.get("event_ids"),
        )
        for ev in (result.get("events") or []):
            send_event_card_for_web(job_id, ev.get("risk_event_id"), actor="web-refresh")
        _log_exit("POST", f"/api/closure/jobs/{job_id}/submit-materials", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except (IllegalTransition, VersionConflict, BusinessConsistencyViolation, InputValidationError) as e:
        return jsonify({"status": "error", "error": str(e)}), 400


# ─── POST /api/closure/jobs/<job_id>/relinquish ──────────────────────────────

@app.post("/api/closure/jobs/<job_id>/relinquish")
def api_relinquish(job_id: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/relinquish", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = business_actions.relinquish_job(
            job_id,
            reason=payload.get("reason", ""),
            actor=actor,
            expected_version=int(payload.get("expected_version", 0)),
        )
        _log_exit("POST", f"/api/closure/jobs/{job_id}/relinquish", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except (IllegalTransition, CardinalityExceeded, BusinessConsistencyViolation, InputValidationError) as e:
        return jsonify({"status": "error", "error": str(e)}), 400


# ─── POST /api/closure/jobs/<job_id>/events/<rid>/escalate ────────────────────

@app.post("/api/closure/jobs/<job_id>/events/<rid>/escalate")
def api_escalate(job_id: str, rid: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/events/{rid}/escalate", job_id=job_id, rid=rid)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = business_actions.escalate_risk(
            job_id,
            event_id=rid,
            new_level=int(payload.get("new_level", 0)),
            reason=payload.get("reason", ""),
            evidence_ids=payload.get("evidence_ids"),
            actor=actor,
            expected_version=int(payload.get("expected_version", 0)),
        )
        send_event_card_for_web(job_id, rid, actor="web-refresh")
        _log_exit("POST", f"/api/closure/jobs/{job_id}/events/{rid}/escalate", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except InputValidationError as e:
        return jsonify({"status": "error", "error": str(e)}), 400


@app.post("/api/closure/jobs/<job_id>/events/<rid>/downgrade")
def api_downgrade(job_id: str, rid: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/events/{rid}/downgrade", job_id=job_id, rid=rid)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = business_actions.downgrade_risk(
            job_id,
            event_id=rid,
            new_level=int(payload.get("new_level", 0)),
            reason=payload.get("reason", ""),
            evidence_ids=payload.get("evidence_ids"),
            actor=actor,
            expected_version=int(payload.get("expected_version", 0)),
        )
        send_event_card_for_web(job_id, rid, actor="web-refresh")
        _log_exit("POST", f"/api/closure/jobs/{job_id}/events/{rid}/downgrade", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except InputValidationError as e:
        return jsonify({"status": "error", "error": str(e)}), 400


# ─── POST /api/closure/jobs/<job_id>/record-review ───────────────────────────

@app.post("/api/closure/jobs/<job_id>/record-review")
def api_record_review(job_id: str):
    _log_entry("POST", f"/api/closure/jobs/{job_id}/record-review", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    try:
        actor = _make_actor(payload)
        result = business_actions.record_closure_review(
            job_id,
            decision=payload.get("decision", ""),
            comment=payload.get("comment", ""),
            actor=actor,
            expected_version=int(payload.get("expected_version", 0)),
        )
        for ev in (result.get("events") or []):
            send_event_card_for_web(job_id, ev.get("risk_event_id"), actor="web-refresh")
        _log_exit("POST", f"/api/closure/jobs/{job_id}/record-review", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except (IllegalTransition, InputValidationError) as e:
        return jsonify({"status": "error", "error": str(e)}), 400


@app.post("/api/closure/jobs/<job_id>/close")
def api_close(job_id: str):
    """便捷关闭：decision=approved + 触发归档。"""
    _log_entry("POST", f"/api/closure/jobs/{job_id}/close", job_id=job_id)
    payload = request.get_json(silent=True) or {}
    payload.setdefault("decision", "approved")
    payload.setdefault("comment", "通过 Web 端直接关闭此告警（终审）")
    return api_record_review(job_id)


# ─── GET /api/closure/jobs/<job_id> ──────────────────────────────────────────

# TODO(待开发·前端整合)：按 docs/风险处置卡片交互设计.md §2.4.1 + §1047，
#       详情入口应改为 `closure/entry/<link_id>` 短时 token 链接（生命周期跟 job 走，
#       job closed → token 过期），返回 HTML 详情页（含复核文字 / 审核意见 textarea
#       + 附件上传区 + 多 event 勾选 + 权限校验）。
#       当前为临时 JSON API，仅用于本地调试与接口联调；前端整合时统一风格与开发。
@app.get("/api/closure/jobs/<job_id>")
def api_get_job(job_id: str):
    _log_entry("GET", f"/api/closure/jobs/{job_id}", job_id=job_id)
    try:
        result = agent_interface.get_state_for_agent(job_id)
        _log_exit("GET", f"/api/closure/jobs/{job_id}", 200, result)
        return jsonify({"status": "ok", "data": result}), 200
    except StateNotFound as e:
        return jsonify({"status": "error", "error": str(e)}), 404


# ─── GET /dl/<token> ─────────────────────────────────────────────────────────

@app.get("/dl/<token>")
def api_dl(token: str):
    _log_entry("GET", f"/dl/{token}", token_prefix=token[:4] + "***")
    open_id = request.args.get("open_id") or "anonymous"
    try:
        link_svc = ClosureLinkService()
        result = link_svc.consume_attachment_link(token, open_id)
        # 重定向到 OSS 签名 URL（5min 有效）
        return redirect(result["oss_url"], code=302)
    except LinkInvalid as e:
        _log_error("GET", f"/dl/{token}", e)
        return jsonify({"status": "error", "error": "link_invalid"}), 404
    except (LinkExpired, LinkExhausted, LinkActorMismatch) as e:
        _log_error("GET", f"/dl/{token}", e)
        return jsonify({"status": "error", "error": str(e)}), 403


# ─── CLI 入口 ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="P8P9 closure web server")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info(f"启动 P8P9 web server: http://{args.host}:{args.port}")
    start_card_update_worker()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]
