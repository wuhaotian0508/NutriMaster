#!/usr/bin/env bash
set -euo pipefail

EXPECTED_MODEL="deepseek-v4-flash"
RELEASES_ROOT="/root/code/nutrimaster-releases"
CURRENT_LINK="/root/code/nutrimaster-current"
STATE_ROOT="/root/code/nutrimaster-deployments"
NGINX_CONF="/etc/nginx/conf.d/nutrimaster.conf"

usage() {
  echo "usage: $0 [--preflight|--execute] <20-hex-release-id>" >&2
  exit 2
}

MODE="${1:-}"
RELEASE_ID="${2:-}"
[[ "$MODE" == "--preflight" || "$MODE" == "--execute" ]] || usage
[[ "$RELEASE_ID" =~ ^[0-9a-f]{20}$ && $# -eq 2 ]] || usage
PREFIX="nutrimaster-release-$RELEASE_ID"
RELEASE_ROOT="$RELEASES_ROOT/$PREFIX"
STATE_DIR="$STATE_ROOT/$RELEASE_ID"
ROLLBACK_SCRIPT="$RELEASE_ROOT/deploy/rollback-production.sh"

if [[ ! -d "$RELEASE_ROOT" || -L "$RELEASE_ROOT" ]]; then
  echo "Staged release is missing or unsafe: $RELEASE_ROOT" >&2
  exit 1
fi
if [[ "$(readlink -f -- "$CURRENT_LINK")" != "$RELEASE_ROOT" ]]; then
  echo "Stable current link does not point to the requested staged release" >&2
  exit 1
fi
if [[ "$(stat -c %a /root/code/nutrimaster/.env)" != "600" \
   || "$(stat -c %a /root/Projects/NutriMaster/.env)" != "600" ]]; then
  echo "Candidate and legacy production .env files must be mode 0600 before cutover" >&2
  exit 1
fi

"$RELEASE_ROOT/.venv/bin/python" \
  "$RELEASE_ROOT/deploy/migrate_production_gateway.py" \
  --check \
  --source /root/Projects/NutriMaster/.env \
  --destination /root/code/nutrimaster/.env

set -a
source "$RELEASE_ROOT/.env"
set +a
if [[ "${MAIN_MODEL:-}" != "$EXPECTED_MODEL" ]]; then
  echo "Production MAIN_MODEL must be exactly $EXPECTED_MODEL" >&2
  exit 1
fi
if [[ -n "${NUTRIMASTER_PI_MODEL:-}" && "$NUTRIMASTER_PI_MODEL" != "$EXPECTED_MODEL" ]]; then
  echo "Production NUTRIMASTER_PI_MODEL must be unset or exactly $EXPECTED_MODEL" >&2
  exit 1
fi

GENERATION_ID="$(sed -n '1p' "$RELEASE_ROOT/data/index/CURRENT" 2>/dev/null || true)"
if [[ ! "$GENERATION_ID" =~ ^[0-9a-f]{64}$ ]]; then
  echo "A fully bootstrapped immutable CURRENT generation is required" >&2
  exit 1
fi
PYTHONPATH="$RELEASE_ROOT/src" \
  "$RELEASE_ROOT/.venv/bin/python" -m nutrimaster.rag.index_builder_cli verify-active >/dev/null

listener_pid() {
  ss -H -ltnp "sport = :$1" | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -n1
}
validate_listener() {
  local port="$1"
  local expected_cwd="$2"
  local command_marker="$3"
  local pid
  local cwd
  pid="$(listener_pid "$port")"
  [[ -n "$pid" ]] || { echo "Expected listener is missing on $port" >&2; return 1; }
  cwd="$(readlink -f -- "/proc/$pid/cwd")"
  [[ "$cwd" == "$expected_cwd" ]] \
    || { echo "Unexpected listener cwd on $port (pid $pid cwd $cwd)" >&2; return 1; }
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq "$command_marker" \
    || { echo "Unexpected listener owner on $port (pid $pid)" >&2; return 1; }
  printf '%s' "$pid"
}

PID_5000="$(validate_listener 5000 /root/Projects/NutriMaster nutrimaster)"
PID_5002="$(validate_listener 5002 /root/code/nutrimaster python)"
PID_8787="$(validate_listener 8787 /root/code/nutrimaster/pi-runtime node)"

[[ ! -e "$STATE_DIR" && ! -L "$STATE_DIR" ]] \
  || { echo "Cutover state already exists; refusing to overwrite it: $STATE_DIR" >&2; exit 1; }

if [[ "$MODE" == "--preflight" ]]; then
  printf '%s\n' \
    "CUTOVER_PREFLIGHT_OK release_id=$RELEASE_ID generation=$GENERATION_ID model=$EXPECTED_MODEL pids=$PID_5000,$PID_5002,$PID_8787"
  exit 0
fi

if [[ "${NUTRIMASTER_PRODUCTION_CHANGE_APPROVED:-}" != "$RELEASE_ID" ]]; then
  echo "Refusing cutover without NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=$RELEASE_ID" >&2
  exit 1
fi
if [[ -z "${NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN:-}" ]]; then
  echo "NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN is required for authenticated cutover smoke" >&2
  exit 1
fi

CUTOVER_STARTED=0
rollback_on_error() {
  local exit_code=$?
  trap - ERR
  if [[ "$CUTOVER_STARTED" == "1" ]]; then
    echo "Cutover failed; starting bounded rollback" >&2
    NUTRIMASTER_PRODUCTION_CHANGE_APPROVED="$RELEASE_ID" \
      "$ROLLBACK_SCRIPT" --execute "$RELEASE_ID" || true
  fi
  exit "$exit_code"
}
trap rollback_on_error ERR

install -d -m 0700 "$STATE_ROOT"
install -d -m 0700 "$STATE_DIR"
cp --preserve=mode,timestamps "$NGINX_CONF" "$STATE_DIR/nutrimaster.conf.before"
{
  printf 'release_id=%s\n' "$RELEASE_ID"
  printf 'generation_id=%s\n' "$GENERATION_ID"
  printf 'model=%s\n' "$EXPECTED_MODEL"
  printf 'pid_5000=%s\n' "$PID_5000"
  printf 'pid_5002=%s\n' "$PID_5002"
  printf 'pid_8787=%s\n' "$PID_8787"
} > "$STATE_DIR/cutover.state"
CUTOVER_STARTED=1

for unit in \
  nutrimaster.slice \
  nutrimaster-pi.service \
  nutrimaster-unified.service \
  nutrimaster-bohrium-proxy.service \
  nutrimaster-bohrium-proxy.socket \
  nutrimaster-index-builder.service \
  nutrimaster-index-builder.path \
  nutrimaster-index-bootstrap.service \
  nutrimaster-index-bootstrap-recovery.service; do
  install -m 0644 "$RELEASE_ROOT/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
install -m 0644 "$RELEASE_ROOT/deploy/nginx/nutrimaster-unified.conf" "$NGINX_CONF"
systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/nutrimaster.slice \
  /etc/systemd/system/nutrimaster-pi.service \
  /etc/systemd/system/nutrimaster-unified.service \
  /etc/systemd/system/nutrimaster-bohrium-proxy.service \
  /etc/systemd/system/nutrimaster-bohrium-proxy.socket \
  /etc/systemd/system/nutrimaster-index-builder.service \
  /etc/systemd/system/nutrimaster-index-builder.path \
  /etc/systemd/system/nutrimaster-index-bootstrap.service \
  /etc/systemd/system/nutrimaster-index-bootstrap-recovery.service
nginx -t

# Stop only the three exact listener PIDs admitted above. No name-based kill,
# wildcard, tmux deletion, or trained* session operation is permitted.
kill -TERM "$PID_5000" "$PID_5002" "$PID_8787"
deadline=$((SECONDS + 45))
while (( SECONDS < deadline )); do
  if [[ -z "$(listener_pid 5000)" && -z "$(listener_pid 5002)" && -z "$(listener_pid 8787)" ]]; then
    break
  fi
  sleep 1
done
[[ -z "$(listener_pid 5000)" && -z "$(listener_pid 5002)" && -z "$(listener_pid 8787)" ]] \
  || { echo "One or more legacy listeners did not stop cleanly" >&2; false; }

systemctl enable --now nutrimaster-pi.service
wait_for_health() {
  local label="$1"
  local unit="$2"
  local url="$3"
  local timeout_seconds="$4"
  local deadline=$((SECONDS + timeout_seconds))
  local state=""
  while (( SECONDS < deadline )); do
    if curl -q --noproxy '*' --fail --silent --max-time 3 "$url" >/dev/null; then
      printf '%s\n' "READY label=$label unit=$unit url=$url"
      return 0
    fi
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    if [[ "$state" == "failed" || "$state" == "inactive" ]]; then
      systemctl --no-pager --full status "$unit" >&2 || true
      echo "$label entered state=$state before becoming healthy" >&2
      return 1
    fi
    sleep 1
  done
  systemctl --no-pager --full status "$unit" >&2 || true
  echo "$label did not become healthy within $timeout_seconds seconds" >&2
  return 1
}

wait_for_health \
  pi \
  nutrimaster-pi.service \
  http://127.0.0.1:8787/healthz \
  90
systemctl enable --now nutrimaster-unified.service
wait_for_health \
  unified \
  nutrimaster-unified.service \
  http://127.0.0.1:5000/api/health \
  240
systemctl enable --now nutrimaster-bohrium-proxy.socket
[[ "$(systemctl is-active nutrimaster-bohrium-proxy.socket)" == "active" ]]

[[ "$(systemctl show nutrimaster-unified.service -p MemoryLimit --value)" == "3221225472" ]]
[[ "$(systemctl show nutrimaster-pi.service -p MemoryLimit --value)" == "805306368" ]]
[[ "$(systemctl show nutrimaster-index-builder.service -p MemoryLimit --value)" == "2684354560" ]]

printf '%s\n' "$NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN" | \
  PYTHONPATH="$RELEASE_ROOT/src" "$RELEASE_ROOT/.venv/bin/python" \
  "$RELEASE_ROOT/deploy/smoke_production.py" \
  --token-stdin \
  --base-url http://127.0.0.1:5000 \
  --expected-generation "$GENERATION_ID" \
  > "$STATE_DIR/smoke-before-nginx.json"

nginx -s reload
sleep 2
for host in nutrimaster.bio nutrimaster.bohrium.com; do
  for path in /api/pi/internal /api/pi/internal/probe; do
    status="$(curl -q --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' \
      --max-time 15 "https://$host$path")"
    [[ "$status" == "404" ]] \
      || { echo "Public internal callback boundary returned $status for $host" >&2; false; }
  done
done

# Enable durable background builds only after the authenticated foreground
# smoke and public boundary have passed. This prevents a pending path event
# from competing with cold-start validation during the cutover window.
systemctl enable --now nutrimaster-index-builder.path

ss -H -ltnp | grep -E ':(5000|5002|8787)\b' > "$STATE_DIR/listeners.after"
[[ "$(systemctl is-active nutrimaster-bohrium-proxy.socket)" == "active" ]]
systemctl --no-pager --full status nutrimaster-unified.service nutrimaster-pi.service \
  > "$STATE_DIR/systemd.after"
printf '%s\n' "CUTOVER_OK release_id=$RELEASE_ID generation=$GENERATION_ID model=$EXPECTED_MODEL" \
  | tee "$STATE_DIR/CUTOVER_OK"
trap - ERR
