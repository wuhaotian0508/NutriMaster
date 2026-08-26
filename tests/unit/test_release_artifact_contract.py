from __future__ import annotations

import importlib.util
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _build_release_module():
    path = ROOT / "deploy" / "build_release.py"
    spec = importlib.util.spec_from_file_location("nutrimaster_build_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_source_allowlist_excludes_state_secrets_and_generated_trees():
    module = _build_release_module()

    files = module.collect_source_files()
    paths = {item.path for item in files}

    assert "src/nutrimaster/experiment/resource_limits.py" in paths
    assert "src/nutrimaster/rag/legacy_bootstrap.py" in paths
    assert "deploy/cutover-production.sh" in paths
    assert "deploy/migrate_production_gateway.py" in paths
    assert "pi-runtime/src/server.js" in paths
    assert not any("/.venv/" in f"/{path}/" for path in paths)
    assert not any("/reports/" in f"/{path}/" for path in paths)
    assert not any("/node_modules/" in f"/{path}/" for path in paths)
    assert not any("/.pi-agent/" in f"/{path}/" for path in paths)
    assert not any(path.endswith("auth.json") for path in paths)
    assert ".env" not in paths
    assert not any(path.startswith("data/") for path in paths)


def test_content_addressed_release_round_trips_and_records_dirty_sources(tmp_path: Path):
    module = _build_release_module()

    built = module.build_release(tmp_path)
    archive = Path(built["archive"])
    verified = module.verify_release(archive)

    assert verified["status"] == "ok"
    assert verified["release_id"] == built["release_id"]
    assert verified["expected_model"] == "deepseek-v4-flash"
    assert built["archive_bytes"] < built["expanded_bytes"] < 64 * 1024 * 1024
    assert Path(built["sidecar"]).read_text().endswith(f"  {archive.name}\n")
    assert (
        "src/nutrimaster/experiment/resource_limits.py"
        in verified["untracked_selected_paths"]
    )

    with tarfile.open(archive, "r:gz") as package:
        manifest_member = next(
            member for member in package.getmembers() if member.name.endswith("/RELEASE.json")
        )
        extracted = package.extractfile(manifest_member)
        assert extracted is not None
        manifest = json.load(extracted)
    assert manifest["expected_model"] == "deepseek-v4-flash"
    assert len(manifest["files"]) == verified["file_count"]


def test_release_verifier_rejects_a_changed_archive_sidecar(tmp_path: Path):
    module = _build_release_module()
    built = module.build_release(tmp_path)
    Path(built["sidecar"]).write_text(
        f"{'0' * 64}  {Path(built['archive']).name}\n",
        encoding="ascii",
    )

    with pytest.raises(RuntimeError, match="sidecar does not match"):
        module.verify_release(Path(built["archive"]))


def test_production_mutation_scripts_are_exactly_authorized_and_rollback_bounded():
    scripts = {
        name: (ROOT / "deploy" / name).read_text(encoding="utf-8")
        for name in (
            "stage-production.sh",
            "bootstrap-production.sh",
            "cutover-production.sh",
            "rollback-production.sh",
        )
    }
    for source in scripts.values():
        assert "NUTRIMASTER_PRODUCTION_CHANGE_APPROVED" in source
        assert "deepseek-v4-flash" in source
        assert "pkill" not in source
        assert "killall" not in source
        assert "rm -" not in source
        assert "tmux kill" not in source
        executable_lines = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert re.search(r"tmux[^\n]*trained", executable_lines) is None

    cutover = scripts["cutover-production.sh"]
    assert 'kill -TERM "$PID_5000" "$PID_5002" "$PID_8787"' in cutover
    assert "NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN" in cutover
    assert "rollback_on_error" in cutover
    assert "smoke_production.py" in cutover
    assert "migrate_production_gateway.py" in cutover
    assert "--check" in cutover
    assert "wait_for_health" in cutover
    assert 'validate_listener 5000 /root/Projects/NutriMaster nutrimaster' in cutover
    assert 'validate_listener 5002 /root/code/nutrimaster python' in cutover
    assert 'validate_listener 8787 /root/code/nutrimaster/pi-runtime node' in cutover
    assert "http://127.0.0.1:8787/healthz" in cutover
    assert "http://127.0.0.1:5000/api/health" in cutover
    assert cutover.index("wait_for_health") < cutover.index("smoke_production.py")
    assert cutover.index("smoke_production.py") < cutover.index(
        "systemctl enable --now nutrimaster-index-builder.path"
    )

    stage = scripts["stage-production.sh"]
    assert "migrate_production_gateway.py" in stage
    assert 'chmod 0600 "$LEGACY_ENV" "$PERSISTENT_ROOT/.env"' in stage
    assert 'mv -T "$NEXT_LINK" "$CURRENT_LINK"' in stage

    rollback = scripts["rollback-production.sh"]
    assert "nutrimaster_rollback_${RELEASE_ID}" in rollback
    assert "nutrimaster_preview_rollback_${RELEASE_ID}" in rollback
    assert "nutrimaster_pi_rollback_${RELEASE_ID}" in rollback
    assert "nutrimaster.bio" not in rollback


def test_systemd_release_paths_are_version_independent_and_bootstrap_is_bounded():
    systemd_dir = ROOT / "deploy" / "systemd"
    for path in systemd_dir.iterdir():
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if "WorkingDirectory=" in source or "DirectoryNotEmpty=" in source:
            assert "/root/code/nutrimaster-current" in source

    for name in (
        "nutrimaster-index-bootstrap.service",
        "nutrimaster-index-bootstrap-recovery.service",
    ):
        source = (systemd_dir / name).read_text(encoding="utf-8")
        assert "MemoryLimit=2560M" in source
        assert "MemoryMax=2560M" in source
        assert "Slice=nutrimaster.slice" in source
        assert "\n[Install]\n" not in source


def test_production_smoke_locks_model_and_reads_token_only_from_stdin():
    source = (ROOT / "deploy" / "smoke_production.py").read_text(encoding="utf-8")

    assert 'EXPECTED_MODEL = "deepseek-v4-flash"' in source
    assert "--token-stdin" in source
    assert "ThreadPoolExecutor(max_workers=4)" in source
    assert "Authorization" in source
    assert "api/rag/search" in source
    assert "api/query" in source
    assert "api/pi/query" in source
