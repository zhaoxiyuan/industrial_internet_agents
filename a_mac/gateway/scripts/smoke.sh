#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
PORT="${CG_SMOKE_PORT:-18787}"
API_KEY='smoke-api-key-0123456789abcdef'
WEBHOOK_TOKEN='smoke-webhook-token'
PID=''

cleanup() {
  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

cat > "$TMP/config.json" <<JSON
{
  "server": {"host":"127.0.0.1","port":$PORT,"apiKey":"$API_KEY"},
  "storage": {"directory":"$TMP/data"},
  "delivery": {"callbackUrl":null},
  "channels": {
    "loopback":{"enabled":true,"accounts":{"default":{"webhookToken":"$WEBHOOK_TOKEN"}}},
    "generic":{"enabled":false,"accounts":{}},
    "feishu":{"enabled":false,"accounts":{}}
  }
}
JSON

cd "$ROOT"
node src/index.js --config "$TMP/config.json" >"$TMP/gateway.log" 2>&1 &
PID=$!

for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:$PORT/readyz" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
curl -fsS "http://127.0.0.1:$PORT/readyz" >/dev/null

INGEST="$(curl -fsS -X POST "http://127.0.0.1:$PORT/webhooks/loopback/default" \
  -H 'content-type: application/json' \
  -H "x-cg-webhook-token: $WEBHOOK_TOKEN" \
  -d '{"platform_event_id":"smoke-event","sender_id":"smoke-user","conversation_id":"smoke-chat","message_id":"smoke-message","text":"hello"}')"

EVENT_ID="$(node -e 'const x=JSON.parse(process.argv[1]); if(!x.event_ids?.[0]) process.exit(2); process.stdout.write(x.event_ids[0])' "$INGEST")"

LIST="$(curl -fsS "http://127.0.0.1:$PORT/v1/events?after_sequence=0" -H "authorization: Bearer $API_KEY")"
node -e 'const x=JSON.parse(process.argv[1]); if(x.events?.length !== 1 || x.events[0].message.text !== "hello") process.exit(2)' "$LIST"

REPLY="$(curl -fsS -X POST "http://127.0.0.1:$PORT/v1/messages/reply" \
  -H "authorization: Bearer $API_KEY" \
  -H 'content-type: application/json' \
  -H 'idempotency-key: smoke-reply' \
  -d "{\"event_id\":\"$EVENT_ID\",\"text\":\"world\"}")"
node -e 'const x=JSON.parse(process.argv[1]); if(x.intent?.status !== "sent" || !x.receipt?.platformMessageId) process.exit(2)' "$REPLY"

echo "Smoke test passed"
