#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


DEPLOY_DIR = Path(__file__).resolve().parent
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

from build_release import EXPECTED_MODEL, verify_release  # noqa: E402


ROOT = DEPLOY_DIR.parent
DEFAULT_HOST = "ali"
MIN_POST_STAGE_FREE_BYTES = 6 * 1024**3
_HOST_RE = re.compile(r"[A-Za-z0-9_.@-]+\Z")
_RELEASE_ID_RE = re.compile(r"[0-9a-f]{20}\Z")
_PHASES = {"pre-stage", "staged"}


_REMOTE_PREFLIGHT = r'''
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

EXPECTED_MODEL = sys.argv[1]
RELEASE_ID = sys.argv[2]
ARCHIVE_BYTES = int(sys.argv[3])
EXPANDED_BYTES = int(sys.argv[4])
MIN_POST_STAGE_FREE_BYTES = int(sys.argv[5])
PHASE = sys.argv[6]

CODE_ROOT = Path("/root/code/nutrimaster")
OLD_ROOT = Path("/root/Projects/NutriMaster")
INDEX_ROOT = CODE_ROOT / "data/index"
LEGACY_SOURCE = OLD_ROOT / "data/index"
CORPUS_DIR = CODE_ROOT / "data/corpus"
TARGET_RELEASE = Path("/root/code/nutrimaster-releases") / f"nutrimaster-release-{RELEASE_ID}"
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")

checks = {}
failures = []
warnings = []


def check(name, condition, detail, *, blocking=True):
    checks[name] = {"ok": bool(condition), "detail": detail}
    if not condition:
        (failures if blocking else warnings).append(f"{name}: {detail}")


def dotenv(path):
    values = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    except OSError:
        return {}
    return values


def pid_for_port(port):
    completed = subprocess.run(
        ["/usr/sbin/ss" if Path("/usr/sbin/ss").exists() else "/usr/bin/ss", "-H", "-ltnp", f"sport = :{port}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    match = re.search(r"pid=(\d+)", completed.stdout)
    return int(match.group(1)) if match else None


def proc_cmd(pid):
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def proc_cwd(pid):
    try:
        return str((Path("/proc") / str(pid) / "cwd").resolve(strict=True))
    except OSError:
        return ""


def proc_env(pid):
    values = {}
    try:
        entries = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return values
    for entry in entries:
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        values[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return values


def health(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            payload = json.load(response)
        return int(response.status), payload
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def file_size(path):
    try:
        details = path.lstat()
    except OSError:
        return 0
    return details.st_size if stat.S_ISREG(details.st_mode) and not path.is_symlink() else 0


def tree_json_bytes(path):
    return sum(
        child.stat().st_size
        for child in path.glob("*.json")
        if child.is_file() and not child.is_symlink()
    )


code_env = dotenv(CODE_ROOT / ".env")
old_env = dotenv(OLD_ROOT / ".env")
required_keys = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "JINA_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
)
check(
    "code_environment_keys",
    all(code_env.get(key) for key in required_keys),
    {key: bool(code_env.get(key)) for key in required_keys},
)
check("code_main_model", code_env.get("MAIN_MODEL") == EXPECTED_MODEL, EXPECTED_MODEL)
check("old_main_model", old_env.get("MAIN_MODEL") == EXPECTED_MODEL, EXPECTED_MODEL)
pi_override = code_env.get("NUTRIMASTER_PI_MODEL")
check(
    "pi_model_override",
    pi_override in (None, "", EXPECTED_MODEL),
    "unset or deepseek-v4-flash",
)
check(
    "jina_proxy_config",
    code_env.get("NUTRIMASTER_JINA_PROXY_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    and code_env.get("NUTRIMASTER_JINA_PROXY_URL") == "http://127.0.0.1:7890",
    "enabled via http://127.0.0.1:7890",
)
gateway_keys = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "MAIN_MODEL")
gateway_config_matches_live = all(
    code_env.get(key) and code_env.get(key) == old_env.get(key) for key in gateway_keys
)
check(
    "gateway_config_matches_live",
    gateway_config_matches_live,
    (
        "candidate gateway matches the proven live 5000/8787 gateway"
        if gateway_config_matches_live
        else "approved stage must atomically hand off the proven live 5000/8787 gateway"
    ),
    # The approved stage performs a verified, backed-up, atomic handoff when
    # the temporary 5002 environment has drifted. After staging, drift blocks.
    blocking=PHASE == "staged",
)

for name, path in (
    ("candidate_environment_permissions", CODE_ROOT / ".env"),
    ("legacy_environment_permissions", OLD_ROOT / ".env"),
):
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = 0o777
    check(
        name,
        mode & 0o077 == 0,
        f"mode={mode:04o}",
        # The explicitly approved stage tightens both server-owned secret
        # files after the atomic gateway handoff.
        blocking=PHASE == "staged",
    )

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


def probe_deepseek(environment):
    if OpenAI is None:
        return {"ok": False, "exception_type": "OpenAIImportError"}
    try:
        response = OpenAI(
            api_key=environment["OPENAI_API_KEY"],
            base_url=environment["OPENAI_BASE_URL"],
            max_retries=2,
            timeout=90,
        ).chat.completions.create(
            model=EXPECTED_MODEL,
            messages=[
                {"role": "user", "content": "Reply with exactly: nutrimaster-ok"}
            ],
            temperature=0,
            # deepseek-v4-flash can emit reasoning_content before content. A
            # 16-token probe can return HTTP 200 with no final answer.
            max_tokens=64,
        )
        content = response.choices[0].message.content or ""
        return {
            "ok": response.model == EXPECTED_MODEL and "nutrimaster-ok" in content.lower(),
            "response_model_ok": response.model == EXPECTED_MODEL,
            "content_ok": "nutrimaster-ok" in content.lower(),
        }
    except Exception as exc:
        body = getattr(exc, "body", None)
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            error = body if isinstance(body, dict) else {}
        return {
            "ok": False,
            "exception_type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
            "error_code": error.get("code"),
        }


live_model_probe = probe_deepseek(old_env)
candidate_model_probe = (
    live_model_probe if gateway_config_matches_live else probe_deepseek(code_env)
)
check("live_deepseek_probe", live_model_probe["ok"], live_model_probe)
check(
    "candidate_deepseek_probe",
    candidate_model_probe["ok"] if PHASE == "staged" else live_model_probe["ok"],
    candidate_model_probe
    if candidate_model_probe["ok"] or PHASE == "staged"
    else {
        "candidate": candidate_model_probe,
        "handoff_source": live_model_probe,
    },
)

expected_processes = {
    5000: ("/root/Projects/NutriMaster", "nutrimaster"),
    5002: ("/root/code/nutrimaster", "python"),
    8787: ("/root/code/nutrimaster/pi-runtime", "node"),
}
processes = {}
for port, (expected_cwd, command_marker) in expected_processes.items():
    pid = pid_for_port(port)
    command = proc_cmd(pid) if pid else ""
    cwd = proc_cwd(pid) if pid else ""
    owner_ok = cwd == expected_cwd and command_marker in command
    processes[str(port)] = {
        "pid": pid,
        "cwd_ok": cwd == expected_cwd,
        "command_marker_ok": command_marker in command,
    }
    check(f"listener_{port}", pid is not None and owner_ok, processes[str(port)])

if processes["5002"]["pid"]:
    environment = proc_env(processes["5002"]["pid"])
    check(
        "live_5002_model_proxy",
        environment.get("MAIN_MODEL") == EXPECTED_MODEL
        and environment.get("NUTRIMASTER_JINA_PROXY_ENABLED", "").lower() in {"1", "true", "yes", "on"},
        "deepseek-v4-flash with Jina proxy enabled",
    )
if processes["8787"]["pid"]:
    environment = proc_env(processes["8787"]["pid"])
    check(
        "live_8787_model",
        environment.get("MAIN_MODEL") == EXPECTED_MODEL
        and environment.get("NUTRIMASTER_PI_MODEL") == EXPECTED_MODEL,
        EXPECTED_MODEL,
    )
    check(
        "live_8787_gateway",
        all(environment.get(key) == old_env.get(key) for key in gateway_keys),
        "8787 matches the proven live gateway without exposing credentials",
    )

for port, url in {
    5000: "http://127.0.0.1:5000/api/health",
    5002: "http://127.0.0.1:5002/api/health",
    8787: "http://127.0.0.1:8787/healthz",
}.items():
    status, payload = health(url)
    check(
        f"health_{port}",
        status == 200 and payload.get("status") in {"ok", "healthy"},
        {"http_status": status, "service_status": payload.get("status")},
    )
check("clash_7890", port_open(7890), "127.0.0.1:7890 reachable")
check("clash_9090", port_open(9090), "127.0.0.1:9090 reachable")

free = shutil.disk_usage(CODE_ROOT).free
stage_bytes = 0 if PHASE == "staged" else ARCHIVE_BYTES + EXPANDED_BYTES
post_stage = free - stage_bytes
check(
    "release_stage_disk",
    post_stage >= MIN_POST_STAGE_FREE_BYTES,
    {
        "available_bytes": free,
        "stage_bytes": stage_bytes,
        "post_stage_bytes": post_stage,
        "minimum_post_stage_bytes": MIN_POST_STAGE_FREE_BYTES,
    },
)
if PHASE == "pre-stage":
    check(
        "release_target_absent",
        not TARGET_RELEASE.exists() and not TARGET_RELEASE.is_symlink(),
        str(TARGET_RELEASE),
    )
else:
    staged_ok = TARGET_RELEASE.is_dir() and not TARGET_RELEASE.is_symlink()
    staged_detail = {"target": str(TARGET_RELEASE)}
    if staged_ok:
        try:
            release_manifest = json.loads(
                (TARGET_RELEASE / "RELEASE.json").read_text(encoding="utf-8")
            )
            staged_ok = (
                release_manifest.get("release_id") == RELEASE_ID
                and release_manifest.get("expected_model") == EXPECTED_MODEL
                and (TARGET_RELEASE / ".env").resolve() == CODE_ROOT / ".env"
                and (TARGET_RELEASE / "data").resolve() == CODE_ROOT / "data"
                and (TARGET_RELEASE / ".venv").resolve() == CODE_ROOT / ".venv"
                and (TARGET_RELEASE / "pi-runtime/node_modules").is_dir()
                and not (TARGET_RELEASE / "pi-runtime/node_modules").is_symlink()
            )
        except Exception as exc:
            staged_ok = False
            staged_detail["error"] = f"{type(exc).__name__}: {exc}"
    check("release_target_staged", staged_ok, staged_detail)
    check(
        "stable_current_link",
        Path("/root/code/nutrimaster-current").is_symlink()
        and Path("/root/code/nutrimaster-current").resolve() == TARGET_RELEASE.resolve(),
        str(TARGET_RELEASE),
    )

managed_live_units = (
    "nutrimaster-unified.service",
    "nutrimaster-pi.service",
    "nutrimaster-index-builder.service",
    "nutrimaster-index-builder.path",
)
existing_live_units = [
    name for name in managed_live_units if (Path("/etc/systemd/system") / name).exists()
]
managed_unit_states = {}
managed_units_safe = True
for name in existing_live_units:
    completed = subprocess.run(
        ["systemctl", "show", name, "-p", "ActiveState", "-p", "UnitFileState"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    properties = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    managed_unit_states[name] = properties
    managed_units_safe = managed_units_safe and (
        completed.returncode == 0
        and properties.get("ActiveState") == "inactive"
        and properties.get("UnitFileState") in {"disabled", "static"}
    )
check(
    "managed_live_units_safe",
    managed_units_safe,
    managed_unit_states if existing_live_units else "not installed",
)
check("legacy_and_target_same_filesystem", LEGACY_SOURCE.stat().st_dev == INDEX_ROOT.stat().st_dev, "hard-link capable")
generation_root = INDEX_ROOT / "generations"
generation_entries = list(generation_root.iterdir()) if generation_root.is_dir() and not generation_root.is_symlink() else []
current_pointer = INDEX_ROOT / "CURRENT"
bootstrap_required_now = not current_pointer.exists() and not current_pointer.is_symlink()
if bootstrap_required_now:
    check("current_pointer_absent", True, "legacy flat layout")
    check("generation_root_empty", not generation_entries, [entry.name for entry in generation_entries[:10]])
else:
    try:
        if current_pointer.is_symlink() or not current_pointer.is_file():
            raise RuntimeError("CURRENT is not a regular file")
        generation_id = current_pointer.read_text(encoding="ascii").strip()
        if not SHA_RE.fullmatch(generation_id):
            raise RuntimeError("CURRENT generation id is invalid")
        generation_dir = generation_root / generation_id
        if generation_dir.is_symlink() or not generation_dir.is_dir():
            raise RuntimeError("CURRENT generation directory is missing or unsafe")
        current_release = Path("/root/code/nutrimaster-current")
        completed = subprocess.run(
            [
                str(current_release / ".venv/bin/python"),
                "-m",
                "nutrimaster.rag.index_builder_cli",
                "verify-active",
            ],
            cwd=current_release,
            env={**os.environ, "PYTHONPATH": str(current_release / "src")},
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            raise RuntimeError("verify-active failed")
        active_report = json.loads(completed.stdout)
        if (
            active_report.get("status") != "ok"
            or active_report.get("generation_id") != generation_id
        ):
            raise RuntimeError("verify-active generation mismatch")
        active_generation_ok = True
        active_generation_detail = {
            "generation_id": generation_id,
            "generation_entries": len(generation_entries),
        }
    except Exception as exc:
        active_generation_ok = False
        active_generation_detail = {"error": f"{type(exc).__name__}: {exc}"}
    check("active_generation_valid", active_generation_ok, active_generation_detail)

chunks_bytes = file_size(LEGACY_SOURCE / "chunks.pkl")
embeddings_bytes = file_size(LEGACY_SOURCE / "embeddings.npy")
corpus_bytes = tree_json_bytes(CORPUS_DIR)
bm25_workspace = max(
    file_size(LEGACY_SOURCE / "bm25.pkl"),
    file_size(INDEX_ROOT / "bm25.pkl"),
    file_size(LEGACY_SOURCE / "bm25_sparse_v4.pkl"),
    file_size(INDEX_ROOT / "bm25_sparse_v4.pkl"),
    chunks_bytes,
)
field_workspace = max(
    2 * file_size(LEGACY_SOURCE / "field_keyword.pkl"),
    2 * file_size(INDEX_ROOT / "field_keyword.pkl"),
    2 * file_size(LEGACY_SOURCE / "field_keyword_v3.sqlite3"),
    2 * file_size(INDEX_ROOT / "field_keyword_v3.sqlite3"),
    4 * chunks_bytes,
)
graph_workspace = max(
    2 * file_size(LEGACY_SOURCE / "graph_index.sqlite"),
    2 * file_size(INDEX_ROOT / "graph_index.sqlite"),
    4 * corpus_bytes,
)
try:
    import numpy as np
    embeddings = np.load(LEGACY_SOURCE / "embeddings.npy", mmap_mode="r")
    embedding_rows = int(embeddings.shape[0]) if embeddings.ndim == 2 else -1
    embedding_shape = [int(value) for value in embeddings.shape]
    del embeddings
except Exception as exc:
    embedding_rows = -1
    embedding_shape = [f"{type(exc).__name__}: {exc}"]
norms_bytes = max(0, embedding_rows) * 4 + 4096
bootstrap_required = (
    corpus_bytes
    + bm25_workspace
    + field_workspace
    + graph_workspace
    + norms_bytes
    + 1024**3
)
if bootstrap_required_now:
    check(
        "bootstrap_disk",
        post_stage >= bootstrap_required,
        {
            "post_stage_bytes": post_stage,
            "required_bytes": bootstrap_required,
            "corpus_snapshot_bytes": corpus_bytes,
            "bm25_workspace_bytes": bm25_workspace,
            "field_workspace_bytes": field_workspace,
            "graph_workspace_bytes": graph_workspace,
            "safety_bytes": 1024**3,
        },
    )
else:
    check(
        "bootstrap_disk",
        True,
        "skipped: a fully verified immutable generation is already active",
    )

manifest_path = LEGACY_SOURCE / "manifest.json"
try:
    raw = manifest_path.read_bytes()
    if len(raw) > 64 * 1024**2:
        raise RuntimeError("manifest is too large")
    manifest = json.loads(raw)
    entries = manifest["files"]
    files = {
        path.name: path
        for path in CORPUS_DIR.glob("*.json")
        if path.is_file() and not path.is_symlink()
    }
    if set(entries) != set(files):
        raise RuntimeError("manifest/corpus file sets differ")
    cursor = 0
    # Preserve the dense manifest's insertion order: its start/end offsets are
    # assigned in build order, not alphabetical filename order.
    for name, entry in entries.items():
        path = files[name]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not isinstance(entry, dict) or not SHA_RE.fullmatch(str(entry.get("sha", ""))):
            raise RuntimeError(f"invalid manifest entry: {name}")
        if digest != entry["sha"]:
            raise RuntimeError(f"corpus checksum mismatch: {name}")
        start, end, count = entry.get("start"), entry.get("end"), entry.get("n_chunks")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (start, end, count)):
            raise RuntimeError(f"invalid chunk range types: {name}")
        if start != cursor or end < start or end - start != count:
            raise RuntimeError(f"invalid chunk range: {name}")
        cursor = end
    source_validation = {
        "corpus_files": len(files),
        "chunks": cursor,
        "embedding_shape": embedding_shape,
    }
    source_ok = cursor == embedding_rows and cursor > 0
except Exception as exc:
    source_ok = False
    source_validation = {"error": f"{type(exc).__name__}: {exc}"}
check("legacy_source_corpus", source_ok, source_validation)

meminfo = {}
for line in Path("/proc/meminfo").read_text().splitlines():
    if ":" in line:
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            meminfo[key] = int(value.strip().split()[0]) * 1024
check("host_memory", meminfo.get("MemTotal", 0) >= 7 * 1024**3, meminfo)

systemd = subprocess.run(["systemctl", "--version"], check=False, capture_output=True, text=True, timeout=5)
systemd_first = systemd.stdout.splitlines()[0] if systemd.stdout else ""
check("systemd_239", systemd_first.startswith("systemd 239 "), systemd_first)
try:
    cgroup_type = subprocess.run(
        ["stat", "-fc", "%T", "/sys/fs/cgroup"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
except Exception:
    cgroup_type = ""
check("cgroup_v1", cgroup_type == "tmpfs", cgroup_type)

nginx = subprocess.run(["nginx", "-t"], check=False, capture_output=True, text=True, timeout=10)
check("current_nginx_syntax", nginx.returncode == 0, "nginx -t passed" if nginx.returncode == 0 else "nginx -t failed")

sessions = subprocess.run(
    ["tmux", "list-sessions", "-F", "#{session_name}"],
    check=False,
    capture_output=True,
    text=True,
    timeout=5,
).stdout.splitlines()
check(
    "known_service_sessions",
    {"nutrimaster", "nutrimaster_pi", "nutrimaster_web_preview"}.issubset(set(sessions)),
    sorted(session for session in sessions if not session.startswith("trained")),
)

report = {
    "status": "ok" if not failures else "failed",
    "release_id": RELEASE_ID,
    "expected_model": EXPECTED_MODEL,
    "checks": checks,
    "failures": failures,
    "warnings": warnings,
}
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if not failures else 1)
'''


def run_production_preflight(
    archive: Path,
    *,
    host: str,
    minimum_post_stage_free_bytes: int,
    phase: str,
) -> dict[str, Any]:
    if not _HOST_RE.fullmatch(host):
        raise RuntimeError(f"invalid SSH host alias: {host!r}")
    if phase not in _PHASES:
        raise RuntimeError(f"invalid preflight phase: {phase!r}")
    local = verify_release(archive)
    release_id = str(local["release_id"])
    if not _RELEASE_ID_RE.fullmatch(release_id):
        raise RuntimeError("verified release id is invalid")
    archive = Path(archive).resolve()
    arguments = [
        EXPECTED_MODEL,
        release_id,
        str(archive.stat().st_size),
        str(local["expanded_bytes"]),
        str(minimum_post_stage_free_bytes),
        phase,
    ]
    remote_command = " ".join(
        [
            "/root/code/nutrimaster/.venv/bin/python",
            "-",
            *(shlex.quote(value) for value in arguments),
        ]
    )
    completed = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            remote_command,
        ],
        input=_REMOTE_PREFLIGHT,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    stdout = completed.stdout.strip()
    if not stdout:
        detail = completed.stderr.strip() or f"ssh exited {completed.returncode}"
        raise RuntimeError(f"production preflight returned no report: {detail}")
    try:
        report = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("production preflight returned invalid JSON") from exc
    result = {
        "local_release": local,
        "production": report,
    }
    if completed.returncode != 0 or report.get("status") != "ok":
        raise ProductionPreflightError(result)
    return result


class ProductionPreflightError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("production release preflight failed closed")
        self.report = report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a release and run read-only production admission checks"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--phase", choices=sorted(_PHASES), default="pre-stage")
    parser.add_argument(
        "--minimum-post-stage-free-bytes",
        type=int,
        default=MIN_POST_STAGE_FREE_BYTES,
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run_production_preflight(
            args.archive,
            host=args.host,
            minimum_post_stage_free_bytes=args.minimum_post_stage_free_bytes,
            phase=args.phase,
        )
    except ProductionPreflightError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
