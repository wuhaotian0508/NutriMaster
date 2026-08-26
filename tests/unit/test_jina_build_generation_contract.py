from __future__ import annotations

import threading
from pathlib import Path

import pytest


def _bare_retriever(index_root: Path):
    from nutrimaster.rag.jina import JinaRetriever

    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever._query_semaphore = threading.BoundedSemaphore(1)
    retriever._index_lock = threading.RLock()
    retriever.index_root = index_root
    retriever.index_path = index_root
    retriever.generation_id = None
    retriever.legacy_index_layout = True
    retriever.data_dir = index_root / "corpus"
    retriever.data_dir.mkdir(parents=True)
    retriever.chunks = []
    retriever.embeddings = None
    retriever.embedding_norms = None
    retriever._bm25 = None
    retriever._field_keyword = None
    retriever.corpus_fingerprint = None
    return retriever


@pytest.mark.parametrize(
    ("incremental", "force", "expected_dense_force", "expected_copy"),
    [
        (True, False, False, True),
        (True, True, True, False),
        (False, False, True, False),
        (False, True, True, False),
    ],
)
def test_build_mode_always_refreshes_dense_and_only_incremental_reuses_snapshot(
    tmp_path: Path,
    monkeypatch,
    incremental: bool,
    force: bool,
    expected_dense_force: bool,
    expected_copy: bool,
):
    import nutrimaster.rag.build_sparse_indexes as sparse_module
    import nutrimaster.rag.jina as jina_module

    retriever = _bare_retriever(tmp_path / "index")
    calls: dict[str, object] = {"copies": 0}

    def record_copy(source, staging, **kwargs):
        calls["copies"] = int(calls["copies"]) + 1
        calls["copy_source"] = Path(source)
        calls["copy_staging"] = Path(staging)
        calls["copy_kwargs"] = kwargs

    class DenseBuilder:
        def __init__(self, *, data_dir, index_dir, embed_texts):
            calls["dense_data_dir"] = Path(data_dir)
            calls["dense_staging"] = Path(index_dir)
            calls["embed_texts"] = embed_texts

        def build(self, *, force):
            calls["dense_force"] = force

    def publish_sparse(index_root, *, source_dir, **kwargs):
        calls["sparse_root"] = Path(index_root)
        calls["sparse_source"] = Path(source_dir)
        calls["sparse_kwargs"] = kwargs
        return {
            "generation_dir": str(Path(index_root) / "generations" / ("a" * 64)),
            "generation_id": "a" * 64,
            "corpus_fingerprint": "b" * 64,
        }

    monkeypatch.setattr(jina_module, "copy_generation_files", record_copy)
    monkeypatch.setattr(jina_module, "IndexService", DenseBuilder)
    monkeypatch.setattr(sparse_module, "build_sparse_indexes", publish_sparse)

    retriever.build_index(
        incremental=incremental,
        force=force,
        load_after_build=False,
        reload_on_failure=False,
    )

    assert calls["copies"] == int(expected_copy)
    assert calls["dense_force"] is expected_dense_force
    assert calls["dense_staging"] == calls["sparse_source"]
    assert calls["dense_staging"] != retriever.index_root
    if expected_copy:
        assert calls["copy_kwargs"] == {
            "include_sparse": False,
            "include_optional": False,
            "include_incremental_manifest": True,
        }


def test_post_publish_runtime_load_failure_restores_previous_current(
    tmp_path: Path,
    monkeypatch,
):
    import nutrimaster.rag.build_sparse_indexes as sparse_module
    import nutrimaster.rag.jina as jina_module

    index_root = tmp_path / "index"
    retriever = _bare_retriever(index_root)
    previous_generation = "1" * 64
    published_generation = "2" * 64
    retriever.generation_id = previous_generation
    retriever.legacy_index_layout = False
    retriever.index_path = index_root / "generations" / previous_generation
    index_root.mkdir(parents=True, exist_ok=True)
    (index_root / "CURRENT").write_text(f"{previous_generation}\n", encoding="utf-8")

    class DenseBuilder:
        def __init__(self, **_kwargs):
            pass

        def build(self, *, force):
            assert force is True

    def publish_sparse(index_root, **_kwargs):
        (Path(index_root) / "CURRENT").write_text(
            f"{published_generation}\n",
            encoding="utf-8",
        )
        return {
            "generation_dir": str(
                Path(index_root) / "generations" / published_generation
            ),
            "generation_id": published_generation,
            "corpus_fingerprint": "3" * 64,
        }

    def restore_previous(index_root, generation_id):
        assert generation_id == previous_generation
        (Path(index_root) / "CURRENT").write_text(
            f"{generation_id}\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(jina_module, "IndexService", DenseBuilder)
    monkeypatch.setattr(sparse_module, "build_sparse_indexes", publish_sparse)
    monkeypatch.setattr(jina_module, "switch_current_generation", restore_previous)
    monkeypatch.setattr(
        retriever,
        "_load_index",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated load gate failure")),
    )

    with pytest.raises(RuntimeError, match="load gate failure"):
        retriever.build_index(
            incremental=False,
            load_after_build=True,
            reload_on_failure=False,
        )

    assert (index_root / "CURRENT").read_text(encoding="utf-8").strip() == previous_generation
    assert retriever.generation_id == previous_generation
    assert retriever.index_path == index_root / "generations" / previous_generation
