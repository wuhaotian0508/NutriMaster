from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from nutrimaster.auth.service import ADMIN_EMAILS, get_current_user
from nutrimaster.web.deps import WebServices, get_services

logger = logging.getLogger(__name__)
router = APIRouter()


def _admin_json_upload_limit() -> int:
    raw = os.getenv("NUTRIMASTER_ADMIN_JSON_MAX_BYTES", str(16 * 1024 * 1024))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("NUTRIMASTER_ADMIN_JSON_MAX_BYTES must be an integer") from exc
    if not 1 <= value <= 50 * 1024 * 1024:
        raise RuntimeError("NUTRIMASTER_ADMIN_JSON_MAX_BYTES must be between 1 byte and 50 MiB")
    return value


def _require_admin(user) -> None:
    """校验用户是否具有管理员权限。

    检查用户邮箱（转小写后）是否在 ADMIN_EMAILS 白名单中。
    若不在白名单中，抛出 403 HTTP 异常。

    参数:
        user: Supabase 用户对象，需包含 email 属性。

    异常:
        HTTPException: 当用户邮箱不在管理员白名单中时抛出（403）。
    """
    email = (user.email or "").lower()
    if email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="仅管理员可访问")


@router.get("/api/skills")
async def list_skills(user=Depends(get_current_user), services: WebServices = Depends(get_services)):
    """获取所有可用技能的列表及其详细信息。

    返回每个技能的名称、描述、关联工具列表、是否共享及完整内容。
    需要用户登录认证。

    参数:
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 skills 列表的 JSON 响应。
    """
    skills = services.skill_loader.list_dir()
    return JSONResponse(
        {
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "tools": skill.tools,
                    "is_shared": skill.is_shared,
                    "content": skill.content,
                }
                for skill in skills
            ]
        }
    )


@router.get("/api/skills/{name}")
async def get_skill(name: str, user=Depends(get_current_user), services: WebServices = Depends(get_services)):
    """根据名称获取指定技能的详细信息。

    参数:
        name: 技能名称。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含技能详情的 JSON 响应。

    异常:
        HTTPException: 当指定名称的技能不存在时抛出（404）。
    """
    skill = services.skill_loader.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return JSONResponse(
        {
            "name": skill.name,
            "description": skill.description,
            "tools": skill.tools,
            "content": skill.content,
            "is_shared": skill.is_shared,
        }
    )


@router.post("/api/skills")
@router.post("/api/skills/generate")
@router.put("/api/skills/{name}")
@router.delete("/api/skills/{name}")
async def skills_readonly():
    """技能管理的只读拒绝端点。

    Skills 现已改为通过后台文件夹维护，前端编辑功能已禁用。
    对所有技能的创建、生成、更新和删除请求均返回 410 Gone。

    异常:
        HTTPException: 始终抛出 410 状态码，提示功能已废弃。
    """
    raise HTTPException(status_code=410, detail="Skills 现在通过后台文件夹维护，不再提供前端编辑接口")


@router.get("/api/tools")
async def list_tools(user=Depends(get_current_user), services: WebServices = Depends(get_services)):
    """获取所有已注册工具的列表。

    返回工具注册表中所有工具的信息。需要用户登录认证。

    参数:
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 tools 列表的 JSON 响应。
    """
    return JSONResponse({"tools": services.registry.list_all()})


@router.post("/api/admin/upload_data")
async def admin_upload_data(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """管理员上传已验证的基因数据 JSON 文件并触发索引重建。

    验证上传文件名是否以 _nutri_plant_verified.json 结尾，
    检查文件内容是否为合法 JSON，保存到语料目录后启动后台索引重建。
    若索引重建正在运行，返回 queued 状态。

    参数:
        file: 上传的文件对象。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含上传状态和文件名的 JSON 响应。

    异常:
        HTTPException: 文件名格式不符（400）、JSON 无效（400）、
                       RAG 未配置（500）或非管理员（403）。
    """
    _require_admin(user)
    filename = file.filename or ""
    if (
        not filename.endswith("_nutri_plant_verified.json")
        or filename != Path(filename).name
    ):
        raise HTTPException(status_code=400, detail="文件名必须以 _nutri_plant_verified.json 结尾")
    if services.settings.rag is None:
        raise HTTPException(status_code=500, detail="RAG 未配置")
    target_path = services.settings.rag.data_dir / filename
    temporary_path = target_path.with_name(
        f".{target_path.name}.{os.getpid()}.{uuid.uuid4().hex}.upload"
    )
    try:
        upload_limit = _admin_json_upload_limit()
        content = await file.read(upload_limit + 1)
        if len(content) > upload_limit:
            raise HTTPException(status_code=413, detail="JSON 文件超过管理员上传大小限制")
        json.loads(content.decode("utf-8"))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target_path)
        directory_fd = os.open(
            target_path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="文件不是有效的 JSON 格式") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    try:
        job = services.request_index_build(force=False, reason="verified-json-upload")
    except Exception as exc:
        logger.exception("[admin] failed to dispatch isolated index builder")
        raise HTTPException(
            status_code=503,
            detail=f"文件已保存，但索引构建任务提交失败: {exc}",
        ) from exc
    return JSONResponse(
        {
            "status": "queued",
            "filename": filename,
            "job_id": job["job_id"],
            "message": "文件已保存，索引任务已排队；尚未构建或生效",
        },
        status_code=202,
    )


@router.get("/api/admin/reindex_status")
async def admin_reindex_status(
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """查询当前索引重建任务的运行状态和统计信息。

    返回索引重建是否正在运行、进度描述、错误信息、上次完成时间、
    当前索引块数以及语料目录中的数据文件数。仅管理员可访问。

    参数:
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含索引状态信息的 JSON 响应。
    """
    _require_admin(user)
    data_files_count = 0
    if services.settings.rag is not None:
        data_files_count = len(list(services.settings.rag.data_dir.glob("*_nutri_plant_verified.json")))
    payload = services.index_build_status()
    payload.update(
        {
            "running": payload.get("state") in {
                "preflight",
                "snapshotting",
                "building",
                "publishing",
                "activating",
            },
            "current_chunks": len(services.retriever.chunks),
            "data_files_count": data_files_count,
        }
    )
    return JSONResponse(payload)
