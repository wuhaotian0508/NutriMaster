from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nutrimaster.auth.service import get_current_user
from nutrimaster.web.deps import SSE_HEADERS, WebServices, get_services, sse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/query")
async def query(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """处理用户的自然语言查询，通过 AI Agent 生成回答并以 SSE 流式返回。

    构建系统提示词、截断对话历史、初始化交互记录会话，
    然后启动 Agent 运行循环，将工具调用和回答内容逐步推送给前端。

    请求体字段:
      - query: 用户查询文本（必填）
      - history: 对话历史消息列表
      - use_personal: 是否使用个人文献库
      - use_depth: 是否启用深度检索模式
      - model_id: 指定使用的 LLM 模型
      - session_id: 会话 ID
      - client_turn_id: 客户端轮次 ID
      - capture_consent: 是否同意记录交互数据

    参数:
        request: FastAPI 请求对象。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        StreamingResponse: SSE 流式响应，推送 Agent 的回答事件。

    异常:
        HTTPException: 查询为空时返回 400。
    """
    data = await request.json()
    query_text = (data.get("query") or "").strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="查询不能为空")
    history = data.get("history", []) or []
    use_personal = data.get("use_personal", False)
    use_depth = data.get("use_depth", False)
    model_id = data.get("model_id", "")
    system_prompt = services.agent.prompt_builder.build(
        user_id=user.id,
        use_depth=use_depth,
        use_personal=use_personal,
    )
    initial_messages = [
        {"role": "system", "content": system_prompt},
        *services.agent._truncate_history(history),
        {"role": "user", "content": query_text},
    ]
    initial_messages = services.agent._strip_reasoning_for_new_turn(initial_messages)
    capture_session = services.interaction_recorder.start(
        user_id=user.id,
        session_id=data.get("session_id") or "",
        client_turn_id=data.get("client_turn_id") or "",
        query=query_text,
        model_id=model_id,
        history=history,
        initial_messages=initial_messages,
        use_personal=use_personal,
        use_depth=use_depth,
        capture_consent=bool(data.get("capture_consent")),
    )

    async def generate():
        """SSE 流式事件生成器，逐步推送 Agent 的回答事件。

        首先发送交互记录捕获状态，然后迭代 Agent 运行产生的事件
        （包括文本块、工具调用、引用等），每个事件都记录到交互会话中。
        结束时调用 capture_session.finish 完成记录。

        生成:
            str: 格式化的 SSE 数据字符串。
        """
        status = "completed"
        try:
            yield sse(
                {
                    "type": "capture",
                    "enabled": capture_session.active,
                    "interaction_id": capture_session.interaction_id if capture_session.active else "",
                    "turn_id": capture_session.turn_id,
                }
            )
            async for event in services.agent.run(
                user_input=query_text,
                user_id=user.id,
                model_id=model_id,
                history=history,
                use_personal=use_personal,
                use_depth=use_depth,
                skill_prefs={},
                tool_overrides={},
            ):
                capture_session.capture_event(event)
                if event.get("type") == "error":
                    status = "error"
                yield sse(event)
        except Exception as exc:
            logger.exception("[/api/query] failed")
            status = "error"
            capture_session.capture_event({"type": "error", "data": str(exc)})
            yield sse({"type": "error", "data": str(exc)})
        finally:
            capture_session.finish(status=status)

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/feedback")
async def feedback(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """提交用户对某次 AI 对话回答的反馈评价。

    支持点赞（up）和点踩（down）两种评价方式，
    可附带评论文字和标签。反馈数据会被记录用于后续模型改进。

    请求体字段:
      - interaction_id: 交互记录 ID（必填）
      - rating: 评价类型，"up" 或 "down"（必填）
      - session_id: 会话 ID
      - turn_id/client_turn_id: 轮次 ID
      - comment: 评价备注文字
      - tags: 标签列表

    参数:
        request: FastAPI 请求对象。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 status 和 feedback_id 的 JSON 响应。

    异常:
        HTTPException: interaction_id 为空（400）或 rating 值无效（400）。
    """
    data = await request.json()
    interaction_id = (data.get("interaction_id") or "").strip()
    rating = (data.get("rating") or "").strip().lower()
    if not interaction_id:
        raise HTTPException(status_code=400, detail="interaction_id 不能为空")
    if rating not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="rating 必须是 up 或 down")
    payload = services.interaction_recorder.record_feedback(
        user_id=user.id,
        interaction_id=interaction_id,
        session_id=data.get("session_id") or "",
        turn_id=data.get("turn_id") or data.get("client_turn_id") or "",
        rating=rating,
        comment=(data.get("comment") or "").strip(),
        tags=data.get("tags") or [],
    )
    return JSONResponse({"status": "ok", "feedback_id": payload["feedback_id"]})


@router.post("/api/rag/search")
async def rag_search_debug(
    request: Request,
    user=Depends(get_current_user),
    services: WebServices = Depends(get_services),
):
    """RAG 检索调试接口，直接执行检索并返回详细结果。

    绕过 Agent 直接调用 rag_search 工具，返回检索查询、模式、
    各数据源命中数、引用列表和格式化文本。用于调试和测试检索效果。

    请求体字段:
      - query: 检索查询文本（必填）
      - pubmed_query: PubMed 专用查询
      - gene_db_query: 基因数据库专用查询
      - mode: 检索模式（"normal" 或 "deep"）
      - include_personal/use_personal: 是否包含个人文献库
      - focus: 检索聚焦方向（默认 "general"）
      - top_k: 返回结果数量（默认 10）

    参数:
        request: FastAPI 请求对象。
        user: 当前登录用户（通过依赖注入获取）。
        services: Web 服务容器（通过依赖注入获取）。

    返回:
        JSONResponse: 包含 query、mode、source_counts、citations、text 的 JSON 响应。

    异常:
        HTTPException: query 为空（400）或 rag_search 工具未注册（500）。
    """
    data = await request.json()
    query_text = (data.get("query") or "").strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="query 不能为空")
    rag_tool = services.registry.get("rag_search")
    if rag_tool is None:
        raise HTTPException(status_code=500, detail="rag_search 未注册")
    packet = await rag_tool.execute(
        query=query_text,
        pubmed_query=data.get("pubmed_query") or "",
        gene_db_query=data.get("gene_db_query") or "",
        mode=data.get("mode") or ("deep" if data.get("use_depth") else "normal"),
        include_personal=bool(data.get("include_personal") or data.get("use_personal")),
        focus=data.get("focus") or "general",
        top_k=int(data.get("top_k") or 10),
        user_id=user.id,
    )
    return JSONResponse(
        {
            "query": packet.query,
            "mode": packet.mode,
            "source_counts": packet.source_counts,
            "citations": packet.citations,
            "text": packet.to_tool_text(),
        }
    )
