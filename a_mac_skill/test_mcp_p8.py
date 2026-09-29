"""Local MCP facade checks without contacting Feishu or altering site data."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import mcp_server


class P8McpTests(unittest.TestCase):
    def test_published_scope(self):
        published = set(mcp_server.mcp._tool_manager._tools)
        self.assertEqual(len(published), 15)
        self.assertTrue({'read_p7_events', 'open_work_ticket', 'resend_current_card',
                         'lookup_feishu_directory', 'recall_jobs'} <= published)
        self.assertFalse({'update_job', 'hitl_decide', 'list_active_p8_jobs'} & published)

    def test_reads_shared_p7_data(self):
        with tempfile.TemporaryDirectory() as data:
            job = Path(data) / 'jobs' / '20260917000000003'
            job.mkdir(parents=True)
            (job / 'p7_result.json').write_text(json.dumps({'events': [
                {'risk_event_id': 'R-1', 'risk_level': 'HIGH'}]}), encoding='utf-8')
            with patch.dict(os.environ, {'AMAC_DATA_DIR': data}):
                result = mcp_server.read_p7_events('20260917000000003')
            self.assertEqual(result['result']['events'][0]['risk_event_id'], 'R-1')

    def test_directory_uses_service_config(self):
        directory = json.dumps({'oc_test': {'name': '现场群'}})
        with patch.dict(os.environ, {'FEISHU_GROUP_MAP': directory}):
            result = mcp_server.lookup_feishu_directory('group', name='现场群')
        self.assertEqual(result['result']['chat_id'], 'oc_test')

    def test_unsafe_job_path_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid job_id'):
            mcp_server.read_p7_events('../outside')


if __name__ == '__main__':
    unittest.main()
