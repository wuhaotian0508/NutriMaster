from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.requests import Request

from nutrimaster.web.request_limits import RequestBodyLimitMiddleware, read_bounded_json_object
from nutrimaster.web.routes.query import (
    _rag_top_k,
    _required_query_text,
    _validated_history,
    feedback,
    query,
    rag_search_debug,
)


def _request_from_chunks(chunks: list[bytes], *, content_length: int | str | None = None) -> Request:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    index = 0

    async def receive():
        nonlocal index
        if index >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        body = chunks[index]
        index += 1
        return {
            "type": "http.request",
            "body": body,
            "more_body": index < len(chunks),
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
        },
        receive,
    )


def _json_request(payload: object) -> Request:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request_from_chunks([body], content_length=len(body))


def _run_body_limit(chunks: list[bytes], *, max_bytes: int, content_length=None):
    messages = []
    index = 0

    async def receive():
        nonlocal index
        if index >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        body = chunks[index]
        index += 1
        return {
            "type": "http.request",
            "body": body,
            "more_body": index < len(chunks),
        }

    async def send(message):
        messages.append(message)

    async def consume_entire_body(scope, downstream_receive, downstream_send):
        more_body = True
        while more_body:
            message = await downstream_receive()
            more_body = message.get("more_body", False)
        await JSONResponse({"status": "ok"})(scope, downstream_receive, downstream_send)

    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/admin/api/upload",
        "raw_path": b"/admin/api/upload",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 5000),
    }
    middleware = RequestBodyLimitMiddleware(consume_entire_body, max_bytes=max_bytes)
    asyncio.run(middleware(scope, receive, send))
    return messages


def test_bounded_json_reader_rejects_declared_and_streamed_oversize_bodies():
    declared = _request_from_chunks([], content_length=11)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(read_bounded_json_object(declared, max_bytes=10))
    assert exc_info.value.status_code == 413


def test_global_asgi_limit_rejects_chunked_body_before_wsgi_can_buffer_it():
    messages = _run_body_limit([b"123456", b"78901"], max_bytes=10)
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    assert response_start["status"] == 413


def test_global_asgi_limit_rejects_declared_oversize_without_reading_body():
    messages = _run_body_limit([], max_bytes=10, content_length=11)
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    assert response_start["status"] == 413

    chunked = _request_from_chunks([b'{"x":"', b"12345", b'"}'])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(read_bounded_json_object(chunked, max_bytes=10))
    assert exc_info.value.status_code == 413


@pytest.mark.parametrize(
    "body, detail",
    [
        (b"not-json", "请求体必须是合法 JSON"),
        (b"[]", "请求体必须是对象"),
    ],
)
def test_bounded_json_reader_rejects_invalid_json_shapes(body, detail):
    request = _request_from_chunks([body])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(read_bounded_json_object(request, max_bytes=1024))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == detail


def test_query_and_history_types_and_character_limits_are_enforced():
    with pytest.raises(HTTPException, match="query 必须是字符串"):
        _required_query_text({"query": ["not", "text"]})
    with pytest.raises(HTTPException, match="16000"):
        _required_query_text({"query": "x" * 16_001})

    assert len(_validated_history({"history": [{"role": "user", "content": "x"}] * 50})) == 50
    with pytest.raises(HTTPException, match="50"):
        _validated_history({"history": [{"role": "user", "content": "x"}] * 51})
    with pytest.raises(HTTPException, match="history 必须是数组"):
        _validated_history({"history": {"role": "user", "content": "x"}})
    with pytest.raises(HTTPException, match="history content 必须是字符串"):
        _validated_history({"history": [{"role": "user", "content": ["x"]}]})
    with pytest.raises(HTTPException, match="100000"):
        _validated_history({"history": [{"role": "user", "content": "x" * 100_001}]})


@pytest.mark.parametrize("value", [0, 51, -1, True, None, 1.5, "many"])
def test_rag_top_k_rejects_values_outside_one_to_fifty(value):
    with pytest.raises(HTTPException) as exc_info:
        _rag_top_k({"top_k": value})
    assert exc_info.value.status_code == 400


def test_rag_top_k_keeps_default_and_numeric_string_compatibility():
    assert _rag_top_k({}) == 10
    assert _rag_top_k({"top_k": 1}) == 1
    assert _rag_top_k({"top_k": "50"}) == 50


def test_query_route_rejects_history_before_starting_agent():
    request = _json_request({"query": "NRT1.1", "history": "not-a-list"})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(query(request, user=SimpleNamespace(id="user-1"), services=SimpleNamespace()))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "history 必须是数组"


def test_rag_route_bounds_auxiliary_queries_and_passes_valid_top_k():
    class RagTool:
        def __init__(self):
            self.kwargs = None

        async def execute(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                query=kwargs["query"],
                mode=kwargs["mode"],
                source_counts={"local": 1},
                citations=[],
                items=[],
                to_tool_text=lambda: "result",
            )

    rag_tool = RagTool()
    services = SimpleNamespace(registry=SimpleNamespace(get=lambda name: rag_tool if name == "rag_search" else None))
    request = _json_request(
        {
            "query": "NRT1.1 evidence",
            "pubmed_query": "NRT1.1",
            "gene_db_query": "NRT1.1",
            "top_k": 50,
        }
    )

    response = asyncio.run(rag_search_debug(request, user=SimpleNamespace(id="user-1"), services=services))

    assert response.status_code == 200
    assert rag_tool.kwargs["top_k"] == 50
    assert rag_tool.kwargs["pubmed_query"] == "NRT1.1"

    too_long = _json_request({"query": "NRT1.1", "pubmed_query": "x" * 8_001})
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(rag_search_debug(too_long, user=SimpleNamespace(id="user-1"), services=services))
    assert exc_info.value.status_code == 400
    assert "pubmed_query" in exc_info.value.detail


def test_feedback_uses_the_same_bounded_reader():
    request = _request_from_chunks([], content_length=1024 * 1024 + 1)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(feedback(request, user=SimpleNamespace(id="user-1"), services=SimpleNamespace()))
    assert exc_info.value.status_code == 413


def test_global_asgi_limit_serializes_wsgi_admin_write_bodies():
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_first(scope, receive, send):
        await receive()
        first_started.set()
        await release_first.wait()
        await JSONResponse({"status": "ok"})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        hold_first,
        max_bytes=1024,
        serialized_body_prefixes=("/admin/",),
    )

    def request_io():
        sent = False
        messages = []

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message):
            messages.append(message)

        return receive, send, messages

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/admin/api/upload",
        "raw_path": b"/admin/api/upload",
        "query_string": b"",
        "headers": [(b"content-length", b"2")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 5000),
    }

    async def exercise():
        receive_one, send_one, _ = request_io()
        first = asyncio.create_task(middleware(scope, receive_one, send_one))
        await first_started.wait()

        receive_two, send_two, second_messages = request_io()
        await middleware(scope, receive_two, send_two)
        second_start = next(
            message
            for message in second_messages
            if message["type"] == "http.response.start"
        )
        assert second_start["status"] == 429

        release_first.set()
        await first

    asyncio.run(exercise())


def test_nginx_rag_search_has_the_same_rate_and_connection_limits_as_agent_queries():
    root = Path(__file__).resolve().parents[2]
    source = (root / "deploy" / "nginx" / "nutrimaster-unified.conf").read_text(encoding="utf-8")
    server_blocks = source.split("\nserver {")[1:]
    bohrium_server = next(block for block in server_blocks if "listen 127.0.0.1:5080;" in block)
    public_server = next(block for block in server_blocks if "listen 443 ssl;" in block)

    for server, rate_zone, connection_zone in (
        (bohrium_server, "nutrimaster_bohrium_agent_rate", "nutrimaster_bohrium_agent_conn"),
        (public_server, "nutrimaster_agent_rate", "nutrimaster_agent_conn"),
    ):
        rag_location = server.split("location = /api/rag/search {", 1)[1].split("}", 1)[0]
        assert "client_max_body_size 1M;" in rag_location
        assert f"limit_req zone={rate_zone} burst=5 nodelay;" in rag_location
        assert f"limit_conn {connection_zone} 2;" in rag_location
