import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from skill.mcp_runtime import langchain_tools


class McpRuntimeTest(unittest.TestCase):
    def test_agent_sees_declared_nested_schema_and_tool_error(self):
        root = Path(__file__).resolve().parent.parent / 'a_mac_skill'
        manifest = yaml.safe_load((root / 'skill-package.yaml').read_text(encoding='utf-8'))
        catalog = yaml.safe_load((root / 'tools/catalog.yaml').read_text(encoding='utf-8'))['tools']
        declared = next(tool for tool in catalog if tool['name'] == 'feishu_send')
        skill = {'name': 'industrial-workflow', 'manifest': json.dumps(manifest),
                 'package_path': str(root)}
        loose_remote = SimpleNamespace(inputSchema={'type': 'object', 'properties': {
            'to': {'type': 'object'}}})
        with patch('skill.mcp_runtime.discover', return_value=([(declared, loose_remote)], [])):
            tools, _ = langchain_tools(skill, 'binding', lambda _: True)

        self.assertEqual(tools[0].args['to']['required'], ['conversation_id'])
        response = tools[0].invoke({'channel': 'feishu', 'account_id': 'P8P9',
                                    'to': {'chat_id': 'oc_test'}, 'text': 'test'})
        self.assertFalse(json.loads(response)['ok'])
        self.assertIn('conversation_id', response)


if __name__ == '__main__':
    unittest.main()
