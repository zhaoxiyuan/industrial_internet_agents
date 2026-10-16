"""Build a code-only local snapshot and SHA256 manifest for Mac deployment."""
import hashlib
import json
import zipfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'P_skill/.runtime'
OUT.mkdir(exist_ok=True)
platform = next(ROOT.glob('skill_decoupling*'))
roots = [(ROOT/'P_skill', 'P_skill'), (platform, 'platform')]
roots += [(ROOT/'a_mac_skill'/p, 'original/'+p) for p in ('P10','P8P9','agents')]
manifest = {}
with zipfile.ZipFile(OUT/'source.zip','w',zipfile.ZIP_DEFLATED) as z:
    for source, target in roots:
        files = []
        for directory, dirs, names in os.walk(source):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__','data','node_modules','logs','artifacts','qa','figures')]
            files.extend(Path(directory)/name for name in names)
        for path in files:
            relative = path.relative_to(source)
            if not path.is_file() or any(part.startswith('.') or part in ('__pycache__','data','node_modules','logs','artifacts') for part in relative.parts):
                continue
            if path.suffix.lower() not in ('.py','.md','.json','.toml','.yaml','.yml','.txt','.html','.css','.js','.docx','.png','.sh','.ps1','.sql','.conf','.dockerignore') and path.name != 'Dockerfile':
                continue
            raw = path.read_bytes()
            name = target+'/'+relative.as_posix()
            manifest[name] = hashlib.sha256(raw).hexdigest()
            z.writestr(name,raw)
    z.writestr('source-manifest.json',json.dumps(manifest,sort_keys=True,indent=2))
print(json.dumps({'archive':str(OUT/'source.zip'),'files':len(manifest),'sha256':hashlib.sha256((OUT/'source.zip').read_bytes()).hexdigest()}))
