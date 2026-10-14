"""P1 两次审批的行为测试；使用真实持久化与调度，外部模型打桩。"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import agents.main_agent as main
import agents.p1_permit_agent as p1
from agents.workflow import (
    clear_execution_registry, get_job_dir, get_stage_result_path, get_workflow_status,
    read_json_file, save_job_application, write_json_file,
)
from tests.test_jsa_analyze import _payload
from tests.test_permit_template import FileHandler
from web.api.workflow import handle_workflow_permit_docx

JOB = '20261013000000999'


class JsaApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        clear_execution_registry()
        self.addCleanup(clear_execution_registry)
        self.start_patch(patch('agents.workflow.file_utils.get_jobs_dir', return_value=self.temp.name))
        self.analyze = self.start_patch(patch('agents.workflow.jsa_analyze.generate_jsa', return_value={
            **_payload(special_sections=[], risk_options=['火灾']), 'source': 'ai', 'type_name': '动火作业',
        }))
        self.decide = self.start_patch(patch.object(p1, '_decide_safety_measures', side_effect=lambda job, name, lines, measures: {
            'source': 'ai', 'items': [{'index': item['index'], 'needed': True} for item in measures],
        }))
        self.p2 = Mock(return_value={'completed': True, 'pending_confirmation': {'message': 'P2 review'}})
        self.start_patch(patch.object(main, 'STAGE_EXECUTORS', {'P1': p1.execute_stage, 'P2': self.p2}))
        self.start_patch(patch.object(main, 'STAGE_CONFIG', {'P1': {'max_attempts': 1}, 'P2': {'max_attempts': 1}}))
        self.broadcast = self.start_patch(patch.object(main, '_broadcast_state'))
        self.application = {'input_source': 'manual', 'permit_type': 'hot_work',
                            'permit_fields': {'work_content': '管道焊接', 'work_place': '装置区', 'risks': ['触电']}}
        save_job_application(JOB, self.application)

    def start_patch(self, item):
        obj = item.start()
        self.addCleanup(item.stop)
        return obj

    def saved(self):
        return read_json_file(get_stage_result_path(JOB, 'p1'))

    def start_review(self):
        outcome = main.run_workflow(self.application, JOB)
        self.assertEqual(outcome['status'], 'waiting')
        result = self.saved()
        self.assertEqual(result['pending_confirmation']['type'], 'jsa_approval')
        self.assertEqual(main.list_pending_confirmations(JOB)[0]['pending'], result['pending_confirmation'])
        self.assertFalse(result['completed'])
        self.assertNotIn('permit_content', result)
        self.assertNotIn('permit_draft_id', result)
        self.decide.assert_not_called()
        self.p2.assert_not_called()
        return result

    def approve_jsa(self, result, **kwargs):
        return main.confirm_and_continue(JOB, 'P1', approval_id=result['pending_confirmation']['approval_id'], **kwargs)

    def test_start_only_analyzes_and_download_is_report_only(self):
        initial = self.start_review()
        stored = read_json_file(str(Path(get_job_dir(JOB)) / 'permit.json'))
        self.assertNotIn('permit_content', stored)
        self.assertTrue(Path(initial['jsa_result']['report_path']).is_file())
        handler = FileHandler()
        handle_workflow_permit_docx(handler, JOB, 'jsa')
        self.assertEqual(handler.status_code, 200)
        permit = FileHandler()
        handle_workflow_permit_docx(permit, JOB)
        self.assertEqual(permit.status_code, 409)
        self.assertFalse((Path(get_job_dir(JOB)) / 'permit.docx').exists())

    def test_two_approvals_and_stale_request_cannot_approve_ticket(self):
        initial = self.start_review()
        outcome = self.approve_jsa(initial)
        self.assertEqual(outcome['status'], 'waiting')
        ticket = self.saved()
        self.assertEqual(ticket['pending_confirmation']['type'], 'permit_final_approval')
        self.assertNotEqual(initial['pending_confirmation']['approval_id'], ticket['pending_confirmation']['approval_id'])
        self.assertEqual(ticket['jsa_result'], initial['jsa_result'])
        self.assertEqual(ticket['permit_content']['permit_values']['risks'], ['触电', '火灾'])
        self.assertEqual(get_workflow_status(JOB)['agents']['P1']['status'], 'waiting')
        self.analyze.assert_called_once()
        self.decide.assert_called_once()
        self.p2.assert_not_called()
        duplicate = self.approve_jsa(initial)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(self.saved(), ticket)
        self.p2.assert_not_called()
        self.assertEqual(main.confirm_and_continue(JOB, 'P1')['status'], 'already_processed')
        second = main.confirm_and_continue(JOB, 'P1', approval_id=ticket['pending_confirmation']['approval_id'])
        self.assertEqual(second['current_stage'], 'P2')
        self.p2.assert_called_once_with(JOB)
        records = read_json_file(str(Path(get_job_dir(JOB)) / 'confirmations.json'))
        self.assertEqual([row['approval_type'] for row in records], ['jsa_approval', 'permit_final_approval'])

    def test_async_jsa_approval_pauses_again_before_p2(self):
        initial = self.start_review()
        waiting = threading.Event()
        def broadcast(job):
            saved = self.saved()
            if saved.get('pending_confirmation', {}).get('type') == 'permit_final_approval':
                waiting.set()
        self.broadcast.side_effect = broadcast
        outcome = self.approve_jsa(initial, async_execute=True)
        self.assertEqual(outcome['status'], 'executing')
        self.assertTrue(waiting.wait(3))
        self.p2.assert_not_called()
        self.assertEqual(self.saved()['pending_confirmation']['type'], 'permit_final_approval')

    def test_jsa_rejection_stops_and_retry_requires_new_review(self):
        initial = self.start_review()
        outcome = main.confirm_and_continue(JOB, 'P1', decision='reject', approval_id=initial['pending_confirmation']['approval_id'])
        self.assertTrue(outcome['rejected'])
        self.decide.assert_not_called()
        self.p2.assert_not_called()
        retry = p1.execute_stage(JOB)
        self.assertEqual(retry['pending_confirmation']['type'], 'jsa_approval')
        self.assertNotEqual(retry['pending_confirmation']['approval_id'], initial['pending_confirmation']['approval_id'])
        self.assertNotIn('permit_content', retry)

    def test_analysis_failure_cannot_be_approved(self):
        self.analyze.return_value = {'source': 'unavailable', 'error': 'model down'}
        initial = self.start_review()
        outcome = self.approve_jsa(initial)
        self.assertEqual(outcome['status'], 'error')
        self.assertIn('不能批准', outcome['error'])
        self.assertEqual(self.saved(), initial)
        self.decide.assert_not_called()

    def test_waiting_restart_reuses_analysis_and_review_id(self):
        initial = self.start_review()
        self.assertEqual(p1.execute_stage(JOB, resume=True), initial)
        self.analyze.assert_called_once()
        self.decide.assert_not_called()

    def test_ticket_generation_failure_preserves_approved_jsa_for_retry(self):
        initial = self.start_review()
        self.decide.side_effect = RuntimeError('measure generation failed')
        outcome = self.approve_jsa(initial)
        self.assertEqual(outcome['status'], 'error')
        saved = self.saved()
        self.assertEqual(saved['jsa_result'], initial['jsa_result'])
        self.assertEqual(saved['jsa_approval']['decision'], 'approve')
        self.decide.side_effect = None
        self.decide.return_value = {'source': 'unavailable', 'items': []}
        ticket = p1.execute_stage(JOB, resume=True)
        self.assertEqual(ticket['pending_confirmation']['type'], 'permit_final_approval')
        self.analyze.assert_called_once()

    def test_changed_application_requires_fresh_jsa_review(self):
        initial = self.start_review()
        self.approve_jsa(initial)
        changed = copy.deepcopy(self.application)
        changed['permit_fields']['work_content'] = '另一处管道焊接'
        save_job_application(JOB, changed)
        next_review = p1.execute_stage(JOB)
        self.assertEqual(next_review['pending_confirmation']['type'], 'jsa_approval')
        self.assertNotIn('permit_content', next_review)
        self.assertEqual(self.analyze.call_count, 2)


if __name__ == '__main__':
    unittest.main()
