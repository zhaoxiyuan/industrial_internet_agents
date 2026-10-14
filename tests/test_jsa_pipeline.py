"""JSA 风险回填、流程顺序和下载的一致性回归；全部模型调用打桩。"""
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

import agents.p1_permit_agent as p1
from agents.workflow import get_permit_type
from agents.workflow.jsa_analyze import generate_jsa, parse_jsa_payload, risk_options_for_type
from agents.workflow import jsa_report
from tests.test_jsa_analyze import _payload
from tests.test_jsa_report import PAYLOAD, VALUES, LINE_OPEN, _render, _headings, _plain, _blocks
from tests.test_permit_template import FileHandler
from web.api.workflow import handle_workflow_permit_docx

HOT = get_permit_type('hot_work')[0]
JOB = '20261013123456789'


class StrictJsaTests(unittest.TestCase):
    def test_invalid_payloads_degrade_without_partial_content(self):
        cases = [
            _payload(risk_options='火灾'),          # 结构错误（非数组）仍应降级
            _payload(pre_checks=[None, {}]),
            _payload(special_sections=[{'title': 'x', 'items': [' ']}]),
            _payload(special_sections=[{'title': 'x', 'items': ['有效条目']}]),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                model = MagicMock()
                model.invoke.return_value.content = json.dumps(payload)
                with patch('agents.model.chat_model.create_chat_model_with_logging', return_value=model):
                    result = generate_jsa(JOB, '动火', [], [], risk_options_for_type(HOT))
                self.assertEqual(set(result), {'source', 'type_name', 'generated_at', 'error'})
                self.assertEqual(result['source'], 'unavailable')

    def test_options_are_filtered_to_this_ticket_with_a_warning(self):
        """risk_options 越界只丢弃并告警，**不让整份分析降级**（辅助字段不拖垮主体）。"""
        payload = _payload(risk_options=['火灾', '中毒', '火灾', '不存在的风险'])
        with self.assertLogs('agents.workflow.jsa_analyze', level='WARNING') as captured:
            result = parse_jsa_payload(json.dumps(payload), None, risk_options_for_type(HOT))
            # 该票没有风险栏（allowed 为空）→ 全部丢弃为空，同样不降级
            none_allowed = parse_jsa_payload(json.dumps(payload), None, [])
        self.assertEqual(result['risk_options'], ['火灾', '中毒'], '越界项应被丢弃、合法项去重保留')
        self.assertEqual(none_allowed['risk_options'], [])
        self.assertIn('不存在的风险', '\n'.join(captured.output), '丢弃必须留下明确告警')
        # 字段整个缺失按空数组处理：模型没识别出票面风险，是合法输出
        missing = _payload()
        del missing['risk_options']
        self.assertEqual(
            parse_jsa_payload(json.dumps(missing), None, risk_options_for_type(HOT))['risk_options'], []
        )

    def test_context_failure_degrades_before_model_call(self):
        with patch('agents.workflow.jsa_analyze.build_jsa_context', side_effect=OSError('template missing')), patch('agents.workflow.jsa_analyze.generate_jsa') as model:
            result = p1._build_jsa_result({}, {}, HOT, JOB)
        model.assert_not_called()
        self.assertEqual(set(result), {'source', 'type_name', 'generated_at', 'error'})
        self.assertEqual(result['source'], 'unavailable')

    def test_renderer_failure_does_not_abort_analysis(self):
        with patch('agents.workflow.jsa_report.render_jsa_report', side_effect=OSError('disk full')):
            self.assertEqual(p1._render_jsa_report_safely(PAYLOAD, VALUES, LINE_OPEN, JOB), '')


class PipelineTests(unittest.TestCase):
    def test_manual_order_union_context_and_no_application_mutation(self):
        for selected in ([], ['火灾'], ['触电', '火灾']):
            with self.subTest(selected=selected):
                app = {'permit_type': 'hot_work', 'permit_fields': {'risks': selected, 'work_content': '焊接'}}
                before = copy.deepcopy(app)
                events = []
                jsa = {**_payload(risk_options=['火灾', '中毒']), 'source': 'ai'}
                def analyze(*args):
                    events.append('jsa')
                    return jsa
                def decide(job, name, context, measures):
                    events.append('measures')
                    self.assertIn('H₂S 泄漏', '\n'.join(context))
                    return {'source': 'unavailable', 'items': []}
                with patch.object(p1, '_build_jsa_result', side_effect=analyze), patch.object(p1, '_decide_safety_measures', side_effect=decide):
                    result = p1._build_manual_permit_result(app, JOB)
                self.assertEqual(events, ['jsa', 'measures'])
                self.assertEqual(result['permit_content']['permit_values']['risks'], list(dict.fromkeys([*selected, '火灾', '中毒'])))
                self.assertEqual(app, before)

    def test_failed_jsa_still_runs_measures_without_hazard_context(self):
        with patch.object(p1, '_build_jsa_result', return_value={'source': 'unavailable', 'error': 'down'}), patch.object(p1, '_decide_safety_measures', return_value={'source': 'ai', 'items': []}) as decide:
            result = p1._build_manual_permit_result({'permit_type': 'hot_work', 'permit_fields': {'risks': ['火灾']}}, JOB)
        self.assertEqual(result['permit_content']['permit_values']['risks'], ['火灾'])
        self.assertNotIn('【JSA', '\n'.join(decide.call_args.args[2]))
        decide.assert_called_once()

    def test_no_risk_field_on_two_ticket_types(self):
        for key in ('line_open', 'temp_power'):
            type_def = get_permit_type(key)[0]
            self.assertEqual(p1._merge_jsa_risks({}, type_def, {'source': 'ai', 'risk_options': ['火灾']}), {})

    def test_docx_order_and_context(self):
        events = []
        def analyze(*args):
            events.append('jsa')
            return {**_payload(), 'source': 'ai'}
        def decide(job, name, context, measures):
            events.append('measures')
            self.assertIn('H₂S 泄漏', '\n'.join(context))
            return {'source': 'no-measures', 'items': []}
        with patch.object(p1, '_build_jsa_result', side_effect=analyze), patch.object(p1, '_decide_safety_measures', side_effect=decide):
            p1._build_docx_permit_result({'job_type': '动火作业', 'job_content': '焊接'}, JOB)
        self.assertEqual(events, ['jsa', 'measures'])

    def test_partial_values_keep_application_context_and_specific_place(self):
        values = p1._jsa_values({'job_content': '焊接', 'region': '大区域', 'work_location': '具体地点'}, {'risks': ['火灾']})
        self.assertEqual(values['work_content'], '焊接')
        self.assertEqual(values['work_place'], '具体地点')

    def test_agent_fallback_reuses_full_pipeline(self):
        app = {'permit_type': 'hot_work', 'permit_fields': {'work_content': '焊接'}}
        rebuilt = {'jsa_result': {'source': 'unavailable'}, 'permit_content': {'input_source': 'manual'}, 'missing_fields': []}
        with tempfile.TemporaryDirectory() as directory, patch('agents.workflow.file_utils.get_jobs_dir', return_value=directory), patch('agents.workflow.read_json_file', return_value={'application': app}), patch.object(p1, '_build_manual_permit_result', return_value=rebuilt) as build:
            result = p1._process_p1_result(JOB, {}, {})
        build.assert_called_once_with(app, JOB)
        self.assertEqual(result['jsa_result'], rebuilt['jsa_result'])


class ReportBoundaryTests(unittest.TestCase):
    def test_dynamic_sections_and_step_rows(self):
        for sections in range(4):
            for count in (4, 15):
                payload = {**PAYLOAD, 'special_sections': PAYLOAD['special_sections'] * sections,
                           'steps': (PAYLOAD['steps'] * 3)[:count]}
                xml, body, names = _render(payload)
                self.assertEqual(len(_headings(body)), 6 + sections)
                self.assertEqual(len(re.findall(r'<w:tr[ >]', body)), count + 1)
                with zipfile.ZipFile(jsa_report.TEMPLATE_PATH) as template:
                    source = template.read('word/document.xml').decode()
                self.assertEqual(re.search(r'<w:sectPr\b.*?</w:sectPr>', xml, re.S).group(), re.search(r'<w:sectPr\b.*?</w:sectPr>', source, re.S).group())

    def test_all_distinct_sample_paragraphs_removed_and_package_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'report.docx')
            jsa_report.render_jsa_report(PAYLOAD, VALUES, LINE_OPEN, output)
            with zipfile.ZipFile(jsa_report.TEMPLATE_PATH) as src, zipfile.ZipFile(output) as dest:
                self.assertEqual(src.namelist(), dest.namelist())
                for name in src.namelist():
                    if name != 'word/document.xml':
                        self.assertEqual(src.read(name), dest.read(name))
                original = src.read('word/document.xml').decode()
                rendered = dest.read('word/document.xml').decode()
            supplied = json.dumps(PAYLOAD, ensure_ascii=False)
            probes = [_plain(p) for p in re.findall(r'<w:p\b.*?</w:p>', original, re.S)
                      if 'Heading' not in p and len(_plain(p)) >= 12 and _plain(p) not in supplied]
            self.assertGreater(len(probes), 15)
            for probe in probes:
                self.assertNotIn(probe, _plain(rendered))


class DownloadTests(unittest.TestCase):
    def test_jsa_download_uses_current_job_fixed_path(self):
        with tempfile.TemporaryDirectory() as directory, patch('agents.workflow.get_job_dir', return_value=directory):
            root = Path(directory)
            (root / 'jsa_report.docx').write_bytes(b'current report')
            (root / 'permit.json').write_text(json.dumps({'jsa_result': {'source': 'ai', 'report_path': '/untrusted/other.docx'}}))
            handler = FileHandler()
            handle_workflow_permit_docx(handler, JOB, 'jsa')
            self.assertEqual(handler.status_code, 200)
            self.assertEqual(handler.wfile.getvalue(), b'current report')
            (root / 'permit.json').write_text(json.dumps({'jsa_result': {'source': 'unavailable'}}))
            handler = FileHandler()
            handle_workflow_permit_docx(handler, JOB, 'jsa')
            self.assertEqual(handler.status_code, 404)

    def test_download_rejects_invalid_artifact_and_job(self):
        for job, artifact in ((JOB, '../jsa'), ('../job', 'jsa')):
            handler = FileHandler()
            handle_workflow_permit_docx(handler, job, artifact)
            self.assertEqual(handler.status_code, 400)

    def test_download_contains_merged_risks(self):
        with tempfile.TemporaryDirectory() as directory, patch('agents.workflow.get_job_dir', return_value=directory):
            root = Path(directory)
            (root / 'application.json').write_text(json.dumps({'application': {'permit_type': 'hot_work', 'permit_fields': {'risks': ['火灾']}}}))
            (root / 'permit.json').write_text(json.dumps({'permit_content': {'permit_values': {'risks': ['火灾', '中毒']}}}))
            handler = FileHandler()
            handle_workflow_permit_docx(handler, JOB)
            self.assertEqual(handler.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(handler.wfile.getvalue())) as archive:
                from agents.workflow.permit_template import _cell_tokens
                text = ''.join(token[3] for token in _cell_tokens(archive.read('word/document.xml').decode()))
            self.assertRegex(text, r'☑\s*火灾')
            self.assertRegex(text, r'☑\s*中毒')
