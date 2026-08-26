import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nutrimaster.web.routes.pi import (
    _bounded_env_integer,
    _bounded_env_number,
    _capture_sse_events,
    _is_loopback_request,
    _pi_tool_runs,
    _read_bounded_json_object,
    _request_bool,
    _tool_callback_endpoint,
    build_pi_messages,
    pi_query,
)


def test_pi_bridge_keeps_only_text_chat_history_and_appends_query():
    messages = build_pi_messages(
        "最新问题",
        [
            {"role": "system", "content": "不应转发"},
            {"role": "user", "content": "  之前的问题  "},
            {"role": "assistant", "content": "之前的回答"},
            {"role": "tool", "content": "不应转发"},
            "invalid",
        ],
    )

    assert messages == [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "最新问题"},
    ]


def test_pi_tool_bridge_uses_local_callback_and_server_owned_request_state(monkeypatch):
    app = SimpleNamespace(state=SimpleNamespace())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/pi/internal/tools",
            "headers": [],
            "client": ("127.0.0.1", 9000),
            "server": ("127.0.0.1", 5007),
            "app": app,
        }
    )
    services = SimpleNamespace(settings=SimpleNamespace(rag=SimpleNamespace(web_port=5002)))

    assert _pi_tool_runs(request) == {}
    assert _is_loopback_request(request) is True
    assert _tool_callback_endpoint(services) == "http://127.0.0.1:5002/api/pi/internal/tools"
    assert _tool_callback_endpoint(services, request) == "http://127.0.0.1:5007/api/pi/internal/tools"

    monkeypatch.setenv("NUTRIMASTER_PI_TOOL_ENDPOINT", "http://127.0.0.1:5009/api/pi/internal/tools/")
    assert _tool_callback_endpoint(services) == "http://127.0.0.1:5009/api/pi/internal/tools"


def test_pi_tool_bridge_rejects_non_boolean_user_context_flags():
    assert _request_bool({"use_personal": True}, "use_personal") is True
    with pytest.raises(HTTPException) as exc_info:
        _request_bool({"use_depth": "deep"}, "use_depth")
    assert exc_info.value.status_code == 400


def test_pi_numeric_limits_reject_invalid_or_dangerous_environment(monkeypatch):
    monkeypatch.setenv("PI_TEST_LIMIT", "not-a-number")
    with pytest.raises(RuntimeError, match="must be a number"):
        _bounded_env_number("PI_TEST_LIMIT", 8, minimum=1, maximum=32)

    monkeypatch.setenv("PI_TEST_LIMIT", "1000")
    with pytest.raises(RuntimeError, match="between 1 and 32"):
        _bounded_env_number("PI_TEST_LIMIT", 8, minimum=1, maximum=32)

    monkeypatch.setenv("PI_TEST_INTEGER", "1.5")
    with pytest.raises(RuntimeError, match="must be an integer"):
        _bounded_env_integer("PI_TEST_INTEGER", 8, minimum=1, maximum=32)


def test_pi_sse_capture_handles_split_and_multiple_frames():
    class Capture:
        def __init__(self):
            self.events = []

        def capture_event(self, event):
            self.events.append(event)

    capture = Capture()
    buffer, saw_error = _capture_sse_events(
        b"",
        b'data: {"type":"text","data":"hel',
        capture,
    )
    assert capture.events == []
    buffer, saw_error = _capture_sse_events(
        buffer,
        b'lo"}\n\ndata: {"type":"error","data":"x"}\n\n',
        capture,
    )

    assert buffer == b""
    assert saw_error is True
    assert capture.events == [
        {"type": "text", "data": "hello"},
        {"type": "error", "data": "x"},
    ]


def test_pi_bridge_bounds_json_before_parsing():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/pi/query",
            "headers": [(b"content-length", b"1048577")],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_read_bounded_json_object(request, max_bytes=1024 * 1024))

    assert exc_info.value.status_code == 413


def test_pi_route_records_forwarded_sse_and_releases_run(monkeypatch):
    payload = json.dumps(
        {
            "query": "NRT1.1 evidence",
            "history": [{"role": "assistant", "content": "context"}],
            "use_personal": False,
            "use_depth": True,
            "session_id": "session-1",
            "client_turn_id": "turn-1",
            "capture_consent": True,
        }
    ).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    app = SimpleNamespace(state=SimpleNamespace())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/pi/query",
            "headers": [(b"content-length", str(len(payload)).encode())],
            "client": ("127.0.0.1", 9000),
            "server": ("127.0.0.1", 5000),
            "app": app,
        },
        receive,
    )

    class CaptureSession:
        active = True
        interaction_id = "interaction-1"
        turn_id = "turn-1"

        def __init__(self):
            self.events = []
            self.statuses = []

        def capture_event(self, event):
            self.events.append(event)

        def finish(self, status="completed"):
            self.statuses.append(status)

    capture = CaptureSession()

    class Recorder:
        def __init__(self):
            self.kwargs = None

        def start(self, **kwargs):
            self.kwargs = kwargs
            return capture

    recorder = Recorder()
    services = SimpleNamespace(
        registry=SimpleNamespace(),
        settings=SimpleNamespace(rag=SimpleNamespace(web_port=5000)),
        interaction_recorder=recorder,
    )
    upstream_requests = []

    class FakeUpstream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_raw(self):
            yield b'data: {"type":"text","data":"answer"}\n\n'
            yield b'data: {"type":"tool_call","tool":"rag_search"}\n\n'
            yield b'data: {"type":"done"}\n\n'

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, *, json):
            upstream_requests.append((method, url, json))
            return FakeUpstream()

    monkeypatch.setattr("nutrimaster.web.routes.pi.httpx.AsyncClient", FakeClient)

    async def consume():
        response = await pi_query(request, user=SimpleNamespace(id="user-1"), services=services)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
        return b"".join(chunks)

    response_body = asyncio.run(consume())

    assert b'"type":"text"' in response_body
    assert [event["type"] for event in capture.events] == ["text", "tool_call", "done"]
    assert capture.statuses == ["completed"]
    assert recorder.kwargs["session_id"] == "session-1"
    assert recorder.kwargs["client_turn_id"] == "turn-1"
    assert recorder.kwargs["capture_consent"] is True
    assert upstream_requests[0][1] == "http://127.0.0.1:8787/v1/chat/stream"
    assert upstream_requests[0][2]["tool_callback"]["endpoint"].startswith("http://127.0.0.1:5000/")
    assert _pi_tool_runs(request) == {}
