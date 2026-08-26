from __future__ import annotations

from typing import Any

from openai import APIConnectionError, APIStatusError

from nutrimaster.config import llm as llm_config
from nutrimaster.config.settings import Settings


_UNAVAILABLE_MESSAGE = "实验模型当前不可用，请检查 EXPERIMENT_MODEL 或稍后重试"


class ExperimentUnavailableError(RuntimeError):
    """Raised when the configured experiment model cannot serve a request."""


def _is_model_not_found(exc: BaseException) -> bool:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    return isinstance(error, dict) and error.get("code") == "model_not_found"


def _is_upstream_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or _is_model_not_found(exc)
    return False


def call_experiment_llm(messages: Any, **kwargs: Any):
    """Call the explicitly configured experiment model through the shared client."""
    model = Settings.from_env().experiment_model
    if not model:
        raise ExperimentUnavailableError(_UNAVAILABLE_MESSAGE)
    try:
        return llm_config.call_llm_sync(messages, model=model, **kwargs)
    except MemoryError:
        raise
    except Exception as exc:
        if _is_upstream_unavailable(exc):
            raise ExperimentUnavailableError(_UNAVAILABLE_MESSAGE) from exc
        raise


__all__ = ["ExperimentUnavailableError", "call_experiment_llm"]
