#!/usr/bin/env bash
set -euo pipefail
cd /data/haotianwu/biojson
set -a
source .env
set +a
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy
export PYTHONPATH=/data/haotianwu/biojson/src
export UV_LINK_MODE=copy
exec .venv/bin/python -m nutrimaster.cli web
