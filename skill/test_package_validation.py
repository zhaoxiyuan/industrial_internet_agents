import copy
import json
import tempfile
import unittest
import base64
import io
import importlib.util
import zipfile
from unittest.mock import patch
from pathlib import Path

import yaml
from skill.package_validation import validate_package, write_lock
from skill.package_store import inspect_source
from skill.service_runtime import control


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    manifest = yaml.safe_load((Path(__file__).resolve().parent.parent/'a_mac_skill/skill-package.yaml').read_text(encoding='utf-8'))
    manifest['metadata']['name'] = 'demo-skill'
    spec = manifest['spec']
    spec.update(services=[next(s for s in spec['services'] if s['serviceId']=='mcp_bridge')],ui=[],dependencies={})
    spec['services'][0]['dependsOn'] = []
    spec['services'][0]['profiles'] = ['directory']
    spec['deployment']['profiles'] = [{'id':'directory','mode':'directory','controlFile':'control.py'}]
    spec['tools']['catalog'] = 'tools.json'
    (root/'control.py').write_text('print("{\\"healthy\\": true}")\n',encoding='utf-8')
    spec['configuration']['schema'] = 'config.json'
    (root/'skill-package.yaml').write_text(yaml.safe_dump(manifest),encoding='utf-8')
    (root/'SKILL.md').write_text('---\nname: demo-skill\ndescription: Demo test\n---\nTest instructions.\n',encoding='utf-8')
    (root/'tools.json').write_text('{"tools":[]}',encoding='utf-8')
    (root/'config.json').write_text('{"type":"object"}',encoding='utf-8')
    write_lock(root)
    return manifest


class ValidationTest(unittest.TestCase):
    def test_docker_stop_passes_site_paths_to_compose(self):
        source = Path(__file__).resolve().parent.parent/'a_mac_skill/deploy/service_control.py'
        spec = importlib.util.spec_from_file_location('amac_stop_control_test', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            (site/'gateway/config').mkdir(parents=True)
            (site/'.env').write_text('', encoding='utf-8')
            (site/'gateway/.env').write_text('', encoding='utf-8')
            (site/'gateway/config/config.feishu.local.json').write_text('{}', encoding='utf-8')
            with patch.dict('os.environ', {'AMAC_SITE_ROOT': folder}):
                with patch.object(module.subprocess, 'run') as run:
                    run.return_value.returncode = 0
                    module.main('stop', 'local-source')
            environment = run.call_args.kwargs['env']
            self.assertEqual(environment['AMAC_SITE_ENV_FILE'], str(site/'.env'))
            self.assertEqual(environment['AMAC_SITE_NGINX_HTPASSWD'],
                             str(site/'deploy/nginx/htpasswd'))
            self.assertIn('stop', run.call_args.args[0])

    def test_gateway_uses_its_own_site_environment(self):
        source = Path(__file__).resolve().parent.parent/'a_mac_skill/deploy/service_control.py'
        spec = importlib.util.spec_from_file_location('amac_service_control_test', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            (site/'gateway/config').mkdir(parents=True)
            (site/'.env').write_text('BUSINESS=ready\n', encoding='utf-8')
            (site/'gateway/.env').write_text('FEISHU_DOMAIN=feishu.cn\n', encoding='utf-8')
            (site/'gateway/config/config.feishu.local.json').write_text(
                '{"domain":"${FEISHU_DOMAIN}"}', encoding='utf-8')
            with patch.dict('os.environ', {'AMAC_SITE_ROOT': folder}):
                env_file, gateway_env, config = module.site_files()
            self.assertEqual(env_file, site/'.env')
            self.assertEqual(gateway_env, site/'gateway/.env')
            module.check_gateway_config(gateway_env, config, {})
            (site/'gateway/.env').write_text('', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'FEISHU_DOMAIN'):
                module.check_gateway_config(gateway_env, config, {})

    def test_declared_token_is_injected_into_controller(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = fixture(root)
            with patch('skill.service_runtime.subprocess.run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = '{"healthy": true}'
                control({'manifest': json.dumps(manifest), 'package_path': str(root)}, 'directory', 'start')
            self.assertEqual(run.call_args.kwargs['env']['AMAC_MCP_TOKEN'],
                             manifest['spec']['tools']['mcp']['auth']['token'])

    def test_real_package_imports_from_project_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(__file__).resolve().parent.parent
            report, saved = inspect_source({'path': 'a_mac_skill'}, workspace,
                                           Path(folder)/'packages', install=True)
            self.assertTrue(report['valid'])
            self.assertTrue(Path(saved, 'skill-package.yaml').is_file())

    def test_mcp_and_control_file_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = fixture(root)
            del manifest['spec']['tools']['mcp']
            (root/'skill-package.yaml').write_text(yaml.safe_dump(manifest),encoding='utf-8')
            write_lock(root)
            self.assertFalse(validate_package(root)['valid'])
            manifest = fixture(root)
            (root/'control.py').unlink()
            write_lock(root)
            self.assertFalse(validate_package(root)['valid'])

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
            self.assertTrue(validate_package(root,{})['valid'])
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
