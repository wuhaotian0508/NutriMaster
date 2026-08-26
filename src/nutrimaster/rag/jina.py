from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import os
import pickle
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import requests

from nutrimaster.config.settings import Settings
from nutrimaster.rag.bm25 import BM25Retriever, rrf_fuse
from nutrimaster.rag.field_keyword import FieldKeywordRetriever
from nutrimaster.rag.gene_index import GeneChunk, IndexService
from nutrimaster.rag.index_generation import (
    copy_generation_files,
    create_staging_generation,
    discard_staging_generation,
    file_sha256,
    generation_manifest_path,
    read_current_generation_id,
    resolve_active_generation,
    switch_current_generation,
    validate_generation_manifest,
)
from nutrimaster.rag.jina_proxy import jina_proxy_request_kwargs

logger = logging.getLogger(__name__)


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int_env(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} must be at most {maximum}")
    return value


def _bounded_float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _file_sha256(path: Path, *, block_size: int = 1024 * 1024) -> str:
    """Backward-compatible local alias for callers outside this module."""
    return file_sha256(path, block_size=block_size)


def _current_settings() -> Settings:
    """获取当前 RAG 配置设置。

    从环境变量加载 Settings 实例，并验证 RAG 配置已正确初始化。

    Returns:
        Settings: 包含 RAG 配置的设置对象。

    Raises:
        RuntimeError: 当 RAG 设置未能成功初始化时抛出。
    """
    settings = Settings.from_env()
    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    return settings


def _build_headers(api_key: str | None = None) -> dict:
    """构建 Jina API 请求所需的 HTTP 头部。

    使用提供的 API 密钥或从环境设置中读取的密钥，生成包含 Bearer 认证令牌
    和 Content-Type 的请求头字典。

    Args:
        api_key: 可选的 Jina API 密钥。如果未提供，则从当前环境设置中读取。

    Returns:
        dict: 包含 Authorization 和 Content-Type 的请求头字典。

    Raises:
        RuntimeError: 当未提供 API 密钥且环境中也未配置时抛出。
    """
    settings = _current_settings()
    key = api_key or settings.jina_api_key
    if not key:
        raise RuntimeError("JINA_API_KEY is required")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _post_with_retry(
    url: str,
    payload: dict,
    headers: dict,
    timeout: int = 60,
    max_retries: int = 20,
    post=requests.post,
) -> dict:
    """带指数退避重试机制的 HTTP POST 请求。

    向指定 URL 发送 JSON POST 请求，遇到可恢复错误（429 限流、5xx 服务端错误、
    连接超时等）时自动重试。429 错误使用线性退避（每次增加 5 秒，最多 30 秒），
    其他错误使用指数退避（2^attempt 秒，最多 30 秒）。

    Args:
        url: 请求目标 URL。
        payload: 将以 JSON 格式发送的请求体字典。
        headers: HTTP 请求头字典。
        timeout: 单次请求的超时时间（秒），默认 60。
        max_retries: 最大重试次数，默认 20。
        post: 用于发送请求的可调用对象，默认为 requests.post，可替换以便测试。

    Returns:
        dict: API 响应的 JSON 解析结果。

    Raises:
        RuntimeError: 当所有重试均失败时抛出，包含最后一次异常信息。
    """
    if timeout <= 0 or max_retries <= 0:
        raise ValueError("embedding API timeout and max_retries must be positive")
    last_exc = None
    for attempt in range(max_retries):
        try:
            if post is requests.post:
                with jina_proxy_request_kwargs() as request_kwargs:
                    response = post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                        **request_kwargs,
                    )
            else:
                response = post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 429:
                last_exc = requests.exceptions.HTTPError(
                    f"rate limited with status 429: {response.text[:200]}"
                )
                if attempt + 1 < max_retries:
                    time.sleep(min(30, 5 * (attempt + 1)))
                continue
            if response.status_code >= 500:
                raise requests.exceptions.HTTPError(
                    f"server error {response.status_code}: {response.text[:200]}"
                )
            response.raise_for_status()
            return response.json()
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.HTTPError,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:
            last_exc = exc
            if attempt + 1 < max_retries:
                time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"embedding API {max_retries} 次全部失败: {last_exc}")


def get_embeddings(
    texts: Sequence[str],
    batch_size: int = 32,
    headers: dict | None = None,
    show_progress: bool = False,
) -> np.ndarray:
    """批量获取文本的嵌入向量。

    将输入文本按指定批次大小分批发送到 Jina Embedding API，获取每段文本的
    向量表示。使用 ``retrieval.passage`` 任务类型，适用于文档段落的嵌入。

    Args:
        texts: 需要获取嵌入向量的文本序列。
        batch_size: 每批发送到 API 的文本数量，默认 32。
        headers: 可选的自定义 HTTP 请求头。未提供时自动构建。
        show_progress: 是否在标准输出打印批次处理进度，默认 False。

    Returns:
        np.ndarray: 形状为 (len(texts), embedding_dim) 的二维嵌入向量数组。
    """
    settings = _current_settings()
    headers = headers or _build_headers()
    embeddings = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start:start + batch_size])
        data = _post_with_retry(
            settings.rag.jina_embedding_url,
            {
                "model": settings.rag.embedding_model,
                "input": batch,
                "task": "retrieval.passage",
            },
            headers,
        )
        embeddings.extend(item["embedding"] for item in data["data"])
        if show_progress:
            print(f"  Embedded {min(start + batch_size, len(texts))}/{len(texts)}")
    return np.array(embeddings)


def get_query_embedding(query: str, headers: dict | None = None) -> np.ndarray:
    """获取单条查询文本的嵌入向量。

    调用 Jina Embedding API，使用 ``retrieval.query`` 任务类型获取查询文本的
    向量表示。与 get_embeddings 不同，此函数专为查询端优化。

    Args:
        query: 需要获取嵌入向量的查询文本。
        headers: 可选的自定义 HTTP 请求头。未提供时自动构建。

    Returns:
        np.ndarray: 查询文本的一维嵌入向量。
    """
    settings = _current_settings()
    headers = headers or _build_headers()
    data = _post_with_retry(
        settings.rag.jina_embedding_url,
        {
            "model": settings.rag.embedding_model,
            "input": [query],
            "task": "retrieval.query",
        },
        headers,
        timeout=_positive_int_env(
            "NUTRIMASTER_JINA_QUERY_TIMEOUT_SECONDS",
            15,
            maximum=60,
        ),
        max_retries=_positive_int_env(
            "NUTRIMASTER_JINA_QUERY_MAX_ATTEMPTS",
            2,
            maximum=5,
        ),
    )
    return np.array(data["data"][0]["embedding"])


def rerank_documents(
    query: str,
    documents: Sequence[str],
    top_n: int | None = None,
    headers: dict | None = None,
) -> list[dict]:
    """使用 Jina Rerank API 对文档列表进行相关性重排序。

    根据查询与每篇文档的语义相关性，调用 Jina Rerank 模型对候选文档重新排序，
    返回按相关性从高到低排列的结果。

    Args:
        query: 用于评估文档相关性的查询文本。
        documents: 待重排序的文档文本序列。
        top_n: 可选，返回排名前 N 的文档。未指定时返回所有结果。
        headers: 可选的自定义 HTTP 请求头。未提供时自动构建。

    Returns:
        list[dict]: 重排序结果列表，每个元素包含 index（原始索引）和
            relevance_score（相关性分数）等字段。
    """
    settings = _current_settings()
    headers = headers or _build_headers()
    payload = {
        "model": settings.rag.rerank_model,
        "query": query,
        "documents": list(documents),
    }
    if top_n is not None:
        payload["top_n"] = top_n
    return _post_with_retry(settings.rag.jina_rerank_url, payload, headers).get("results", [])


class JinaReranker:
    """Jina 重排序器：使用 Jina Rerank API 对候选文档进行语义相关性重排序。

    支持失败重试和自定义 HTTP 客户端注入。

    Attributes:
        rerank_url: Jina Rerank API 的 URL。
        model: 重排序模型名称。
    """

    def __init__(
        self,
        *,
        api_key: str,
        rerank_url: str,
        model: str,
        post_json: Callable[[str, dict, dict, int], dict] | None = None,
    ):
        """初始化 Jina 重排序器。

        创建一个可复用的重排序器实例，封装 Jina Rerank API 的调用逻辑，
        支持自定义 HTTP 请求函数以便于测试和扩展。

        Args:
            api_key: Jina API 认证密钥。
            rerank_url: Jina Rerank API 的完整 URL 地址。
            model: 使用的重排序模型名称（如 ``jina-reranker-v2-base-multilingual``）。
            post_json: 可选的自定义 POST 请求函数，签名为
                ``(url, payload, headers, timeout) -> dict``。
                未提供时使用默认的 requests.post 实现。
        """
        self.rerank_url = rerank_url
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._post_json = post_json or self._default_post_json

    @staticmethod
    def _default_post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
        """默认的 JSON POST 请求实现。

        使用 requests 库发送 POST 请求并返回 JSON 响应。作为 JinaReranker
        的默认 HTTP 客户端，在未注入自定义 post_json 时使用。

        Args:
            url: 请求目标 URL。
            payload: 将以 JSON 格式发送的请求体字典。
            headers: HTTP 请求头字典。
            timeout: 请求超时时间（秒）。

        Returns:
            dict: API 响应的 JSON 解析结果。

        Raises:
            requests.exceptions.HTTPError: 当 HTTP 响应状态码表示错误时抛出。
        """
        with jina_proxy_request_kwargs() as request_kwargs:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
                **request_kwargs,
            )
        response.raise_for_status()
        return response.json()

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_n: int,
        max_retries: int = 3,
    ) -> list[dict]:
        """执行文档重排序，返回按相关性排序的候选文档。

        将候选文档的 content 字段提取后发送到 Jina Rerank API，根据查询的
        语义相关性对文档重新排序。支持失败重试，当所有重试均失败时退化为
        按原始顺序截取前 top_n 个结果。

        Args:
            query: 用于评估相关性的查询文本。
            candidates: 候选文档列表，每个元素为包含 ``content`` 键的字典。
            top_n: 返回排名最高的前 N 个文档。
            max_retries: API 调用最大重试次数，默认 3。

        Returns:
            list[dict]: 重排序后的文档列表，每个字典在原始候选字段基础上
                新增 ``score`` 字段表示相关性分数。若 API 全部失败，
                则返回原始列表的前 top_n 个元素（无 score 字段）。
        """
        if not candidates:
            return []
        payload = {
            "model": self.model,
            "query": query,
            "documents": [candidate["content"] for candidate in candidates],
            "top_n": min(top_n, len(candidates)),
        }
        for attempt in range(1, max_retries + 1):
            try:
                data = self._post_json(self.rerank_url, payload, self._headers, 60)
                ranked = []
                for item in data["results"]:
                    entry = dict(candidates[item["index"]])
                    entry["score"] = item["relevance_score"]
                    ranked.append(entry)
                return ranked
            except MemoryError:
                raise
            except Exception as exc:
                logger.warning("Jina rerank attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(1 * attempt)
        return candidates[:top_n]


class JinaRetriever:
    """Jina 向量检索器：基于 Jina Embedding API 的基因信息检索引擎。

    负责索引的构建、持久化加载和基于余弦相似度的向量检索。
    支持按信息块类型和基因类型过滤结果。

    Attributes:
        settings: 应用配置实例。
        index_path: 索引文件存储目录。
        data_dir: 基因数据文件目录。
        chunks: 已加载的基因信息块列表。
        embeddings: 已加载的嵌入向量矩阵。
        load_error: 索引加载错误信息（若有）。
    """

    def __init__(
        self,
        index_path: Path | None = None,
        data_dir: Path | None = None,
        *,
        settings: Settings | None = None,
        autoload: bool = True,
    ):
        """初始化 Jina 向量检索器。

        创建检索器实例，加载或准备向量索引。初始化时会自动尝试从磁盘加载
        已有的索引文件（chunks.pkl 和 embeddings.npy）。

        Args:
            index_path: 可选的索引文件存储目录路径。未指定时从 RAG 设置中读取。
            data_dir: 可选的语料数据目录路径（包含 JSON 基因数据文件）。
                未指定时从 RAG 设置中读取。
            settings: 可选的 Settings 实例。未提供时从环境变量自动加载。
            autoload: 是否在初始化时加载当前索引。独立 builder 可关闭，
                避免构建前将完整服务语料加载进内存。

        Raises:
            RuntimeError: 当 RAG 设置未能成功初始化时抛出。
        """
        self.settings = settings or Settings.from_env()
        rag = self.settings.rag
        if rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        requested_index_root = Path(index_path or rag.index_dir)
        self.data_dir = Path(data_dir or rag.data_dir)
        requested_index_root.mkdir(parents=True, exist_ok=True)
        self._require_generation = _env_flag("NUTRIMASTER_REQUIRE_INDEX_GENERATION") or _env_flag(
            "NUTRIMASTER_REQUIRE_SPARSE_INDEXES"
        )
        resolved_generation = resolve_active_generation(
            requested_index_root,
            require_generation=self._require_generation,
            # Startup loads and checks sparse generation metadata below when
            # production calls require_sparse_indexes(). Avoid transiently
            # materializing BM25 twice before the service binds its port.
            validate_artifact_contracts=False,
        )
        self.index_root = resolved_generation.index_root
        self.index_path = resolved_generation.path
        self.generation_id = resolved_generation.generation_id
        self.legacy_index_layout = resolved_generation.legacy
        self.chunks: list[GeneChunk] = []
        self.embeddings: np.ndarray | None = None
        self.embedding_norms: np.ndarray | None = None
        self.corpus_fingerprint: str | None = None
        self.load_error: str | None = None
        self._bm25: BM25Retriever | None = None
        self._bm25_error: str | None = None
        self._field_keyword: FieldKeywordRetriever | None = None
        self._field_keyword_error: str | None = None
        self._generation_validated = False
        self._generation_error: str | None = None
        self._dense_query_error: str | None = None
        self._corpus_file_count: int | None = None
        self._manifest_file_count: int | None = None
        self._index_lock = threading.RLock()
        # Dense BLAS, BM25 and field rescoring all touch corpus-scale data.
        # Keep the expensive section bounded even after an asyncio timeout:
        # cancellation of ``to_thread`` does not stop its worker immediately,
        # so the semaphore must live in the worker rather than the coroutine.
        self._query_semaphore = threading.BoundedSemaphore(
            _positive_int_env("NUTRIMASTER_RAG_MAX_CONCURRENT_SEARCHES", 1, maximum=8)
        )
        self._dense_norm_block_rows = _positive_int_env(
            "NUTRIMASTER_DENSE_NORM_BLOCK_ROWS",
            4096,
            maximum=65_536,
        )
        self._field_keyword_rrf_weight = _bounded_float_env(
            "NUTRIMASTER_FIELD_KEYWORD_RRF_WEIGHT",
            1.15,
            minimum=0.0,
            maximum=10.0,
        )
        if autoload:
            self._load_index()

    def build_index(
        self,
        data_dir: Path = None,
        force: bool = False,
        incremental: bool = True,
        *,
        load_after_build: bool = True,
        reload_on_failure: bool = True,
    ):
        """构建或更新向量索引。

        扫描数据目录中的 JSON 文件，将基因数据分块并生成嵌入向量，持久化到磁盘。
        支持增量构建模式，仅处理新增或变更的文件。构建完成后自动重新加载索引。

        Args:
            data_dir: 可选的数据目录路径，覆盖实例初始化时的设置。
            force: 是否强制完全重建索引（忽略已有索引），默认 False。
            incremental: 是否使用增量构建模式（仅处理变更文件），默认 True；
                False 时从语料全量重建 dense 索引，而不是复用旧 dense 快照。
            load_after_build: 是否在发布后加载新索引。短命 builder 可关闭。
            reload_on_failure: 是否在普通失败后重载旧索引。独立 builder
                不服务查询，可关闭以避免故障时的内存峰值。
        """
        with self._query_semaphore, self._index_lock:
            if data_dir is not None:
                self.data_dir = Path(data_dir)

            previous_path = self.index_path
            previous_generation_id = self.generation_id
            previous_legacy_layout = self.legacy_index_layout
            dense_staging: Path | None = None
            published_generation_id: str | None = None
            try:
                dense_staging = create_staging_generation(self.index_root)
                full_rebuild = force or not incremental
                if not full_rebuild:
                    copy_generation_files(
                        previous_path,
                        dense_staging,
                        include_sparse=False,
                        # Graph is rebuilt from the same corpus snapshot below;
                        # carrying the old SQLite file only consumes staging
                        # disk and can never be published by this path.
                        include_optional=False,
                        include_incremental_manifest=True,
                    )

                # Release the serving object graph before either the dense or
                # sparse offline builder loads its own copy. Searches remain
                # blocked by _index_lock for this explicit administrative path.
                self.chunks = []
                self.embeddings = None
                self.embedding_norms = None
                self._bm25 = None
                self._field_keyword = None
                gc.collect()

                service = IndexService(
                    data_dir=self.data_dir,
                    index_dir=dense_staging,
                    embed_texts=self._embed_texts,
                )
                service.build(force=full_rebuild)

                from nutrimaster.rag.build_sparse_indexes import build_sparse_indexes

                result = build_sparse_indexes(
                    self.index_root,
                    source_dir=dense_staging,
                    build_graph=True,
                    graph_data_dir=self.data_dir,
                )
                published_generation_id = str(result["generation_id"])
                self.index_path = Path(result["generation_dir"])
                self.generation_id = published_generation_id
                self.legacy_index_layout = False
                self.corpus_fingerprint = str(result["corpus_fingerprint"])
                if load_after_build:
                    self._load_index()
                    if self.load_error or self.embeddings is None or not self.chunks:
                        raise RuntimeError(self.load_error or "vector index is empty after rebuild")
                    self.require_sparse_indexes()
            except MemoryError:
                # The isolated builder must terminate immediately under
                # allocator pressure. Reloading the serving generation here
                # would allocate the corpus-sized object graph a second time.
                self.chunks = []
                self.embeddings = None
                self.embedding_norms = None
                self._bm25 = None
                self._field_keyword = None
                raise
            except Exception as build_exc:
                self.index_path = previous_path
                self.generation_id = previous_generation_id
                self.legacy_index_layout = previous_legacy_layout
                # ``build_sparse_indexes`` atomically publishes CURRENT before
                # returning. If the optional in-process load/startup gate then
                # fails, keep the process and pointer on the same previously
                # validated generation. The isolated production worker has an
                # additional out-of-process rollback guard for OOM/SIGKILL.
                if (
                    published_generation_id is not None
                    and previous_generation_id is not None
                ):
                    try:
                        current_generation_id = read_current_generation_id(self.index_root)
                        if current_generation_id == published_generation_id:
                            switch_current_generation(
                                self.index_root,
                                previous_generation_id,
                            )
                    except MemoryError:
                        raise
                    except Exception as rollback_exc:
                        raise RuntimeError(
                            "index build failed and CURRENT could not be rolled back: "
                            f"{type(rollback_exc).__name__}: {rollback_exc}"
                        ) from build_exc
                if reload_on_failure:
                    self._load_index()
                raise
            finally:
                pending_exception = sys.exception()
                try:
                    if dense_staging is not None and dense_staging.exists():
                        discard_staging_generation(self.index_root, dense_staging)
                except Exception:
                    # Cleanup remains strict for success and ordinary
                    # failures. Under OOM, abandoned staging is recovered
                    # out-of-process and must not replace the allocator
                    # exception that prevents a corpus reload.
                    if not isinstance(pending_exception, MemoryError):
                        raise

    def _cache_file_counts(self) -> None:
        """Cache diagnostic counts once instead of parsing them on every health check."""

        data_dir = getattr(self, "data_dir", None)
        self._corpus_file_count = None
        if data_dir is not None:
            data_dir = Path(data_dir)
            self._corpus_file_count = (
                sum(1 for _path in data_dir.glob("*.json"))
                if data_dir.exists()
                else 0
            )
        self._manifest_file_count = None
        manifest_file = self.index_path / "manifest.json"
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
                self._manifest_file_count = len(files) if isinstance(files, dict) else None
            except MemoryError:
                raise
            except Exception:
                self._manifest_file_count = None

    def _load_index(self):
        """从磁盘加载已持久化的向量索引。

        读取索引目录中的 chunks.pkl（分块数据）和 embeddings.npy（嵌入向量）
        文件，加载到内存中。同时验证分块数量与嵌入矩阵行数是否一致。
        如果加载失败或数据不一致，会将 chunks 和 embeddings 重置为空，
        并将错误信息记录到 self.load_error。
        """
        chunks_file = self.index_path / "chunks.pkl"
        embeddings_file = self.index_path / "embeddings.npy"
        embedding_norms_file = self.index_path / "embedding_norms.npy"
        self._cache_file_counts()
        self.load_error = None
        self._bm25 = None
        self._bm25_error = None
        self._field_keyword = None
        self._field_keyword_error = None
        self._generation_validated = False
        self._generation_error = None
        self._dense_query_error = None
        self.embedding_norms = None
        self.corpus_fingerprint = None
        if chunks_file.exists() and embeddings_file.exists():
            try:
                with chunks_file.open("rb") as file:
                    chunks = pickle.load(file)
                mmap_enabled = os.getenv("NUTRIMASTER_RAG_MMAP_EMBEDDINGS", "1").lower() not in {"0", "false", "no", "off"}
                embeddings = np.load(embeddings_file, mmap_mode="r" if mmap_enabled else None)
            except MemoryError:
                self.chunks = []
                self.embeddings = None
                raise
            except Exception as exc:
                self.chunks = []
                self.embeddings = None
                self.load_error = f"{type(exc).__name__}: {exc}"
                return
            if len(chunks) != embeddings.shape[0]:
                self.chunks = []
                self.embeddings = None
                self.load_error = (
                    f"Index shape mismatch: chunks={len(chunks)} embeddings={embeddings.shape[0]}"
                )
                return
            self.chunks = chunks
            self.embeddings = embeddings
            if self.generation_id is not None:
                try:
                    generation = validate_generation_manifest(
                        self.index_path,
                        expected_chunks=len(chunks),
                        expected_embedding_shape=embeddings.shape,
                        verify_checksums=False,
                    )
                    self.corpus_fingerprint = str(generation["corpus_fingerprint"])
                except MemoryError:
                    self.chunks = []
                    self.embeddings = None
                    raise
                except Exception as exc:
                    self.chunks = []
                    self.embeddings = None
                    self.load_error = f"{type(exc).__name__}: {exc}"
                    return
            else:
                self.corpus_fingerprint = _file_sha256(chunks_file)
            if (
                embedding_norms_file.exists()
                and (
                    self.generation_id is not None
                    or embedding_norms_file.stat().st_mtime_ns >= embeddings_file.stat().st_mtime_ns
                )
            ):
                try:
                    norms = np.load(embedding_norms_file, mmap_mode="r")
                    if norms.shape == (len(chunks),):
                        self.embedding_norms = norms
                except MemoryError:
                    raise
                except Exception:
                    self.embedding_norms = None
        else:
            self.chunks = []
            self.embeddings = None

    def index_status(self) -> dict:
        """获取当前索引的状态信息。

        返回一个包含数据目录、索引目录、文件数量、加载状态等信息的字典，
        用于诊断和监控索引健康状态。

        Returns:
            包含索引状态各项指标的字典。
        """
        chunks_file = self.index_path / "chunks.pkl"
        embeddings_file = self.index_path / "embeddings.npy"
        embedding_norms_file = self.index_path / "embedding_norms.npy"
        manifest_file = self.index_path / "manifest.json"
        bm25_file = BM25Retriever(self.index_path).path
        field_keyword_file = FieldKeywordRetriever(self.index_path).path
        generation_file = generation_manifest_path(self.index_path)
        return {
            "data_dir": str(self.data_dir),
            "index_root": str(self.index_root),
            "index_dir": str(self.index_path),
            "generation_id": self.generation_id,
            "legacy_index_layout": self.legacy_index_layout,
            "corpus_files": self._corpus_file_count,
            "manifest_files": self._manifest_file_count,
            "chunks_loaded": len(self.chunks),
            "embeddings_loaded": 0 if self.embeddings is None else int(self.embeddings.shape[0]),
            "embedding_shape": None if self.embeddings is None else list(self.embeddings.shape),
            "chunks_file_exists": chunks_file.exists(),
            "embeddings_file_exists": embeddings_file.exists(),
            "embedding_norms_file_exists": embedding_norms_file.exists(),
            "embedding_norms_loaded": 0 if self.embedding_norms is None else int(self.embedding_norms.shape[0]),
            "corpus_fingerprint": self.corpus_fingerprint,
            "manifest_file_exists": manifest_file.exists(),
            "bm25_file_exists": bm25_file.exists(),
            "bm25_loaded": self._bm25 is not None,
            "bm25_chunks": None if self._bm25 is None else self._bm25.n_chunks,
            "bm25_error": self._bm25_error,
            "field_keyword_file_exists": field_keyword_file.exists(),
            "field_keyword_loaded": self._field_keyword is not None,
            "field_keyword_chunks": None if self._field_keyword is None else self._field_keyword.n_chunks,
            "field_keyword_error": self._field_keyword_error,
            "generation_manifest_exists": generation_file.exists(),
            "generation_validated": self._generation_validated,
            "generation_error": self._generation_error,
            "dense_query_error": self._dense_query_error,
            "load_error": self.load_error,
        }

    def search(
        self,
        query: str,
        top_k: int | None = None,
        chunk_type_filter: list[str] | None = None,
        gene_type_filter: list[str] | None = None,
    ) -> list[tuple[GeneChunk, float]]:
        """基于向量余弦相似度搜索基因信息块。

        将查询文本转为嵌入向量，计算与所有索引块的余弦相似度，
        按相似度从高到低返回结果。支持按块类型和基因类型过滤。

        Args:
            query: 搜索查询文本。
            top_k: 返回的最大结果数（可选，默认从配置读取）。
            chunk_type_filter: 可选的块类型过滤列表。
            gene_type_filter: 可选的基因类型过滤列表。

        Returns:
            (GeneChunk, 相似度分数) 元组列表，按分数降序排列。
        """
        with self._query_semaphore, self._index_lock:
            if self.embeddings is None or not self.chunks:
                self._load_index()
            if self.embeddings is None or not self.chunks:
                return []
            top_k = top_k or (self.settings.rag.top_k_retrieval if self.settings.rag else 20)
            return [
                (self.chunks[index], score)
                for index, score in self._dense_search_indices(
                    query,
                    top_k=top_k,
                    chunk_type_filter=chunk_type_filter,
                    gene_type_filter=gene_type_filter,
                )
            ]

    def _ensure_embedding_norms(self) -> np.ndarray:
        """Load or build row norms without allocating an embeddings-sized temporary."""
        with self._index_lock:
            if self.embeddings is None:
                return np.zeros(0, dtype=np.float32)
            if self.embedding_norms is not None and self.embedding_norms.shape == (self.embeddings.shape[0],):
                return self.embedding_norms

            norms_path = self.index_path / "embedding_norms.npy"
            if self.generation_id is not None:
                raise RuntimeError(
                    "embedding norms are missing from the pinned immutable retrieval generation; "
                    "build and publish a new generation offline"
                )
            tmp_path = norms_path.with_name(f".{norms_path.name}.{os.getpid()}.{id(self)}.tmp")
            tmp_path.unlink(missing_ok=True)
            try:
                norms = np.lib.format.open_memmap(
                    tmp_path,
                    mode="w+",
                    dtype=np.float32,
                    shape=(self.embeddings.shape[0],),
                )
                block_rows = self._dense_norm_block_rows
                for start in range(0, self.embeddings.shape[0], block_rows):
                    end = min(start + block_rows, self.embeddings.shape[0])
                    block = np.asarray(self.embeddings[start:end], dtype=np.float32)
                    norms[start:end] = np.sqrt(np.einsum("ij,ij->i", block, block, optimize=True))
                norms.flush()
                del norms
                tmp_path.replace(norms_path)
            except MemoryError:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            self.embedding_norms = np.load(norms_path, mmap_mode="r")
            return self.embedding_norms

    def _dense_search_indices(
        self,
        query: str,
        top_k: int,
        chunk_type_filter: list[str] | None = None,
        gene_type_filter: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        """Return dense search results as chunk indices to support hybrid fusion."""
        with self._index_lock:
            query_embedding = np.asarray(self.get_query_embedding(query), dtype=self.embeddings.dtype)
            if (
                query_embedding.ndim != 1
                or self.embeddings.ndim != 2
                or query_embedding.shape[0] != self.embeddings.shape[1]
                or not np.all(np.isfinite(query_embedding))
            ):
                raise RuntimeError(
                    "query embedding is non-finite or incompatible with the loaded embedding matrix"
                )
            query_norm = float(np.linalg.norm(query_embedding))
            similarities = np.asarray(self.embeddings @ query_embedding).ravel()
            norms = self._ensure_embedding_norms()
            denom = np.asarray(norms) * query_norm
            scores = similarities / np.where(denom == 0, 1, denom)

            if not chunk_type_filter and not gene_type_filter:
                result_count = min(max(0, int(top_k)), len(scores))
                if result_count == 0:
                    return []
                # Preserve the legacy deterministic tie rule (highest index
                # first after reversing np.argsort). A partial partition can
                # select a different document at an equal-score top-k edge.
                order = np.argsort(scores)[::-1][:result_count]
                return [(int(index), float(scores[index])) for index in order]

            order = np.argsort(scores)[::-1]
            results = []
            for index in order:
                chunk = self.chunks[int(index)]
                if chunk_type_filter and chunk.chunk_type not in chunk_type_filter:
                    continue
                if gene_type_filter and chunk.gene_type not in gene_type_filter:
                    continue
                results.append((int(index), float(scores[index])))
                if len(results) >= top_k:
                    break
            return results

    def _rebuild_bm25(self) -> BM25Retriever | None:
        """Rebuild and persist BM25 from the currently loaded chunks."""
        with self._index_lock:
            if self.generation_id is not None:
                self._bm25 = None
                self._bm25_error = (
                    "the pinned retrieval generation is immutable; build BM25 in a new offline generation"
                )
                return None
            if not self.chunks:
                self._bm25 = None
                return None
            try:
                bm25 = BM25Retriever(self.index_path)
                bm25.build(self.chunks, corpus_fingerprint=self.corpus_fingerprint)
                bm25.save()
            except MemoryError:
                raise
            except Exception as exc:
                self._bm25 = None
                self._bm25_error = f"{type(exc).__name__}: {exc}"
                logger.warning("BM25 rebuild failed: %s", exc)
                return None
            self._bm25 = bm25
            self._bm25_error = None
            return bm25

    def _ensure_bm25(self, *, allow_rebuild: bool | None = None) -> BM25Retriever | None:
        """Load a valid compact BM25 index; never rebuild online by default."""
        with self._index_lock:
            if self._bm25 is not None and self._bm25.n_chunks == len(self.chunks):
                return self._bm25
            try:
                bm25 = BM25Retriever(self.index_path)
                if bm25.load(
                    expected_chunks=len(self.chunks),
                    expected_fingerprint=self.corpus_fingerprint,
                ):
                    self._bm25 = bm25
                    self._bm25_error = None
                    return bm25
                self._bm25_error = "compact BM25 index is missing, stale, or incompatible"
            except MemoryError:
                raise
            except Exception as exc:
                self._bm25_error = f"{type(exc).__name__}: {exc}"
                logger.warning("BM25 load failed: %s", exc)
            if allow_rebuild is None:
                allow_rebuild = _env_flag("NUTRIMASTER_SPARSE_INDEX_BUILD_ON_MISS")
            return self._rebuild_bm25() if allow_rebuild else None

    def _rebuild_field_keyword(self) -> FieldKeywordRetriever | None:
        """Rebuild and persist the field-keyword projection from loaded chunks."""
        with self._index_lock:
            if self.generation_id is not None:
                self._field_keyword = None
                self._field_keyword_error = (
                    "the pinned retrieval generation is immutable; build field-keyword in a new offline generation"
                )
                return None
            if not self.chunks:
                self._field_keyword = None
                return None
            try:
                retriever = FieldKeywordRetriever(self.index_path, chunks=self.chunks)
                retriever.build(self.chunks, corpus_fingerprint=self.corpus_fingerprint)
                retriever.save()
            except MemoryError:
                raise
            except Exception as exc:
                self._field_keyword = None
                self._field_keyword_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Field keyword rebuild failed: %s", exc)
                return None
            self._field_keyword = retriever
            self._field_keyword_error = None
            return retriever

    def _ensure_field_keyword(self, *, allow_rebuild: bool | None = None) -> FieldKeywordRetriever | None:
        """Load the disk-backed field index; never rebuild online by default."""
        with self._index_lock:
            if self._field_keyword is not None and self._field_keyword.n_chunks == len(self.chunks):
                return self._field_keyword
            try:
                retriever = FieldKeywordRetriever(self.index_path, chunks=self.chunks)
                if retriever.load(
                    expected_chunks=len(self.chunks),
                    expected_fingerprint=self.corpus_fingerprint,
                ):
                    self._field_keyword = retriever
                    self._field_keyword_error = None
                    return retriever
                self._field_keyword_error = "field-keyword index is missing, stale, or incompatible"
            except MemoryError:
                raise
            except Exception as exc:
                self._field_keyword_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Field keyword load failed: %s", exc)
            if allow_rebuild is None:
                allow_rebuild = _env_flag("NUTRIMASTER_SPARSE_INDEX_BUILD_ON_MISS")
            return self._rebuild_field_keyword() if allow_rebuild else None

    def require_sparse_indexes(self) -> None:
        """Fail startup unless both full-feature sparse indexes match the corpus."""
        missing = []
        if self._require_generation and self.generation_id is None:
            missing.append("immutable retrieval generation (CURRENT is required in production)")
        if self._ensure_bm25(allow_rebuild=False) is None:
            missing.append(f"BM25 ({self._bm25_error or 'unavailable'})")
        if self._ensure_field_keyword(allow_rebuild=False) is None:
            missing.append(f"field keyword ({self._field_keyword_error or 'unavailable'})")
        if self.embeddings is None or not self.chunks or self.corpus_fingerprint is None:
            missing.append("dense corpus (unavailable)")
        else:
            try:
                validate_generation_manifest(
                    self.index_path,
                    expected_chunks=len(self.chunks),
                    expected_embedding_shape=self.embeddings.shape,
                    expected_corpus_fingerprint=self.corpus_fingerprint,
                )
                norms = np.load(self.index_path / "embedding_norms.npy", mmap_mode="r")
                if norms.shape != (len(self.chunks),):
                    raise RuntimeError("embedding norm shape does not match the loaded corpus")
                self.embedding_norms = norms
                self._generation_validated = True
                self._generation_error = None
            except MemoryError:
                raise
            except Exception as exc:
                self._generation_validated = False
                self._generation_error = f"{type(exc).__name__}: {exc}"
                missing.append(f"retrieval generation ({self._generation_error})")
        if missing:
            raise RuntimeError("Required sparse indexes are unavailable: " + "; ".join(missing))

    async def hybrid_search(
        self,
        query: str,
        top_k: int = 20,
        rerank: bool = True,
        rerank_top_n: int = 50,
        keyword_spec: dict | list | str | None = None,
    ) -> list[tuple[GeneChunk, float]]:
        """异步混合搜索：向量、BM25 和可选字段关键词召回通过 RRF 融合。

        字段关键词层默认不启用，只有传入 agent 生成的 keyword_spec，或设置
        NUTRIMASTER_ENABLE_FIELD_KEYWORD=1 时才参与融合。

        Args:
            query: 搜索查询文本。
            top_k: 返回的最大结果数，默认 20。
            rerank: 保留的兼容参数，当前未调用外部 rerank。
            rerank_top_n: 双路召回的候选池大小。
            keyword_spec: Agent 生成的字段关键词 JSON；为空时默认不启用字段关键词层。

        Returns:
            (GeneChunk, 相似度分数) 元组列表。
        """
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(
                self._hybrid_search_sync,
                query,
                top_k,
                rerank,
                rerank_top_n,
                keyword_spec,
                cancelled,
            )
        except asyncio.CancelledError:
            # asyncio cannot forcibly stop a running worker thread. Mark the
            # queued/running search so it exits before another expensive stage
            # instead of consuming capacity after its HTTP client has gone.
            cancelled.set()
            raise

    def _hybrid_search_sync(
        self,
        query: str,
        top_k: int = 20,
        rerank: bool = True,
        rerank_top_n: int = 50,
        keyword_spec: dict | list | str | None = None,
        cancelled: threading.Event | None = None,
    ) -> list[tuple[GeneChunk, float]]:
        """Run one immutable-generation hybrid query in a bounded worker."""

        del rerank  # Kept in the public contract for compatibility.
        with self._query_semaphore, self._index_lock:
            if cancelled is not None and cancelled.is_set():
                return []
            if self.embeddings is None or not self.chunks:
                self._load_index()
            if self.embeddings is None or not self.chunks:
                return []

            candidate_k = max(top_k, rerank_top_n)
            try:
                dense_ranked = self._dense_search_indices(query, top_k=candidate_k)
                self._dense_query_error = None
            except MemoryError:
                # Memory exhaustion is not an external Jina outage. Continuing
                # into sparse retrieval would allocate still more memory.
                raise
            except Exception as exc:
                # Query embeddings are an external dependency. Keep the local
                # BM25/field indexes available during a bounded Jina outage;
                # startup still fails closed on damaged dense artifacts.
                dense_ranked = []
                self._dense_query_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Dense query failed; continuing with local sparse retrieval: %s",
                    self._dense_query_error,
                )
            if cancelled is not None and cancelled.is_set():
                return []
            ranked_lists: list[list[tuple[int, float]]] = [dense_ranked] if dense_ranked else []
            weights: list[float] = [1.0] if dense_ranked else []

            if _env_flag("NUTRIMASTER_DISABLE_BM25"):
                bm25 = None
            else:
                bm25 = self._ensure_bm25()
            if bm25 is not None:
                bm25_ranked = bm25.search(query, top_k=candidate_k)
                if bm25_ranked:
                    ranked_lists.append(bm25_ranked)
                    weights.append(1.0)

            if cancelled is not None and cancelled.is_set():
                return []

            enable_field_keyword = keyword_spec is not None or _env_flag(
                "NUTRIMASTER_ENABLE_FIELD_KEYWORD"
            )
            if enable_field_keyword:
                field_keyword = self._ensure_field_keyword()
                if field_keyword is not None:
                    field_ranked = field_keyword.search(keyword_spec or query, top_k=candidate_k)
                    if field_ranked:
                        ranked_lists.append(field_ranked)
                        weights.append(self._field_keyword_rrf_weight)

            fused = rrf_fuse(*ranked_lists, weights=weights)
            if not fused:
                return [(self.chunks[index], score) for index, score in dense_ranked[:top_k]]
            return [(self.chunks[index], score) for index, score in fused[:top_k]]

    def get_query_embedding(self, query: str) -> np.ndarray:
        """获取单条查询文本的嵌入向量。

        调用 Jina Embedding API，使用 ``retrieval.query`` 任务类型将查询文本
        转换为向量表示，用于后续的余弦相似度计算。

        Args:
            query: 需要转换为嵌入向量的查询文本。

        Returns:
            np.ndarray: 查询文本的一维嵌入向量。
        """
        headers = self._headers()
        payload = {
            "model": self.settings.rag.embedding_model if self.settings.rag else "jina-embeddings-v3",
            "input": [query],
            "task": "retrieval.query",
        }
        data = _post_with_retry(
            self.settings.rag.jina_embedding_url,
            payload,
            headers,
            timeout=_positive_int_env(
                "NUTRIMASTER_JINA_QUERY_TIMEOUT_SECONDS",
                15,
                maximum=60,
            ),
            max_retries=_positive_int_env(
                "NUTRIMASTER_JINA_QUERY_MAX_ATTEMPTS",
                2,
                maximum=5,
            ),
        )
        return np.array(data["data"][0]["embedding"])

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量将文本转换为嵌入向量。

        调用 Jina Embedding API，使用 ``retrieval.passage`` 任务类型对文本列表
        进行批量嵌入。此方法供 IndexService 在构建索引时调用。

        Args:
            texts: 需要转换为嵌入向量的文本列表。如果为空列表，
                返回形状为 (0, 0) 的零矩阵。

        Returns:
            np.ndarray: 形状为 (len(texts), embedding_dim) 的二维嵌入向量数组。
        """
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        # 使用批处理避免单次请求过大，并输出进度防止 SSH 超时
        batch_size = 32
        headers = self._headers()
        all_embeddings = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            payload = {
                "model": self.settings.rag.embedding_model if self.settings.rag else "jina-embeddings-v3",
                "input": batch,
                "task": "retrieval.passage",
            }
            data = _post_with_retry(self.settings.rag.jina_embedding_url, payload, headers)
            all_embeddings.extend(item["embedding"] for item in data["data"])

            # 输出进度，防止 SSH 超时断开
            progress = min(start + batch_size, len(texts))
            logger.info(f"  Embedded {progress}/{len(texts)} chunks")
            print(f"  Embedded {progress}/{len(texts)} chunks", flush=True)

        return np.array(all_embeddings)

    def _headers(self) -> dict:
        """构建包含 Jina API 认证信息的 HTTP 请求头。

        从实例的 settings 中读取 Jina API 密钥，构建包含 Bearer 认证令牌
        和 Content-Type 的请求头字典。

        Returns:
            dict: 包含 Authorization 和 Content-Type 的请求头字典。

        Raises:
            RuntimeError: 当 settings 中未配置 JINA_API_KEY 时抛出。
        """
        if not self.settings.jina_api_key:
            raise RuntimeError("JINA_API_KEY is required")
        return {
            "Authorization": f"Bearer {self.settings.jina_api_key}",
            "Content-Type": "application/json",
        }
