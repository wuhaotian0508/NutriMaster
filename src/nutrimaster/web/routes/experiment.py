from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nutrimaster.auth.service import get_current_user
from nutrimaster.web.deps import SSE_HEADERS, WebServices, get_services, sse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/experiment/preview")
async def experiment_preview(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """预览实验设计方案，返回候选基因列表。

    根据用户提供的研究目标或基因列表，调用实验设计服务生成候选基因预览。
    支持从 goal、query、answer_text 等多个字段读取研究目标，
    以及从 selected_gene_names 或 user_genes 字段读取用户指定的基因。

    参数:
        request: FastAPI 请求对象，请求体应包含 goal/query 或 genes 字段。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 genes 候选基因列表的 JSON 响应。

    异常:
        HTTPException: 缺少 goal 和 genes 时返回 400，服务异常时返回 500。
    """
    data = await request.json()
    goal = (data.get("goal") or data.get("query") or data.get("answer_text") or "").strip()
    genes = data.get("genes") or None
    selected_gene_names = data.get("selected_gene_names") or data.get("user_genes") or None
    if not goal and not genes:
        raise HTTPException(status_code=400, detail="缺少 goal 或 genes")
    try:
        preview = await services.experiment_service.preview(
            goal=goal,
            genes=genes,
            selected_gene_names=selected_gene_names,
        )
    except Exception as exc:
        logger.exception("[/api/experiment/preview] failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"genes": preview})


@router.post("/api/experiment/run")
async def experiment_run(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """执行完整的实验设计 Pipeline 并通过 SSE 流式返回结果。

    根据请求体中的基因列表执行一键实验设计流程，生成标准操作规程（SOP）。
    通过 Server-Sent Events 实时推送执行进度和最终结果。

    SSE 事件类型:
      - progress: 执行进度更新
      - result: 包含生成的 SOP 结果
      - done: 执行完成
      - error: 执行出错

    参数:
        request: FastAPI 请求对象，请求体应包含 genes 基因列表。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        StreamingResponse: SSE 流式响应。

    异常:
        HTTPException: 缺少 genes 列表时返回 400。
    """
    data = await request.json()
    genes = data.get("genes")
    if not genes:
        raise HTTPException(status_code=400, detail="缺少 genes 列表")

    async def generate():
        try:
            async for event in services.experiment_service.run(genes=genes):
                if event.get("type") in ("progress", "result", "error"):
                    yield sse(event)
            yield sse({"type": "done"})
        except Exception as exc:
            logger.exception("[/api/experiment/run] failed")
            yield sse({"type": "error", "data": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/gene-transfer/preview")
async def gene_transfer_preview(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """预览转基因实验方案：从对话文本提取基因（NCBI 验证）并推断受体物种。

    返回:
        JSONResponse: 包含 genes（验证后的基因列表）和 species（受体物种名列表）的 JSON。
    """
    data = await request.json()
    goal = (data.get("goal") or data.get("query") or data.get("answer_text") or "").strip()
    selected_gene_names = data.get("selected_gene_names") or data.get("user_genes") or None
    if not goal:
        raise HTTPException(status_code=400, detail="缺少 goal")
    try:
        result = await services.gene_transfer_service.preview_species(
            goal=goal,
            selected_gene_names=selected_gene_names,
        )
    except Exception as exc:
        logger.exception("[/api/gene-transfer/preview] failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/api/gene-transfer/run")
async def gene_transfer_run(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """执行转基因实验方案生成 Pipeline，通过 SSE 流式返回结果。

    请求体字段:
        genes: 已验证的基因信息字典列表。
        species_list: 受体物种拉丁名列表。

    SSE 事件类型:
        progress / result / done / error
    """
    data = await request.json()
    genes = data.get("genes")
    species_list = data.get("species_list")
    if not genes:
        raise HTTPException(status_code=400, detail="缺少 genes 列表")
    if not species_list:
        raise HTTPException(status_code=400, detail="缺少 species_list 列表")

    async def generate():
        try:
            yield sse({"type": "progress", "step": 1, "total": 2, "msg": "正在从 NCBI 获取基因序列及上下游区域..."})
            sops = await services.gene_transfer_service.run(genes=genes, species_list=species_list)
            yield sse({"type": "progress", "step": 2, "total": 2, "msg": "正在填充转基因实验方案模板..."})
            yield sse({"type": "result", "sops": sops})
            yield sse({"type": "done"})
        except Exception as exc:
            logger.exception("[/api/gene-transfer/run] failed")
            yield sse({"type": "error", "data": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)
