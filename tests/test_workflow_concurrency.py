"""工单文件并发、执行占用和审批幂等测试。"""
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from agents.main_agent import confirm_and_continue
from agents.workflow import (
    add_job_log,
    claim_job_execution,
    clear_execution_registry,
    get_execution_status,
    get_job_execution_owner,
    get_stage_result_path,
    get_workflow_status,
    init_execution_status,
    init_workflow_status,
    read_json_file,
    release_job_execution,
    update_stage_status,
    update_workflow_status,
    write_json_file,
)


class WorkflowFileConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs_dir_patch = patch(
            "agents.workflow.file_utils.get_jobs_dir",
            return_value=self.temp_dir.name,
        )
        self.jobs_dir_patch.start()

    def tearDown(self):
        self.jobs_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_concurrent_log_appends_do_not_lose_entries(self):
        job_id = "job-logs"

        def append(index):
            add_job_log(job_id, {"message": f"log-{index}"})

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(append, range(120)))

        logs = read_json_file(os.path.join(self.temp_dir.name, job_id, "logs.json"))
        self.assertEqual(len(logs), 120)
        self.assertEqual({item["message"] for item in logs}, {f"log-{i}" for i in range(120)})

    def test_atomic_write_leaves_complete_json_and_no_temp_file(self):
        path = os.path.join(self.temp_dir.name, "job-atomic", "state.json")
        payload = {"items": list(range(5000))}

        write_json_file(path, payload)

        self.assertEqual(read_json_file(path), payload)
        leftovers = [name for name in os.listdir(os.path.dirname(path)) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_concurrent_status_updates_keep_both_stage_changes(self):
        job_id = "job-status"
        init_workflow_status(job_id)
        barrier = threading.Barrier(3)

        def update_p1():
            barrier.wait()
            update_workflow_status(job_id, {"P1_status": "completed"})

        def update_p2():
            barrier.wait()
            update_workflow_status(job_id, {"P2_status": "running"})

        first = threading.Thread(target=update_p1)
        second = threading.Thread(target=update_p2)
        first.start()
        second.start()
        barrier.wait()
        first.join(timeout=2)
        second.join(timeout=2)

        status = get_workflow_status(job_id)
        self.assertEqual(status["agents"]["P1"]["status"], "completed")
        self.assertEqual(status["agents"]["P2"]["status"], "running")


class ExecutionGuardTests(unittest.TestCase):
    def setUp(self):
        clear_execution_registry()

    def tearDown(self):
        clear_execution_registry()

    def test_same_job_can_only_be_claimed_once(self):
        first = claim_job_execution("job-a", "start")
        second = claim_job_execution("job-a", "resume")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(get_job_execution_owner("job-a"), "start")
        self.assertTrue(release_job_execution("job-a", first))
        self.assertIsNotNone(claim_job_execution("job-a", "resume"))

    def test_different_jobs_do_not_block_each_other(self):
        first = claim_job_execution("job-a", "start")
        second = claim_job_execution("job-b", "start")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)


class ConfirmationIdempotencyTests(unittest.TestCase):
    def setUp(self):
        clear_execution_registry()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs_dir_patch = patch(
            "agents.workflow.file_utils.get_jobs_dir",
            return_value=self.temp_dir.name,
        )
        self.jobs_dir_patch.start()

    def tearDown(self):
        clear_execution_registry()
        self.jobs_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_concurrent_approval_only_runs_next_stage_once(self):
        job_id = "job-confirm"
        init_execution_status(job_id)
        init_workflow_status(job_id)
        update_stage_status(job_id, "P2", "running")
        update_stage_status(job_id, "P2", "waiting")
        update_workflow_status(job_id, {
            "P2_status": "waiting",
            "main_agent": {
                "status": "waiting",
                "current_stage": "P2",
                "pending_confirmations": ["P2"],
            },
        })
        write_json_file(get_stage_result_path(job_id, "p2"), {
            "job_id": job_id,
            "stage": "P2",
            "completed": True,
            "pending_confirmation": {"message": "等待确认"},
        })
        next_stage_started = threading.Event()
        allow_next_stage_finish = threading.Event()

        def execute_p3(_job_id):
            next_stage_started.set()
            allow_next_stage_finish.wait(timeout=2)
            return {"completed": True}

        p3 = Mock(side_effect=execute_p3)
        results = []

        def approve():
            results.append(confirm_and_continue(job_id, "P2", "approve"))

        with (
            patch("agents.main_agent.STAGE_EXECUTORS", {"P2": Mock(), "P3": p3}),
            patch("agents.main_agent.STAGE_CONFIG", {"P3": {"max_attempts": 1}}),
            patch("agents.main_agent._broadcast_state"),
        ):
            first = threading.Thread(target=approve)
            first.start()
            self.assertTrue(next_stage_started.wait(timeout=2))
            second = threading.Thread(target=approve)
            second.start()
            second.join(timeout=2)
            allow_next_stage_finish.set()
            first.join(timeout=2)

        self.assertEqual(p3.call_count, 1)
        self.assertEqual(
            sorted(item["status"] for item in results),
            ["already_processed", "completed"],
        )
        workflow = get_workflow_status(job_id)
        self.assertEqual(workflow["main_agent"]["status"], "completed")
        self.assertEqual(get_execution_status(job_id)["stages"]["P3"]["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
