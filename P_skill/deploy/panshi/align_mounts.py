"""Align host/container paths after confirming no tasks are active."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen
root=Path('/opt/industrial-agents')
with urlopen('http://127.0.0.1:18770/api/overview',timeout=20) as response:o=json.load(response)
active=[t['id'] for t in o['tasks'] if t['status'] in ('running','queued','waiting') or t.get('executor_active')]
assert not active, 'Active tasks: '+str(active)
subprocess.run(['docker','compose','stop','platform','skill-executor'],cwd=root,check=True)
source=root/'state/platform'
target=root/'platform/data'
if not source.is_symlink():
    assert source.is_dir() and not target.exists()
    source.rename(target)
    source.symlink_to('../platform/data',target_is_directory=True)
shutil.copy2(root/'P_skill/deploy/panshi/compose.yaml',root/'compose.yaml')
subprocess.run(['docker','compose','up','-d','--no-build','platform','skill-executor'],cwd=root,check=True)
print('Paths aligned; existing platform data preserved; Docker CLI mounted read-only')
