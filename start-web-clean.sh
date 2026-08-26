#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
set -a
source .env
set +a
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy
export PYTHONPATH="$SCRIPT_DIR/src"
export UV_LINK_MODE=copy
exec "$SCRIPT_DIR/.venv/bin/python" -m nutrimaster.cli web "$@"
