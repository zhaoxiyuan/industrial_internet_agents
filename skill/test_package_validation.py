import copy
import json
import tempfile
import unittest
import base64
import io
import zipfile
from pathlib import Path

import yaml
from skill.package_validation import validate_package, write_lock
from skill.package_store import inspect_source


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    manifest = yaml.safe_load((Path(__file__).resolve().parent.parent/'a_mac_skill/skill-package.yaml').read_text(encoding='utf-8'))
    manifest['metadata']['name'] = 'demo-skill'
    spec = manifest['spec']
    spec.update(services=[],ui=[],dependencies={})
    spec['deployment']['profiles'] = [{'id':'existing','mode':'external'}]
    spec['tools']['catalog'] = 'tools.json'
    spec['tools'].pop('mcp', None)
    spec['configuration']['schema'] = 'config.json'
    (root/'skill-package.yaml').write_text(yaml.safe_dump(manifest),encoding='utf-8')
    (root/'SKILL.md').write_text('---\nname: demo-skill\ndescription: Demo test\n---\nTest instructions.\n',encoding='utf-8')
    (root/'tools.json').write_text('{"tools":[]}',encoding='utf-8')
    (root/'config.json').write_text('{"type":"object"}',encoding='utf-8')
    write_lock(root)
    return manifest


class ValidationTest(unittest.TestCase):
    def test_zip_import_snapshot_and_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            root = workspace/'input'
            fixture(root)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                for p in root.iterdir():
                    z.write(p,'demo/'+p.name)
            payload = {'archive':base64.b64encode(stream.getvalue()).decode(),'configuration':{}}
            report, saved = inspect_source(payload,workspace,workspace/'packages',True)
            self.assertTrue(report['valid'])
            self.assertTrue(validate_package(saved,{})['valid'])
            (root/'SKILL.md').write_text('changed',encoding='utf-8')
            self.assertTrue(validate_package(saved,{})['valid'])
            bad = io.BytesIO()
            with zipfile.ZipFile(bad,'w') as z:
                z.writestr('../outside','no')
            with self.assertRaises(ValueError):
                inspect_source({'archive':base64.b64encode(bad.getvalue()).decode()},workspace,workspace/'packages')
            self.assertFalse((workspace/'outside').exists())

    def test_credentials_and_invalid_tool_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            fixture(root)
            (root/'.env').write_text('EXAMPLE=fixture',encoding='utf-8')
            self.assertFalse(validate_package(root,{})['valid'])
            (root/'.env').unlink()
            (root/'tools.json').write_text('{"tools":[{"name":"incomplete"}]}',encoding='utf-8')
            write_lock(root)
            self.assertFalse(validate_package(root,{})['valid'])

    def test_real_package_is_valid_but_not_deployable(self):
        report = validate_package(Path(__file__).resolve().parent.parent/'a_mac_skill')
        self.assertTrue(report['valid'], report['checks'])
        self.assertFalse(report['deployable'])
        self.assertTrue(any(c['code']=='tool.registration' for c in report['checks']))
        self.assertFalse(any(c['code']=='tool.adapter' for c in report['checks']))

    def test_valid_package_and_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            self.assertTrue(validate_package(root,{})['deployable'])
            (root/'SKILL.md').write_text((root/'SKILL.md').read_text()+'tamper',encoding='utf-8')
            self.assertFalse(validate_package(root,{})['valid'])

    def test_escape_missing_schema_and_cycle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = fixture(root)
            for modify in ('escape','missing','cycle'):
                candidate = copy.deepcopy(manifest)
                if modify=='escape':
                    candidate['spec']['tools']['catalog']='../outside.json'
                elif modify=='missing':
                    del candidate['spec']['lifecycle']
                else:
                    original=yaml.safe_load((Path(__file__).resolve().parent.parent/'a_mac_skill/skill-package.yaml').read_text(encoding='utf-8'))
                    service=original['spec']['services'][0]
                    service['dependsOn']=[service['serviceId']]
                    candidate['spec']['services']=[service]
                (root/'skill-package.yaml').write_text(yaml.safe_dump(candidate),encoding='utf-8')
                write_lock(root)
                self.assertFalse(validate_package(root,{})['valid'],modify)


if __name__ == '__main__':
    unittest.main()
