# P8P9/upload_service.py — 附件上传 service（§7 方案 B·独立上传服务）
#
# 设计依据：
#   - 飞书 Card 2.0 没有原生「文件上传」组件，只能文字 / 图片
#   - 我们走「短时 upload token → 上传页 HTML（独立路由）→ 业务动作接收 upload_ids」
#   - 数据隔离：文件存磁盘 .bin，元数据存 state.materials.submissions[].uploads[]
#   - 校验：扩展名 + MIME 双重 + 单文件 ≤20MB + 单 job 累计 ≤100MB
#   - 可见性：upload token 仅 accepted_by 本人 / record_closure_review 决策人可消费
#
# 公开 API：
#   - UploadService.compute_upload_id() -> str                    # 生成 upload_id
#   - UploadService.job_uploads_dir(job_id) -> Path              # {job_id}/uploads/
#   - UploadService.file_path(job_id, upload_id) -> Path          # .../{upload_id}.bin
#   - UploadService.validate_extension(filename, mime) -> None   # 校验扩展名 / MIME
#   - UploadService.validate_size(file_bytes_len) -> None        # 校验大小
#   - UploadService.atomic_write_file(job_id, upload_id, bytes_) -> None
#   - UploadService.cumulative_size(job_id) -> int               # 累计已用
#   - UploadService.append_metadata(job_id, meta, *, target_path) -> dict
#   - UploadService.read_file(job_id, upload_id) -> bytes         # 下载用
#   - UploadService.delete_file(job_id, upload_id) -> bool        # 清理（测试 / 取消）
#
# 异常类：
#   - UploadValidationError: 扩展名 / MIME / 大小非法
#   - UploadSizeExceeded: 单文件 > 20MB
#   - UploadJobSizeExceeded: job 累计 > 100MB
#   - UploadFileMissing: 上传后文件丢失（disk failure）
#
# 持久化：
#   - 文件: {base_dir}/{job_id}/uploads/{upload_id}.bin（原子写）
#   - 元数据: state["materials"]["submissions"][i]["uploads"][k] = {...}
#           / state["risk_changes"][i]["uploads"][k] = {...}

from __future__ import annotations
import os
import re
import secrets
import string
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .models import (
    UPLOAD_MAX_FILE_SIZE,
    UPLOAD_MAX_JOB_SIZE,
    UPLOAD_ALLOWED_EXTENSIONS,
    UPLOAD_ALLOWED_MIME_TYPES,
    UPLOAD_ID_PREFIX,
    UPLOAD_ID_LEN,
)


# ─── 异常类 ──────────────────────────────────────────────────────────────────

class UploadValidationError(Exception):
    """上传参数非法（扩展名 / MIME / 元数据缺失）。"""


class UploadSizeExceeded(Exception):
    """单文件超过 UPLOAD_MAX_FILE_SIZE。"""


class UploadJobSizeExceeded(Exception):
    """job 累计已用 + 本次超过 UPLOAD_MAX_JOB_SIZE。"""


class UploadFileMissing(Exception):
    """上传后文件在磁盘上找不到（写入失败 / 被清理）。"""


# ─── 工具 ─────────────────────────────────────────────────────────────────────

# 8 字符 base62 字母表
_ID_ALPHABET = string.ascii_letters + string.digits


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_ext(filename: str) -> str:
    """提取扩展名（lower，无点）。"""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _safe_upload_id(upload_id: str) -> bool:
    """upload_id 必须是 up_ + 8 字符 base62。"""
    if not upload_id or not isinstance(upload_id, str):
        return False
    if not upload_id.startswith(UPLOAD_ID_PREFIX):
        return False
    body = upload_id[len(UPLOAD_ID_PREFIX):]
    if len(body) != UPLOAD_ID_LEN:
        return False
    return all(c in _ID_ALPHABET for c in body)


# ─── UploadService ────────────────────────────────────────────────────────────

class UploadService:
    """§7 附件上传 service。

    与 ClosureService / ClosureLinkService 同 base_dir（默认 data/jobs）。
    持久化布局：{base_dir}/{job_id}/uploads/{upload_id}.bin。
    """

    def __init__(self, base_dir: str = None) -> None:
        if base_dir is None:
            import os
            base_dir = os.environ.get("P8P9_BASE_DIR", "data/jobs")
        self.base_dir = Path(base_dir)

    # ─── 路径 ────────────────────────────────────────────────────────────────

    def job_uploads_dir(self, job_id: str) -> Path:
        """{base_dir}/{job_id}/uploads/（lazy 创建）。"""
        d = self.base_dir / job_id / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def file_path(self, job_id: str, upload_id: str) -> Path:
        if not _safe_upload_id(upload_id):
            raise UploadValidationError(f"upload_id 非法: {upload_id!r}")
        return self.job_uploads_dir(job_id) / f"{upload_id}.bin"

    # ─── ID ──────────────────────────────────────────────────────────────────

    @staticmethod
    def compute_upload_id() -> str:
        """生成 upload_id: up_ + 8 字符 base62。"""
        body = "".join(secrets.choice(_ID_ALPHABET) for _ in range(UPLOAD_ID_LEN))
        return f"{UPLOAD_ID_PREFIX}{body}"

    # ─── 校验 ────────────────────────────────────────────────────────────────

    def validate_extension(self, filename: str, mime_type: str) -> None:
        """校验扩展名 ∈ 白名单 且 MIME ∈ 白名单。

        Raises:
            UploadValidationError: 扩展名或 MIME 不在白名单。
        """
        ext = _split_ext(filename or "")
        if ext not in UPLOAD_ALLOWED_EXTENSIONS:
            raise UploadValidationError(
                f"扩展名 {ext!r} 不在白名单 {sorted(UPLOAD_ALLOWED_EXTENSIONS)}"
            )
        mime = (mime_type or "").lower().strip()
        if mime not in UPLOAD_ALLOWED_MIME_TYPES:
            raise UploadValidationError(
                f"MIME {mime!r} 不在白名单 {sorted(UPLOAD_ALLOWED_MIME_TYPES)}"
            )

    def validate_size(self, file_size: int) -> None:
        """校验单文件大小 ≤ UPLOAD_MAX_FILE_SIZE。

        Raises:
            UploadSizeExceeded: 单文件超过上限。
        """
        if not isinstance(file_size, int) or file_size < 0:
            raise UploadValidationError(f"file_size 非法: {file_size!r}")
        if file_size > UPLOAD_MAX_FILE_SIZE:
            raise UploadSizeExceeded(
                f"单文件 {file_size} > 上限 {UPLOAD_MAX_FILE_SIZE}"
            )

    def check_job_quota(self, job_id: str, incoming_size: int) -> None:
        """校验 job 累计 (已用 + incoming) ≤ UPLOAD_MAX_JOB_SIZE。

        Raises:
            UploadJobSizeExceeded: 累计超过上限。
        """
        used = self.cumulative_size(job_id)
        if used + incoming_size > UPLOAD_MAX_JOB_SIZE:
            raise UploadJobSizeExceeded(
                f"job={job_id} 累计 {used} + 本次 {incoming_size} "
                f"> 上限 {UPLOAD_MAX_JOB_SIZE}"
            )

    def cumulative_size(self, job_id: str) -> int:
        """扫描 uploads 目录，统计已存在的 .bin 文件总大小（字节）。"""
        d = self.base_dir / job_id / "uploads"
        if not d.exists():
            return 0
        total = 0
        for f in d.iterdir():
            if f.is_file() and f.suffix == ".bin":
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
        return total

    # ─── 文件读写 ────────────────────────────────────────────────────────────

    def atomic_write_file(self, job_id: str, upload_id: str, bytes_: bytes) -> Path:
        """原子写 .bin：tempfile + os.replace + fsync（与 ClosureService 同模式）。

        Raises:
            UploadValidationError: upload_id 非法。
            UploadFileMissing: 写完但路径找不到（理论上不可能，兜底）。
        """
        if not isinstance(bytes_, (bytes, bytearray)):
            raise UploadValidationError(
                f"file_bytes 必须是 bytes/bytearray，得到 {type(bytes_).__name__}"
            )
        dest = self.file_path(job_id, upload_id)
        tmp_path: Optional[str] = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".upload_", suffix=".bin.tmp",
                dir=str(dest.parent),
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(bytes_)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        # Windows 上 os.fsync 对某些文件类型可能不支持；不致命
                        pass
            finally:
                pass
            os.replace(tmp_path, dest)
        except Exception:
            # 清理可能残留的临时文件
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise
        if not dest.exists():
            raise UploadFileMissing(
                f"写后未找到: job={job_id} upload_id={upload_id} path={dest}"
            )
        return dest

    def read_file(self, job_id: str, upload_id: str) -> bytes:
        """读 .bin。raises UploadFileMissing 如果不存在。"""
        path = self.file_path(job_id, upload_id)
        if not path.exists():
            raise UploadFileMissing(
                f"找不到附件: job={job_id} upload_id={upload_id} path={path}"
            )
        with path.open("rb") as f:
            return f.read()

    def delete_file(self, job_id: str, upload_id: str) -> bool:
        """删除 .bin。返回是否删成功（不存在也算成功）。"""
        try:
            path = self.file_path(job_id, upload_id)
        except UploadValidationError:
            return False
        if not path.exists():
            return True
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # ─── 元数据 ──────────────────────────────────────────────────────────────

    @staticmethod
    def build_metadata(
        upload_id: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        uploaded_by: str,
        uploaded_by_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构造上传元数据 dict（业务方直接 append 到 state 的 uploads 列表）。"""
        return {
            "upload_id": upload_id,
            "filename": filename,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "uploaded_by": uploaded_by,
            "uploaded_by_name": uploaded_by_name,
            "uploaded_at": _now_iso(),
        }

    @staticmethod
    def target_uploads_list(state: Dict[str, Any], *, target: str) -> list:
        """根据 target 返回 / 创建 state 内对应列表的可写引用。

        target ∈ {"materials_submission", "risk_change"}。
        - materials_submission: state["materials"]["submissions"][last_index]["uploads"]
        - risk_change: state["risk_changes"][last_index]["uploads"]
        """
        if target == "materials_submission":
            submissions = state.setdefault("materials", {}).setdefault("submissions", [])
            if not submissions:
                raise UploadValidationError("materials.submissions 为空，无法 append upload")
            return submissions[-1].setdefault("uploads", [])
        if target == "risk_change":
            changes = state.setdefault("risk_changes", [])
            if not changes:
                raise UploadValidationError("risk_changes 为空，无法 append upload")
            return changes[-1].setdefault("uploads", [])
        raise UploadValidationError(f"未知 target: {target!r}")


__all__ = [
    "UploadService",
    "UploadValidationError",
    "UploadSizeExceeded",
    "UploadJobSizeExceeded",
    "UploadFileMissing",
]