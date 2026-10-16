"""Async HTTP adapter to ORIGINAL a_mac_skill P10, no report business reimplementation."""
import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .store import Store, canonical


def worker(root, job_id, export_id):
    store = Store(root, job_id)
    config = store.config()
    with store.db() as db:
        exported = store.get(db, 'export', export_id)
    original = Path(os.environ['P10_ORIGINAL_ROOT']).resolve()
    sys.path.insert(0, str(original))
    data_root = store.root / exported['data_root']
    os.environ['AMAC_DATA_DIR'] = str(data_root)
    os.environ['P8P9_BASE_DIR'] = str(data_root / 'jobs')
    from langchain_openai import ChatOpenAI
    from langchain_core.callbacks import BaseCallbackHandler
    calls = []
    class Audit(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            for group in response.generations:
                for generation in group:
                    calls.extend({'name': c['name']} for c in getattr(generation.message, 'tool_calls', []))
    model_config = json.loads(Path(os.environ['P10_MODEL_CONFIG']).read_text())
    model = ChatOpenAI(model=model_config['model'], base_url=model_config['base_url'], api_key=model_config['api_key'],
                       max_tokens=8000, timeout=180, max_retries=0, callbacks=[Audit()],
                       use_responses_api=False, model_kwargs={'parallel_tool_calls': False})
    from agents.p10_report_agent import run_p10_report
    permit = json.loads((data_root/'jobs'/exported['erp_job_id']/'work_permit.json').read_text())
    result = run_p10_report(exported['erp_job_id'], request_id=export_id, work_permit=permit,
                           permit_source='work_permit.json#', model=model)
    with store.db() as db:
        store.put(db, 'p10_request', export_id, {'status': 'failed' if result.get('error') else 'completed',
                   'result': result, 'tool_calls': calls, 'export_id': export_id, 'contains_mock': exported['contains_mock'],
                   'scope_gaps': exported['scope_gaps']})


def server(root, job_id, token_file, port):
    store = Store(root, job_id)
    token = Path(token_file).read_text().strip()
    lock = threading.Lock()
    def run(export_id):
        env = dict(os.environ)
        try:
            result = subprocess.run([sys.executable, '-m', 'industrial_skills.p10_bridge', '--root', str(root), '--job-id', job_id, '--worker', export_id],
                                    env=env, capture_output=True, timeout=1200)
            if result.returncode:
                detail=result.stderr.decode('utf-8',errors='replace')[-3000:]
                model_config=json.loads(Path(os.environ['P10_MODEL_CONFIG']).read_text())
                detail=detail.replace(model_config['api_key'],'[redacted]')
                raise RuntimeError(detail)
        except (subprocess.TimeoutExpired, RuntimeError) as error:
            with store.db() as db:
                store.put(db, 'p10_request', export_id, {'status': 'failed', 'error': 'P10_WORKER_FAILED_OR_TIMEOUT',
                           'diagnostic':str(error) if isinstance(error,RuntimeError) else 'Worker exceeded 1200 seconds'})
    # Never assume a crashed worker is still running or silently relaunch it.
    with store.db() as db:
        for row in store.rows(db, 'p10_request'):
            if row['status'] == 'running':
                store.put(db, 'p10_request', row['id'], {**row, 'status': 'interrupted'})
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token):
                return self.reply(403, {'error': 'FORBIDDEN'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length < 4096:
                    raise ValueError('INVALID_REQUEST')
                args = json.loads(self.rfile.read(length))
                export_id = args['export_id']
                if set(args) != {'export_id'} or self.path not in ('/submit', '/status'):
                    raise ValueError('INVALID_REQUEST')
                with lock, store.db() as db:
                    store.get(db, 'export', export_id)
                    value = store.get(db, 'p10_request', export_id, False)
                    if not value and self.path == '/submit':
                        value = store.put(db, 'p10_request', export_id, {'status': 'running', 'export_id': export_id})
                        launch = True
                    else:
                        launch = False
                if launch:
                    threading.Thread(target=run, args=(export_id,), daemon=True).start()
                return self.reply(200, value or {'status': 'not_submitted'})
            except (ValueError, KeyError):
                return self.reply(400, {'error': 'INVALID_OR_FORBIDDEN_EXPORT'})
        def reply(self, status, value):
            raw = canonical(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--worker')
    parser.add_argument('--token-file')
    parser.add_argument('--port', type=int, default=8091)
    args = parser.parse_args()
    if args.worker:
        worker(args.root, args.job_id, args.worker)
    else:
        server(args.root, args.job_id, args.token_file, args.port)
