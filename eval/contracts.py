"""Shared lightweight contracts for eval components."""

from typing import Any, Protocol


class EvalAgent(Protocol):
    """Minimum protocol every eval agent must satisfy."""

    name: str

    async def answer(self, question: str) -> dict[str, Any]:
        """Return {"ok": bool, "output": str, "error": str | None}."""
        ...
