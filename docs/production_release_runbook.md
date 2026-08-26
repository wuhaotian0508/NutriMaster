# NutriMaster content-addressed production release runbook

This runbook is intentionally fail-closed. Staging, bootstrap, cutover, and
rollback all require `NUTRIMASTER_PRODUCTION_CHANGE_APPROVED` to equal the exact
20-hex release ID. The scripts never reset the repository, overwrite an
existing release directory, delete an index generation, use name-based process
kills, or touch a `trained*` tmux session.

## 1. Build and read-only admission

From the local repository:

```bash
.venv/bin/python deploy/build_release.py build
.venv/bin/python deploy/build_release.py verify \
  dist/releases/nutrimaster-release-<id>.tar.gz
.venv/bin/python deploy/preflight_release.py \
  dist/releases/nutrimaster-release-<id>.tar.gz --host ali
```

The release contains only explicit runtime source/configuration files. It
records every file hash and all selected dirty/untracked paths. It excludes
`.env`, corpus/index/user data, reports, Python virtual environments,
`node_modules`, `.pi-agent`, caches, logs, and keys. `preflight_release.py` is
read-only. Before staging, the legacy/candidate `.env` permission findings and
candidate/live gateway drift are non-blocking remediation warnings. The
approved stage verifies the working live `deepseek-v4-flash` route, atomically
hands off only `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `MAIN_MODEL` when they
differ, retains a mode-0600 exclusive backup, and tightens both secret files to
0600. No credential value is printed or placed in the release. Every blocking
check must pass.
In particular it proves:

- `MAIN_MODEL`, live 5002, and live 8787 are `deepseek-v4-flash`;
- Jina is configured through the live localhost Clash proxy;
- old 5000's 18,581-file dense manifest matches every production corpus SHA,
  all chunk ranges, 103,024 chunks, and the embedding shape;
- the hard-link bootstrap source and target share a filesystem;
- release staging and the conservative bootstrap workspace fit while retaining
  at least the configured disk safety reserve;
- current 5000/5002/8787 owners, health, Nginx, systemd 239, and cgroup v1 are
  the expected production baseline.

## 2. Explicitly approved stage

Create a new incoming directory and upload only the archive, its sidecar, and
the two admission/stage programs. This is the first production write and must
not be run without explicit approval:

```bash
scp dist/releases/nutrimaster-release-<id>.tar.gz \
    dist/releases/nutrimaster-release-<id>.tar.gz.sha256 \
    deploy/build_release.py deploy/stage-production.sh \
    ali:/root/code/nutrimaster-incoming/<id>/
```

On the server, after setting the exact approval ID:

```bash
export NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=<id>
/root/code/nutrimaster-incoming/<id>/stage-production.sh --execute \
  /root/code/nutrimaster-incoming/<id>/nutrimaster-release-<id>.tar.gz
```

Staging verifies the archive twice, extracts only into a new content-addressed
directory, verifies every extracted hash, validates the proven live gateway,
and atomically hands that gateway to the candidate persistent `.env` if the
temporary 5002 configuration drifted. The original candidate file is retained
under `/root/code/nutrimaster-config-backups/<id>.env.before` with mode 0600.
It then installs Node dependencies from the lock, runs syntax/config checks,
links server-owned `.env`/data/venv state, tightens both live secret files to
0600, and atomically creates or advances `/root/code/nutrimaster-current` only
after the new release is fully staged. An existing link must resolve to a
validated versioned release; its directory is retained as rollback evidence.
It does not stop or reload a live service.

Rerun admission in staged mode; now every check, including `.env` mode and the
staged symlink/dependency layout, is blocking:

```bash
.venv/bin/python deploy/preflight_release.py \
  dist/releases/nutrimaster-release-<id>.tar.gz --host ali --phase staged
```

## 3. One-time immutable index bootstrap

The temporary 5002 flat index is not a valid migration source: its manifest is
one corpus file behind. The bootstrap is fixed to old 5000's complete dense
snapshot at `/root/Projects/NutriMaster/data/index`, whose corpus was proved
identical in step 1.

Run the no-write preflight first, then the approved systemd-cgroup build:

```bash
/root/code/nutrimaster-current/deploy/bootstrap-production.sh --preflight <id>
NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=<id> \
  /root/code/nutrimaster-current/deploy/bootstrap-production.sh --execute <id>
```

The bootstrap snapshots and hashes the corpus, hard-links only the immutable
dense chunks/embeddings/manifest, rebuilds norms, compact BM25, field SQLite,
and Graph SQLite, hashes every generation artifact, then atomically creates
`CURRENT`. It runs under `MemoryLimit=2560M` and the shared 5632 MiB slice. Low
disk, corpus drift, concurrent builders, an existing pointer, a changed dense
inode, incomplete graph, OOM, or ambiguous recovery state all fail closed.
The recovery unit can activate exactly one fully validated orphan publication
or remove only private `.staging-*`/bootstrap snapshot work; it never guesses
between multiple generations.

Bootstrap is one-time. A later application release must independently run
`verify-active` against the existing immutable generation and skips the
bootstrap workspace requirement; it must not rebuild or delete that generation
merely to advance application code.

Do not delete the old flat indexes. They remain the service rollback path.

## 4. Cutover and automatic rollback

First run the cutover preflight:

```bash
/root/code/nutrimaster-current/deploy/cutover-production.sh --preflight <id>
```

Use a real short-lived user bearer token for the authenticated smoke. Read it
without echo so it is not placed in shell history or argv:

```bash
read -rs NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN
export NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN
export NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=<id>
/root/code/nutrimaster-current/deploy/cutover-production.sh --execute <id>
unset NUTRIMASTER_PRODUCTION_E2E_BEARER_TOKEN
```

The cutover records and revalidates the exact legacy listener PIDs, backs up
the current Nginx file, installs and syntax-checks all units/configuration, and
then sends `SIGTERM` only to those three PIDs. It starts one unified Python
process at 5000 and one Pi Node sidecar at 8787 under their cgroups; 5002 must
remain closed. Pi and unified each have a bounded health-readiness loop before
the next service or smoke step; `Type=simple` activation alone is not treated
as socket readiness. Before Nginx reload it verifies the active generation, four
concurrent deep RAG searches, legacy SSE, Pi SSE/tool bridging, authentication,
and exact `deepseek-v4-flash`. After reload it verifies that both public
internal callback paths return 404.

Any failure after state capture invokes `rollback-production.sh`. Rollback
stops/disables the new units, restores and validates the old Nginx config, and
restarts old 5000/5002/8787 in release-ID-specific `nutrimaster_*_rollback_*`
sessions. It never operates on `trained*` or an unrelated tmux session.
The failed deployment state and release are retained. A corrected retry uses a
new content-addressed release ID and a new state directory; it never edits the
failed release or deletes the first attempt's evidence.

Manual rollback uses the same exact approval:

```bash
NUTRIMASTER_PRODUCTION_CHANGE_APPROVED=<id> \
  /root/code/nutrimaster-current/deploy/rollback-production.sh --execute <id>
```

## 5. Observation and cleanup boundary

For at least 30 minutes, observe HTTP 5xx/502, SSE completion, latency, service
restart counts, `memory.failcnt`, OOM journal entries, unified/Pi RSS, and that
5002 remains closed. Exercise Admin extraction, CRISPR, gene transfer, personal
library isolation, and durable index enqueue without forcing another build.

Do not delete the old projects, flat indexes, incoming archive, release,
deployment state, rollback sessions, or any generation during the observation
window. Cleanup is a separate explicitly approved operation after rollback is
no longer required.
