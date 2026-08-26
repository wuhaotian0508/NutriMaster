#!/usr/bin/env bash
set -euo pipefail

EXPECTED_MODEL="deepseek-v4-flash"
STATE_ROOT="/root/code/nutrimaster-deployments"
NGINX_CONF="/etc/nginx/conf.d/nutrimaster.conf"

if [[ "${1:-}" != "--execute" || ! "${2:-}" =~ ^[0-9a-f]{20}$ || $# -ne 2 ]]; then
  echo "usage: $0 --execute <20-hex-release-id>" >&2
  exit 2
fi
RELEASE_ID="$2"
STATE_DIR="$STATE_ROOT/$RELEASE_ID"
if [[ "${NUTRIMASTER_PRODUCTION_CHANGE_APPROVED:-}" != "$RELEASE_ID" ]]; then
  echo "Refusing rollback without NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=$RELEASE_ID" >&2
  exit 1
fi
if [[ ! -d "$STATE_DIR" || -L "$STATE_DIR" || ! -f "$STATE_DIR/nutrimaster.conf.before" ]]; then
  echo "Rollback state is missing or unsafe: $STATE_DIR" >&2
  exit 1
fi

systemctl disable --now nutrimaster-index-builder.path >/dev/null 2>&1 || true
systemctl stop nutrimaster-index-builder.service >/dev/null 2>&1 || true
systemctl disable --now nutrimaster-bohrium-proxy.socket >/dev/null 2>&1 || true
systemctl stop nutrimaster-bohrium-proxy.service >/dev/null 2>&1 || true
systemctl disable --now nutrimaster-unified.service nutrimaster-pi.service >/dev/null 2>&1 || true

install -m 0644 "$STATE_DIR/nutrimaster.conf.before" "$NGINX_CONF"
nginx -t
nginx -s reload

port_open() {
  ss -H -ltn "sport = :$1" | grep -q .
}

start_rollback_session() {
  local session="$1"
  local directory="$2"
  local command="$3"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Rollback tmux session already exists: $session" >&2
    return 1
  fi
  tmux new-session -d -s "$session" -c "$directory" "/bin/bash -lc '$command'"
}

if ! port_open 5000; then
  start_rollback_session \
    "nutrimaster_rollback_${RELEASE_ID}" \
    "/root/Projects/NutriMaster" \
    "set -a; source .env; set +a; export MAIN_MODEL=$EXPECTED_MODEL; exec .venv/bin/nutrimaster web"
fi
if ! port_open 5002; then
  start_rollback_session \
    "nutrimaster_preview_rollback_${RELEASE_ID}" \
    "/root/code/nutrimaster" \
    "set -a; source .env; set +a; export MAIN_MODEL=$EXPECTED_MODEL; exec .venv/bin/python -m nutrimaster.cli web --port 5002"
fi
if ! port_open 8787; then
  start_rollback_session \
    "nutrimaster_pi_rollback_${RELEASE_ID}" \
    "/root/code/nutrimaster/pi-runtime" \
    "set -a; source ../.env; set +a; export MAIN_MODEL=$EXPECTED_MODEL NUTRIMASTER_PI_MODEL=$EXPECTED_MODEL NUTRIMASTER_PI_HOST=127.0.0.1 NUTRIMASTER_PI_PORT=8787; exec /opt/node-v22.21.1-linux-x64/bin/node src/server.js"
fi

deadline=$((SECONDS + 90))
while (( SECONDS < deadline )); do
  if curl -q --noproxy '*' --fail --silent --max-time 3 http://127.0.0.1:5000/api/health >/dev/null \
     && curl -q --noproxy '*' --fail --silent --max-time 3 http://127.0.0.1:5002/api/health >/dev/null \
     && curl -q --noproxy '*' --fail --silent --max-time 3 http://127.0.0.1:8787/healthz >/dev/null; then
    printf '%s\n' "ROLLBACK_OK release_id=$RELEASE_ID model=$EXPECTED_MODEL" | tee "$STATE_DIR/ROLLBACK_OK"
    exit 0
  fi
  sleep 1
done

echo "Rollback services did not all become healthy within 90 seconds" >&2
exit 1
