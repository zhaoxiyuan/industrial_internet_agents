#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

find src test examples -name '*.js' -print0 | xargs -0 -n1 node --check
node --test --test-concurrency=1

python3 - <<'PY'
from pathlib import Path
p = Path('openapi/openapi.yaml')
text = p.read_text(encoding='utf-8')
required = ['openapi: 3.1.0', '/v1/messages/send:', '/v1/events/stream:', 'bearerAuth:']
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f'OpenAPI file is missing required markers: {missing}')
print('OpenAPI marker check passed')
PY

echo "All checks passed"
