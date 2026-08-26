from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from nutrimaster.auth.service import get_current_user
from nutrimaster.web.deps import WebServices, get_services

router = APIRouter()


class FileStorageAdapter:
    """将 FastAPI 的 UploadFile 适配为文件存储接口。

    封装 UploadFile 对象，提供 save 方法用于将上传的文件内容
    写入到本地文件系统的指定路径。
    """

    def __init__(self, upload_file: UploadFile, *, max_bytes: int):
        """初始化文件存储适配器。

        参数:
            upload_file: FastAPI 的 UploadFile 实例。
            max_bytes: 流式写入允许的最大字节数。
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._file = upload_file
        self._max_bytes = int(max_bytes)

    def save(self, path):
        """将上传文件的内容保存到指定的本地路径。

        分块复制上传内容，并在超过上限时立即停止，避免缺少或伪造
        Content-Length 的请求写满磁盘。

        参数:
            path: 目标文件路径（字符串或 Path 对象）。
        """
        target = Path(path)
        written = 0
        try:
            with target.open("wb") as output:
                while chunk := self._file.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > self._max_bytes:
                        max_mb = self._max_bytes / (1024 * 1024)
                        raise ValueError(f"文件过大，最大 {max_mb:g}MB")
                    output.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise


@router.post("/api/personal/upload")
@router.post("/api/library/upload")
async def personal_upload(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """上传 PDF 文件到当前用户的个人文献库。

    仅支持 PDF 格式文件。上传后自动进行文本提取和索引，
    使文件内容可用于个人知识库的 RAG 检索。

    参数:
        request: FastAPI 请求对象。
        file: 上传的 PDF 文件。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含上传状态和文件信息的 JSON 响应。

    异常:
        HTTPException: 非 PDF 文件（400）或上传处理失败（400）。
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    try:
        library = services.get_personal_lib(user.id)
        max_bytes = int(library.rag_settings.max_pdf_size_mb * 1024 * 1024)
        info = await asyncio.to_thread(
            library.upload_pdf,
            FileStorageAdapter(file, max_bytes=max_bytes),
            file.filename,
        )
        return JSONResponse({"status": "ok", "file": info})
    except (ValueError, ImportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/personal/files")
@router.get("/api/library/files")
async def personal_files(
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """获取当前用户个人文献库中所有文件的列表。

    返回文件名、大小、上传时间等元信息。

    参数:
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 files 文件列表的 JSON 响应。
    """
    files = await asyncio.to_thread(services.get_personal_lib(user.id).list_files)
    return JSONResponse({"files": files})


@router.delete("/api/personal/files/{filename}")
@router.delete("/api/library/files/{filename}")
async def personal_delete(
    filename: str,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """删除当前用户个人文献库中的指定文件。

    参数:
        filename: 要删除的文件名。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 删除成功时返回 {"status": "ok"}。

    异常:
        HTTPException: 文件不存在时抛出 404。
    """
    ok = await asyncio.to_thread(services.get_personal_lib(user.id).delete_file, filename)
    if ok:
        return JSONResponse({"status": "ok"})
    raise HTTPException(status_code=404, detail="文件不存在")


@router.put("/api/personal/files/{filename}/rename")
@router.put("/api/library/files/{filename}/rename")
async def personal_rename(
    filename: str,
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """重命名当前用户个人文献库中的指定文件。

    参数:
        filename: 要重命名的原文件名。
        request: FastAPI 请求对象，请求体应包含 new_name 字段。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 重命名成功时返回 {"status": "ok"}。

    异常:
        HTTPException: 新文件名为空（400）或文件不存在（404）。
    """
    data = await request.json()
    new_name = (data.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="新文件名不能为空")
    ok = await asyncio.to_thread(services.get_personal_lib(user.id).rename_file, filename, new_name)
    if ok:
        return JSONResponse({"status": "ok"})
    raise HTTPException(status_code=404, detail="文件不存在")
