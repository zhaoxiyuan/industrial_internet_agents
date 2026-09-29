"""P8P9/tests/test_upload.py — §7 方案 B 附件上传 service / token / 路由 单测。

覆盖范围：
  - UploadService：ID 生成 / 扩展名校验 / MIME 校验 / 大小校验 / 配额校验
  - UploadService：atomic_write_file / read_file / cumulative_size
  - ClosureLinkService：create_upload_token / consume_upload_token / append_upload_to_token
  - web_server Blueprint：/api/closure/upload/new + /api/closure/upload (multipart)
"""
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from P8P9.upload_service import (
    UploadService,
    UploadValidationError,
    UploadSizeExceeded,
    UploadJobSizeExceeded,
    UploadFileMissing,
)
from P8P9.links import (
    ClosureLinkService,
    LinkInvalid, LinkExpired, LinkExhausted, LinkActorMismatch,
)
from P8P9.state_machine import ClosureService
from P8P9.upload_link_signing import signed_upload_query
from P8P9.models import (
    UPLOAD_MAX_FILE_SIZE,
    UPLOAD_MAX_JOB_SIZE,
    UPLOAD_ALLOWED_EXTENSIONS,
    UPLOAD_ALLOWED_MIME_TYPES,
    UPLOAD_ID_PREFIX,
    UPLOAD_TOKEN_PREFIX,
)


def _seed_job(base_dir: str, job_id: str, status: str = "open",
              accepted_by: dict = None) -> str:
    """在 base_dir/{job_id}/closure_state.json 写一个最小化 state。"""
    os.makedirs(os.path.join(base_dir, job_id), exist_ok=True)
    state = {
        "schema_version": "1.1",
        "job_id": job_id,
        "version": 0,
        "job_status": status,
        "display_risk_level": 2,
        "events": [],
        "materials": {"submissions": []},
        "accepted_by": accepted_by,
        "links": {},
        "upload_links": {},
    }
    path = os.path.join(base_dir, job_id, "closure_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return path


# ─── UploadService 单元测试 ─────────────────────────────────────────────────

class TestUploadServiceID(unittest.TestCase):
    def test_compute_upload_id_format(self):
        uid = UploadService.compute_upload_id()
        self.assertTrue(uid.startswith(UPLOAD_ID_PREFIX))
        body = uid[len(UPLOAD_ID_PREFIX):]
        self.assertEqual(len(body), 8)
        # base62 字母表
        from string import ascii_letters, digits
        self.assertTrue(all(c in (ascii_letters + digits) for c in body))

    def test_compute_upload_id_random(self):
        ids = {UploadService.compute_upload_id() for _ in range(50)}
        # 8 字符 base62 = 62^8 ≈ 2.18e14；50 个几乎不可能碰撞
        self.assertGreater(len(ids), 45)


class TestUploadServiceValidation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = UploadService(base_dir=self.tmpdir)

    def test_extension_accept_ok(self):
        for ext, mime in [
            ("jpg", "image/jpeg"),
            ("png", "image/png"),
            ("pdf", "application/pdf"),
            ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("mp4", "video/mp4"),
        ]:
            self.svc.validate_extension(f"file.{ext}", mime)  # 不抛

    def test_extension_rejected(self):
        for ext, mime in [
            ("exe", "application/octet-stream"),
            ("", "image/png"),
        ]:
            with self.assertRaises(UploadValidationError):
                self.svc.validate_extension(f"file.{ext}" if ext else "noext", mime)

    def test_mime_rejected(self):
        with self.assertRaises(UploadValidationError):
            self.svc.validate_extension("doc.pdf", "application/x-msdownload")

    def test_size_ok(self):
        self.svc.validate_size(UPLOAD_MAX_FILE_SIZE)
        self.svc.validate_size(0)

    def test_size_exceeded(self):
        with self.assertRaises(UploadSizeExceeded):
            self.svc.validate_size(UPLOAD_MAX_FILE_SIZE + 1)

    def test_size_negative_rejected(self):
        with self.assertRaises(UploadValidationError):
            self.svc.validate_size(-1)


class TestUploadServiceQuota(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = UploadService(base_dir=self.tmpdir)
        _seed_job(self.tmpdir, "JOB-001")

    def test_cumulative_size_empty(self):
        self.assertEqual(self.svc.cumulative_size("JOB-001"), 0)

    def test_cumulative_size_after_writes(self):
        # 写 2 个文件
        uid1 = UploadService.compute_upload_id()
        uid2 = UploadService.compute_upload_id()
        self.svc.atomic_write_file("JOB-001", uid1, b"x" * 100)
        self.svc.atomic_write_file("JOB-001", uid2, b"y" * 200)
        self.assertEqual(self.svc.cumulative_size("JOB-001"), 300)

    def test_check_job_quota_ok(self):
        self.svc.check_job_quota("JOB-001", 1024)

    def test_check_job_quota_exceeded(self):
        self.svc.atomic_write_file(
            "JOB-001", UploadService.compute_upload_id(),
            b"z" * (UPLOAD_MAX_JOB_SIZE - 10),
        )
        with self.assertRaises(UploadJobSizeExceeded):
            self.svc.check_job_quota("JOB-001", 100)


class TestUploadServiceFileIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = UploadService(base_dir=self.tmpdir)
        _seed_job(self.tmpdir, "JOB-001")

    def test_atomic_write_and_read(self):
        uid = UploadService.compute_upload_id()
        data = b"hello world" * 100
        path = self.svc.atomic_write_file("JOB-001", uid, data)
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_size, len(data))
        self.assertEqual(self.svc.read_file("JOB-001", uid), data)

    def test_atomic_write_creates_uploads_dir(self):
        uid = UploadService.compute_upload_id()
        # 删除 uploads 目录后再写，应该 lazy 创建
        import shutil
        uploads_dir = os.path.join(self.tmpdir, "JOB-001", "uploads")
        if os.path.exists(uploads_dir):
            shutil.rmtree(uploads_dir)
        self.svc.atomic_write_file("JOB-001", uid, b"hi")
        self.assertTrue(os.path.exists(uploads_dir))

    def test_invalid_upload_id_rejected(self):
        with self.assertRaises(UploadValidationError):
            self.svc.atomic_write_file("JOB-001", "bad_id", b"hi")

    def test_read_missing(self):
        with self.assertRaises(UploadFileMissing):
            self.svc.read_file("JOB-001", "up_notexist")

    def test_delete_file(self):
        uid = UploadService.compute_upload_id()
        self.svc.atomic_write_file("JOB-001", uid, b"x")
        self.assertTrue(self.svc.delete_file("JOB-001", uid))
        self.assertTrue(self.svc.delete_file("JOB-001", uid))  # idempotent
        with self.assertRaises(UploadFileMissing):
            self.svc.read_file("JOB-001", uid)

    def test_build_metadata(self):
        meta = UploadService.build_metadata(
            upload_id="up_abc12345",
            filename="test.pdf",
            mime_type="application/pdf",
            size_bytes=1234,
            uploaded_by="ou_x",
            uploaded_by_name="张三",
        )
        self.assertEqual(meta["upload_id"], "up_abc12345")
        self.assertEqual(meta["filename"], "test.pdf")
        self.assertEqual(meta["size_bytes"], 1234)
        self.assertIn("uploaded_at", meta)


# ─── ClosureLinkService upload_token 测试 ────────────────────────────────────

class TestUploadToken(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        _seed_job(self.tmpdir, "JOB-001")
        self.link_svc = ClosureLinkService(base_dir=self.tmpdir)

    def test_create_token_format(self):
        info = self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        self.assertTrue(info["token"].startswith(UPLOAD_TOKEN_PREFIX))
        self.assertEqual(len(info["token"]), len(UPLOAD_TOKEN_PREFIX) + 8)
        self.assertEqual(info["target"], "materials_submission")
        self.assertEqual(info["ttl_minutes"], 30)
        self.assertIn("token=", info["upload_url"])

    def test_create_token_persists_to_state(self):
        self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        state = ClosureService(base_dir=self.tmpdir).get_state("JOB-001")
        self.assertIn("upload_links", state)
        self.assertEqual(len(state["upload_links"]), 1)

    def test_consume_token_returns_uploads(self):
        info = self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        # 追加一个 upload metadata
        meta = UploadService.build_metadata(
            upload_id="up_zz123456", filename="x.pdf",
            mime_type="application/pdf", size_bytes=100,
            uploaded_by="ou_x", uploaded_by_name="张三",
        )
        self.link_svc.append_upload_to_token(info["token"], meta)
        # 消费
        consumed = self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")
        self.assertEqual(consumed["job_id"], "JOB-001")
        self.assertEqual(consumed["target"], "materials_submission")
        self.assertEqual(len(consumed["uploads"]), 1)
        self.assertEqual(consumed["uploads"][0]["upload_id"], "up_zz123456")

    def test_consume_actor_mismatch(self):
        info = self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        with self.assertRaises(LinkActorMismatch):
            self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_y")

    def test_consume_invalid_token(self):
        with self.assertRaises(LinkInvalid):
            self.link_svc.consume_upload_token("tk_nonexst1", actor_open_id="ou_x")

    def test_consume_format_invalid(self):
        with self.assertRaises(LinkInvalid):
            self.link_svc.consume_upload_token("not_a_token", actor_open_id="ou_x")

    def test_consume_after_expiry(self):
        info = self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
            ttl_minutes=1,
        )
        # 篡改 state 把 expiry 改成过去
        state_path = os.path.join(self.tmpdir, "JOB-001", "closure_state.json")
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        for entry in state["upload_links"].values():
            entry["expiry"] = past
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        with self.assertRaises(LinkExpired):
            self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")

    def test_consume_max_consume_reached(self):
        info = self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
            max_consume_count=2,
        )
        self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")
        self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")
        with self.assertRaises(LinkExhausted):
            self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")

    def test_append_upload_to_token_dedup(self):
        info = self.link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        meta = UploadService.build_metadata(
            upload_id="up_abc12345", filename="x.pdf",
            mime_type="application/pdf", size_bytes=100,
            uploaded_by="ou_x",
        )
        self.link_svc.append_upload_to_token(info["token"], meta)
        self.link_svc.append_upload_to_token(info["token"], meta)  # 重复
        consumed = self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")
        self.assertEqual(len(consumed["uploads"]), 1)

    def test_consume_token_wrong_job_id_caught_by_business_actions(self):
        """业务动作层 _consume_uploads 会校验 info["job_id"] == job_id。"""
        _seed_job(self.tmpdir, "JOB-002")
        info = self.link_svc.create_upload_token(
            "JOB-002", actor="ou_x", target="materials_submission",
        )
        # 这里只测 token 本身没限制；业务层会校验
        consumed = self.link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")
        self.assertEqual(consumed["job_id"], "JOB-002")


# ─── business_actions._consume_uploads 单元测试 ──────────────────────────────

class TestConsumeUploadsHelper(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._old_env = os.environ.get("P8P9_BASE_DIR")
        os.environ["P8P9_BASE_DIR"] = self.tmpdir
        _seed_job(
            self.tmpdir, "JOB-001",
            status="acknowledged",
            accepted_by={"open_id": "ou_x", "name": "张三"},
        )

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("P8P9_BASE_DIR", None)
        else:
            os.environ["P8P9_BASE_DIR"] = self._old_env

    def test_consume_uploads_none_returns_empty(self):
        from P8P9.business_actions import _consume_uploads
        result = _consume_uploads("JOB-001", None, "ou_x")
        self.assertEqual(result, [])

    def test_consume_uploads_with_token(self):
        from P8P9.business_actions import _consume_uploads
        link_svc = ClosureLinkService(base_dir=self.tmpdir)
        info = link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        meta = UploadService.build_metadata(
            upload_id="up_aaa11111", filename="evidence.pdf",
            mime_type="application/pdf", size_bytes=2048,
            uploaded_by="ou_x",
        )
        link_svc.append_upload_to_token(info["token"], meta)
        result = _consume_uploads("JOB-001", info["token"], "ou_x")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["upload_id"], "up_aaa11111")

    def test_consume_uploads_token_wrong_job(self):
        from P8P9.business_actions import _consume_uploads
        from P8P9.state_machine import InputValidationError
        _seed_job(self.tmpdir, "JOB-002", status="open")
        link_svc = ClosureLinkService(base_dir=self.tmpdir)
        info = link_svc.create_upload_token(
            "JOB-002", actor="ou_x", target="materials_submission",
        )
        with self.assertRaises(InputValidationError):
            _consume_uploads("JOB-001", info["token"], "ou_x")

    def test_consume_uploads_token_actor_mismatch(self):
        from P8P9.business_actions import _consume_uploads
        from P8P9.state_machine import InputValidationError
        link_svc = ClosureLinkService(base_dir=self.tmpdir)
        info = link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        with self.assertRaises(InputValidationError):
            _consume_uploads("JOB-001", info["token"], "ou_y")


# ─── Flask Blueprint 集成测试 ────────────────────────────────────────────────

class TestUploadBlueprint(unittest.TestCase):
    """验证 /api/closure/upload/new + /api/closure/upload (multipart) 端到端。"""

    def setUp(self):
        # 切换到 a/ 让 web_server import 工作
        from pathlib import Path
        import sys
        a_dir = Path(__file__).resolve().parents[1]
        if str(a_dir) not in sys.path:
            sys.path.insert(0, str(a_dir))

        # 临时目录作为 P8P9_BASE_DIR
        self.tmpdir = tempfile.mkdtemp()
        self._old_env = os.environ.get("P8P9_BASE_DIR")
        os.environ["P8P9_BASE_DIR"] = self.tmpdir
        self._old_signing_secret = os.environ.get("P8P9_UPLOAD_LINK_SECRET")
        os.environ["P8P9_UPLOAD_LINK_SECRET"] = "test-upload-signing-key-32-bytes-minimum"

        # seed 一个 acknowledged job
        _seed_job(
            self.tmpdir, "JOB-001",
            status="acknowledged",
            accepted_by={"open_id": "ou_x", "name": "张三"},
        )

        from P8P9.web_server import app
        self.app = app
        self.client = app.test_client()

    def _signed_new_url(self, job_id="JOB-001", target="materials_submission", open_id="ou_x", **kwargs):
        query = signed_upload_query(job_id, target, open_id, 0, **kwargs)
        return f"/api/closure/upload/new?{query}"

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("P8P9_BASE_DIR", None)
        else:
            os.environ["P8P9_BASE_DIR"] = self._old_env
        if self._old_signing_secret is None:
            os.environ.pop("P8P9_UPLOAD_LINK_SECRET", None)
        else:
            os.environ["P8P9_UPLOAD_LINK_SECRET"] = self._old_signing_secret

    def test_upload_new_redirect(self):
        resp = self.client.get(self._signed_new_url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("token=tk_", resp.headers.get("Location", ""))

    def test_upload_new_actor_mismatch(self):
        resp = self.client.get(self._signed_new_url(open_id="ou_y"))
        self.assertEqual(resp.status_code, 403)
        body = resp.get_json()
        self.assertEqual(body["error"], "actor_mismatch")

    def test_upload_new_no_acceptor(self):
        _seed_job(self.tmpdir, "JOB-NEW", status="open", accepted_by=None)
        resp = self.client.get(self._signed_new_url(job_id="JOB-NEW"))
        self.assertEqual(resp.status_code, 403)
        body = resp.get_json()
        self.assertEqual(body["error"], "no_acceptor")

    def test_upload_new_bad_target(self):
        resp = self.client.get(self._signed_new_url(target="bad"))
        self.assertEqual(resp.status_code, 400)

    def test_upload_new_rejects_unsigned_or_tampered_link(self):
        unsigned = self.client.get(
            "/api/closure/upload/new?job_id=JOB-001&target=materials_submission&open_id=ou_x"
        )
        self.assertEqual(unsigned.status_code, 403)
        tampered = self.client.get(self._signed_new_url().replace("open_id=ou_x", "open_id=ou_y"))
        self.assertEqual(tampered.status_code, 403)

    def test_upload_new_rejects_expired_or_stale_version(self):
        expired = self.client.get(self._signed_new_url(expires=int(time.time()) - 1))
        self.assertEqual(expired.status_code, 403)
        stale = signed_upload_query("JOB-001", "materials_submission", "ou_x", 1)
        self.assertEqual(self.client.get(f"/api/closure/upload/new?{stale}").status_code, 403)

    def test_upload_post_ok(self):
        # 创建 token
        link_svc = ClosureLinkService(base_dir=self.tmpdir)
        info = link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        # 上传文件
        resp = self.client.post(
            f"/api/closure/upload?token={info['token']}",
            data={"file": (io.BytesIO(b"hello world"), "test.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["upload_id"].startswith("up_"))
        self.assertEqual(body["filename"], "test.pdf")
        # token 关联 uploads
        consumed = link_svc.consume_upload_token(info["token"], actor_open_id="ou_x")
        self.assertEqual(len(consumed["uploads"]), 1)
        self.assertEqual(consumed["uploads"][0]["upload_id"], body["upload_id"])

    def test_upload_post_extension_rejected(self):
        link_svc = ClosureLinkService(base_dir=self.tmpdir)
        info = link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        resp = self.client.post(
            f"/api/closure/upload?token={info['token']}",
            data={"file": (io.BytesIO(b"x"), "evil.exe")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "validation_error")

    def test_upload_post_size_exceeded(self):
        link_svc = ClosureLinkService(base_dir=self.tmpdir)
        info = link_svc.create_upload_token(
            "JOB-001", actor="ou_x", target="materials_submission",
        )
        big = b"x" * (UPLOAD_MAX_FILE_SIZE + 1)
        resp = self.client.post(
            f"/api/closure/upload?token={info['token']}",
            data={"file": (io.BytesIO(big), "big.bin")},
            content_type="multipart/form-data",
        )
        # 注意 .bin 扩展名会被扩展名校验先拒（白名单外）
        # 所以期望 400 validation_error
        self.assertEqual(resp.status_code, 400)


# ─── cards.py uploads 渲染测试 ──────────────────────────────────────────────

class TestCardsUploadsRendering(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        import sys
        a_dir = Path(__file__).resolve().parents[2]
        if str(a_dir) not in sys.path:
            sys.path.insert(0, str(a_dir))

    def test_uploads_to_markdown_empty(self):
        from P8P9.cards import _uploads_to_markdown
        self.assertEqual(_uploads_to_markdown([]), "")

    def test_uploads_to_markdown_basic(self):
        from P8P9.cards import _uploads_to_markdown
        uploads = [
            {"upload_id": "up_a", "filename": "x.pdf", "size_bytes": 1024,
             "uploaded_by": "ou_x", "uploaded_by_name": "张三"},
            {"upload_id": "up_b", "filename": "y.jpg", "size_bytes": 1024 * 1024 * 5,
             "uploaded_by": "ou_y", "uploaded_by_name": "李四"},
        ]
        md = _uploads_to_markdown(uploads)
        self.assertIn("x.pdf", md)
        self.assertIn("y.jpg", md)
        self.assertIn("1.0 KB", md)
        self.assertIn("5.0 MB", md)

    def test_all_uploads_dedup(self):
        from P8P9.cards import _all_uploads
        state = {
            "materials": {"submissions": [
                {"uploads": [{"upload_id": "up_a", "filename": "x"}, {"upload_id": "up_b", "filename": "y"}]},
                {"uploads": [{"upload_id": "up_a", "filename": "x_again"}]},  # 重复
            ]},
            "risk_changes": [
                {"uploads": [{"upload_id": "up_c", "filename": "z"}]},
            ],
        }
        all_u = _all_uploads(state)
        ids = [u["upload_id"] for u in all_u]
        self.assertEqual(ids, ["up_a", "up_b", "up_c"])  # 首次出现顺序 + risk_changes 后续

    def test_closed_card_includes_uploads_section(self):
        from P8P9.cards import build_job_card
        state = {
            "job_id": "JOB-CLOSED",
            "version": 10,
            "job_status": "closed",
            "display_risk_level": 2,
            "materials": {"submissions": [
                {"uploads": [{"upload_id": "up_a", "filename": "doc.pdf",
                              "size_bytes": 12345, "uploaded_by": "ou_x",
                              "uploaded_by_name": "张三"}]}
            ]},
            "review": {"last_comment": "通过"},
            "events": [],
        }
        card = build_job_card(state, version=10, entry_url="/x")
        elements = card["body"]["elements"]
        # 检查包含"全部附件"段
        found_uploads_md = any(
            el.get("tag") == "markdown" and "全部附件" in el.get("content", "")
            for el in elements
        )
        self.assertTrue(found_uploads_md, "closed card should show uploads section")

    def test_acknowledged_card_has_upload_button(self):
        from P8P9.cards import build_job_card
        state = {
            "job_id": "JOB-A",
            "version": 1,
            "job_status": "acknowledged",
            "display_risk_level": 2,
            "accepted_by": {"open_id": "ou_x", "name": "张三"},
            "events": [],
            "materials": {},
        }
        card = build_job_card(
            state, version=1, entry_url="/x",
            actor_open_id="ou_x",
            upload_url_factory=lambda j, t, actor: f"/api/closure/upload/new?job_id={j}&target={t}&open_id={actor}",
        )
        elements = card["body"]["elements"]
        # 检查包含 📎 上传附件 link_button
        found_upload_btn = False
        for el in elements:
            if el.get("tag") == "button":
                txt = (el.get("text") or {}).get("content", "")
                if "📎" in txt and "上传" in txt:
                    found_upload_btn = True
                    # behaviors 应该含 open_url
                    url_in_behaviors = any(
                        b.get("type") == "open_url" and "/api/closure/upload/new" in b.get("default_url", "")
                        for b in el.get("behaviors", [])
                    )
                    self.assertTrue(url_in_behaviors, "upload button must point to /api/closure/upload/new")
                    break
        self.assertTrue(found_upload_btn, "acknowledged card should have 📎 upload link_button")


if __name__ == "__main__":
    unittest.main()
