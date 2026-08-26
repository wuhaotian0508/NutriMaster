from __future__ import annotations

import asyncio

from nutrimaster.agent.pi_tools import (
    EXPERIMENT_DESIGN_TOOL,
    PI_TOOL_SCHEMAS,
    PiToolContext,
    PiToolService,
    RAG_SEARCH_TOOL,
)
from nutrimaster.rag.evidence import EvidenceItem, EvidencePacket


class FakeRegistry:
    def __init__(self, results: dict[str, object]):
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return result


def _packet(*, items: list[EvidenceItem] | None = None) -> EvidencePacket:
    return EvidencePacket(
        query="NRT1.1 nitrogen",
        mode="deep",
        items=items or [],
        source_counts={"pubmed": 1, "gene_db": 1, "personal": 1},
        warnings=["PubMed returned one partial match"],
    )


def test_rag_contract_injects_trusted_context_and_serializes_evidence(monkeypatch):
    packet = _packet(
        items=[
            EvidenceItem(
                source_id="7",
                source_type="gene_db",
                title="NRT1.1 controls nitrogen signalling",
                content="Evidence text",
                doi="10.1000/example",
            )
        ]
    )
    registry = FakeRegistry({RAG_SEARCH_TOOL: packet})
    service = PiToolService(registry)
    monkeypatch.setattr(
        "nutrimaster.agent.pi_tools.extract_graph_evidence",
        lambda _: [{"backend": "sqlite", "nodes": [{"id": "NRT1.1"}], "edges": []}],
    )

    payload = asyncio.run(
        service.execute(
            RAG_SEARCH_TOOL,
            {
                "query": " NRT1.1 nitrogen ",
                "top_k": 12,
                # These model-provided values must not affect data access.
                "user_id": "attacker",
                "include_personal": False,
                "mode": "normal",
            },
            PiToolContext(user_id="authenticated-user", include_personal=True, mode="deep"),
        )
    )

    assert payload["ok"] is True
    assert payload["citations"][0]["doi"] == "10.1000/example"
    assert payload["graph_evidence"] == [{"backend": "sqlite", "nodes": [{"id": "NRT1.1"}], "edges": []}]
    assert payload["source_counts"] == {"pubmed": 1, "gene_db": 1, "personal": 1}
    assert payload["warnings"] == ["PubMed returned one partial match"]
    assert "[1] NRT1.1 controls nitrogen signalling" in payload["tool_text"]

    name, call = registry.calls[0]
    assert name == RAG_SEARCH_TOOL
    assert call["user_id"] == "authenticated-user"
    assert call["include_personal"] is True
    assert call["mode"] == "deep"
    assert call["query"] == "NRT1.1 nitrogen"


def test_rag_contract_preserves_empty_results():
    registry = FakeRegistry({RAG_SEARCH_TOOL: _packet()})
    payload = asyncio.run(
        PiToolService(registry).execute(
            RAG_SEARCH_TOOL,
            {"query": "unseen gene"},
            PiToolContext(user_id="user-1"),
        )
    )

    assert payload["ok"] is True
    assert payload["citations"] == []
    assert payload["graph_evidence"] == []
    assert "未找到可用证据" in payload["tool_text"]


def test_rag_contract_preserves_partial_source_failures_and_warnings():
    packet = EvidencePacket(
        query="NRT1.1",
        mode="normal",
        items=[
            EvidenceItem(
                source_id="1",
                source_type="gene_db",
                title="Local NRT1.1 record",
                content="Local evidence",
            )
        ],
        source_counts={"pubmed": 0, "gene_db": 1},
        warnings=["PubMed 未返回结果；如需重试，请重新调用 rag_search 并提供英文 pubmed_query。"],
    )
    payload = asyncio.run(
        PiToolService(FakeRegistry({RAG_SEARCH_TOOL: packet})).execute(
            RAG_SEARCH_TOOL,
            {"query": "NRT1.1"},
            PiToolContext(user_id="user-1"),
        )
    )

    assert payload["ok"] is True
    assert payload["source_counts"] == {"pubmed": 0, "gene_db": 1}
    assert payload["citations"][0]["source_type"] == "gene_db"
    assert payload["warnings"] == ["PubMed 未返回结果；如需重试，请重新调用 rag_search 并提供英文 pubmed_query。"]


def test_rag_contract_keeps_citations_stable_across_a_pi_run():
    paper_a = EvidenceItem(
        source_id="1",
        source_type="pubmed",
        title="Paper A",
        content="A",
        doi="10.1000/a",
    )
    paper_b = EvidenceItem(
        source_id="2",
        source_type="gene_db",
        title="Paper B",
        content="B",
        doi="10.1000/b",
    )
    registry = FakeRegistry(
        {
            RAG_SEARCH_TOOL: [
                EvidencePacket(query="first", mode="normal", items=[paper_a]),
                EvidencePacket(query="second", mode="normal", items=[paper_a, paper_b]),
            ]
        }
    )

    async def _execute(name: str, **kwargs):
        registry.calls.append((name, kwargs))
        return registry.results[name].pop(0)

    registry.execute = _execute
    service = PiToolService(registry)
    first = asyncio.run(service.execute(RAG_SEARCH_TOOL, {"query": "first"}, PiToolContext(user_id="user-1")))
    second = asyncio.run(service.execute(RAG_SEARCH_TOOL, {"query": "second"}, PiToolContext(user_id="user-1")))

    assert [citation["source_id"] for citation in first["citations"]] == ["1"]
    assert [citation["source_id"] for citation in second["citations"]] == ["1", "2"]


def test_rag_contract_returns_stable_errors_without_invoking_registry():
    registry = FakeRegistry({RAG_SEARCH_TOOL: _packet()})
    service = PiToolService(registry)

    invalid = asyncio.run(service.execute(RAG_SEARCH_TOOL, {"query": ""}, PiToolContext(user_id="user-1")))
    unknown = asyncio.run(service.execute("shell", {}, PiToolContext(user_id="user-1")))

    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_arguments"
    assert unknown["error"]["code"] == "unknown_tool"
    assert registry.calls == []


def test_rag_contract_bounds_top_k_and_keyword_spec_size():
    registry = FakeRegistry({RAG_SEARCH_TOOL: _packet()})
    service = PiToolService(registry)

    too_many = asyncio.run(
        service.execute(
            RAG_SEARCH_TOOL,
            {"query": "NRT1.1", "top_k": 101},
            PiToolContext(user_id="user-1"),
        )
    )
    too_large = asyncio.run(
        service.execute(
            RAG_SEARCH_TOOL,
            {"query": "NRT1.1", "gene_db_keyword_spec": "x" * 40_000},
            PiToolContext(user_id="user-1"),
        )
    )

    assert too_many["error"]["code"] == "invalid_arguments"
    assert too_large["error"]["code"] == "invalid_arguments"
    assert registry.calls == []


def test_pi_tool_contract_bounds_model_generated_text_and_gene_cardinality():
    registry = FakeRegistry(
        {
            RAG_SEARCH_TOOL: _packet(),
            EXPERIMENT_DESIGN_TOOL: "preview",
        }
    )
    service = PiToolService(registry)

    long_query = asyncio.run(
        service.execute(
            RAG_SEARCH_TOOL,
            {"query": "x" * 16_001},
            PiToolContext(user_id="user-1"),
        )
    )
    too_many_genes = asyncio.run(
        service.execute(
            EXPERIMENT_DESIGN_TOOL,
            {
                "experiment_type": "crispr",
                "goal": "edit nitrate uptake",
                "genes": [{"gene": f"NRT{index}"} for index in range(51)],
            },
            PiToolContext(user_id="user-1"),
        )
    )

    assert long_query["error"]["code"] == "invalid_arguments"
    assert too_many_genes["error"]["code"] == "invalid_arguments"
    assert registry.calls == []


def test_experiment_contract_preserves_preview_and_complete_stages():
    registry = FakeRegistry(
        {
            EXPERIMENT_DESIGN_TOOL: "实验设计预览：请确认基因和物种。",
        }
    )
    service = PiToolService(registry)

    preview = asyncio.run(
        service.execute(
            EXPERIMENT_DESIGN_TOOL,
            {
                "experiment_type": "crispr",
                "goal": "敲除水稻 NRT1.1",
                "genes": [{"gene": " NRT1.1 ", "species": " Oryza sativa "}],
            },
            PiToolContext(user_id="user-1"),
        )
    )
    complete = asyncio.run(
        service.execute(
            EXPERIMENT_DESIGN_TOOL,
            {
                "experiment_type": "gene_transfer",
                "goal": "过表达 OsNRT1.1",
                "output": "full_sop",
                "confirmed": True,
            },
            PiToolContext(user_id="user-1"),
        )
    )

    assert preview["ok"] is True
    assert preview["stage"] == "preview"
    assert complete["stage"] == "complete"
    first_name, first_call = registry.calls[0]
    assert first_name == EXPERIMENT_DESIGN_TOOL
    assert first_call["genes"] == [{"gene": "NRT1.1", "species": "Oryza sativa"}]


def test_experiment_contract_validates_confirmation_and_masks_runtime_errors():
    registry = FakeRegistry({EXPERIMENT_DESIGN_TOOL: RuntimeError("secret backend detail")})
    service = PiToolService(registry)

    invalid = asyncio.run(
        service.execute(
            EXPERIMENT_DESIGN_TOOL,
            {"experiment_type": "crispr", "goal": "x", "confirmed": "yes"},
            PiToolContext(user_id="user-1"),
        )
    )
    failed = asyncio.run(
        service.execute(
            EXPERIMENT_DESIGN_TOOL,
            {"experiment_type": "crispr", "goal": "x"},
            PiToolContext(user_id="user-1"),
        )
    )

    assert invalid["error"]["code"] == "invalid_arguments"
    assert failed["error"]["code"] == "tool_execution_failed"
    assert "secret backend detail" not in failed["error"]["message"]


def test_pi_tool_schemas_exclude_trusted_request_context():
    rag_properties = PI_TOOL_SCHEMAS[RAG_SEARCH_TOOL]["properties"]
    experiment_properties = PI_TOOL_SCHEMAS[EXPERIMENT_DESIGN_TOOL]["properties"]

    assert PI_TOOL_SCHEMAS[RAG_SEARCH_TOOL]["required"] == ["query"]
    assert PI_TOOL_SCHEMAS[EXPERIMENT_DESIGN_TOOL]["required"] == ["experiment_type", "goal"]
    assert {"user_id", "include_personal", "mode"}.isdisjoint(rag_properties)
    assert set(experiment_properties) >= {"experiment_type", "goal", "genes", "output", "confirmed"}


def test_personal_library_context_requires_authenticated_user():
    try:
        PiToolContext(user_id=None, include_personal=True)
    except ValueError as exc:
        assert getattr(exc, "code") == "invalid_context"
    else:
        raise AssertionError("personal-library context should require a user id")
