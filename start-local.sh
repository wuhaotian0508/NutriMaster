#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -r .env ]]; then
  echo "Missing readable environment file: $SCRIPT_DIR/.env" >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv/bin/python; run: uv sync --dev" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required for the Pi runtime (Node >=22.19)" >&2
  exit 1
fi
if [[ ! -d pi-runtime/node_modules/@earendil-works ]]; then
  echo "Pi dependencies are missing; run: npm --prefix pi-runtime ci" >&2
  exit 1
fi

set -a
source .env
set +a

export PYTHONPATH="$SCRIPT_DIR/src"
export UV_LINK_MODE=copy
export NUTRIMASTER_PI_HOST=127.0.0.1
export NUTRIMASTER_PI_PORT="${NUTRIMASTER_LOCAL_PI_PORT:-8787}"
export NUTRIMASTER_PI_RUNTIME_URL="http://127.0.0.1:${NUTRIMASTER_PI_PORT}"
export NUTRIMASTER_PI_AGENT_DIR="$SCRIPT_DIR/pi-runtime/.pi-agent"
export WEB_HOST="${NUTRIMASTER_LOCAL_WEB_HOST:-127.0.0.1}"
export WEB_PORT="${NUTRIMASTER_LOCAL_WEB_PORT:-5000}"

if [[ "$WEB_PORT" == "$NUTRIMASTER_PI_PORT" ]]; then
  echo "Local Web and Pi ports must be different" >&2
  exit 1
fi

if ! curl -q --noproxy '*' --fail --silent --show-error --max-time 1 \
  "$NUTRIMASTER_PI_RUNTIME_URL/healthz" >/dev/null 2>&1; then
  :
else
  echo "Pi runtime port $NUTRIMASTER_PI_PORT is already in use" >&2
  exit 1
fi

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "${WEB_PID:-}" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill -TERM "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "${PI_PID:-}" ]] && kill -0 "$PI_PID" 2>/dev/null; then
    kill -TERM "$PI_PID" 2>/dev/null || true
    wait "$PI_PID" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

(
  cd "$SCRIPT_DIR/pi-runtime"
  exec node src/server.js
) &
PI_PID=$!

ready=0
for _ in {1..30}; do
  if curl -q --noproxy '*' --fail --silent --show-error --max-time 1 \
    "$NUTRIMASTER_PI_RUNTIME_URL/healthz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$PI_PID" 2>/dev/null; then
    wait "$PI_PID" || true
    echo "Pi runtime exited before readiness" >&2
    exit 1
  fi
  sleep 0.5
done
if [[ "$ready" != 1 ]]; then
  echo "Pi runtime did not become ready at $NUTRIMASTER_PI_RUNTIME_URL/healthz" >&2
  exit 1
fi

echo "Pi runtime ready at $NUTRIMASTER_PI_RUNTIME_URL"
"$SCRIPT_DIR/.venv/bin/python" -m nutrimaster.cli web \
  --host "$WEB_HOST" \
  --port "$WEB_PORT" &
WEB_PID=$!
wait "$WEB_PID"
