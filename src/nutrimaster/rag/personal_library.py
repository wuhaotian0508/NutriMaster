from __future__ import annotations

import json
import os
import pickle
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import requests

from nutrimaster.config.settings import RagSettings, Settings
from nutrimaster.rag.jina_proxy import jina_proxy_request_kwargs

_SAFE_FILENAME_RE = re.compile(r"[^\w\u4e00-\u9fff\-. ]", re.UNICODE)

_PERSONAL_DENSE_BLOCK_ROWS_ENV = "NUTRIMASTER_PERSONAL_DENSE_BLOCK_ROWS"
_PERSONAL_EMBED_BATCH_SIZE_ENV = "NUTRIMASTER_PERSONAL_EMBED_BATCH_SIZE"
_PERSONAL_MAX_CHUNKS_ENV = "NUTRIMASTER_PERSONAL_MAX_CHUNKS"
_PERSONAL_MAX_EXTRACTED_CHARS_ENV = "NUTRIMASTER_PERSONAL_MAX_EXTRACTED_CHARS"

_DEFAULT_DENSE_BLOCK_ROWS = 2_048
_MAX_DENSE_BLOCK_ROWS = 16_384
_DEFAULT_EMBED_BATCH_SIZE = 32
_MAX_EMBED_BATCH_SIZE = 128
_DEFAULT_MAX_CHUNKS = 10_000
_HARD_MAX_CHUNKS = 100_000
_DEFAULT_MAX_EXTRACTED_CHARS = 20_000_000
_HARD_MAX_EXTRACTED_CHARS = 100_000_000

_PERSONAL_INDEX_SCHEMA_VERSION = 1
_PERSONAL_GENERATION_RE = re.compile(r"[0-9a-f]{32}")
_PERSONAL_INDEX_ARTIFACTS = (
    "chunks.pkl",
    "embeddings.npy",
    "embedding_norms.npy",
    "manifest.json",
)
_PERSONAL_GENERATION_COMMIT = "COMMITTED.json"

# PDF extraction and embedding are the two largest request-local allocations.
# Serialize them across users so concurrent uploads cannot multiply that peak.
_GLOBAL_UPLOAD_SEMAPHORE = threading.BoundedSemaphore(value=1)


def _bounded_positive_int_env(name: str, default: int, *, maximum: int) -> int:
    """Read a positive integer setting while enforcing a hard upper bound."""
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer between 1 and {maximum}") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be an integer between 1 and {maximum}")
    return value


def sanitize_filename(name: str) -> str:
    """清洗文件名，移除不安全字符并规范化格式。

    将文件名中的特殊字符替换为下划线，合并连续的下划线或空格，
    去除首尾的下划线、点号和空格。若清洗后为空字符串，则生成
    基于时间戳的默认文件名。

    Args:
        name: 原始文件名，可包含路径前缀（会被自动去除）。

    Returns:
        清洗后的安全文件名字符串。仅保留字母、数字、中文、连字符、
        点号和空格。若结果为空则返回 ``upload_<timestamp>.pdf``。
    """
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = re.sub(r"[_ ]{2,}", "_", name).strip("_. ")
    return name or f"upload_{int(time.time())}.pdf"


class PersonalLibrary:
    """个人文库：为每个用户提供独立的 PDF 文件管理和向量检索功能。

    支持 PDF 文件的上传、文本提取、分块、嵌入向量计算和相似度搜索。
    每个用户的数据（PDF 文件、索引、清单）存储在独立的目录中。

    Attributes:
        user_id: 用户唯一标识。
        rag_settings: RAG 配置。
        base_dir: 用户文库根目录。
        pdf_dir: PDF 文件存储目录。
        index_dir: 索引文件存储目录。
        chunks: 已加载的文本分块列表。
        embeddings: 已加载的嵌入向量矩阵。
        manifest: 文件清单字典。
    """

    def __init__(
        self,
        user_id: str,
        *,
        rag_settings: RagSettings | None = None,
        embed_texts=None,
    ):
        """初始化个人文库实例。

        为指定用户创建或加载个人文库，包括 PDF 存储目录、向量索引目录
        以及文件清单。初始化时会自动从磁盘加载已有的索引数据。

        Args:
            user_id: 用户唯一标识符，用于隔离不同用户的文库数据。
            rag_settings: RAG 配置对象。若为 None，则从环境变量自动加载。
            embed_texts: 自定义文本嵌入函数，签名为
                ``(texts: list[str]) -> np.ndarray``。若为 None，则使用
                基于 Jina API 的默认嵌入方法。

        Raises:
            RuntimeError: 当 RAG 配置初始化失败时抛出。
        """
        self.user_id = user_id
        self.rag_settings = rag_settings or Settings.from_env().rag
        if self.rag_settings is None:
            raise RuntimeError("RAG settings failed to initialize")
        self._embed_texts = embed_texts or self._default_embed_texts
        self.base_dir = self.rag_settings.personal_lib_dir / user_id
        self.pdf_dir = self.base_dir / "pdfs"
        self.index_dir = self.base_dir / "index"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._generations_dir = self.index_dir / "generations"
        self._generations_dir.mkdir(parents=True, exist_ok=True)
        self._current_file = self.index_dir / "CURRENT"
        self._lock = threading.RLock()
        self._dense_block_rows = _bounded_positive_int_env(
            _PERSONAL_DENSE_BLOCK_ROWS_ENV,
            _DEFAULT_DENSE_BLOCK_ROWS,
            maximum=_MAX_DENSE_BLOCK_ROWS,
        )
        self._embedding_batch_size = _bounded_positive_int_env(
            _PERSONAL_EMBED_BATCH_SIZE_ENV,
            _DEFAULT_EMBED_BATCH_SIZE,
            maximum=_MAX_EMBED_BATCH_SIZE,
        )
        self._max_total_chunks = _bounded_positive_int_env(
            _PERSONAL_MAX_CHUNKS_ENV,
            _DEFAULT_MAX_CHUNKS,
            maximum=_HARD_MAX_CHUNKS,
        )
        self._max_extracted_chars = _bounded_positive_int_env(
            _PERSONAL_MAX_EXTRACTED_CHARS_ENV,
            _DEFAULT_MAX_EXTRACTED_CHARS,
            maximum=_HARD_MAX_EXTRACTED_CHARS,
        )
        self.chunks: list[dict[str, Any]] = []
        self.embeddings: np.ndarray | None = None
        self.embedding_norms: np.ndarray | None = None
        self.manifest: dict[str, Any] = {}
        self._active_generation: str | None = None
        self._active_index_dir = self.index_dir
        self._load_index()

    def _load_index(self) -> None:
        """从磁盘加载索引数据。

        ``CURRENT`` 是唯一的可见性边界。它只会指向已经完整落盘、校验并
        写入 ``COMMITTED.json`` 的不可变 generation。这样即使进程在写入
        任意一个大文件时被 SIGKILL/OOM，加载器看到的仍是上一代完整快照。

        没有 ``CURRENT`` 时兼容读取旧版平铺文件；第一次成功保存后即切换
        到 generation 布局，之后不会再读取这些旧文件。
        """
        with self._lock:
            active_generation: str | None = None
            snapshot_dir = self.index_dir
            if self._current_file.exists():
                marker = self._read_current_marker()
                candidates = [marker["generation"]]
                previous = marker.get("previous")
                if previous is not None and previous not in candidates:
                    candidates.append(previous)

                failures: list[str] = []
                loaded = None
                for generation in candidates:
                    try:
                        candidate_dir = self._generation_path(generation)
                        loaded = self._load_snapshot(
                            candidate_dir,
                            expected_generation=generation,
                        )
                    except MemoryError:
                        # Do not attempt another corpus load while the process is
                        # already out of memory; the supervisor must handle it.
                        raise
                    except Exception as exc:
                        failures.append(f"{generation}: {exc}")
                        continue
                    active_generation = generation
                    snapshot_dir = candidate_dir
                    break
                if loaded is None:
                    raise RuntimeError(
                        "personal library CURRENT has no complete generation: "
                        + "; ".join(failures)
                    )
            else:
                loaded = self._load_snapshot(self.index_dir)

            (
                loaded_chunks,
                loaded_embeddings,
                loaded_norms,
                loaded_manifest,
            ) = loaded

            # Assign only after the complete snapshot has passed validation so a
            # failed reload cannot leave this instance in a partially loaded state.
            self.chunks = loaded_chunks
            self.embeddings = loaded_embeddings
            self.embedding_norms = loaded_norms
            self.manifest = loaded_manifest
            self._active_generation = active_generation
            self._active_index_dir = snapshot_dir

    def _load_snapshot(
        self,
        snapshot_dir: Path,
        *,
        expected_generation: str | None = None,
    ) -> tuple[list[dict[str, Any]], np.ndarray | None, np.ndarray | None, dict[str, Any]]:
        """Load and validate one complete snapshot without mutating instance state."""
        chunks_file = snapshot_dir / "chunks.pkl"
        embeddings_file = snapshot_dir / "embeddings.npy"
        norms_file = snapshot_dir / "embedding_norms.npy"
        manifest_file = snapshot_dir / "manifest.json"

        commit: dict[str, Any] | None = None
        if expected_generation is not None:
            commit_file = snapshot_dir / _PERSONAL_GENERATION_COMMIT
            try:
                value = json.loads(commit_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError("generation commit marker is missing or unreadable") from exc
            if not isinstance(value, dict):
                raise RuntimeError("generation commit marker must contain an object")
            commit = value
            if commit.get("schema_version") != _PERSONAL_INDEX_SCHEMA_VERSION:
                raise RuntimeError("generation commit marker schema is unsupported")
            if commit.get("generation") != expected_generation:
                raise RuntimeError("generation commit marker identity does not match directory")
            artifacts = commit.get("artifacts")
            if not isinstance(artifacts, dict) or set(artifacts) != set(
                _PERSONAL_INDEX_ARTIFACTS
            ):
                raise RuntimeError("generation commit marker artifact list is incomplete")
            for filename in _PERSONAL_INDEX_ARTIFACTS:
                artifact = artifacts.get(filename)
                path = snapshot_dir / filename
                if (
                    not isinstance(artifact, dict)
                    or not isinstance(artifact.get("size"), int)
                    or artifact["size"] < 0
                    or not path.is_file()
                    or path.stat().st_size != artifact["size"]
                ):
                    raise RuntimeError(f"generation artifact is incomplete: {filename}")

        loaded_chunks: list[dict[str, Any]] = []
        loaded_embeddings: np.ndarray | None = None
        loaded_norms: np.ndarray | None = None
        loaded_manifest: dict[str, Any] = {}

        required_presence = (
            chunks_file.exists(),
            embeddings_file.exists(),
            manifest_file.exists(),
        )
        if any(required_presence) and not all(required_presence):
            raise RuntimeError(
                "personal library chunks, embeddings, and manifest must exist together"
            )
        if expected_generation is not None and not chunks_file.exists():
            raise RuntimeError("committed personal library generation has no index")
        if chunks_file.exists():
            try:
                with chunks_file.open("rb") as file:
                    value = pickle.load(file)
            except (OSError, pickle.UnpicklingError, EOFError) as exc:
                raise RuntimeError("personal library chunks.pkl is unreadable") from exc
            if not isinstance(value, list):
                raise RuntimeError("personal library chunks.pkl must contain a list")
            loaded_chunks = value
            try:
                loaded_embeddings = np.load(
                    embeddings_file,
                    mmap_mode="r",
                    allow_pickle=False,
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError("personal library embeddings.npy is unreadable") from exc
            if loaded_embeddings.ndim != 2:
                raise RuntimeError("personal library embeddings must be a 2-D matrix")
            if loaded_embeddings.shape[0] != len(loaded_chunks):
                raise RuntimeError(
                    "personal library chunks and embeddings row counts do not match"
                )
            if expected_generation is None:
                loaded_norms = self._load_or_build_norms(loaded_embeddings, norms_file)
            else:
                try:
                    loaded_norms = np.load(norms_file, mmap_mode="r", allow_pickle=False)
                except (OSError, ValueError) as exc:
                    raise RuntimeError("personal library embedding_norms.npy is unreadable") from exc
                if loaded_norms.ndim != 1 or loaded_norms.shape[0] != len(loaded_chunks):
                    raise RuntimeError(
                        "personal library embedding norms row count does not match chunks"
                    )
        if manifest_file.exists():
            try:
                value = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError("personal library manifest.json is unreadable") from exc
            if not isinstance(value, dict):
                raise RuntimeError("personal library manifest.json must contain an object")
            loaded_manifest = value
        elif expected_generation is not None:
            raise RuntimeError("committed personal library generation has no manifest")

        if commit is not None:
            expected_shape = commit.get("embedding_shape")
            expected_dtype = commit.get("embedding_dtype")
            if (
                loaded_embeddings is None
                or expected_shape != list(loaded_embeddings.shape)
                or expected_dtype != loaded_embeddings.dtype.str
                or commit.get("chunks") != len(loaded_chunks)
            ):
                raise RuntimeError("generation commit metadata does not match index contents")
        return loaded_chunks, loaded_embeddings, loaded_norms, loaded_manifest

    def _read_current_marker(self) -> dict[str, Any]:
        try:
            marker = json.loads(self._current_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("personal library CURRENT is unreadable") from exc
        if (
            not isinstance(marker, dict)
            or marker.get("schema_version") != _PERSONAL_INDEX_SCHEMA_VERSION
        ):
            raise RuntimeError("personal library CURRENT has an unsupported schema")
        self._validate_generation_id(marker.get("generation"))
        previous = marker.get("previous")
        if previous is not None:
            self._validate_generation_id(previous)
        return marker

    @staticmethod
    def _validate_generation_id(value: Any) -> str:
        if not isinstance(value, str) or _PERSONAL_GENERATION_RE.fullmatch(value) is None:
            raise RuntimeError("personal library generation id is invalid")
        return value

    def _generation_path(self, generation: str) -> Path:
        return self._generations_dir / self._validate_generation_id(generation)

    def _load_or_build_norms(
        self,
        embeddings: np.ndarray,
        norms_path: Path,
    ) -> np.ndarray:
        """Load row norms by mmap, rebuilding an absent/stale file in blocks."""
        embeddings_path = norms_path.with_name("embeddings.npy")
        norms_are_fresh = (
            norms_path.exists()
            and embeddings_path.exists()
            and norms_path.stat().st_mtime_ns >= embeddings_path.stat().st_mtime_ns
        )
        if norms_are_fresh:
            try:
                norms = np.load(norms_path, mmap_mode="r", allow_pickle=False)
                if norms.ndim == 1 and norms.shape[0] == embeddings.shape[0]:
                    return norms
            except (OSError, ValueError):
                pass

        temp_path = self._temporary_path(norms_path)
        norms_dtype = self._norm_dtype(embeddings.dtype)
        output: np.memmap | None = None
        try:
            output = np.lib.format.open_memmap(
                temp_path,
                mode="w+",
                dtype=norms_dtype,
                shape=(embeddings.shape[0],),
            )
            for start in range(0, embeddings.shape[0], self._dense_block_rows):
                end = min(start + self._dense_block_rows, embeddings.shape[0])
                block = np.asarray(embeddings[start:end], dtype=norms_dtype)
                output[start:end] = np.sqrt(
                    np.einsum("ij,ij->i", block, block, optimize=False)
                )
            output.flush()
            del output
            output = None
            self._fsync_file(temp_path)
            os.replace(temp_path, norms_path)
        except Exception:
            if output is not None:
                del output
            temp_path.unlink(missing_ok=True)
            raise
        return np.load(norms_path, mmap_mode="r", allow_pickle=False)

    @staticmethod
    def _norm_dtype(embedding_dtype: np.dtype[Any]) -> np.dtype[Any]:
        dtype = np.dtype(embedding_dtype)
        return np.dtype(np.float64 if dtype.itemsize > np.dtype(np.float32).itemsize else np.float32)

    @staticmethod
    def _temporary_path(target: Path) -> Path:
        return target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as file:
            os.fsync(file.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _array_blocks(self, matrix: np.ndarray) -> Iterator[np.ndarray]:
        for start in range(0, matrix.shape[0], self._dense_block_rows):
            end = min(start + self._dense_block_rows, matrix.shape[0])
            yield matrix[start:end]

    def _stage_embedding_files(
        self,
        *,
        target_dir: Path,
        embedding_blocks: Iterable[np.ndarray],
        embedding_shape: tuple[int, int],
        embedding_dtype: np.dtype[Any],
    ) -> tuple[Path, Path]:
        """Write embeddings and their row norms to private bounded-memory files."""
        embeddings_temp = target_dir / "embeddings.npy"
        norms_temp = target_dir / "embedding_norms.npy"
        dtype = np.dtype(embedding_dtype)
        if not np.issubdtype(dtype, np.floating):
            raise ValueError("personal library embeddings must use a floating-point dtype")
        if len(embedding_shape) != 2 or embedding_shape[0] < 0 or embedding_shape[1] <= 0:
            raise ValueError("personal library embeddings must have shape (rows, dimensions)")

        norms_dtype = self._norm_dtype(dtype)
        embedding_output: np.memmap | None = None
        norms_output: np.memmap | None = None
        try:
            embedding_output = np.lib.format.open_memmap(
                embeddings_temp,
                mode="w+",
                dtype=dtype,
                shape=embedding_shape,
            )
            norms_output = np.lib.format.open_memmap(
                norms_temp,
                mode="w+",
                dtype=norms_dtype,
                shape=(embedding_shape[0],),
            )
            offset = 0
            for raw_block in embedding_blocks:
                block = np.asarray(raw_block)
                if block.ndim != 2 or block.shape[1] != embedding_shape[1]:
                    raise ValueError("embedding block dimensions do not match the index")
                if block.shape[0] > self._dense_block_rows:
                    raise ValueError(
                        f"embedding blocks must contain at most {self._dense_block_rows} rows"
                    )
                end = offset + block.shape[0]
                if end > embedding_shape[0]:
                    raise ValueError("embedding blocks contain more rows than expected")
                numeric_block = np.asarray(block, dtype=dtype)
                if not np.all(np.isfinite(numeric_block)):
                    raise ValueError("personal library embeddings must contain only finite values")
                embedding_output[offset:end] = numeric_block
                norm_block = np.asarray(numeric_block, dtype=norms_dtype)
                norms_output[offset:end] = np.sqrt(
                    np.einsum("ij,ij->i", norm_block, norm_block, optimize=False)
                )
                offset = end
            if offset != embedding_shape[0]:
                raise ValueError(
                    f"embedding blocks contain {offset} rows; expected {embedding_shape[0]}"
                )
            embedding_output.flush()
            norms_output.flush()
            del embedding_output
            del norms_output
            embedding_output = None
            norms_output = None
            self._fsync_file(embeddings_temp)
            self._fsync_file(norms_temp)
            return embeddings_temp, norms_temp
        except Exception:
            if embedding_output is not None:
                del embedding_output
            if norms_output is not None:
                del norms_output
            embeddings_temp.unlink(missing_ok=True)
            norms_temp.unlink(missing_ok=True)
            raise

    def _write_json_fsync(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

    def _stage_current_marker(
        self,
        generation: str,
        *,
        previous: str | None,
    ) -> Path:
        marker_temp = self._temporary_path(self._current_file)
        try:
            self._write_json_fsync(
                marker_temp,
                {
                    "schema_version": _PERSONAL_INDEX_SCHEMA_VERSION,
                    "generation": self._validate_generation_id(generation),
                    "previous": previous,
                },
            )
            return marker_temp
        except Exception:
            marker_temp.unlink(missing_ok=True)
            raise

    def _save_index(
        self,
        *,
        chunks: list[dict[str, Any]] | None = None,
        manifest: dict[str, Any] | None = None,
        embedding_blocks: Iterable[np.ndarray] | None = None,
        embedding_shape: tuple[int, int] | None = None,
        embedding_dtype: np.dtype[Any] | None = None,
    ) -> None:
        """Persist a complete immutable generation, then atomically publish it.

        Large artifacts are written below a private transaction directory. A
        commit marker is fsynced only after all artifacts are durable and pass a
        read-back validation. Renaming ``CURRENT`` is the sole visibility change;
        a process death before it leaves the prior generation selected.
        """
        with self._lock:
            chunks_to_save = self.chunks if chunks is None else chunks
            manifest_to_save = self.manifest if manifest is None else manifest
            if embedding_blocks is None:
                if self.embeddings is None:
                    if chunks_to_save:
                        raise RuntimeError("cannot save chunks without embeddings")
                else:
                    embedding_shape = tuple(self.embeddings.shape)
                    embedding_dtype = self.embeddings.dtype
                    embedding_blocks = self._array_blocks(self.embeddings)
            if embedding_blocks is None or embedding_shape is None or embedding_dtype is None:
                raise RuntimeError("an embedding matrix is required to save the personal index")
            if embedding_shape[0] != len(chunks_to_save):
                raise ValueError("personal library chunks and embeddings row counts do not match")

            generation = uuid.uuid4().hex
            staging_dir = self._generations_dir / f".{generation}.tmp"
            generation_dir = self._generation_path(generation)
            current_temp: Path | None = None
            generation_published = False
            current_published = False
            validated: tuple[
                list[dict[str, Any]],
                np.ndarray | None,
                np.ndarray | None,
                dict[str, Any],
            ] | None = None
            try:
                staging_dir.mkdir(mode=0o700)
                chunks_path = staging_dir / "chunks.pkl"
                manifest_path = staging_dir / "manifest.json"
                with chunks_path.open("wb") as file:
                    pickle.dump(chunks_to_save, file, protocol=pickle.HIGHEST_PROTOCOL)
                    file.flush()
                    os.fsync(file.fileno())
                self._write_json_fsync(manifest_path, manifest_to_save)
                self._stage_embedding_files(
                    target_dir=staging_dir,
                    embedding_blocks=embedding_blocks,
                    embedding_shape=embedding_shape,
                    embedding_dtype=embedding_dtype,
                )
                commit_payload = {
                    "schema_version": _PERSONAL_INDEX_SCHEMA_VERSION,
                    "generation": generation,
                    "created_at_ns": time.time_ns(),
                    "chunks": len(chunks_to_save),
                    "embedding_shape": list(embedding_shape),
                    "embedding_dtype": np.dtype(embedding_dtype).str,
                    "artifacts": {
                        filename: {"size": (staging_dir / filename).stat().st_size}
                        for filename in _PERSONAL_INDEX_ARTIFACTS
                    },
                }
                self._write_json_fsync(
                    staging_dir / _PERSONAL_GENERATION_COMMIT,
                    commit_payload,
                )
                self._fsync_directory(staging_dir)

                os.replace(staging_dir, generation_dir)
                generation_published = True
                self._fsync_directory(self._generations_dir)

                # The final directory is still invisible until CURRENT moves.
                # Read it back once from its stable pathname and retain that
                # validated mmap snapshot for the live instance.  Reloading the
                # same generation again after CURRENT commits would create a
                # needless second chunks object graph at the worst memory point.
                validated = self._load_snapshot(
                    generation_dir,
                    expected_generation=generation,
                )

                previous = self._active_generation
                current_temp = self._stage_current_marker(
                    generation,
                    previous=previous,
                )
                os.replace(current_temp, self._current_file)
                current_temp = None
                current_published = True
                self._fsync_directory(self.index_dir)
                (
                    self.chunks,
                    self.embeddings,
                    self.embedding_norms,
                    self.manifest,
                ) = validated
                self._active_generation = generation
                self._active_index_dir = generation_dir
            finally:
                if current_temp is not None:
                    current_temp.unlink(missing_ok=True)
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
                # Caught failures before CURRENT changes are fully recoverable;
                # discard their now-unreferenced generation. A SIGKILL may leave
                # the same directory behind, but it remains invisible forever.
                if generation_published and not current_published and generation_dir.exists():
                    shutil.rmtree(generation_dir)

    def upload_pdf(self, file_storage, filename: str) -> dict:
        """上传并索引一个 PDF 文件。

        将 PDF 文件保存到用户的文库目录，提取文本内容，按页分块后计算
        嵌入向量，并更新索引和文件清单。上传前会检查文件数量和大小限制。

        Args:
            file_storage: 类文件对象（如 Flask 的 ``FileStorage``），需提供
                ``save(path)`` 方法。
            filename: 原始文件名，会经过 ``sanitize_filename`` 清洗。

        Returns:
            该文件的元信息字典，包含 ``num_pages``（页数）、``num_chunks``（分块数）、
            ``upload_time``（上传时间）和 ``size_mb``（文件大小，单位 MB）。

        Raises:
            ValueError: 当超出文件数量上限、文件过大或 PDF 无法提取文本时抛出。
        """
        with _GLOBAL_UPLOAD_SEMAPHORE, self._lock:
            if len(self.manifest) >= self.rag_settings.max_files_per_user:
                raise ValueError(f"最多上传 {self.rag_settings.max_files_per_user} 个文件")
            safe_name = sanitize_filename(filename)

            pdf_path = self.pdf_dir / safe_name
            upload_temp = self._temporary_path(pdf_path)
            pdf_backup: Path | None = None
            pdf_published = False
            index_saved = False
            try:
                file_storage.save(str(upload_temp))
                size_mb = upload_temp.stat().st_size / (1024 * 1024)
                if size_mb > self.rag_settings.max_pdf_size_mb:
                    raise ValueError(
                        f"文件过大 ({size_mb:.1f}MB)，最大 "
                        f"{self.rag_settings.max_pdf_size_mb}MB"
                    )
                pages_text = self._extract_pdf_text(
                    upload_temp,
                    max_chars=self._max_extracted_chars,
                )
                if not pages_text:
                    raise ValueError("PDF 无法提取文本（可能是扫描件）")
                extracted_chars = sum(len(text) for _, text in pages_text)
                if extracted_chars > self._max_extracted_chars:
                    raise ValueError(
                        f"PDF 可提取文本过多 ({extracted_chars} 字符)，最大 "
                        f"{self._max_extracted_chars} 字符"
                    )

                new_chunks = self._chunk_pages(safe_name, pages_text)
                new_total = len(self.chunks) + len(new_chunks)
                if new_total > self._max_total_chunks:
                    raise ValueError(
                        f"个人文库分块总数将达到 {new_total}，最大 "
                        f"{self._max_total_chunks}"
                    )

                # Both expansion gates run before the remote embedding call.
                new_embeddings = self._validate_embedding_matrix(
                    self._embed_texts([chunk["content"] for chunk in new_chunks]),
                    expected_rows=len(new_chunks),
                )
                existing = self.embeddings
                if existing is not None:
                    if existing.ndim != 2 or existing.shape[0] != len(self.chunks):
                        raise RuntimeError("loaded personal library embeddings are inconsistent")
                    if existing.shape[1] != new_embeddings.shape[1]:
                        raise ValueError(
                            "new embeddings use a different dimension from the existing index"
                        )
                    output_dtype = np.result_type(existing.dtype, new_embeddings.dtype)
                    output_shape = (new_total, existing.shape[1])

                    def embedding_blocks() -> Iterator[np.ndarray]:
                        yield from self._array_blocks(existing)
                        yield from self._array_blocks(new_embeddings)

                else:
                    if self.chunks:
                        raise RuntimeError("personal library chunks exist without embeddings")
                    output_dtype = new_embeddings.dtype
                    output_shape = tuple(new_embeddings.shape)

                    def embedding_blocks() -> Iterator[np.ndarray]:
                        yield from self._array_blocks(new_embeddings)

                candidate_chunks = [*self.chunks, *new_chunks]
                file_metadata = {
                    "num_pages": len(pages_text),
                    "num_chunks": len(new_chunks),
                    "upload_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "size_mb": round(size_mb, 2),
                }
                candidate_manifest = {**self.manifest, safe_name: file_metadata}

                # Publish the PDF first, but remove it if the still-private index
                # snapshot cannot be committed. Existing index artifacts remain
                # untouched on every pre-commit exception.
                if pdf_path.exists():
                    pdf_backup = self._temporary_path(pdf_path).with_suffix(".bak")
                    os.link(pdf_path, pdf_backup)
                os.replace(upload_temp, pdf_path)
                pdf_published = True
                self._save_index(
                    chunks=candidate_chunks,
                    manifest=candidate_manifest,
                    embedding_blocks=embedding_blocks(),
                    embedding_shape=output_shape,
                    embedding_dtype=output_dtype,
                )
                index_saved = True
                del new_embeddings
                return dict(self.manifest[safe_name])
            finally:
                upload_temp.unlink(missing_ok=True)
                if pdf_published and not index_saved:
                    if pdf_backup is None:
                        pdf_path.unlink(missing_ok=True)
                    else:
                        os.replace(pdf_backup, pdf_path)
                        pdf_backup = None
                if pdf_backup is not None:
                    pdf_backup.unlink(missing_ok=True)

    def _validate_embedding_matrix(
        self,
        value: Any,
        *,
        expected_rows: int,
    ) -> np.ndarray:
        """Validate an embedding result without a matrix-sized finite mask."""
        matrix = np.asarray(value)
        if matrix.ndim != 2 or matrix.shape[0] != expected_rows or matrix.shape[1] <= 0:
            raise ValueError(
                f"embedding result must have shape ({expected_rows}, dimensions)"
            )
        if not np.issubdtype(matrix.dtype, np.floating):
            raise ValueError("embedding result must use a floating-point dtype")
        for block in self._array_blocks(matrix):
            if not np.all(np.isfinite(block)):
                raise ValueError("embedding result contains non-finite values")
        return matrix

    @staticmethod
    def _extract_pdf_text(
        pdf_path: Path,
        *,
        max_chars: int | None = None,
    ) -> list[tuple[int, str]]:
        """从 PDF 文件中提取各页文本内容。

        使用 PyMuPDF (fitz) 库逐页提取文本，跳过内容为空的页面。

        Args:
            pdf_path: PDF 文件的路径。

        Returns:
            包含 ``(页码, 文本内容)`` 元组的列表，页码从 1 开始计数。
            仅包含有非空文本的页面。

        Raises:
            ImportError: 当未安装 PyMuPDF 时抛出。
            ValueError: 当 PDF 解析失败时抛出。
        """
        try:
            import fitz
        except ImportError as exc:
            raise ImportError("需要安装 PyMuPDF: pip install PyMuPDF") from exc
        try:
            doc = fitz.open(str(pdf_path))
            try:
                pages: list[tuple[int, str]] = []
                extracted_chars = 0
                for page in doc:
                    text = page.get_text()
                    if not text.strip():
                        continue
                    extracted_chars += len(text)
                    if max_chars is not None and extracted_chars > max_chars:
                        raise ValueError(
                            f"PDF 可提取文本过多 ({extracted_chars} 字符)，最大 "
                            f"{max_chars} 字符"
                        )
                    pages.append((page.number + 1, text))
                return pages
            finally:
                doc.close()
        except ValueError:
            raise
        except MemoryError:
            raise
        except Exception as exc:
            raise ValueError(f"PDF 解析失败: {exc}") from exc

    def _chunk_pages(self, filename: str, pages_text: list[tuple[int, str]]) -> list[dict]:
        """将各页文本按固定窗口大小进行分块。

        使用滑动窗口方式将每一页的文本切分为多个重叠的文本块，
        窗口大小和重叠量由 RAG 配置决定。每个分块包含来源、标题、
        内容和元数据信息。

        Args:
            filename: 清洗后的文件名，用于标记分块的来源。
            pages_text: 由 ``_extract_pdf_text`` 返回的 ``(页码, 文本)`` 列表。

        Returns:
            分块字典列表，每个字典包含 ``source_type``、``title``、``content``、
            ``url``、``score`` 和 ``metadata``（含 ``filename`` 与 ``page``）字段。
        """
        chunks = []
        step = max(self.rag_settings.chunk_size - self.rag_settings.chunk_overlap, 1)
        for page_num, text in pages_text:
            start = 0
            while start < len(text):
                chunk_text = text[start:start + self.rag_settings.chunk_size]
                if chunk_text.strip():
                    chunks.append(
                        {
                            "source_type": "personal",
                            "title": filename,
                            "content": chunk_text.strip(),
                            "url": "",
                            "score": 0.0,
                            "metadata": {"filename": filename, "page": page_num},
                        }
                    )
                start += step
        return chunks

    def delete_file(self, filename: str) -> bool:
        """从个人文库中删除指定文件及其索引数据。

        删除磁盘上的 PDF 文件，移除该文件对应的所有文本分块和嵌入向量，
        并更新文件清单和持久化索引。

        Args:
            filename: 要删除的文件名（清洗后的安全文件名）。

        Returns:
            删除成功返回 True；若文件不在清单中则返回 False。
        """
        with self._lock:
            if filename not in self.manifest:
                return False
            if self.embeddings is None:
                raise RuntimeError("personal library manifest exists without embeddings")
            keep = [
                index
                for index, chunk in enumerate(self.chunks)
                if chunk["metadata"].get("filename") != filename
            ]
            candidate_chunks = [self.chunks[index] for index in keep]
            candidate_manifest = dict(self.manifest)
            del candidate_manifest[filename]
            existing = self.embeddings

            def embedding_blocks() -> Iterator[np.ndarray]:
                for start in range(0, len(keep), self._dense_block_rows):
                    indices = np.asarray(
                        keep[start:start + self._dense_block_rows],
                        dtype=np.intp,
                    )
                    yield existing[indices]

            self._save_index(
                chunks=candidate_chunks,
                manifest=candidate_manifest,
                embedding_blocks=embedding_blocks(),
                embedding_shape=(len(keep), existing.shape[1]),
                embedding_dtype=existing.dtype,
            )
            (self.pdf_dir / filename).unlink(missing_ok=True)
            return True

    def rename_file(self, old_name: str, new_name: str) -> bool:
        """重命名个人文库中的文件。

        更新磁盘上的 PDF 文件名、所有相关分块的元数据以及文件清单，
        新文件名会经过 ``sanitize_filename`` 清洗。

        Args:
            old_name: 当前文件名。
            new_name: 新文件名（会被清洗处理）。

        Returns:
            重命名成功返回 True；若原文件名不在清单中则返回 False。
        """
        with self._lock:
            if old_name not in self.manifest:
                return False
            safe_new = sanitize_filename(new_name)
            if safe_new != old_name and safe_new in self.manifest:
                raise ValueError(f"文件已存在: {safe_new}")

            candidate_chunks: list[dict[str, Any]] = []
            for chunk in self.chunks:
                if chunk["metadata"].get("filename") != old_name:
                    candidate_chunks.append(chunk)
                    continue
                updated = dict(chunk)
                updated["metadata"] = {**chunk["metadata"], "filename": safe_new}
                updated["title"] = safe_new
                candidate_chunks.append(updated)
            candidate_manifest = dict(self.manifest)
            metadata = candidate_manifest.pop(old_name)
            candidate_manifest[safe_new] = metadata

            old_path = self.pdf_dir / old_name
            new_path = self.pdf_dir / safe_new
            renamed = False
            if old_path != new_path and old_path.exists():
                if new_path.exists():
                    raise ValueError(f"文件已存在: {safe_new}")
                os.replace(old_path, new_path)
                renamed = True
            try:
                self._save_index(
                    chunks=candidate_chunks,
                    manifest=candidate_manifest,
                )
            except Exception:
                if renamed:
                    os.replace(new_path, old_path)
                raise
            return True

    def list_files(self) -> list[dict]:
        """列出个人文库中的所有文件。

        Returns:
            文件信息字典列表，每个字典包含 ``filename``（文件名）及其
            元信息（``num_pages``、``num_chunks``、``upload_time``、``size_mb``）。
        """
        with self._lock:
            return [
                {"filename": filename, **dict(meta)}
                for filename, meta in self.manifest.items()
            ]

    def search(self, query_embedding: np.ndarray, top_k: int = 10) -> list[dict]:
        """基于余弦相似度的向量搜索。

        计算查询向量与所有分块嵌入向量之间的余弦相似度，返回相似度
        最高的 top_k 个分块。

        Args:
            query_embedding: 查询文本的嵌入向量，形状为 ``(dim,)``。
            top_k: 返回的最大结果数量，默认为 10。

        Returns:
            按相似度降序排列的分块字典列表，每个字典在原始分块信息
            基础上额外包含 ``score``（余弦相似度得分）字段。
            若索引为空则返回空列表。
        """
        with self._lock:
            if self.embeddings is None or len(self.embeddings) == 0:
                return []
            if self.embedding_norms is None:
                self.embedding_norms = self._load_or_build_norms(
                    self.embeddings,
                    self._active_index_dir / "embedding_norms.npy",
                )
            query = np.asarray(query_embedding)
            if (
                query.ndim != 1
                or query.shape[0] != self.embeddings.shape[1]
                or not np.issubdtype(query.dtype, np.number)
                or not np.all(np.isfinite(query))
            ):
                raise ValueError(
                    "query embedding must be a finite vector matching the index dimension"
                )

            query_norm = float(np.linalg.norm(query))
            score_dtype = np.result_type(self.embeddings.dtype, query.dtype, np.float32)
            scores = np.empty(self.embeddings.shape[0], dtype=score_dtype)
            for start in range(0, self.embeddings.shape[0], self._dense_block_rows):
                end = min(start + self._dense_block_rows, self.embeddings.shape[0])
                dots = np.dot(self.embeddings[start:end], query)
                denominators = (
                    np.asarray(self.embedding_norms[start:end], dtype=score_dtype)
                    * query_norm
                    + 1e-9
                )
                scores[start:end] = dots / denominators

            result_count = min(max(0, int(top_k)), len(scores))
            # Preserve the legacy deterministic tie rule: np.argsort followed
            # by reversal ranks the highest original row index first on a tie.
            indices = np.argsort(scores)[::-1][:result_count]
            results = []
            for index in indices:
                item = dict(self.chunks[int(index)])
                item["score"] = float(scores[int(index)])
                results.append(item)
            return results

    def _default_embed_texts(
        self,
        texts: list[str],
        batch_size: int | None = None,
    ) -> np.ndarray:
        """使用 Jina Embedding API 计算文本嵌入向量。

        调用 Jina 的嵌入服务，将文本列表转换为稠密向量表示。
        模型名称和 API 地址由 RAG 配置决定。

        Args:
            texts: 待嵌入的文本列表。
            batch_size: 每次请求的文本数量，默认读取个人库配置（32），硬上限 128。

        Returns:
            形状为 ``(len(texts), embedding_dim)`` 的嵌入向量矩阵。

        Raises:
            RuntimeError: 当 JINA_API_KEY 未配置时抛出。
            requests.HTTPError: 当 API 请求失败时抛出。
        """
        api_key = Settings.from_env().jina_api_key
        if not api_key:
            raise RuntimeError("JINA_API_KEY is required")
        effective_batch_size = self._embedding_batch_size if batch_size is None else batch_size
        try:
            effective_batch_size = int(effective_batch_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding batch_size must be an integer between 1 and 128") from exc
        if not 1 <= effective_batch_size <= _MAX_EMBED_BATCH_SIZE:
            raise ValueError("embedding batch_size must be an integer between 1 and 128")
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        output: np.ndarray | None = None
        output_dimension: int | None = None
        with jina_proxy_request_kwargs() as request_kwargs:
            for start in range(0, len(texts), effective_batch_size):
                batch = texts[start:start + effective_batch_size]
                response = requests.post(
                    self.rag_settings.jina_embedding_url,
                    json={
                        "model": self.rag_settings.embedding_model,
                        "input": batch,
                        "task": "retrieval.passage",
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=60,
                    **request_kwargs,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list) or len(data) != len(batch):
                    raise RuntimeError(
                        "Jina embedding response count does not match the request batch"
                    )
                try:
                    batch_embeddings = np.asarray(
                        [item["embedding"] for item in data],
                        dtype=np.float32,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("Jina embedding response is malformed") from exc
                if batch_embeddings.ndim != 2 or batch_embeddings.shape[1] <= 0:
                    raise RuntimeError("Jina embedding response must contain non-empty vectors")
                if not np.all(np.isfinite(batch_embeddings)):
                    raise RuntimeError("Jina embedding response contains non-finite values")
                if output_dimension is None:
                    output_dimension = batch_embeddings.shape[1]
                    output = np.empty((len(texts), output_dimension), dtype=np.float32)
                elif batch_embeddings.shape[1] != output_dimension:
                    raise RuntimeError("Jina embedding dimensions changed between batches")
                output[start:start + len(batch)] = batch_embeddings

        if output is None:  # Guarded by the non-empty texts check above.
            raise RuntimeError("Jina embedding response was empty")
        return output


class PersonalLibraryService:
    """个人文库服务层，为每个用户的个人文库操作提供稳定的接口边界。

    作为 ``PersonalLibrary`` 的代理层，封装底层文库实例，对外提供
    统一的上传、列表、删除、重命名和搜索接口。
    """

    def __init__(self, library: Any):
        """初始化个人文库服务。

        Args:
            library: ``PersonalLibrary`` 实例，作为底层文库操作的委托对象。
        """
        self._library = library

    def upload_pdf(self, file_storage: Any, filename: str) -> dict:
        """上传 PDF 文件到个人文库。

        Args:
            file_storage: 类文件对象（如 Flask 的 ``FileStorage``），需提供
                ``save(path)`` 方法。
            filename: 原始文件名。

        Returns:
            上传文件的元信息字典。
        """
        return self._library.upload_pdf(file_storage, filename)

    def list_files(self) -> list[dict]:
        """列出个人文库中的所有文件。

        Returns:
            文件信息字典列表。
        """
        return self._library.list_files()

    def delete_file(self, filename: str) -> bool:
        """删除个人文库中的指定文件。

        Args:
            filename: 要删除的文件名。

        Returns:
            删除成功返回 True，文件不存在返回 False。
        """
        return self._library.delete_file(filename)

    def rename_file(self, filename: str, new_name: str) -> bool:
        """重命名个人文库中的指定文件。

        Args:
            filename: 当前文件名。
            new_name: 新文件名。

        Returns:
            重命名成功返回 True，文件不存在返回 False。
        """
        return self._library.rename_file(filename, new_name)

    def search(self, query_embedding: Any, top_k: int = 5) -> list[dict]:
        """在个人文库中进行向量相似度搜索。

        Args:
            query_embedding: 查询文本的嵌入向量。
            top_k: 返回的最大结果数量，默认为 5。

        Returns:
            按相似度降序排列的搜索结果列表。
        """
        return self._library.search(query_embedding, top_k=top_k)


__all__ = ["PersonalLibrary", "PersonalLibraryService", "sanitize_filename"]
