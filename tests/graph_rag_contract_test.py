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


def test_extract_graph_evidence_handles_graph_metadata_and_missing_fields():
    from nutrimaster.rag.evidence import EvidenceItem, EvidencePacket, extract_graph_evidence

    packet = EvidencePacket(
        query="MYB1",
        mode="normal",
        items=[
            EvidenceItem(
                source_id="1",
                source_type="graph_db",
                title="Graph neighborhood",
                content="graph text",
                metadata={
                    "backend": "sqlite",
                    "seeds": [{"id": "gene-1", "name": "MYB1", "type": "Gene"}],
                    "nodes": [
                        {"id": "gene-1", "name": "MYB1", "type": "Gene"},
                        {"id": "gene-2", "name": "CHS", "type": "Gene"},
                    ],
                    "edges": [
                        {
                            "id": "edge-1",
                            "src": "gene-1",
                            "dst": "gene-2",
                            "relation": "regulates",
                            "evidence": {"doi": "10.1000/myb", "summary": "MYB1 regulates CHS."},
                        }
                    ],
                    "direction": "downstream",
                    "hops": 2,
                },
            ),
            EvidenceItem(
                source_id="2",
                source_type="graph_db",
                title="Broken graph",
                content="missing graph payload",
                metadata={"backend": "sqlite"},
            ),
        ],
    )

    graphs = extract_graph_evidence(packet)

    assert len(graphs) == 1
    assert graphs[0]["backend"] == "sqlite"
    assert graphs[0]["direction"] == "downstream"
    assert graphs[0]["hops"] == 2
    assert graphs[0]["nodes"][0]["name"] == "MYB1"
    assert graphs[0]["edges"][0]["evidence"]["doi"] == "10.1000/myb"


def test_agent_emits_graph_evidence_for_rag_packet():
    from nutrimaster.agent.agent import Agent
    from nutrimaster.rag.evidence import EvidenceItem, EvidencePacket

    class FakePromptBuilder:
        def build(self, **kwargs):
            return "system"

    class FakeRegistry:
        tool_names = {"rag_search"}
        get_definitions = []

        async def execute(self, name, **kwargs):
            return EvidencePacket(
                query=kwargs["query"],
                mode="normal",
                items=[
                    EvidenceItem(
                        source_id="",
                        source_type="graph_db",
                        title="Graph neighborhood",
                        content="graph text",
                        metadata={
                            "backend": "sqlite",
                            "seeds": [{"id": "a", "name": "MYB1", "type": "Gene"}],
                            "nodes": [
                                {"id": "a", "name": "MYB1", "type": "Gene"},
                                {"id": "b", "name": "CHS", "type": "Gene"},
                            ],
                            "edges": [{"id": "ab", "src": "a", "dst": "b", "relation": "regulates"}],
                            "direction": "downstream",
                            "hops": 1,
                        },
                    )
                ],
                source_counts={"graph_db": 1},
            )

    calls = {"count": 0}

    async def fake_llm(messages, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "rag_search",
                            "arguments": json.dumps({"query": "MYB1"}),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "MYB1 regulates CHS [1]."}

    agent = Agent(FakeRegistry(), skill_loader=None, call_llm=fake_llm, prompt_builder=FakePromptBuilder())
    events = asyncio.run(_collect_async(agent.run("MYB1")))

    graph_events = [event for event in events if event["type"] == "graph_evidence"]
    assert len(graph_events) == 1
    assert graph_events[0]["data"][0]["edges"][0]["relation"] == "regulates"


async def _collect_async(stream):
    return [event async for event in stream]


def test_graph_query_extracts_target_species_and_field_hints():
    from nutrimaster.rag.graph import extract_graph_query

    graph_query = extract_graph_query(
        "HY5 如何影响 lycopene in Solanum lycopersicum",
        mode="deep",
        focus="pathway",
    )

    assert graph_query.entities == ("HY5",)
    assert graph_query.target_entities == ("lycopene",)
    assert graph_query.species == "Solanum lycopersicum"
    assert graph_query.direction == "downstream"
    assert graph_query.max_hops == 4


def test_neo4j_graph_source_renders_path_evidence_without_live_neo4j():
    from nutrimaster.rag.graph import GraphQuery, Neo4jGraphSource, ResolvedNode
    from nutrimaster.rag.graph.path_search import (
        GraphPath,
        GraphPathNode,
        GraphPathRelationship,
        GraphPathSearchResult,
    )

    class FakeSearcher:
        def search(self, query, *, top_k, mode, focus):
            graph_query = GraphQuery(
                raw_query=query,
                entities=("HY5",),
                target_entities=("lycopene",),
                direction="downstream",
                max_hops=4,
            )
            return GraphPathSearchResult(
                graph_query=graph_query,
                starts=(ResolvedNode(id="hy5", name="HY5", type="Gene", species="Tomato", score=1.0),),
                targets=(ResolvedNode(id="lyc", name="lycopene", type="Metabolite", score=1.0),),
                paths=(
                    GraphPath(
                        nodes=(
                            GraphPathNode(id="hy5", name="HY5", type="Gene", species="Tomato"),
                            GraphPathNode(id="psy1", name="PSY1", type="Gene", species="Tomato"),
                        ),
                        relationships=(
                            GraphPathRelationship(
                                id="edge-1",
                                source_id="hy5",
                                target_id="psy1",
                                type="REGULATES",
                                evidence={
                                    "doi": "10.1000/hy5",
                                    "domain": "regulation",
                                    "mode": "activation",
                                    "validation": "promoter binding",
                                    "summary": "HY5 activates PSY1.",
                                },
                            ),
                        ),
                        score=1.2,
                        search_kind="between",
                    ),
                ),
            )

    items = asyncio.run(Neo4jGraphSource(searcher=FakeSearcher()).search("HY5 如何影响 lycopene"))

    assert len(items) == 1
    assert items[0].metadata["backend"] == "neo4j"
    assert "HY5 (Gene), Tomato -[:REGULATES]-> PSY1 (Gene), Tomato" in items[0].content
    assert "evidence=10.1000/hy5" in items[0].content
