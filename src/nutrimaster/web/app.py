from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.wsgi import WSGIMiddleware

from nutrimaster.config.settings import Settings
from nutrimaster.web.deps import create_services
from nutrimaster.web.request_limits import RequestBodyLimitMiddleware
from nutrimaster.web.routes import admin, auth, experiment, library, pi, query, system

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    force=True,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 NutriMaster FastAPI 应用实例。

    初始化所有核心服务（检索器、Agent、工具注册表等），配置 CORS 中间件、
    异常处理、上传限制、路由、静态文件服务，并挂载 Flask 管理后台。

    参数:
        settings: 应用配置对象。若为 None，则从环境变量自动加载。

    返回:
        FastAPI: 完整配置的 FastAPI 应用实例。

    异常:
        RuntimeError: 当 RAG 配置初始化失败时抛出。
    """
    settings = settings or Settings.from_env()
    if settings.rag is None:
        raise RuntimeError("RAG settings failed to initialize")

    app = FastAPI(title="NutriMaster", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    logger.info("正在初始化 Web 服务与检索索引...")
    app.state.services = create_services(settings)
    app.state.limiter = Limiter(key_func=get_remote_address)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.site_url] if settings.site_url else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _install_exception_handlers(app)
    _install_upload_limit(app)
    _install_routes(app)
    _install_static(app)
    _mount_extraction_admin(app)

    services = app.state.services
    logger.info("已注册工具: %s", sorted(services.registry.tool_names))
    logger.info("已加载 Skills: %s", [skill.name for skill in services.skill_loader.list_dir()])
    logger.info("检索器初始化完成，已加载 %s 个文档块", len(services.retriever.chunks))
    return app


def _install_exception_handlers(app: FastAPI) -> None:
    """为 FastAPI 应用安装全局异常处理器。

    注册两个异常处理器：
      1. RateLimitExceeded: 请求频率超限时返回 429 错误响应。
      2. Exception: 捕获所有未处理异常，记录堆栈日志并返回 500 错误（HTTPException 除外）。

    参数:
        app: FastAPI 应用实例。
    """
    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        """处理请求频率超限异常。

        参数:
            request: 当前 HTTP 请求对象。
            exc: 频率限制超出异常实例。

        返回:
            JSONResponse: 包含错误提示的 429 状态码响应。
        """
        return JSONResponse({"error": "请求频率过高，请稍后再试"}, status_code=429)

    @app.exception_handler(Exception)
    async def _generic_exception_handler(request: Request, exc: Exception):
        """处理所有未捕获的通用异常并记录日志。

        HTTPException 会被直接重新抛出，由 FastAPI 框架自行处理。

        参数:
            request: 当前 HTTP 请求对象。
            exc: 未处理的异常实例。

        返回:
            JSONResponse: 包含通用错误提示的 500 状态码响应。
        """
        if isinstance(exc, MemoryError):
            # Allocator exhaustion is a process-capacity signal. Avoid a
            # traceback log and synthetic JSON allocation while memory is low.
            raise exc
        if isinstance(exc, HTTPException):
            raise exc
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "服务器内部错误，请稍后再试"}, status_code=500)


def _install_upload_limit(app: FastAPI) -> None:
    """为 FastAPI 应用安装文件上传大小限制中间件。

    检查请求的 Content-Length header，若超过 50MB 限制则拒绝请求并返回 413 错误。

    参数:
        app: FastAPI 应用实例。
    """
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=50 * 1024 * 1024,
        # Starlette's WSGI adapter concatenates the complete body before Flask
        # authentication runs. Serialize Admin mutations so several allowed
        # 50MiB requests cannot become an aggregate pre-authentication OOM.
        serialized_body_prefixes=("/admin/",),
    )


def _install_routes(app: FastAPI) -> None:
    """注册所有 API 路由。"""
    app.include_router(query.router)
    app.include_router(pi.router)
    app.include_router(experiment.router)
    app.include_router(library.router)
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(system.router)


def _install_static(app: FastAPI) -> None:
    """挂载静态文件目录并注册首页路由。"""
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index():
        """返回首页 HTML 文件。"""
        return FileResponse(str(static_dir / "index.html"))


def _mount_extraction_admin(app: FastAPI) -> None:
    """挂载 Flask extraction admin 蓝图到 /admin 路径。"""
    from flask import Flask as FlaskApp
    from nutrimaster.web.admin.app import (
        admin_bp,
        configure_index_build_status,
        configure_index_refresh,
        configure_index_status,
        configure_pipeline_execution_gate,
    )

    services = app.state.services

    def refresh_admin_index(data_dir: Path, force: bool = False) -> dict:
        """Queue an isolated index build for the management console."""
        return services.request_index_build(
            data_dir=data_dir,
            force=force,
            reason="extraction-admin",
        )

    flask_app = FlaskApp(__name__, static_folder=None)
    configure_index_refresh(refresh_admin_index)
    configure_index_status(services.retriever.index_status)
    configure_index_build_status(services.index_build_status)
    configure_pipeline_execution_gate(services.experiment_service.execution_gate)
    flask_app.register_blueprint(admin_bp)
    app.mount("/admin", WSGIMiddleware(flask_app))


app = create_app()


if __name__ == "__main__":
    import uvicorn

    runtime_settings = app.state.settings
    rag = runtime_settings.rag
    # This module has already constructed ``app`` above. Reusing that object
    # prevents ``python -m nutrimaster.web.app`` from importing the module a
    # second time and constructing another corpus-sized service container.
    # Use ``nutrimaster.cli web --reload`` for the development reloader.
    uvicorn.run(
        app,
        host=rag.web_host if rag else "0.0.0.0",
        port=rag.web_port if rag else 5000,
        reload=False,
        workers=1,
    )
