from __future__ import annotations

import asyncio
import pickle
from pathlib import Path

import numpy as np


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


def test_rrf_fuse_combines_dense_and_bm25_ranks():
    from nutrimaster.rag.bm25 import rrf_fuse

    fused = rrf_fuse([(1, 0.9), (0, 0.8)], [(0, 3.0)])

    assert fused[0][0] == 0
    assert fused[1][0] == 1


def test_jina_hybrid_search_fuses_dense_and_bm25_and_builds_missing_index(tmp_path: Path):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.jina import JinaRetriever

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
    assert (index_dir / "bm25.pkl").exists()
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
    (index_dir / "bm25.pkl").write_bytes(b"bad pickle")

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

    assert (index_dir / "bm25.pkl").exists()
    assert retriever.index_status()["bm25_chunks"] == 1
