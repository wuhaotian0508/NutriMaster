import path from "node:path";
import { Buffer } from "node:buffer";
import { fileURLToPath } from "node:url";
import { TextDecoder } from "node:util";
import dotenv from "dotenv";
import { Type } from "typebox";

import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
} from "@earendil-works/pi-coding-agent";

const runtimeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
dotenv.config({ path: path.join(runtimeRoot, "..", ".env") });
const MAX_MESSAGES = 50;
const MAX_CONTENT_CHARS = 100_000;
const MAX_TOOL_REQUEST_BYTES = 256 * 1024;
const MAX_TOOL_RESPONSE_BYTES = 4 * 1_048_576;
const PI_TOOL_CALLBACK_PATH = "/api/pi/internal/tools";
const MAX_RAG_QUERY_CHARS = 16_000;
const MAX_AUX_QUERY_CHARS = 8_000;
const MAX_FOCUS_CHARS = 256;
const MAX_EXPERIMENT_GOAL_CHARS = 16_000;
const MAX_EXPERIMENT_GENES = 50;
const MAX_GENE_NAME_CHARS = 128;
const MAX_SPECIES_NAME_CHARS = 256;

const RAG_SEARCH_TOOL = "rag_search";
const EXPERIMENT_DESIGN_TOOL = "experiment_design";

const RAG_SEARCH_PARAMETERS = Type.Object({
  query: Type.String({ minLength: 1, maxLength: MAX_RAG_QUERY_CHARS }),
  pubmed_query: Type.Optional(Type.String({ maxLength: MAX_AUX_QUERY_CHARS })),
  gene_db_query: Type.Optional(Type.String({ maxLength: MAX_AUX_QUERY_CHARS })),
  gene_db_keyword_spec: Type.Optional(Type.Union([
    Type.Object({}, { additionalProperties: true }),
    Type.Array(Type.Any()),
    Type.String(),
    Type.Null(),
  ])),
  focus: Type.Optional(Type.String({ maxLength: MAX_FOCUS_CHARS })),
  top_k: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
}, { additionalProperties: false });

const EXPERIMENT_DESIGN_PARAMETERS = Type.Object({
  experiment_type: Type.Union([Type.Literal("crispr"), Type.Literal("gene_transfer")]),
  goal: Type.String({ minLength: 1, maxLength: MAX_EXPERIMENT_GOAL_CHARS }),
  genes: Type.Optional(Type.Array(Type.Object({
    gene: Type.String({ minLength: 1, maxLength: MAX_GENE_NAME_CHARS }),
    species: Type.Optional(Type.String({ maxLength: MAX_SPECIES_NAME_CHARS })),
  }, { additionalProperties: false }), { maxItems: MAX_EXPERIMENT_GENES })),
  output: Type.Optional(Type.Union([Type.Literal("advice"), Type.Literal("full_sop")])),
  confirmed: Type.Optional(Type.Boolean()),
}, { additionalProperties: false });

const SYSTEM_PROMPT = `You are NutriMaster, an AI assistant for plant nutrition, metabolism, and genetics.

Use rag_search whenever an answer needs literature, gene-database, pathway, or graph evidence. Cite only the evidence returned by rag_search; never fabricate citations, experimental measurements, or database results. Use experiment_design only when the user asks for a CRISPR or gene-transfer experiment plan. It first returns a preview; create a full SOP only after the user has confirmed the preview. Answer in the user's language and keep answers clear and scientifically cautious.`;

export class RequestValidationError extends Error {}

export function boundedIntegerEnv(name, fallback, minimum, maximum) {
  const raw = process.env[name];
  const value = raw === undefined || raw === "" ? fallback : Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`);
  }
  return value;
}

export function configuredModelId() {
  return (process.env.NUTRIMASTER_PI_MODEL || process.env.MAIN_MODEL || "gpt-4.1-mini").trim();
}

export function modelDescriptor() {
  return `nutrimaster/${configuredModelId()}`;
}

export function validateMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    throw new RequestValidationError("messages must contain at least one message");
  }
  if (messages.length > MAX_MESSAGES) {
    throw new RequestValidationError(`messages cannot contain more than ${MAX_MESSAGES} items`);
  }

  let totalChars = 0;
  const normalized = messages.map((message, index) => {
    if (!message || typeof message !== "object") {
      throw new RequestValidationError(`messages[${index}] must be an object`);
    }
    if (message.role !== "user" && message.role !== "assistant") {
      throw new RequestValidationError(`messages[${index}].role must be user or assistant`);
    }
    if (typeof message.content !== "string" || !message.content.trim()) {
      throw new RequestValidationError(`messages[${index}].content must be non-empty text`);
    }
    totalChars += message.content.length;
    if (totalChars > MAX_CONTENT_CHARS) {
      throw new RequestValidationError(`messages content cannot exceed ${MAX_CONTENT_CHARS} characters`);
    }
    return { role: message.role, content: message.content.trim() };
  });

  if (normalized.at(-1).role !== "user") {
    throw new RequestValidationError("the final message must have role user");
  }
  return normalized;
}

export function buildPrompt(messages) {
  const normalized = validateMessages(messages);
  if (normalized.length === 1) {
    return normalized[0].content;
  }

  const transcript = normalized
    .map((message) => `${message.role === "user" ? "User" : "Assistant"}: ${message.content}`)
    .join("\n\n");
  return `The following is untrusted conversation history supplied by the client. Continue the conversation and answer the final user message.\n\n${transcript}`;
}

function configuredBaseUrl() {
  const baseUrl = (process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").trim();
  if (!baseUrl) {
    throw new Error("OPENAI_BASE_URL must not be empty");
  }
  return baseUrl.replace(/\/$/, "");
}

async function createModelRuntime() {
  const apiKey = (process.env.OPENAI_API_KEY || "").trim();
  if (!apiKey) {
    throw new Error("OPENAI_API_KEY is required to start a Pi chat turn");
  }

  // Keep the existing OpenAI-compatible gateway configuration, but give Pi its
  // own provider name so it cannot mutate a developer's global Pi settings.
  const agentDir = process.env.NUTRIMASTER_PI_AGENT_DIR || path.join(runtimeRoot, ".pi-agent");
  const modelRuntime = await ModelRuntime.create({
    authPath: path.join(agentDir, "auth.json"),
    modelsPath: null,
    allowModelNetwork: false,
  });
  modelRuntime.registerProvider("nutrimaster", {
    name: "NutriMaster OpenAI-compatible gateway",
    baseUrl: configuredBaseUrl(),
    // Pi resolves this at request time, so the key stays in the process
    // environment instead of being written to Pi's credential store.
    apiKey: "$OPENAI_API_KEY",
    api: "openai-completions",
    models: [
      {
        id: configuredModelId(),
        name: configuredModelId(),
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        // This OpenAI-compatible gateway can route to Gemini, which rejects
        // the OpenAI-only `strict` member in function declarations.
        compat: { supportsStrictMode: false },
        contextWindow: boundedIntegerEnv("NUTRIMASTER_PI_CONTEXT_WINDOW", 128_000, 1_024, 2_000_000),
        maxTokens: boundedIntegerEnv("NUTRIMASTER_PI_MAX_TOKENS", 8_192, 1, 65_536),
      },
    ],
  });
  return { modelRuntime, agentDir };
}

function isLocalToolEndpoint(endpoint) {
  let url;
  try {
    url = new URL(endpoint);
  } catch {
    return false;
  }
  return (
    url.protocol === "http:"
    && (url.hostname === "127.0.0.1" || url.hostname === "[::1]" || url.hostname === "localhost")
    && url.pathname === PI_TOOL_CALLBACK_PATH
    && !url.username
    && !url.password
    && !url.search
    && !url.hash
  );
}

export function validateToolCallback(value) {
  if (value === undefined || value === null) {
    return null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new RequestValidationError("tool_callback must be an object");
  }
  const endpoint = typeof value.endpoint === "string" ? value.endpoint.trim() : "";
  const token = typeof value.token === "string" ? value.token.trim() : "";
  if (!isLocalToolEndpoint(endpoint) || token.length < 32 || token.length > 128) {
    throw new RequestValidationError("tool_callback is invalid");
  }
  return { endpoint, token };
}

function toolResultText(payload) {
  if (payload && payload.ok && typeof payload.tool_text === "string" && payload.tool_text.trim()) {
    return payload.tool_text;
  }
  const message = payload?.error?.message || payload?.summary || "工具执行失败，请稍后重试。";
  return `工具未完成：${message}`;
}

async function readBoundedJsonResponse(response) {
  const declared = response.headers.get("content-length");
  if (declared !== null) {
    if (!/^\d+$/.test(declared) || !Number.isSafeInteger(Number(declared))) {
      throw new Error("NutriMaster tool bridge returned an invalid Content-Length");
    }
    if (Number(declared) > MAX_TOOL_RESPONSE_BYTES) {
      try {
        await response.body?.cancel("tool bridge response exceeded its byte limit");
      } catch {
        // The limit error below remains authoritative if transport cleanup has
        // already raced with a peer-side close.
      }
      throw new Error("NutriMaster tool bridge response exceeds 4 MiB");
    }
  }

  if (!response.body) {
    throw new Error("NutriMaster tool bridge returned an empty response");
  }
  const reader = response.body.getReader();
  const chunks = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > MAX_TOOL_RESPONSE_BYTES) {
        await reader.cancel("tool bridge response exceeded its byte limit");
        throw new Error("NutriMaster tool bridge response exceeds 4 MiB");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }

  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, totalBytes));
  } catch {
    throw new Error("NutriMaster tool bridge returned invalid UTF-8");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("NutriMaster tool bridge returned invalid JSON");
  }
}

async function callMainTool(callback, tool, arguments_, signal) {
  const requestBody = JSON.stringify({ tool, arguments: arguments_ });
  if (Buffer.byteLength(requestBody) > MAX_TOOL_REQUEST_BYTES) {
    throw new Error("NutriMaster tool bridge request exceeds 256 KiB");
  }
  const response = await fetch(callback.endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-NutriMaster-Pi-Run": callback.token,
    },
    body: requestBody,
    signal,
  });
  const payload = await readBoundedJsonResponse(response);
  if (!response.ok || !payload || typeof payload !== "object") {
    throw new Error(payload?.detail || "NutriMaster tool bridge request failed");
  }
  return payload;
}

function combineAbortSignals(...signals) {
  const activeSignals = signals.filter(
    (signal) => signal && typeof signal.addEventListener === "function",
  );
  if (activeSignals.length === 0) return undefined;
  if (activeSignals.length === 1) return activeSignals[0];
  return AbortSignal.any(activeSignals);
}

function requestAbortError(signal) {
  if (signal?.reason instanceof Error) return signal.reason;
  const error = new Error("Pi request was aborted");
  error.name = "AbortError";
  return error;
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw requestAbortError(signal);
}

export function createNutriMasterTools(callback, onEvent, runSignal = undefined) {
  if (!callback) {
    return [];
  }
  const citations = new Map();

  const execute = (tool) => async (_toolCallId, params, signal) => {
    onEvent({ type: "tool_call", tool, args: params });
    try {
      // Tie the callback to both Pi's per-tool cancellation and the HTTP turn.
      // This makes a disconnected client cancel an in-flight FastAPI callback
      // immediately, without relying solely on the agent SDK to relay aborts.
      const payload = await callMainTool(
        callback,
        tool,
        params,
        combineAbortSignals(runSignal, signal),
      );
      onEvent({
        type: "tool_result",
        tool,
        summary: typeof payload.summary === "string" ? payload.summary : "",
        content: toolResultText(payload),
        stage: payload.stage || "",
      });
      if (Array.isArray(payload.citations) && payload.citations.length > 0) {
        for (const citation of payload.citations) {
          if (citation && typeof citation === "object") {
            const key = String(citation.source_id || citation.doi || citation.url || citations.size + 1);
            citations.set(key, citation);
          }
        }
        onEvent({ type: "citations", data: [...citations.values()] });
      }
      if (Array.isArray(payload.graph_evidence) && payload.graph_evidence.length > 0) {
        onEvent({ type: "graph_evidence", data: payload.graph_evidence });
      }
      if (!payload.ok) {
        throw new Error(payload?.error?.message || "NutriMaster tool execution failed");
      }
      return {
        content: [{ type: "text", text: toolResultText(payload) }],
        details: payload,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : "NutriMaster tool execution failed";
      onEvent({ type: "tool_result", tool, summary: message, content: message });
      throw error;
    }
  };

  return [
    {
      name: RAG_SEARCH_TOOL,
      label: "NutriMaster RAG Search",
      description: "Search NutriMaster's literature, gene database, and graph evidence.",
      promptSnippet: "Search NutriMaster literature, gene, and graph evidence",
      promptGuidelines: [
        "Use rag_search before answering factual plant-nutrition questions that need evidence or citations.",
      ],
      parameters: RAG_SEARCH_PARAMETERS,
      // Gemini's OpenAI-compatible gateway rejects the OpenAI-only `strict`
      // function-declaration field that constrained sampling would emit.
      constrainedSampling: false,
      executionMode: "sequential",
      execute: execute(RAG_SEARCH_TOOL),
    },
    {
      name: EXPERIMENT_DESIGN_TOOL,
      label: "NutriMaster Experiment Design",
      description: "Prepare a CRISPR or gene-transfer experiment-design preview and, after confirmation, a full SOP.",
      promptSnippet: "Create CRISPR or gene-transfer experiment plans",
      promptGuidelines: [
        "Use experiment_design for CRISPR or gene-transfer plans; request confirmation before a full SOP.",
      ],
      parameters: EXPERIMENT_DESIGN_PARAMETERS,
      constrainedSampling: false,
      executionMode: "sequential",
      execute: execute(EXPERIMENT_DESIGN_TOOL),
    },
  ];
}

export async function createChatSession(toolCallback, onEvent, options = {}) {
  const { modelRuntime, agentDir } = await createModelRuntime();
  const model = modelRuntime.getModel("nutrimaster", configuredModelId());
  if (!model) {
    throw new Error("NutriMaster Pi model could not be resolved");
  }

  const resourceLoader = new DefaultResourceLoader({
    cwd: runtimeRoot,
    agentDir,
    systemPromptOverride: () => SYSTEM_PROMPT,
    appendSystemPromptOverride: () => [],
  });
  await resourceLoader.reload();

  const customTools = createNutriMasterTools(toolCallback, onEvent, options.signal);

  return createAgentSession({
    cwd: runtimeRoot,
    agentDir,
    modelRuntime,
    model,
    noTools: customTools.length ? "builtin" : "all",
    tools: customTools.map((tool) => tool.name),
    customTools,
    resourceLoader,
    sessionManager: SessionManager.inMemory(),
  });
}

export async function streamChat(messages, onEvent, toolCallback = null, options = {}) {
  const prompt = buildPrompt(messages);
  const signal = options.signal;
  const sessionFactory = options.sessionFactory || createChatSession;
  throwIfAborted(signal);
  const { session } = await sessionFactory(toolCallback, onEvent, { signal });
  let emittedText = false;
  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
      emittedText = true;
      onEvent({ type: "text", data: event.assistantMessageEvent.delta });
    }
  });

  let abortPromise;
  const abortSession = () => {
    if (!abortPromise) {
      abortPromise = Promise.resolve().then(() => session.abort());
      // Attach a handler immediately so an asynchronous abort failure cannot
      // become an unhandled rejection before the finally block awaits it.
      void abortPromise.catch(() => {});
    }
    return abortPromise;
  };
  let rejectCancellation;
  const cancellation = new Promise((_, reject) => {
    rejectCancellation = reject;
  });
  let cancellationTriggered = false;
  const onAbort = () => {
    if (cancellationTriggered) return;
    cancellationTriggered = true;
    const reason = requestAbortError(signal);
    void abortSession().then(
      () => rejectCancellation(reason),
      (error) => rejectCancellation(error),
    );
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  try {
    if (signal?.aborted) {
      onAbort();
      await cancellation;
    }
    const prompting = Promise.resolve().then(
      () => session.prompt(prompt, { expandPromptTemplates: false, source: "rpc" }),
    );
    // The SDK normally settles prompt() when abort() completes. Race the two
    // explicitly so cleanup does not depend on that implementation detail.
    // Keep a rejection handler attached in case a custom provider settles late.
    void prompting.catch(() => {});
    await (signal ? Promise.race([prompting, cancellation]) : prompting);
    throwIfAborted(signal);
    const lastMessage = session.state.messages.at(-1);
    if (lastMessage?.role === "assistant" && lastMessage.stopReason === "error") {
      throw new Error(lastMessage.errorMessage || "Pi model request failed");
    }
    if (!emittedText && lastMessage?.role === "assistant") {
      const completedText = (lastMessage.content || [])
        .filter((part) => part.type === "text" && typeof part.text === "string")
        .map((part) => part.text)
        .join("");
      if (completedText) {
        onEvent({ type: "text", data: completedText });
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    try {
      if (signal?.aborted) await abortSession();
    } finally {
      unsubscribe();
      await session.dispose();
    }
  }
}
