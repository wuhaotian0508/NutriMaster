from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _module():
    path = ROOT / "deploy" / "migrate_production_gateway.py"
    spec = importlib.util.spec_from_file_location("migrate_production_gateway", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env(key: str, base: str, *, extra: str = "PRESERVE_ME=yes\n") -> bytes:
    return (
        f"OPENAI_API_KEY='{key}'\n"
        f"OPENAI_BASE_URL='{base}'\n"
        "MAIN_MODEL='deepseek-v4-flash'\n"
        f"{extra}"
    ).encode()


def test_gateway_handoff_is_atomic_bounded_and_preserves_other_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    source = tmp_path / "legacy.env"
    destination = tmp_path / "candidate.env"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    backup = backup_dir / "release.env.before"
    original = _env("candidate-key", "https://candidate.invalid/v1")
    source.write_bytes(_env("live-key", "https://live.example/v1", extra=""))
    destination.write_bytes(original)
    destination.chmod(0o644)
    monkeypatch.setattr(
        module,
        "probe_gateway",
        lambda gateway: {
            "ok": gateway["OPENAI_API_KEY"] == "live-key",
            "response_model_ok": True,
            "content_ok": True,
        },
    )

    result = module.migrate_gateway(source, destination, backup)

    assert result["action"] == "migrated"
    assert backup.read_bytes() == original
    assert stat_mode(destination) == 0o600
    migrated, raw = module.read_gateway(destination)
    assert migrated == {
        "OPENAI_API_KEY": "live-key",
        "OPENAI_BASE_URL": "https://live.example/v1",
        "MAIN_MODEL": "deepseek-v4-flash",
    }
    assert b"PRESERVE_ME=yes" in raw
    assert b"candidate-key" not in raw


def test_gateway_handoff_restores_destination_when_post_write_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    source = tmp_path / "legacy.env"
    destination = tmp_path / "candidate.env"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    backup = backup_dir / "release.env.before"
    original = _env("candidate-key", "https://candidate.invalid/v1")
    source.write_bytes(_env("live-key", "https://live.example/v1"))
    destination.write_bytes(original)
    probes = iter(({"ok": True}, {"ok": False}))
    monkeypatch.setattr(module, "probe_gateway", lambda gateway: next(probes))

    with pytest.raises(RuntimeError, match="destination was restored"):
        module.migrate_gateway(source, destination, backup)

    assert destination.read_bytes() == original
    assert backup.read_bytes() == original
    assert stat_mode(destination) == 0o600


def test_matching_gateway_is_not_rewritten_or_backed_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    source = tmp_path / "legacy.env"
    destination = tmp_path / "candidate.env"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    backup = backup_dir / "release.env.before"
    content = _env("live-key", "https://live.example/v1")
    source.write_bytes(content)
    destination.write_bytes(content)
    destination.chmod(0o644)
    monkeypatch.setattr(module, "probe_gateway", lambda gateway: {"ok": True})

    result = module.migrate_gateway(source, destination, backup)

    assert result["action"] == "unchanged"
    assert destination.read_bytes() == content
    assert stat_mode(destination) == 0o600
    assert not backup.exists()


def test_read_only_gateway_check_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    source = tmp_path / "legacy.env"
    destination = tmp_path / "candidate.env"
    source.write_bytes(_env("live-key", "https://live.example/v1"))
    destination.write_bytes(_env("candidate-key", "https://candidate.invalid/v1"))
    monkeypatch.setattr(module, "probe_gateway", lambda gateway: {"ok": True})

    with pytest.raises(RuntimeError, match="does not match"):
        module.check_gateway(source, destination)


def test_read_only_gateway_check_proves_exact_model_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    source = tmp_path / "legacy.env"
    destination = tmp_path / "candidate.env"
    content = _env("live-key", "https://live.example/v1")
    source.write_bytes(content)
    destination.write_bytes(content)
    destination.chmod(0o644)
    monkeypatch.setattr(
        module,
        "probe_gateway",
        lambda gateway: {"ok": True, "response_model_ok": True, "content_ok": True},
    )

    result = module.check_gateway(source, destination)

    assert result["action"] == "checked"
    assert destination.read_bytes() == content
    assert stat_mode(destination) == 0o644


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
