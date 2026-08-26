#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -r .env ]]; then
  echo "Missing readable environment file: $SCRIPT_DIR/.env" >&2
  exit 1
fi

set -a
source .env
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

# One canonical FastAPI process owns both legacy and Pi routes.  The Node Pi
# runtime remains a localhost sidecar and does not load the Python indexes.
export WEB_HOST=127.0.0.1
export WEB_PORT=5000
export NUTRIMASTER_UNIFIED_WEB_PORT=5000
export WEB_CONCURRENCY=1
export FORWARDED_ALLOW_IPS=127.0.0.1
export NUTRIMASTER_WEB_LIMIT_CONCURRENCY=32
export DEBUG=false
export NUTRIMASTER_DEV_AUTH_BYPASS=0
export NUTRIMASTER_DISABLE_BM25=0
export NUTRIMASTER_ENABLE_FIELD_KEYWORD=1
export NUTRIMASTER_REQUIRE_SPARSE_INDEXES=1
export NUTRIMASTER_REQUIRE_INDEX_GENERATION=1
export NUTRIMASTER_SPARSE_INDEX_BUILD_ON_MISS=0
export NUTRIMASTER_WEB_BUILD_INDEX=0
export NUTRIMASTER_WEB_BUILD_GRAPH=0
export NUTRIMASTER_ALLOW_ONLINE_REINDEX=0
export NUTRIMASTER_PIPELINE_DEFAULT_WORKERS=1
export NUTRIMASTER_PIPELINE_MAX_WORKERS=1
export NUTRIMASTER_EXTRACTION_MAX_MARKDOWN_BYTES=16777216
export NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE=16
export NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS=900
export NUTRIMASTER_RAG_MAX_CONCURRENT_SEARCHES=1
export NUTRIMASTER_RAG_MMAP_EMBEDDINGS=true
export NUTRIMASTER_GRAPH_BACKEND="${NUTRIMASTER_GRAPH_BACKEND:-sqlite}"
export NUTRIMASTER_REQUIRE_GRAPH_INDEX=1
export NUTRIMASTER_PI_PORT=8787
export NUTRIMASTER_PI_RUNTIME_URL="http://127.0.0.1:8787"
export NUTRIMASTER_PI_TOOL_ENDPOINT="http://127.0.0.1:${WEB_PORT}/api/pi/internal/tools"
export NUTRIMASTER_PI_TOOL_TIMEOUT_SECONDS="${NUTRIMASTER_PI_TOOL_TIMEOUT_SECONDS:-120}"
export NUTRIMASTER_PI_MAX_ACTIVE_RUNS=8
export NUTRIMASTER_PI_TURN_TIMEOUT_SECONDS=300
export NUTRIMASTER_JINA_QUERY_TIMEOUT_SECONDS=15
export NUTRIMASTER_JINA_QUERY_MAX_ATTEMPTS=2
export MALLOC_ARENA_MAX=2
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export BLIS_NUM_THREADS=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$SCRIPT_DIR/src"
export UV_LINK_MODE=copy

if [[ "$NUTRIMASTER_GRAPH_BACKEND" == "off" ]]; then
  echo "Production graph retrieval cannot be disabled" >&2
  exit 1
fi

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy

# Starting the canonical service while either old Python listener is alive
# would put two index owners back on the host before Uvicorn binds. Inspect
# only ports 5000/5002 and fail closed; operators must stop known owners.
SS_BIN=""
for candidate in /usr/bin/ss /bin/ss /usr/sbin/ss /sbin/ss; do
  if [[ -x "$candidate" ]]; then
    SS_BIN="$candidate"
    break
  fi
done
if [[ -z "$SS_BIN" ]]; then
  echo "Cannot verify that production ports 5000/5002 are closed: ss is unavailable" >&2
  exit 1
fi
if ! OLD_5000_LISTENERS="$("$SS_BIN" -H -ltn 'sport = :5000')"; then
  echo "Cannot verify that port 5000 is closed: ss failed" >&2
  exit 1
fi
if [[ -n "$OLD_5000_LISTENERS" ]]; then
  echo "Port 5000 is still listening; stop and disable the known old 5000 service before starting the unified service" >&2
  exit 1
fi
if ! LEGACY_5002_LISTENERS="$("$SS_BIN" -H -ltn 'sport = :5002')"; then
  echo "Cannot verify that legacy port 5002 is closed: ss failed" >&2
  exit 1
fi
if [[ -n "$LEGACY_5002_LISTENERS" ]]; then
  echo "Legacy port 5002 is still listening; stop and disable the known old 5002 service before starting the unified service" >&2
  exit 1
fi

if ! /usr/bin/curl -q --noproxy '*' --fail --silent --show-error --max-time 3 \
  "${NUTRIMASTER_PI_RUNTIME_URL}/healthz" >/dev/null; then
  echo "Warning: Pi runtime health check failed; legacy service will still start: ${NUTRIMASTER_PI_RUNTIME_URL}/healthz" >&2
fi

# Validate hashes, array shapes, compact sparse metadata, and SQLite indexes in
# a short-lived process before the request process unpickles chunks.pkl.
"$SCRIPT_DIR/.venv/bin/python" -m nutrimaster.rag.index_builder_cli verify-active

exec "$SCRIPT_DIR/.venv/bin/python" -m nutrimaster.cli web \
  --host "$WEB_HOST" \
  --port "$WEB_PORT"
