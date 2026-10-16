"""Mac setup: verify synced source then copy only model config into isolated secret file."""
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for name, sha in json.loads((ROOT/'source-manifest.json').read_text()).items():
    if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha:
        raise RuntimeError('SYNC_HASH_MISMATCH:'+name)
runtime = ROOT/'P_skill/.runtime'
runtime.mkdir(exist_ok=True)
os.chmod(runtime,0o700)
docker = '/Applications/Docker.app/Contents/Resources/bin/docker'
script = 'import json; from agents.model.config import get_settings; s=get_settings(); print(json.dumps(dict(model=s.OPENAI_MODEL,base_url=s.OPENAI_BASE_URL,api_key=s.OPENAI_API_KEY)))'
if (runtime/'model.json').exists():
    # Migration retains the private model configuration; subsequent deployments
    # must not depend on the legacy P6 container remaining online.
    config = json.loads((runtime/'model.json').read_text())
else:
    result = subprocess.run([docker,'exec','amac-skill-p6-1','python','-c',script],capture_output=True,check=True)
    config = json.loads(result.stdout)
if not all(config.get(k) for k in ('model','base_url','api_key')):
    raise RuntimeError('EXISTING_MODEL_CONFIGURATION_INCOMPLETE')
for name, value in [('model.json',json.dumps(config)),('bridge.token',secrets.token_urlsafe(32))]:
    path = runtime/name
    if not path.exists() or name == 'model.json':
        path.write_text(value)
    os.chmod(path,0o600)
print(json.dumps({'synced_files_verified':True,'model':config['model'],'model_configured':True,'secrets_printed':False}))
