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
            base_dir = os.environ.get("P8P9_BASE_DIR", "data/p8p9_jobs")
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
            cur["version"] = cur.get("version", 0) + 1
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
            cur["version"] = cur.get("version", 0) + 1
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


__all__ = [
    "ClosureLinkService",
    "LinkInvalid",
    "LinkExpired",
    "LinkExhausted",
    "LinkActorMismatch",
]