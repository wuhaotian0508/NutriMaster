"""Hard in-process bounds for sequence and SOP materialization.

These limits are deliberately constants rather than operator-tunable values.
The unified Web service must remain safe even when its environment is copied
from an older deployment or controlled by an untrusted launcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


# DNA is ASCII, so character counts closely track the retained payload bytes.
MAX_NCBI_SEQUENCE_BASES = 1_000_000
MAX_NCBI_CUMULATIVE_SEQUENCE_BASES = 8_000_000
# Allow bounded FASTA headers and line separators in addition to the bases.
MAX_NCBI_FASTA_RESPONSE_BYTES = MAX_NCBI_SEQUENCE_BASES + 64 * 1024

MAX_SOP_CHARS = 4_000_000
MAX_CUMULATIVE_SOP_CHARS = 8_000_000


class ExperimentResourceLimitError(RuntimeError):
    """Raised before an experiment workflow retains an oversized payload."""


@dataclass
class NCBISequenceBudget:
    """Track per-record and cumulative NCBI sequence characters."""

    used: int = 0

    def consume_length(self, length: int, *, label: str) -> None:
        if length < 0:
            raise ValueError("sequence length cannot be negative")
        if length > MAX_NCBI_SEQUENCE_BASES:
            raise ExperimentResourceLimitError(
                f"NCBI sequence {label!r} exceeds the hard per-record limit "
                f"of {MAX_NCBI_SEQUENCE_BASES} bases"
            )
        if length > MAX_NCBI_CUMULATIVE_SEQUENCE_BASES - self.used:
            raise ExperimentResourceLimitError(
                "NCBI sequences exceed the hard cumulative limit of "
                f"{MAX_NCBI_CUMULATIVE_SEQUENCE_BASES} bases"
            )
        self.used += length

    def consume(self, sequence: str, *, label: str) -> None:
        self.consume_length(len(sequence), label=label)


@dataclass
class SOPOutputBudget:
    """Track each SOP and the complete response without duplicating content."""

    used: int = 0

    def consume(self, text: str, *, label: str) -> None:
        if not isinstance(text, str):
            raise ExperimentResourceLimitError(f"SOP {label!r} must be text")
        length = len(text)
        if length > MAX_SOP_CHARS:
            raise ExperimentResourceLimitError(
                f"SOP {label!r} exceeds the hard per-document limit of "
                f"{MAX_SOP_CHARS} characters"
            )
        if length > MAX_CUMULATIVE_SOP_CHARS - self.used:
            raise ExperimentResourceLimitError(
                "SOP output exceeds the hard cumulative limit of "
                f"{MAX_CUMULATIVE_SOP_CHARS} characters"
            )
        self.used += length


def validate_sop_output(sops: Mapping[str, str]) -> None:
    """Validate an already-produced SOP mapping as a defense-in-depth check."""
    if not isinstance(sops, Mapping):
        raise ExperimentResourceLimitError("SOP output must be a mapping")
    budget = SOPOutputBudget()
    for label, value in sops.items():
        budget.consume(value, label=str(label))


__all__ = [
    "ExperimentResourceLimitError",
    "MAX_CUMULATIVE_SOP_CHARS",
    "MAX_NCBI_CUMULATIVE_SEQUENCE_BASES",
    "MAX_NCBI_FASTA_RESPONSE_BYTES",
    "MAX_NCBI_SEQUENCE_BASES",
    "MAX_SOP_CHARS",
    "NCBISequenceBudget",
    "SOPOutputBudget",
    "validate_sop_output",
]
