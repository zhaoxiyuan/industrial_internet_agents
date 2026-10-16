"""Read-only smoke checks; temporary random nginx user is removed in finally."""
import base64
import json
import secrets
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

def fetch(path, auth=None):
    request = urllib.request.Request('http://127.0.0.1:18080'+path)
    if auth:
        request.add_header('Authorization', 'Basic '+auth)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()

for path in ['/skill/', '/api/overview', '/player/']:
    assert fetch(path)[0] == 401, path
file = Path('/etc/nginx/industrial-htpasswd.admin')
original = file.read_bytes()
username, password = 'deploy-check-'+secrets.token_hex(6), secrets.token_urlsafe(32)
hashed = subprocess.run(['htpasswd', '-niB', username], input=password+'\n', text=True, capture_output=True, check=True).stdout
try:
    file.write_bytes(original.rstrip(b'\n')+b'\n'+hashed.encode())
    auth = base64.b64encode((username+':'+password).encode()).decode()
    results = {}
    for path in ['/skill/', '/web/app.js', '/api/overview', '/player/', '/player/v2/data/status']:
        status, body = fetch(path, auth)
        assert status == 200, (path, status, body[:200])
        results[path] = status
        if path == '/api/overview':
            overview = json.loads(body)
            counts = {key:len(overview[key]) for key in ['agents', 'skills', 'sessions', 'tasks', 'schedules', 'connections']}
            assert not any(counts.values()), counts
    print(json.dumps({'nginx_auth': 'passed', 'routes': results, 'fresh_platform_counts':counts}))
finally:
    file.write_bytes(original)
