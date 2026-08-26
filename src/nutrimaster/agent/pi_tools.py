"""Transport-neutral tool contract for the Pi agent runtime.

This module is deliberately independent from FastAPI and the Node Pi runtime.
The web/runtime owner can expose it over an internal HTTP endpoint, a process
bridge, or an in-process call without changing the RAG or experiment logic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from nutrimaster.rag.evidence import CitationRegistry, EvidencePacket, extract_graph_evidence

logger = logging.getLogger(__name__)

RAG_SEARCH_TOOL = "rag_search"
EXPERIMENT_DESIGN_TOOL = "experiment_design"
MAX_RAG_TOP_K = 100
MAX_KEYWORD_SPEC_BYTES = 32 * 1024
MAX_RAG_QUERY_CHARS = 16_000
MAX_AUX_QUERY_CHARS = 8_000
MAX_FOCUS_CHARS = 256
MAX_EXPERIMENT_GOAL_CHARS = 16_000
MAX_EXPERIMENT_GENES = 50
MAX_GENE_NAME_CHARS = 128
MAX_SPECIES_NAME_CHARS = 256


def _nested_memory_error(exc: BaseException) -> MemoryError | None:
    if isinstance(exc, MemoryError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            exhausted = _nested_memory_error(nested)
            if exhausted is not None:
                return exhausted
    return None


class PiToolContractError(ValueError):
    """A caller-visible validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PiToolContext:
    """Trusted request metadata supplied by the authenticated application.

    It must never be constructed from model-generated tool arguments.  The
    adapter intentionally ignores any context-like fields in ``params``.
    """

    user_id: str | None
    include_personal: bool = False
    mode: str = "normal"

    def __post_init__(self) -> None:
        if self.mode not in {"normal", "deep"}:
            raise PiToolContractError("invalid_context", "mode must be normal or deep")
        if self.include_personal and not self.user_id:
            raise PiToolContractError(
                "invalid_context",
                "user_id is required when include_personal is enabled",
            )


@dataclass(frozen=True)
class PiToolResult:
    """JSON-safe result returned to the Pi runtime adapter."""

    tool: str
    ok: bool
    tool_text: str = ""
    summary: str = ""
    citations: tuple[dict[str, Any], ...] = ()
    graph_evidence: tuple[dict[str, Any], ...] = ()
    source_counts: Mapping[str, int] | None = None
    warnings: tuple[str, ...] = ()
    stage: str = ""
    error: Mapping[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the stable payload consumed by a Pi extension or HTTP bridge."""
        payload: dict[str, Any] = {
            "tool": self.tool,
            "ok": self.ok,
            "tool_text": self.tool_text,
            "summary": self.summary,
            "citations": list(self.citations),
            "graph_evidence": list(self.graph_evidence),
            "source_counts": dict(self.source_counts or {}),
            "warnings": list(self.warnings),
        }
        if self.stage:
            payload["stage"] = self.stage
        if self.error is not None:
            payload["error"] = dict(self.error)
        return payload


# These schemas describe only model-provided arguments.  PiToolContext is
# always injected by the authenticated application and is never part of this
# public tool schema.
PI_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    RAG_SEARCH_TOOL: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "pubmed_query": {"type": "string"},
            "gene_db_query": {"type": "string"},
            "gene_db_keyword_spec": {"type": ["object", "array", "string", "null"]},
            "focus": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_RAG_TOP_K},
        },
        "required": ["query"],
        "additionalProperties": True,
    },
    EXPERIMENT_DESIGN_TOOL: {
        "type": "object",
        "properties": {
            "experiment_type": {"type": "string", "enum": ["crispr", "gene_transfer"]},
            "goal": {"type": "string", "minLength": 1},
            "genes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "gene": {"type": "string"},
                        "species": {"type": "string"},
                    },
                    "required": ["gene"],
                },
            },
            "output": {"type": "string", "enum": ["advice", "full_sop"]},
            "confirmed": {"type": "boolean"},
        },
        "required": ["experiment_type", "goal"],
        "additionalProperties": True,
    },
}


class PiToolService:
    """Adapt existing high-level NutriMaster tools to the Pi result contract.

    Create one instance for each Pi agent run.  It owns a CitationRegistry so
    repeated ``rag_search`` calls within that run preserve global numbering and
    de-duplicate papers, exactly like the legacy Python Agent.
    """

    def __init__(self, registry: Any, *, citation_registry: CitationRegistry | None = None):
        self.registry = registry
        self.citation_registry = citation_registry or CitationRegistry()

    async def execute(
        self,
        tool: str,
        params: Mapping[str, Any] | None,
        context: PiToolContext,
    ) -> dict[str, Any]:
        """Execute one supported tool and return a JSON-serializable payload.

        Expected validation failures are encoded in the result, so a transport
        layer can return the same contract for HTTP 4xx and Pi tool errors.
        Unexpected service failures are logged server-side and intentionally
        expose only a generic message to the model/client.
        """
        try:
            if tool == RAG_SEARCH_TOOL:
                return (await self._rag_search(params, context)).to_dict()
            if tool == EXPERIMENT_DESIGN_TOOL:
                return (await self._experiment_design(params)).to_dict()
            raise PiToolContractError("unknown_tool", f"Unsupported Pi tool: {tool}")
        except PiToolContractError as exc:
            return self._error_result(tool, exc.code, exc.message).to_dict()
        except MemoryError:
            # Allocator exhaustion is a process-safety condition, not a normal
            # tool failure.  Do not allocate/log a synthetic tool payload and
            # let the Pi loop continue consuming memory.
            raise
        except Exception as exc:
            exhausted = _nested_memory_error(exc)
            if exhausted is not None:
                raise exhausted from exc
            logger.exception("Pi tool execution failed: %s", tool)
            return self._error_result(
                tool,
                "tool_execution_failed",
                "工具执行失败，请稍后重试。",
            ).to_dict()

    async def _rag_search(
        self,
        params: Mapping[str, Any] | None,
        context: PiToolContext,
    ) -> PiToolResult:
        values = _mapping(params)
        query = _required_text(values, "query", maximum=MAX_RAG_QUERY_CHARS)
        pubmed_query = _optional_text(values, "pubmed_query", maximum=MAX_AUX_QUERY_CHARS)
        gene_db_query = _optional_text(values, "gene_db_query", maximum=MAX_AUX_QUERY_CHARS)
        focus = _optional_text(values, "focus", maximum=MAX_FOCUS_CHARS) or "general"
        top_k = _positive_int(values.get("top_k", 10), "top_k", maximum=MAX_RAG_TOP_K)
        keyword_spec = values.get("gene_db_keyword_spec")
        if keyword_spec is not None and not isinstance(keyword_spec, (dict, list, str)):
            raise PiToolContractError(
                "invalid_arguments",
                "gene_db_keyword_spec must be an object, array, string, or null",
            )
        if keyword_spec is not None:
            try:
                encoded_spec = json.dumps(
                    keyword_spec,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise PiToolContractError(
                    "invalid_arguments",
                    "gene_db_keyword_spec must contain finite JSON values",
                ) from exc
            if len(encoded_spec) > MAX_KEYWORD_SPEC_BYTES:
                raise PiToolContractError(
                    "invalid_arguments",
                    f"gene_db_keyword_spec must not exceed {MAX_KEYWORD_SPEC_BYTES} bytes",
                )

        # Never read user_id/include_personal/mode from model-generated params.
        result = await self.registry.execute(
            RAG_SEARCH_TOOL,
            query=query,
            pubmed_query=pubmed_query,
            gene_db_query=gene_db_query,
            gene_db_keyword_spec=keyword_spec,
            focus=focus,
            top_k=top_k,
            user_id=context.user_id,
            include_personal=context.include_personal,
            mode=context.mode,
        )
        if not isinstance(result, EvidencePacket):
            raise PiToolContractError("invalid_tool_result", "rag_search returned an invalid evidence packet")

        result = self.citation_registry.assign_packet(result)
        tool_text = result.to_tool_text()
        graph_evidence = extract_graph_evidence(result)
        return PiToolResult(
            tool=RAG_SEARCH_TOOL,
            ok=True,
            tool_text=tool_text,
            summary=_summary(tool_text),
            citations=tuple(result.citations),
            graph_evidence=tuple(graph_evidence),
            source_counts=result.source_counts,
            warnings=tuple(result.warnings),
        )

    async def _experiment_design(self, params: Mapping[str, Any] | None) -> PiToolResult:
        values = _mapping(params)
        experiment_type = _required_text(values, "experiment_type")
        if experiment_type not in {"crispr", "gene_transfer"}:
            raise PiToolContractError("invalid_arguments", "experiment_type must be crispr or gene_transfer")
        goal = _required_text(values, "goal", maximum=MAX_EXPERIMENT_GOAL_CHARS)
        genes = _genes(values.get("genes"))
        output = _optional_text(values, "output") or "advice"
        if output not in {"advice", "full_sop"}:
            raise PiToolContractError("invalid_arguments", "output must be advice or full_sop")
        confirmed = values.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise PiToolContractError("invalid_arguments", "confirmed must be a boolean")

        result = await self.registry.execute(
            EXPERIMENT_DESIGN_TOOL,
            experiment_type=experiment_type,
            goal=goal,
            genes=genes,
            output=output,
            confirmed=confirmed,
        )
        tool_text = str(result)
        stage = "complete" if output == "full_sop" and confirmed else "preview"
        return PiToolResult(
            tool=EXPERIMENT_DESIGN_TOOL,
            ok=True,
            tool_text=tool_text,
            summary=_summary(tool_text),
            stage=stage,
        )

    @staticmethod
    def _error_result(tool: str, code: str, message: str) -> PiToolResult:
        return PiToolResult(
            tool=tool,
            ok=False,
            summary=message,
            error={"code": code, "message": message},
        )


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PiToolContractError("invalid_arguments", "tool arguments must be an object")
    return value


def _required_text(
    values: Mapping[str, Any],
    field: str,
    *,
    maximum: int | None = None,
) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PiToolContractError("invalid_arguments", f"{field} must be non-empty text")
    if maximum is not None and len(value) > maximum:
        raise PiToolContractError(
            "invalid_arguments",
            f"{field} must not exceed {maximum} characters",
        )
    return value.strip()


def _optional_text(
    values: Mapping[str, Any],
    field: str,
    *,
    maximum: int | None = None,
) -> str:
    value = values.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PiToolContractError("invalid_arguments", f"{field} must be text")
    if maximum is not None and len(value) > maximum:
        raise PiToolContractError(
            "invalid_arguments",
            f"{field} must not exceed {maximum} characters",
        )
    return value.strip()


def _positive_int(value: Any, field: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PiToolContractError("invalid_arguments", f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise PiToolContractError("invalid_arguments", f"{field} must be at most {maximum}")
    return value


def _genes(value: Any) -> list[dict[str, str]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise PiToolContractError("invalid_arguments", "genes must be an array")
    if len(value) > MAX_EXPERIMENT_GENES:
        raise PiToolContractError(
            "invalid_arguments",
            f"genes must not contain more than {MAX_EXPERIMENT_GENES} items",
        )
    output: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise PiToolContractError("invalid_arguments", f"genes[{index}] must be an object")
        gene = item.get("gene")
        species = item.get("species", "")
        if not isinstance(gene, str) or not gene.strip():
            raise PiToolContractError("invalid_arguments", f"genes[{index}].gene must be non-empty text")
        if not isinstance(species, str):
            raise PiToolContractError("invalid_arguments", f"genes[{index}].species must be text")
        if len(gene) > MAX_GENE_NAME_CHARS:
            raise PiToolContractError(
                "invalid_arguments",
                f"genes[{index}].gene must not exceed {MAX_GENE_NAME_CHARS} characters",
            )
        if len(species) > MAX_SPECIES_NAME_CHARS:
            raise PiToolContractError(
                "invalid_arguments",
                f"genes[{index}].species must not exceed {MAX_SPECIES_NAME_CHARS} characters",
            )
        output.append({"gene": gene.strip(), "species": species.strip()})
    return output


def _summary(text: str, limit: int = 500) -> str:
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


__all__ = [
    "EXPERIMENT_DESIGN_TOOL",
    "PI_TOOL_SCHEMAS",
    "PiToolContext",
    "PiToolContractError",
    "PiToolResult",
    "PiToolService",
    "RAG_SEARCH_TOOL",
]
