from __future__ import annotations

import os
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
from nutrimaster.experiment import ExperimentDesignService
from nutrimaster.rag.jina import JinaRetriever
from nutrimaster.rag.personal_library import PersonalLibrary
from nutrimaster.rag.service import (
    GeneDbSource,
    PersonalLibrarySource,
    PubMedSource,
    RAGSearchService,
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
    personal_libs: TTLCache = field(default_factory=lambda: TTLCache(maxsize=200, ttl=3600))
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

    def refresh_index(self, data_dir: Path | None = None, *, force: bool = False) -> None:
        """刷新 RAG 向量检索索引并清除 BM25 缓存。

        执行增量索引构建，并清除 BM25 缓存以确保后续搜索使用最新数据。

        参数:
            data_dir: 语料文件目录路径。若为 None，使用检索器的默认目录。
            force: 是否强制重建索引（默认 False，仅增量更新）。
        """
        kwargs: dict[str, Any] = {"incremental": True, "force": force}
        if data_dir is not None:
            kwargs["data_dir"] = data_dir
        self.retriever.build_index(**kwargs)
        if hasattr(self.retriever, "_bm25"):
            self.retriever._bm25 = None


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
    settings = settings or Settings.from_env()
    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")
    retriever = JinaRetriever(settings=settings)
    build_index = os.getenv("NUTRIMASTER_WEB_BUILD_INDEX", "").lower() in {"1", "true", "yes", "on"}

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

    if build_index:
        retriever.build_index()

    registry = ToolRegistry()
    experiment_service = ExperimentDesignService()
    rag_service = RAGSearchService(
        pubmed_source=PubMedSource(),
        gene_db_source=GeneDbSource(retriever),
        personal_source=PersonalLibrarySource(
            get_personal_lib=get_personal_lib,
            get_query_embedding=retriever.get_query_embedding,
        ),
    )
    for tool in (RagSearchTool(rag_service), ExperimentDesignTool(experiment_service)):
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
    )
    holder["services"] = services
    return services


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
