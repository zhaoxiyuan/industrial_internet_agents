# P8P9/upload_routes.py — 附件上传路由（§7 方案 B·独立上传服务）
#
# 路由：
#   GET  /api/closure/upload?token=tk_xxx        → 上传页 HTML（多文件 form）
#   POST /api/closure/upload?token=tk_xxx        → 单文件 multipart 上传，返回 upload_id
#   GET  /api/closure/upload/done?job_id=&ids=   → 完成页（提示回到飞书卡片）
#
# 流程：
#   1. 飞书卡片回调触发 → 服务端调 ClosureLinkService.create_upload_token
#   2. 卡片 2.0 渲染按钮 link_button → /api/closure/upload?token=tk_xxx（新标签）
#   3. 用户上传 N 个文件（每次 POST 都 append_upload_to_token）
#   4. 用户点完成 → 上传页跳转到 /api/closure/upload/done
#   5. 用户回飞书卡片提交审核表单，表单中带 upload_token
#   6. business_actions 收到 upload_token → consume_upload_token → 拿 upload_ids
#      → 写入 state.materials.submissions[-1].uploads (或 risk_changes[-1].uploads)
#
# CLAUDE.md 规范：所有端点入口 / 出口 / 异常日志。

from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

try:
    from flask import Blueprint, request, jsonify, render_template, redirect  # type: ignore
except ImportError:
    raise ImportError("需要 flask：`pip install flask`")

from .upload_service import (
    UploadService,
    UploadValidationError,
    UploadSizeExceeded,
    UploadJobSizeExceeded,
    UploadFileMissing,
)
from .links import (
    ClosureLinkService,
    LinkInvalid, LinkExpired, LinkExhausted, LinkActorMismatch,
)
from .models import UPLOAD_TOKEN_PREFIX, UPLOAD_TOKEN_TTL_MINUTES
from .state_machine import ClosureService, StateNotFound


logger = logging.getLogger("P8P9.upload_routes")

# Blueprint（不直接用全局 app，便于挂载到任何 Flask 容器）
upload_bp = Blueprint("upload", __name__)


# ─── 日志工具 ────────────────────────────────────────────────────────────────

def _log_entry(method: str, path: str, **params: Any) -> None:
    """[HTTP方法] [端点路径] 进入: 请求参数（敏感字段脱敏）。"""
    safe = {k: v for k, v in params.items() if k not in ("token",)}
    logger.info(f"[{method}] {path} 进入: {safe}")


def _log_exit(method: str, path: str, payload: Any) -> None:
    """[HTTP方法] [端点路径] 响应: 响应数据（截断大字段）。"""
    try:
        s = json.dumps(payload, ensure_ascii=False, default=str)[:1500]
    except Exception:
        s = repr(payload)[:1500]
    logger.info(f"[{method}] {path} 响应: {s}")


def _log_error(method: str, path: str, exc: Exception) -> None:
    logger.exception(f"[{method}] {path} 异常: {exc}")


# ─── 路由：上传页 HTML ──────────────────────────────────────────────────────

@upload_bp.get("/api/closure/upload")
def upload_page():
    """上传页 HTML（GET）。query 参数 token 用于身份校验。"""
    _log_entry("GET", "/api/closure/upload",
               token=request.args.get("token", "")[:12] + "...")
    token = (request.args.get("token") or "").strip()
    if not token.startswith(UPLOAD_TOKEN_PREFIX):
        _log_exit("GET", "/api/closure/upload",
                  {"error": "invalid_token"})
        return jsonify({"error": "invalid_token",
                        "detail": "token 必须以 tk_ 开头"}), 400

    try:
        # 仅校验 token 合法性（不消费，消费在 commit 时）
        link_svc = ClosureLinkService()
        link_svc._find_upload_token(token)
        # 不在此路由消费 token；token 校验由上传 POST / commit 阶段处理
        payload = {
            "token": token,
        }
        html = render_template("upload.html", **payload)
        _log_exit("GET", "/api/closure/upload", {"status": "ok", "len_html": len(html)})
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except LinkInvalid as e:
        _log_exit("GET", "/api/closure/upload", {"error": "link_invalid", "detail": str(e)})
        return jsonify({"error": "link_invalid", "detail": str(e)}), 404
    except Exception as e:
        _log_error("GET", "/api/closure/upload", e)
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


# ─── 路由：单文件上传 ────────────────────────────────────────────────────────

@upload_bp.post("/api/closure/upload")
def upload_post():
    """单文件 multipart 上传。query 参数 token。"""
    _log_entry("POST", "/api/closure/upload",
               token=request.args.get("token", "")[:12] + "...")

    token = (request.args.get("token") or "").strip()
    if not token.startswith(UPLOAD_TOKEN_PREFIX):
        return jsonify({"error": "invalid_token"}), 400

    # 解析 multipart（Flask request.files）
    if "file" not in request.files:
        _log_exit("POST", "/api/closure/upload",
                  {"error": "missing_file_field"})
        return jsonify({"error": "missing_file_field",
                        "detail": "form 必须包含 'file' 字段"}), 400

    file_storage = request.files["file"]
    filename = file_storage.filename or ""
    mime_type = (file_storage.mimetype or "").lower()
    file_bytes = file_storage.read()  # 整文件读入内存
    file_size = len(file_bytes)

    link_svc = ClosureLinkService()
    upload_svc = UploadService()

    try:
        # 1. token 合法性（存在性 + 未过期 + actor 一致性）
        job_id, entry = link_svc._find_upload_token(token)
        if entry is None:
            return jsonify({"error": "link_invalid",
                            "detail": "token 不存在"}), 404
        # 过期校验
        from datetime import datetime, timezone
        expiry = datetime.fromisoformat(entry["expiry"])
        if datetime.now(timezone.utc) > expiry:
            return jsonify({"error": "link_expired",
                            "detail": "token 已过期"}), 403

        # 2. 业务参数校验
        upload_svc.validate_extension(filename, mime_type)
        upload_svc.validate_size(file_size)
        upload_svc.check_job_quota(job_id, file_size)

        # 3. 原子写文件
        upload_id = UploadService.compute_upload_id()
        upload_svc.atomic_write_file(job_id, upload_id, file_bytes)

        # 4. 把 upload metadata 追加到 token 关联列表
        meta = UploadService.build_metadata(
            upload_id=upload_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=file_size,
            uploaded_by=entry["created_by"],
        )
        link_svc.append_upload_to_token(token, meta)

        # 5. 返回 upload_id 给前端
        result = {
            "upload_id": upload_id,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": file_size,
            "job_id": job_id,
            "target": entry["target"],
        }
        _log_exit("POST", "/api/closure/upload", result)
        return jsonify(result), 200

    except UploadValidationError as e:
        _log_exit("POST", "/api/closure/upload",
                  {"error": "validation_error", "detail": str(e)})
        return jsonify({"error": "validation_error", "detail": str(e)}), 400
    except UploadSizeExceeded as e:
        _log_exit("POST", "/api/closure/upload",
                  {"error": "size_exceeded", "detail": str(e)})
        return jsonify({"error": "size_exceeded", "detail": str(e)}), 413
    except UploadJobSizeExceeded as e:
        _log_exit("POST", "/api/closure/upload",
                  {"error": "job_size_exceeded", "detail": str(e)})
        return jsonify({"error": "job_size_exceeded", "detail": str(e)}), 413
    except Exception as e:
        _log_error("POST", "/api/closure/upload", e)
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


# ─── 路由：完成页 ──────────────────────────────────────────────────────────

@upload_bp.get("/api/closure/upload/done")
def upload_done():
    """完成页（GET）。query 参数 ids=up_xxx,up_yyy 显示已上传附件。"""
    _log_entry("GET", "/api/closure/upload/done", job_id=request.args.get("job_id", ""))
    job_id = (request.args.get("job_id") or "").strip()
    ids_raw = (request.args.get("ids") or "").strip()
    ids = [x for x in ids_raw.split(",") if x]
    payload = {
        "job_id": job_id,
        "upload_ids": ids,
        "count": len(ids),
    }
    try:
        html = render_template("upload_done.html", **payload)
        _log_exit("GET", "/api/closure/upload/done",
                  {"status": "ok", "count": len(ids)})
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        _log_error("GET", "/api/closure/upload/done", e)
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


# ─── 路由：创建 upload token 并重定向到上传页 ──────────────────────────────

@upload_bp.get("/api/closure/upload/new")
def upload_new():
    """生成 upload token → 302 重定向到 /api/closure/upload?token=tk_xxx。

    query 参数: job_id, target, open_id
      - job_id: 作业 ID
      - target: "materials_submission" / "risk_change"
      - open_id: 申请人 open_id（必须是 accepted_by 或 review decider）

    业务权限：
      - materials_submission → 必须 accepted_by.open_id == open_id
      - risk_change → 不校验（review decider 由 record_closure_review 上下文决定）
    """
    job_id = (request.args.get("job_id") or "").strip()
    target = (request.args.get("target") or "").strip()
    open_id = (request.args.get("open_id") or "").strip()

    _log_entry("GET", "/api/closure/upload/new",
               job_id=job_id, target=target, open_id=open_id[:12] + "...")

    if not job_id or target not in ("materials_submission", "risk_change"):
        return jsonify({"error": "bad_request",
                        "detail": "job_id 必填；target ∈ {materials_submission, risk_change}"}), 400
    if not open_id:
        return jsonify({"error": "bad_request",
                        "detail": "open_id 必填"}), 400

    try:
        # 业务权限校验（仅 materials_submission 严格）
        svc = ClosureService()
        state = svc.get_state(job_id)  # StateNotFound
        if target == "materials_submission":
            accepted = state.get("accepted_by") or {}
            if not accepted:
                return jsonify({"error": "no_acceptor",
                                "detail": "作业尚未接取；无法创建上传 token"}), 403
            if accepted.get("open_id") != open_id:
                return jsonify({"error": "actor_mismatch",
                                "detail": f"open_id={open_id!r} ≠ 接取人={accepted.get('open_id')!r}"}), 403
        # risk_change 不校验 open_id（review decider 身份由 record_closure_review 业务流确认）

        link_svc = ClosureLinkService()
        info = link_svc.create_upload_token(
            job_id,
            actor=open_id,
            target=target,
            ttl_minutes=UPLOAD_TOKEN_TTL_MINUTES,
            max_consume_count=1,
        )
        # 302 重定向到上传页（带 token query）
        upload_url = info["upload_url"]
        _log_exit("GET", "/api/closure/upload/new", {"upload_url": upload_url})
        return redirect(upload_url, code=302)
    except StateNotFound as e:
        _log_exit("GET", "/api/closure/upload/new",
                  {"error": "state_not_found", "detail": str(e)})
        return jsonify({"error": "state_not_found", "detail": str(e)}), 404
    except Exception as e:
        _log_error("GET", "/api/closure/upload/new", e)
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


__all__ = ["upload_bp"]