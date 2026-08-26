from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from openai import InternalServerError
from starlette.requests import Request

from nutrimaster.config import llm as llm_config
from nutrimaster.experiment import llm as experiment_llm
from nutrimaster.experiment.llm import ExperimentUnavailableError
from nutrimaster.web.routes.experiment import (
    experiment_preview,
    gene_transfer_preview,
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


def test_sync_llm_call_honors_explicit_model_override(monkeypatch):
    captured = {}

    class Completions:
        def create(self, **params):
            captured.update(params)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()),
    )
    monkeypatch.setattr(llm_config, "_DEFAULT_SYNC_CLIENT", client)
    monkeypatch.setattr(
        llm_config.Settings,
        "from_env",
        classmethod(lambda cls: SimpleNamespace(model="main-model")),
    )

    result = llm_config.call_llm_sync(
        [{"role": "user", "content": "hello"}],
        model="experiment-model",
        temperature=0,
    )

    assert result.content == "ok"
    assert captured["model"] == "experiment-model"


def test_experiment_llm_uses_experiment_model(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        experiment_llm.Settings,
        "from_env",
        classmethod(
            lambda cls: SimpleNamespace(experiment_model="experiment-model")
        ),
    )

    def fake_call(messages, **kwargs):
        captured["messages"] = messages
        captured.update(kwargs)
        return SimpleNamespace(content="[]")

    monkeypatch.setattr(experiment_llm.llm_config, "call_llm_sync", fake_call)

    experiment_llm.call_experiment_llm(
        [{"role": "user", "content": "extract"}],
        temperature=0,
    )

    assert captured["model"] == "experiment-model"
    assert captured["temperature"] == 0


def test_model_not_found_is_sanitized_as_experiment_unavailable(monkeypatch):
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    response = httpx.Response(503, request=request)
    upstream = InternalServerError(
        "gateway request id: secret-internal-id",
        response=response,
        body={
            "error": {
                "code": "model_not_found",
                "message": "no distributor; request id: secret-internal-id",
            }
        },
    )
    monkeypatch.setattr(
        experiment_llm.Settings,
        "from_env",
        classmethod(
            lambda cls: SimpleNamespace(experiment_model="missing-model")
        ),
    )
    monkeypatch.setattr(
        experiment_llm.llm_config,
        "call_llm_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(upstream),
    )

    with pytest.raises(ExperimentUnavailableError) as exc_info:
        experiment_llm.call_experiment_llm(
            [{"role": "user", "content": "extract"}]
        )

    assert "EXPERIMENT_MODEL" in str(exc_info.value)
    assert "secret-internal-id" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("route", "service_name", "method_name", "path"),
    [
        (experiment_preview, "experiment_service", "preview", "/api/experiment/preview"),
        (
            gene_transfer_preview,
            "gene_transfer_service",
            "preview_species",
            "/api/gene-transfer/preview",
        ),
    ],
)
def test_preview_routes_map_experiment_unavailable_to_503(
    route,
    service_name,
    method_name,
    path,
):
    async def unavailable(**_kwargs):
        raise ExperimentUnavailableError(
            "实验模型当前不可用，请检查 EXPERIMENT_MODEL 或稍后重试"
        )

    service = SimpleNamespace(**{method_name: unavailable})
    services = SimpleNamespace(**{service_name: service})
    request = _json_request(path, {"goal": "express NRT1 in tobacco"})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            route(
                request,
                user=SimpleNamespace(id="user-1"),
                services=services,
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == (
        "实验模型当前不可用，请检查 EXPERIMENT_MODEL 或稍后重试"
    )
