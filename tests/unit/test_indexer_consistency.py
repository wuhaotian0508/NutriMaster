from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]

from nutrimaster.rag.gene_index import CHUNKER_VERSION, GeneChunk, IncrementalIndexer, sha256_of


def _write_manifest(index_dir: Path, filename: str, sha: str, n_chunks: int) -> None:
    (index_dir / "manifest.json").write_text(
        json.dumps(
            {
                "chunker_version": CHUNKER_VERSION,
                "files": {
                    filename: {
                        "sha": sha,
                        "chunker_version": CHUNKER_VERSION,
                        "n_chunks": n_chunks,
                        "start": 0,
                        "end": n_chunks,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _sample_chunk() -> GeneChunk:
    return GeneChunk(
        gene_name="PSY1",
        paper_title="Pilot carotenoid paper",
        journal="Plant Test",
        doi="10.0000/test",
        gene_type="Pathway_Genes",
        content="PSY1 controls carotenoid biosynthesis in tomato fruit.",
        metadata={"species": "Solanum lycopersicum"},
        chunk_type="pathway_gene",
        chunker_version=CHUNKER_VERSION,
        source_fields=["Gene"],
        relations={},
    )


def test_rebuilds_when_manifest_matches_but_index_artifacts_are_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    json_file = data_dir / "paper_nutri_plant_verified.json"
    json_file.write_text("{}", encoding="utf-8")
    _write_manifest(index_dir, json_file.name, sha256_of(json_file), n_chunks=1)

    load_calls = []

    def load_paper(path: Path) -> list[GeneChunk]:
        load_calls.append(path.name)
        return [_sample_chunk()]

    def embed(texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 3), dtype=np.float32)

    chunks, embeddings = IncrementalIndexer(index_dir, data_dir, embed).build_incremental(
        load_paper_fn=load_paper,
    )

    assert load_calls == [json_file.name]
    assert len(chunks) == 1
    assert embeddings is not None
    assert embeddings.shape == (1, 3)


def test_rebuilds_when_manifest_ranges_cannot_be_satisfied_by_old_index(tmp_path: Path):
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    json_file = data_dir / "paper_nutri_plant_verified.json"
    json_file.write_text("{}", encoding="utf-8")
    _write_manifest(index_dir, json_file.name, sha256_of(json_file), n_chunks=1)

    with (index_dir / "chunks.pkl").open("wb") as f:
        pickle.dump([], f)
    np.save(index_dir / "embeddings.npy", np.zeros((0, 3), dtype=np.float32))

    def load_paper(path: Path) -> list[GeneChunk]:
        return [_sample_chunk()]

    def embed(texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 3), dtype=np.float32)

    chunks, embeddings = IncrementalIndexer(index_dir, data_dir, embed).build_incremental(
        load_paper_fn=load_paper,
    )

    assert len(chunks) == 1
    assert embeddings is not None
    assert embeddings.shape == (1, 3)


def test_incremental_indexer_adds_updates_and_removes_corpus_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()

    first = data_dir / "first.json"
    second = data_dir / "second.json"
    first.write_text('{"gene": "PSY1"}', encoding="utf-8")
    second.write_text('{"gene": "SIG6"}', encoding="utf-8")

    load_calls: list[str] = []

    def load_paper(path: Path) -> list[GeneChunk]:
        load_calls.append(path.name)
        gene = json.loads(path.read_text(encoding="utf-8"))["gene"]
        return [
            GeneChunk(
                gene_name=gene,
                paper_title=f"{gene} paper",
                journal="Plant Test",
                doi=f"10.0000/{gene.lower()}",
                gene_type="Pathway_Genes",
                content=f"{gene} controls a tested pathway.",
                metadata={},
                chunk_type="gene",
                chunker_version=CHUNKER_VERSION,
                source_fields=["gene"],
                relations={},
            )
        ]

    def embed(texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 3), dtype=np.float32)

    indexer = IncrementalIndexer(index_dir, data_dir, embed)
    chunks, embeddings = indexer.build_incremental(load_paper_fn=load_paper)

    assert load_calls == ["first.json", "second.json"]
    assert [chunk.gene_name for chunk in chunks] == ["PSY1", "SIG6"]
    assert embeddings is not None
    assert embeddings.shape == (2, 3)

    load_calls.clear()
    first.write_text('{"gene": "PSY2"}', encoding="utf-8")
    second.unlink()
    chunks, embeddings = indexer.build_incremental(load_paper_fn=load_paper)

    assert load_calls == ["first.json"]
    assert [chunk.gene_name for chunk in chunks] == ["PSY2"]
    assert embeddings is not None
    assert embeddings.shape == (1, 3)
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest["files"]) == ["first.json"]


def test_incremental_indexer_writes_canonical_chunk_module(tmp_path: Path):
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    json_file = data_dir / "paper.json"
    json_file.write_text("{}", encoding="utf-8")

    indexer = IncrementalIndexer(index_dir, data_dir, lambda texts: np.ones((len(texts), 3), dtype=np.float32))
    indexer.build_incremental(load_paper_fn=lambda path: [_sample_chunk()])

    with (index_dir / "chunks.pkl").open("rb") as f:
        saved_chunks = pickle.load(f)

    assert saved_chunks[0].__class__.__module__ == "nutrimaster.rag.gene_index"


def test_incremental_indexer_removes_stale_embeddings_for_empty_corpus(tmp_path: Path):
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()

    json_file = data_dir / "paper.json"
    json_file.write_text("{}", encoding="utf-8")

    indexer = IncrementalIndexer(index_dir, data_dir, lambda texts: np.ones((len(texts), 3), dtype=np.float32))
    chunks, embeddings = indexer.build_incremental(load_paper_fn=lambda path: [_sample_chunk()])
    assert len(chunks) == 1
    assert embeddings is not None
    assert (index_dir / "embeddings.npy").exists()

    json_file.unlink()
    chunks, embeddings = indexer.build_incremental(load_paper_fn=lambda path: [_sample_chunk()])

    assert chunks == []
    assert embeddings is None
    assert not (index_dir / "embeddings.npy").exists()


def test_incremental_indexer_streams_embedding_parts_without_concatenate(
    tmp_path: Path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    data_dir.mkdir()
    index_dir.mkdir()
    (data_dir / "first.json").write_text("{}", encoding="utf-8")
    (data_dir / "second.json").write_text("{}", encoding="utf-8")

    indexer = IncrementalIndexer(
        index_dir,
        data_dir,
        lambda texts: np.ones((len(texts), 3), dtype=np.float32),
    )

    def forbidden_concatenate(*args, **kwargs):
        raise AssertionError("dense index assembly must not allocate np.concatenate")

    monkeypatch.setattr(np, "concatenate", forbidden_concatenate)
    chunks, embeddings = indexer.build_incremental(
        load_paper_fn=lambda path: [_sample_chunk()],
    )

    assert len(chunks) == 2
    assert isinstance(embeddings, np.memmap)
    np.testing.assert_array_equal(embeddings, np.ones((2, 3), dtype=np.float32))

    # The unchanged incremental pass must also keep the old dense matrix
    # file-backed while it copies per-file slices into the atomic temporary.
    chunks, embeddings = indexer.build_incremental(
        load_paper_fn=lambda path: (_ for _ in ()).throw(AssertionError("unexpected rebuild")),
    )
    assert len(chunks) == 2
    assert isinstance(embeddings, np.memmap)
    np.testing.assert_array_equal(embeddings, np.ones((2, 3), dtype=np.float32))
