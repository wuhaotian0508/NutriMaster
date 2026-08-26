from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nutrimaster.config.settings import Settings
from nutrimaster.rag.index_generation import (
    INDEX_ARTIFACT_FILENAMES,
    INDEX_STAGING_PREFIX,
    generations_path,
    read_current_generation_id,
    switch_current_generation,
    validate_generation_manifest,
)


INDEX_BUILD_JOB_VERSION = "nutrimaster-index-build-job-v1"
INDEX_BUILD_STATUS_VERSION = "nutrimaster-index-build-status-v1"
SYSTEMD_START_BUILDER = (
    "/usr/bin/systemctl",
    "start",
    "--no-block",
    "nutrimaster-index-builder.service",
)
SYSTEMD_RESTART_UNIFIED = (
    "/usr/bin/systemctl",
    "restart",
    "nutrimaster-unified.service",
)
SYSTEMD_RESET_FAILED_UNIFIED = (
    "/usr/bin/systemctl",
    "reset-failed",
    "nutrimaster-unified.service",
)
SYSTEMD_IS_SYSTEM_RUNNING = (
    "/usr/bin/systemctl",
    "is-system-running",
)
_FINAL_GENERATION_RE = re.compile(r"[0-9a-f]{64}\Z")
_ACTIVE_STATES = {"preflight", "snapshotting", "building", "publishing", "activating"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    if Path(path).is_symlink():
        raise RuntimeError(f"index builder control file must not be a symlink: {path}")
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    if len(raw) > 1024 * 1024:
        raise RuntimeError(f"index builder control file is unexpectedly large: {path}")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"index builder control file is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"index builder control file must contain an object: {path}")
    return payload


class IndexBuildDispatchError(RuntimeError):
    def __init__(self, job_id: str, message: str):
        super().__init__(message)
        self.job_id = job_id


@dataclass(frozen=True)
class DiskPreflight:
    available_bytes: int
    required_bytes: int
    generation_bytes: int
    dense_workspace_bytes: int
    corpus_snapshot_bytes: int
    safety_bytes: int

    def as_dict(self) -> dict[str, int]:
        return {
            "available_bytes": self.available_bytes,
            "required_bytes": self.required_bytes,
            "generation_bytes": self.generation_bytes,
            "dense_workspace_bytes": self.dense_workspace_bytes,
            "corpus_snapshot_bytes": self.corpus_snapshot_bytes,
            "safety_bytes": self.safety_bytes,
        }


class IndexBuildQueue:
    """Durable request queue shared by Web and the isolated builder service."""

    def __init__(
        self,
        index_root: Path,
        *,
        dispatcher: Callable[[], None] | None = None,
        job_id_factory: Callable[[], str] | None = None,
    ):
        self.index_root = Path(index_root).resolve()
        self.control_dir = self.index_root / "builder-state"
        self.jobs_dir = self.control_dir / "jobs"
        self.pending_dir = self.jobs_dir / "pending"
        self.completed_dir = self.jobs_dir / "completed"
        self.failed_dir = self.jobs_dir / "failed"
        self.work_dir = self.control_dir / "work"
        self.status_path = self.control_dir / "status.json"
        self.lock_path = self.control_dir / "builder.lock"
        self.enqueue_lock_path = self.control_dir / "enqueue.lock"
        self.dispatcher = dispatcher or dispatch_systemd_builder
        self.job_id_factory = job_id_factory or (lambda: uuid.uuid4().hex)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        dispatcher: Callable[[], None] | None = None,
    ) -> "IndexBuildQueue":
        if settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        return cls(settings.rag.index_dir, dispatcher=dispatcher)

    def ensure_layout(self) -> None:
        for path in (
            self.pending_dir,
            self.completed_dir,
            self.failed_dir,
            self.work_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError(f"index builder state path is invalid: {path}")

    @contextmanager
    def _locked(self, path: Path, *, nonblocking: bool = False) -> Iterator[None]:
        self.ensure_layout()
        with path.open("a+b") as lock_file:
            operation = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
            try:
                fcntl.flock(lock_file.fileno(), operation)
            except BlockingIOError as exc:
                raise RuntimeError("another index builder already owns the build lock") from exc
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def builder_lock(self) -> Iterator[None]:
        with self._locked(self.lock_path, nonblocking=True):
            yield

    def enqueue(
        self,
        *,
        force: bool,
        reason: str,
        active_generation_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(force, bool):
            raise ValueError("force must be a boolean")
        if not isinstance(reason, str) or not reason or len(reason) > 128:
            raise ValueError("index build reason is invalid")
        if active_generation_id is not None and not _FINAL_GENERATION_RE.fullmatch(
            active_generation_id
        ):
            raise ValueError("active generation id is invalid")

        job_id = self.job_id_factory()
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise RuntimeError("generated index build job id is invalid")
        payload = {
            "version": INDEX_BUILD_JOB_VERSION,
            "job_id": job_id,
            "force": force,
            "reason": reason,
            "active_generation": active_generation_id,
            "created_at": _utc_now(),
        }
        request_path = self.pending_dir / f"{job_id}.json"

        with self._locked(self.enqueue_lock_path):
            if request_path.exists() or request_path.is_symlink():
                raise RuntimeError("index build job id collision")
            _atomic_write_json(request_path, payload)
            current_status = _read_json(self.status_path) or {}
            if current_status.get("state") not in _ACTIVE_STATES:
                self.write_status(
                    state="queued",
                    job_ids=[job_id],
                    force=force,
                    reason=reason,
                    message="Index build request is durably queued",
                )
            try:
                self.dispatcher()
            except MemoryError:
                # The request is already durable. Preserve allocator
                # exhaustion instead of disguising it as dispatch failure.
                raise
            except Exception as exc:
                failed_path = self.failed_dir / request_path.name
                os.replace(request_path, failed_path)
                _fsync_directory(self.pending_dir)
                _fsync_directory(self.failed_dir)
                if current_status.get("state") not in _ACTIVE_STATES:
                    self.write_status(
                        state="failed",
                        job_ids=[job_id],
                        force=force,
                        reason=reason,
                        error=f"builder dispatch failed: {type(exc).__name__}: {exc}",
                    )
                raise IndexBuildDispatchError(
                    job_id,
                    f"index builder dispatch failed: {type(exc).__name__}: {exc}",
                ) from exc
        return payload

    def pending_requests(self) -> list[dict[str, Any]]:
        self.ensure_layout()
        requests: list[dict[str, Any]] = []
        for path in sorted(self.pending_dir.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                continue
            payload = _read_json(path)
            if payload is None:
                continue
            if (
                payload.get("version") != INDEX_BUILD_JOB_VERSION
                or payload.get("job_id") != path.stem
                or not isinstance(payload.get("force"), bool)
                or not isinstance(payload.get("reason"), str)
                or not payload.get("reason")
                or len(payload["reason"]) > 128
                or (
                    payload.get("active_generation") is not None
                    and not isinstance(payload.get("active_generation"), str)
                )
                or (
                    isinstance(payload.get("active_generation"), str)
                    and not _FINAL_GENERATION_RE.fullmatch(payload["active_generation"])
                )
            ):
                raise RuntimeError(f"invalid pending index build request: {path}")
            requests.append(payload)
        return requests

    def write_status(self, *, state: str, **fields: Any) -> dict[str, Any]:
        payload = {
            "version": INDEX_BUILD_STATUS_VERSION,
            "state": state,
            "updated_at": _utc_now(),
            **fields,
        }
        _atomic_write_json(self.status_path, payload)
        return payload

    def finish_requests(self, job_ids: list[str], *, succeeded: bool) -> None:
        destination_dir = self.completed_dir if succeeded else self.failed_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        for job_id in job_ids:
            if not re.fullmatch(r"[0-9a-f]{32}", job_id):
                raise RuntimeError("refusing to archive an invalid index build job id")
            source = self.pending_dir / f"{job_id}.json"
            if not source.exists():
                continue
            os.replace(source, destination_dir / source.name)
        _fsync_directory(self.pending_dir)
        _fsync_directory(destination_dir)

    def status(self, *, active_generation_id: str | None = None) -> dict[str, Any]:
        payload = _read_json(self.status_path) or {
            "version": INDEX_BUILD_STATUS_VERSION,
            "state": "idle",
            "updated_at": None,
        }
        payload = dict(payload)
        payload["stored_state"] = payload.get("state")
        payload["pending_jobs"] = len(self.pending_requests())
        try:
            payload["current_generation"] = read_current_generation_id(self.index_root)
        except RuntimeError as exc:
            payload["current_generation"] = None
            payload["current_generation_error"] = str(exc)
        payload["active_generation"] = active_generation_id
        published = payload.get("published_generation")
        if (
            published
            and payload.get("stored_state") in {"awaiting_activation", "succeeded"}
            and active_generation_id == published
        ):
            payload["state"] = "succeeded"
            payload["activation_required"] = False
        elif published and payload.get("stored_state") == "awaiting_activation":
            payload["activation_required"] = True
        else:
            payload["activation_required"] = False
        if payload["pending_jobs"] and payload["state"] not in _ACTIVE_STATES:
            payload["next_state"] = "queued"
        return payload


def dispatch_systemd_builder() -> None:
    """Start one fixed systemd unit; no request field reaches the command line."""

    completed = subprocess.run(
        list(SYSTEMD_START_BUILDER),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown systemctl error").strip()
        raise RuntimeError(detail)


def _bounded_positive_int_env(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


def _boolean_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def restart_unified_service() -> None:
    """Restart one fixed unit; no job or HTTP field reaches the command."""

    reset = subprocess.run(
        list(SYSTEMD_RESET_FAILED_UNIFIED),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if reset.returncode != 0:
        detail = (reset.stderr or reset.stdout or "unknown systemctl error").strip()
        raise RuntimeError(f"could not reset unified service failure counter: {detail}")
    completed = subprocess.run(
        list(SYSTEMD_RESTART_UNIFIED),
        check=False,
        capture_output=True,
        text=True,
        # Unified drains Pi/SSE turns for up to five minutes before exit.
        # Keep process restart wait distinct from the subsequent health gate.
        timeout=420,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown systemctl error").strip()
        raise RuntimeError(f"unified service restart failed: {detail}")


def system_is_stopping() -> bool:
    """Best-effort guard against starting services during host shutdown."""

    completed = subprocess.run(
        list(SYSTEMD_IS_SYSTEM_RUNNING),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    state = (completed.stdout or completed.stderr or "").strip().lower()
    return state in {"stopping", "offline"}


def wait_for_active_generation(expected_generation: str) -> None:
    if not _FINAL_GENERATION_RE.fullmatch(expected_generation):
        raise RuntimeError("expected generation id is invalid")
    port = _bounded_positive_int_env(
        "NUTRIMASTER_UNIFIED_WEB_PORT",
        5000,
        maximum=65535,
    )
    timeout_seconds = _bounded_positive_int_env(
        "NUTRIMASTER_INDEX_ACTIVATION_TIMEOUT_SECONDS",
        120,
        maximum=600,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout_seconds
    last_error = "health endpoint did not return the expected generation"
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=3) as response:
                payload = json.load(response)
            active = (payload.get("index") or {}).get("generation_id")
            if payload.get("status") == "ok" and active == expected_generation:
                return
            last_error = f"health generation is {active!r}, expected {expected_generation!r}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1)
    raise RuntimeError(f"unified service activation timed out: {last_error}")


def activate_unified_generation(expected_generation: str) -> None:
    restart_unified_service()
    wait_for_active_generation(expected_generation)


def _tree_size(path: Path) -> int:
    total = 0
    for child in Path(path).iterdir():
        if child.is_symlink() or not child.is_file():
            continue
        total += child.stat().st_size
    return total


def calculate_disk_preflight(
    *,
    index_root: Path,
    source_generation: Path,
    data_dir: Path,
    safety_bytes: int,
    available_bytes: int | None = None,
) -> DiskPreflight:
    """Estimate the builder's worst simultaneous on-disk working set.

    The dense builder first owns a private dense copy and atomic save temps;
    the sparse builder then owns a second, complete generation. The corpus is
    also copied into a stable private snapshot. Existing generations are
    already reflected in ``disk_usage.free`` and therefore are not counted.
    """

    source_generation = Path(source_generation)
    if source_generation.is_symlink() or not source_generation.is_dir():
        raise RuntimeError(f"active generation directory is invalid: {source_generation}")
    generation_bytes = _tree_size(source_generation)
    dense_names = {
        INDEX_ARTIFACT_FILENAMES["chunks"],
        INDEX_ARTIFACT_FILENAMES["embeddings"],
        "manifest.json",
    }
    dense_bytes = sum(
        child.stat().st_size
        for child in source_generation.iterdir()
        if child.is_file() and not child.is_symlink() and child.name in dense_names
    )
    corpus_bytes = sum(
        path.stat().st_size
        for path in Path(data_dir).glob("*.json")
        if path.is_file() and not path.is_symlink()
    )
    required = generation_bytes + (2 * dense_bytes) + corpus_bytes + safety_bytes
    free = (
        int(available_bytes)
        if available_bytes is not None
        else int(shutil.disk_usage(Path(index_root)).free)
    )
    return DiskPreflight(
        available_bytes=free,
        required_bytes=required,
        generation_bytes=generation_bytes,
        dense_workspace_bytes=2 * dense_bytes,
        corpus_snapshot_bytes=corpus_bytes,
        safety_bytes=safety_bytes,
    )


def _copy_stable_json(source: Path, destination: Path, *, attempts: int = 4) -> None:
    last_error: Exception | None = None
    for _attempt in range(attempts):
        try:
            before = source.stat()
            raw = source.read_bytes()
            after = source.stat()
            if (
                before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or len(raw) != after.st_size
            ):
                raise RuntimeError("source changed while it was copied")
            json.loads(raw)
            destination.write_bytes(raw)
            return
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise RuntimeError(f"could not take a stable corpus snapshot of {source.name}: {last_error}")


def snapshot_corpus(data_dir: Path, destination: Path) -> int:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    count = 0
    for source in sorted(Path(data_dir).glob("*.json")):
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"corpus entry is not a regular file: {source}")
        _copy_stable_json(source, destination / source.name)
        count += 1
    _fsync_directory(destination)
    if count == 0:
        raise RuntimeError("refusing to build an empty corpus snapshot")
    return count


def cleanup_abandoned_build_work(queue: IndexBuildQueue) -> list[str]:
    """Remove only private staging/snapshot paths while holding builder.lock."""

    removed: list[str] = []
    queue.ensure_layout()
    for candidate in sorted(queue.work_dir.iterdir()):
        if (
            candidate.name.startswith("corpus-snapshot-")
            and candidate.is_dir()
            and not candidate.is_symlink()
        ):
            shutil.rmtree(candidate)
            removed.append(str(candidate))
    generation_root = generations_path(queue.index_root)
    if generation_root.is_dir() and not generation_root.is_symlink():
        for candidate in sorted(generation_root.iterdir()):
            if (
                candidate.name.startswith(INDEX_STAGING_PREFIX)
                and candidate.is_dir()
                and not candidate.is_symlink()
            ):
                shutil.rmtree(candidate)
                removed.append(str(candidate))
        if removed:
            _fsync_directory(generation_root)
    if removed:
        _fsync_directory(queue.work_dir)
    return removed


def prune_old_generations(
    index_root: Path,
    *,
    protected_generation_ids: set[str],
) -> list[str]:
    """Delete only validated final generations older than the protected pair.

    CURRENT and the serving/rollback generation must be supplied as protected.
    If either cannot be identified, callers must skip pruning entirely.
    """

    current = read_current_generation_id(index_root)
    protected = set(protected_generation_ids) | {current}
    if not protected or any(not _FINAL_GENERATION_RE.fullmatch(item) for item in protected):
        raise RuntimeError("cannot prune generations without valid protected generation ids")

    removed: list[str] = []
    root = generations_path(index_root)
    for candidate in sorted(root.iterdir()):
        if candidate.name in protected or not _FINAL_GENERATION_RE.fullmatch(candidate.name):
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            payload = validate_generation_manifest(candidate, verify_checksums=False)
        except RuntimeError:
            continue
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != candidate.name:
            continue
        # A previously published generation is intentionally 0555. Restore
        # owner traversal/write only after every protection and identity check,
        # immediately before deleting this exact unreferenced directory.
        candidate.chmod(0o700)
        shutil.rmtree(candidate)
        removed.append(candidate.name)
    if removed:
        _fsync_directory(root)
    return removed


def seal_published_generation(index_root: Path, generation_id: str) -> Path:
    """Make one validated published generation read-only (files 0444, dir 0555)."""

    if not _FINAL_GENERATION_RE.fullmatch(generation_id):
        raise RuntimeError("cannot seal an invalid generation id")
    generation_dir = generations_path(index_root) / generation_id
    if generation_dir.is_symlink() or not generation_dir.is_dir():
        raise RuntimeError(f"published generation is missing: {generation_dir}")
    payload = validate_generation_manifest(generation_dir, verify_checksums=False)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != generation_id:
        raise RuntimeError("published generation id does not match its manifest")
    for artifact in generation_dir.iterdir():
        if artifact.is_symlink() or not artifact.is_file():
            raise RuntimeError(f"published generation contains an invalid entry: {artifact}")
        artifact.chmod(0o444)
    generation_dir.chmod(0o555)
    _fsync_directory(generations_path(index_root))
    return generation_dir


class IndexBuildWorker:
    """Run corpus-sized index work only inside the dedicated builder service."""

    def __init__(
        self,
        settings: Settings,
        *,
        queue: IndexBuildQueue | None = None,
        build: Callable[[Path, bool], str] | None = None,
        activate: Callable[[str], None] | None = None,
        auto_activate: bool | None = None,
        available_bytes: int | None = None,
    ):
        if settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        self.settings = settings
        self.queue = queue or IndexBuildQueue.from_settings(settings)
        self._build = build or self._build_with_jina
        self._activate = activate or activate_unified_generation
        self._auto_activate = (
            _boolean_env("NUTRIMASTER_INDEX_BUILDER_AUTO_ACTIVATE", True)
            if auto_activate is None
            else auto_activate
        )
        self._available_bytes = available_bytes

    def _build_with_jina(self, snapshot_dir: Path, force: bool) -> str:
        from nutrimaster.rag.jina import JinaRetriever

        # This process never serves queries. Loading the active corpus before
        # construction, loading the result, or reloading the old generation on
        # failure only amplifies its memory peak without improving availability.
        retriever = JinaRetriever(settings=self.settings, autoload=False)
        retriever.build_index(
            data_dir=snapshot_dir,
            incremental=True,
            force=force,
            load_after_build=False,
            reload_on_failure=False,
        )
        if not retriever.generation_id:
            raise RuntimeError("builder did not publish an immutable generation")
        return str(retriever.generation_id)

    def _safety_bytes(self) -> int:
        raw = os.getenv("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES", str(1024**3))
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError("NUTRIMASTER_INDEX_BUILDER_DISK_SAFETY_BYTES must be an integer") from exc
        if value < 256 * 1024 * 1024:
            raise RuntimeError("index builder disk safety margin must be at least 256 MiB")
        return value

    def run_once(self) -> dict[str, Any]:
        rag = self.settings.rag
        assert rag is not None
        with self.queue.builder_lock():
            requests = self.queue.pending_requests()
            if not requests:
                return self.queue.write_status(state="idle", message="No pending index build requests")

            job_ids = [str(request["job_id"]) for request in requests]
            force = any(bool(request["force"]) for request in requests)
            reasons = sorted({str(request.get("reason", "unknown")) for request in requests})
            previous_generation = read_current_generation_id(rag.index_dir)
            serving_generations = {
                str(request["active_generation"])
                for request in requests
                if request.get("active_generation")
            }
            removed_generations: list[str] = []
            cleanup_error = None
            removed_abandoned_work = cleanup_abandoned_build_work(self.queue)
            self.queue.write_status(
                state="preflight",
                job_ids=job_ids,
                force=force,
                reasons=reasons,
                previous_generation=previous_generation,
                serving_generations=sorted(serving_generations),
                removed_generations=removed_generations,
                cleanup_error=cleanup_error,
                removed_abandoned_work=removed_abandoned_work,
            )

            try:
                preflight = calculate_disk_preflight(
                    index_root=rag.index_dir,
                    source_generation=generations_path(rag.index_dir) / previous_generation,
                    data_dir=rag.data_dir,
                    safety_bytes=self._safety_bytes(),
                    available_bytes=self._available_bytes,
                )
            except MemoryError:
                # Leave the durable job active. ExecStopPost performs rollback
                # and cleanup in a fresh process with available memory.
                raise
            except Exception as exc:
                self.queue.finish_requests(job_ids, succeeded=False)
                return self.queue.write_status(
                    state="failed",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    error=f"disk preflight failed: {type(exc).__name__}: {exc}",
                    removed_abandoned_work=removed_abandoned_work,
                )
            if preflight.available_bytes < preflight.required_bytes:
                error = (
                    "insufficient disk space for isolated index build: "
                    f"available={preflight.available_bytes} required={preflight.required_bytes}"
                )
                self.queue.finish_requests(job_ids, succeeded=False)
                return self.queue.write_status(
                    state="failed",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    serving_generations=sorted(serving_generations),
                    removed_generations=removed_generations,
                    cleanup_error=cleanup_error,
                    disk_preflight=preflight.as_dict(),
                    error=error,
                    removed_abandoned_work=removed_abandoned_work,
                )

            self.queue.write_status(
                state="snapshotting",
                job_ids=job_ids,
                force=force,
                reasons=reasons,
                previous_generation=previous_generation,
                serving_generations=sorted(serving_generations),
                removed_generations=removed_generations,
                cleanup_error=cleanup_error,
                disk_preflight=preflight.as_dict(),
                removed_abandoned_work=removed_abandoned_work,
            )
            self.queue.work_dir.mkdir(parents=True, exist_ok=True)
            try:
                with tempfile.TemporaryDirectory(
                    prefix="corpus-snapshot-",
                    dir=self.queue.work_dir,
                ) as temporary:
                    snapshot_dir = Path(temporary) / "corpus"
                    corpus_files = snapshot_corpus(rag.data_dir, snapshot_dir)
                    self.queue.write_status(
                        state="building",
                        job_ids=job_ids,
                        force=force,
                        reasons=reasons,
                        previous_generation=previous_generation,
                        serving_generations=sorted(serving_generations),
                        removed_generations=removed_generations,
                        cleanup_error=cleanup_error,
                        corpus_files=corpus_files,
                        disk_preflight=preflight.as_dict(),
                        removed_abandoned_work=removed_abandoned_work,
                    )
                    published_generation = self._build(snapshot_dir, force)
            except MemoryError:
                # Avoid allocating failure payloads while exhausted. The
                # out-of-process recovery guard owns rollback and cleanup.
                raise
            except Exception as exc:
                rollback_error = None
                try:
                    current = read_current_generation_id(rag.index_dir)
                    if current != previous_generation:
                        switch_current_generation(rag.index_dir, previous_generation)
                except MemoryError:
                    raise
                except Exception as rollback_exc:  # pragma: no cover - catastrophic path
                    rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"
                self.queue.finish_requests(job_ids, succeeded=False)
                return self.queue.write_status(
                    state="failed",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    serving_generations=sorted(serving_generations),
                    removed_generations=removed_generations,
                    cleanup_error=cleanup_error,
                    error=f"{type(exc).__name__}: {exc}",
                    rollback_error=rollback_error,
                    removed_abandoned_work=removed_abandoned_work,
                )

            current = read_current_generation_id(rag.index_dir)
            if current != published_generation:
                rollback_error = None
                try:
                    switch_current_generation(rag.index_dir, previous_generation)
                except MemoryError:
                    raise
                except Exception as exc:
                    rollback_error = f"{type(exc).__name__}: {exc}"
                self.queue.finish_requests(job_ids, succeeded=False)
                return self.queue.write_status(
                    state="failed",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    serving_generations=sorted(serving_generations),
                    removed_generations=removed_generations,
                    cleanup_error=cleanup_error,
                    error="builder result does not match CURRENT after publication",
                    rollback_error=rollback_error,
                    removed_abandoned_work=removed_abandoned_work,
                )

            try:
                seal_published_generation(rag.index_dir, published_generation)
            except MemoryError:
                raise
            except Exception as exc:
                rollback_error = None
                try:
                    switch_current_generation(rag.index_dir, previous_generation)
                except MemoryError:
                    raise
                except Exception as rollback_exc:
                    rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"
                self.queue.finish_requests(job_ids, succeeded=False)
                return self.queue.write_status(
                    state="failed",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    published_generation=published_generation,
                    error=f"failed to seal published generation: {type(exc).__name__}: {exc}",
                    rollback_error=rollback_error,
                    removed_abandoned_work=removed_abandoned_work,
                )

            if (
                published_generation == previous_generation
                and published_generation in serving_generations
            ):
                self.queue.finish_requests(job_ids, succeeded=True)
                return self.queue.write_status(
                    state="succeeded",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    published_generation=published_generation,
                    serving_generations=sorted(serving_generations),
                    removed_generations=removed_generations,
                    cleanup_error=cleanup_error,
                    activation_required=False,
                    message="Corpus produced the already-active immutable generation",
                    removed_abandoned_work=removed_abandoned_work,
                )
            if not self._auto_activate:
                try:
                    removed_generations = prune_old_generations(
                        rag.index_dir,
                        protected_generation_ids=(
                            {published_generation, previous_generation} | serving_generations
                        ),
                    )
                except MemoryError:
                    raise
                except Exception as exc:
                    cleanup_error = f"{type(exc).__name__}: {exc}"
                self.queue.finish_requests(job_ids, succeeded=True)
                return self.queue.write_status(
                    state="awaiting_activation",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    published_generation=published_generation,
                    serving_generations=sorted(serving_generations),
                    removed_generations=removed_generations,
                    cleanup_error=cleanup_error,
                    activation_required=True,
                    message=(
                        "Generation is fully validated and CURRENT is published; "
                        "the unified service remains pinned until a controlled restart"
                    ),
                    removed_abandoned_work=removed_abandoned_work,
                )

            self.queue.write_status(
                state="activating",
                job_ids=job_ids,
                force=force,
                reasons=reasons,
                previous_generation=previous_generation,
                published_generation=published_generation,
                serving_generations=sorted(serving_generations),
                removed_generations=removed_generations,
                cleanup_error=cleanup_error,
                removed_abandoned_work=removed_abandoned_work,
            )
            try:
                self._activate(published_generation)
            except MemoryError:
                raise
            except Exception as activation_exc:
                rollback_error = None
                rollback_succeeded = False
                try:
                    switch_current_generation(rag.index_dir, previous_generation)
                    self._activate(previous_generation)
                    rollback_succeeded = True
                except MemoryError:
                    raise
                except Exception as exc:
                    rollback_error = f"{type(exc).__name__}: {exc}"
                self.queue.finish_requests(job_ids, succeeded=False)
                return self.queue.write_status(
                    state="failed",
                    job_ids=job_ids,
                    force=force,
                    reasons=reasons,
                    previous_generation=previous_generation,
                    published_generation=published_generation,
                    serving_generations=sorted(serving_generations),
                    removed_generations=removed_generations,
                    cleanup_error=cleanup_error,
                    activation_error=f"{type(activation_exc).__name__}: {activation_exc}",
                    rollback_succeeded=rollback_succeeded,
                    rollback_error=rollback_error,
                    removed_abandoned_work=removed_abandoned_work,
                )

            try:
                removed_generations = prune_old_generations(
                    rag.index_dir,
                    protected_generation_ids={published_generation, previous_generation},
                )
            except MemoryError:
                raise
            except Exception as exc:
                cleanup_error = f"{type(exc).__name__}: {exc}"
            self.queue.finish_requests(job_ids, succeeded=True)
            return self.queue.write_status(
                state="succeeded",
                job_ids=job_ids,
                force=force,
                reasons=reasons,
                previous_generation=previous_generation,
                published_generation=published_generation,
                serving_generations=[published_generation],
                removed_generations=removed_generations,
                cleanup_error=cleanup_error,
                activation_required=False,
                message="Generation was built, activated, and verified through /api/health",
                removed_abandoned_work=removed_abandoned_work,
            )


def recover_interrupted_build(
    settings: Settings,
    *,
    queue: IndexBuildQueue | None = None,
    activate: Callable[[str], None] = activate_unified_generation,
    restart_service: bool | None = None,
) -> dict[str, Any]:
    """ExecStopPost guard for OOM/kill during build or activation.

    Normal terminal states are left untouched. For an interrupted active state,
    CURRENT is restored to the recorded previous generation. If publication or
    activation had begun, the old generation is restarted and health-checked.
    """

    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    service_result = os.getenv("SERVICE_RESULT", "")
    exit_status = os.getenv("EXIT_STATUS", "")
    host_stopping = False
    if restart_service is None:
        # systemd 239 may expose a cgroup-v1 OOM as signal/SIGKILL rather
        # than the newer oom-kill result. Both SIGKILL and a manual SIGTERM
        # during activation must restore process/pointer consistency. Only a
        # host shutdown transaction suppresses the unified restart.
        host_stopping = system_is_stopping()
        restart_service = not host_stopping
    queue = queue or IndexBuildQueue.from_settings(settings)
    with queue.builder_lock():
        status = _read_json(queue.status_path) or {}
        state = str(status.get("state", "idle"))
        if state not in _ACTIVE_STATES:
            return status or queue.write_status(state="idle")
        previous = status.get("previous_generation")
        job_ids = [
            str(item)
            for item in status.get("job_ids", [])
            if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{32}", item)
        ]
        rollback_error = None
        rollback_succeeded = False
        pointer_restored = False
        service_restart_skipped = False
        removed_abandoned_work: list[str] = []
        if isinstance(previous, str) and _FINAL_GENERATION_RE.fullmatch(previous):
            try:
                current = read_current_generation_id(settings.rag.index_dir)
                if current != previous:
                    switch_current_generation(settings.rag.index_dir, previous)
                pointer_restored = True
                restart_required = (
                    state in {"publishing", "activating"} or current != previous
                )
                if restart_required and restart_service:
                    activate(previous)
                elif restart_required:
                    service_restart_skipped = True
                rollback_succeeded = pointer_restored and not service_restart_skipped
            except MemoryError:
                raise
            except Exception as exc:
                rollback_error = f"{type(exc).__name__}: {exc}"
        else:
            rollback_error = "interrupted status did not contain a valid previous generation"
        try:
            removed_abandoned_work = cleanup_abandoned_build_work(queue)
        except MemoryError:
            raise
        except Exception as cleanup_exc:
            cleanup_error = f"{type(cleanup_exc).__name__}: {cleanup_exc}"
        else:
            cleanup_error = None
        queue.finish_requests(job_ids, succeeded=False)
        return queue.write_status(
            state="failed",
            job_ids=job_ids,
            previous_generation=previous,
            published_generation=status.get("published_generation"),
            error=f"isolated builder was interrupted during {state}",
            service_result=service_result or None,
            exit_status=exit_status or None,
            host_stopping=host_stopping,
            pointer_restored=pointer_restored,
            service_restart_skipped=service_restart_skipped,
            rollback_succeeded=rollback_succeeded,
            rollback_error=rollback_error,
            cleanup_error=cleanup_error,
            removed_abandoned_work=removed_abandoned_work,
        )


__all__ = [
    "DiskPreflight",
    "IndexBuildDispatchError",
    "IndexBuildQueue",
    "IndexBuildWorker",
    "calculate_disk_preflight",
    "cleanup_abandoned_build_work",
    "activate_unified_generation",
    "dispatch_systemd_builder",
    "prune_old_generations",
    "recover_interrupted_build",
    "seal_published_generation",
    "snapshot_corpus",
    "system_is_stopping",
    "wait_for_active_generation",
]
