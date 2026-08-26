from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class RequestBodyTooLarge(Exception):
    """Internal receive-stream sentinel used by the body-limit middleware."""


class RequestBodyLimitMiddleware:
    """Enforce a byte limit on the actual ASGI receive stream.

    A Content-Length-only check is insufficient for chunked requests and for
    mounted WSGI applications, whose adapter may concatenate the complete body
    before a route-level upload limit runs.
    """

    def __init__(
        self,
        app,
        *,
        max_bytes: int,
        serialized_body_prefixes: Iterable[str] = (),
    ):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = int(max_bytes)
        self.serialized_body_prefixes = tuple(serialized_body_prefixes)
        self._serialized_body_gate = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "").upper()
        serialize_body = method in {"POST", "PUT", "PATCH"} and any(
            path.startswith(prefix) for prefix in self.serialized_body_prefixes
        )
        gate_acquired = False
        if serialize_body:
            gate_acquired = self._serialized_body_gate.acquire(blocking=False)
            if not gate_acquired:
                await self._reject(scope, receive, send, status_code=429)
                return

        try:
            content_length = None
            for name, value in scope.get("headers", []):
                if name.lower() == b"content-length":
                    content_length = value
            if content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except (TypeError, ValueError):
                    await self._reject(scope, receive, send, status_code=400)
                    return
                if declared_bytes < 0:
                    await self._reject(scope, receive, send, status_code=400)
                    return
                if declared_bytes > self.max_bytes:
                    await self._reject(scope, receive, send, status_code=413)
                    return

            received_bytes = 0
            response_started = False

            async def limited_receive():
                nonlocal received_bytes
                message = await receive()
                if message.get("type") == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > self.max_bytes:
                        raise RequestBodyTooLarge
                return message

            async def tracked_send(message):
                nonlocal response_started
                if message.get("type") == "http.response.start":
                    response_started = True
                await send(message)

            try:
                await self.app(scope, limited_receive, tracked_send)
            except RequestBodyTooLarge:
                if response_started:
                    # Headers are already on the wire, so a second response would
                    # violate ASGI. Abort the stream and let the server close it.
                    raise
                await self._reject(scope, receive, send, status_code=413)
        finally:
            if gate_acquired:
                self._serialized_body_gate.release()

    async def _reject(self, scope, receive, send, *, status_code: int) -> None:
        max_mebibytes = self.max_bytes / (1024 * 1024)
        if status_code == 400:
            detail = "Content-Length 无效"
        elif status_code == 429:
            detail = "已有管理写请求正在处理，请稍后重试"
        else:
            detail = f"请求体过大，最大 {max_mebibytes:g}MiB"
        await JSONResponse({"error": detail}, status_code=status_code)(scope, receive, send)


async def read_bounded_json_object(request: Request, *, max_bytes: int) -> dict[str, Any]:
    """Stream and decode one JSON object without buffering an unbounded body."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from None
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="Content-Length 无效")
        if declared_size > max_bytes:
            raise HTTPException(status_code=413, detail="请求体过大")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="请求体过大")
        body.extend(chunk)

    try:
        data = json.loads(body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="请求体必须是合法 JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求体必须是对象")
    return data
