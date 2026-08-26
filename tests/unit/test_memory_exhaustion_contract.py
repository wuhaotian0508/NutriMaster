from __future__ import annotations

import asyncio
import json
import pickle
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from nutrimaster.agent.pi_tools import PiToolContext, PiToolService
from nutrimaster.agent.agent import Agent
from nutrimaster.rag.jina import JinaRetriever
from nutrimaster.rag.service import RAGSearchContext, RAGSearchService
from nutrimaster.extraction import pipeline as extraction_pipeline


def test_hybrid_search_never_downgrades_memory_exhaustion_to_sparse_retrieval():
    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever.embeddings = np.ones((1, 1), dtype=np.float32)
    retriever.chunks = [SimpleNamespace(content="chunk")]
    retriever._query_semaphore = threading.BoundedSemaphore(1)
    retriever._index_lock = threading.RLock()
    retriever._dense_query_error = None

    def exhaust(*_args, **_kwargs):
        raise MemoryError("simulated allocator pressure")

    retriever._dense_search_indices = exhaust

    with pytest.raises(MemoryError, match="allocator pressure"):
        retriever._hybrid_search_sync("query")


def test_index_load_never_converts_memory_exhaustion_to_an_empty_index(tmp_path, monkeypatch):
    (tmp_path / "chunks.pkl").write_bytes(b"placeholder")
    np.save(tmp_path / "embeddings.npy", np.ones((1, 1), dtype=np.float32))

    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever.index_path = tmp_path
    retriever.generation_id = None

    def exhaust(_file):
        raise MemoryError("simulated pickle pressure")

    monkeypatch.setattr(pickle, "load", exhaust)
    with pytest.raises(MemoryError, match="pickle pressure"):
        retriever._load_index()


def test_lazy_sparse_loaders_propagate_memory_exhaustion(tmp_path, monkeypatch):
    import nutrimaster.rag.jina as jina

    class ExhaustedBm25:
        def __init__(self, _path):
            pass

        def load(self, **_kwargs):
            raise MemoryError("simulated BM25 pressure")

    class ExhaustedField:
        def __init__(self, _path, chunks=None):
            pass

        def load(self, **_kwargs):
            raise MemoryError("simulated field pressure")

    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever._index_lock = threading.RLock()
    retriever.index_path = tmp_path
    retriever.chunks = [SimpleNamespace(content="chunk")]
    retriever.corpus_fingerprint = "fingerprint"
    retriever._bm25 = None
    retriever._bm25_error = None
    retriever._field_keyword = None
    retriever._field_keyword_error = None

    monkeypatch.setattr(jina, "BM25Retriever", ExhaustedBm25)
    with pytest.raises(MemoryError, match="BM25 pressure"):
        retriever._ensure_bm25(allow_rebuild=False)

    monkeypatch.setattr(jina, "FieldKeywordRetriever", ExhaustedField)
    with pytest.raises(MemoryError, match="field pressure"):
        retriever._ensure_field_keyword(allow_rebuild=False)


def test_offline_builder_does_not_reload_the_corpus_after_memory_exhaustion(
    tmp_path,
    monkeypatch,
):
    import nutrimaster.rag.build_sparse_indexes as sparse_builder
    import nutrimaster.rag.jina as jina_module

    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever._query_semaphore = threading.BoundedSemaphore(1)
    retriever._index_lock = threading.RLock()
    retriever.index_root = tmp_path
    retriever.index_path = tmp_path
    retriever.generation_id = "a" * 64
    retriever.legacy_index_layout = False
    retriever.data_dir = tmp_path / "corpus"
    retriever.chunks = [SimpleNamespace(content="chunk")]
    retriever.embeddings = np.ones((1, 1), dtype=np.float32)
    retriever.embedding_norms = np.ones(1, dtype=np.float32)
    retriever._bm25 = object()
    retriever._field_keyword = object()

    def exhaust(*_args, **_kwargs):
        raise MemoryError("simulated builder pressure")

    class DenseBuilder:
        def __init__(self, **_kwargs):
            pass

        def build(self, *, force):
            assert force is True

    monkeypatch.setattr(jina_module, "IndexService", DenseBuilder)
    monkeypatch.setattr(sparse_builder, "build_sparse_indexes", exhaust)
    monkeypatch.setattr(
        retriever,
        "_load_index",
        lambda: pytest.fail("the corpus must not be reloaded after MemoryError"),
    )

    with pytest.raises(MemoryError, match="builder pressure"):
        retriever.build_index(incremental=False)
    assert retriever.chunks == []
    assert retriever.embeddings is None


@pytest.mark.parametrize("phase", ["embeddings", "norms", "bm25", "field"])
def test_generation_validation_preserves_memory_error_identity(
    tmp_path,
    monkeypatch,
    phase,
):
    import nutrimaster.rag.index_generation as generation
    from nutrimaster.rag import bm25 as bm25_module
    from nutrimaster.rag import field_keyword as field_module

    exhausted = MemoryError(f"simulated {phase} validation pressure")
    monkeypatch.setattr(
        generation,
        "validate_generation_manifest",
        lambda *_args, **_kwargs: {
            "chunk_count": 1,
            "embedding_shape": [1, 1],
            "corpus_fingerprint": "a" * 64,
            "artifacts": {},
        },
    )

    def load_array(path, *_args, **_kwargs):
        artifact = "norms" if "norms" in str(path) else "embeddings"
        if artifact == phase:
            raise exhausted
        return (
            np.ones(1, dtype=np.float32)
            if artifact == "norms"
            else np.ones((1, 1), dtype=np.float32)
        )

    def load_bm25(*_args, **_kwargs):
        if phase == "bm25":
            raise exhausted
        return True

    def load_field(*_args, **_kwargs):
        if phase == "field":
            raise exhausted
        return True

    monkeypatch.setattr(generation.np, "load", load_array)
    monkeypatch.setattr(bm25_module.BM25Retriever, "load", load_bm25)
    monkeypatch.setattr(field_module.FieldKeywordRetriever, "load", load_field)
    with pytest.raises(MemoryError) as caught:
        generation.validate_generation(tmp_path)
    assert caught.value is exhausted


def test_sparse_builder_cleans_staging_without_replacing_memory_error(
    tmp_path,
    monkeypatch,
):
    import nutrimaster.rag.build_sparse_indexes as sparse_builder

    exhausted = MemoryError("simulated sparse staging pressure")
    discarded = []
    original_discard = sparse_builder.discard_staging_generation

    def exhaust(*_args, **_kwargs):
        raise exhausted

    def record_discard(index_root, staging_dir):
        discarded.append(staging_dir)
        original_discard(index_root, staging_dir)

    monkeypatch.setattr(sparse_builder, "copy_generation_files", exhaust)
    monkeypatch.setattr(sparse_builder, "discard_staging_generation", record_discard)

    with pytest.raises(MemoryError) as caught:
        sparse_builder.build_sparse_indexes(tmp_path / "index")

    assert caught.value is exhausted
    assert len(discarded) == 1
    assert not discarded[0].exists()


def test_jina_staging_cleanup_failure_cannot_mask_memory_error(tmp_path, monkeypatch):
    import nutrimaster.rag.jina as jina_module

    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever._query_semaphore = threading.BoundedSemaphore(1)
    retriever._index_lock = threading.RLock()
    retriever.index_root = tmp_path / "index"
    retriever.index_path = retriever.index_root
    retriever.generation_id = None
    retriever.legacy_index_layout = True
    retriever.data_dir = tmp_path / "corpus"
    retriever.chunks = []
    retriever.embeddings = None
    retriever.embedding_norms = None
    retriever._bm25 = None
    retriever._field_keyword = None
    exhausted = MemoryError("simulated dense staging pressure")

    def exhaust(*_args, **_kwargs):
        raise exhausted

    def cleanup_failure(*_args, **_kwargs):
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(jina_module, "copy_generation_files", exhaust)
    monkeypatch.setattr(jina_module, "discard_staging_generation", cleanup_failure)

    with pytest.raises(MemoryError) as caught:
        retriever.build_index(incremental=True)
    assert caught.value is exhausted


def test_builder_mode_does_not_reload_previous_generation_after_ordinary_failure(
    tmp_path,
    monkeypatch,
):
    import nutrimaster.rag.build_sparse_indexes as sparse_builder
    import nutrimaster.rag.jina as jina_module

    retriever = JinaRetriever.__new__(JinaRetriever)
    retriever._query_semaphore = threading.BoundedSemaphore(1)
    retriever._index_lock = threading.RLock()
    retriever.index_root = tmp_path
    retriever.index_path = tmp_path
    retriever.generation_id = "a" * 64
    retriever.legacy_index_layout = False
    retriever.data_dir = tmp_path / "corpus"
    retriever.chunks = [SimpleNamespace(content="chunk")]
    retriever.embeddings = np.ones((1, 1), dtype=np.float32)
    retriever.embedding_norms = np.ones(1, dtype=np.float32)
    retriever._bm25 = object()
    retriever._field_keyword = object()
    failed = RuntimeError("simulated ordinary builder failure")

    def fail(*_args, **_kwargs):
        raise failed

    class DenseBuilder:
        def __init__(self, **_kwargs):
            pass

        def build(self, *, force):
            assert force is True

    monkeypatch.setattr(jina_module, "IndexService", DenseBuilder)
    monkeypatch.setattr(sparse_builder, "build_sparse_indexes", fail)
    monkeypatch.setattr(
        retriever,
        "_load_index",
        lambda: pytest.fail("isolated builder must not reload the serving generation"),
    )

    with pytest.raises(RuntimeError) as caught:
        retriever.build_index(incremental=False, reload_on_failure=False)
    assert caught.value is failed


def test_builder_mode_skips_initial_and_post_publish_corpus_loads(tmp_path, monkeypatch):
    import nutrimaster.rag.build_sparse_indexes as sparse_builder
    import nutrimaster.rag.jina as jina_module
    from nutrimaster.config.settings import RagSettings, Settings

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    settings = Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=index_dir,
            personal_lib_dir=tmp_path / "personal",
        ),
    )
    monkeypatch.setattr(
        JinaRetriever,
        "_load_index",
        lambda _self: pytest.fail("isolated builder must not load a runtime corpus"),
    )
    retriever = JinaRetriever(settings=settings, autoload=False)
    generation_id = "b" * 64

    class DenseBuilder:
        def __init__(self, **_kwargs):
            pass

        def build(self, *, force):
            assert force is True

    monkeypatch.setattr(jina_module, "IndexService", DenseBuilder)
    monkeypatch.setattr(
        sparse_builder,
        "build_sparse_indexes",
        lambda *_args, **_kwargs: {
            "generation_dir": str(index_dir / "generations" / generation_id),
            "generation_id": generation_id,
            "corpus_fingerprint": "c" * 64,
        },
    )

    retriever.build_index(
        incremental=False,
        load_after_build=False,
        reload_on_failure=False,
    )

    assert retriever.generation_id == generation_id
    assert retriever.chunks == []
    assert retriever.embeddings is None


def test_dense_builder_fallbacks_preserve_memory_error_identity(
    tmp_path,
    monkeypatch,
):
    import nutrimaster.rag.gene_index as gene_index

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    indexer = gene_index.IncrementalIndexer(
        tmp_path / "index",
        data_dir,
        lambda _texts: np.ones((1, 1), dtype=np.float32),
    )

    manifest_error = MemoryError("simulated manifest pressure")
    indexer.manifest_path.write_text("{}", encoding="utf-8")

    def exhaust_manifest(_payload):
        raise manifest_error

    monkeypatch.setattr(gene_index.json, "loads", exhaust_manifest)
    with pytest.raises(MemoryError) as caught:
        indexer._load_manifest()
    assert caught.value is manifest_error

    existing_error = MemoryError("simulated existing-index pressure")
    indexer.chunks_path.write_bytes(b"placeholder")
    np.save(indexer.embeds_path, np.ones((1, 1), dtype=np.float32))

    def exhaust_existing(_file):
        raise existing_error

    monkeypatch.setattr(gene_index.pickle, "load", exhaust_existing)
    with pytest.raises(MemoryError) as caught:
        indexer._load_existing()
    assert caught.value is existing_error


def test_chunk_strategy_never_falls_back_after_memory_exhaustion(monkeypatch):
    import nutrimaster.rag.gene_index as gene_index

    exhausted = MemoryError("simulated strategy pressure")

    def exhaust(*_args, **_kwargs):
        raise exhausted

    monkeypatch.setattr(gene_index, "route_paper", lambda _paper: "plant_genes")
    monkeypatch.setattr(gene_index.PlantGenesChunker, "chunk", exhaust)
    monkeypatch.setattr(
        gene_index.GenericChunker,
        "chunk",
        lambda *_args, **_kwargs: pytest.fail("generic fallback must not run after OOM"),
    )

    with pytest.raises(MemoryError) as caught:
        gene_index.chunk_paper({})
    assert caught.value is exhausted


def test_index_status_reuses_cached_file_counts(tmp_path, monkeypatch):
    from pathlib import Path

    from nutrimaster.config.settings import RagSettings, Settings

    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    personal_dir = tmp_path / "personal"
    data_dir.mkdir()
    index_dir.mkdir()
    (data_dir / "first.json").write_text("{}", encoding="utf-8")
    (data_dir / "second.json").write_text("{}", encoding="utf-8")
    with (index_dir / "chunks.pkl").open("wb") as file:
        pickle.dump([SimpleNamespace(content="chunk")], file)
    np.save(index_dir / "embeddings.npy", np.ones((1, 1), dtype=np.float32))
    (index_dir / "manifest.json").write_text(
        json.dumps({"files": {"first.json": {}, "second.json": {}}}),
        encoding="utf-8",
    )
    settings = Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=data_dir,
            index_dir=index_dir,
            personal_lib_dir=personal_dir,
        ),
    )
    retriever = JinaRetriever(settings=settings)

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: pytest.fail("health must not reread manifest.json"),
    )
    monkeypatch.setattr(
        Path,
        "glob",
        lambda *_args, **_kwargs: pytest.fail("health must not rescan the corpus"),
    )

    first = retriever.index_status()
    second = retriever.index_status()
    assert first["corpus_files"] == second["corpus_files"] == 2
    assert first["manifest_files"] == second["manifest_files"] == 2


def test_multi_source_wrapper_propagates_memory_exhaustion():
    class ExhaustedSource:
        async def search(self, *_args, **_kwargs):
            raise MemoryError("simulated allocator pressure")

    with pytest.raises(MemoryError, match="allocator pressure"):
        asyncio.run(
            RAGSearchService._safe_search(
                ExhaustedSource(),
                "query",
                top_k=10,
                context=RAGSearchContext(),
            )
        )


def test_multi_source_search_cancels_siblings_after_memory_exhaustion():
    exhausted = MemoryError("simulated allocator pressure")
    sibling_cancelled = asyncio.Event()

    class ExhaustedSource:
        async def search(self, *_args, **_kwargs):
            await asyncio.sleep(0)
            raise exhausted

    class WaitingSource:
        async def search(self, *_args, **_kwargs):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise

    async def exercise():
        service = RAGSearchService(
            pubmed_source=ExhaustedSource(),
            gene_db_source=WaitingSource(),
        )
        with pytest.raises(MemoryError) as caught:
            await service.search("query")
        assert caught.value is exhausted
        assert sibling_cancelled.is_set()

    asyncio.run(exercise())


def test_pi_tool_adapter_never_converts_memory_exhaustion_to_tool_error():
    class ExhaustedRegistry:
        async def execute(self, *_args, **_kwargs):
            raise MemoryError("simulated allocator pressure")

    with pytest.raises(MemoryError, match="allocator pressure"):
        asyncio.run(
            PiToolService(ExhaustedRegistry()).execute(
                "rag_search",
                {"query": "nitrogen uptake"},
                PiToolContext(user_id="user-1"),
            )
        )


def test_pi_tool_adapter_extracts_memory_exhaustion_from_exception_group():
    exhausted = MemoryError("grouped allocator pressure")

    class ExhaustedRegistry:
        async def execute(self, *_args, **_kwargs):
            raise ExceptionGroup("parallel tool failure", [RuntimeError("peer"), exhausted])

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(
            PiToolService(ExhaustedRegistry()).execute(
                "rag_search",
                {"query": "nitrogen uptake"},
                PiToolContext(user_id="user-1"),
            )
        )
    assert exc_info.value is exhausted


def test_legacy_agent_never_continues_after_tool_memory_exhaustion():
    class ExhaustedRegistry:
        tool_names = {"rag_search"}
        get_definitions = []

        async def execute(self, *_args, **_kwargs):
            raise MemoryError("simulated allocator pressure")

    class EmptySkillLoader:
        def list_dir(self, user_id=None):
            return []

    async def tool_call_llm(*_args, **_kwargs):
        return SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(
                        name="rag_search",
                        arguments=json.dumps({"query": "nitrogen uptake"}),
                    ),
                )
            ],
        )

    agent = Agent(
        registry=ExhaustedRegistry(),
        skill_loader=EmptySkillLoader(),
        call_llm=tool_call_llm,
    )

    async def collect():
        return [event async for event in agent.run(user_input="query")]

    with pytest.raises(MemoryError, match="allocator pressure"):
        asyncio.run(collect())


def test_extraction_worker_never_downgrades_memory_exhaustion(monkeypatch, tmp_path):
    def exhaust(*_args, **_kwargs):
        raise MemoryError("simulated extraction pressure")

    monkeypatch.setattr(extraction_pipeline, "extract_paper", exhaust)
    with pytest.raises(MemoryError, match="extraction pressure"):
        extraction_pipeline.process_one_paper(
            tmp_path / "paper.md",
            "paper",
            SimpleNamespace(),
        )


def test_parallel_extraction_batch_stops_on_memory_exhaustion(monkeypatch, tmp_path):
    calls = 0
    calls_lock = threading.Lock()

    def exhaust(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        raise MemoryError("simulated batch pressure")

    monkeypatch.setattr(extraction_pipeline, "process_one_paper", exhaust)
    with pytest.raises(MemoryError, match="batch pressure"):
        extraction_pipeline.run_pipeline_batch(
            ["first.md", "second.md", "third.md"],
            input_dir=tmp_path,
            workers=2,
            tracker=SimpleNamespace(),
        )

    assert calls <= 2
