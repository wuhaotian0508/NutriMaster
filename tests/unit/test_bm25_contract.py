from __future__ import annotations

import asyncio
import json
import pickle
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import requests


def test_cancelled_hybrid_search_marks_background_worker_for_early_exit():
    from nutrimaster.rag.jina import JinaRetriever

    retriever = object.__new__(JinaRetriever)
    worker_started = threading.Event()
    cancellation_seen = threading.Event()

    def fake_search(*args):
        cancelled = args[-1]
        worker_started.set()
        if cancelled.wait(timeout=2):
            cancellation_seen.set()
        return []

    retriever._hybrid_search_sync = fake_search

    async def scenario():
        task = asyncio.create_task(retriever.hybrid_search("cancel me"))
        await asyncio.to_thread(worker_started.wait, 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled hybrid search did not propagate cancellation")
        assert await asyncio.to_thread(cancellation_seen.wait, 2)

    asyncio.run(scenario())


def test_query_api_retry_is_bounded_and_does_not_sleep_after_final_failure(monkeypatch):
    import nutrimaster.rag.jina as jina_module

    calls = []
    sleeps = []

    def failing_post(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.exceptions.Timeout("offline")

    monkeypatch.setattr(jina_module.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="2 次全部失败"):
        jina_module._post_with_retry(
            "https://example.invalid",
            {},
            {},
            timeout=3,
            max_retries=2,
            post=failing_post,
        )

    assert len(calls) == 2
    assert sleeps == [1]


def test_hybrid_search_keeps_sparse_retrieval_when_query_embedding_is_offline(monkeypatch):
    from nutrimaster.rag.jina import JinaRetriever

    monkeypatch.delenv("NUTRIMASTER_DISABLE_BM25", raising=False)
    monkeypatch.delenv("NUTRIMASTER_ENABLE_FIELD_KEYWORD", raising=False)
    retriever = object.__new__(JinaRetriever)
    retriever._query_semaphore = threading.BoundedSemaphore(1)
    retriever._index_lock = threading.RLock()
    retriever.embeddings = np.ones((2, 2), dtype=np.float32)
    retriever.chunks = [SimpleNamespace(gene_name="dense"), SimpleNamespace(gene_name="bm25")]
    retriever._dense_query_error = None
    retriever._dense_search_indices = lambda *args, **kwargs: (_ for _ in ()).throw(
        requests.exceptions.Timeout("Jina offline")
    )
    retriever._ensure_bm25 = lambda **kwargs: SimpleNamespace(
        search=lambda query, top_k: [(1, 3.0)]
    )

    results = retriever._hybrid_search_sync("GAME8", top_k=1, rerank_top_n=2)

    assert results[0][0].gene_name == "bm25"
    assert "Jina offline" in retriever._dense_query_error


def test_bm25_tokenizer_preserves_scientific_identifiers():
    from nutrimaster.rag.bm25 import tokenize

    tokens = tokenize("GAME8 and CYP72A208 affect 25S α-番茄碱; DOI 10.1038/s41467-000.")

    assert "game8" in tokens
    assert "cyp72a208" in tokens
    assert "25s" in tokens
    assert "α-番茄碱" in tokens
    assert "10.1038/s41467-000." in tokens


def test_bm25_retriever_build_save_load_search_and_chunk_count_guard(tmp_path: Path):
    from nutrimaster.rag.bm25 import BM25Retriever
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [
        GeneChunk(
            gene_name="GAME8",
            paper_title="Alkaloid stereochemistry",
            journal="Nature",
            doi="10.example/game8",
            gene_type="Pathway_Genes",
            content="GAME8 controls C25 stereochemistry.",
            metadata={"EC_Number": "1.14.14.1"},
        ),
        GeneChunk(
            gene_name="PSY1",
            paper_title="Carotenoid biosynthesis",
            journal="Plant Cell",
            doi="10.example/psy1",
            gene_type="Pathway_Genes",
            content="PSY1 supports lycopene accumulation.",
            metadata={},
        ),
    ]

    retriever = BM25Retriever(tmp_path)
    retriever.build(chunks)
    retriever.save()

    loaded = BM25Retriever(tmp_path)
    assert loaded.load(expected_chunks=2)
    assert loaded.search("GAME8 stereochemistry", top_k=1)[0][0] == 0

    mismatched = BM25Retriever(tmp_path)
    assert not mismatched.load(expected_chunks=3)


def test_compact_bm25_matches_rank_bm25_okapi_order(tmp_path: Path):
    from rank_bm25 import BM25Okapi

    from nutrimaster.rag.bm25 import BM25Retriever, chunk_to_bm25_text, tokenize
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [
        GeneChunk("GAME8", "Alkaloid", "Nature", "d1", "Pathway_Genes", "GAME8 controls C25 stereochemistry", {}),
        GeneChunk("PSY1", "Carotenoid", "Cell", "d2", "Pathway_Genes", "PSY1 makes lycopene", {}),
        GeneChunk("GAME8B", "Other", "Science", "d3", "Common_Genes", "GAME8 related enzyme", {}),
    ]
    query = "GAME8 stereochemistry GAME8"

    retriever = BM25Retriever(tmp_path)
    retriever.build(chunks, corpus_fingerprint="generation-a")
    retriever.save()
    actual = retriever.search(query, top_k=3)

    reference = BM25Okapi([tokenize(chunk_to_bm25_text(chunk)) for chunk in chunks])
    scores = reference.get_scores(tokenize(query))
    expected = [int(index) for index in np.argsort(scores)[::-1] if scores[index] > 0]

    assert [index for index, _score in actual] == expected
    assert np.allclose([score for _index, score in actual], [scores[index] for index in expected], rtol=1e-5)
    assert not BM25Retriever(tmp_path).load(expected_chunks=3, expected_fingerprint="generation-b")


def test_compact_bm25_fallback_does_not_clip_or_wrap_large_term_frequencies(tmp_path: Path):
    from nutrimaster.rag.bm25 import BM25Retriever
    from nutrimaster.rag.gene_index import GeneChunk

    repetitions = 70_000
    chunk = GeneChunk(
        "",
        "",
        "",
        "",
        "",
        " ".join(["rare spacer"] * repetitions),
        {},
    )
    retriever = BM25Retriever(tmp_path)
    retriever.build([chunk], corpus_fingerprint="large-tf")

    assert retriever.term_frequencies.dtype == np.uint32
    assert retriever.search("rare", top_k=1) == [(0, float(repetitions))]


def test_rrf_fuse_combines_dense_and_bm25_ranks():
    from nutrimaster.rag.bm25 import rrf_fuse

    fused = rrf_fuse([(1, 0.9), (0, 0.8)], [(0, 3.0)])

    assert fused[0][0] == 0
    assert fused[1][0] == 1


def test_jina_hybrid_search_fuses_dense_and_bm25_and_builds_missing_index(tmp_path: Path, monkeypatch):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.jina import JinaRetriever

    # This contract verifies fusion regardless of inherited developer-shell
    # flags; the production launcher explicitly keeps BM25 enabled.
    monkeypatch.delenv("NUTRIMASTER_DISABLE_BM25", raising=False)
    monkeypatch.setenv("NUTRIMASTER_SPARSE_INDEX_BUILD_ON_MISS", "1")

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    chunks = [
        GeneChunk(
            gene_name="GAME8",
            paper_title="Alkaloid stereochemistry",
            journal="Nature",
            doi="10.example/game8",
            gene_type="Pathway_Genes",
            content="GAME8 controls C25 stereochemistry.",
            metadata={},
        ),
        GeneChunk(
            gene_name="PSY1",
            paper_title="Carotenoid biosynthesis",
            journal="Plant Cell",
            doi="10.example/psy1",
            gene_type="Pathway_Genes",
            content="PSY1 supports lycopene accumulation.",
            metadata={},
        ),
    ]
    with (index_dir / "chunks.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    np.save(index_dir / "embeddings.npy", np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32))

    settings = Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=index_dir,
            personal_lib_dir=tmp_path / "personal",
        ),
    )
    retriever = JinaRetriever(settings=settings)
    retriever.get_query_embedding = lambda query: np.array([1.0, 0.0], dtype=np.float32)

    results = asyncio.run(retriever.hybrid_search("GAME8", top_k=1, rerank_top_n=2))

    assert results[0][0].gene_name == "GAME8"
    assert (index_dir / "bm25_sparse_v4.pkl").exists()
    status = retriever.index_status()
    assert status["bm25_loaded"] is True
    assert status["bm25_chunks"] == 2


def test_jina_hybrid_search_falls_back_to_dense_when_bm25_unavailable(tmp_path: Path):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.jina import JinaRetriever

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    chunks = [
        GeneChunk("GAME8", "Alkaloid", "Nature", "10.example/game8", "Pathway_Genes", "GAME8", {}),
        GeneChunk("PSY1", "Carotenoid", "Plant Cell", "10.example/psy1", "Pathway_Genes", "PSY1", {}),
    ]
    with (index_dir / "chunks.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    np.save(index_dir / "embeddings.npy", np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32))
    (index_dir / "bm25_sparse_v4.pkl").write_bytes(b"bad pickle")

    settings = Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=index_dir,
            personal_lib_dir=tmp_path / "personal",
        ),
    )
    retriever = JinaRetriever(settings=settings)
    retriever.get_query_embedding = lambda query: np.array([1.0, 0.0], dtype=np.float32)
    retriever._rebuild_bm25 = lambda: None

    results = asyncio.run(retriever.hybrid_search("GAME8", top_k=1, rerank_top_n=2))

    assert results[0][0].gene_name == "PSY1"


def test_jina_build_index_rebuilds_bm25_after_vector_index_update(tmp_path: Path, monkeypatch):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.gene_index import GeneChunk
    import nutrimaster.rag.jina as jina_module

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    (data_dir / "paper.json").write_text(
        json.dumps(
            {
                "Title": "Alkaloid stereochemistry",
                "DOI": "10.example/game8",
                "Pathway_Genes": [
                    {"Gene_Name": "GAME8", "Primary_Product": "steroidal alkaloid"}
                ],
            }
        ),
        encoding="utf-8",
    )
    chunks = [
        GeneChunk(
            "GAME8",
            "Alkaloid stereochemistry",
            "Nature",
            "10.example/game8",
            "Pathway_Genes",
            "GAME8 controls C25 stereochemistry.",
            {},
        )
    ]

    class FakeIndexService:
        def __init__(self, *, data_dir, index_dir, embed_texts):
            self.index_dir = Path(index_dir)

        def build(self, *, force=False):
            with (self.index_dir / "chunks.pkl").open("wb") as file:
                pickle.dump(chunks, file)
            np.save(self.index_dir / "embeddings.npy", np.ones((1, 2), dtype=np.float32))

    monkeypatch.setattr(jina_module, "IndexService", FakeIndexService)
    settings = Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=index_dir,
            personal_lib_dir=tmp_path / "personal",
        ),
    )
    retriever = jina_module.JinaRetriever(settings=settings)

    retriever.build_index(force=True)

    assert (retriever.index_path / "bm25_sparse_v4.pkl").exists()
    assert retriever.index_path.parent == index_dir / "generations"
    assert (index_dir / "CURRENT").read_text(encoding="utf-8").strip() == retriever.generation_id
    assert retriever.index_status()["bm25_chunks"] == 1
