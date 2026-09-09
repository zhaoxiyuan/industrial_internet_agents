"""执行状态、重试和断点恢复的回归测试。"""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agents.main_agent import confirm_and_continue, execute_stage_with_retry, run_workflow
from agents.p1_permit_agent import (
    clear_agent_registry,
    create_permit_agent_with_hitl,
    execute_stage as execute_p1_stage,
    run_permit_agent_with_hitl,
)
from agents.workflow.execution_status import (
    get_execution_status,
    init_execution_status,
    update_stage_status,
)
from agents.workflow.file_utils import get_stage_result_path, write_json_file
from agents.workflow.workflow_state import get_workflow_status, init_workflow_status


class ExecutionStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs_dir_patch = patch(
            "agents.workflow.file_utils.get_jobs_dir",
            return_value=self.temp_dir.name,
        )
        self.jobs_dir_patch.start()
        init_execution_status("job")

    def tearDown(self):
        self.jobs_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_attempt_is_persisted_before_terminal_status(self):
        update_stage_status("job", "P1", "running")
        update_stage_status("job", "P1", "failed", error="network timeout")

        stage = get_execution_status("job")["stages"]["P1"]
        self.assertEqual(stage["attempts"], 1)
        self.assertEqual(stage["history"][0]["attempt"], 1)
        self.assertEqual(stage["last_error_type"], "temporary")

    def test_waiting_is_not_recorded_as_failure(self):
        executor = Mock(return_value={
            "completed": False,
            "pending_confirmation": {"message": "等待确认"},
        })

        with patch("agents.main_agent.add_job_log"), patch("time.sleep"):
            result = execute_stage_with_retry(
                "job", "P1", executor, {"max_attempts": 3, "retry_on_temporary": True}
            )

        self.assertIn("pending_confirmation", result)
        stage = get_execution_status("job")["stages"]["P1"]
        self.assertEqual(stage["status"], "waiting")
        self.assertEqual(stage["attempts"], 1)
        self.assertIsNone(stage["last_error"])

        with patch("agents.main_agent.add_job_log"), patch("time.sleep"):
            execute_stage_with_retry(
                "job",
                "P1",
                Mock(return_value={"completed": True}),
                {"max_attempts": 3, "retry_on_temporary": True},
                continuation=True,
            )
        stage = get_execution_status("job")["stages"]["P1"]
        self.assertEqual(stage["attempts"], 1)
        self.assertEqual(len(stage["history"]), 1)
        self.assertEqual(stage["history"][0]["status"], "completed")

    def test_temporary_failure_retries_and_records_each_attempt(self):
        executor = Mock(side_effect=[
            {"completed": False, "error": "network timeout"},
            {"completed": True},
        ])

        with patch("agents.main_agent.add_job_log"), patch("time.sleep"):
            result = execute_stage_with_retry(
                "job", "P2", executor, {"max_attempts": 3, "retry_on_temporary": True}
            )

        self.assertTrue(result["completed"])
        stage = get_execution_status("job")["stages"]["P2"]
        self.assertEqual(stage["attempts"], 2)
        self.assertEqual([item["status"] for item in stage["history"]], ["failed", "completed"])


class ResumeWorkflowTests(unittest.TestCase):
    def test_resume_starts_at_requested_stage_without_reinitializing(self):
        p1 = Mock(return_value={"completed": True})
        p2 = Mock(return_value={"completed": True})
        p3 = Mock(return_value={"completed": True})
        executors = {"P1": p1, "P2": p2, "P3": p3}

        with (
            patch("agents.main_agent.STAGE_EXECUTORS", executors),
            patch("agents.main_agent.STAGE_CONFIG", {
                stage: {"critical": True, "max_attempts": 1, "retry_on_temporary": True}
                for stage in executors
            }),
            patch("agents.main_agent.save_job_application") as save_application,
            patch("agents.main_agent.init_workflow_status") as init_workflow,
            patch("agents.main_agent.init_execution_status") as init_execution,
            patch("agents.main_agent.get_execution_status", return_value={
                "stages": {
                    "P1": {"status": "completed"},
                    "P2": {"status": "completed"},
                    "P3": {"status": "completed"},
                }
            }),
            patch("agents.main_agent.update_workflow_status"),
            patch("agents.main_agent.finalize_execution_status"),
            patch("agents.main_agent.add_job_log"),
            patch("agents.main_agent._broadcast_state"),
            patch("agents.main_agent.execute_stage_with_retry", side_effect=lambda job, stage, executor, config, force=False: executor(job)),
        ):
            result = run_workflow(
                {"job_content": "demo"},
                thread_id="20260908123456789",
                start_stage="P2",
                resume=True,
            )

        self.assertEqual(result["status"], "completed")
        p1.assert_not_called()
        p2.assert_called_once()
        p3.assert_called_once()
        save_application.assert_not_called()
        init_workflow.assert_not_called()
        init_execution.assert_not_called()

    def test_critical_failure_stops_before_next_stage(self):
        p2 = Mock(return_value={"completed": False, "error": "invalid parameter"})
        p3 = Mock(return_value={"completed": True})
        executors = {"P2": p2, "P3": p3}

        with (
            patch("agents.main_agent.STAGE_EXECUTORS", executors),
            patch("agents.main_agent.STAGE_CONFIG", {
                "P2": {"critical": True, "max_attempts": 1},
                "P3": {"critical": True, "max_attempts": 1},
            }),
            patch("agents.main_agent.is_stage_critical", side_effect=lambda stage: stage == "P2"),
            patch("agents.main_agent.can_retry_stage", return_value=False),
            patch("agents.main_agent.update_workflow_status"),
            patch("agents.main_agent.add_job_log"),
            patch("agents.main_agent._broadcast_state"),
            patch("agents.main_agent.execute_stage_with_retry", side_effect=lambda job, stage, executor, config, force=False: executor(job)),
        ):
            result = run_workflow({}, "20260908123456789", start_stage="P2", resume=True)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["current_stage"], "P2")
        p3.assert_not_called()

    def test_noncritical_failure_is_preserved_and_flow_continues(self):
        p9 = Mock(return_value={"completed": False, "error": "archive unavailable"})
        p10 = Mock(return_value={"completed": True})
        executors = {"P9": p9, "P10": p10}
        status_updates = Mock()

        with (
            patch("agents.main_agent.STAGE_EXECUTORS", executors),
            patch("agents.main_agent.STAGE_CONFIG", {
                "P9": {"critical": False, "max_attempts": 1},
                "P10": {"critical": False, "max_attempts": 1},
            }),
            patch("agents.main_agent.is_stage_critical", return_value=False),
            patch("agents.main_agent.get_execution_status", return_value={
                "stages": {"P9": {"status": "failed"}, "P10": {"status": "completed"}}
            }),
            patch("agents.main_agent.finalize_execution_status"),
            patch("agents.main_agent.update_workflow_status", status_updates),
            patch("agents.main_agent.add_job_log"),
            patch("agents.main_agent._broadcast_state"),
            patch("agents.main_agent.execute_stage_with_retry", side_effect=lambda job, stage, executor, config, force=False: executor(job)),
        ):
            result = run_workflow({}, "20260908123456789", start_stage="P9", resume=True)

        self.assertEqual(result["status"], "completed")
        p10.assert_called_once()
        self.assertIn(
            call("20260908123456789", {
                "P9_status": "failed",
                "main_agent": {"status": "error", "current_stage": "P9", "error": "archive unavailable"},
            }),
            status_updates.call_args_list,
        )

    def test_reject_stops_workflow_and_marks_current_stage_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "agents.workflow.file_utils.get_jobs_dir", return_value=temp_dir
        ):
            init_execution_status("job")
            init_workflow_status("job")
            update_stage_status("job", "P2", "running")
            update_stage_status("job", "P2", "waiting")
            write_json_file(get_stage_result_path("job", "p2"), {
                "job_id": "job",
                "stage": "P2",
                "completed": True,
                "pending_confirmation": {"message": "等待确认"},
            })
            p3 = Mock(return_value={"completed": True})

            with (
                patch("agents.main_agent.STAGE_EXECUTORS", {"P2": Mock(), "P3": p3}),
                patch("agents.main_agent.save_confirmation"),
                patch("agents.main_agent.add_job_log"),
                patch("agents.main_agent._broadcast_state"),
            ):
                result = confirm_and_continue("job", "P2", "reject", notes="条件不满足")

            self.assertEqual(result["status"], "error")
            self.assertTrue(result["rejected"])
            self.assertTrue(result["can_retry"])
            p3.assert_not_called()
            self.assertEqual(get_execution_status("job")["stages"]["P2"]["status"], "failed")
            workflow = get_workflow_status("job")
            self.assertEqual(workflow["agents"]["P2"]["status"], "failed")
            self.assertEqual(workflow["main_agent"]["current_stage"], "P2")


class P1HitlTests(unittest.TestCase):
    def test_final_p1_approval_marks_waiting_attempt_completed(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "agents.workflow.file_utils.get_jobs_dir", return_value=temp_dir
        ):
            init_execution_status("job")
            init_workflow_status("job")
            update_stage_status("job", "P1", "running")
            update_stage_status("job", "P1", "waiting")
            write_json_file(get_stage_result_path("job", "p1"), {
                "job_id": "job",
                "stage": "P1",
                "completed": True,
                "pending_confirmation": {"type": "permit_final_approval"},
            })
            p2 = Mock(return_value={"completed": True})

            with (
                patch("agents.main_agent.STAGE_EXECUTORS", {"P1": Mock(), "P2": p2}),
                patch("agents.main_agent.STAGE_CONFIG", {"P2": {"max_attempts": 1}}),
                patch("agents.main_agent.save_confirmation"),
                patch("agents.main_agent.add_job_log"),
                patch("agents.main_agent._broadcast_state"),
            ):
                result = confirm_and_continue("job", "P1", "approve")

            self.assertEqual(result["status"], "completed")
            p2.assert_called_once_with("job")
            self.assertEqual(
                get_execution_status("job")["stages"]["P1"]["status"],
                "completed",
            )

    def test_p1_produces_one_final_stage_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "agents.workflow.file_utils.get_jobs_dir", return_value=temp_dir
        ):
            application_path = f"{temp_dir}/job/application.json"
            write_json_file(application_path, {"application": {"job_content": "测试作业"}})
            with (
                patch("agents.p1_permit_agent.is_agent_interrupted", return_value=False),
                patch("agents.p1_permit_agent.run_permit_agent_with_hitl", return_value={
                    "interrupted": False,
                    "result": '{"task_id":"T-1","permit_draft_id":"PD-1","missing_fields":[]}',
                    "next": [],
                }),
                patch("agents.p1_permit_agent.add_job_log"),
                patch("agents.p1_permit_agent.push_websocket_log"),
            ):
                result = execute_p1_stage("job")

        self.assertTrue(result["completed"])
        self.assertEqual(
            result["pending_confirmation"]["type"],
            "permit_final_approval",
        )

    def test_p1_agent_has_no_per_tool_approval_middleware(self):
        clear_agent_registry("single-approval")
        agent = Mock()
        with (
            patch("agents.p1_permit_agent.create_chat_model_with_logging", return_value=Mock()),
            patch("agents.p1_permit_agent.create_agent", return_value=agent) as create_agent,
        ):
            created = create_permit_agent_with_hitl("single-approval")

        self.assertIs(created, agent)
        self.assertNotIn("middleware", create_agent.call_args.kwargs)
        clear_agent_registry("single-approval")

    def test_reject_resets_p1_checkpoint_and_does_not_continue(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "agents.workflow.file_utils.get_jobs_dir", return_value=temp_dir
        ):
            init_execution_status("job")
            init_workflow_status("job")
            update_stage_status("job", "P1", "running")
            update_stage_status("job", "P1", "waiting")
            write_json_file(get_stage_result_path("job", "p1"), {
                "job_id": "job",
                "stage": "P1",
                "completed": False,
                "pending_confirmation": {"message": "等待工具确认"},
            })
            p2 = Mock(return_value={"completed": True})

            with (
                patch("agents.main_agent.STAGE_EXECUTORS", {"P1": Mock(), "P2": p2}),
                patch("agents.main_agent.reset_permit_execution") as reset_checkpoint,
                patch("agents.main_agent.save_confirmation"),
                patch("agents.main_agent.add_job_log"),
                patch("agents.main_agent._broadcast_state"),
            ):
                result = confirm_and_continue("job", "P1", "reject")

            self.assertTrue(result["rejected"])
            reset_checkpoint.assert_called_once_with("job")
            p2.assert_not_called()

    def test_first_run_invokes_only_hitl_agent_once(self):
        agent = Mock()
        agent.invoke.return_value = {}
        agent.get_state.return_value = SimpleNamespace(next=(), interrupts=())

        with (
            patch("agents.p1_permit_agent.create_permit_agent_with_hitl", return_value=agent),
            patch("agents.p1_permit_agent.create_chat_model_with_logging") as create_plain_model,
            patch("agents.p1_permit_agent.extract_output", return_value="done"),
            patch("agents.p1_permit_agent.push_websocket_log"),
        ):
            result = run_permit_agent_with_hitl("处理申请", "job")

        self.assertFalse(result["interrupted"])
        agent.invoke.assert_called_once()
        create_plain_model.assert_not_called()

    def test_resume_uses_checkpoint_command_and_approves_all_actions(self):
        agent = Mock()
        interrupt = SimpleNamespace(value={"action_requests": [{}, {}]})
        agent.get_state.side_effect = [
            SimpleNamespace(next=("middleware",), interrupts=(interrupt,)),
            SimpleNamespace(next=(), interrupts=()),
        ]
        agent.invoke.return_value = {"messages": []}

        with (
            patch("agents.p1_permit_agent.create_permit_agent_with_hitl", return_value=agent),
            patch("agents.p1_permit_agent.extract_output", return_value="done"),
            patch("agents.p1_permit_agent.push_websocket_log"),
        ):
            result = run_permit_agent_with_hitl(
                None, "job", resume=True, decision="approve"
            )

        command = agent.invoke.call_args.args[0]
        self.assertEqual(command.resume, {
            "decisions": [{"type": "approve"}, {"type": "approve"}]
        })
        self.assertFalse(result["interrupted"])
        self.assertEqual(result["result"], "done")


class P6MonitorTests(unittest.TestCase):
    def test_http_handler_does_not_override_workflow_tool(self):
        from agents import p6_monitor_agent

        self.assertTrue(hasattr(p6_monitor_agent.monitor_start, "invoke"))
        self.assertTrue(callable(p6_monitor_agent.api_monitor_start))
        self.assertTrue(any(
            route.path == "/api/monitor/start"
            and "POST" in getattr(route, "methods", set())
            for route in p6_monitor_agent.app.routes
        ))


if __name__ == "__main__":
    unittest.main()
