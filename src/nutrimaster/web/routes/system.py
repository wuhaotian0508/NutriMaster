from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from nutrimaster.web.deps import WebServices, get_services

router = APIRouter()


@router.get("/api/health")
async def health(services: WebServices = Depends(get_services)):
    """系统健康检查端点。

    返回服务的运行状态，包括检索索引状态、文档块总数、
    已注册的工具列表和已加载的技能列表。
    该接口无需认证，供监控和运维使用。

    参数:
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 status、total_chunks、index、tools、skills 的 JSON 响应。
    """
    index_status = services.retriever.index_status() if hasattr(services.retriever, "index_status") else {}
    return JSONResponse(
        {
            "status": "ok",
            "total_chunks": len(services.retriever.chunks),
            "index": index_status,
            "tools": sorted(services.registry.tool_names),
            "skills": [skill.name for skill in services.skill_loader.list_dir()],
        }
    )


@router.get("/api/config")
async def frontend_config(services: WebServices = Depends(get_services)):
    """返回前端应用所需的公开配置信息。

    提供 Supabase 连接信息、站点 URL、管理后台端口、可用模型列表
    以及交互数据记录策略等配置。该接口无需认证。

    参数:
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 supabase_url、supabase_anon_key、admin_port、
                      site_url、models、interaction_capture 的 JSON 响应。
    """
    settings = services.settings
    rag = settings.rag
    return JSONResponse(
        {
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
            "admin_port": rag.admin_port if rag else 5501,
            "site_url": settings.site_url,
            "models": [],
            "interaction_capture": services.interaction_recorder.policy.public_config(),
        }
    )
