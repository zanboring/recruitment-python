from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_service import (
    call_glm4_stream, call_ollama_stream, call_chat_stream, update_conversation_history,
    request_cancel, is_cancelled, clear_cancel,
)
from app.services.model_service import ModelService
from app.schemas.common import Result
from app.database import get_db

router = APIRouter(prefix="/api/ai", tags=["AI对话"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


async def sse_generator(chunks_generator, session_id: str, user_message: str):
    full_response = ""
    async for chunk in chunks_generator:
        if session_id and is_cancelled(session_id):
            yield f"data: [已取消]\n\n"
            clear_cancel(session_id)
            return
        full_response += chunk
        yield f"data: {chunk}\n\n"
    if session_id:
        clear_cancel(session_id)
        await update_conversation_history(session_id, user_message, full_response)


@router.post("/chat")
async def chat(request: ChatRequest):
    async def generate():
        async for chunk in sse_generator(call_glm4_stream(request.message, request.session_id), request.session_id, request.message):
            yield chunk
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/chat-local")
async def chat_local(request: ChatRequest):
    async def generate():
        async for chunk in sse_generator(call_ollama_stream(request.message, request.session_id), request.session_id, request.message):
            yield chunk
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/chat-stream")
async def chat_stream(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    async def generate():
        async for chunk in sse_generator(call_chat_stream(request.message, request.session_id, db), request.session_id, request.message):
            yield chunk
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/status")
async def ai_status():
    """返回 AI 模块状态：GLM-4 配置情况、Ollama 可用性、当前模型偏好。"""
    status = await ModelService.get_status()
    return Result.success(status)


@router.post("/cancel")
async def cancel_chat(session_id: str = Query(..., description="要取消的会话 ID")):
    """取消指定会话正在进行的流式请求。"""
    request_cancel(session_id)
    return Result.success({"session_id": session_id, "cancelled": True})