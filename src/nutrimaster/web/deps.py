from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cachetools import TTLCache
from fastapi import Request

from nutrimaster.agent.agent import Agent
from nutrimaster.agent.interaction_recording import InteractionRecorder
from nutrimaster.agent.skills import SkillLoader
from nutrimaster.agent.tools import ExperimentDesignTool, RagSearchTool, ToolRegistry
from nutrimaster.config.llm import call_llm
from nutrimaster.config.settings import Settings
from nutrimaster.experiment import (
    ExperimentDesignService,
    ExperimentExecutionGate,
    GeneTransferDesignService,
)
from nutrimaster.rag.graph import (
    GraphDbSource,
    LocalGraphIndex,
    Neo4jGraphConfig,
    Neo4jGraphSource,
    Neo4jGraphStore,
)
from nutrimaster.rag.index_build_jobs import IndexBuildQueue
from nutrimaster.rag.jina import JinaRetriever
from nutrimaster.rag.personal_library import PersonalLibrary
from nutrimaster.rag.service import (
    GeneDbSource,
    PersonalLibrarySource,
    PubMedSource,
    RAGSearchService,
)

logger = logging.getLogger(__name__)

_PERSONAL_LIBRARY_CACHE_SIZE_ENV = "NUTRIMASTER_PERSONAL_LIBRARY_CACHE_SIZE"
_PERSONAL_LIBRARY_CACHE_TTL_ENV = "NUTRIMASTER_PERSONAL_LIBRARY_CACHE_TTL_SECONDS"
_DEFAULT_PERSONAL_LIBRARY_CACHE_SIZE = 16
_MAX_PERSONAL_LIBRARY_CACHE_SIZE = 64
_DEFAULT_PERSONAL_LIBRARY_CACHE_TTL_SECONDS = 900
_MAX_PERSONAL_LIBRARY_CACHE_TTL_SECONDS = 86_400


def _bounded_positive_int_env(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw in {None, ""} else int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be an integer between 1 and {maximum}") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _personal_library_cache_from_env() -> TTLCache:
    """Create the bounded per-user library cache from fail-fast settings."""
    return TTLCache(
        maxsize=_bounded_positive_int_env(
            _PERSONAL_LIBRARY_CACHE_SIZE_ENV,
            _DEFAULT_PERSONAL_LIBRARY_CACHE_SIZE,
            maximum=_MAX_PERSONAL_LIBRARY_CACHE_SIZE,
        ),
        ttl=_bounded_positive_int_env(
            _PERSONAL_LIBRARY_CACHE_TTL_ENV,
            _DEFAULT_PERSONAL_LIBRARY_CACHE_TTL_SECONDS,
            maximum=_MAX_PERSONAL_LIBRARY_CACHE_TTL_SECONDS,
        ),
    )


@dataclass
class ReindexState:
    """索引重建任务的状态追踪数据类。

    用于在后台线程中追踪索引重建操作的运行状态、进度信息和错误信息。
    所有对该对象字段的修改都应在 lock 保护下进行，以确保线程安全。
    """
    lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False
    progress: str = ""
    error: str | None = None
    last_completed: str | None = None


@dataclass
class WebServices:
    """Web 服务层的核心依赖容器。

    集中管理所有 Web 应用所需的服务实例，包括检索器、Agent、工具注册表、
    技能加载器、交互记录器、实验设计服务等。通过 FastAPI 的依赖注入机制
    提供给各路由处理函数使用。
    """
    settings: Settings
    retriever: JinaRetriever
    registry: Any
    skill_loader: Any
    agent: Agent
    interaction_recorder: InteractionRecorder
    experiment_service: ExperimentDesignService
    gene_transfer_service: GeneTransferDesignService
    personal_libs: TTLCache = field(default_factory=_personal_library_cache_from_env)
    personal_libs_lock: threading.Lock = field(default_factory=threading.Lock)
    reindex_state: ReindexState = field(default_factory=ReindexState)

    def get_personal_lib(self, user_id: str) -> PersonalLibrary:
        """获取指定用户的个人文献库实例（线程安全、带 TTL 缓存）。

        使用带 TTL 缓存的字典存储用户文献库实例，同一用户在缓存有效期内
        返回相同的实例。通过 personal_libs_lock 保证线程安全。

        参数:
            user_id: 用户唯一标识符。

        返回:
            PersonalLibrary: 该用户的个人文献库实例。
        """
        with self.personal_libs_lock:
            if user_id not in self.personal_libs:
                self.personal_libs[user_id] = PersonalLibrary(user_id)
            return self.personal_libs[user_id]

    def request_index_build(
        self,
        data_dir: Path | None = None,
        *,
        force: bool = False,
        reason: str = "admin",
    ) -> dict[str, Any]:
        """Durably queue index work for the isolated systemd builder.

        The request-serving process is intentionally incapable of calling
        ``JinaRetriever.build_index``. A successful return means only that a
        durable job was written and the fixed builder unit was dispatched.
        """

        if self.settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        configured_data_dir = self.settings.rag.data_dir.resolve()
        if data_dir is not None and Path(data_dir).resolve() != configured_data_dir:
            raise RuntimeError("index builder only accepts the configured corpus directory")
        queue = IndexBuildQueue.from_settings(self.settings)
        return queue.enqueue(
            force=force,
            reason=reason,
            active_generation_id=self.retriever.generation_id,
        )

    def refresh_index(self, data_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
        """Compatibility wrapper which queues, but never performs, a rebuild."""

        return self.request_index_build(
            data_dir=data_dir,
            force=force,
            reason="legacy-admin-refresh",
        )

    def index_build_status(self) -> dict[str, Any]:
        if self.settings.rag is None:
            raise RuntimeError("RAG settings failed to initialize")
        return IndexBuildQueue.from_settings(self.settings).status(
            active_generation_id=self.retriever.generation_id,
        )


def create_services(settings: Settings | None = None) -> WebServices:
    """创建并初始化 Web 服务层的所有核心服务实例。

    构建检索器、工具注册表、RAG 搜索服务、实验设计服务、Agent 等组件，
    并将它们组装为 WebServices 容器。根据环境变量 NUTRIMASTER_WEB_BUILD_INDEX
    决定是否在启动时构建检索索引。

    参数:
        settings: 应用配置对象。若为 None，则从环境变量自动加载。

    返回:
        WebServices: 包含所有已初始化服务的依赖容器。

    异常:
        RuntimeError: 当 RAG 配置初始化失败时抛出。
    """
    # Parse cache bounds before loading any corpus-sized service so invalid
    # production configuration fails immediately and cheaply.
    personal_libs = _personal_library_cache_from_env()
    settings = settings or Settings.from_env()
    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    build_index = os.getenv("NUTRIMASTER_WEB_BUILD_INDEX", "").lower() in {"1", "true", "yes", "on"}
    if build_index:
        raise RuntimeError(
            "NUTRIMASTER_WEB_BUILD_INDEX is forbidden: submit work to the isolated "
            "nutrimaster-index-builder service"
        )
    retriever = JinaRetriever(settings=settings)

    holder: dict[str, WebServices] = {}

    def get_personal_lib(user_id: str) -> PersonalLibrary:
        """延迟绑定的个人文献库获取函数。

        在 WebServices 实例创建完成后，通过 holder 字典间接引用
        services 实例的 get_personal_lib 方法，用于解决循环依赖。

        参数:
            user_id: 用户唯一标识符。

        返回:
            PersonalLibrary: 该用户的个人文献库实例。
        """
        return holder["services"].get_personal_lib(user_id)

    if os.getenv("NUTRIMASTER_REQUIRE_SPARSE_INDEXES", "").lower() in {"1", "true", "yes", "on"}:
        # Production should fail before binding a port rather than silently
        # dropping BM25/field recall or rebuilding a corpus-sized index inside
        # the request-serving process.
        retriever.require_sparse_indexes()

    registry = ToolRegistry()
    experiment_gate = ExperimentExecutionGate()
    experiment_service = ExperimentDesignService(execution_gate=experiment_gate)
    gene_transfer_service = GeneTransferDesignService(execution_gate=experiment_gate)
    graph_source = _create_graph_source(
        settings,
        index_dir=Path(getattr(retriever, "index_path", settings.rag.index_dir)),
        expected_corpus_fingerprint=getattr(retriever, "corpus_fingerprint", None),
    )
    rag_service = RAGSearchService(
        pubmed_source=PubMedSource(),
        gene_db_source=GeneDbSource(retriever),
        graph_source=graph_source,
        personal_source=PersonalLibrarySource(
            get_personal_lib=get_personal_lib,
            get_query_embedding=retriever.get_query_embedding,
        ),
    )
    for tool in (RagSearchTool(rag_service), ExperimentDesignTool(experiment_service, gene_transfer_service)):
        registry.register(tool)
    skill_loader = SkillLoader()

    services = WebServices(
        settings=settings,
        retriever=retriever,
        registry=registry,
        skill_loader=skill_loader,
        agent=Agent(registry=registry, skill_loader=skill_loader, call_llm=call_llm),
        interaction_recorder=InteractionRecorder.from_settings(settings),
        experiment_service=experiment_service,
        gene_transfer_service=gene_transfer_service,
        personal_libs=personal_libs,
    )
    holder["services"] = services
    return services


def _validate_local_graph_index(
    db_path: Path,
    *,
    expected_corpus_fingerprint: str | None = None,
) -> None:
    """Fail closed on a corrupt, empty, or schema-incompatible SQLite graph."""
    if db_path.is_symlink() or not db_path.is_file():
        raise RuntimeError(f"required graph index is missing or invalid: {db_path}")
    uri = f"{db_path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name IN ('nodes', 'edges')"
                )
            }
            populated = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM nodes), EXISTS(SELECT 1 FROM edges)"
            ).fetchone()
            metadata = (
                dict(connection.execute("SELECT key, value FROM metadata"))
                if expected_corpus_fingerprint is not None
                else {}
            )
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"required graph index is unreadable: {exc}") from exc
    if quick_check != ("ok",):
        raise RuntimeError(f"required graph index failed SQLite quick_check: {quick_check}")
    if tables != {"nodes", "edges"} or populated != (1, 1):
        raise RuntimeError("required graph index is empty or schema-incompatible")
    if (
        expected_corpus_fingerprint is not None
        and metadata.get("corpus_fingerprint") != expected_corpus_fingerprint
    ):
        raise RuntimeError("required graph index does not match the active retrieval corpus")
    if expected_corpus_fingerprint is not None:
        from nutrimaster.rag.graph.index import GRAPH_INDEX_VERSION

        if metadata.get("version") != GRAPH_INDEX_VERSION:
            raise RuntimeError("required graph index version is incompatible")


def _create_graph_source(
    settings: Settings,
    *,
    index_dir: Path | None = None,
    expected_corpus_fingerprint: str | None = None,
) -> Any | None:
    """根据环境变量创建图 RAG 来源。

    NUTRIMASTER_GRAPH_BACKEND:
      - sqlite: 默认，使用 data/index/graph_index.sqlite；
      - neo4j: 连接 Neo4j 并执行 Cypher 路径搜索；
      - off: 关闭图 RAG。

    Returns:
        Graph source 实例；不可用时返回 None，不影响 PubMed/GeneDB。
    """
    if settings.rag is None:
        return None

    backend = os.getenv("NUTRIMASTER_GRAPH_BACKEND", "sqlite").strip().lower()
    build_graph = os.getenv("NUTRIMASTER_WEB_BUILD_GRAPH", "").lower() in {"1", "true", "yes", "on"}
    require_graph = os.getenv("NUTRIMASTER_REQUIRE_GRAPH_INDEX", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if backend not in {"off", "sqlite", "neo4j"}:
        raise RuntimeError(f"Unsupported NUTRIMASTER_GRAPH_BACKEND: {backend}")
    if backend == "off":
        if require_graph:
            raise RuntimeError("graph retrieval is required in production and cannot be disabled")
        return None

    if backend == "neo4j":
        try:
            store = Neo4jGraphStore(Neo4jGraphConfig.from_env())
            if build_graph:
                store.build_from_corpus(settings.rag.data_dir)
            if not store.is_available():
                store.close()
                if require_graph:
                    raise RuntimeError("required Neo4j graph backend is unavailable")
                return None
            return Neo4jGraphSource(store)
        except MemoryError:
            raise
        except Exception as exc:
            if require_graph:
                raise RuntimeError(f"required Neo4j graph backend failed: {exc}") from exc
            logger.warning("Neo4j graph source unavailable: %s: %r", type(exc).__name__, exc)
            return None

    graph_index_dir = Path(index_dir or settings.rag.index_dir)
    graph_db_path = graph_index_dir / "graph_index.sqlite"
    if build_graph:
        if graph_index_dir.resolve() != settings.rag.index_dir.resolve():
            raise RuntimeError(
                "the active retrieval generation is immutable; build graph_index.sqlite offline"
            )
        LocalGraphIndex(graph_db_path).build_from_corpus(
            settings.rag.data_dir,
            corpus_fingerprint=expected_corpus_fingerprint,
        )
    if require_graph:
        _validate_local_graph_index(
            graph_db_path,
            expected_corpus_fingerprint=expected_corpus_fingerprint,
        )
    return GraphDbSource(graph_db_path) if graph_db_path.is_file() else None


def get_services(request: Request) -> WebServices:
    """FastAPI 依赖注入函数：从请求对象中获取 WebServices 实例。

    从 FastAPI 应用状态中提取在启动时创建的核心服务容器。

    参数:
        request: FastAPI 请求对象。

    返回:
        WebServices: 应用的核心服务容器实例。
    """
    return request.app.state.services


def sse(payload: dict) -> str:
    """将数据字典序列化为 SSE（Server-Sent Events）协议数据格式字符串。

    参数:
        payload: 要发送的数据字典，会被 JSON 序列化（ensure_ascii=False 以保留中文）。

    返回:
        str: 符合 SSE 协议的数据行，格式为 "data: {...}\\n\\n"。
    """
    import json

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
