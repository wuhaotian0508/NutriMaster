#!/usr/bin/env bash
set -euo pipefail

EXPECTED_MODEL="deepseek-v4-flash"
RELEASES_ROOT="/root/code/nutrimaster-releases"
CURRENT_LINK="/root/code/nutrimaster-current"
PERSISTENT_ROOT="/root/code/nutrimaster"
LEGACY_ENV="/root/Projects/NutriMaster/.env"
CONFIG_BACKUP_ROOT="/root/code/nutrimaster-config-backups"

usage() {
  echo "usage: $0 --execute /absolute/path/nutrimaster-release-<id>.tar.gz" >&2
  exit 2
}

[[ "${1:-}" == "--execute" && $# -eq 2 ]] || usage
ARCHIVE="$2"
[[ "$ARCHIVE" == /* ]] || usage
ARCHIVE_NAME="$(basename -- "$ARCHIVE")"
if [[ ! "$ARCHIVE_NAME" =~ ^nutrimaster-release-([0-9a-f]{20})\.tar\.gz$ ]]; then
  echo "Invalid content-addressed release filename: $ARCHIVE_NAME" >&2
  exit 2
fi
RELEASE_ID="${BASH_REMATCH[1]}"
PREFIX="nutrimaster-release-$RELEASE_ID"
TARGET="$RELEASES_ROOT/$PREFIX"
PREVIOUS_TARGET=""

if [[ "${NUTRIMASTER_PRODUCTION_CHANGE_APPROVED:-}" != "$RELEASE_ID" ]]; then
  echo "Refusing production stage without NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=$RELEASE_ID" >&2
  exit 1
fi
if [[ ! -f "$ARCHIVE" || -L "$ARCHIVE" ]]; then
  echo "Release archive is missing or unsafe: $ARCHIVE" >&2
  exit 1
fi
if [[ ! -f "$ARCHIVE.sha256" || -L "$ARCHIVE.sha256" ]]; then
  echo "Release SHA256 sidecar is missing or unsafe: $ARCHIVE.sha256" >&2
  exit 1
fi
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  echo "Refusing to overwrite existing release target: $TARGET" >&2
  exit 1
fi
if [[ ! -f "$(dirname -- "$ARCHIVE")/build_release.py" ]]; then
  echo "Upload deploy/build_release.py beside the archive before staging" >&2
  exit 1
fi

if [[ -e "$CURRENT_LINK" || -L "$CURRENT_LINK" ]]; then
  if [[ ! -L "$CURRENT_LINK" ]]; then
    echo "Stable current path exists but is not a symlink: $CURRENT_LINK" >&2
    exit 1
  fi
  PREVIOUS_TARGET="$(readlink -f -- "$CURRENT_LINK")"
  if [[ ! -d "$PREVIOUS_TARGET" || -L "$PREVIOUS_TARGET" \
     || "$(dirname -- "$PREVIOUS_TARGET")" != "$RELEASES_ROOT" \
     || ! "$(basename -- "$PREVIOUS_TARGET")" =~ ^nutrimaster-release-[0-9a-f]{20}$ ]]; then
    echo "Stable current link does not point to a safe versioned release" >&2
    exit 1
  fi
  PREVIOUS_TARGET="$PREVIOUS_TARGET" "$PERSISTENT_ROOT/.venv/bin/python" - <<'PY'
import json
import os
from pathlib import Path

target = Path(os.environ["PREVIOUS_TARGET"])
manifest = json.loads((target / "RELEASE.json").read_text(encoding="utf-8"))
if target.name != f"nutrimaster-release-{manifest.get('release_id')}":
    raise RuntimeError("current release directory does not match its manifest")
if manifest.get("expected_model") != "deepseek-v4-flash":
    raise RuntimeError("current release model contract is invalid")
PY
fi

cd "$(dirname -- "$ARCHIVE")"
sha256sum --check --strict "$ARCHIVE_NAME.sha256"
"$PERSISTENT_ROOT/.venv/bin/python" ./build_release.py verify "$ARCHIVE"

install -d -m 0755 "$RELEASES_ROOT"
# build_release.py has already rejected traversal, links, devices, duplicate
# names, unlisted files, and hash mismatches. Extraction is into a new prefix
# and never overlays an existing release.
tar --extract --gzip --file "$ARCHIVE" --directory "$RELEASES_ROOT" \
  --no-same-owner --no-same-permissions
if [[ ! -d "$TARGET" || -L "$TARGET" ]]; then
  echo "Verified archive did not create the expected release directory" >&2
  exit 1
fi

TARGET="$TARGET" "$PERSISTENT_ROOT/.venv/bin/python" - <<'PY'
import hashlib
import json
import os
import stat
from pathlib import Path

target = Path(os.environ["TARGET"]).resolve()
manifest = json.loads((target / "RELEASE.json").read_text(encoding="utf-8"))
expected = {entry["path"]: entry for entry in manifest["files"]}
for relative, entry in expected.items():
    path = target / relative
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"staged release member is not a regular file: {relative}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if details.st_size != entry["size"] or digest != entry["sha256"]:
        raise RuntimeError(f"staged release member does not match RELEASE.json: {relative}")
actual = {
    str(path.relative_to(target))
    for path in target.rglob("*")
    if path.is_file() and path.name not in {"RELEASE.json", "SHA256SUMS"}
}
if actual != set(expected):
    raise RuntimeError("staged release contains files outside RELEASE.json")
if manifest.get("expected_model") != "deepseek-v4-flash":
    raise RuntimeError("staged release model contract is not deepseek-v4-flash")
PY

# The live 5000 and 8787 gateway is the authoritative known-good DeepSeek
# route. The temporary 5002 environment can drift independently; atomically
# hand off only the three gateway keys, with an exclusive rollback copy. This
# mutation is reachable only after the exact release approval above.
install -d -m 0700 "$CONFIG_BACKUP_ROOT"
"$PERSISTENT_ROOT/.venv/bin/python" \
  "$TARGET/deploy/migrate_production_gateway.py" \
  --source "$LEGACY_ENV" \
  --destination "$PERSISTENT_ROOT/.env" \
  --backup "$CONFIG_BACKUP_ROOT/$RELEASE_ID.env.before"
chmod 0600 "$LEGACY_ENV" "$PERSISTENT_ROOT/.env"

# Link only server-owned persistent state. Secrets, indexes, corpus, and the
# shared dependency environment are never copied into a release artifact.
ln -s "$PERSISTENT_ROOT/.env" "$TARGET/.env"
ln -s "$PERSISTENT_ROOT/data" "$TARGET/data"
ln -s "$PERSISTENT_ROOT/.venv" "$TARGET/.venv"
if [[ -d "$PERSISTENT_ROOT/pi-runtime/.pi-agent" && ! -L "$PERSISTENT_ROOT/pi-runtime/.pi-agent" ]]; then
  ln -s "$PERSISTENT_ROOT/pi-runtime/.pi-agent" "$TARGET/pi-runtime/.pi-agent"
fi

# Node dependencies are installed from the exact lock inside the versioned
# release. They are not shared with the live 8787 process.
cd "$TARGET/pi-runtime"
npm ci --omit=dev --ignore-scripts
npm run check

cd "$TARGET"
PYTHONPATH="$TARGET/src" "$TARGET/.venv/bin/python" -m compileall -q "$TARGET/src/nutrimaster"
set -a
source "$TARGET/.env"
set +a
if [[ "${MAIN_MODEL:-}" != "$EXPECTED_MODEL" ]]; then
  echo "Production MAIN_MODEL must be exactly $EXPECTED_MODEL" >&2
  exit 1
fi
if [[ -n "${NUTRIMASTER_PI_MODEL:-}" && "$NUTRIMASTER_PI_MODEL" != "$EXPECTED_MODEL" ]]; then
  echo "Production NUTRIMASTER_PI_MODEL must be unset or exactly $EXPECTED_MODEL" >&2
  exit 1
fi
PYTHONPATH="$TARGET/src" "$TARGET/.venv/bin/python" -m nutrimaster.cli check-config

if [[ -n "$PREVIOUS_TARGET" ]]; then
  NEXT_LINK="$RELEASES_ROOT/.nutrimaster-current-$RELEASE_ID"
  if [[ -e "$NEXT_LINK" || -L "$NEXT_LINK" ]]; then
    echo "Refusing to overwrite stale stable-link candidate: $NEXT_LINK" >&2
    exit 1
  fi
  ln -s "$TARGET" "$NEXT_LINK"
  mv -T "$NEXT_LINK" "$CURRENT_LINK"
else
  ln -s "$TARGET" "$CURRENT_LINK"
fi

printf '%s\n' \
  "STAGED release_id=$RELEASE_ID target=$TARGET previous=${PREVIOUS_TARGET:-none} model=$EXPECTED_MODEL"
