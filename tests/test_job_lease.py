"""工单页面占用（租约）测试：占用、续期、超时释放与接口拦截。"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from agents.workflow.job_lease import (
    claim_job_lease,
    clear_job_leases,
    get_job_lease,
    get_occupant,
    heartbeat_job_lease,
    is_job_lease_holder,
    release_job_lease,
)
from web.api.workflow import (
    OCCUPIED_ERROR,
    handle_workflow_claim,
    handle_workflow_confirm,
    handle_workflow_heartbeat,
    handle_latest_incomplete_workflow,
    handle_workflow_release,
    handle_workflow_resume,
)


class JsonHandler:
    """与 tests/test_workflow_history.py 一致的简化响应收集器。"""

    def __init__(self):
        self.data = None
        self.status_code = None

    def send_json(self, data, status=200):
        self.data = data
        self.status_code = status


JOB = "20260914120000001"
OTHER_JOB = "20260914120000002"


class JobLeaseCoreTests(unittest.TestCase):
    def setUp(self):
        clear_job_leases()

    def tearDown(self):
        clear_job_leases()

    def test_first_claim_wins_and_records_owner(self):
        lease = claim_job_lease(JOB, "page-a", "页面#aaaaaa")

        self.assertIsNotNone(lease)
        self.assertEqual(lease["holder_id"], "page-a")
        self.assertEqual(lease["holder_label"], "页面#aaaaaa")
        # 持有者看自己不算被占用
        self.assertIsNone(get_occupant(JOB, "page-a"))

    def test_second_page_is_refused(self):
        claim_job_lease(JOB, "page-a")

        self.assertIsNone(claim_job_lease(JOB, "page-b"))
        occupant = get_occupant(JOB, "page-b")
        self.assertEqual(occupant["holder_id"], "page-a")

    def test_strict_holder_check_rejects_missing_or_expired_lease(self):
        self.assertFalse(is_job_lease_holder(JOB, "page-a"))
        claim_job_lease(JOB, "page-a")
        self.assertTrue(is_job_lease_holder(JOB, "page-a"))
        self.assertFalse(is_job_lease_holder(JOB, "page-b"))
        self.assertFalse(is_job_lease_holder(JOB, None))

        with patch("agents.workflow.job_lease.LEASE_TTL_SECONDS", 0.0):
            time.sleep(0.001)
            self.assertFalse(is_job_lease_holder(JOB, "page-a"))

    def test_heartbeat_and_release_only_work_for_holder(self):
        claim_job_lease(JOB, "page-a")

        self.assertTrue(heartbeat_job_lease(JOB, "page-a"))
        self.assertFalse(heartbeat_job_lease(JOB, "page-b"))
        # 非持有者不能释放，避免旧页面误释放新页面的租约
        self.assertFalse(release_job_lease(JOB, "page-b"))
        self.assertIsNotNone(get_job_lease(JOB))
        self.assertTrue(release_job_lease(JOB, "page-a"))
        self.assertIsNone(get_job_lease(JOB))

    def test_expired_lease_is_released_automatically(self):
        claim_job_lease(JOB, "page-a")

        with patch("agents.workflow.job_lease.LEASE_TTL_SECONDS", 0.2):
            time.sleep(0.3)
            # 心跳超时：原页面失去占用
            self.assertFalse(heartbeat_job_lease(JOB, "page-a"))
            # 其他页面可以接手，不会因为对方页面消失而永久锁死
            lease = claim_job_lease(JOB, "page-b", "页面#bbbbbb")
            self.assertIsNotNone(lease)
            self.assertEqual(lease["holder_id"], "page-b")

    def test_different_jobs_do_not_block_each_other(self):
        # 两个页面各拿一张工单，互不影响
        self.assertIsNotNone(claim_job_lease(JOB, "page-a"))
        self.assertIsNotNone(claim_job_lease(OTHER_JOB, "page-b"))

        # 同一张工单仍然只能有一个占用者
        self.assertIsNone(claim_job_lease(JOB, "page-b"))
        self.assertIsNone(claim_job_lease(OTHER_JOB, "page-a"))
        self.assertEqual(get_occupant(JOB, "page-b")["holder_id"], "page-a")
        self.assertEqual(get_occupant(OTHER_JOB, "page-a")["holder_id"], "page-b")


class JobLeaseApiTests(unittest.TestCase):
    def setUp(self):
        clear_job_leases()
        self.temp_dir = tempfile.TemporaryDirectory()
        # 两处都要补丁：file_utils 里的路径拼装，以及 web 层直接 import 的包级名字。
        self.patchers = [
            patch(
                "agents.workflow.file_utils.get_jobs_dir",
                return_value=self.temp_dir.name,
            ),
            patch(
                "agents.workflow.get_jobs_dir",
                return_value=self.temp_dir.name,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        self.temp_dir.cleanup()
        clear_job_leases()

    def make_waiting_job(self, job_id, stage="P4"):
        job_dir = os.path.join(self.temp_dir.name, job_id)
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, "workflow_status.json"), "w", encoding="utf-8") as file:
            json.dump({
                "main_agent": {"status": "waiting", "current_stage": stage},
                "agents": {stage: {"status": "waiting"}},
            }, file, ensure_ascii=False)
        return job_dir

    def test_claim_endpoint_reports_occupied_for_other_page(self):
        handler = JsonHandler()
        handle_workflow_claim(handler, {"job_id": JOB, "holder_id": "page-a"})
        self.assertEqual(handler.data["status"], "ok")
        self.assertFalse(handler.data["read_only"])

        other = JsonHandler()
        handle_workflow_claim(other, {"job_id": JOB, "holder_id": "page-b"})
        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.data["status"], "occupied")
        self.assertEqual(other.data["error"], OCCUPIED_ERROR)
        self.assertTrue(other.data["read_only"])

    def test_claim_endpoint_validates_input(self):
        bad_job = JsonHandler()
        handle_workflow_claim(bad_job, {"job_id": "not-a-job", "holder_id": "page-a"})
        self.assertEqual(bad_job.status_code, 400)

        no_holder = JsonHandler()
        handle_workflow_claim(no_holder, {"job_id": JOB, "holder_id": ""})
        self.assertEqual(no_holder.status_code, 400)

    def test_same_page_can_renew_without_conflict(self):
        first = JsonHandler()
        handle_workflow_claim(first, {"job_id": JOB, "holder_id": "page-a"})

        again = JsonHandler()
        handle_workflow_claim(again, {"job_id": JOB, "holder_id": "page-a"})
        self.assertEqual(again.data["status"], "ok")

    def test_other_page_cannot_replace_active_lease(self):
        handle_workflow_claim(JsonHandler(), {"job_id": JOB, "holder_id": "page-a"})
        other = JsonHandler()
        handle_workflow_claim(other, {"job_id": JOB, "holder_id": "page-b"})

        handler = JsonHandler()
        handle_workflow_heartbeat(handler, {"job_id": JOB, "holder_id": "page-a"})

        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.data["status"], "occupied")
        self.assertEqual(handler.data["status"], "ok")
        self.assertFalse(handler.data["read_only"])

    def test_release_endpoint_only_releases_own_lease(self):
        handle_workflow_claim(JsonHandler(), {"job_id": JOB, "holder_id": "page-a"})

        other = JsonHandler()
        handle_workflow_release(other, {"job_id": JOB, "holder_id": "page-b"})
        self.assertFalse(other.data["released"])
        self.assertIsNotNone(get_job_lease(JOB))

        mine = JsonHandler()
        handle_workflow_release(mine, {"job_id": JOB, "holder_id": "page-a"})
        self.assertTrue(mine.data["released"])
        self.assertIsNone(get_job_lease(JOB))

    def test_confirm_is_refused_while_another_page_holds_the_job(self):
        """被占用的工单：审批请求直接返回 409，不进入执行流程。"""
        handle_workflow_claim(JsonHandler(), {"job_id": JOB, "holder_id": "page-a"})

        handler = JsonHandler()
        with patch("agents.main_agent.confirm_and_continue") as confirm:
            handle_workflow_confirm(handler, {
                "thread_id": JOB,
                "stage": "P1",
                "decision": "approve",
                "holder_id": "page-b",
                "async_execute": True,
            })

        self.assertEqual(handler.status_code, 409)
        self.assertEqual(handler.data["error"], OCCUPIED_ERROR)
        confirm.assert_not_called()

    def test_confirm_allows_current_holder(self):
        handle_workflow_claim(JsonHandler(), {"job_id": JOB, "holder_id": "page-a"})

        handler = JsonHandler()
        with patch(
            "agents.main_agent.confirm_and_continue",
            return_value={"job_id": JOB, "status": "executing", "pending_confirmations": []},
        ) as confirm:
            handle_workflow_confirm(handler, {
                "thread_id": JOB,
                "stage": "P1",
                "decision": "approve",
                "holder_id": "page-a",
                "async_execute": True,
            })

        confirm.assert_called_once()
        self.assertEqual(handler.data["status"], "executing")

    def test_confirm_requires_an_existing_lease(self):
        for payload in (
            {"holder_id": "page-a"},
            {},
        ):
            handler = JsonHandler()
            with patch("agents.main_agent.confirm_and_continue") as confirm:
                handle_workflow_confirm(handler, {
                    "thread_id": JOB,
                    "stage": "P1",
                    "decision": "approve",
                    "async_execute": True,
                    **payload,
                })
            self.assertEqual(handler.status_code, 409)
            self.assertEqual(handler.data["status"], "lease_required")
            confirm.assert_not_called()

    def test_expired_lease_cannot_confirm(self):
        claim_job_lease(JOB, "page-a")
        with patch("agents.workflow.job_lease.LEASE_TTL_SECONDS", 0.0):
            time.sleep(0.001)
            handler = JsonHandler()
            with patch("agents.main_agent.confirm_and_continue") as confirm:
                handle_workflow_confirm(handler, {
                    "thread_id": JOB,
                    "stage": "P1",
                    "decision": "approve",
                    "holder_id": "page-a",
                    "async_execute": True,
                })
        self.assertEqual(handler.status_code, 409)
        self.assertEqual(handler.data["status"], "lease_required")
        confirm.assert_not_called()

    def test_resume_requires_an_existing_lease(self):
        handler = JsonHandler()
        handle_workflow_resume(handler, {"job_id": JOB, "holder_id": "page-a"})
        self.assertEqual(handler.status_code, 409)
        self.assertEqual(handler.data["status"], "lease_required")

    def test_latest_incomplete_skips_job_held_by_another_page(self):
        """新页面不应被自动拉去接管正由其他页面操作的工单。"""
        self.make_waiting_job(JOB)
        handle_workflow_claim(JsonHandler(), {"job_id": JOB, "holder_id": "page-a"})

        new_page = JsonHandler()
        handle_latest_incomplete_workflow(new_page, "page-b")
        self.assertEqual(new_page.data["status"], "none")

        # 持有者自己仍然能找回这张工单
        owner = JsonHandler()
        handle_latest_incomplete_workflow(owner, "page-a")
        self.assertEqual(owner.data["job_id"], JOB)


if __name__ == "__main__":
    unittest.main()
