from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nutrimaster.experiment.crispr.accession2sequence import _fetch_fasta_text
from nutrimaster.experiment.crispr.pipeline import ExperimentPipeline
from nutrimaster.experiment.gene_transfer.experiment_design import (
    run_gene_transfer_design,
)
from nutrimaster.experiment.gene_transfer.gene2updown import (
    GeneSequenceResult,
    run_gene_transfer_sequences,
    write_gene_flanks,
)
from nutrimaster.experiment.gene_validation import (
    extract_transgenic_species_with_llm,
    verify_genes_with_ncbi,
)
from nutrimaster.experiment.service import (
    MAX_EXPERIMENT_GENES,
    MAX_EXPERIMENT_GOAL_CHARS,
    MAX_GENE_NAME_CHARS,
    MAX_RECIPIENT_SPECIES,
    MAX_SELECTED_GENE_NAMES,
    MAX_SPECIES_NAME_CHARS,
    ExperimentBusyError,
    ExperimentDesignService,
    ExperimentExecutionGate,
    ExperimentInputError,
    GeneTransferDesignService,
    normalize_experiment_genes,
    normalize_experiment_goal,
    normalize_recipient_species,
    normalize_selected_gene_names,
)
from nutrimaster.experiment.resource_limits import (
    ExperimentResourceLimitError,
    NCBISequenceBudget,
)
from nutrimaster.experiment.sop import format_sops
from nutrimaster.web.routes.experiment import (
    _MAX_EXPERIMENT_JSON_BODY_BYTES,
    experiment_preview,
    experiment_run,
    gene_transfer_preview,
    gene_transfer_run,
)


def _json_request(path: str, data: dict) -> Request:
    body = json.dumps(data).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-length", str(len(body)).encode())],
        },
        receive,
    )


@pytest.mark.parametrize(
    ("route", "path"),
    [
        (experiment_preview, "/api/experiment/preview"),
        (experiment_run, "/api/experiment/run"),
        (gene_transfer_preview, "/api/gene-transfer/preview"),
        (gene_transfer_run, "/api/gene-transfer/run"),
    ],
)
def test_all_experiment_routes_reject_declared_oversized_json(route, path):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [
                (
                    b"content-length",
                    str(_MAX_EXPERIMENT_JSON_BODY_BYTES + 1).encode(),
                )
            ],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            route(
                request,
                user=SimpleNamespace(id="user-1"),
                services=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 413


def test_experiment_route_bounds_actual_stream_despite_forged_content_length():
    chunks = [b"{", b"x" * _MAX_EXPERIMENT_JSON_BODY_BYTES, b"}"]

    async def receive():
        chunk = chunks.pop(0)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": bool(chunks),
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/experiment/preview",
            "headers": [(b"content-length", b"2")],
        },
        receive,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            experiment_preview(
                request,
                user=SimpleNamespace(id="user-1"),
                services=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 413


def test_experiment_input_contract_has_explicit_text_and_list_bounds():
    with pytest.raises(ExperimentInputError, match="goal 过长"):
        normalize_experiment_goal("x" * (MAX_EXPERIMENT_GOAL_CHARS + 1))

    with pytest.raises(ExperimentInputError, match="genes 最多包含"):
        normalize_experiment_genes(
            [{"gene": "NRT1", "species": "Oryza sativa"}]
            * (MAX_EXPERIMENT_GENES + 1)
        )

    with pytest.raises(ExperimentInputError, match=r"genes\[0\]\.gene 过长"):
        normalize_experiment_genes(
            [
                {
                    "gene": "x" * (MAX_GENE_NAME_CHARS + 1),
                    "species": "Oryza sativa",
                }
            ]
        )

    with pytest.raises(ExperimentInputError, match=r"genes\[0\]\.species 过长"):
        normalize_experiment_genes(
            [
                {
                    "gene": "NRT1",
                    "species": "x" * (MAX_SPECIES_NAME_CHARS + 1),
                }
            ]
        )

    with pytest.raises(ExperimentInputError, match="selected_gene_names 最多包含"):
        normalize_selected_gene_names(["NRT1"] * (MAX_SELECTED_GENE_NAMES + 1))

    with pytest.raises(ExperimentInputError, match="species_list 最多包含"):
        normalize_recipient_species(
            ["Oryza sativa"] * (MAX_RECIPIENT_SPECIES + 1)
        )


def test_ncbi_sequence_budget_enforces_per_record_and_cumulative_hard_limits(
    monkeypatch,
):
    import nutrimaster.experiment.resource_limits as limits

    monkeypatch.setattr(limits, "MAX_NCBI_SEQUENCE_BASES", 10)
    monkeypatch.setattr(limits, "MAX_NCBI_CUMULATIVE_SEQUENCE_BASES", 15)

    with pytest.raises(ExperimentResourceLimitError, match="per-record"):
        NCBISequenceBudget().consume("A" * 11, label="oversized")

    budget = NCBISequenceBudget()
    budget.consume("A" * 8, label="first")
    with pytest.raises(ExperimentResourceLimitError, match="cumulative"):
        budget.consume("C" * 8, label="second")


def test_ncbi_fasta_download_stops_at_streamed_response_hard_limit(monkeypatch):
    import nutrimaster.experiment.crispr.accession2sequence as downloader

    class Response:
        headers = {}
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b">id\nAAAA"
            yield b"AAAA"

    class Session:
        def get(self, *_args, **kwargs):
            assert kwargs["stream"] is True
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(downloader, "MAX_NCBI_FASTA_RESPONSE_BYTES", 10)
    monkeypatch.setattr(downloader, "_has_env_proxy", lambda: False)
    monkeypatch.setattr(downloader, "_build_session", lambda _proxy: Session())

    with pytest.raises(ExperimentResourceLimitError, match="FASTA response"):
        _fetch_fasta_text("NM_TEST", {"id": "NM_TEST"})


def test_gene_transfer_does_not_downgrade_sequence_limit_to_a_skipped_gene(
    monkeypatch,
):
    exhausted = ExperimentResourceLimitError("sequence cap")

    def reject(**_kwargs):
        raise exhausted

    monkeypatch.setattr(
        "nutrimaster.experiment.gene_transfer.gene2updown.get_gene_flanks",
        reject,
    )

    with pytest.raises(ExperimentResourceLimitError) as caught:
        run_gene_transfer_sequences(
            [{"gene": "NRT1", "species": "Oryza sativa"}]
        )
    assert caught.value is exhausted


def test_gene_transfer_sop_output_enforces_each_and_cumulative_limits(monkeypatch):
    import nutrimaster.experiment.resource_limits as limits
    import nutrimaster.experiment.gene_transfer.experiment_design as design

    result = GeneSequenceResult(
        gene="NRT1",
        species="Oryza sativa",
        accession="NC_TEST",
        gene_seq="",
        upstream_seq="",
        downstream_seq="",
    )
    monkeypatch.setattr(design, "_get_template_text", lambda _species: "x" * 11)
    monkeypatch.setattr(limits, "MAX_SOP_CHARS", 10)
    monkeypatch.setattr(limits, "MAX_CUMULATIVE_SOP_CHARS", 20)
    with pytest.raises(ExperimentResourceLimitError, match="per-document"):
        run_gene_transfer_design([result], ["Oryza sativa"])

    monkeypatch.setattr(design, "_get_template_text", lambda _species: "x" * 8)
    monkeypatch.setattr(limits, "MAX_SOP_CHARS", 10)
    monkeypatch.setattr(limits, "MAX_CUMULATIVE_SOP_CHARS", 12)
    with pytest.raises(ExperimentResourceLimitError, match="cumulative"):
        run_gene_transfer_design(
            [result],
            ["Oryza sativa", "Zea mays"],
        )


def test_formatted_sop_output_counts_headings_against_the_hard_limit(monkeypatch):
    import nutrimaster.experiment.resource_limits as limits
    import nutrimaster.experiment.sop as sop_module

    monkeypatch.setattr(limits, "MAX_SOP_CHARS", 20)
    monkeypatch.setattr(limits, "MAX_CUMULATIVE_SOP_CHARS", 10)
    monkeypatch.setattr(sop_module, "MAX_CUMULATIVE_SOP_CHARS", 10)

    with pytest.raises(ExperimentResourceLimitError, match="formatted SOP"):
        format_sops({"rice": "AAAA"})


def test_crispr_preview_keeps_optional_species_contract(monkeypatch):
    class Pipeline:
        def cleanup(self):
            pass

    monkeypatch.setattr(
        "nutrimaster.experiment.service.verify_genes_with_ncbi",
        lambda genes: [
            {
                **genes[0],
                "ncbi_found": False,
                "gene_ids": [],
            }
        ],
    )
    service = ExperimentDesignService(pipeline_factory=Pipeline)

    result = asyncio.run(
        service.preview(goal="edit NRT1", genes=[{"gene": "NRT1"}])
    )

    assert result == [
        {
            "gene": "NRT1",
            "species": "",
            "ncbi_found": False,
            "gene_ids": [],
        }
    ]


def test_shared_experiment_gate_rejects_cross_service_overlap(monkeypatch):
    started = threading.Event()
    release_worker = threading.Event()

    class BlockingPipeline:
        def extract_genes_with_llm(self, _goal):
            started.set()
            assert release_worker.wait(2)
            return [{"gene": "NRT1", "species": "Oryza sativa"}]

        def cleanup(self):
            pass

    gate = ExperimentExecutionGate()
    crispr = ExperimentDesignService(
        pipeline_factory=BlockingPipeline,
        execution_gate=gate,
    )
    gene_transfer = GeneTransferDesignService(execution_gate=gate)
    monkeypatch.setattr(
        "nutrimaster.experiment.service.verify_genes_with_ncbi",
        lambda genes: genes,
    )

    async def exercise():
        first = asyncio.create_task(crispr.preview(goal="edit NRT1"))
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(ExperimentBusyError, match="实验服务正忙"):
            await gene_transfer.preview_species(goal="express NRT1")
        release_worker.set()
        assert await first == [{"gene": "NRT1", "species": "Oryza sativa"}]

    asyncio.run(exercise())


def test_crispr_gate_stays_held_until_worker_exits_after_sse_disconnect():
    worker_continued = threading.Event()
    release_worker = threading.Event()
    worker_cleaned = threading.Event()

    class BlockingPipeline:
        def run_all_from_genes(self, _genes):
            yield {"type": "progress", "step": 1}
            worker_continued.set()
            assert release_worker.wait(2)
            yield {"type": "result", "sops": {}}

        def cleanup(self):
            worker_cleaned.set()

    gate = ExperimentExecutionGate()
    service = ExperimentDesignService(
        pipeline_factory=BlockingPipeline,
        execution_gate=gate,
    )

    async def exercise():
        events = service.run(
            genes=[{"gene": "NRT1", "species": "Oryza sativa"}]
        )
        assert (await anext(events))["type"] == "progress"
        assert await asyncio.to_thread(worker_continued.wait, 2)
        await events.aclose()

        # Closing the HTTP/SSE iterator cannot stop a synchronous network
        # worker. Capacity remains reserved until that worker actually exits.
        with pytest.raises(ExperimentBusyError, match="实验服务正忙"):
            gate.try_acquire()

        release_worker.set()
        assert await asyncio.to_thread(worker_cleaned.wait, 2)
        for _ in range(100):
            try:
                gate.try_acquire()
            except ExperimentBusyError:
                await asyncio.sleep(0.01)
            else:
                gate.release()
                break
        else:
            pytest.fail("experiment gate was not released after worker exit")

    asyncio.run(exercise())


def test_experiment_routes_map_invalid_bounded_fields_to_400():
    request = _json_request(
        "/api/gene-transfer/run",
        {
            "genes": [{"gene": "NRT1", "species": "Oryza sativa"}],
            "species_list": ["x" * (MAX_SPECIES_NAME_CHARS + 1)],
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            gene_transfer_run(
                request,
                user=SimpleNamespace(id="user-1"),
                services=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 400
    assert "species_list[0] 过长" in exc_info.value.detail


def test_preview_route_propagates_memory_error_unchanged():
    error = MemoryError("allocator pressure")

    class ExhaustedService:
        async def preview(self, **_kwargs):
            raise error

    request = _json_request("/api/experiment/preview", {"goal": "edit NRT1"})
    services = SimpleNamespace(experiment_service=ExhaustedService())

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(
            experiment_preview(
                request,
                user=SimpleNamespace(id="user-1"),
                services=services,
            )
        )

    assert exc_info.value is error


def test_gene_transfer_preview_route_propagates_memory_error_unchanged():
    error = MemoryError("allocator pressure")

    class ExhaustedService:
        async def preview_species(self, **_kwargs):
            raise error

    request = _json_request("/api/gene-transfer/preview", {"goal": "express NRT1"})
    services = SimpleNamespace(gene_transfer_service=ExhaustedService())

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(
            gene_transfer_preview(
                request,
                user=SimpleNamespace(id="user-1"),
                services=services,
            )
        )

    assert exc_info.value is error


def test_experiment_sse_route_does_not_serialize_memory_error():
    error = MemoryError("allocator pressure")

    class ExhaustedService:
        async def run(self, **_kwargs):
            if False:
                yield None
            raise error

    request = _json_request(
        "/api/experiment/run",
        {"genes": [{"gene": "NRT1", "species": "Oryza sativa"}]},
    )
    services = SimpleNamespace(experiment_service=ExhaustedService())

    async def consume():
        response = await experiment_run(
            request,
            user=SimpleNamespace(id="user-1"),
            services=services,
        )
        return [chunk async for chunk in response.body_iterator]

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(consume())

    assert exc_info.value is error


def test_gene_transfer_sse_route_does_not_serialize_memory_error():
    error = MemoryError("allocator pressure")

    class ExhaustedService:
        async def run(self, **_kwargs):
            raise error

    request = _json_request(
        "/api/gene-transfer/run",
        {
            "genes": [{"gene": "NRT1", "species": "Oryza sativa"}],
            "species_list": ["Nicotiana tabacum"],
        },
    )
    services = SimpleNamespace(gene_transfer_service=ExhaustedService())

    async def consume():
        response = await gene_transfer_run(
            request,
            user=SimpleNamespace(id="user-1"),
            services=services,
        )
        return [chunk async for chunk in response.body_iterator]

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(consume())

    assert exc_info.value is error


def test_experiment_service_propagates_worker_memory_error(monkeypatch):
    error = MemoryError("allocator pressure")

    class Pipeline:
        cleaned = False

        def run_all_from_genes(self, _genes):
            if False:
                yield None
            raise error

        def cleanup(self):
            self.cleaned = True

    pipeline = Pipeline()
    service = ExperimentDesignService(pipeline_factory=lambda: pipeline)

    async def consume():
        return [
            event
            async for event in service.run(
                genes=[{"gene": "NRT1", "species": "Oryza sativa"}]
            )
        ]

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(consume())

    assert exc_info.value is error
    assert pipeline.cleaned is True


def test_experiment_service_propagates_memory_error_while_serializing_failure():
    error = MemoryError("allocator pressure")

    class UnserializableFailure(RuntimeError):
        def __str__(self):
            raise error

    class Pipeline:
        def run_all_from_genes(self, _genes):
            if False:
                yield None
            raise UnserializableFailure()

        def cleanup(self):
            pass

    service = ExperimentDesignService(pipeline_factory=Pipeline)

    async def consume():
        return [
            event
            async for event in service.run(
                genes=[{"gene": "NRT1", "species": "Oryza sativa"}]
            )
        ]

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(consume())

    assert exc_info.value is error


def test_crispr_pipeline_never_turns_memory_error_into_error_event(
    monkeypatch,
    tmp_path,
):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(
        "nutrimaster.experiment.crispr.pipeline.step1_gene2acc.run_gene2accession",
        exhaust,
    )
    pipeline = ExperimentPipeline(work_dir=tmp_path)
    events = pipeline.run_all_from_genes(
        [{"gene": "NRT1", "species": "Oryza sativa"}]
    )

    assert next(events)["type"] == "progress"
    with pytest.raises(MemoryError) as exc_info:
        next(events)

    assert exc_info.value is error


def test_gene_transfer_service_propagates_worker_memory_error(monkeypatch):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(
        "nutrimaster.experiment.gene_transfer.gene2updown.run_gene_transfer_sequences",
        exhaust,
    )
    service = GeneTransferDesignService()

    with pytest.raises(MemoryError) as exc_info:
        asyncio.run(
            service.run(
                genes=[{"gene": "NRT1", "species": "Oryza sativa"}],
                species_list=["Nicotiana tabacum"],
            )
        )

    assert exc_info.value is error


def test_species_extraction_propagates_memory_error(monkeypatch):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("nutrimaster.config.llm.call_llm_sync", exhaust)

    with pytest.raises(MemoryError) as exc_info:
        extract_transgenic_species_with_llm("express NRT1 in tobacco")

    assert exc_info.value is error


def test_ncbi_gene_verification_propagates_memory_error(monkeypatch):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(
        "nutrimaster.experiment.crispr.gene2accession._search_gene_ids",
        exhaust,
    )

    with pytest.raises(MemoryError) as exc_info:
        verify_genes_with_ncbi(
            [{"gene": "NRT1", "species": "Oryza sativa"}]
        )

    assert exc_info.value is error


@pytest.mark.parametrize(
    "sequence_runner",
    [write_gene_flanks, run_gene_transfer_sequences],
)
def test_gene_transfer_sequence_boundaries_propagate_memory_error(
    monkeypatch,
    tmp_path,
    sequence_runner,
):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(
        "nutrimaster.experiment.gene_transfer.gene2updown.get_gene_flanks",
        exhaust,
    )
    genes = [{"gene": "NRT1", "species": "Oryza sativa"}]

    with pytest.raises(MemoryError) as exc_info:
        if sequence_runner is write_gene_flanks:
            sequence_runner(genes, tmp_path / "flanks.fasta")
        else:
            sequence_runner(genes)

    assert exc_info.value is error


def test_accession_lookup_propagates_memory_error(monkeypatch, tmp_path):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(
        "nutrimaster.experiment.crispr.gene2accession._find_accession_for_gene",
        exhaust,
    )

    from nutrimaster.experiment.crispr.gene2accession import run_gene2accession

    with pytest.raises(MemoryError) as exc_info:
        run_gene2accession(
            [{"gene": "NRT1", "species": "Oryza sativa"}],
            tmp_path,
        )

    assert exc_info.value is error


def test_crispr_target_lookup_propagates_memory_error(monkeypatch, tmp_path):
    error = MemoryError("allocator pressure")

    def exhaust(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(
        "nutrimaster.experiment.crispr.crispr_target._parse_fasta",
        lambda _path: iter([("Oryza_sativa_NRT1_NM_001", "ACGT")]),
    )
    monkeypatch.setattr(
        "nutrimaster.experiment.crispr.crispr_target._fetch_result_page",
        exhaust,
    )

    from nutrimaster.experiment.crispr.crispr_target import run_crispr_target

    with pytest.raises(MemoryError) as exc_info:
        run_crispr_target([tmp_path / "genes.fasta"], tmp_path)

    assert exc_info.value is error
