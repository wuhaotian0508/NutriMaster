#!/usr/bin/env bash
set -euo pipefail

EXPECTED_MODEL="deepseek-v4-flash"
RELEASES_ROOT="/root/code/nutrimaster-releases"
CURRENT_LINK="/root/code/nutrimaster-current"
BOOTSTRAP_STATE_ROOT="/root/code/nutrimaster-bootstrap"

if [[ ( "${1:-}" != "--preflight" && "${1:-}" != "--execute" ) \
   || ! "${2:-}" =~ ^[0-9a-f]{20}$ || $# -ne 2 ]]; then
  echo "usage: $0 [--preflight|--execute] <20-hex-release-id>" >&2
  exit 2
fi
MODE="$1"
RELEASE_ID="$2"
RELEASE_ROOT="$RELEASES_ROOT/nutrimaster-release-$RELEASE_ID"
STATE_DIR="$BOOTSTRAP_STATE_ROOT/$RELEASE_ID"

if [[ ! -d "$RELEASE_ROOT" || -L "$RELEASE_ROOT" ]]; then
  echo "Staged release is missing or unsafe: $RELEASE_ROOT" >&2
  exit 1
fi
if [[ "$(readlink -f -- "$CURRENT_LINK")" != "$RELEASE_ROOT" ]]; then
  echo "Stable current link does not point to the requested staged release" >&2
  exit 1
fi
set -a
source "$RELEASE_ROOT/.env"
set +a
if [[ "${MAIN_MODEL:-}" != "$EXPECTED_MODEL" ]]; then
  echo "Production MAIN_MODEL must be exactly $EXPECTED_MODEL" >&2
  exit 1
fi
if [[ -e "$RELEASE_ROOT/data/index/CURRENT" || -L "$RELEASE_ROOT/data/index/CURRENT" ]]; then
  echo "Bootstrap is one-time only; CURRENT already exists" >&2
  exit 1
fi

export NUTRIMASTER_LEGACY_INDEX_SOURCE=/root/Projects/NutriMaster/data/index
PYTHONPATH="$RELEASE_ROOT/src" \
  "$RELEASE_ROOT/start-index-builder-production.sh" preflight-legacy

if [[ "$MODE" == "--preflight" ]]; then
  printf '%s\n' "BOOTSTRAP_PREFLIGHT_OK release_id=$RELEASE_ID model=$EXPECTED_MODEL"
  exit 0
fi
if [[ "${NUTRIMASTER_PRODUCTION_CHANGE_APPROVED:-}" != "$RELEASE_ID" ]]; then
  echo "Refusing bootstrap without NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=$RELEASE_ID" >&2
  exit 1
fi
if [[ -e "$STATE_DIR" || -L "$STATE_DIR" ]]; then
  echo "Bootstrap state already exists; refusing to overwrite it: $STATE_DIR" >&2
  exit 1
fi

install -d -m 0700 "$BOOTSTRAP_STATE_ROOT"
install -d -m 0700 "$STATE_DIR"
for unit in \
  nutrimaster.slice \
  nutrimaster-index-bootstrap.service \
  nutrimaster-index-bootstrap-recovery.service; do
  install -m 0644 "$RELEASE_ROOT/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/nutrimaster.slice \
  /etc/systemd/system/nutrimaster-index-bootstrap.service \
  /etc/systemd/system/nutrimaster-index-bootstrap-recovery.service

if ! systemctl start nutrimaster-index-bootstrap.service; then
  systemctl start nutrimaster-index-bootstrap-recovery.service || true
  systemctl --no-pager --full status nutrimaster-index-bootstrap.service \
    > "$STATE_DIR/bootstrap.failed" || true
  echo "Legacy bootstrap failed; recovery was attempted under the same 2560 MiB limit" >&2
  exit 1
fi

GENERATION_ID="$(sed -n '1p' "$RELEASE_ROOT/data/index/CURRENT")"
[[ "$GENERATION_ID" =~ ^[0-9a-f]{64}$ ]]
PYTHONPATH="$RELEASE_ROOT/src" \
  "$RELEASE_ROOT/.venv/bin/python" -m nutrimaster.rag.index_builder_cli verify-active \
  > "$STATE_DIR/verify-active.json"
systemctl show nutrimaster-index-bootstrap.service \
  -p Result -p MemoryLimit -p MemoryCurrent -p OOMPolicy \
  > "$STATE_DIR/systemd.result"
[[ "$(systemctl show nutrimaster-index-bootstrap.service -p Result --value)" == "success" ]]
[[ "$(systemctl show nutrimaster-index-bootstrap.service -p MemoryLimit --value)" == "2684354560" ]]
printf '%s\n' "BOOTSTRAP_OK release_id=$RELEASE_ID generation=$GENERATION_ID model=$EXPECTED_MODEL" \
  | tee "$STATE_DIR/BOOTSTRAP_OK"
