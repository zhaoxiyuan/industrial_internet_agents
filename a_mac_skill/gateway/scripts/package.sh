#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(node -p "require('$ROOT/package.json').version")"
OUT="${1:-$(dirname "$ROOT")/openclaw-channel-gateway-standalone-$VERSION.zip}"

cd "$(dirname "$ROOT")"
rm -f "$OUT"
zip -qr "$OUT" "$(basename "$ROOT")" \
  -x '*/data/gateway-state.json' \
     '*/config/config.local.json' \
     '*/config/*.production.json' \
     '*/.env' \
     '*/node_modules/*' \
     '*/.venv/*' \
     '*/__pycache__/*'
OUT_DIR="$(dirname "$OUT")"
OUT_NAME="$(basename "$OUT")"
(
  cd "$OUT_DIR"
  sha256sum "$OUT_NAME" > "$OUT_NAME.sha256"
)
echo "$OUT"
