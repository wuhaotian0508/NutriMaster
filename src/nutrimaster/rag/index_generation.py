from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


INDEX_GENERATION_VERSION = "retrieval-generation-v1"
INDEX_GENERATION_FILENAME = "retrieval_generation_v1.json"
INDEX_GENERATIONS_DIRNAME = "generations"
INDEX_CURRENT_FILENAME = "CURRENT"
INDEX_STAGING_PREFIX = ".staging-"
INDEX_ARTIFACT_FILENAMES = {
    "chunks": "chunks.pkl",
    "embeddings": "embeddings.npy",
    "embedding_norms": "embedding_norms.npy",
    "bm25": "bm25_sparse_v4.pkl",
    "field_keyword": "field_keyword_v3.sqlite3",
}
INDEX_OPTIONAL_ARTIFACT_FILENAMES = {
    # The dense incremental manifest is not needed to answer queries, but it
    # must be content-addressed whenever it is present.  Otherwise a later
    # incremental build could trust mutable metadata that was never part of
    # the immutable generation contract.
    "dense_manifest": "manifest.json",
    "graph": "graph_index.sqlite",
}

_GENERATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ResolvedGeneration:
    """One immutable retrieval generation selected for a process lifetime."""

    index_root: Path
    path: Path
    generation_id: str | None
    legacy: bool = False

    @property
    def index_dir(self) -> Path:
        """Compatibility alias for callers that name the resolved path index_dir."""

        return self.path


def file_sha256(path: Path, *, block_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        descriptor = file.fileno()
        if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_SEQUENTIAL"):
            try:
                os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_SEQUENTIAL)
            except OSError:
                pass
        try:
            while block := file.read(block_size):
                digest.update(block)
        finally:
            # Full validation streams several GiB. Drop the clean validation
            # pages so they do not remain charged to the service cgroup; the
            # actual mmap/SQLite query paths fault their working set on demand.
            if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
                try:
                    os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
                except OSError:
                    pass
    return digest.hexdigest()


def generation_manifest_path(index_dir: Path) -> Path:
    return Path(index_dir) / INDEX_GENERATION_FILENAME


def generations_path(index_root: Path) -> Path:
    return Path(index_root) / INDEX_GENERATIONS_DIRNAME


def current_generation_path(index_root: Path) -> Path:
    return Path(index_root) / INDEX_CURRENT_FILENAME


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_generation_id(value: str) -> str:
    if not isinstance(value, str) or not _GENERATION_ID_RE.fullmatch(value):
        raise RuntimeError("CURRENT contains an invalid retrieval generation id")
    if value in {".", ".."} or value.startswith(INDEX_STAGING_PREFIX):
        raise RuntimeError("CURRENT contains an invalid retrieval generation id")
    return value


def read_current_generation_id(index_root: Path) -> str:
    """Read a single safe generation id without accepting path traversal."""

    current_path = current_generation_path(index_root)
    if current_path.is_symlink():
        raise RuntimeError(f"retrieval generation pointer must not be a symlink: {current_path}")
    try:
        raw = current_path.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(f"retrieval generation pointer is missing: {current_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"retrieval generation pointer is unreadable: {exc}") from exc
    if len(raw) > 256:
        raise RuntimeError("CURRENT retrieval generation pointer is too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("CURRENT retrieval generation pointer is not UTF-8") from exc
    generation_id = text[:-1] if text.endswith("\n") else text
    if not generation_id or "\n" in generation_id or "\r" in generation_id:
        raise RuntimeError("CURRENT retrieval generation pointer is empty or malformed")
    if generation_id != generation_id.strip():
        raise RuntimeError("CURRENT retrieval generation pointer contains surrounding whitespace")
    return _validated_generation_id(generation_id)


def _read_generation_manifest(index_dir: Path) -> dict[str, Any]:
    manifest_path = generation_manifest_path(index_dir)
    if manifest_path.is_symlink():
        raise RuntimeError(f"retrieval generation manifest must not be a symlink: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"retrieval generation manifest is missing: {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"retrieval generation manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("retrieval generation manifest root must be an object")
    return payload


def _coerce_nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"retrieval generation {label} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"retrieval generation {label} is invalid") from exc
    if result < 0 or result != value:
        raise RuntimeError(f"retrieval generation {label} is invalid")
    return result


def write_generation_manifest(
    index_dir: Path,
    *,
    chunk_count: int,
    embedding_shape: tuple[int, ...] | list[int],
    corpus_fingerprint: str | None = None,
) -> Path:
    """Write the commit manifest inside a mutable staging generation.

    A direct child of ``generations/`` is immutable once it has a final name;
    only ``.staging-*`` children may be changed by this API.
    """

    index_dir = Path(index_dir).resolve()
    if (
        index_dir.parent.name == INDEX_GENERATIONS_DIRNAME
        and not index_dir.name.startswith(INDEX_STAGING_PREFIX)
    ):
        raise RuntimeError(f"published retrieval generation is immutable: {index_dir}")

    shape = [int(value) for value in embedding_shape]
    if len(shape) != 2 or any(value < 0 for value in shape):
        raise RuntimeError("retrieval generation embedding shape must be a two-dimensional shape")
    if shape[0] != int(chunk_count):
        raise RuntimeError("retrieval generation embedding rows do not match chunk count")

    artifacts: dict[str, dict[str, Any]] = {}
    for name, filename in INDEX_ARTIFACT_FILENAMES.items():
        path = index_dir / filename
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"required retrieval artifact is missing: {path}")
        artifacts[name] = {
            "filename": filename,
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
        }
    for name, filename in INDEX_OPTIONAL_ARTIFACT_FILENAMES.items():
        path = index_dir / filename
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"optional retrieval artifact is invalid: {path}")
        artifacts[name] = {
            "filename": filename,
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
        }

    chunks_hash = artifacts["chunks"]["sha256"]
    if corpus_fingerprint is not None and chunks_hash != corpus_fingerprint:
        raise RuntimeError("chunks changed while retrieval generation was being committed")
    payload = {
        "version": INDEX_GENERATION_VERSION,
        "chunk_count": int(chunk_count),
        "embedding_shape": shape,
        "corpus_fingerprint": chunks_hash,
        "artifacts": artifacts,
    }

    target = generation_manifest_path(index_dir)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("x", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, target)
        _fsync_directory(index_dir)
    finally:
        tmp_path.unlink(missing_ok=True)
    return target


def validate_generation_manifest(
    index_dir: Path,
    *,
    expected_chunks: int | None = None,
    expected_embedding_shape: tuple[int, ...] | list[int] | None = None,
    expected_corpus_fingerprint: str | None = None,
    verify_checksums: bool = True,
) -> dict[str, Any]:
    """Validate generation metadata, and optionally hash every artifact.

    ``verify_checksums=False`` is only used while pinning/loading a directory
    before the production startup gate performs its single full hash pass.
    Publication and the default public contract always verify checksums.
    """

    index_dir = Path(index_dir)
    payload = _read_generation_manifest(index_dir)
    if payload.get("version") != INDEX_GENERATION_VERSION:
        raise RuntimeError("retrieval generation manifest version is incompatible")

    chunk_count = _coerce_nonnegative_int(payload.get("chunk_count"), label="chunk count")
    if expected_chunks is not None and chunk_count != int(expected_chunks):
        raise RuntimeError("retrieval generation chunk count does not match the loaded corpus")

    embedding_shape = payload.get("embedding_shape")
    if (
        not isinstance(embedding_shape, list)
        or len(embedding_shape) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in embedding_shape)
        or embedding_shape[0] != chunk_count
    ):
        raise RuntimeError("retrieval generation embedding shape is invalid")
    if expected_embedding_shape is not None:
        expected_shape = [int(value) for value in expected_embedding_shape]
        if embedding_shape != expected_shape:
            raise RuntimeError("retrieval generation embedding shape does not match the loaded matrix")

    corpus_fingerprint = payload.get("corpus_fingerprint")
    if not isinstance(corpus_fingerprint, str) or not _SHA256_RE.fullmatch(corpus_fingerprint):
        raise RuntimeError("retrieval generation corpus fingerprint is invalid")
    if (
        expected_corpus_fingerprint is not None
        and corpus_fingerprint != expected_corpus_fingerprint
    ):
        raise RuntimeError("retrieval generation corpus fingerprint does not match chunks.pkl")

    manifest_artifacts = payload.get("artifacts")
    if not isinstance(manifest_artifacts, dict):
        raise RuntimeError("retrieval generation artifact table is invalid")
    required_names = set(INDEX_ARTIFACT_FILENAMES)
    allowed_names = required_names | set(INDEX_OPTIONAL_ARTIFACT_FILENAMES)
    if not required_names.issubset(manifest_artifacts) or not set(manifest_artifacts).issubset(
        allowed_names
    ):
        raise RuntimeError("retrieval generation artifact table is incomplete or contains unknown entries")

    artifact_filenames = {**INDEX_ARTIFACT_FILENAMES, **INDEX_OPTIONAL_ARTIFACT_FILENAMES}
    for name, entry in manifest_artifacts.items():
        filename = artifact_filenames[name]
        if not isinstance(entry, dict) or entry.get("filename") != filename:
            raise RuntimeError(f"retrieval generation entry is invalid: {name}")
        expected_size = _coerce_nonnegative_int(entry.get("size"), label=f"artifact size: {filename}")
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            raise RuntimeError(f"retrieval generation artifact checksum is invalid: {filename}")
        path = index_dir / filename
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"retrieval generation artifact is missing: {filename}")
        if expected_size != path.stat().st_size:
            raise RuntimeError(f"retrieval generation artifact size changed: {filename}")
        if verify_checksums and expected_hash != file_sha256(path):
            raise RuntimeError(f"retrieval generation artifact checksum changed: {filename}")

    if manifest_artifacts["chunks"]["sha256"] != corpus_fingerprint:
        raise RuntimeError("retrieval generation corpus fingerprint does not match chunks.pkl")
    return payload


def validate_generation(
    index_dir: Path,
    *,
    expected_chunks: int | None = None,
    expected_embedding_shape: tuple[int, ...] | list[int] | None = None,
    expected_corpus_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Fully validate dense shapes plus sparse artifact generation metadata."""

    index_dir = Path(index_dir)
    payload = validate_generation_manifest(
        index_dir,
        expected_chunks=expected_chunks,
        expected_embedding_shape=expected_embedding_shape,
        expected_corpus_fingerprint=expected_corpus_fingerprint,
    )
    chunk_count = int(payload["chunk_count"])
    embedding_shape = tuple(int(value) for value in payload["embedding_shape"])
    fingerprint = str(payload["corpus_fingerprint"])

    try:
        embeddings = np.load(index_dir / INDEX_ARTIFACT_FILENAMES["embeddings"], mmap_mode="r")
    except MemoryError:
        raise
    except Exception as exc:
        raise RuntimeError(f"retrieval generation embeddings.npy is unreadable: {exc}") from exc
    if embeddings.shape != embedding_shape:
        raise RuntimeError("retrieval generation embeddings.npy shape does not match manifest")
    del embeddings

    try:
        norms = np.load(index_dir / INDEX_ARTIFACT_FILENAMES["embedding_norms"], mmap_mode="r")
    except MemoryError:
        raise
    except Exception as exc:
        raise RuntimeError(f"retrieval generation embedding_norms.npy is unreadable: {exc}") from exc
    if norms.shape != (chunk_count,):
        raise RuntimeError("retrieval generation embedding_norms.npy shape does not match chunks")
    del norms

    try:
        from nutrimaster.rag.bm25 import BM25Retriever

        bm25 = BM25Retriever(index_dir)
        bm25_valid = bm25.load(
            expected_chunks=chunk_count,
            expected_fingerprint=fingerprint,
        )
    except MemoryError:
        raise
    except Exception as exc:
        raise RuntimeError(f"retrieval generation BM25 artifact is unreadable: {exc}") from exc
    if not bm25_valid:
        raise RuntimeError("retrieval generation BM25 artifact does not match the corpus")
    del bm25

    try:
        from nutrimaster.rag.field_keyword import FieldKeywordRetriever

        field_keyword = FieldKeywordRetriever(index_dir)
        field_valid = field_keyword.load(
            expected_chunks=chunk_count,
            expected_fingerprint=fingerprint,
        )
    except MemoryError:
        raise
    except Exception as exc:
        raise RuntimeError(f"retrieval generation field-keyword artifact is unreadable: {exc}") from exc
    if not field_valid:
        raise RuntimeError("retrieval generation field-keyword artifact does not match the corpus")

    if "graph" in payload["artifacts"]:
        import sqlite3

        from nutrimaster.rag.graph.index import GRAPH_INDEX_VERSION

        graph_path = index_dir / INDEX_OPTIONAL_ARTIFACT_FILENAMES["graph"]
        uri = f"{graph_path.resolve().as_uri()}?mode=ro&immutable=1"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name IN ('nodes', 'edges', 'metadata')"
                    )
                }
                populated = connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM nodes), EXISTS(SELECT 1 FROM edges)"
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"retrieval generation graph artifact is unreadable: {exc}") from exc
        if quick_check != ("ok",):
            raise RuntimeError("retrieval generation graph artifact failed SQLite quick_check")
        if tables != {"nodes", "edges", "metadata"} or populated != (1, 1):
            raise RuntimeError("retrieval generation graph artifact is empty or schema-incompatible")
        if metadata.get("version") != GRAPH_INDEX_VERSION:
            raise RuntimeError("retrieval generation graph artifact version is incompatible")
        if metadata.get("corpus_fingerprint") != fingerprint:
            raise RuntimeError("retrieval generation graph artifact does not match the corpus")
    return payload


def _generation_id_from_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_staging_generation(index_root: Path) -> Path:
    """Create a unique generation directory which is not visible to readers."""

    index_root = Path(index_root).resolve()
    index_root.mkdir(parents=True, exist_ok=True)
    generations_dir = generations_path(index_root)
    generations_dir.mkdir(parents=True, exist_ok=True)
    while True:
        staging = generations_dir / f"{INDEX_STAGING_PREFIX}{os.getpid()}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(mode=0o700)
        except FileExistsError:
            continue
        _fsync_directory(generations_dir)
        return staging


def _assert_owned_staging(index_root: Path, staging_dir: Path) -> tuple[Path, Path]:
    index_root = Path(index_root).resolve()
    staging_dir = Path(staging_dir).resolve()
    generations_dir = generations_path(index_root).resolve()
    if (
        staging_dir.parent != generations_dir
        or not staging_dir.name.startswith(INDEX_STAGING_PREFIX)
        or staging_dir.is_symlink()
        or not staging_dir.is_dir()
    ):
        raise RuntimeError(f"not a staging retrieval generation owned by {index_root}: {staging_dir}")
    return index_root, staging_dir


def discard_staging_generation(index_root: Path, staging_dir: Path) -> None:
    """Remove only a verified, unpublished staging directory."""

    _index_root, staging_dir = _assert_owned_staging(index_root, staging_dir)
    shutil.rmtree(staging_dir)


def copy_generation_files(
    source_dir: Path,
    staging_dir: Path,
    *,
    include_sparse: bool = True,
    sparse_artifact_names: set[str] | None = None,
    include_optional: bool = True,
    include_incremental_manifest: bool = True,
) -> None:
    """Copy a source snapshot into staging without linking mutable source files."""

    source_dir = Path(source_dir).resolve()
    staging_dir = Path(staging_dir).resolve()
    if source_dir == staging_dir:
        raise RuntimeError("retrieval generation source and staging directory must differ")
    if (
        staging_dir.parent.name != INDEX_GENERATIONS_DIRNAME
        or not staging_dir.name.startswith(INDEX_STAGING_PREFIX)
        or not staging_dir.is_dir()
    ):
        raise RuntimeError(f"retrieval generation copies may only target staging: {staging_dir}")
    filenames = [
        INDEX_ARTIFACT_FILENAMES["chunks"],
        INDEX_ARTIFACT_FILENAMES["embeddings"],
    ]
    if include_sparse:
        selected_sparse = (
            set(INDEX_ARTIFACT_FILENAMES) - {"chunks", "embeddings"}
            if sparse_artifact_names is None
            else set(sparse_artifact_names)
        )
        unknown_sparse = selected_sparse - set(INDEX_ARTIFACT_FILENAMES)
        if unknown_sparse:
            raise RuntimeError(
                f"unknown retrieval artifacts requested for staging: {sorted(unknown_sparse)}"
            )
        filenames.extend(
            filename
            for name, filename in INDEX_ARTIFACT_FILENAMES.items()
            if name in selected_sparse
        )
    if include_optional:
        filenames.extend(INDEX_OPTIONAL_ARTIFACT_FILENAMES.values())
    if include_incremental_manifest:
        # ``manifest.json`` is now an optional, checksummed generation
        # artifact. Keep the compatibility flag without copying it twice.
        if "manifest.json" not in filenames:
            filenames.append("manifest.json")

    for filename in filenames:
        source = source_dir / filename
        if not source.is_file() or source.is_symlink():
            continue
        destination = staging_dir / filename
        shutil.copy2(source, destination)
        _fsync_file(destination)
    _fsync_directory(staging_dir)


def finalize_staging_generation(index_root: Path, staging_dir: Path) -> ResolvedGeneration:
    """Atomically give a fully validated staging directory its immutable id."""

    index_root, staging_dir = _assert_owned_staging(index_root, staging_dir)
    payload = validate_generation(staging_dir)
    generation_id = _generation_id_from_payload(payload)
    final_dir = generations_path(index_root) / generation_id

    artifact_filenames = [
        entry["filename"] for entry in payload["artifacts"].values()
    ]
    for filename in (*artifact_filenames, INDEX_GENERATION_FILENAME):
        _fsync_file(staging_dir / filename)
    _fsync_directory(staging_dir)

    try:
        os.rename(staging_dir, final_dir)
    except OSError:
        if not final_dir.is_dir() or final_dir.is_symlink():
            raise
        existing_payload = validate_generation(final_dir)
        if _generation_id_from_payload(existing_payload) != generation_id:
            raise RuntimeError(f"retrieval generation id collision: {generation_id}")
        discard_staging_generation(index_root, staging_dir)
    _fsync_directory(generations_path(index_root))
    return ResolvedGeneration(
        index_root=index_root,
        path=final_dir,
        generation_id=generation_id,
        legacy=False,
    )


def _atomic_write_current(index_root: Path, generation_id: str) -> None:
    generation_id = _validated_generation_id(generation_id)
    current_path = current_generation_path(index_root)
    tmp_path = current_path.with_name(
        f".{INDEX_CURRENT_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with tmp_path.open("x", encoding="utf-8") as file:
            file.write(f"{generation_id}\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, current_path)
        _fsync_directory(index_root)
    finally:
        tmp_path.unlink(missing_ok=True)


def switch_current_generation(index_root: Path, generation_id: str) -> ResolvedGeneration:
    """Validate a final generation and atomically switch CURRENT to it."""

    index_root = Path(index_root).resolve()
    generation_id = _validated_generation_id(generation_id)
    final_dir = generations_path(index_root) / generation_id
    if final_dir.is_symlink() or not final_dir.is_dir():
        raise RuntimeError(f"retrieval generation directory is missing: {final_dir}")
    payload = validate_generation(final_dir)
    if _generation_id_from_payload(payload) != generation_id:
        raise RuntimeError("retrieval generation directory id does not match its manifest")
    _atomic_write_current(index_root, generation_id)
    return ResolvedGeneration(index_root, final_dir, generation_id, legacy=False)


def publish_generation(index_root: Path, staging_dir: Path) -> ResolvedGeneration:
    """Finalize all artifacts first, then atomically switch the CURRENT pointer."""

    resolved = finalize_staging_generation(index_root, staging_dir)
    # The exact inode tree was fully validated immediately before its atomic
    # rename. Avoid a second corpus-sized validation pass here.
    _atomic_write_current(resolved.index_root, str(resolved.generation_id))
    return resolved


def resolve_active_generation(
    index_root: Path,
    *,
    require_generation: bool = False,
    validate_artifact_contracts: bool = True,
) -> ResolvedGeneration:
    """Resolve CURRENT once, or explicitly allow a development-only flat index.

    An existing but malformed ``CURRENT`` is never ignored. This prevents a
    corrupt production deployment from silently serving unrelated flat files.
    """

    index_root = Path(index_root).resolve()
    current_path = current_generation_path(index_root)
    if not current_path.exists() and not current_path.is_symlink():
        if require_generation:
            raise RuntimeError(
                f"immutable retrieval generation is required but CURRENT is missing: {current_path}"
            )
        return ResolvedGeneration(index_root, index_root, None, legacy=True)

    generation_id = read_current_generation_id(index_root)
    generation_dir = generations_path(index_root) / generation_id
    if generation_dir.is_symlink() or not generation_dir.is_dir():
        raise RuntimeError(f"CURRENT retrieval generation directory is missing: {generation_dir}")
    payload = (
        validate_generation(generation_dir)
        if validate_artifact_contracts
        else validate_generation_manifest(generation_dir, verify_checksums=False)
    )
    if _generation_id_from_payload(payload) != generation_id:
        raise RuntimeError("CURRENT retrieval generation id does not match its manifest")
    return ResolvedGeneration(index_root, generation_dir, generation_id, legacy=False)


__all__ = [
    "INDEX_ARTIFACT_FILENAMES",
    "INDEX_CURRENT_FILENAME",
    "INDEX_GENERATIONS_DIRNAME",
    "INDEX_GENERATION_FILENAME",
    "INDEX_GENERATION_VERSION",
    "INDEX_OPTIONAL_ARTIFACT_FILENAMES",
    "ResolvedGeneration",
    "copy_generation_files",
    "create_staging_generation",
    "current_generation_path",
    "discard_staging_generation",
    "file_sha256",
    "finalize_staging_generation",
    "generation_manifest_path",
    "generations_path",
    "publish_generation",
    "read_current_generation_id",
    "resolve_active_generation",
    "switch_current_generation",
    "validate_generation",
    "validate_generation_manifest",
    "write_generation_manifest",
]
