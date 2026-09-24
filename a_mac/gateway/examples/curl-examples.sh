#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8787}"
API_KEY="${CHANNEL_GATEWAY_API_KEY:?set CHANNEL_GATEWAY_API_KEY}"
WEBHOOK_TOKEN="${LOOPBACK_WEBHOOK_TOKEN:?set LOOPBACK_WEBHOOK_TOKEN}"
EVENT_KEY="example-$(date +%s)"

curl -sS -X POST "$BASE_URL/webhooks/loopback/default" \
  -H 'Content-Type: application/json' \
  -H "X-CG-Webhook-Token: $WEBHOOK_TOKEN" \
  -d "{\"platform_event_id\":\"$EVENT_KEY\",\"sender_id\":\"user-1\",\"conversation_id\":\"chat-1\",\"message_id\":\"msg-$EVENT_KEY\",\"text\":\"hello\"}"

echo
curl -sS "$BASE_URL/v1/events?after_sequence=0&limit=100" \
  -H "Authorization: Bearer $API_KEY"
echo
