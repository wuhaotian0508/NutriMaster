from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nutrimaster.agent.pi_tools import PiToolContext
from nutrimaster.web.routes.pi import (
    PiToolRun,
    _is_loopback_request,
    _pi_tool_runs,
    _read_bounded_json_object,
    execute_pi_tool,
)


ROOT = Path(__file__).resolve().parents[2]
RUN_HEADER = b"x-nutrimaster-pi-run"


def _callback_request(
    app,
    token: str,
    *,
    disconnect: asyncio.Event | None = None,
    body: dict | None = None,
) -> Request:
    payload = json.dumps(
        body or {"tool": "rag_search", "arguments": {"query": "NRT1.1"}}
    ).encode()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        if disconnect is None:
            await asyncio.Future()
        else:
            await disconnect.wait()
        return {"type": "http.disconnect"}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/pi/internal/tools",
            "headers": [
                (b"content-length", str(len(payload)).encode()),
                (RUN_HEADER, token.encode()),
            ],
            "client": ("127.0.0.1", 8787),
            "server": ("127.0.0.1", 5000),
            "app": app,
        },
        receive,
    )


def test_internal_callback_rejects_proxy_forwarding_headers_even_from_loopback():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/pi/internal/tools",
            "headers": [(b"x-forwarded-for", b"203.0.113.8")],
            "client": ("127.0.0.1", 443),
        }
    )

    assert _is_loopback_request(request) is False

    nginx = (ROOT / "deploy/nginx/nutrimaster-unified.conf").read_text(encoding="utf-8")
    assert "location = /api/pi/internal {" in nginx
    assert "location ^~ /api/pi/internal/ {" in nginx


def test_pi_reader_rejects_negative_content_length():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/pi/query",
            "headers": [(b"content-length", b"-1")],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_read_bounded_json_object(request, max_bytes=1024))

    assert exc_info.value.status_code == 400


def test_one_run_token_cannot_execute_tools_concurrently():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        token = "run-token-" + "x" * 40
        started = asyncio.Event()
        release = asyncio.Event()

        class ToolService:
            async def execute(self, *_args):
                started.set()
                await release.wait()
                return {"ok": True, "tool": "rag_search"}

        run = PiToolRun(
            ToolService(),
            PiToolContext(user_id="user-1"),
            expires_at=time.monotonic() + 60,
        )
        setattr(app.state, "pi_tool_runs", {token: run})
        first = asyncio.create_task(execute_pi_tool(_callback_request(app, token)))
        await started.wait()

        with pytest.raises(HTTPException) as exc_info:
            await execute_pi_tool(_callback_request(app, token))
        assert exc_info.value.status_code == 409

        release.set()
        response = await first
        assert json.loads(response.body)["ok"] is True

    asyncio.run(scenario())


def test_callback_disconnect_cancels_tool_and_revokes_run_token():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        token = "run-token-" + "x" * 40
        disconnect = asyncio.Event()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class ToolService:
            async def execute(self, *_args):
                started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        run = PiToolRun(
            ToolService(),
            PiToolContext(user_id="user-1"),
            expires_at=time.monotonic() + 60,
        )
        setattr(app.state, "pi_tool_runs", {token: run})
        request = _callback_request(app, token, disconnect=disconnect)
        executing = asyncio.create_task(execute_pi_tool(request))
        await started.wait()
        disconnect.set()

        with pytest.raises(HTTPException) as exc_info:
            await executing
        assert exc_info.value.status_code == 499
        assert cancelled.is_set()
        assert run.revoked.is_set()
        assert _pi_tool_runs(request) == {}

    asyncio.run(scenario())


def test_run_token_expiry_cancels_in_flight_tool():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        token = "run-token-" + "x" * 40
        cancelled = asyncio.Event()

        class ToolService:
            async def execute(self, *_args):
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        run = PiToolRun(
            ToolService(),
            PiToolContext(user_id="user-1"),
            expires_at=time.monotonic() + 0.02,
        )
        setattr(app.state, "pi_tool_runs", {token: run})
        request = _callback_request(app, token)

        with pytest.raises(HTTPException) as exc_info:
            await execute_pi_tool(request)
        assert exc_info.value.status_code == 401
        assert cancelled.is_set()
        assert run.revoked.is_set()
        assert _pi_tool_runs(request) == {}

    asyncio.run(scenario())


def test_tool_route_preserves_memory_error_identity_and_revokes_token():
    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        token = "run-token-" + "x" * 40
        exhausted = MemoryError("allocator pressure")

        class ToolService:
            async def execute(self, *_args):
                raise exhausted

        run = PiToolRun(
            ToolService(),
            PiToolContext(user_id="user-1"),
            expires_at=time.monotonic() + 60,
        )
        setattr(app.state, "pi_tool_runs", {token: run})
        request = _callback_request(app, token)

        with pytest.raises(MemoryError) as exc_info:
            await execute_pi_tool(request)
        assert exc_info.value is exhausted
        assert run.revoked.is_set()
        assert _pi_tool_runs(request) == {}

    asyncio.run(scenario())
