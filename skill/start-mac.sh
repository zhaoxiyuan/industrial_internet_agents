#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export AMAC_SITE_ROOT="/Users/edge_security/IndustrialAgents/a"
export SKILL_HOST=127.0.0.1
export SKILL_PORT=8765
cd "$ROOT/.."
exec "$ROOT/.venv/bin/python" "$ROOT/app.py"
