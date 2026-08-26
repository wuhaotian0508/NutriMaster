# Isolated index builder operations

The unified Web process never builds retrieval artifacts. A successful Admin
single-paper preview, pipeline completion, and manual rebuild endpoints
atomically queue a job under
`RAG_INDEX_DIR/builder-state/jobs/pending/`. The manual rebuild endpoint returns
`202 queued`; preview and pipeline flows expose the queued job ID through their
response/event. In every case, only durable status can later report build and
activation success.

The production Admin pipeline is also serial: only one pipeline instance may
run, with both its default and maximum worker counts fixed at one. Per-paper
callbacks do not build an index. A preview or a completed/stopped pipeline only
enqueues a durable request for this builder.

## Production units

Install `nutrimaster.slice`, `nutrimaster-index-builder.service`, and
`nutrimaster-index-builder.path` together with the unified and Pi units. Enable
the path watcher as well as the two long-running services:

```bash
systemctl daemon-reload
systemctl enable --now nutrimaster-index-builder.path nutrimaster-pi.service
```

Do not start the unified unit while either old Python listener is alive. Record
and stop the known owners of ports 5000 and 5002, verify both ports are closed,
and only then run `systemctl enable --now nutrimaster-unified.service`. Its
startup guard performs the same check before loading an index and fails closed
if listener inspection is unavailable or inconclusive.

The checked-in path unit assumes the default production index root
`/root/code/nutrimaster/data/index`. If `RAG_INDEX_DIR` is overridden, update
`DirectoryNotEmpty` to the corresponding absolute `builder-state/jobs/pending`
directory before enabling it. The Web dispatcher invokes only the fixed
`nutrimaster-index-builder.service` unit; no request data is interpolated into
a command.

On the systemd 239/cgroup-v1 production host, verify the effective hard limits,
not only the unit text:

```bash
systemctl show nutrimaster-unified.service nutrimaster-pi.service \
  nutrimaster-index-builder.service -p Slice -p MemoryLimit
cat /sys/fs/cgroup/memory/nutrimaster.slice/memory.limit_in_bytes
```

Expected hard limits are 3 GiB for unified, 768 MiB for Pi, 2560 MiB for the
builder, and 5632 MiB (5.5 GiB) for their aggregate slice. The production host
is cgroup v1 and has host swap; `MemoryHigh` and `MemorySwapMax` are not
enforcement controls at service level there, so do not claim that these units disable
swap. The v1 `MemoryLimit` values and aggregate slice are the effective RAM
boundaries.

The unified service has a 360-second stop timeout: Pi/SSE turns may use their
full 300-second deadline before connection teardown. The builder has a
660-second stop timeout because interrupted activation recovery can include a
30-second `reset-failed`, a graceful unified restart lasting up to 420 seconds,
and a 120-second rollback health check. During a controlled activation,
`systemctl restart` may therefore remain in `deactivating` state for several
minutes; do not send an additional kill while the bounded drain is progressing.

## Build, publication, and activation

Before writing a staging generation, the builder requires enough free space
for a complete new generation, two dense work copies (including atomic-save
temps), a stable corpus snapshot, and a 1 GiB safety margin. Insufficient space
sets the job to `failed` before build and leaves `CURRENT` unchanged.

For the current 103,024-chunk corpus, one complete generation is approximately
3.1 GiB and the calculated additional preflight requirement is approximately
5.833 GiB. This is a reference snapshot, not a fixed threshold: the builder's
calculation for the current corpus is authoritative. Production free space is
tight after retaining two generations, so check `df` before submitting a job
and expect a later build to fail safely rather than consuming rollback space.

The builder then:

1. takes a stable private JSON corpus snapshot;
2. builds dense, compact BM25, field-keyword SQLite, and graph artifacts;
3. validates hashes, shapes, fingerprints, and SQLite integrity;
4. atomically publishes an immutable generation and seals files `0444` and the
   generation directory `0555`;
5. restarts the fixed `nutrimaster-unified.service` unit;
6. waits for `/api/health` to report the exact published generation ID.

Only step 6 produces `succeeded`. If activation fails, the builder switches
`CURRENT` back to the recorded previous generation, restarts unified again,
and verifies the rollback generation. An OOM or forced stop is handled by the
unit's `ExecStopPost` recovery command using the same recorded state.

`NUTRIMASTER_INDEX_BUILDER_AUTO_ACTIVATE=false` is an explicit maintenance
mode. It reports `awaiting_activation`, never `succeeded`, until the running
Web process actually reports the published generation.

## Generation retention and disk cleanup

Cleanup runs only after a successful activation (or a deliberately published
`awaiting_activation` generation). It protects `CURRENT` and the immediately
previous serving/rollback generation, then removes only older, validated,
64-hex final generation directories. Retention cleanup never removes `CURRENT`,
a symlink, an invalid final generation, or the rollback generation.

Private `.staging-*` directories and `builder-state/work/corpus-snapshot-*`
directories left by an interrupted builder are different: while holding the
exclusive builder lock, recovery removes those unpublished work paths before
the next disk preflight. They can never be referenced by `CURRENT`.

If preflight rejects a build while exactly those two protected generations are
present, do not delete either to force the build through. Expand the disk or
move an operator-approved archival copy off-host first.

Do not bypass this lifecycle with cron, `nohup`, `tmux`, a second builder, or a
direct invocation of `rebuild_index_robust.py`, `build_sparse_indexes`, or the
graph CLI against the production active index. Those paths bypass the durable
status, memory boundary, whole-generation validation, activation check, and
rollback contract.

## Verification and status

The unified startup script runs the short-lived command below before the Web
process can unpickle `chunks.pkl`:

```bash
.venv/bin/python -m nutrimaster.rag.index_builder_cli verify-active
```

Inspect the durable state independently of Admin UI with:

```bash
.venv/bin/python -m nutrimaster.rag.index_builder_cli status
journalctl -u nutrimaster-index-builder.service -n 200 --no-pager
```

Relevant states are `queued`, `preflight`, `snapshotting`, `building`,
`activating`, `awaiting_activation`, `succeeded`, and `failed`. A dispatch
error returns HTTP 503 and is stored as `failed`; it is never returned as an
accepted or completed build.
