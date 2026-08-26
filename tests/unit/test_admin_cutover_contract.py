from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_admin_never_constructs_or_builds_a_corpus_sized_retriever():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "nutrimaster" / "web" / "admin" / "app.py").read_text(encoding="utf-8")

    assert not (root / "admin").exists()
    assert "_sys.path.insert" not in source
    assert "from retriever import JinaRetriever" not in source
    assert "JinaRetriever" not in source
    assert ".build_index(" not in source
    assert "IndexBuildQueue.from_settings" in source


def test_admin_pipeline_queues_the_isolated_builder_and_does_not_claim_completion():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "nutrimaster" / "web" / "admin" / "app.py").read_text(encoding="utf-8")

    assert "def configure_index_refresh(" in source
    assert "build_job = _refresh_index(DATA_DIR, force=False)" in source
    assert 'eq.put(("index_queued"' in source
    assert 'eq.put(("index_rebuilt"' not in source
    assert "retriever.build_index(data_dir=DATA_DIR, force=True)" not in source


def test_fastapi_web_fails_closed_if_legacy_inline_build_flag_is_enabled():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "nutrimaster" / "web" / "deps.py").read_text(encoding="utf-8")

    assert "NUTRIMASTER_WEB_BUILD_INDEX is forbidden" in source
    assert "retriever.build_index(" not in source


def test_inline_build_flag_fails_before_retriever_is_constructed(tmp_path, monkeypatch):
    from nutrimaster.config.settings import RagSettings, Settings
    import nutrimaster.web.deps as deps

    settings = Settings(
        project_root=tmp_path,
        rag=RagSettings(
            data_dir=tmp_path / "corpus",
            index_dir=tmp_path / "index",
            personal_lib_dir=tmp_path / "personal",
        ),
    )
    monkeypatch.setenv("NUTRIMASTER_WEB_BUILD_INDEX", "1")
    monkeypatch.setattr(
        deps,
        "JinaRetriever",
        lambda **_kwargs: pytest.fail("retriever must not be constructed"),
    )

    with pytest.raises(RuntimeError, match="is forbidden"):
        deps.create_services(settings)


def test_builder_unit_has_no_ordering_dependency_on_unified_service():
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy" / "systemd" / "nutrimaster-index-builder.service").read_text(
        encoding="utf-8"
    )

    assert "After=network-online.target\n" in unit
    assert "After=network-online.target nutrimaster-unified.service" not in unit


def test_pipeline_worker_environment_limits_are_bounded(monkeypatch):
    from nutrimaster.web.admin.app import _pipeline_worker_env

    monkeypatch.setenv("TEST_PIPELINE_WORKERS", "8")
    assert _pipeline_worker_env("TEST_PIPELINE_WORKERS", 4, maximum=8) == 8
    monkeypatch.setenv("TEST_PIPELINE_WORKERS", "65")
    with pytest.raises(RuntimeError, match="between 1 and 64"):
        _pipeline_worker_env("TEST_PIPELINE_WORKERS", 4, maximum=64)
    monkeypatch.setenv("TEST_PIPELINE_WORKERS", "invalid")
    with pytest.raises(RuntimeError, match="must be an integer"):
        _pipeline_worker_env("TEST_PIPELINE_WORKERS", 4, maximum=64)


def test_admin_pipeline_is_strictly_serial_and_does_not_retain_all_reports():
    from nutrimaster.web.admin import app as admin_app

    assert admin_app.PIPELINE_DEFAULTS["max_workers"] == 1
    assert admin_app.PIPELINE_LIMITS["max_workers"] == (1, 1)
    source = Path(admin_app.__file__).read_text(encoding="utf-8")
    assert "retain_reports=False" in source


def test_admin_preview_and_experiments_share_the_same_execution_gate():
    from flask import Flask
    from nutrimaster.experiment.service import ExperimentExecutionGate
    from nutrimaster.web.admin import app as admin_app

    gate = ExperimentExecutionGate()
    admin_app.configure_pipeline_execution_gate(gate)
    gate.try_acquire()  # Simulate an in-flight online experiment.
    flask_app = Flask(__name__)
    try:
        with flask_app.test_request_context("/api/pipeline/preview", method="POST"):
            response, status = admin_app.api_pipeline_preview.__wrapped__()
        assert status == 409
        assert response.get_json()["error"] == "Pipeline 正在运行中"
    finally:
        gate.release()
        admin_app.configure_pipeline_execution_gate(None)


def test_unified_app_injects_experiment_gate_into_admin():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src" / "nutrimaster" / "web" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "configure_pipeline_execution_gate" in source
    assert "services.experiment_service.execution_gate" in source


def test_admin_batch_mode_can_release_each_full_report(monkeypatch, tmp_path):
    from nutrimaster.extraction import pipeline

    monkeypatch.setattr(
        pipeline,
        "process_one_paper",
        lambda *_args, **_kwargs: {
            "status": "processed",
            "report": {"summary": {"total_fields": 1}},
        },
    )
    result = pipeline.run_pipeline_batch(
        ["first.md", "second.md"],
        input_dir=tmp_path,
        workers=1,
        tracker=SimpleNamespace(),
        retain_reports=False,
    )
    assert result["done"] == 2
    assert result["all_reports"] == []


def test_extraction_markdown_reader_has_a_hard_byte_limit(tmp_path, monkeypatch):
    from nutrimaster.extraction import config

    monkeypatch.setattr(config, "MAX_MARKDOWN_BYTES", 8)
    accepted = tmp_path / "accepted.md"
    accepted.write_bytes(b"12345678")
    assert config.read_markdown_bounded(accepted) == "12345678"

    rejected = tmp_path / "rejected.md"
    rejected.write_bytes(b"123456789")
    with pytest.raises(ValueError, match="exceeds the 8-byte"):
        config.read_markdown_bounded(rejected)


def test_verified_json_upload_has_a_stricter_bounded_limit(monkeypatch):
    from nutrimaster.web.routes.admin import _admin_json_upload_limit

    monkeypatch.delenv("NUTRIMASTER_ADMIN_JSON_MAX_BYTES", raising=False)
    assert _admin_json_upload_limit() == 16 * 1024 * 1024
    monkeypatch.setenv("NUTRIMASTER_ADMIN_JSON_MAX_BYTES", str(50 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match="50 MiB"):
        _admin_json_upload_limit()


def _admin_upload_services(tmp_path: Path, *, dispatch_error: Exception | None = None):
    from nutrimaster.config.settings import RagSettings, Settings

    data_dir = tmp_path / "corpus"
    data_dir.mkdir()

    class Services:
        settings = Settings(
            project_root=tmp_path,
            rag=RagSettings(
                data_dir=data_dir,
                index_dir=tmp_path / "index",
                personal_lib_dir=tmp_path / "personal",
            ),
        )
        retriever = SimpleNamespace(chunks=[])

        @staticmethod
        def request_index_build(**_kwargs):
            if dispatch_error is not None:
                raise dispatch_error
            return {"job_id": "a" * 32}

    return Services(), data_dir


def test_verified_json_upload_returns_202_queued_not_fake_success(tmp_path, monkeypatch):
    from fastapi import UploadFile
    import nutrimaster.web.routes.admin as admin

    monkeypatch.setattr(admin, "ADMIN_EMAILS", {"admin@example.test"})
    services, data_dir = _admin_upload_services(tmp_path)
    upload = UploadFile(
        filename="paper_nutri_plant_verified.json",
        file=io.BytesIO(b'{"Title":"test"}'),
    )

    response = asyncio.run(
        admin.admin_upload_data(
            file=upload,
            user=SimpleNamespace(email="admin@example.test"),
            services=services,
        )
    )
    payload = json.loads(response.body)

    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["job_id"] == "a" * 32
    assert (data_dir / "paper_nutri_plant_verified.json").is_file()


def test_verified_json_upload_dispatch_failure_is_explicit_503(tmp_path, monkeypatch):
    from fastapi import HTTPException, UploadFile
    import nutrimaster.web.routes.admin as admin

    monkeypatch.setattr(admin, "ADMIN_EMAILS", {"admin@example.test"})
    services, data_dir = _admin_upload_services(
        tmp_path,
        dispatch_error=RuntimeError("builder unavailable"),
    )
    upload = UploadFile(
        filename="paper_nutri_plant_verified.json",
        file=io.BytesIO(b'{"Title":"test"}'),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            admin.admin_upload_data(
                file=upload,
                user=SimpleNamespace(email="admin@example.test"),
                services=services,
            )
        )

    assert raised.value.status_code == 503
    assert "文件已保存" in raised.value.detail
    assert (data_dir / "paper_nutri_plant_verified.json").is_file()


def test_verified_json_upload_limit_reads_only_limit_plus_one(tmp_path, monkeypatch):
    from fastapi import HTTPException, UploadFile
    import nutrimaster.web.routes.admin as admin

    monkeypatch.setattr(admin, "ADMIN_EMAILS", {"admin@example.test"})
    monkeypatch.setenv("NUTRIMASTER_ADMIN_JSON_MAX_BYTES", "8")
    services, _data_dir = _admin_upload_services(tmp_path)
    upload = UploadFile(
        filename="paper_nutri_plant_verified.json",
        file=io.BytesIO(b'"12345678"'),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            admin.admin_upload_data(
                file=upload,
                user=SimpleNamespace(email="admin@example.test"),
                services=services,
            )
        )

    assert raised.value.status_code == 413
