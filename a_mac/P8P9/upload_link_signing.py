"""Signed, time-limited links for opening the public upload flow."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode


UPLOAD_LINK_TTL_SECONDS = 7 * 24 * 60 * 60


def _signing_key() -> bytes:
    key = os.environ.get("P8P9_UPLOAD_LINK_SECRET", "").encode("utf-8")
    if len(key) < 32:
        raise RuntimeError("P8P9_UPLOAD_LINK_SECRET must contain at least 32 bytes")
    return key


def _message(job_id: str, target: str, open_id: str, version: int, expires: int) -> bytes:
    return "\n".join((job_id, target, open_id, str(version), str(expires))).encode("utf-8")


def signed_upload_query(
    job_id: str, target: str, open_id: str, version: int, *, expires: int | None = None,
) -> str:
    """Create a bearer link tied to the job version and accepted person's ID."""
    expiry = int(expires if expires is not None else time.time() + UPLOAD_LINK_TTL_SECONDS)
    signature = hmac.new(
        _signing_key(), _message(job_id, target, open_id, version, expiry), hashlib.sha256,
    ).hexdigest()
    return urlencode({
        "job_id": job_id, "target": target, "open_id": open_id,
        "version": version, "expires": expiry, "sig": signature,
    })


def verify_upload_query(
    job_id: str, target: str, open_id: str, version: str,
    expires: str, signature: str, *, current_version: int,
) -> bool:
    """Reject tampered, stale or expired links without issuing an upload token."""
    try:
        signed_version = int(version)
        expiry = int(expires)
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if (not job_id or not target or not open_id or signed_version != current_version
            or expiry <= now or expiry > now + UPLOAD_LINK_TTL_SECONDS + 60
            or len(signature) != 64):
        return False
    expected = hmac.new(
        _signing_key(), _message(job_id, target, open_id, signed_version, expiry), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
