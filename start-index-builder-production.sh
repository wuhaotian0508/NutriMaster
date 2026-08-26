#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BUILDER_COMMAND="${1:-run-once}"
if [[ "$BUILDER_COMMAND" != "run-once" \
   && "$BUILDER_COMMAND" != "recover-interrupted" \
   && "$BUILDER_COMMAND" != "bootstrap-legacy" \
   && "$BUILDER_COMMAND" != "preflight-legacy" \
   && "$BUILDER_COMMAND" != "recover-bootstrap" ]]; then
  echo "Unsupported index builder command: $BUILDER_COMMAND" >&2
  exit 2
fi

if [[ ! -r .env ]]; then
  echo "Missing readable environment file: $SCRIPT_DIR/.env" >&2
  exit 1
fi

set -a
source .env
set +a

# This process has its own systemd cgroup. It is the only production process
# allowed to construct dense, sparse, field-keyword, and graph artifacts.
export NUTRIMASTER_REQUIRE_INDEX_GENERATION=1
export NUTRIMASTER_REQUIRE_SPARSE_INDEXES=1
export NUTRIMASTER_SPARSE_INDEX_BUILD_ON_MISS=0
export NUTRIMASTER_DISABLE_BM25=0
export NUTRIMASTER_ENABLE_FIELD_KEYWORD=1
export NUTRIMASTER_RAG_MMAP_EMBEDDINGS=true
export NUTRIMASTER_UNIFIED_WEB_PORT=5000
export NUTRIMASTER_INDEX_ACTIVATION_TIMEOUT_SECONDS=120
export NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES="${NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES:-1073741824}"
export NUTRIMASTER_LEGACY_BOOTSTRAP_DISK_SAFETY_BYTES="${NUTRIMASTER_LEGACY_BOOTSTRAP_DISK_SAFETY_BYTES:-1073741824}"
export NUTRIMASTER_INDEX_BUILDER_AUTO_ACTIVATE="${NUTRIMASTER_INDEX_BUILDER_AUTO_ACTIVATE:-true}"
export MALLOC_ARENA_MAX=2
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export BLIS_NUM_THREADS=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$SCRIPT_DIR/src"
export UV_LINK_MODE=copy

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy

if [[ "$BUILDER_COMMAND" == "bootstrap-legacy" || "$BUILDER_COMMAND" == "preflight-legacy" ]]; then
  if [[ -z "${NUTRIMASTER_LEGACY_INDEX_SOURCE:-}" ]]; then
    echo "NUTRIMASTER_LEGACY_INDEX_SOURCE is required for $BUILDER_COMMAND" >&2
    exit 1
  fi
  if [[ ! -d "$NUTRIMASTER_LEGACY_INDEX_SOURCE" ]]; then
    echo "Legacy index source is not a directory: $NUTRIMASTER_LEGACY_INDEX_SOURCE" >&2
    exit 1
  fi
fi

exec "$SCRIPT_DIR/.venv/bin/python" -m nutrimaster.rag.index_builder_cli "$BUILDER_COMMAND"
