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

    result = index.neighborhood("PAL downstream product", hops=2, direction="downstream")

    genes = [node for node in result.nodes if node["type"] == "Gene" and node["name"] == "PAL"]
    assert len(genes) == 1
    assert genes[0]["species"] == "Arabidopsis thaliana"
    assert {edge["relation"] for edge in result.edges} >= {"catalyzes", "produces"}


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
    assert items[0].metadata["edges"]


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
