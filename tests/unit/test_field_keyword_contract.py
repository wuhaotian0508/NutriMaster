from __future__ import annotations

import asyncio
import pickle
from pathlib import Path

import numpy as np


def test_field_keyword_scores_schema_fields_occurrence_decay_and_cohit(tmp_path: Path):
    from nutrimaster.rag.field_keyword import FieldKeywordRetriever
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [
        GeneChunk(
            gene_name="TrCYP72A1633",
            paper_title="White clover glycyrrhetinic acid pathway",
            journal="Plant Biotechnology",
            doi="10.example/ga",
            gene_type="Pathway_Genes",
            chunk_type="pathway_gene",
            content="GA appears in white clover. GA accumulation is low.",
            metadata={
                "Gene_Name": "TrCYP72A1633",
                "Applied_Species_Latin_Name": "Trifolium repens",
                "Terminal_Metabolite": "glycyrrhetinic acid; glycyrrhetinic acid",
                "Primary_Product": "glycyrrhetinic acid",
                "Catalyzed_Reaction_Description": "glycyrrhetol to glycyrrhetaldehyde to glycyrrhetinic acid",
            },
        ),
        GeneChunk(
            gene_name="GA20ox",
            paper_title="Gibberellic acid regulation",
            journal="Plant Journal",
            doi="10.example/gibberellin",
            gene_type="Common_Genes",
            chunk_type="common_gene",
            content="GA regulates plant height.",
            metadata={
                "Gene_Name": "GA20ox",
                "Terminal_Metabolite": "gibberellic acid",
            },
        ),
    ]
    spec = {
        "concepts": [
            {
                "concept": "target_metabolite",
                "query_weight": 5,
                "surface_forms": [
                    {"text": "glycyrrhetinic acid", "weight": 1.0, "match": "phrase"},
                    {
                        "text": "GA",
                        "weight": 0.25,
                        "match": "token",
                        "requires_cohit": ["glycyrrh", "triterpene", "CYP72A"],
                    },
                ],
                "target_fields": {
                    "Terminal_Metabolite": 2.5,
                    "Primary_Product": 2.3,
                    "Catalyzed_Reaction_Description": 2.0,
                    "content": 0.4,
                },
                "concept_cap_multiplier": 2.0,
            }
        ],
        "occurrence_decay": 0.5,
    }

    retriever = FieldKeywordRetriever(tmp_path)
    retriever.build(chunks)

    results = retriever.search(spec, top_k=2)

    assert results[0][0] == 0
    assert [index for index, _score in results] == [0]
    explanation = retriever.explain(0, spec)
    assert explanation["score"] > 0
    assert any(match["field"] == "Terminal_Metabolite" for match in explanation["matches"])


def test_field_keyword_save_load_and_chunk_count_guard(tmp_path: Path):
    from nutrimaster.rag.field_keyword import FieldKeywordRetriever
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [
        GeneChunk(
            gene_name="BAS",
            paper_title="Triterpene pathway",
            journal="Plant Cell",
            doi="10.example/bas",
            gene_type="Pathway_Genes",
            chunk_type="pathway_gene",
            content="beta-amyrin synthase",
            metadata={"Primary_Product": "β-amyrin"},
        )
    ]

    retriever = FieldKeywordRetriever(tmp_path, chunks=chunks)
    retriever.build(chunks, corpus_fingerprint="generation-a")
    retriever.save()

    loaded = FieldKeywordRetriever(tmp_path, chunks=chunks)
    assert loaded.load(expected_chunks=1, expected_fingerprint="generation-a")
    assert loaded.search("beta-amyrin", top_k=1)[0][0] == 0
    assert not FieldKeywordRetriever(tmp_path).load(expected_chunks=2)
    assert not FieldKeywordRetriever(tmp_path).load(expected_chunks=1, expected_fingerprint="generation-b")


def test_field_keyword_preserves_short_surface_form_recall(tmp_path: Path):
    from nutrimaster.rag.field_keyword import FieldKeywordRetriever
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [
        GeneChunk("GA20ox", "Gibberellin", "Plant Cell", "d1", "Common_Genes", "GA controls height", {}),
        GeneChunk("PSY1", "Carotenoid", "Nature", "d2", "Pathway_Genes", "lycopene", {}),
    ]
    spec = {
        "concepts": [
            {
                "concept": "short_alias",
                "surface_forms": [{"text": "GA", "match": "token"}],
                "target_fields": {"content": 1.0},
            }
        ]
    }
    retriever = FieldKeywordRetriever(tmp_path, chunks=chunks)
    retriever.build(chunks)

    assert retriever.search(spec, top_k=2)[0][0] == 0


def test_field_keyword_short_tokens_use_sqlite_instead_of_corpus_scan(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.field_keyword as field_module
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [
        GeneChunk("GA20ox", "", "", "", "", "GA response", {"Gene_Name": "GA20ox"}),
        GeneChunk("NRT1", "", "", "", "", "nitrogen", {"Gene_Name": "NRT1"}),
    ]
    retriever = field_module.FieldKeywordRetriever(tmp_path, chunks=chunks)
    retriever.build(chunks, corpus_fingerprint="generation-a")
    retriever.save()
    def unexpected_scan(_requests):
        raise AssertionError("short token candidates must come from the token FTS table")

    monkeypatch.setattr(retriever, "_scan_candidate_requests", unexpected_scan)
    spec = {
        "concepts": [
            {
                "concept": "short hormones",
                "surface_forms": [
                    {"text": "GA", "match": "token"},
                    {"text": "N", "match": "token"},
                ],
                "target_fields": {"Gene_Name": 2.0, "content": 1.0},
            }
        ]
    }

    assert retriever.search(spec, top_k=2)


def test_field_keyword_short_substrings_keep_exact_scan_fallback(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.field_keyword as field_module
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [GeneChunk("GA20ox", "", "", "", "", "GA response", {})]
    retriever = field_module.FieldKeywordRetriever(tmp_path, chunks=chunks)
    retriever.build(chunks)
    calls = []
    original = retriever._scan_candidate_requests

    def counted(requests):
        calls.append(requests)
        return original(requests)

    monkeypatch.setattr(retriever, "_scan_candidate_requests", counted)
    spec = {
        "concepts": [{
            "concept": "substring",
            "surface_forms": [{"text": "GA", "match": "substring"}],
            "target_fields": {"content": 1.0},
        }]
    }

    assert retriever.search(spec, top_k=1)[0][0] == 0
    assert len(calls) == 1
    assert set(calls[0]) == {"ga"}


def test_field_keyword_queries_each_long_surface_once_across_all_fields(tmp_path: Path, monkeypatch):
    import nutrimaster.rag.field_keyword as field_module
    from nutrimaster.rag.gene_index import GeneChunk

    chunks = [GeneChunk("GAME8", "Alkaloid", "", "", "", "stereochemistry", {})]
    retriever = field_module.FieldKeywordRetriever(tmp_path, chunks=chunks)
    retriever.build(chunks, corpus_fingerprint="generation-a")
    retriever.save()
    calls = []
    original = field_module._query_field_candidates

    def counted(connection, needle, field_names):
        calls.append((needle, field_names))
        return original(connection, needle, field_names)

    monkeypatch.setattr(field_module, "_query_field_candidates", counted)
    spec = {
        "concepts": [
            {
                "concept": "GAME8",
                "surface_forms": [{"text": "GAME8"}],
                "target_fields": {"Gene_Name": 2.0, "content": 1.0, "paper_title": 0.5},
            }
        ]
    }

    assert retriever.search(spec, top_k=1)[0][0] == 0
    assert len(calls) == 1
    assert calls[0][0] == "game8"
    assert set(calls[0][1]) == {"Gene_Name", "__gene_name__", "__content__", "__paper_title__"}


def test_jina_hybrid_search_uses_explicit_field_keyword_spec(tmp_path: Path, monkeypatch):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.jina import JinaRetriever

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    chunks = [
        GeneChunk(
            gene_name="TrCYP72A1633",
            paper_title="White clover glycyrrhetinic acid pathway",
            journal="Plant Biotechnology",
            doi="10.example/ga",
            gene_type="Pathway_Genes",
            chunk_type="pathway_gene",
            content="glycyrrhetinic acid pathway",
            metadata={"Terminal_Metabolite": "glycyrrhetinic acid", "Applied_Species_Latin_Name": "Trifolium repens"},
        ),
        GeneChunk(
            gene_name="UnrelatedDenseHit",
            paper_title="Dense favorite",
            journal="Nature",
            doi="10.example/dense",
            gene_type="Common_Genes",
            chunk_type="common_gene",
            content="dense vector favorite",
            metadata={"Terminal_Metabolite": "anthocyanin"},
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
    monkeypatch.setenv("NUTRIMASTER_DISABLE_BM25", "1")
    monkeypatch.setenv("NUTRIMASTER_SPARSE_INDEX_BUILD_ON_MISS", "1")

    spec = {
        "concepts": [
            {
                "concept": "target_metabolite",
                "query_weight": 5,
                "surface_forms": [{"text": "glycyrrhetinic acid", "weight": 1.0, "match": "phrase"}],
                "target_fields": {"Terminal_Metabolite": 2.5, "content": 0.4},
            }
        ]
    }

    results = asyncio.run(retriever.hybrid_search("GA in white clover", top_k=1, rerank_top_n=2, keyword_spec=spec))

    assert results[0][0].gene_name == "TrCYP72A1633"
    assert (index_dir / "field_keyword_v3.sqlite3").exists()
    assert retriever.index_status()["field_keyword_chunks"] == 2


def test_production_field_flag_executes_dense_bm25_and_field_branches(tmp_path: Path, monkeypatch):
    from nutrimaster.config.settings import RagSettings, Settings
    from nutrimaster.rag.gene_index import GeneChunk
    from nutrimaster.rag.jina import JinaRetriever

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    chunks = [
        GeneChunk("dense", "", "", "", "", "dense", {}),
        GeneChunk("bm25", "", "", "", "", "bm25", {}),
        GeneChunk("field", "", "", "", "", "field", {}),
    ]
    with (index_dir / "chunks.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    np.save(index_dir / "embeddings.npy", np.eye(3, dtype=np.float32))
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
    calls = []

    class SparseBranch:
        def __init__(self, name, ranked):
            self.name = name
            self.ranked = ranked

        def search(self, query, top_k):
            calls.append((self.name, query, top_k))
            return self.ranked

    retriever._dense_search_indices = lambda query, top_k: (
        calls.append(("dense", query, top_k)) or [(0, 1.0)]
    )
    retriever._ensure_bm25 = lambda: SparseBranch("bm25", [(1, 1.0)])
    retriever._ensure_field_keyword = lambda: SparseBranch("field", [(2, 1.0)])
    monkeypatch.setenv("NUTRIMASTER_DISABLE_BM25", "0")
    monkeypatch.setenv("NUTRIMASTER_ENABLE_FIELD_KEYWORD", "1")

    results = retriever._hybrid_search_sync("NRT1.1 nitrogen", top_k=3, rerank_top_n=3)

    assert {name for name, _query, _top_k in calls} == {"dense", "bm25", "field"}
    assert {chunk.gene_name for chunk, _score in results} == {"dense", "bm25", "field"}
