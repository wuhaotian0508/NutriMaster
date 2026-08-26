# Pi Runtime Migration

## Current Boundary

`pi-runtime/` is a standalone Node service based on the maintained Pi SDK
(`@earendil-works/pi-coding-agent`). It owns the agent loop, model streaming,
the NutriMaster system prompt, and the two approved NutriMaster tools:
`rag_search` and `experiment_design`.

The existing Python RAG, Graph RAG, personal-library, and SOP code remains in
place and is not imported by this service. This makes a later tool migration an
additive change instead of a rewrite of the current data layer.

```text
browser
  -> POST /api/pi/query (FastAPI authentication boundary)
  -> POST /v1/chat/stream (localhost Pi runtime)
  -> configured OpenAI-compatible model gateway
  -> (when requested) localhost-only authenticated tool callback
     -> Python RAG / experiment services
```

There is exactly one Python Web process.  Legacy `/api/query`, Pi
`/api/pi/query`, Admin, auth, personal library, and experiment routes all share
the same `WebServices`, `ToolRegistry`, and `JinaRetriever`. Port 5002 must not
remain as a second production Python service. Before starting the canonical
unified process, identify, stop, and disable the known old 5000/5002 owners,
then verify both listener ports are closed. The startup guard performs this
check before it loads any index, preventing a transient duplicate Python
working set even when the eventual bind would have failed.

## Frontend Contract

The frontend should call `POST /api/pi/query` with the same text-chat fields
already used by `/api/query`:

```json
{
  "query": "请解释氮吸收的基本机制",
  "history": [
    {"role": "user", "content": "我研究水稻"},
    {"role": "assistant", "content": "好的"}
  ]
}
```

The endpoint is authenticated by the existing FastAPI dependency and proxies
Pi's SSE stream unchanged. In addition to `text`, `error`, and `done`, the
bridge emits `tools_enabled`, `tool_call`, `tool_result`, `citations`, and
`graph_evidence` when a tool is used.

The current web page uses Pi by default. The `Pi 工具模式` / `Pi Tool Mode`
toggle persists per browser and passes the personal-library and deep-search
choices to the server-owned Python tool context. An explicit `false` value in
local storage remains a temporary rollback switch to the legacy `/api/query`
path; new browsers do not opt into that path.

## Run Locally

```bash
cd pi-runtime
nvm use
npm install
npm start
```

For the complete local Web + Pi stack, run `./start-local.sh` from the
repository root. It waits for the Pi `/healthz` endpoint before starting
FastAPI. Running `uv run nutrimaster web` alone starts only the Python Web
process and is intended for legacy-route or backend-only debugging.

The runtime reads `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `MAIN_MODEL` from
its process environment. Set `NUTRIMASTER_PI_MODEL` only when the chat model
should differ from `MAIN_MODEL`.

It binds to `127.0.0.1:8787` by default and must remain behind the authenticated
FastAPI bridge or a trusted reverse proxy. Do not expose it directly.

## Production build and start

Build query-time artifacts only through the durable isolated builder and deploy
a complete immutable generation. Never run a direct sparse/graph/reindex command
against the production active index. The compact CSR artifact preserves the
original BM25Okapi formula; the field index uses contentless SQLite FTS for
candidates and the existing weighted scorer for exact ranking. Before starting
the request service, validate that `CURRENT` resolves to that generation:

```bash
.venv/bin/python -m nutrimaster.rag.index_builder_cli verify-active
systemctl enable --now nutrimaster-index-builder.path nutrimaster-pi.service
# Stop and disable the identified old 5000/5002 owners; verify both ports closed.
systemctl enable --now nutrimaster-unified.service
```

`start-pi-production.sh` and `start-unified-production.sh` are the respective
systemd `ExecStart` commands. Do not run them sequentially in one foreground
shell: the first is long-lived and the second command would never execute.

For production supervision, install the three services, the builder path unit,
and `nutrimaster.slice` under `deploy/systemd/`. The unified Python service is
limited to 3 GiB, the isolated index builder to 2.5 GiB, and the Pi Node
sidecar to 768 MiB. Their parent slice has a 5.5 GiB aggregate hard limit so a
simultaneous query/build regression cannot consume all host memory. Both
`MemoryLimit` and `MemoryMax` are present with identical values intentionally:
production uses systemd 239 with cgroup v1, whose compatibility branch writes
that common value to `memory.limit_in_bytes`, while cgroup v2 enforces
`MemoryMax` directly. On cgroup v1, `MemoryHigh` and `MemorySwapMax` are not
enforcement controls.
`OOMScoreAdjust` biases a shared-slice OOM toward the offline builder first,
then the restartable Pi sidecar, and away from the unified request process;
the kernel still makes the final victim selection.

The unified unit permits a 360-second graceful stop so an in-flight Pi/SSE
turn can use its 300-second deadline and still close cleanly. The builder unit
permits 660 seconds for `ExecStopPost`: interrupted activation recovery may
spend up to 30 seconds resetting a start-limited unified unit, 420 seconds on
its graceful restart, and 120 seconds verifying the rollback generation. Do
not manually kill either unit merely because a controlled restart remains in
the `deactivating` state during these bounded drain windows.

`start-unified-production.sh` warns when the localhost Pi sidecar is unhealthy
but still starts the legacy route, then binds one FastAPI worker to
`127.0.0.1:5000`. It keeps BM25 and field keyword retrieval enabled, refuses
online index construction, and fails startup unless `CURRENT` points to a
complete immutable generation whose dense, BM25, field and graph artifacts all
pass checksum and corpus guards. Index construction is submitted as a durable
job and runs only in `nutrimaster-index-builder.service`; that unit deliberately
has no ordering dependency on the unified service because it performs a
controlled unified restart after publishing a generation.

Production Admin extraction is serialized as well: only one pipeline instance
may run, and both its default and maximum worker counts are one. A successful
paper or completed/stopped pipeline may enqueue a durable build request, but it
never builds an index in the Web process and never waits for activation inline.

The builder performs its disk preflight before creating staging data. For the
current 103,024-chunk corpus, a complete generation is about 3.1 GiB and the
calculated additional build headroom is about 5.833 GiB (new generation, two
dense work copies, corpus snapshot, and 1 GiB safety margin). These figures are
only a current reference; the live preflight is authoritative. `CURRENT` and
the immediately previous rollback generation are both protected. If preflight
fails with those two generations present, expand storage or move an
operator-approved older archive off-host; never delete either protected
generation to force a build.

Use `deploy/nginx/nutrimaster-unified.conf` as the cutover configuration.  Both
SSE routes point at the same upstream with buffering disabled, while
`/api/pi/internal/tools` is rejected at the public proxy boundary.

## Tool and security boundary

1. `rag_search` is backed by the existing Python RAG service and returns
   structured evidence, citations, and graph evidence.
2. `experiment_design` preserves the preview/confirmation workflow.
3. Pi's built-in file and shell tools remain disabled; only the two tools above
   are registered.
4. FastAPI issues a short-lived per-turn capability token. The callback only
   accepts loopback requests, and user identity/personal-library permissions
   come from the authenticated server context rather than model arguments.
