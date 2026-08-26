import http from "node:http";
import { fileURLToPath } from "node:url";
import { TextDecoder } from "node:util";

import {
  configuredModelId,
  boundedIntegerEnv,
  modelDescriptor,
  RequestValidationError,
  streamChat,
  validateToolCallback,
} from "./runtime.js";

const MAX_BODY_BYTES = 1_048_576;
const MAX_PENDING_SSE_BYTES = 4 * 1_048_576;
const port = boundedIntegerEnv("NUTRIMASTER_PI_PORT", 8787, 1, 65_535);
const host = process.env.NUTRIMASTER_PI_HOST || "127.0.0.1";

function sendJson(response, statusCode, body) {
  response.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body));
}

function writeSse(response, event) {
  if (response.destroyed || response.writableEnded) return false;
  const frame = `data: ${JSON.stringify(event)}\n\n`;
  const frameBytes = Buffer.byteLength(frame);
  if (frameBytes > MAX_PENDING_SSE_BYTES
      || response.writableLength + frameBytes > MAX_PENDING_SSE_BYTES) {
    return false;
  }
  // A false return value only means the small writable high-water mark was
  // crossed.  Keep streaming while the explicitly bounded pending-byte budget
  // still has room; the turn is cancelled before that budget can be exceeded.
  response.write(frame);
  return true;
}

function declaredBodyBytes(request) {
  const value = request.headers?.["content-length"];
  if (value === undefined) return null;
  if (Array.isArray(value) || !/^\d+$/.test(value)) {
    throw new RequestValidationError("Content-Length must be a non-negative integer");
  }
  const size = Number(value);
  if (!Number.isSafeInteger(size)) {
    throw new RequestValidationError("Content-Length must be a non-negative integer");
  }
  if (size > MAX_BODY_BYTES) {
    throw new RequestValidationError("request body exceeds 1 MiB");
  }
  return size;
}

export async function readJson(request) {
  declaredBodyBytes(request);
  const chunks = [];
  let totalBytes = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    totalBytes += bytes.byteLength;
    if (totalBytes > MAX_BODY_BYTES) {
      throw new RequestValidationError("request body exceeds 1 MiB");
    }
    chunks.push(bytes);
  }

  let body;
  try {
    // Decode once after all byte chunks have been joined.  Per-chunk implicit
    // Buffer-to-string conversion corrupts a UTF-8 code point split at a TCP
    // boundary, and JavaScript string length is not a network byte limit.
    body = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, totalBytes));
  } catch {
    throw new RequestValidationError("request body must be valid UTF-8");
  }
  try {
    return JSON.parse(body || "{}");
  } catch {
    throw new RequestValidationError("request body must be valid JSON");
  }
}

function corsHeaders() {
  const allowedOrigin = process.env.NUTRIMASTER_PI_ALLOWED_ORIGIN;
  return {
    ...(allowedOrigin ? { "Access-Control-Allow-Origin": allowedOrigin } : {}),
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  };
}

function abortError(message, name = "AbortError") {
  const error = new Error(message);
  error.name = name;
  return error;
}

function createTurnAbortContext(request, response, timeoutMs) {
  const controller = new AbortController();
  let completed = false;

  const abort = (reason) => {
    if (!completed && !controller.signal.aborted) controller.abort(reason);
  };
  const onRequestAborted = () => abort(abortError("Pi request body was aborted"));
  const onRequestClose = () => {
    // IncomingMessage emits `close` after a normally consumed request body on
    // modern Node versions. Only an incomplete/aborted body is a disconnect;
    // a later client disconnect is observed on ServerResponse below.
    if (request.aborted || !request.complete) {
      abort(abortError("Pi request connection closed"));
    }
  };
  const onResponseClose = () => {
    // ServerResponse also emits `close` after response.end(). writableEnded
    // distinguishes that normal path from a client disappearing mid-stream.
    if (!response.writableEnded) {
      abort(abortError("Pi response connection closed"));
    }
  };

  request.once("aborted", onRequestAborted);
  request.once("close", onRequestClose);
  response.once("close", onResponseClose);

  const timeout = setTimeout(() => {
    abort(abortError("Pi runtime turn timed out", "TimeoutError"));
  }, timeoutMs);
  timeout.unref?.();

  const cleanup = () => {
    clearTimeout(timeout);
    request.removeListener("aborted", onRequestAborted);
    request.removeListener("close", onRequestClose);
    response.removeListener("close", onResponseClose);
  };

  return {
    signal: controller.signal,
    abort,
    complete() {
      completed = true;
      cleanup();
    },
  };
}

async function handleStream(request, response, { streamChatImpl, turnTimeoutMs }) {
  const turn = createTurnAbortContext(request, response, turnTimeoutMs);
  response.writeHead(200, {
    ...corsHeaders(),
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });

  try {
    const body = await readJson(request);
    if (body.model && body.model !== modelDescriptor()) {
      throw new RequestValidationError(`model must be ${modelDescriptor()}`);
    }
    const toolCallback = validateToolCallback(body.tool_callback);
    if (toolCallback) {
      writeSse(response, { type: "tools_enabled", tools: ["rag_search", "experiment_design"] });
    }
    await streamChatImpl(
      body.messages,
      (event) => {
        if (!writeSse(response, event)) {
          turn.abort(abortError("Pi response backpressure limit exceeded"));
        }
      },
      toolCallback,
      { signal: turn.signal },
    );
    writeSse(response, { type: "done" });
  } catch (error) {
    const timedOut = turn.signal.aborted && turn.signal.reason?.name === "TimeoutError";
    if (!turn.signal.aborted) console.error("Pi runtime request failed", error);
    if (!response.destroyed && !response.writableEnded) {
      const message = timedOut
        ? "Pi runtime request timed out"
        : error instanceof RequestValidationError
          ? error.message
          : "Pi runtime request failed";
      writeSse(response, { type: "error", data: message });
      writeSse(response, { type: "done" });
    }
  } finally {
    // Remove close listeners before response.end(): its normal `close` event
    // must not be mistaken for a disconnected client.
    turn.complete();
    if (!response.destroyed && !response.writableEnded) response.end();
  }
}

export function createServer(options = {}) {
  const streamChatImpl = options.streamChatImpl || streamChat;
  const turnTimeoutMs = options.turnTimeoutMs
    ?? boundedIntegerEnv("NUTRIMASTER_PI_TURN_TIMEOUT_SECONDS", 300, 1, 900) * 1000;
  const maxActiveTurns = options.maxActiveTurns
    ?? boundedIntegerEnv("NUTRIMASTER_PI_MAX_ACTIVE_RUNS", 8, 1, 32);
  if (!Number.isSafeInteger(turnTimeoutMs) || turnTimeoutMs < 1) {
    throw new Error("turnTimeoutMs must be a positive integer");
  }
  if (!Number.isSafeInteger(maxActiveTurns) || maxActiveTurns < 1 || maxActiveTurns > 32) {
    throw new Error("maxActiveTurns must be an integer between 1 and 32");
  }
  let activeTurns = 0;
  return http.createServer(async (request, response) => {
    const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
    if (request.method === "OPTIONS") {
      response.writeHead(204, corsHeaders());
      response.end();
      return;
    }
    if (request.method === "GET" && url.pathname === "/healthz") {
      sendJson(response, 200, {
        status: "ok",
        runtime: "pi",
        toolsEnabled: true,
        tools: ["rag_search", "experiment_design"],
        model: modelDescriptor(),
      });
      return;
    }
    if (request.method === "GET" && url.pathname === "/v1/models") {
      sendJson(response, 200, { data: [{ id: modelDescriptor(), configuredModel: configuredModelId() }] });
      return;
    }
    if (request.method === "POST" && url.pathname === "/v1/chat/stream") {
      if (activeTurns >= maxActiveTurns) {
        sendJson(response, 503, { error: "Pi runtime is at its active-turn limit" });
        return;
      }
      activeTurns += 1;
      try {
        await handleStream(request, response, { streamChatImpl, turnTimeoutMs });
      } finally {
        activeTurns -= 1;
      }
      return;
    }
    sendJson(response, 404, { error: "not found" });
  });
}

function start() {
  const server = createServer();
  server.listen(port, host, () => {
    console.log(`NutriMaster Pi runtime listening on http://${host}:${port}`);
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  start();
}
