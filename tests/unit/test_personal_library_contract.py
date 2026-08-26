from __future__ import annotations

import io
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def _rag_settings(tmp_path: Path, **env_overrides):
    from nutrimaster.config.settings import RagSettings

    return RagSettings.from_env(
        tmp_path,
        {
            "RAG_PERSONAL_LIB_DIR": str(tmp_path / "personal_lib"),
            **env_overrides,
        },
    )


def _chunk(name: str, content: str) -> dict:
    return {
        "source_type": "personal",
        "title": name,
        "content": content,
        "url": "",
        "score": 0.0,
        "metadata": {"filename": name, "page": 1},
    }


class _FakeUpload:
    def __init__(self, payload: bytes = b"fake-pdf"):
        self.payload = payload

    def save(self, path: str) -> None:
        Path(path).write_bytes(self.payload)


def test_personal_library_chunks_and_searches_without_legacy_imports(tmp_path: Path):
    import inspect

    from nutrimaster.rag.personal_library import PersonalLibrary
    import nutrimaster.rag.personal_library as personal_module
    from nutrimaster.config.settings import RagSettings

    assert "core.config" not in inspect.getsource(personal_module)
    assert "search.embedding_utils" not in inspect.getsource(personal_module)

    rag_settings = RagSettings.from_env(
        tmp_path,
        {
            "RAG_PERSONAL_LIB_DIR": str(tmp_path / "personal_lib"),
            "CHUNK_SIZE": "10",
            "CHUNK_OVERLAP": "2",
        },
    )
    library = PersonalLibrary(
        "user-1",
        rag_settings=rag_settings,
        embed_texts=lambda texts: np.eye(len(texts), 3, dtype=np.float32),
    )

    chunks = library._chunk_pages("paper.pdf", [(1, "abcdefghij" "klmnop")])
    assert [chunk["metadata"]["page"] for chunk in chunks] == [1, 1]
    assert chunks[0]["content"] == "abcdefghij"

    library.chunks = chunks[:2]
    library.embeddings = np.eye(2, 3, dtype=np.float32)
    results = library.search(np.array([1.0, 0.0, 0.0]), top_k=1)

    assert results[0]["content"] == "abcdefghij"
    assert results[0]["score"] > 0.99


def test_legacy_personal_library_facade_is_removed():
    root = Path(__file__).resolve().parents[2]

    from nutrimaster.rag.personal_library import PersonalLibrary

    assert PersonalLibrary is not None
    assert not (root / "rag" / "search" / "personal_lib.py").exists()


def test_saved_embeddings_and_norms_reload_as_mmaps_and_preserve_tie_order(
    tmp_path: Path,
    monkeypatch,
):
    import nutrimaster.rag.personal_library as personal_module

    monkeypatch.setenv("NUTRIMASTER_PERSONAL_DENSE_BLOCK_ROWS", "1")
    library = personal_module.PersonalLibrary(
        "mmap-user",
        rag_settings=_rag_settings(tmp_path),
        embed_texts=lambda texts: np.empty((len(texts), 2), dtype=np.float32),
    )
    library.chunks = [
        _chunk("zero.pdf", "zero"),
        _chunk("one.pdf", "one"),
        _chunk("two.pdf", "two"),
    ]
    library.embeddings = np.array(
        [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        dtype=np.float32,
    )
    library.manifest = {"zero.pdf": {}, "one.pdf": {}, "two.pdf": {}}
    library._save_index()
    library._load_index()

    assert isinstance(library.embeddings, np.memmap)
    assert isinstance(library.embedding_norms, np.memmap)
    np.testing.assert_allclose(library.embedding_norms, [1.0, 1.0, 1.0])

    original_norm = np.linalg.norm

    def reject_matrix_norm(value, *args, **kwargs):
        assert np.asarray(value).ndim != 2, "search normalized the complete matrix"
        return original_norm(value, *args, **kwargs)

    monkeypatch.setattr(personal_module.np.linalg, "norm", reject_matrix_norm)
    results = library.search(np.array([1.0, 0.0], dtype=np.float32), top_k=2)

    assert [result["title"] for result in results] == ["one.pdf", "zero.pdf"]
    assert all(result["score"] > 0.99 for result in results)


def test_stale_norms_are_rebuilt_when_embeddings_are_newer(tmp_path: Path):
    import pickle

    from nutrimaster.rag.personal_library import PersonalLibrary

    settings = _rag_settings(tmp_path)
    legacy_index = settings.personal_lib_dir / "stale-user" / "index"
    legacy_index.mkdir(parents=True)
    with (legacy_index / "chunks.pkl").open("wb") as file:
        pickle.dump([_chunk("paper.pdf", "text")], file)
    (legacy_index / "manifest.json").write_text(
        '{"paper.pdf": {"num_chunks": 1}}',
        encoding="utf-8",
    )
    embeddings_path = legacy_index / "embeddings.npy"
    norms_path = legacy_index / "embedding_norms.npy"
    np.save(embeddings_path, np.array([[3.0, 4.0]], dtype=np.float32))
    np.save(norms_path, np.array([5.0], dtype=np.float32))
    np.save(embeddings_path, np.array([[6.0, 8.0]], dtype=np.float32))
    norms_mtime = norms_path.stat().st_mtime_ns
    os.utime(embeddings_path, ns=(norms_mtime + 1, norms_mtime + 1))

    reloaded = PersonalLibrary("stale-user", rag_settings=settings)
    assert isinstance(reloaded.embedding_norms, np.memmap)
    np.testing.assert_allclose(reloaded.embedding_norms, [10.0])


def test_incomplete_legacy_snapshot_is_not_loaded(tmp_path: Path):
    from nutrimaster.rag.personal_library import PersonalLibrary

    settings = _rag_settings(tmp_path)
    legacy_index = settings.personal_lib_dir / "incomplete-user" / "index"
    legacy_index.mkdir(parents=True)
    (legacy_index / "manifest.json").write_text(
        '{"paper.pdf": {"num_chunks": 1}}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="must exist together"):
        PersonalLibrary("incomplete-user", rag_settings=settings)


def test_failed_multi_file_commit_restores_previous_index(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.personal_library as personal_module

    library = personal_module.PersonalLibrary("atomic-user", rag_settings=_rag_settings(tmp_path))
    library.chunks = [_chunk("old.pdf", "old")]
    library.embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
    library.manifest = {"old.pdf": {"num_chunks": 1}}
    library._save_index()
    targets = [
        library._active_index_dir / "chunks.pkl",
        library._active_index_dir / "embeddings.npy",
        library._active_index_dir / "embedding_norms.npy",
        library._active_index_dir / "manifest.json",
    ]
    original_bytes = {target: target.read_bytes() for target in targets}
    original_current = library._current_file.read_bytes()

    real_replace = personal_module.os.replace

    def fail_current_replace(source, target):
        if Path(target) == library._current_file:
            raise OSError("injected commit failure")
        return real_replace(source, target)

    monkeypatch.setattr(personal_module.os, "replace", fail_current_replace)
    with pytest.raises(OSError, match="injected commit failure"):
        library._save_index(
            chunks=[_chunk("new.pdf", "new")],
            manifest={"new.pdf": {"num_chunks": 1}},
            embedding_blocks=[np.array([[0.0, 1.0]], dtype=np.float32)],
            embedding_shape=(1, 2),
            embedding_dtype=np.dtype(np.float32),
        )

    assert {target: target.read_bytes() for target in targets} == original_bytes
    assert library._current_file.read_bytes() == original_current
    reloaded = personal_module.PersonalLibrary(
        "atomic-user",
        rag_settings=_rag_settings(tmp_path),
    )
    assert reloaded.manifest == {"old.pdf": {"num_chunks": 1}}
    assert [chunk["content"] for chunk in reloaded.chunks] == ["old"]
    assert not list(library.index_dir.glob(".*.tmp"))
    assert not list(library.index_dir.glob(".*.bak"))


def test_current_generation_ignores_legacy_and_incomplete_transaction_files(tmp_path: Path):
    from nutrimaster.rag.personal_library import PersonalLibrary

    settings = _rag_settings(tmp_path)
    library = PersonalLibrary("visibility-user", rag_settings=settings)
    library.chunks = [_chunk("committed.pdf", "committed")]
    library.embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
    library.manifest = {"committed.pdf": {"num_chunks": 1}}
    library._save_index()

    # Simulate both stale flat-layout files and a process killed halfway through
    # constructing another private generation. Neither is a visibility marker.
    (library.index_dir / "chunks.pkl").write_bytes(b"not-a-pickle")
    incomplete = library._generations_dir / f".{('f' * 32)}.tmp"
    incomplete.mkdir()
    (incomplete / "manifest.json").write_text('{"partial.pdf": {}}', encoding="utf-8")

    reloaded = PersonalLibrary("visibility-user", rag_settings=settings)

    assert reloaded.manifest == {"committed.pdf": {"num_chunks": 1}}
    assert [chunk["content"] for chunk in reloaded.chunks] == ["committed"]
    assert reloaded._active_generation == library._active_generation


def test_loader_falls_back_to_previous_complete_generation(tmp_path: Path):
    from nutrimaster.rag.personal_library import PersonalLibrary

    settings = _rag_settings(tmp_path)
    library = PersonalLibrary("fallback-user", rag_settings=settings)
    library.chunks = [_chunk("old.pdf", "old")]
    library.embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
    library.manifest = {"old.pdf": {"num_chunks": 1}}
    library._save_index()
    old_generation = library._active_generation

    library._save_index(
        chunks=[_chunk("new.pdf", "new")],
        manifest={"new.pdf": {"num_chunks": 1}},
        embedding_blocks=[np.array([[0.0, 1.0]], dtype=np.float32)],
        embedding_shape=(1, 2),
        embedding_dtype=np.dtype(np.float32),
    )
    assert library._active_generation != old_generation
    (library._active_index_dir / "embeddings.npy").write_bytes(b"truncated")

    reloaded = PersonalLibrary("fallback-user", rag_settings=settings)

    assert reloaded._active_generation == old_generation
    assert reloaded.manifest == {"old.pdf": {"num_chunks": 1}}
    assert [chunk["content"] for chunk in reloaded.chunks] == ["old"]


def test_generation_loader_does_not_retry_after_memory_error(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.personal_library import PersonalLibrary

    settings = _rag_settings(tmp_path)
    library = PersonalLibrary("oom-user", rag_settings=settings)
    library.chunks = [_chunk("paper.pdf", "text")]
    library.embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
    library.manifest = {"paper.pdf": {"num_chunks": 1}}
    library._save_index()
    calls = 0

    def fail_load(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise MemoryError("injected")

    monkeypatch.setattr(PersonalLibrary, "_load_snapshot", fail_load)
    with pytest.raises(MemoryError, match="injected"):
        PersonalLibrary("oom-user", rag_settings=settings)
    assert calls == 1


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("NUTRIMASTER_PERSONAL_DENSE_BLOCK_ROWS", "16385"),
        ("NUTRIMASTER_PERSONAL_EMBED_BATCH_SIZE", "129"),
        ("NUTRIMASTER_PERSONAL_MAX_CHUNKS", "100001"),
        ("NUTRIMASTER_PERSONAL_MAX_EXTRACTED_CHARS", "100000001"),
    ],
)
def test_personal_memory_settings_have_hard_upper_bounds(
    tmp_path: Path,
    monkeypatch,
    env_name: str,
    value: str,
):
    from nutrimaster.rag.personal_library import PersonalLibrary

    monkeypatch.setenv(env_name, value)
    with pytest.raises(RuntimeError, match=env_name):
        PersonalLibrary("bounded-user", rag_settings=_rag_settings(tmp_path))


@pytest.mark.parametrize(
    ("limit_env", "limit", "text", "chunk_size", "message"),
    [
        ("NUTRIMASTER_PERSONAL_MAX_EXTRACTED_CHARS", "5", "abcdef", "10", "文本过多"),
        ("NUTRIMASTER_PERSONAL_MAX_CHUNKS", "2", "abcde", "2", "分块总数"),
    ],
)
def test_upload_expansion_gates_run_before_embedding_and_clean_up_pdf(
    tmp_path: Path,
    monkeypatch,
    limit_env: str,
    limit: str,
    text: str,
    chunk_size: str,
    message: str,
):
    from nutrimaster.rag.personal_library import PersonalLibrary

    monkeypatch.setenv(limit_env, limit)
    embed_calls: list[list[str]] = []
    library = PersonalLibrary(
        "limited-user",
        rag_settings=_rag_settings(
            tmp_path,
            CHUNK_SIZE=chunk_size,
            CHUNK_OVERLAP="0",
        ),
        embed_texts=lambda texts: embed_calls.append(texts),
    )
    monkeypatch.setattr(
        library,
        "_extract_pdf_text",
        lambda path, *, max_chars=None: [(1, text)],
    )

    with pytest.raises(ValueError, match=message):
        library.upload_pdf(_FakeUpload(), "large.pdf")

    assert embed_calls == []
    assert list(library.pdf_dir.iterdir()) == []
    assert library.chunks == []
    assert library.manifest == {}


def test_upload_and_delete_immediately_remap_the_committed_index(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.rag.personal_library import PersonalLibrary

    library = PersonalLibrary(
        "write-user",
        rag_settings=_rag_settings(tmp_path, CHUNK_SIZE="10", CHUNK_OVERLAP="0"),
        embed_texts=lambda texts: np.array([[1.0, 0.0] for _ in texts], dtype=np.float32),
    )
    monkeypatch.setattr(
        library,
        "_extract_pdf_text",
        lambda path, *, max_chars=None: [(1, "content")],
    )

    metadata = library.upload_pdf(_FakeUpload(), "paper.pdf")

    assert metadata["num_chunks"] == 1
    assert isinstance(library.embeddings, np.memmap)
    assert isinstance(library.embedding_norms, np.memmap)
    assert library.delete_file("paper.pdf") is True
    assert isinstance(library.embeddings, np.memmap)
    assert isinstance(library.embedding_norms, np.memmap)
    assert library.embeddings.shape == (0, 2)
    assert library.chunks == []
    assert library.manifest == {}


def test_upload_adopts_the_single_readback_snapshot_without_reloading(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.rag.personal_library import PersonalLibrary

    library = PersonalLibrary(
        "single-readback-user",
        rag_settings=_rag_settings(tmp_path, CHUNK_SIZE="10", CHUNK_OVERLAP="0"),
        embed_texts=lambda texts: np.array(
            [[1.0, 0.0] for _ in texts],
            dtype=np.float32,
        ),
    )
    monkeypatch.setattr(
        library,
        "_extract_pdf_text",
        lambda path, *, max_chars=None: [(1, "content")],
    )
    real_load_snapshot = library._load_snapshot
    snapshot_calls: list[Path] = []

    def counted_load_snapshot(path, **kwargs):
        snapshot_calls.append(Path(path))
        return real_load_snapshot(path, **kwargs)

    monkeypatch.setattr(library, "_load_snapshot", counted_load_snapshot)
    monkeypatch.setattr(
        library,
        "_load_index",
        lambda: pytest.fail("committed generation must not be loaded a second time"),
    )

    library.upload_pdf(_FakeUpload(), "paper.pdf")

    assert len(snapshot_calls) == 1
    assert snapshot_calls[0] == library._active_index_dir
    assert isinstance(library.embeddings, np.memmap)
    assert isinstance(library.embedding_norms, np.memmap)
    assert library.manifest["paper.pdf"]["num_chunks"] == 1


def test_generation_readback_memory_error_keeps_previous_pointer_and_live_state(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.rag.personal_library import PersonalLibrary

    library = PersonalLibrary("readback-oom-user", rag_settings=_rag_settings(tmp_path))
    library.chunks = [_chunk("old.pdf", "old")]
    library.embeddings = np.array([[1.0, 0.0]], dtype=np.float32)
    library.manifest = {"old.pdf": {"num_chunks": 1}}
    library._save_index()
    previous_current = library._current_file.read_bytes()
    previous_generation = library._active_generation
    previous_chunks = library.chunks
    error = MemoryError("injected generation readback OOM")

    monkeypatch.setattr(
        library,
        "_load_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    with pytest.raises(MemoryError) as raised:
        library._save_index(
            chunks=[_chunk("new.pdf", "new")],
            manifest={"new.pdf": {"num_chunks": 1}},
            embedding_blocks=[np.array([[0.0, 1.0]], dtype=np.float32)],
            embedding_shape=(1, 2),
            embedding_dtype=np.dtype(np.float32),
        )

    assert raised.value is error
    assert library._current_file.read_bytes() == previous_current
    assert library._active_generation == previous_generation
    assert library.chunks is previous_chunks
    assert not [
        path
        for path in library._generations_dir.iterdir()
        if path.name != previous_generation
    ]


def test_pdf_extraction_preserves_memory_error_identity(tmp_path: Path, monkeypatch):
    from nutrimaster.rag.personal_library import PersonalLibrary

    error = MemoryError("injected PDF extraction OOM")

    class Page:
        def get_text(self):
            raise error

    class Document:
        def __iter__(self):
            return iter([Page()])

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda _path: Document()))

    with pytest.raises(MemoryError) as raised:
        PersonalLibrary._extract_pdf_text(tmp_path / "paper.pdf")
    assert raised.value is error


def test_default_embedding_calls_are_bounded_and_incrementally_collected(
    tmp_path: Path,
    monkeypatch,
):
    import nutrimaster.rag.personal_library as personal_module

    library = personal_module.PersonalLibrary("batch-user", rag_settings=_rag_settings(tmp_path))
    monkeypatch.setattr(
        personal_module.Settings,
        "from_env",
        classmethod(lambda cls: SimpleNamespace(jina_api_key="test-key")),
    )
    request_sizes: list[int] = []

    class FakeResponse:
        def __init__(self, batch: list[str]):
            self.batch = batch

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {"embedding": [float(value), 1.0]}
                    for value in self.batch
                ]
            }

    def fake_post(url, *, json, **kwargs):
        batch = json["input"]
        request_sizes.append(len(batch))
        return FakeResponse(batch)

    monkeypatch.setattr(personal_module.requests, "post", fake_post)
    result = library._default_embed_texts([str(index) for index in range(70)])

    assert request_sizes == [32, 32, 6]
    assert result.shape == (70, 2)
    assert result.dtype == np.float32
    np.testing.assert_array_equal(result[69], [69.0, 1.0])


def test_list_files_uses_the_instance_lock(tmp_path: Path):
    from nutrimaster.rag.personal_library import PersonalLibrary

    library = PersonalLibrary("locked-user", rag_settings=_rag_settings(tmp_path))
    library.manifest = {"paper.pdf": {"num_chunks": 1}}
    started = threading.Event()
    finished = threading.Event()

    def list_in_worker() -> None:
        started.set()
        library.list_files()
        finished.set()

    with library._lock:
        worker = threading.Thread(target=list_in_worker)
        worker.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.05)
    worker.join(timeout=1)

    assert finished.is_set()


def test_uploads_use_the_process_wide_semaphore(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.personal_library as personal_module

    library = personal_module.PersonalLibrary(
        "semaphore-user",
        rag_settings=_rag_settings(tmp_path),
        embed_texts=lambda texts: np.ones((len(texts), 2), dtype=np.float32),
    )
    monkeypatch.setattr(
        library,
        "_extract_pdf_text",
        lambda path, *, max_chars=None: [(1, "content")],
    )
    save_called = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    class SignallingUpload(_FakeUpload):
        def save(self, path: str) -> None:
            save_called.set()
            super().save(path)

    def upload_in_worker() -> None:
        try:
            library.upload_pdf(SignallingUpload(), "paper.pdf")
        except BaseException as exc:  # Report worker failures in the main test thread.
            errors.append(exc)
        finally:
            finished.set()

    assert personal_module._GLOBAL_UPLOAD_SEMAPHORE.acquire(timeout=1)
    try:
        worker = threading.Thread(target=upload_in_worker)
        worker.start()
        assert not save_called.wait(timeout=0.05)
    finally:
        personal_module._GLOBAL_UPLOAD_SEMAPHORE.release()
    worker.join(timeout=2)

    assert finished.is_set()
    assert errors == []
    assert save_called.is_set()


def test_personal_library_cache_defaults_and_configurable_bounds(monkeypatch):
    from nutrimaster.web.deps import _personal_library_cache_from_env

    monkeypatch.delenv("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE", raising=False)
    monkeypatch.delenv("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS", raising=False)
    defaults = _personal_library_cache_from_env()
    assert defaults.maxsize == 16
    assert defaults.ttl == 900

    monkeypatch.setenv("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE", "4")
    monkeypatch.setenv("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS", "120")
    configured = _personal_library_cache_from_env()
    assert configured.maxsize == 4
    assert configured.ttl == 120


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE", "0"),
        ("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE", "65"),
        ("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS", "0"),
        ("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS", "86401"),
        ("NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS", "invalid"),
    ],
)
def test_personal_library_cache_invalid_config_fails_fast(
    monkeypatch,
    env_name: str,
    value: str,
):
    import nutrimaster.web.deps as deps_module

    monkeypatch.setenv(env_name, value)
    monkeypatch.setattr(
        deps_module.Settings,
        "from_env",
        classmethod(lambda cls: pytest.fail("Settings.from_env must not run first")),
    )
    with pytest.raises(RuntimeError, match=env_name):
        deps_module.create_services()


def test_file_storage_adapter_enforces_streamed_byte_limit_and_cleans_partial_file(
    tmp_path: Path,
):
    from nutrimaster.web.routes.library import FileStorageAdapter

    oversized_target = tmp_path / "oversized.pdf"
    upload = SimpleNamespace(file=io.BytesIO(b"x" * 11))
    with pytest.raises(ValueError, match="文件过大"):
        FileStorageAdapter(upload, max_bytes=10).save(oversized_target)
    assert not oversized_target.exists()

    exact_target = tmp_path / "exact.pdf"
    exact_upload = SimpleNamespace(file=io.BytesIO(b"x" * 10))
    FileStorageAdapter(exact_upload, max_bytes=10).save(exact_target)
    assert exact_target.read_bytes() == b"x" * 10
