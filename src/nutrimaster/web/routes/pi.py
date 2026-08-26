from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from nutrimaster.agent.pi_tools import PiToolContext, PiToolService
from nutrimaster.auth.service import get_current_user
from nutrimaster.web.deps import SSE_HEADERS, WebServices, get_services, sse

router = APIRouter()
_DEFAULT_RUNTIME_URL = "http://127.0.0.1:8787"
_PI_RUNS_STATE_KEY = "pi_tool_runs"
_PI_RUN_TOKEN_HEADER = "X-NutriMaster-Pi-Run"
_MAX_CAPTURE_FRAME_BYTES = 1024 * 1024
_MAX_QUERY_BODY_BYTES = 1024 * 1024
_MAX_TOOL_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class PiToolRun:
    """One authenticated Pi turn and its server-owned tool context."""

    tool_service: PiToolService
    context: PiToolContext
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False, repr=False)
    revoked: asyncio.Event = field(default_factory=asyncio.Event, compare=False, repr=False)

    def revoke(self) -> None:
        self.revoked.set()


def build_pi_messages(query: str, history: list) -> list[dict[str, str]]:
    """Translate the current web chat payload into Pi's text-only transcript."""
    messages: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    messages.append({"role": "user", "content": query})
    return messages


def _request_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{key} 必须是布尔值")
    return value


def _bounded_env_number(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_env_integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


async def _read_bounded_json_object(request: Request, *, max_bytes: int) -> dict[str, Any]:
    """Read one JSON object without allowing an authenticated request to allocate an unbounded body."""
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


def _capture_sse_events(
    buffer: bytes | bytearray,
    chunk: bytes,
    capture_session,
) -> tuple[bytearray, bool]:
    """Decode complete data-only SSE frames while forwarding bytes unchanged."""
    if not isinstance(buffer, bytearray):
        buffer = bytearray(buffer)
    buffer.extend(chunk)
    saw_error = False
    while True:
        lf_offset = buffer.find(b"\n\n")
        crlf_offset = buffer.find(b"\r\n\r\n")
        offsets = [offset for offset in (lf_offset, crlf_offset) if offset >= 0]
        if not offsets:
            if len(buffer) > _MAX_CAPTURE_FRAME_BYTES:
                buffer.clear()
            break
        offset = min(offsets)
        separator_size = 4 if buffer[offset:offset + 4] == b"\r\n\r\n" else 2
        frame = bytes(buffer[:offset])
        del buffer[:offset + separator_size]
        data_lines = []
        for line in frame.splitlines():
            if line.startswith(b"data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        try:
            event = json.loads(b"\n".join(data_lines))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            capture_session.capture_event(event)
            saw_error = saw_error or event.get("type") == "error"
    return buffer, saw_error


def _pi_tool_runs(request: Request) -> dict[str, PiToolRun]:
    runs = getattr(request.app.state, _PI_RUNS_STATE_KEY, None)
    if runs is None:
        runs = {}
        setattr(request.app.state, _PI_RUNS_STATE_KEY, runs)
    now = time.monotonic()
    for token, run in list(runs.items()):
        if run.expires_at <= now:
            run.revoke()
            runs.pop(token, None)
    return runs


def _is_loopback_request(request: Request) -> bool:
    client = request.client
    if client is None or client.host not in {"127.0.0.1", "::1"}:
        return False
    # A public request proxied by Nginx also arrives from a loopback socket.
    # The production proxy denies this path, and rejecting forwarding headers
    # here provides an independent application-level fail-closed boundary.
    return not any(
        request.headers.get(name)
        for name in ("forwarded", "x-forwarded-for", "x-real-ip")
    )


def _revoke_pi_tool_run(request: Request, token: str, run: PiToolRun) -> None:
    run.revoke()
    runs = getattr(request.app.state, _PI_RUNS_STATE_KEY, None)
    if isinstance(runs, dict) and runs.get(token) is run:
        runs.pop(token, None)


async def _wait_for_disconnect(request: Request) -> None:
    """Wait for the ASGI disconnect that follows an already-consumed body."""
    while True:
        try:
            message = await request.receive()
        except asyncio.CancelledError:
            raise
        except MemoryError:
            raise
        except Exception:
            # A receive-channel failure means the callback client is gone even
            # if the server could not produce an explicit http.disconnect.
            return
        if message.get("type") == "http.disconnect":
            return
        await asyncio.sleep(0)


async def _cancel_task(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _tool_callback_endpoint(services: WebServices, request: Request | None = None) -> str:
    configured = os.getenv("NUTRIMASTER_PI_TOOL_ENDPOINT", "").strip()
    if configured:
        return configured.rstrip("/")
    port = None
    if request is not None:
        server = request.scope.get("server")
        if isinstance(server, (tuple, list)) and len(server) >= 2:
            port = server[1]
    if not isinstance(port, int) or port <= 0:
        port = services.settings.rag.web_port if services.settings.rag else 5000
    return f"http://127.0.0.1:{port}/api/pi/internal/tools"


@router.post("/api/pi/query")
async def pi_query(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """Authenticated SSE bridge to Pi with server-owned tool capabilities."""
    data = await _read_bounded_json_object(request, max_bytes=_MAX_QUERY_BODY_BYTES)
    query_value = data.get("query")
    if not isinstance(query_value, str) or not query_value.strip():
        raise HTTPException(status_code=400, detail="查询不能为空")
    query_text = query_value.strip()
    history = data.get("history", []) or []
    if not isinstance(history, list):
        raise HTTPException(status_code=400, detail="history 必须是数组")
    include_personal = _request_bool(data, "use_personal")
    use_depth = _request_bool(data, "use_depth")
    capture_consent = _request_bool(data, "capture_consent")
    max_active_runs = _bounded_env_integer(
        "NUTRIMASTER_PI_MAX_ACTIVE_RUNS",
        8,
        minimum=1,
        maximum=32,
    )
    turn_timeout = _bounded_env_number(
        "NUTRIMASTER_PI_TURN_TIMEOUT_SECONDS",
        300,
        minimum=1,
        maximum=900,
    )

    runtime_url = os.getenv("NUTRIMASTER_PI_RUNTIME_URL", _DEFAULT_RUNTIME_URL).rstrip("/")
    context = PiToolContext(
        user_id=str(getattr(user, "id", "") or "") or None,
        include_personal=include_personal,
        mode="deep" if use_depth else "normal",
    )
    run_token = secrets.token_urlsafe(32)
    runs = _pi_tool_runs(request)
    if len(runs) >= max_active_runs:
        raise HTTPException(status_code=503, detail="Pi 当前请求较多，请稍后重试")
    run = PiToolRun(
        PiToolService(services.registry),
        context,
        expires_at=time.monotonic() + turn_timeout + 60.0,
    )
    messages = build_pi_messages(query_text, history)
    if len(messages) > 50 or sum(len(message["content"]) for message in messages) > 100_000:
        raise HTTPException(status_code=400, detail="对话历史过长，请新建会话后重试")
    payload = {
        "messages": messages,
        "tool_callback": {
            "endpoint": _tool_callback_endpoint(services, request),
            "token": run_token,
        },
    }
    capture_session = services.interaction_recorder.start(
        user_id=context.user_id,
        session_id=data.get("session_id") or "",
        client_turn_id=data.get("client_turn_id") or "",
        query=query_text,
        model_id=(os.getenv("NUTRIMASTER_PI_MODEL") or os.getenv("MAIN_MODEL") or "pi-runtime"),
        history=history,
        initial_messages=payload["messages"],
        use_personal=include_personal,
        use_depth=use_depth,
        capture_consent=capture_consent,
    )
    runs[run_token] = run

    finished = False

    def finish_once(status: str, *, persist_capture: bool = True) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        _revoke_pi_tool_run(request, run_token, run)
        if persist_capture:
            capture_session.finish(status=status)

    async def generate() -> AsyncIterator[str | bytes]:
        status = "completed"
        capture_buffer = bytearray()
        memory_exhausted = False
        try:
            yield sse(
                {
                    "type": "capture",
                    "enabled": capture_session.active,
                    "interaction_id": capture_session.interaction_id if capture_session.active else "",
                    "turn_id": capture_session.turn_id,
                }
            )
            timeout = httpx.Timeout(timeout=None, connect=5.0)
            async with asyncio.timeout(turn_timeout):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", f"{runtime_url}/v1/chat/stream", json=payload) as upstream:
                        if upstream.status_code != 200:
                            status = "error"
                            event = {"type": "error", "data": "Pi runtime 暂不可用，请稍后重试"}
                            capture_session.capture_event(event)
                            yield sse(event)
                            yield sse({"type": "done"})
                            return
                        async for chunk in upstream.aiter_raw():
                            capture_buffer, saw_error = _capture_sse_events(
                                capture_buffer,
                                chunk,
                                capture_session,
                            )
                            if saw_error:
                                status = "error"
                            yield chunk
        except TimeoutError:
            status = "error"
            event = {"type": "error", "data": "Pi 请求超时，请缩小问题范围后重试"}
            capture_session.capture_event(event)
            yield sse(event)
            yield sse({"type": "done"})
        except httpx.HTTPError:
            status = "error"
            event = {"type": "error", "data": "Pi runtime 暂不可用，请稍后重试"}
            capture_session.capture_event(event)
            yield sse(event)
            yield sse({"type": "done"})
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except MemoryError:
            # Revoke the callback immediately and avoid asking the recorder to
            # allocate/serialize another interaction payload under pressure.
            status = "error"
            memory_exhausted = True
            raise
        finally:
            finish_once(status, persist_capture=not memory_exhausted)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
        background=BackgroundTask(finish_once, "cancelled"),
    )


@router.post("/api/pi/internal/tools")
async def execute_pi_tool(request: Request):
    """Execute a Pi tool through an unforgeable, short-lived local capability."""
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Pi tool bridge only accepts localhost requests")

    run_token = request.headers.get(_PI_RUN_TOKEN_HEADER, "")
    run = _pi_tool_runs(request).get(run_token)
    if run is None:
        raise HTTPException(status_code=401, detail="Pi tool capability is invalid or expired")

    data = await _read_bounded_json_object(request, max_bytes=_MAX_TOOL_BODY_BYTES)
    tool = data.get("tool")
    if not isinstance(tool, str) or not tool:
        raise HTTPException(status_code=400, detail="tool 必须是非空字符串")
    arguments = data.get("arguments", {})
    if not isinstance(arguments, dict):
        raise HTTPException(status_code=400, detail="arguments 必须是对象")

    # The Pi SDK declares all custom tools sequential.  Reject a replayed
    # concurrent callback instead of queuing another expensive retrieval or
    # experiment behind the same leaked capability.
    if run.lock.locked():
        raise HTTPException(status_code=409, detail="Pi tool capability is already in use")

    tool_timeout = _bounded_env_number(
        "NUTRIMASTER_PI_TOOL_TIMEOUT_SECONDS",
        120,
        minimum=1,
        maximum=300,
    )
    try:
        async with run.lock:
            # Revalidate after the streamed request body and lock acquisition;
            # a slow body must not extend a capability beyond its expiry or
            # resurrect a run already removed by the outer SSE request.
            if _pi_tool_runs(request).get(run_token) is not run:
                raise HTTPException(
                    status_code=401,
                    detail="Pi tool capability is invalid or expired",
                )
            remaining_lifetime = run.expires_at - time.monotonic()
            if remaining_lifetime <= 0:
                _revoke_pi_tool_run(request, run_token, run)
                raise HTTPException(
                    status_code=401,
                    detail="Pi tool capability is invalid or expired",
                )

            tool_task = asyncio.create_task(
                run.tool_service.execute(tool, arguments, run.context)
            )
            disconnect_task = asyncio.create_task(_wait_for_disconnect(request))
            revoked_task = asyncio.create_task(run.revoked.wait())
            try:
                done, _ = await asyncio.wait(
                    {tool_task, disconnect_task, revoked_task},
                    timeout=min(tool_timeout, remaining_lifetime),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if tool_task in done:
                    payload = await tool_task
                    return JSONResponse(payload)

                await _cancel_task(tool_task)
                if revoked_task in done or time.monotonic() >= run.expires_at:
                    _revoke_pi_tool_run(request, run_token, run)
                    raise HTTPException(
                        status_code=401,
                        detail="Pi tool capability is invalid or expired",
                    )
                if disconnect_task in done:
                    _revoke_pi_tool_run(request, run_token, run)
                    raise HTTPException(status_code=499, detail="Pi tool callback disconnected")
                raise HTTPException(status_code=504, detail="Pi tool execution timed out")
            finally:
                await _cancel_task(disconnect_task)
                await _cancel_task(revoked_task)
                if not tool_task.done():
                    await _cancel_task(tool_task)
    except (asyncio.CancelledError, MemoryError):
        # Cancellation and allocator exhaustion are process-safety signals,
        # never normal model-visible tool errors. Revoke first, then preserve
        # the original exception identity for the ASGI/server boundary.
        _revoke_pi_tool_run(request, run_token, run)
        raise
