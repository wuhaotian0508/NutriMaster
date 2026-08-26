from __future__ import annotations

import json
import pickle
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest


def _write_artifacts(index_dir: Path) -> None:
    from nutrimaster.rag.index_generation import INDEX_ARTIFACT_FILENAMES

    index_dir.mkdir()
    for index, filename in enumerate(INDEX_ARTIFACT_FILENAMES.values(), start=1):
        (index_dir / filename).write_bytes(bytes([index]) * index)


def _write_complete_generation(staging_dir: Path, marker: str, *, graph: bool = False) -> str:
    from nutrimaster.rag.bm25 import BM25Retriever
    from nutrimaster.rag.field_keyword import FieldKeywordRetriever
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.index_generation import file_sha256, write_generation_manifest

    staging_dir.mkdir(parents=True, exist_ok=True)
    chunks = [
        GeneChunk(
            gene_name=f"GENE-{marker}-{index}",
            paper_title=f"paper {marker}",
            journal="test",
            doi=f"10.test/{marker}/{index}",
            gene_type="Pathway_Genes",
            content=f"{marker} retrieval generation canonical content {index}",
            metadata={"Generation": marker},
        )
        for index in range(2)
    ]
    chunks_path = staging_dir / "chunks.pkl"
    with chunks_path.open("wb") as file:
        pickle.dump(chunks, file)
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    np.save(staging_dir / "embeddings.npy", embeddings)
    np.save(staging_dir / "embedding_norms.npy", np.linalg.norm(embeddings, axis=1))
    fingerprint = file_sha256(chunks_path)

    bm25 = BM25Retriever(staging_dir)
    bm25.build(chunks, corpus_fingerprint=fingerprint)
    bm25.save()
    field = FieldKeywordRetriever(staging_dir, chunks=chunks)
    field.build(chunks, corpus_fingerprint=fingerprint)
    field.save()
    if graph:
        from nutrimaster.rag.graph.index import GRAPH_INDEX_VERSION

        with sqlite3.connect(staging_dir / "graph_index.sqlite") as connection:
            connection.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE nodes(id TEXT PRIMARY KEY);
                CREATE TABLE edges(id TEXT PRIMARY KEY);
                INSERT INTO nodes VALUES ('node');
                INSERT INTO edges VALUES ('edge');
                """
            )
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (("version", GRAPH_INDEX_VERSION), ("corpus_fingerprint", fingerprint)),
            )

    write_generation_manifest(
        staging_dir,
        chunk_count=len(chunks),
        embedding_shape=embeddings.shape,
        corpus_fingerprint=fingerprint,
    )
    return fingerprint


def _stage(root: Path, marker: str, *, graph: bool = False) -> Path:
    from nutrimaster.rag.index_generation import create_staging_generation

    staging = create_staging_generation(root)
    _write_complete_generation(staging, marker, graph=graph)
    return staging


def _publish(root: Path, marker: str, *, graph: bool = False):
    from nutrimaster.rag.index_generation import publish_generation

    return publish_generation(root, _stage(root, marker, graph=graph))


def test_generation_manifest_binds_every_retrieval_artifact(tmp_path: Path):
    from nutrimaster.rag.index_generation import (
        file_sha256,
        validate_generation_manifest,
        write_generation_manifest,
    )

    index_dir = tmp_path / "index"
    _write_artifacts(index_dir)
    corpus_fingerprint = file_sha256(index_dir / "chunks.pkl")
    manifest_path = write_generation_manifest(
        index_dir,
        chunk_count=2,
        embedding_shape=(2, 3),
        corpus_fingerprint=corpus_fingerprint,
    )

    payload = validate_generation_manifest(
        index_dir,
        expected_chunks=2,
        expected_embedding_shape=(2, 3),
        expected_corpus_fingerprint=corpus_fingerprint,
    )
    assert manifest_path.is_file()
    assert payload["artifacts"]["embedding_norms"]["filename"] == "embedding_norms.npy"

    (index_dir / "embedding_norms.npy").write_bytes(b"stale-norms")
    with pytest.raises(RuntimeError, match="embedding_norms.npy"):
        validate_generation_manifest(
            index_dir,
            expected_chunks=2,
            expected_embedding_shape=(2, 3),
            expected_corpus_fingerprint=corpus_fingerprint,
        )


def test_generation_manifest_is_published_only_after_all_artifacts_exist(tmp_path: Path):
    from nutrimaster.rag.index_generation import (
        file_sha256,
        generation_manifest_path,
        write_generation_manifest,
    )

    index_dir = tmp_path / "index"
    _write_artifacts(index_dir)
    (index_dir / "field_keyword_v3.sqlite3").unlink()

    with pytest.raises(FileNotFoundError, match="field_keyword"):
        write_generation_manifest(
            index_dir,
            chunk_count=1,
            embedding_shape=(1, 2),
            corpus_fingerprint=file_sha256(index_dir / "chunks.pkl"),
        )
    assert not generation_manifest_path(index_dir).exists()


def test_complete_generation_appears_only_after_atomic_current_switch(tmp_path: Path):
    from nutrimaster.rag.index_generation import (
        current_generation_path,
        publish_generation,
        resolve_active_generation,
    )

    root = tmp_path / "index"
    staging = _stage(root, "a")
    assert not current_generation_path(root).exists()

    published = publish_generation(root, staging)

    assert current_generation_path(root).read_text(encoding="utf-8").strip() == published.generation_id
    assert published.path.parent == root / "generations"
    assert not staging.exists()
    assert resolve_active_generation(root).path == published.path


def test_failed_generation_validation_keeps_previous_current(tmp_path: Path):
    from nutrimaster.rag.index_generation import publish_generation, read_current_generation_id

    root = tmp_path / "index"
    first = _publish(root, "a")
    broken = _stage(root, "b")
    (broken / "embedding_norms.npy").write_bytes(b"broken after manifest")

    with pytest.raises(RuntimeError, match="embedding_norms.npy"):
        publish_generation(root, broken)

    assert read_current_generation_id(root) == first.generation_id
    assert first.path.is_dir()


def test_resolved_generation_and_jina_process_remain_pinned_after_new_publish(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.index_generation import resolve_active_generation
    from nutrimaster.rag.jina import JinaRetriever

    monkeypatch.delenv("NUTRIMASTER_REQUIRE_INDEX_GENERATION", raising=False)
    monkeypatch.delenv("NUTRIMASTER_REQUIRE_SPARSE_INDEXES", raising=False)
    root = tmp_path / "index"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    first = _publish(root, "a")
    pinned_resolution = resolve_active_generation(root)
    settings = Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=root,
            personal_lib_dir=tmp_path / "personal",
        ),
    )
    old_process = JinaRetriever(settings=settings)

    second = _publish(root, "b")
    new_resolution = resolve_active_generation(root)
    new_process = JinaRetriever(settings=settings)

    assert pinned_resolution.path == first.path
    assert old_process.index_path == first.path
    assert old_process.chunks[0].metadata["Generation"] == "a"
    assert new_resolution.path == second.path
    assert new_process.index_path == second.path
    assert new_process.chunks[0].metadata["Generation"] == "b"


def test_published_generation_cannot_be_rewritten_through_manifest_api(tmp_path: Path):
    from nutrimaster.rag.index_generation import write_generation_manifest

    root = tmp_path / "index"
    published = _publish(root, "a")
    manifest_before = (published.path / "retrieval_generation_v1.json").read_bytes()

    with pytest.raises(RuntimeError, match="immutable"):
        write_generation_manifest(
            published.path,
            chunk_count=2,
            embedding_shape=(2, 2),
        )
    assert (published.path / "retrieval_generation_v1.json").read_bytes() == manifest_before


def test_concurrent_publishers_leave_current_pointing_to_one_complete_generation(tmp_path: Path):
    from nutrimaster.rag.index_generation import publish_generation, resolve_active_generation

    root = tmp_path / "index"
    stages = [_stage(root, "a"), _stage(root, "b")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        published = list(executor.map(lambda path: publish_generation(root, path), stages))

    active = resolve_active_generation(root)
    assert active.generation_id in {item.generation_id for item in published}
    assert active.path in {item.path for item in published}


@pytest.mark.parametrize("pointer", [b"", b"../escape\n", b"a/b\n", b" generation\n", b"a\nb\n"])
def test_current_rejects_empty_malformed_and_path_traversal_ids(tmp_path: Path, pointer: bytes):
    from nutrimaster.rag.index_generation import resolve_active_generation

    root = tmp_path / "index"
    root.mkdir()
    (root / "CURRENT").write_bytes(pointer)

    with pytest.raises(RuntimeError, match="CURRENT|generation pointer"):
        resolve_active_generation(root)


@pytest.mark.parametrize("damage", ["manifest", "missing", "checksum"])
def test_resolver_rejects_damaged_generation_without_legacy_fallback(
    tmp_path: Path,
    damage: str,
):
    from nutrimaster.rag.index_generation import resolve_active_generation

    root = tmp_path / "index"
    published = _publish(root, "a")
    if damage == "manifest":
        (published.path / "retrieval_generation_v1.json").write_text("{", encoding="utf-8")
    elif damage == "missing":
        (published.path / "bm25_sparse_v4.pkl").unlink()
    else:
        (published.path / "embedding_norms.npy").write_bytes(b"changed")

    with pytest.raises(RuntimeError):
        resolve_active_generation(root)


def test_production_requires_current_while_development_can_use_flat_layout(tmp_path: Path):
    from nutrimaster.rag.index_generation import resolve_active_generation

    root = tmp_path / "index"
    root.mkdir()
    (root / "chunks.pkl").write_bytes(b"development-only")

    development = resolve_active_generation(root, require_generation=False)
    assert development.legacy is True
    assert development.path == root
    with pytest.raises(RuntimeError, match="required.*CURRENT"):
        resolve_active_generation(root, require_generation=True)


def test_same_chunk_count_with_different_content_gets_distinct_generation_ids(tmp_path: Path):
    root = tmp_path / "index"
    first = _publish(root, "a")
    second = _publish(root, "b")

    assert first.generation_id != second.generation_id
    assert first.path.is_dir()
    assert second.path.is_dir()


@pytest.mark.parametrize("artifact", ["norms", "bm25", "field"])
def test_full_validation_rejects_shape_or_fingerprint_mismatch_even_with_fresh_hashes(
    tmp_path: Path,
    artifact: str,
):
    from nutrimaster.rag.index_generation import publish_generation, write_generation_manifest

    root = tmp_path / "index"
    staging = _stage(root, "mismatch")
    if artifact == "norms":
        np.save(staging / "embedding_norms.npy", np.ones(1, dtype=np.float32))
    elif artifact == "bm25":
        with (staging / "bm25_sparse_v4.pkl").open("rb") as file:
            payload = pickle.load(file)
        payload["corpus_fingerprint"] = "0" * 64
        with (staging / "bm25_sparse_v4.pkl").open("wb") as file:
            pickle.dump(payload, file)
    else:
        with sqlite3.connect(staging / "field_keyword_v3.sqlite3") as connection:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'corpus_fingerprint'",
                ("0" * 64,),
            )
    write_generation_manifest(staging, chunk_count=2, embedding_shape=(2, 2))

    with pytest.raises(RuntimeError, match="norm|BM25|field-keyword"):
        publish_generation(root, staging)


def test_fault_before_current_switch_preserves_old_generation(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.index_generation as generation_module

    root = tmp_path / "index"
    first = _publish(root, "a")
    staging = _stage(root, "b")

    def injected_failure(_root, _generation_id):
        raise OSError("injected failure before CURRENT switch")

    monkeypatch.setattr(generation_module, "_atomic_write_current", injected_failure)
    with pytest.raises(OSError, match="injected"):
        generation_module.publish_generation(root, staging)

    assert generation_module.read_current_generation_id(root) == first.generation_id
    assert first.path.is_dir()


def test_graph_is_bound_to_generation_and_default_builder_does_not_copy_rebuilt_sparse(
    tmp_path: Path,
    monkeypatch,
):
    import nutrimaster.rag.build_sparse_indexes as build_module
    from nutrimaster.rag.graph.index import GRAPH_INDEX_VERSION
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.index_generation import (
        file_sha256,
        resolve_active_generation,
        validate_generation_manifest,
    )

    root = tmp_path / "index"
    root.mkdir()
    chunks = [GeneChunk("G", "P", "J", "D", "T", "graph generation source", {})]
    with (root / "chunks.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    np.save(root / "embeddings.npy", np.ones((1, 2), dtype=np.float32))
    fingerprint = file_sha256(root / "chunks.pkl")
    with sqlite3.connect(root / "graph_index.sqlite") as connection:
        connection.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE nodes(id INTEGER PRIMARY KEY);
            CREATE TABLE edges(id INTEGER PRIMARY KEY);
            INSERT INTO nodes VALUES (1);
            INSERT INTO edges VALUES (1);
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (("version", GRAPH_INDEX_VERSION), ("corpus_fingerprint", fingerprint)),
        )

    copy_calls = []
    original_copy = build_module.copy_generation_files

    def recording_copy(*args, **kwargs):
        copy_calls.append(kwargs.copy())
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(build_module, "copy_generation_files", recording_copy)
    result = build_module.build_sparse_indexes(root)
    generation_dir = Path(result["generation_dir"])
    manifest = validate_generation_manifest(generation_dir)

    assert copy_calls[0]["include_sparse"] is False
    assert copy_calls[0]["include_optional"] is True
    assert (generation_dir / "graph_index.sqlite").is_file()
    assert result["artifacts"]["graph"].endswith("graph_index.sqlite")
    assert manifest["artifacts"]["graph"]["filename"] == "graph_index.sqlite"

    with sqlite3.connect(generation_dir / "graph_index.sqlite") as connection:
        connection.execute("INSERT INTO nodes DEFAULT VALUES")
    with pytest.raises(RuntimeError, match="graph_index.sqlite"):
        resolve_active_generation(root)


@pytest.mark.parametrize("value, message", [("0", "positive integer"), ("65537", "at most 65536"), ("bad", "positive integer")])
def test_offline_norm_block_rows_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch,
    value: str,
    message: str,
):
    from nutrimaster.rag.build_sparse_indexes import _build_embedding_norms

    monkeypatch.setenv("NUTRIMASTER_DENSE_NORM_BLOCK_ROWS", value)
    with pytest.raises(RuntimeError, match=message):
        _build_embedding_norms(tmp_path, np.ones((2, 2), dtype=np.float32))
