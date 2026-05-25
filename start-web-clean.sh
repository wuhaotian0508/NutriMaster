#!/usr/bin/env bash
set -euo pipefail
cd /root/Projects/NutriMaster
set -a
source .env
set +a
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy
export PYTHONPATH=/root/Projects/NutriMaster/src
exec /root/Projects/NutriMaster/.venv/bin/python -m nutrimaster.cli web
