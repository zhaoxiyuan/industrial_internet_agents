"""Serve the existing public Mac platform using persisted Feishu connections."""
import json
import os
import signal
import sys
import threading

# Non-interactive SSH starts with a minimal PATH on macOS. Keep Docker available
# to platform-owned Skill runners on every restart, without shell setup steps.
if sys.platform == 'darwin':
    os.environ['PATH'] = '/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:' + os.environ.get('PATH', '/usr/bin:/bin')
from verify_platform import RUNTIME, Service
from app import make_server

service=Service(RUNTIME/'platform')
server=make_server(service,18770)
def stop(*args):
    threading.Thread(target=server.shutdown,daemon=True).start()
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
print('Current public platform: http://127.0.0.1:18770; Feishu=persisted connections',flush=True)
try:server.serve_forever()
finally:
    server.server_close();service.close()
