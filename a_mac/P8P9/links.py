# P8P9/links.py — 短时链接 service（§6.5 附件下载 + §1047 任务入口）
#
# 设计依据：
#   - §6.5 附件下载 token（防泄漏：校验 actor_open_id；消费次数上限；TTL）
#   - §2.0.1 service 边界（不 import langchain）
#
# TODO(待开发·前端整合)：按 docs/风险处置卡片交互设计.md §1047 + §672，
#       任务入口链接 `closure/entry/<link_id>` 也要走类似短时 token 机制：
#         - create_closure_entry_link(job_id, *, actor, ttl_minutes) -> dict
#         - consume_closure_entry_link(token, actor_open_id) -> dict
#         - 生命周期跟 job 走（job closed → entry link 自动失效）
#         - 支持 ?event_ids= 多 event 展示
#       当前仅实现附件下载链接；任务入口待前端整合时统一风格与开发。
#
# 公开 API：
#   - ClosureLinkService.create_attachment_link(job_id, evidence_id, *, actor, ttl_minutes, max_consume_count) -> dict
#   - ClosureLinkService.consume_attachment_link(token, actor_open_id) -> dict
#
# 异常类：
#   - LinkInvalid: token 不存在（404）
#   - LinkExpired: token 已过期（403）
#   - LinkExhausted: token 已耗尽消费次数（403）
#   - LinkActorMismatch: actor_open_id 与 token 绑定的 open_id 不一致（防泄漏）
#
# 持久化：state["attachment_links"][token] = {...}
# token 生成：8 字符 base62（secrets 模块）。
# OSS URL 生成：mock（生产应接 OSS SDK）。本计划返回 mock 302 URL。

from __future__ import annotations
import json
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .state_machine import ClosureService, StateNotFound
from .models import (
    UPLOAD_TOKEN_PREFIX,
    UPLOAD_TOKEN_TTL_MINUTES,
)


class LinkInvalid(Exception):
    """token 不存在（404）。"""


class LinkExpired(Exception):
    """token 已过期（403）。"""


class LinkExhausted(Exception):
    """token 已达消费次数上限（403）。"""


class LinkActorMismatch(Exception):
    """actor_open_id 与 token 绑定不一致（防泄漏）。"""


# 8 字符 base62 字母表
_TOKEN_ALPHABET = string.ascii_letters + string.digits
_TOKEN_LEN = 8


def _generate_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LEN))


def _mock_oss_signed_url(evidence_id: str, expires_at: datetime) -> str:
    """mock OSS 签名 URL。生产环境应调 OSS SDK 生成 5min 有效 URL。
    本计划返回固定 URL + query 参数。
    """
    return (
        f"https://oss-mock.local/{evidence_id}"
        f"?X-Expires-At={expires_at.isoformat()}&Signature=mock"
    )


class ClosureLinkService:
    """§6.5 短时附件下载链接 service。

    持久化：state["attachment_links"][token] = {...}。
    每次 create / consume 都重读 / 重写 state（与 ClosureService 锁同 job_id）。
    """

    def __init__(self, base_dir: str = None) -> None:
        if base_dir is None:
            import os
            # 2026-09-17：与 ClosureService 同目录（data/jobs/），保持一致
            base_dir = os.environ.get("P8P9_BASE_DIR", "data/jobs")
        self.base_dir = Path(base_dir)

    def _svc(self) -> ClosureService:
        # 复用 ClosureService 的持久化路径
        return ClosureService(base_dir=str(self.base_dir))

    def create_attachment_link(
        self,
        job_id: str,
        evidence_id: str,
        *,
        actor: Dict[str, Any],
        ttl_minutes: int = 24 * 60,
        max_consume_count: int = 999,
    ) -> Dict[str, Any]:
        """创建短时下载链接。

        Returns:
            {
                "token": "abcd1234",
                "short_url": "/dl/abcd1234",
                "expiry": ISO8601,
                "ttl_minutes": int,
                "max_consume_count": int,
            }
        """
        if not isinstance(actor, dict) or "open_id" not in actor:
            raise ValueError("actor 必须含 open_id")

        svc = self._svc()
        state = svc.get_state(job_id)  # StateNotFound if not exists
        token = _generate_token()
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(minutes=ttl_minutes)

        link_entry = {
            "token": token,
            "evidence_id": evidence_id,
            "created_by": actor["open_id"],
            "created_at": now.isoformat(),
            "expiry": expiry.isoformat(),
            "max_consume_count": max_consume_count,
            "consumed_count": 0,
            "consumed_history": [],
        }

        # 用 ClosureService 自身的 lock + atomic_write 通道
        links = dict(state.get("links") or {})
        links[token] = link_entry

        from threading import Lock
        with svc._lock_for(job_id):
            cur = svc.get_state(job_id)
            cur_links = dict(cur.get("links") or {})
            cur_links[token] = link_entry
            cur["links"] = cur_links
            # 不 bump version：链接簿记是基础设施操作，不影响业务乐观锁。
            # bump 会让用户在「点上传链接 → 上传文件」期间失效按钮的 expected_version。
            cur["updated_at"] = now.isoformat()
            svc._atomic_write(job_id, cur)

        return {
            "token": token,
            "short_url": f"/dl/{token}",
            "expiry": expiry.isoformat(),
            "ttl_minutes": ttl_minutes,
            "max_consume_count": max_consume_count,
        }

    def consume_attachment_link(
        self, token: str, actor_open_id: str,
    ) -> Dict[str, Any]:
        """消费 token。

        校验：
          1. token 存在
          2. actor_open_id == token.created_by（防泄漏）
          3. 未过期
          4. consumed_count < max_consume_count

        Returns:
            {"oss_url": "...", "evidence_id": "...", "expires_at": ISO8601}
        """
        # 反查 token（不依赖知道 job_id；扫描所有 state 太重）
        # 简化：要求调用方同时传 job_id → 这里我们存 token → job_id 反查在 create 时已写入。
        # 实际实现：维护 token_index.json；本计划先要求调用方先 get_state。

        # 简化策略：在 state["links"]["by_token"][token] = job_id
        # 但我们 create 时没存反查；所以这里临时用「先扫所有 jobs 反查」。
        # 工程上更优是另开一个 _link_index.json；为简化起见，本服务用 dict 反向索引到内存。

        job_id, entry = self._find_token(token)
        if entry is None:
            raise LinkInvalid(f"token={token!r} 不存在")

        # actor 一致性（防泄漏）
        if entry["created_by"] != actor_open_id:
            raise LinkActorMismatch(
                f"token={token!r} 绑定的 open_id={entry['created_by']!r} "
                f"≠ 消费方={actor_open_id!r}"
            )

        # 过期校验
        expiry = datetime.fromisoformat(entry["expiry"])
        if datetime.now(timezone.utc) > expiry:
            raise LinkExpired(f"token={token!r} 已过期")

        # 消费次数校验
        if entry["consumed_count"] >= entry["max_consume_count"]:
            raise LinkExhausted(f"token={token!r} 已达消费上限 {entry['max_consume_count']}")

        # 原子更新 consumed_count
        svc = self._svc()
        with svc._lock_for(job_id):
            cur = svc.get_state(job_id)
            links = dict(cur.get("links") or {})
            entry_now = dict(links[token])
            entry_now["consumed_count"] = entry_now.get("consumed_count", 0) + 1
            entry_now["consumed_history"] = list(entry_now.get("consumed_history", []))
            entry_now["consumed_history"].append({
                "by": actor_open_id,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            links[token] = entry_now
            cur["links"] = links
            # 不 bump version（基础设施簿记，非业务状态变更）
            cur["updated_at"] = datetime.now(timezone.utc).isoformat()
            svc._atomic_write(job_id, cur)

        # 生成 5min 有效 OSS URL（mock）
        oss_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        oss_url = _mock_oss_signed_url(entry["evidence_id"], oss_expiry)
        return {
            "oss_url": oss_url,
            "evidence_id": entry["evidence_id"],
            "expires_at": oss_expiry.isoformat(),
            "redirect": True,
        }

    # ─── 内部 token 反查 ────────────────────────────────────────────────────

    def _find_token(self, token: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """扫描 base_dir/<job_id>/closure_state.json 找 token。

        工程上应该维护 _link_index.json（O(1) 反查）；为简化 + 单元测试友好，
        这里用目录扫描；callers 高频时建议自行维护索引。
        """
        if not self.base_dir.exists():
            return None, None
        for job_dir in self.base_dir.iterdir():
            if not job_dir.is_dir():
                continue
            if job_dir.name.startswith("_"):  # 跳过 _long_term / _archive
                continue
            state_path = job_dir / "closure_state.json"
            if not state_path.exists():
                continue
            try:
                with state_path.open("r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            links = state.get("links") or {}
            if token in links:
                return job_dir.name, links[token]
        return None, None

    # ─── Upload token（2026-09-20 方案 B）────────────────────────────────────

    # upload token 与 attachment link 共用 _find_token，但存储字段独立。
    # upload_links 是独立字典，避免和 attachment links 的 24h TTL 混淆。
    def create_upload_token(
        self,
        job_id: str,
        *,
        actor: str,
        target: str,
        ttl_minutes: int = UPLOAD_TOKEN_TTL_MINUTES,
        max_consume_count: int = 1,
    ) -> Dict[str, Any]:
        """创建短时 upload token（单次消费为默认）。

        Args:
            job_id: 作业 ID。
            actor: 创建人 open_id（accepted_by / record_closure_review 决策人）。
            target: 上传目标的语义标签 ∈ {"materials_submission", "risk_change"}。
            ttl_minutes: 默认 30 分钟。
            max_consume_count: 默认 1 次（一次性 token）。

        Returns:
            {
                "token": "tk_xxxxxxxx",
                "upload_url": "/api/closure/upload?token=tk_xxxxxxxx",
                "expiry": ISO8601,
                "ttl_minutes": int,
                "max_consume_count": int,
                "target": str,
            }
        """
        if not isinstance(actor, str) or not actor:
            raise ValueError("actor 必须是非空字符串 open_id")

        svc = self._svc()
        svc.get_state(job_id)  # StateNotFound if not exists
        token = UPLOAD_TOKEN_PREFIX + _generate_token()
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(minutes=ttl_minutes)

        entry = {
            "token": token,
            "kind": "upload",  # 与 attachment 区分
            "job_id": job_id,
            "target": target,
            "created_by": actor,
            "created_at": now.isoformat(),
            "expiry": expiry.isoformat(),
            "max_consume_count": max_consume_count,
            "consumed_count": 0,
            "consumed_history": [],
            "uploads": [],  # 上传成功后由 append_upload_to_token 填充 metadata
        }

        with svc._lock_for(job_id):
            cur = svc.get_state(job_id)
            # 独立字段 upload_links（不与 attachment links 混存）
            upload_links = dict(cur.get("upload_links") or {})
            upload_links[token] = entry
            cur["upload_links"] = upload_links
            # 不 bump version（基础设施簿记，非业务状态变更）
            cur["updated_at"] = now.isoformat()
            svc._atomic_write(job_id, cur)

        return {
            "token": token,
            "upload_url": f"/api/closure/upload?token={token}",
            "expiry": expiry.isoformat(),
            "ttl_minutes": ttl_minutes,
            "max_consume_count": max_consume_count,
            "target": target,
        }

    def consume_upload_token(
        self, token: str, actor_open_id: str,
    ) -> Dict[str, Any]:
        """消费 upload token。

        校验：
          1. token 存在且 kind == "upload"
          2. actor_open_id == token.created_by
          3. 未过期
          4. consumed_count < max_consume_count

        Returns:
            {"job_id": ..., "target": ..., "upload_ids": [...]}
        """
        if not isinstance(token, str) or not token.startswith(UPLOAD_TOKEN_PREFIX):
            raise LinkInvalid(f"token 格式非法: {token!r}")

        job_id, entry = self._find_upload_token(token)
        if entry is None:
            raise LinkInvalid(f"upload token={token!r} 不存在")

        # actor 一致性（防泄漏：只有创建人能消费）
        if entry["created_by"] != actor_open_id:
            raise LinkActorMismatch(
                f"token={token!r} 绑定的 open_id={entry['created_by']!r} "
                f"≠ 消费方={actor_open_id!r}"
            )

        expiry = datetime.fromisoformat(entry["expiry"])
        if datetime.now(timezone.utc) > expiry:
            raise LinkExpired(f"upload token={token!r} 已过期")

        if entry["consumed_count"] >= entry["max_consume_count"]:
            raise LinkExhausted(
                f"upload token={token!r} 已达消费上限 {entry['max_consume_count']}"
            )

        # 原子递增 consumed_count
        svc = self._svc()
        with svc._lock_for(job_id):
            cur = svc.get_state(job_id)
            upload_links = dict(cur.get("upload_links") or {})
            entry_now = dict(upload_links[token])
            entry_now["consumed_count"] = entry_now.get("consumed_count", 0) + 1
            entry_now["consumed_history"] = list(entry_now.get("consumed_history", []))
            entry_now["consumed_history"].append({
                "by": actor_open_id,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            upload_links[token] = entry_now
            cur["upload_links"] = upload_links
            # 不 bump version（基础设施簿记，非业务状态变更）
            cur["updated_at"] = datetime.now(timezone.utc).isoformat()
            svc._atomic_write(job_id, cur)

        return {
            "job_id": entry["job_id"],
            "target": entry["target"],
            "uploads": list(entry.get("uploads") or []),
        }

    def append_upload_to_token(
        self, token: str, upload_meta: Dict[str, Any],
    ) -> None:
        """上传成功后：把 upload metadata 追加到 token 的 uploads 列表。

        upload_meta 来自 UploadService.build_metadata(...)。
        让业务动作可以通过 consume_upload_token 拿到本次会话的全部 upload 元数据。
        """
        if not isinstance(token, str) or not token.startswith(UPLOAD_TOKEN_PREFIX):
            raise LinkInvalid(f"token 格式非法: {token!r}")
        if not isinstance(upload_meta, dict) or "upload_id" not in upload_meta:
            raise ValueError("upload_meta 必须是 dict 且含 upload_id")

        job_id, entry = self._find_upload_token(token)
        if entry is None:
            raise LinkInvalid(f"upload token={token!r} 不存在")

        svc = self._svc()
        with svc._lock_for(job_id):
            cur = svc.get_state(job_id)
            upload_links = dict(cur.get("upload_links") or {})
            entry_now = dict(upload_links[token])
            uploads = list(entry_now.get("uploads") or [])
            if upload_meta["upload_id"] not in [u.get("upload_id") for u in uploads]:
                uploads.append(upload_meta)
            entry_now["uploads"] = uploads
            upload_links[token] = entry_now
            cur["upload_links"] = upload_links
            # 不 bump version（基础设施簿记，非业务状态变更）
            cur["updated_at"] = datetime.now(timezone.utc).isoformat()
            svc._atomic_write(job_id, cur)

    # ─── Upload token 反查（独立字典）────────────────────────────────────────

    def _find_upload_token(
        self, token: str,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """扫描 base_dir/<job_id>/closure_state.json 找 upload token。"""
        if not self.base_dir.exists():
            return None, None
        for job_dir in self.base_dir.iterdir():
            if not job_dir.is_dir():
                continue
            if job_dir.name.startswith("_"):
                continue
            state_path = job_dir / "closure_state.json"
            if not state_path.exists():
                continue
            try:
                with state_path.open("r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            upload_links = state.get("upload_links") or {}
            if token in upload_links:
                return job_dir.name, upload_links[token]
        return None, None


__all__ = [
    "ClosureLinkService",
    "LinkInvalid",
    "LinkExpired",
    "LinkExhausted",
    "LinkActorMismatch",
]