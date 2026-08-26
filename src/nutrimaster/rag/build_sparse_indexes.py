from __future__ import annotations

import argparse
import gc
import json
import os
import pickle
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from nutrimaster.config.settings import Settings
from nutrimaster.rag.bm25 import BM25Retriever
from nutrimaster.rag.field_keyword import FieldKeywordRetriever
from nutrimaster.rag.index_generation import (
    INDEX_ARTIFACT_FILENAMES,
    INDEX_OPTIONAL_ARTIFACT_FILENAMES,
    copy_generation_files,
    create_staging_generation,
    current_generation_path,
    discard_staging_generation,
    file_sha256,
    generation_manifest_path,
    publish_generation,
    resolve_active_generation,
    write_generation_manifest,
)


def _positive_int_env(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    if value > maximum:
        raise RuntimeError(f"{name} must be at most {maximum}")
    return value


def _build_embedding_norms(index_dir: Path, embeddings: np.ndarray) -> Path:
    block_rows = _positive_int_env(
        "NUTRIMASTER_DENSE_NORM_BLOCK_ROWS",
        4096,
        maximum=65_536,
    )
    norms_path = index_dir / "embedding_norms.npy"
    tmp_path = norms_path.with_name(
        f".{norms_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    tmp_path.unlink(missing_ok=True)
    try:
        norms = np.lib.format.open_memmap(
            tmp_path,
            mode="w+",
            dtype=np.float32,
            shape=(embeddings.shape[0],),
        )
        for start in range(0, embeddings.shape[0], block_rows):
            end = min(start + block_rows, embeddings.shape[0])
            block = np.asarray(embeddings[start:end], dtype=np.float32)
            norms[start:end] = np.sqrt(np.einsum("ij,ij->i", block, block, optimize=True))
        norms.flush()
        del norms
        tmp_path.replace(norms_path)
    except MemoryError:
        # Cleanup is best-effort under allocator exhaustion and must never
        # replace the original MemoryError that JinaRetriever handles
        # specially to avoid reloading the corpus.
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return norms_path


_LEGACY_DENSE_FILENAMES = (
    INDEX_ARTIFACT_FILENAMES["chunks"],
    INDEX_ARTIFACT_FILENAMES["embeddings"],
    "manifest.json",
)


@dataclass(frozen=True)
class _LegacyFileSnapshot:
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int


def _regular_file_snapshot(path: Path) -> _LegacyFileSnapshot:
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f"legacy index artifact must not be a symlink: {path}")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeError(f"legacy index artifact must be a regular file: {path}")
    return _LegacyFileSnapshot(
        path=path,
        device=int(details.st_dev),
        inode=int(details.st_ino),
        size=int(details.st_size),
        mtime_ns=int(details.st_mtime_ns),
    )


def _link_legacy_dense_files(
    source_dir: Path,
    staging_dir: Path,
) -> tuple[_LegacyFileSnapshot, ...]:
    """Pin legacy dense artifacts without duplicating their disk blocks.

    This mode exists only for the one-time flat-index bootstrap.  It links the
    read-only dense snapshot and incremental manifest, then the caller builds
    every query-time derived artifact in private staging.  No old sparse or
    graph artifact is ever reused.
    """

    source_dir = Path(source_dir).resolve()
    staging_dir = Path(staging_dir).resolve()
    snapshots: list[_LegacyFileSnapshot] = []
    for filename in _LEGACY_DENSE_FILENAMES:
        source = source_dir / filename
        snapshot = _regular_file_snapshot(source)
        destination = staging_dir / filename
        try:
            os.link(source, destination, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(
                "legacy bootstrap requires same-filesystem hard links; "
                f"could not link {source} to private staging: {exc}"
            ) from exc
        linked = _regular_file_snapshot(destination)
        if (
            linked.device != snapshot.device
            or linked.inode != snapshot.inode
            or linked.size != snapshot.size
        ):
            raise RuntimeError(f"legacy bootstrap did not pin the expected inode: {source}")
        snapshots.append(snapshot)
    return tuple(snapshots)


def _assert_legacy_dense_files_unchanged(
    snapshots: tuple[_LegacyFileSnapshot, ...],
) -> None:
    for expected in snapshots:
        current = _regular_file_snapshot(expected.path)
        if (
            current.device != expected.device
            or current.inode != expected.inode
            or current.size != expected.size
            or current.mtime_ns != expected.mtime_ns
        ):
            raise RuntimeError(
                f"legacy index artifact changed during bootstrap: {expected.path}"
            )


def build_sparse_indexes(
    index_dir: Path,
    *,
    source_dir: Path | None = None,
    build_bm25: bool = True,
    build_field_keyword: bool = True,
    build_norms: bool = True,
    build_graph: bool = False,
    graph_data_dir: Path | None = None,
    link_legacy_dense: bool = False,
) -> dict[str, Any]:
    """Build a complete immutable query generation and atomically publish it.

    ``index_dir`` is the stable index root. Existing flat files or the current
    immutable generation are copied into a private staging directory first;
    no active artifact is ever opened for writing. Skip flags reuse a matching
    artifact from the source and still require the final generation to pass the
    complete validation contract. ``link_legacy_dense`` is reserved for the
    guarded one-time bootstrap: it pins only legacy chunks/embeddings/manifest
    by inode and requires every derived artifact to be rebuilt.
    """

    index_root = Path(index_dir).resolve()
    index_root.mkdir(parents=True, exist_ok=True)
    if source_dir is None:
        source = resolve_active_generation(
            index_root,
            require_generation=False,
            validate_artifact_contracts=False,
        ).path
    else:
        source = Path(source_dir).resolve()

    if link_legacy_dense and (
        not build_bm25
        or not build_field_keyword
        or not build_norms
        or not build_graph
        or graph_data_dir is None
    ):
        raise RuntimeError(
            "legacy bootstrap must rebuild norms, BM25, field-keyword, and graph artifacts"
        )

    staging_dir = create_staging_generation(index_root)
    published = None
    legacy_snapshots: tuple[_LegacyFileSnapshot, ...] = ()
    try:
        if link_legacy_dense:
            legacy_snapshots = _link_legacy_dense_files(source, staging_dir)
        else:
            reused_sparse = {
                name
                for name, should_build in (
                    ("embedding_norms", build_norms),
                    ("bm25", build_bm25),
                    ("field_keyword", build_field_keyword),
                )
                if not should_build
            }
            copy_generation_files(
                source,
                staging_dir,
                include_sparse=bool(reused_sparse),
                sparse_artifact_names=reused_sparse,
                # A rebuilt graph is created directly inside private staging.
                # Otherwise preserve only a graph already bound to this corpus.
                include_optional=not build_graph,
            )
        chunks_path = staging_dir / INDEX_ARTIFACT_FILENAMES["chunks"]
        embeddings_path = staging_dir / INDEX_ARTIFACT_FILENAMES["embeddings"]
        if not chunks_path.is_file() or not embeddings_path.is_file():
            raise FileNotFoundError("chunks.pkl and embeddings.npy must both exist in the source index")

        with chunks_path.open("rb") as file:
            chunks = pickle.load(file)
        embeddings = np.load(embeddings_path, mmap_mode="r")
        if embeddings.ndim != 2 or len(chunks) != embeddings.shape[0]:
            raise RuntimeError(
                f"index shape mismatch: chunks={len(chunks)} embeddings={embeddings.shape}"
            )
        chunk_count = len(chunks)
        embedding_shape = tuple(int(value) for value in embeddings.shape)
        fingerprint = file_sha256(chunks_path)

        if build_norms:
            _build_embedding_norms(staging_dir, embeddings)

        if build_bm25:
            bm25 = BM25Retriever(staging_dir)
            bm25.build(chunks, corpus_fingerprint=fingerprint)
            bm25.save()
            del bm25
            gc.collect()
            verifier = BM25Retriever(staging_dir)
            if not verifier.load(
                expected_chunks=chunk_count,
                expected_fingerprint=fingerprint,
            ):
                raise RuntimeError("BM25 artifact failed post-build validation")
            del verifier
            gc.collect()

        if build_field_keyword:
            field_keyword = FieldKeywordRetriever(staging_dir, chunks=chunks)
            field_keyword.build(chunks, corpus_fingerprint=fingerprint)
            field_keyword.save()
            del field_keyword
            verifier = FieldKeywordRetriever(staging_dir, chunks=chunks)
            if not verifier.load(
                expected_chunks=chunk_count,
                expected_fingerprint=fingerprint,
            ):
                raise RuntimeError("field-keyword artifact failed post-build validation")
            del verifier

        if build_graph:
            if graph_data_dir is None:
                raise RuntimeError("graph_data_dir is required when build_graph is enabled")
            from nutrimaster.rag.graph.index import LocalGraphIndex

            LocalGraphIndex(staging_dir / "graph_index.sqlite").build_from_corpus(
                graph_data_dir,
                corpus_fingerprint=fingerprint,
            )

        if legacy_snapshots:
            # Detect both in-place writes and atomic replacement of a legacy
            # source path before the linked inode tree can be published.
            _assert_legacy_dense_files_unchanged(legacy_snapshots)

        write_generation_manifest(
            staging_dir,
            chunk_count=chunk_count,
            embedding_shape=embedding_shape,
            corpus_fingerprint=fingerprint,
        )

        # Full validation loads compact BM25 metadata. Drop the canonical
        # chunks first so publication does not create an avoidable peak.
        del chunks, embeddings
        gc.collect()
        published = publish_generation(index_root, staging_dir)
    except MemoryError:
        # The isolated recovery path can remove abandoned staging if cleanup
        # itself cannot complete under pressure. Preserve the allocator signal
        # so JinaRetriever never mistakes it for a recoverable build failure.
        try:
            if staging_dir.exists():
                discard_staging_generation(index_root, staging_dir)
        except Exception:
            pass
        raise
    except Exception:
        if staging_dir.exists():
            discard_staging_generation(index_root, staging_dir)
        raise

    final_artifacts = {
        name: str(published.path / filename)
        for name, filename in INDEX_ARTIFACT_FILENAMES.items()
    }
    final_artifacts.update(
        {
            name: str(published.path / filename)
            for name, filename in INDEX_OPTIONAL_ARTIFACT_FILENAMES.items()
            if (published.path / filename).is_file()
        }
    )
    final_artifacts["generation_manifest"] = str(
        generation_manifest_path(published.path)
    )
    final_artifacts["current"] = str(current_generation_path(index_root))
    return {
        "index_dir": str(index_root),
        "source_dir": str(source),
        "generation_dir": str(published.path),
        "generation_id": published.generation_id,
        "chunks": chunk_count,
        "embedding_shape": list(embedding_shape),
        "corpus_fingerprint": fingerprint,
        "artifacts": final_artifacts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build compact NutriMaster query indexes offline")
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="optional dense source snapshot; defaults to CURRENT or the development flat index",
    )
    parser.add_argument("--skip-bm25", action="store_true")
    parser.add_argument("--skip-field-keyword", action="store_true")
    parser.add_argument("--skip-norms", action="store_true")
    parser.add_argument(
        "--build-graph",
        action="store_true",
        help="rebuild graph_index.sqlite from the corpus and bind it to this generation",
    )
    parser.add_argument(
        "--graph-data-dir",
        type=Path,
        help="verified corpus directory for --build-graph; defaults to configured RAG data_dir",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.index_dir is None:
        settings = Settings.from_env()
        if settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        index_dir = settings.rag.index_dir
    else:
        index_dir = args.index_dir
    graph_data_dir = args.graph_data_dir
    if args.build_graph and graph_data_dir is None:
        runtime_settings = Settings.from_env()
        if runtime_settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        graph_data_dir = runtime_settings.rag.data_dir
    result = build_sparse_indexes(
        index_dir,
        source_dir=args.source_dir,
        build_bm25=not args.skip_bm25,
        build_field_keyword=not args.skip_field_keyword,
        build_norms=not args.skip_norms,
        build_graph=args.build_graph,
        graph_data_dir=graph_data_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
