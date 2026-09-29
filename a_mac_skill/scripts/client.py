"""Controlled HTTP client for existing a_mac APIs; no Docker or shell execution."""
import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROUTES = {
    'monitor-start': ('P6_BASE_URL','POST','/api/monitor/start'),
    'monitor-stop': ('P6_BASE_URL','POST','/api/monitor/stop'),
    'monitor-status': ('P6_BASE_URL','GET','/api/monitor/status'),
    'p6-events': ('P6_BASE_URL','GET','/api/agent/events'),
    'p7-records': ('P6_BASE_URL','GET','/api/a6/assessments'),
    'closure-get': ('P8P9_BASE_URL','GET','/api/closure/jobs/{job_id}'),
    'feishu-events': ('GATEWAY_BASE_URL','GET','/v1/events'),
    'feishu-reply': ('GATEWAY_BASE_URL','POST','/v1/messages/reply'),
    'feishu-send': ('GATEWAY_BASE_URL','POST','/v1/messages/send'),
    'feishu-ack': ('GATEWAY_BASE_URL','POST','/v1/events/{event_id}/ack'),
}


def call(operation, args):
    env, method, route = ROUTES[operation]
    base = os.environ.get(env, '').rstrip('/')
    if urlparse(base).scheme not in ('http','https') or not urlparse(base).netloc:
        raise ValueError('需在实例环境配置 ' + env)
    args = dict(args)
    for key in ('job_id','event_id'):
        if '{'+key+'}' in route:
            value = args.pop(key, '')
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
                raise ValueError('缺少或非法 ' + key)
            route = route.replace('{'+key+'}', quote(value, safe=''))
    headers = {'Content-Type':'application/json'}
    if env == 'GATEWAY_BASE_URL':
        token = os.environ.get('GATEWAY_API_KEY') or os.environ.get('CG_API_KEY')
    else:
        token = os.environ.get('AMAC_API_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = None
    if method == 'GET':
        route += ('?' + urlencode(args)) if args else ''
    else:
        body = json.dumps(args).encode('utf-8')
    # Never automatically retry writes or ACK failures.
    request = Request(base + route, data=body, headers=headers, method=method)
    with urlopen(request, timeout=60) as response:
        return json.load(response)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=sorted(ROUTES))
    parser.add_argument('--args-file', type=Path, required=True)
    opts = parser.parse_args()
    try:
        args = json.loads(opts.args_file.read_text(encoding='utf-8'))
        if not isinstance(args, dict):
            raise ValueError('参数必须是 JSON 对象')
        print(json.dumps(call(opts.operation, args), ensure_ascii=False))
    except (ValueError, OSError, HTTPError, URLError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        raise SystemExit(1)
