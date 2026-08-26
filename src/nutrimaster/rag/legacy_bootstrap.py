from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from nutrimaster.config.settings import Settings
from nutrimaster.rag.build_sparse_indexes import build_sparse_indexes
from nutrimaster.rag.index_build_jobs import (
    IndexBuildQueue,
    cleanup_abandoned_build_work,
    snapshot_corpus,
)
from nutrimaster.rag.index_generation import (
    INDEX_ARTIFACT_FILENAMES,
    INDEX_CURRENT_FILENAME,
    file_sha256,
    generations_path,
    read_current_generation_id,
    switch_current_generation,
    validate_generation,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_FINAL_GENERATION_RE = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_DISK_SAFETY_BYTES = 1024 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class LegacyBootstrapDiskPreflight:
    available_bytes: int
    required_bytes: int
    corpus_snapshot_bytes: int
    bm25_workspace_bytes: int
    field_workspace_bytes: int
    graph_workspace_bytes: int
    norms_bytes: int
    safety_bytes: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _regular_file_size(path: Path) -> int:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return 0
    return int(path.stat().st_size)


def _largest_existing(index_dirs: tuple[Path, ...], filename: str) -> int:
    return max((_regular_file_size(path / filename) for path in index_dirs), default=0)


def _disk_safety_bytes() -> int:
    raw = os.getenv(
        "NUTRIMASTER_LEGACY_BOOTSTRAP_DISK_SAFETY_BYTES",
        str(_DEFAULT_DISK_SAFETY_BYTES),
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "NUTRIMASTER_LEGACY_BOOTSTRAP_DISK_SAFETY_BYTES must be an integer"
        ) from exc
    if not _DEFAULT_DISK_SAFETY_BYTES <= value <= 16 * 1024**3:
        raise RuntimeError(
            "NUTRIMASTER_LEGACY_BOOTSTRAP_DISK_SAFETY_BYTES must be between 1 and 16 GiB"
        )
    return value


def calculate_legacy_bootstrap_disk_preflight(
    *,
    index_root: Path,
    source_dir: Path,
    corpus_dir: Path,
    safety_bytes: int,
    available_bytes: int | None = None,
) -> LegacyBootstrapDiskPreflight:
    """Conservatively bound the one-time flat-index migration workspace.

    Dense chunks, embeddings, and the incremental manifest are hard-linked and
    consume no second set of blocks. The estimates below deliberately use the
    larger of legacy artifacts and corpus-derived multipliers for each newly
    built artifact, and include temporary SQLite/graph growth plus an explicit
    post-build safety reserve.
    """

    index_root = Path(index_root).resolve()
    source_dir = Path(source_dir).resolve()
    corpus_dir = Path(corpus_dir).resolve()
    candidates = (source_dir, index_root)
    chunks_bytes = _regular_file_size(
        source_dir / INDEX_ARTIFACT_FILENAMES["chunks"]
    )
    embeddings_bytes = _regular_file_size(
        source_dir / INDEX_ARTIFACT_FILENAMES["embeddings"]
    )
    if chunks_bytes <= 0 or embeddings_bytes <= 0:
        raise RuntimeError("legacy chunks.pkl and embeddings.npy are required")

    corpus_bytes = sum(
        path.stat().st_size
        for path in corpus_dir.glob("*.json")
        if path.is_file() and not path.is_symlink()
    )
    bm25_workspace = max(
        _largest_existing(candidates, "bm25.pkl"),
        _largest_existing(candidates, "bm25_sparse_v4.pkl"),
        chunks_bytes,
    )
    field_workspace = max(
        2 * _largest_existing(candidates, "field_keyword.pkl"),
        2 * _largest_existing(candidates, "field_keyword_v3.sqlite3"),
        4 * chunks_bytes,
    )
    graph_workspace = max(
        2 * _largest_existing(candidates, "graph_index.sqlite"),
        4 * corpus_bytes,
    )
    try:
        embeddings = np.load(
            source_dir / INDEX_ARTIFACT_FILENAMES["embeddings"],
            mmap_mode="r",
        )
    except Exception as exc:
        raise RuntimeError(f"legacy embeddings.npy is unreadable: {exc}") from exc
    if embeddings.ndim != 2:
        raise RuntimeError("legacy embeddings.npy must be two-dimensional")
    norms_bytes = int(embeddings.shape[0]) * np.dtype(np.float32).itemsize + 4096
    del embeddings

    required = (
        corpus_bytes
        + bm25_workspace
        + field_workspace
        + graph_workspace
        + norms_bytes
        + int(safety_bytes)
    )
    free = (
        int(available_bytes)
        if available_bytes is not None
        else int(shutil.disk_usage(index_root).free)
    )
    return LegacyBootstrapDiskPreflight(
        available_bytes=free,
        required_bytes=required,
        corpus_snapshot_bytes=corpus_bytes,
        bm25_workspace_bytes=bm25_workspace,
        field_workspace_bytes=field_workspace,
        graph_workspace_bytes=graph_workspace,
        norms_bytes=norms_bytes,
        safety_bytes=int(safety_bytes),
    )


def _read_legacy_manifest(source_dir: Path) -> dict[str, Any]:
    path = Path(source_dir) / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"legacy dense manifest is missing or unsafe: {path}")
    raw = path.read_bytes()
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise RuntimeError("legacy dense manifest is unexpectedly large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy dense manifest is invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise RuntimeError("legacy dense manifest has an invalid schema")
    if not isinstance(payload.get("chunker_version"), str) or not payload["chunker_version"]:
        raise RuntimeError("legacy dense manifest has no chunker version")
    return payload


def validate_legacy_source_against_corpus(
    source_dir: Path,
    corpus_dir: Path,
) -> dict[str, Any]:
    """Hash every corpus file and prove it is the dense snapshot's corpus."""

    source_dir = Path(source_dir).resolve()
    corpus_dir = Path(corpus_dir).resolve()
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise RuntimeError(f"legacy source directory is invalid: {source_dir}")
    if corpus_dir.is_symlink() or not corpus_dir.is_dir():
        raise RuntimeError(f"legacy corpus snapshot is invalid: {corpus_dir}")

    payload = _read_legacy_manifest(source_dir)
    entries = payload["files"]
    corpus_files = {
        path.name: path
        for path in corpus_dir.glob("*.json")
        if path.is_file() and not path.is_symlink()
    }
    if set(entries) != set(corpus_files):
        missing = sorted(set(entries) - set(corpus_files))[:5]
        extra = sorted(set(corpus_files) - set(entries))[:5]
        raise RuntimeError(
            "legacy dense manifest does not match the corpus file set: "
            f"missing={missing}, extra={extra}"
        )

    cursor = 0
    chunker_version = payload["chunker_version"]
    # Chunk ranges are assigned in the dense builder's manifest insertion
    # order. Alphabetically reordering filenames would manufacture a false
    # discontinuity even when every file and checksum is correct.
    for name, entry in entries.items():
        if Path(name).name != name:
            raise RuntimeError(f"legacy dense manifest contains an unsafe filename: {name}")
        path = corpus_files[name]
        if not isinstance(entry, dict):
            raise RuntimeError(f"legacy dense manifest entry is invalid: {name}")
        digest = entry.get("sha")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise RuntimeError(f"legacy dense manifest checksum is invalid: {name}")
        if file_sha256(path) != digest:
            raise RuntimeError(f"legacy corpus checksum does not match dense manifest: {name}")
        if entry.get("chunker_version") != chunker_version:
            raise RuntimeError(f"legacy dense manifest chunker version differs: {name}")
        start = entry.get("start")
        end = entry.get("end")
        n_chunks = entry.get("n_chunks")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or isinstance(n_chunks, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not isinstance(n_chunks, int)
            or start != cursor
            or end < start
            or end - start != n_chunks
        ):
            raise RuntimeError(f"legacy dense manifest chunk range is invalid: {name}")
        cursor = end

    embeddings = np.load(
        source_dir / INDEX_ARTIFACT_FILENAMES["embeddings"],
        mmap_mode="r",
    )
    if embeddings.ndim != 2 or int(embeddings.shape[0]) != cursor:
        raise RuntimeError(
            "legacy dense manifest chunk total does not match embeddings.npy"
        )
    shape = [int(value) for value in embeddings.shape]
    del embeddings
    return {
        "chunker_version": chunker_version,
        "corpus_files": len(corpus_files),
        "chunks": cursor,
        "embedding_shape": shape,
    }


def _assert_no_generation_pointer_or_published_generation(index_root: Path) -> None:
    current = Path(index_root) / INDEX_CURRENT_FILENAME
    if current.exists() or current.is_symlink():
        raise RuntimeError(
            "legacy bootstrap is one-time only and refuses an existing CURRENT pointer"
        )
    root = generations_path(index_root)
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"generation root is invalid: {root}")
    entries = list(root.iterdir())
    if entries:
        raise RuntimeError(
            "legacy bootstrap found existing generation work; run recover-bootstrap first"
        )


def _remove_owned_snapshot(queue: IndexBuildQueue, snapshot: Path) -> None:
    snapshot = Path(snapshot).resolve()
    work_dir = queue.work_dir.resolve()
    if (
        snapshot.parent != work_dir
        or not snapshot.name.startswith("corpus-snapshot-bootstrap-")
        or snapshot.is_symlink()
        or not snapshot.is_dir()
    ):
        raise RuntimeError(f"refusing to remove an unowned bootstrap snapshot: {snapshot}")
    shutil.rmtree(snapshot)


def bootstrap_legacy_generation(
    settings: Settings,
    *,
    source_dir: Path,
    available_bytes: int | None = None,
) -> dict[str, Any]:
    """Publish the first immutable generation from a verified flat snapshot."""

    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    index_root = settings.rag.index_dir.resolve()
    data_dir = settings.rag.data_dir.resolve()
    source_dir = Path(source_dir).resolve()
    queue = IndexBuildQueue.from_settings(settings, dispatcher=lambda: None)

    with queue.builder_lock():
        _assert_no_generation_pointer_or_published_generation(index_root)
        preflight = calculate_legacy_bootstrap_disk_preflight(
            index_root=index_root,
            source_dir=source_dir,
            corpus_dir=data_dir,
            safety_bytes=_disk_safety_bytes(),
            available_bytes=available_bytes,
        )
        queue.write_status(
            state="preflight",
            operation="bootstrap-legacy",
            disk_preflight=preflight.as_dict(),
        )
        if preflight.available_bytes < preflight.required_bytes:
            error = (
                "insufficient disk space for legacy bootstrap: "
                f"available={preflight.available_bytes}, required={preflight.required_bytes}"
            )
            queue.write_status(
                state="failed",
                operation="bootstrap-legacy",
                disk_preflight=preflight.as_dict(),
                error=error,
            )
            raise RuntimeError(error)

        snapshot = queue.work_dir / f"corpus-snapshot-bootstrap-{uuid.uuid4().hex}"
        queue.write_status(
            state="snapshotting",
            operation="bootstrap-legacy",
            disk_preflight=preflight.as_dict(),
        )
        try:
            snapshot_corpus(data_dir, snapshot)
            source_validation = validate_legacy_source_against_corpus(
                source_dir,
                snapshot,
            )
            queue.write_status(
                state="building",
                operation="bootstrap-legacy",
                disk_preflight=preflight.as_dict(),
                source_validation=source_validation,
            )
            result = build_sparse_indexes(
                index_root,
                source_dir=source_dir,
                build_bm25=True,
                build_field_keyword=True,
                build_norms=True,
                build_graph=True,
                graph_data_dir=snapshot,
                link_legacy_dense=True,
            )
        except MemoryError:
            # Do not disguise allocator exhaustion with status or cleanup
            # failures. The systemd recovery command handles private remnants.
            raise
        except Exception as exc:
            queue.write_status(
                state="failed",
                operation="bootstrap-legacy",
                disk_preflight=preflight.as_dict(),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            if snapshot.exists():
                _remove_owned_snapshot(queue, snapshot)

        queue.write_status(
            state="succeeded",
            operation="bootstrap-legacy",
            disk_preflight=preflight.as_dict(),
            source_validation=source_validation,
            published_generation=result["generation_id"],
        )
        return {
            **result,
            "state": "succeeded",
            "disk_preflight": preflight.as_dict(),
            "source_validation": source_validation,
        }


def preflight_legacy_generation(
    settings: Settings,
    *,
    source_dir: Path,
    available_bytes: int | None = None,
) -> dict[str, Any]:
    """Run the complete bootstrap admission check without writing anything."""

    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    index_root = settings.rag.index_dir.resolve()
    data_dir = settings.rag.data_dir.resolve()
    source_dir = Path(source_dir).resolve()
    _assert_no_generation_pointer_or_published_generation(index_root)
    if source_dir.stat().st_dev != index_root.stat().st_dev:
        raise RuntimeError(
            "legacy bootstrap source and target must be on the same filesystem"
        )
    preflight = calculate_legacy_bootstrap_disk_preflight(
        index_root=index_root,
        source_dir=source_dir,
        corpus_dir=data_dir,
        safety_bytes=_disk_safety_bytes(),
        available_bytes=available_bytes,
    )
    if preflight.available_bytes < preflight.required_bytes:
        raise RuntimeError(
            "insufficient disk space for legacy bootstrap: "
            f"available={preflight.available_bytes}, required={preflight.required_bytes}"
        )
    source_validation = validate_legacy_source_against_corpus(source_dir, data_dir)
    return {
        "status": "ok",
        "operation": "preflight-legacy",
        "source_dir": str(source_dir),
        "index_root": str(index_root),
        "corpus_dir": str(data_dir),
        "disk_preflight": preflight.as_dict(),
        "source_validation": source_validation,
    }


def recover_legacy_bootstrap(settings: Settings) -> dict[str, Any]:
    """Recover only private remnants or one fully valid orphan publication."""

    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    index_root = settings.rag.index_dir.resolve()
    queue = IndexBuildQueue.from_settings(settings, dispatcher=lambda: None)
    with queue.builder_lock():
        try:
            active_id = read_current_generation_id(index_root)
        except RuntimeError:
            active_id = None
        if active_id is not None:
            active = switch_current_generation(index_root, active_id)
            removed = cleanup_abandoned_build_work(queue)
            return {
                "state": "already-active",
                "generation_id": active.generation_id,
                "removed_abandoned_work": removed,
            }

        root = generations_path(index_root)
        finals = []
        if root.is_dir() and not root.is_symlink():
            finals = [
                path
                for path in root.iterdir()
                if _FINAL_GENERATION_RE.fullmatch(path.name)
                and path.is_dir()
                and not path.is_symlink()
            ]
        if len(finals) > 1:
            raise RuntimeError(
                "multiple orphan generations exist; refusing to guess which one to activate"
            )
        if len(finals) == 1:
            payload = validate_generation(finals[0])
            del payload
            active = switch_current_generation(index_root, finals[0].name)
            removed = cleanup_abandoned_build_work(queue)
            queue.write_status(
                state="succeeded",
                operation="recover-bootstrap",
                published_generation=active.generation_id,
                removed_abandoned_work=removed,
            )
            return {
                "state": "recovered",
                "generation_id": active.generation_id,
                "removed_abandoned_work": removed,
            }

        removed = cleanup_abandoned_build_work(queue)
        queue.write_status(
            state="idle",
            operation="recover-bootstrap",
            removed_abandoned_work=removed,
        )
        return {
            "state": "cleaned" if removed else "idle",
            "generation_id": None,
            "removed_abandoned_work": removed,
        }
