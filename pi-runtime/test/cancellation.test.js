import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { createServer } from "../src/server.js";
import { createNutriMasterTools, streamChat } from "../src/runtime.js";


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolve_, reject_) => {
    resolve = resolve_;
    reject = reject_;
  });
  return { promise, resolve, reject };
}

function withTimeout(promise, label, timeoutMs = 2_000) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", reject);
      resolve();
    });
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  return address.port;
}

async function closeServer(server) {
  if (!server.listening) return;
  const closed = new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
  server.closeAllConnections?.();
  await closed;
}

function streamPayload(withTools = false) {
  return {
    messages: [{ role: "user", content: "test cancellation" }],
    ...(withTools
      ? {
        tool_callback: {
          endpoint: "http://127.0.0.1:5000/api/pi/internal/tools",
          token: "x".repeat(43),
        },
      }
      : {}),
  };
}


test("streamChat awaits session.abort before disposing on every cancelled turn", async () => {
  // Repeat the same cancellation to catch listener/order races rather than
  // relying on a single lucky event-loop interleaving.
  for (let iteration = 0; iteration < 3; iteration += 1) {
    const controller = new AbortController();
    const promptStarted = deferred();
    const order = [];
    const session = {
      state: { messages: [] },
      subscribe() {
        return () => order.push("unsubscribe");
      },
      prompt() {
        promptStarted.resolve();
        // Deliberately never settle. streamChat must stop after awaiting
        // session.abort(), rather than assuming prompt() reacts correctly.
        return new Promise(() => {});
      },
      async abort() {
        order.push("abort:start");
        await new Promise((resolve) => setImmediate(resolve));
        order.push("abort:end");
      },
      async dispose() {
        order.push("dispose");
      },
    };

    const running = streamChat(
      [{ role: "user", content: `cancel turn ${iteration}` }],
      () => {},
      null,
      {
        signal: controller.signal,
        sessionFactory: async (_callback, _onEvent, options) => {
          assert.equal(options.signal, controller.signal);
          return { session };
        },
      },
    );
    await promptStarted.promise;

    const reason = new Error(`client disconnected ${iteration}`);
    reason.name = "AbortError";
    controller.abort(reason);

    await assert.rejects(running, (error) => error === reason);
    assert.deepEqual(order, ["abort:start", "abort:end", "unsubscribe", "dispose"]);
  }
});


test("the HTTP turn signal cancels an in-flight tool callback fetch", async () => {
  const callback = {
    endpoint: "http://127.0.0.1:5000/api/pi/internal/tools",
    token: "x".repeat(43),
  };
  const controller = new AbortController();
  const fetchStarted = deferred();
  const previousFetch = globalThis.fetch;
  let callbackSignal;

  globalThis.fetch = async (_url, options) => {
    callbackSignal = options.signal;
    fetchStarted.resolve();
    return new Promise((_, reject) => {
      if (callbackSignal.aborted) {
        reject(callbackSignal.reason);
        return;
      }
      callbackSignal.addEventListener("abort", () => reject(callbackSignal.reason), { once: true });
    });
  };

  try {
    const tools = createNutriMasterTools(callback, () => {}, controller.signal);
    const running = tools[0].execute("call-1", { query: "NRT1.1" });
    await fetchStarted.promise;

    const reason = new Error("turn cancelled");
    reason.name = "AbortError";
    controller.abort(reason);

    await assert.rejects(running, (error) => error === reason);
    assert.equal(callbackSignal.aborted, true);
    assert.equal(callbackSignal.reason, reason);
  } finally {
    globalThis.fetch = previousFetch;
  }
});


test("a client disconnect aborts the server turn without leaving work running", async () => {
  const started = deferred();
  const aborted = deferred();
  const settled = deferred();
  const server = createServer({
    turnTimeoutMs: 5_000,
    streamChatImpl: async (_messages, _onEvent, _callback, options) => {
      const signal = options.signal;
      started.resolve(signal);
      try {
        await new Promise((_, reject) => {
          if (signal.aborted) {
            reject(signal.reason);
            return;
          }
          signal.addEventListener("abort", () => {
            aborted.resolve(signal.reason);
            reject(signal.reason);
          }, { once: true });
        });
      } finally {
        settled.resolve();
      }
    },
  });
  const port = await listen(server);
  const responseDestroyed = deferred();

  try {
    const request = http.request({
      host: "127.0.0.1",
      port,
      path: "/v1/chat/stream",
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }, (response) => {
      response.once("data", () => {
        response.destroy();
        responseDestroyed.resolve();
      });
      response.on("error", () => {});
    });
    request.on("error", () => {});
    request.end(JSON.stringify(streamPayload(true)));

    const signal = await withTimeout(started.promise, "stream start");
    await withTimeout(responseDestroyed.promise, "client disconnect");
    const reason = await withTimeout(aborted.promise, "server abort");
    await withTimeout(settled.promise, "stream cleanup");

    assert.equal(signal.aborted, true);
    assert.equal(reason.name, "AbortError");
    assert.match(reason.message, /response connection closed/);
  } finally {
    await closeServer(server);
  }
});


test("the server timeout aborts the turn and returns a terminal SSE error", async () => {
  const aborted = deferred();
  const server = createServer({
    turnTimeoutMs: 30,
    streamChatImpl: async (_messages, _onEvent, _callback, options) => {
      const { signal } = options;
      await new Promise((_, reject) => {
        signal.addEventListener("abort", () => {
          aborted.resolve(signal.reason);
          reject(signal.reason);
        }, { once: true });
      });
    },
  });
  const port = await listen(server);

  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(streamPayload()),
    });
    const body = await response.text();
    const reason = await withTimeout(aborted.promise, "timeout abort");

    assert.equal(response.status, 200);
    assert.equal(reason.name, "TimeoutError");
    assert.match(body, /Pi runtime request timed out/);
    assert.match(body, /"type":"done"/);
  } finally {
    await closeServer(server);
  }
});


test("normal response.end does not abort a completed turn", async () => {
  let turnSignal;
  const server = createServer({
    turnTimeoutMs: 1_000,
    streamChatImpl: async (_messages, _onEvent, _callback, options) => {
      turnSignal = options.signal;
    },
  });
  const port = await listen(server);

  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(streamPayload()),
    });
    const body = await response.text();
    await new Promise((resolve) => setImmediate(resolve));

    assert.match(body, /"type":"done"/);
    assert.ok(turnSignal);
    assert.equal(turnSignal.aborted, false);
  } finally {
    await closeServer(server);
  }
});
