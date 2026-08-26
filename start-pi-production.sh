#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LIVE_ENV_FILE="${NUTRIMASTER_PI_GATEWAY_ENV:-$SCRIPT_DIR/.env}"

if [[ ! -r "$LIVE_ENV_FILE" ]]; then
  echo "Pi gateway environment file is not readable: $LIVE_ENV_FILE" >&2
  exit 1
fi

# Use the same deployment environment as the unified Python service unless an
# operator explicitly supplies a different gateway file.
set -a
source "$LIVE_ENV_FILE"
set +a

EXPECTED_MODEL="deepseek-v4-flash"
if [[ "${MAIN_MODEL:-}" != "$EXPECTED_MODEL" ]]; then
  echo "Production MAIN_MODEL must be exactly $EXPECTED_MODEL" >&2
  exit 1
fi
if [[ -n "${NUTRIMASTER_PI_MODEL:-}" && "$NUTRIMASTER_PI_MODEL" != "$EXPECTED_MODEL" ]]; then
  echo "Production NUTRIMASTER_PI_MODEL must be unset or exactly $EXPECTED_MODEL" >&2
  exit 1
fi

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy
unset NODE_TLS_REJECT_UNAUTHORIZED NODE_USE_ENV_PROXY
export NUTRIMASTER_PI_MODEL="${NUTRIMASTER_PI_MODEL:-$EXPECTED_MODEL}"
export NUTRIMASTER_PI_HOST=127.0.0.1
export NUTRIMASTER_PI_PORT=8787
export NUTRIMASTER_PI_TURN_TIMEOUT_SECONDS=300
export NUTRIMASTER_PI_AGENT_DIR="$SCRIPT_DIR/pi-runtime/.pi-agent"
export NUTRIMASTER_PI_CONTEXT_WINDOW="${NUTRIMASTER_PI_CONTEXT_WINDOW:-128000}"
export NUTRIMASTER_PI_MAX_TOKENS="${NUTRIMASTER_PI_MAX_TOKENS:-8192}"
export NUTRIMASTER_PI_MAX_ACTIVE_RUNS="${NUTRIMASTER_PI_MAX_ACTIVE_RUNS:-8}"
# Keep V8 below the sidecar's 768 MiB cgroup ceiling.  Callback bodies, SSE
# pending bytes, and active turns are separately bounded in the runtime.
export NODE_OPTIONS="--max-old-space-size=512"
export PATH="/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"

cd "$SCRIPT_DIR/pi-runtime"
# Make Node (not an npm wrapper) the systemd main process so SIGTERM, RSS
# accounting, OOM attribution, and restart status apply to the actual runtime.
exec node src/server.js
