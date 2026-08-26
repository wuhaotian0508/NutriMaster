from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pytest


def _settings(tmp_path: Path):
    from nutrimaster.config.settings import RagSettings, Settings

    data_dir = tmp_path / "corpus"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=tmp_path / "index",
            personal_lib_dir=tmp_path / "personal",
        ),
    )


def _publish_generation(index_root: Path, marker: str):
    from nutrimaster.rag.bm25 import BM25Retriever
    from nutrimaster.rag.field_keyword import FieldKeywordRetriever
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.index_generation import (
        create_staging_generation,
        file_sha256,
        publish_generation,
        write_generation_manifest,
    )

    staging = create_staging_generation(index_root)
    chunks = [
        GeneChunk(
            gene_name=f"GENE-{marker}",
            paper_title=f"paper-{marker}",
            journal="test",
            doi=f"10.test/{marker}",
            gene_type="Pathway_Genes",
            content=f"stable retrieval content for {marker}",
            metadata={"marker": marker},
        )
    ]
    with (staging / "chunks.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
    np.save(staging / "embeddings.npy", embeddings)
    np.save(staging / "embedding_norms.npy", np.array([1.0], dtype=np.float32))
    fingerprint = file_sha256(staging / "chunks.pkl")
    bm25 = BM25Retriever(staging)
    bm25.build(chunks, corpus_fingerprint=fingerprint)
    bm25.save()
    field = FieldKeywordRetriever(staging, chunks=chunks)
    field.build(chunks, corpus_fingerprint=fingerprint)
    field.save()
    (staging / "manifest.json").write_text(
        '{"chunker_version":"test","files":{"paper.json":{}}}\n',
        encoding="utf-8",
    )
    write_generation_manifest(
        staging,
        chunk_count=1,
        embedding_shape=embeddings.shape,
        corpus_fingerprint=fingerprint,
    )
    return publish_generation(index_root, staging)


def _queue(settings, *, fail_dispatch: bool = False):
    from nutrimaster.rag.index_build_jobs import IndexBuildQueue

    def dispatch():
        if fail_dispatch:
            raise RuntimeError("systemd unavailable")

    return IndexBuildQueue.from_settings(settings, dispatcher=dispatch)


def test_queue_is_durable_and_dispatch_failure_is_not_reported_as_accepted(tmp_path: Path):
    from nutrimaster.rag.index_build_jobs import IndexBuildDispatchError

    settings = _settings(tmp_path)
    queue = _queue(settings)
    job = queue.enqueue(force=False, reason="test")

    assert (queue.pending_dir / f"{job['job_id']}.json").is_file()
    assert queue.status()["state"] == "queued"
    assert queue.status()["pending_jobs"] == 1

    failed_queue = _queue(settings, fail_dispatch=True)
    with pytest.raises(IndexBuildDispatchError, match="dispatch failed"):
        failed_queue.enqueue(force=True, reason="test-failure")
    assert failed_queue.status()["state"] == "failed"


def test_failed_dispatch_does_not_overwrite_an_active_builder_status(tmp_path: Path):
    from nutrimaster.rag.index_build_jobs import IndexBuildDispatchError

    settings = _settings(tmp_path)
    queue = _queue(settings, fail_dispatch=True)
    queue.write_status(state="building", job_ids=["0" * 32])

    with pytest.raises(IndexBuildDispatchError):
        queue.enqueue(force=False, reason="concurrent")

    assert queue.status()["stored_state"] == "building"
    assert queue.status()["pending_jobs"] == 0


def test_builder_lock_rejects_a_second_corpus_builder(tmp_path: Path):
    settings = _settings(tmp_path)
    first = _queue(settings)
    second = _queue(settings)

    with first.builder_lock():
        with pytest.raises(RuntimeError, match="already owns the build lock"):
            with second.builder_lock():
                pytest.fail("second builder must not enter")


def test_low_disk_rejects_before_build_and_keeps_current(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker
    from nutrimaster.rag.index_generation import read_current_generation_id

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    previous = _publish_generation(settings.rag.index_dir, "previous")
    old = _publish_generation(settings.rag.index_dir, "old")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    stale_snapshot = queue.work_dir / "corpus-snapshot-stale"
    stale_snapshot.mkdir(parents=True)
    (stale_snapshot / "partial.json").write_text("{}", encoding="utf-8")
    stale_generation = settings.rag.index_dir / "generations" / ".staging-stale"
    stale_generation.mkdir()
    (stale_generation / "partial").write_bytes(b"partial")
    queue.enqueue(
        force=False,
        reason="low-disk",
        active_generation_id=old.generation_id,
    )
    called = False

    def build(_snapshot: Path, _force: bool) -> str:
        nonlocal called
        called = True
        raise AssertionError("build must not run")

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=build,
        auto_activate=False,
        available_bytes=0,
    ).run_once()

    assert result["state"] == "failed"
    assert "insufficient disk space" in result["error"]
    assert called is False
    assert read_current_generation_id(settings.rag.index_dir) == old.generation_id
    assert previous.path.is_dir()
    assert not stale_snapshot.exists()
    assert not stale_generation.exists()
    assert len(result["removed_abandoned_work"]) == 2


def test_isolated_worker_uses_jina_without_runtime_index_loads(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.jina as jina_module
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker

    settings = _settings(tmp_path)
    calls = {}

    class BuilderRetriever:
        generation_id = None

        def __init__(self, *, settings, autoload):
            calls["settings"] = settings
            calls["autoload"] = autoload

        def build_index(self, **kwargs):
            calls["build"] = kwargs
            self.generation_id = "b" * 64

    monkeypatch.setattr(jina_module, "JinaRetriever", BuilderRetriever)
    worker = IndexBuildWorker(settings, auto_activate=False)

    generation_id = worker._build_with_jina(tmp_path / "snapshot", force=True)

    assert generation_id == "b" * 64
    assert calls["settings"] is settings
    assert calls["autoload"] is False
    assert calls["build"] == {
        "data_dir": tmp_path / "snapshot",
        "incremental": True,
        "force": True,
        "load_after_build": False,
        "reload_on_failure": False,
    }


def test_isolated_worker_propagates_memory_error_for_fresh_process_recovery(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker
    from nutrimaster.rag.index_generation import read_current_generation_id

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    job = queue.enqueue(
        force=False,
        reason="memory-exhaustion",
        active_generation_id=old.generation_id,
    )
    exhausted = MemoryError("simulated isolated builder pressure")

    def exhaust(_snapshot: Path, _force: bool) -> str:
        raise exhausted

    worker = IndexBuildWorker(
        settings,
        queue=queue,
        build=exhaust,
        auto_activate=False,
        available_bytes=10**12,
    )
    with pytest.raises(MemoryError) as caught:
        worker.run_once()

    assert caught.value is exhausted
    assert read_current_generation_id(settings.rag.index_dir) == old.generation_id
    assert (queue.pending_dir / f"{job['job_id']}.json").is_file()
    assert queue.status()["stored_state"] == "building"


def test_activation_failure_rolls_back_and_verifies_previous_generation(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker
    from nutrimaster.rag.index_generation import read_current_generation_id

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    queue.enqueue(
        force=False,
        reason="activation-rollback",
        active_generation_id=old.generation_id,
    )
    built = {}

    def build(_snapshot: Path, _force: bool) -> str:
        new = _publish_generation(settings.rag.index_dir, "new")
        built["new"] = new.generation_id
        return new.generation_id

    activation_attempts = []

    def activate(generation_id: str) -> None:
        activation_attempts.append(generation_id)
        if generation_id != old.generation_id:
            raise RuntimeError("new service failed health check")

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=build,
        activate=activate,
        auto_activate=True,
        available_bytes=10**12,
    ).run_once()

    assert result["state"] == "failed"
    assert result["rollback_succeeded"] is True
    assert activation_attempts == [built["new"], old.generation_id]
    assert read_current_generation_id(settings.rag.index_dir) == old.generation_id
    assert old.path.is_dir()
    assert queue.status(active_generation_id=built["new"])["state"] == "failed"


def test_exception_after_publication_restores_previous_current(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker
    from nutrimaster.rag.index_generation import read_current_generation_id

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    queue.enqueue(
        force=False,
        reason="post-publication-failure",
        active_generation_id=old.generation_id,
    )
    built = {}

    def publish_then_fail(_snapshot: Path, _force: bool) -> str:
        new = _publish_generation(settings.rag.index_dir, "new")
        built["new"] = new.generation_id
        raise RuntimeError("simulated failure after CURRENT switch")

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=publish_then_fail,
        auto_activate=False,
        available_bytes=10**12,
    ).run_once()

    assert result["state"] == "failed"
    assert result["rollback_error"] is None
    assert read_current_generation_id(settings.rag.index_dir) == old.generation_id
    assert (settings.rag.index_dir / "generations" / built["new"]).is_dir()


def test_success_is_reported_only_after_activation_health_contract(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    queue.enqueue(
        force=False,
        reason="activate",
        active_generation_id=old.generation_id,
    )
    activated = []

    def build(_snapshot: Path, _force: bool) -> str:
        return _publish_generation(settings.rag.index_dir, "new").generation_id

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=build,
        activate=activated.append,
        auto_activate=True,
        available_bytes=10**12,
    ).run_once()

    assert result["state"] == "succeeded"
    assert activated == [result["published_generation"]]
    generation_dir = settings.rag.index_dir / "generations" / result["published_generation"]
    assert generation_dir.stat().st_mode & 0o777 == 0o555
    assert (generation_dir / "chunks.pkl").stat().st_mode & 0o777 == 0o444
    assert queue.status(active_generation_id=result["published_generation"])["state"] == "succeeded"


def test_cleanup_runs_only_after_success_and_keeps_current_plus_rollback(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker, seal_published_generation

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    oldest = _publish_generation(settings.rag.index_dir, "oldest")
    seal_published_generation(settings.rag.index_dir, oldest.generation_id)
    previous = _publish_generation(settings.rag.index_dir, "previous")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    queue.enqueue(
        force=False,
        reason="retention",
        active_generation_id=previous.generation_id,
    )

    def build(_snapshot: Path, _force: bool) -> str:
        return _publish_generation(settings.rag.index_dir, "current").generation_id

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=build,
        activate=lambda _generation: None,
        auto_activate=True,
        available_bytes=10**12,
    ).run_once()

    current = settings.rag.index_dir / "generations" / result["published_generation"]
    assert result["state"] == "succeeded"
    assert current.is_dir()
    assert previous.path.is_dir()
    assert not oldest.path.exists()
    assert result["removed_generations"] == [oldest.generation_id]


def test_auto_activation_can_be_explicitly_disabled_without_fake_success(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    queue.enqueue(force=False, reason="manual", active_generation_id=old.generation_id)

    def build(_snapshot: Path, _force: bool) -> str:
        return _publish_generation(settings.rag.index_dir, "new").generation_id

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=build,
        auto_activate=False,
        available_bytes=10**12,
    ).run_once()

    assert result["state"] == "awaiting_activation"
    assert result["activation_required"] is True
    assert queue.status(active_generation_id=old.generation_id)["state"] == "awaiting_activation"
    assert queue.status(active_generation_id=result["published_generation"])["state"] == "succeeded"


def test_same_current_pointer_is_not_success_when_serving_process_is_older(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.index_build_jobs import IndexBuildWorker

    monkeypatch.setenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(256 * 1024 * 1024))
    settings = _settings(tmp_path)
    serving = _publish_generation(settings.rag.index_dir, "serving")
    published = _publish_generation(settings.rag.index_dir, "published")
    (settings.rag.data_dir / "paper.json").write_text("{}", encoding="utf-8")
    queue = _queue(settings)
    queue.enqueue(
        force=False,
        reason="still-pinned",
        active_generation_id=serving.generation_id,
    )

    result = IndexBuildWorker(
        settings,
        queue=queue,
        build=lambda _snapshot, _force: published.generation_id,
        auto_activate=False,
        available_bytes=10**12,
    ).run_once()

    assert result["state"] == "awaiting_activation"
    assert result["published_generation"] == published.generation_id
    assert serving.path.is_dir()


def test_interrupted_activation_restores_and_restarts_previous_generation(tmp_path: Path):
    from nutrimaster.rag.index_build_jobs import recover_interrupted_build
    from nutrimaster.rag.index_generation import read_current_generation_id

    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    new = _publish_generation(settings.rag.index_dir, "new")
    queue = _queue(settings)
    job = queue.enqueue(
        force=False,
        reason="interrupted",
        active_generation_id=old.generation_id,
    )
    queue.write_status(
        state="activating",
        job_ids=[job["job_id"]],
        previous_generation=old.generation_id,
        published_generation=new.generation_id,
    )
    activated = []

    result = recover_interrupted_build(
        settings,
        queue=queue,
        activate=activated.append,
        restart_service=True,
    )

    assert result["state"] == "failed"
    assert result["rollback_succeeded"] is True
    assert activated == [old.generation_id]
    assert read_current_generation_id(settings.rag.index_dir) == old.generation_id
    assert not (queue.pending_dir / f"{job['job_id']}.json").exists()
    assert (queue.failed_dir / f"{job['job_id']}.json").is_file()


@pytest.mark.parametrize(
    ("exit_status", "host_stopping", "expected_restart"),
    [
        ("9", False, True),   # systemd 239/cgroup-v1 OOM commonly appears as SIGKILL
        ("15", False, True),  # manual stop during activation must restore consistency
        ("9", True, False),   # never start unified during host shutdown
    ],
)
def test_exec_stop_recovery_distinguishes_oom_from_shutdown(
    tmp_path: Path,
    monkeypatch,
    exit_status: str,
    host_stopping: bool,
    expected_restart: bool,
):
    import nutrimaster.rag.index_build_jobs as jobs

    settings = _settings(tmp_path)
    old = _publish_generation(settings.rag.index_dir, "old")
    new = _publish_generation(settings.rag.index_dir, "new")
    queue = _queue(settings)
    job = queue.enqueue(
        force=False,
        reason="exec-stop",
        active_generation_id=old.generation_id,
    )
    queue.write_status(
        state="activating",
        job_ids=[job["job_id"]],
        previous_generation=old.generation_id,
        published_generation=new.generation_id,
    )
    monkeypatch.setenv("SERVICE_RESULT", "signal")
    monkeypatch.setenv("EXIT_STATUS", exit_status)
    monkeypatch.setattr(jobs, "system_is_stopping", lambda: host_stopping)
    activated = []

    result = jobs.recover_interrupted_build(
        settings,
        queue=queue,
        activate=activated.append,
    )

    assert activated == ([old.generation_id] if expected_restart else [])
    assert result["service_restart_skipped"] is (not expected_restart)
    assert result["pointer_restored"] is True
    assert result["rollback_succeeded"] is expected_restart


def test_systemd_commands_are_fixed_and_never_use_a_shell(monkeypatch):
    import nutrimaster.rag.index_build_jobs as jobs

    calls = []

    class Completed:
        returncode = 0
        stderr = ""
        stdout = ""

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(jobs.subprocess, "run", run)
    jobs.dispatch_systemd_builder()
    jobs.restart_unified_service()

    assert calls[0][0] == list(jobs.SYSTEMD_START_BUILDER)
    assert calls[1][0] == list(jobs.SYSTEMD_RESET_FAILED_UNIFIED)
    assert calls[2][0] == list(jobs.SYSTEMD_RESTART_UNIFIED)
    assert calls[2][1]["timeout"] == 420
    assert all("shell" not in kwargs for _args, kwargs in calls)


def test_deployment_uses_independent_and_aggregate_cgroup_v1_limits():
    root = Path(__file__).resolve().parents[2]
    unified = (root / "deploy/systemd/nutrimaster-unified.service").read_text(encoding="utf-8")
    builder = (root / "deploy/systemd/nutrimaster-index-builder.service").read_text(encoding="utf-8")
    pi = (root / "deploy/systemd/nutrimaster-pi.service").read_text(encoding="utf-8")
    slice_unit = (root / "deploy/systemd/nutrimaster.slice").read_text(encoding="utf-8")

    assert "MemoryLimit=3G" in unified
    assert "MemoryLimit=2560M" in builder
    assert "MemoryLimit=768M" in pi
    assert all("Slice=nutrimaster.slice" in unit for unit in (unified, builder, pi))
    assert "MemoryLimit=5632M" in slice_unit
    assert "TimeoutStopSec=360" in unified
    assert "TimeoutStopSec=660" in builder
    assert "ExecStopPost=" in builder
