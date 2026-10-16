"""Package source allowlists only; never include local or Mac runtime state."""
from pathlib import Path
import hashlib
import json
import tarfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'P_skill/.runtime/panshi'
OUT.mkdir(parents=True, exist_ok=True)
platform = next(ROOT.glob('skill_decoupling*'))
files = []
for base, target, children in [
    (platform, 'platform', ['app.py', 'cli.py', 'requirements.lock.txt', 'platform_core', 'web']),
    (ROOT/'P_skill', 'P_skill', ['runtime', 'Docker/mock_player', 'main', 'P6', 'P7', 'P8', 'P10', 'P9', 'dist', 'deploy/panshi']),
]:
    for child in children:
        source = base/child
        for path in ([source] if source.is_file() else source.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(base)
            if any(p in {'__pycache__', '.runtime', '.venv', '.pytest_cache', 'node_modules', 'data', 'tests', '.git'} for p in relative.parts):
                continue
            if path.suffix in {'.pyc', '.log', '.db', '.sqlite3'} or path.name.startswith('.env'):
                continue
            files.append((path, str(Path(target)/relative).replace('\\', '/')))
manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for path,name in files}
with tarfile.open(OUT/'source.tar.gz', 'w:gz') as archive:
    for path,name in files:
        archive.add(path, arcname=name, recursive=False)
    archive.add(ROOT/'P_skill/deploy/panshi/build.dockerignore', arcname='.dockerignore', recursive=False)
(OUT/'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps({'files':len(files), 'bytes':(OUT/'source.tar.gz').stat().st_size, 'runtime_data_included':False}))
