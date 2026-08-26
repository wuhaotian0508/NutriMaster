from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
import pytest


def _settings(tmp_path: Path):
    from nutrimaster.config.settings import RagSettings, Settings

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    index_root = tmp_path / "target-index"
    index_root.mkdir()
    return Settings(
        project_root=tmp_path,
        jina_api_key="test-key",
        rag=RagSettings(
            data_dir=corpus,
            index_dir=index_root,
            personal_lib_dir=tmp_path / "personal",
        ),
    )


def _write_legacy_source(settings, tmp_path: Path) -> Path:
    from nutrimaster.rag.gene_index import GeneChunk

    paper = {
        "Title": "PAL controls phenylpropanoid entry",
        "Journal": "Plant Journal",
        "DOI": "10.1000/pal",
        "Pathway_Genes": [
            {
                "Gene_Name": "PAL",
                "Species_Latin_Name": "Arabidopsis thaliana",
                "Primary_Substrate": "L-phenylalanine",
                "Primary_Product": "cinnamic acid",
                "Biosynthetic_Pathway": "phenylpropanoid biosynthesis",
            }
        ],
    }
    corpus_file = settings.rag.data_dir / "pal.json"
    corpus_file.write_text(json.dumps(paper, sort_keys=True), encoding="utf-8")
    raw = corpus_file.read_bytes()

    source = tmp_path / "legacy-index"
    source.mkdir()
    chunks = [
        GeneChunk(
            gene_name="PAL",
            paper_title=paper["Title"],
            journal=paper["Journal"],
            doi=paper["DOI"],
            gene_type="Pathway_Genes",
            content="PAL converts L-phenylalanine into cinnamic acid",
            metadata=paper["Pathway_Genes"][0],
        )
    ]
    with (source / "chunks.pkl").open("wb") as file:
        pickle.dump(chunks, file)
    np.save(source / "embeddings.npy", np.array([[1.0, 0.0]], dtype=np.float32))
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "chunker_version": chunks[0].chunker_version,
                "files": {
                    corpus_file.name: {
                        "sha": hashlib.sha256(raw).hexdigest(),
                        "chunker_version": chunks[0].chunker_version,
                        "n_chunks": 1,
                        "start": 0,
                        "end": 1,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    # Old object-heavy artifacts must never be reused by the bootstrap.
    (source / "bm25.pkl").write_bytes(b"legacy-bm25")
    (source / "field_keyword.pkl").write_bytes(b"legacy-field")
    return source


def test_bootstrap_publishes_a_fully_valid_generation_without_dense_copies(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.rag.index_generation import (
        read_current_generation_id,
        validate_generation,
    )
    from nutrimaster.rag.legacy_bootstrap import bootstrap_legacy_generation

    settings = _settings(tmp_path)
    source = _write_legacy_source(settings, tmp_path)
    monkeypatch.setenv(
        "NUTRIMASTER_LEGACY_BOOTSTRAP_DISK_SAFETY_BYTES",
        str(1024**3),
    )

    result = bootstrap_legacy_generation(
        settings,
        source_dir=source,
        available_bytes=20 * 1024**3,
    )

    generation = Path(result["generation_dir"])
    assert result["state"] == "succeeded"
    assert read_current_generation_id(settings.rag.index_dir) == result["generation_id"]
    payload = validate_generation(generation)
    assert set(payload["artifacts"]) == {
        "chunks",
        "embeddings",
        "embedding_norms",
        "bm25",
        "field_keyword",
        "dense_manifest",
        "graph",
    }
    for filename in ("chunks.pkl", "embeddings.npy", "manifest.json"):
        assert (generation / filename).stat().st_ino == (source / filename).stat().st_ino
    assert (generation / "bm25_sparse_v4.pkl").read_bytes() != b"legacy-bm25"
    assert (generation / "field_keyword_v3.sqlite3").read_bytes() != b"legacy-field"
    assert not any(settings.rag.index_dir.glob("generations/.staging-*"))
    assert not any(
        (settings.rag.index_dir / "builder-state" / "work").glob(
            "corpus-snapshot-bootstrap-*"
        )
    )


def test_bootstrap_rejects_corpus_drift_before_creating_a_generation(
    tmp_path: Path,
):
    from nutrimaster.rag.legacy_bootstrap import bootstrap_legacy_generation

    settings = _settings(tmp_path)
    source = _write_legacy_source(settings, tmp_path)
    (settings.rag.data_dir / "pal.json").write_text('{"changed":true}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum does not match"):
        bootstrap_legacy_generation(
            settings,
            source_dir=source,
            available_bytes=20 * 1024**3,
        )

    assert not (settings.rag.index_dir / "CURRENT").exists()
    assert not (settings.rag.index_dir / "generations").exists()


def test_bootstrap_disk_gate_fails_before_snapshot_or_generation(
    tmp_path: Path,
):
    from nutrimaster.rag.legacy_bootstrap import bootstrap_legacy_generation

    settings = _settings(tmp_path)
    source = _write_legacy_source(settings, tmp_path)

    with pytest.raises(RuntimeError, match="insufficient disk space"):
        bootstrap_legacy_generation(
            settings,
            source_dir=source,
            available_bytes=1,
        )

    assert not (settings.rag.index_dir / "CURRENT").exists()
    assert not (settings.rag.index_dir / "generations").exists()
    assert not any(
        (settings.rag.index_dir / "builder-state" / "work").glob(
            "corpus-snapshot-bootstrap-*"
        )
    )


def test_read_only_preflight_proves_source_corpus_and_disk_contract(
    tmp_path: Path,
):
    from nutrimaster.rag.legacy_bootstrap import preflight_legacy_generation

    settings = _settings(tmp_path)
    source = _write_legacy_source(settings, tmp_path)

    result = preflight_legacy_generation(
        settings,
        source_dir=source,
        available_bytes=20 * 1024**3,
    )

    assert result["status"] == "ok"
    assert result["source_validation"]["chunks"] == 1
    assert result["disk_preflight"]["required_bytes"] > 1024**3
    assert not (settings.rag.index_dir / "CURRENT").exists()
    assert not (settings.rag.index_dir / "builder-state").exists()
    assert not (settings.rag.index_dir / "generations").exists()


def test_bootstrap_is_one_time_and_recovery_never_guesses_between_generations(
    tmp_path: Path,
):
    from nutrimaster.rag.index_generation import current_generation_path
    from nutrimaster.rag.legacy_bootstrap import (
        bootstrap_legacy_generation,
        recover_legacy_bootstrap,
    )

    settings = _settings(tmp_path)
    source = _write_legacy_source(settings, tmp_path)
    first = bootstrap_legacy_generation(
        settings,
        source_dir=source,
        available_bytes=20 * 1024**3,
    )

    with pytest.raises(RuntimeError, match="one-time only"):
        bootstrap_legacy_generation(
            settings,
            source_dir=source,
            available_bytes=20 * 1024**3,
        )
    recovered = recover_legacy_bootstrap(settings)
    assert recovered["state"] == "already-active"
    assert recovered["generation_id"] == first["generation_id"]

    current_generation_path(settings.rag.index_dir).unlink()
    duplicate = settings.rag.index_dir / "generations" / ("f" * 64)
    duplicate.mkdir()
    with pytest.raises(RuntimeError, match="multiple orphan generations"):
        recover_legacy_bootstrap(settings)
