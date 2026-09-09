import json
import os
import tempfile
import unittest
from unittest.mock import patch

from agents.p1_permit_agent import _build_fallback_permit
from web.api.workflow import handle_workflow_history, handle_workflow_job_detail


class JsonHandler:
    def __init__(self):
        self.data = None
        self.status_code = None

    def send_json(self, data, status=200):
        self.data = data
        self.status_code = status


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)


class WorkflowHistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_job(self, job_id, status="waiting", stage="P1"):
        job_dir = os.path.join(self.jobs_dir, job_id)
        os.makedirs(job_dir)
        write_json(os.path.join(job_dir, "application.json"), {
            "application": {
                "job_type": "动火作业",
                "job_content": "F-0102 焊接修补",
                "region": "催化装置",
                "personnel": [{"name": "王德军"}],
            },
            "saved_at": "2026-09-09T01:00:00+00:00",
        })
        write_json(os.path.join(job_dir, "workflow_status.json"), {
            "created_at": "2026-09-09T01:00:00+00:00",
            "updated_at": "2026-09-09T02:00:00+00:00",
            "main_agent": {"status": status, "current_stage": stage},
            "agents": {"P1": {"status": "waiting" if status == "waiting" else "completed"}},
        })
        write_json(os.path.join(job_dir, "p1_result.json"), {
            "pending_confirmation": {"type": "permit_final_approval", "message": "请审批"},
            "missing_fields": ["job_level"],
        })
        write_json(os.path.join(job_dir, "permit.json"), {
            "permit_draft_id": "PD-1",
            "permit_content": {"job_type": "动火作业"},
        })
        return job_dir

    def workflow_patches(self):
        return (
            patch("agents.workflow.get_jobs_dir", return_value=self.jobs_dir),
            patch("agents.workflow.get_job_dir", side_effect=lambda job_id: os.path.join(self.jobs_dir, job_id)),
        )

    def test_history_lists_valid_jobs_and_marks_completed_read_only(self):
        self.make_job("20260909120000001", status="completed", stage="completed")
        self.make_job("20260909120000002", status="waiting", stage="P1")
        os.makedirs(os.path.join(self.jobs_dir, "test"))
        handler = JsonHandler()
        patches = self.workflow_patches()
        with patches[0], patches[1]:
            handle_workflow_history(handler, 50)

        self.assertEqual(handler.status_code, 200)
        self.assertEqual([item["job_id"] for item in handler.data["jobs"]], [
            "20260909120000002", "20260909120000001"
        ])
        self.assertTrue(handler.data["jobs"][0]["can_continue"])
        self.assertFalse(handler.data["jobs"][1]["can_continue"])

    def test_job_detail_contains_permit_for_p1_approval(self):
        self.make_job("20260909120000003")
        handler = JsonHandler()
        patches = self.workflow_patches()
        with patches[0], patches[1]:
            handle_workflow_job_detail(handler, "20260909120000003")

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.data["pending"], ["P1"])
        p1_data = handler.data["pending_data"]["P1"]
        self.assertEqual(p1_data["application"]["job_content"], "F-0102 焊接修补")
        self.assertEqual(p1_data["permit"]["permit_draft_id"], "PD-1")

    def test_job_detail_rejects_untrusted_path(self):
        handler = JsonHandler()
        handle_workflow_job_detail(handler, "../secrets")
        self.assertEqual(handler.status_code, 400)


class PermitFallbackTests(unittest.TestCase):
    def test_hot_work_fallback_uses_application_instead_of_fixed_mock(self):
        application = {
            "job_content": "对 F-0102 进行动火焊接",
            "region": "催化装置",
            "equipment": ["F-0102"],
            "personnel": [{"name": "王德军", "qualifications": ["焊工证"]}],
            "planned_start": "2026-09-09T09:00:00+08:00",
            "planned_end": "2026-09-09T17:00:00+08:00",
            "job_level": "二级",
        }
        jsa, permit, missing = _build_fallback_permit(application, "20260909120000004")
        self.assertEqual(permit["job_type"], "动火作业")
        self.assertEqual(permit["equipment"], ["F-0102"])
        self.assertTrue(any(item["description"] == "火灾、爆炸" for item in jsa["hazards"]))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
