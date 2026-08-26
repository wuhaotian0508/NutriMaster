import assert from "node:assert/strict";
import test from "node:test";

import { createServer, readJson } from "../src/server.js";
import { RequestValidationError } from "../src/runtime.js";


function chunkedRequest(chunks, headers = {}) {
  return {
    headers,
    async *[Symbol.asyncIterator]() {
      yield* chunks;
    },
  };
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


test("request JSON preserves a UTF-8 code point split across transport chunks", async () => {
  const expected = "水稻氮营养";
  const encoded = Buffer.from(JSON.stringify({ messages: [{ role: "user", content: expected }] }));
  const firstMultibyte = encoded.indexOf(Buffer.from("水"));
  assert.ok(firstMultibyte > 0);

  const payload = await readJson(chunkedRequest([
    encoded.subarray(0, firstMultibyte + 1),
    encoded.subarray(firstMultibyte + 1),
  ]));

  assert.equal(payload.messages[0].content, expected);
});


test("request limit counts UTF-8 bytes rather than JavaScript characters", async () => {
  const encoded = Buffer.from(JSON.stringify({
    messages: [{ role: "user", content: "你".repeat(350_000) }],
  }));
  assert.ok(encoded.byteLength > 1_048_576);
  assert.ok(encoded.toString("utf8").length < 1_048_576);

  await assert.rejects(
    readJson(chunkedRequest([encoded])),
    (error) => error instanceof RequestValidationError && /exceeds 1 MiB/.test(error.message),
  );
});


test("runtime rejects excess concurrent turns before creating another session", async () => {
  let releaseFirst;
  let firstStarted;
  const started = new Promise((resolve) => { firstStarted = resolve; });
  const release = new Promise((resolve) => { releaseFirst = resolve; });
  const server = createServer({
    maxActiveTurns: 1,
    turnTimeoutMs: 5_000,
    streamChatImpl: async () => {
      firstStarted();
      await release;
    },
  });
  const port = await listen(server);
  const payload = {
    messages: [{ role: "user", content: "bounded concurrency" }],
    tool_callback: {
      endpoint: "http://127.0.0.1:5000/api/pi/internal/tools",
      token: "x".repeat(43),
    },
  };

  try {
    const firstResponsePromise = fetch(`http://127.0.0.1:${port}/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await started;

    const second = await fetch(`http://127.0.0.1:${port}/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    assert.equal(second.status, 503);
    assert.match(await second.text(), /active-turn limit/);

    releaseFirst();
    const first = await firstResponsePromise;
    assert.equal(first.status, 200);
    assert.match(await first.text(), /"type":"done"/);
  } finally {
    releaseFirst();
    await closeServer(server);
  }
});
