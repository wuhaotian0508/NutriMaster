from __future__ import annotations

import asyncio
import json
from pathlib import Path


def test_local_graph_index_builds_deduped_species_aware_neighborhood(tmp_path: Path):
    from nutrimaster.rag.graph import LocalGraphIndex

    corpus = tmp_path / "corpus"
    corpus.mkdir()
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
                "Core_Validation_Method": "enzyme assay",
            },
            {
                "Gene_Name": "PAL",
                "Species_Latin_Name": "Arabidopsis thaliana",
                "Primary_Substrate": "L-phenylalanine",
                "Primary_Product": "cinnamic acid",
                "Biosynthetic_Pathway": "phenylpropanoid biosynthesis",
                "Core_Validation_Method": "enzyme assay",
            },
        ],
    }
    (corpus / "pal.json").write_text(json.dumps(paper), encoding="utf-8")

    db_path = tmp_path / "graph.sqlite"
    index = LocalGraphIndex(db_path)
    index.build_from_corpus(corpus)

    result = index.neighborhood("PAL pathway neighbors", hops=1, direction="both")

    genes = [node for node in result.nodes if node["type"] == "Gene" and node["name"] == "PAL"]
    assert len(genes) == 1
    assert genes[0]["species"] == "Arabidopsis thaliana"
    assert {edge["relation"] for edge in result.edges} >= {"input_of", "produces"}
    assert max(edge["evidence"].get("evidence_count", 1) for edge in result.edges) == 2


def test_local_graph_edge_batches_are_bounded_and_merge_across_flushes(
    tmp_path: Path,
    monkeypatch,
):
    from nutrimaster.rag.graph import LocalGraphIndex

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.json").write_text(
        json.dumps(
            {
                "Title": "first evidence",
                "Pathway_Genes": [
                    {
                        "Gene_Name": "PAL",
                        "Applied_Species": "Tomato",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (corpus / "b.json").write_text(
        json.dumps(
            {
                "Title": "second evidence",
                "Common_Genes": [
                    {
                        "Gene_Name": "PAL",
                        "Applied_Species": "Tomato",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    index = LocalGraphIndex(tmp_path / "graph.sqlite", edge_batch_size=1)
    original_flush = index._flush_pending_edges
    peak_pending = 0
    flushes = 0

    def observed_flush(db):
        nonlocal peak_pending, flushes
        peak_pending = max(peak_pending, len(index._pending_edges))
        flushes += 1
        return original_flush(db)

    monkeypatch.setattr(index, "_flush_pending_edges", observed_flush)
    index.build_from_corpus(corpus)

    assert peak_pending <= 1
    assert flushes >= 2
    with index.connect(read_only=True) as db:
        rows = db.execute(
            "SELECT evidence_json FROM edges WHERE relation = 'tested_in'"
        ).fetchall()
    assert len(rows) == 1
    evidence = json.loads(rows[0]["evidence_json"])
    assert evidence["title"] == "first evidence"
    assert evidence["evidence_count"] == 2
    assert evidence["source_sections"] == ["Common_Genes", "Pathway_Genes"]


def test_graph_db_source_returns_evidence_item(tmp_path: Path):
    from nutrimaster.rag.graph import GraphDbSource, LocalGraphIndex

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "reg.json").write_text(
        json.dumps(
            {
                "Title": "MYB regulates CHS",
                "Regulation_Genes": [
                    {
                        "Gene_Name": "MYB1",
                        "Species_Latin_Name": "Solanum lycopersicum",
                        "Primary_Regulatory_Targets": "CHS; DFR",
                        "Regulation_Mode": "activation",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "graph.sqlite"
    LocalGraphIndex(db_path).build_from_corpus(corpus)

    items = asyncio.run(GraphDbSource(db_path).search("MYB1 谁调控下游", focus="mechanism"))

    assert len(items) == 1
    assert items[0].source_type == "graph_db"
    assert "MYB1" in items[0].content
    assert "--regulates-->" in items[0].content
    assert items[0].metadata["paths"]
    assert "edges" not in items[0].metadata


def test_graph_relationship_indexes_are_section_and_field_scoped(tmp_path: Path):
    from nutrimaster.rag.graph import LocalGraphIndex

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "mixed.json").write_text(
        json.dumps(
            {
                "Title": "Mixed graph records",
                "DOI": "10.1000/mixed",
                "Pathway_Genes": [
                    {
                        "Gene_Name": "PAL",
                        "Species_Latin_Name": "Arabidopsis thaliana",
                        "Primary_Substrate": "L-phenylalanine",
                        "Primary_Product": "cinnamic acid",
                    }
                ],
                "Regulation_Genes": [
                    {
                        "Gene_Name": "MYB1",
                        "Species_Latin_Name": "Arabidopsis thaliana",
                        "Primary_Regulatory_Targets": "PAL; PAL; CHS",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "graph.sqlite"
    index = LocalGraphIndex(db_path)
    index.build_from_corpus(corpus)

    with index.connect() as db:
        rows = db.execute("SELECT relation, evidence_json FROM edges").fetchall()

    evidence = [json.loads(row["evidence_json"]) for row in rows]
    relationship_indexes = [item["relationship_index"] for item in evidence]
    assert len(relationship_indexes) == len(set(relationship_indexes))
    assert any("Pathway_Genes[0]::Primary_Product[0]::PRODUCES" in value for value in relationship_indexes)
    assert any("Regulation_Genes[0]::Primary_Regulatory_Targets[0]::REGULATES" in value for value in relationship_indexes)
    assert [row["relation"] for row in rows].count("regulates") == 2


def test_rag_service_includes_optional_graph_source():
    from nutrimaster.rag.evidence import EvidenceItem
    from nutrimaster.rag.service import RAGSearchContext, RAGSearchService

    class FakeSource:
        def __init__(self, source_type: str):
            self.source_type = source_type

        async def search(self, query, **kwargs):
            return [
                EvidenceItem(
                    source_id="",
                    source_type=self.source_type,
                    title=f"{self.source_type} result",
                    content=f"{query} evidence",
                    score=1.0,
                )
            ]

    service = RAGSearchService(
        pubmed_source=FakeSource("pubmed"),
        gene_db_source=FakeSource("gene_db"),
        graph_source=FakeSource("graph_db"),
    )

    packet = asyncio.run(service.search("PAL", RAGSearchContext(top_k=5)))

    assert packet.source_counts == {"pubmed": 1, "gene_db": 1, "graph_db": 1}
    assert {item.source_type for item in packet.items} == {"pubmed", "gene_db", "graph_db"}
