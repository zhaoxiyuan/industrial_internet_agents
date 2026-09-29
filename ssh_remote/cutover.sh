#!/bin/bash
set -euo pipefail

HOME_DIR=/Users/edge_security
DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
OLD_AGENT="$HOME_DIR/Library/LaunchAgents/com.industrial-agents.ngrok-ssh.plist"
NEW_AGENT="$HOME_DIR/Library/LaunchAgents/com.industrial-agents.edge-nginx.plist"
DOMAIN="gui/$(id -u)"

cp "$HOME_DIR/ssh_remote/com.industrial-agents.edge-nginx.plist" "$NEW_AGENT"
plutil -lint "$NEW_AGENT"
launchctl bootout "$DOMAIN" "$OLD_AGENT" 2>/dev/null || true
"$DOCKER" stop a-nginx >/dev/null

restore_old() {
    launchctl bootout "$DOMAIN" "$NEW_AGENT" 2>/dev/null || true
    "$DOCKER" start a-nginx >/dev/null
    launchctl bootstrap "$DOMAIN" "$OLD_AGENT" 2>/dev/null || true
    echo 'Host Nginx cutover failed; old Docker ingress restored.' >&2
}

if ! launchctl bootstrap "$DOMAIN" "$NEW_AGENT"; then
    restore_old
    exit 1
fi

for attempt in 1 2 3 4 5; do
    ssh_status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/sshws/ || true)"
    skill_status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/skill/ || true)"
    business_status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/admin/config/ || true)"
    if [[ "$ssh_status" == 401 && "$skill_status" == 401 && "$business_status" == 401 ]]; then
        echo 'Host Nginx cutover healthy: sshws=401 skill=401 business=401'
        exit 0
    fi
    sleep 2
done

restore_old
exit 1
