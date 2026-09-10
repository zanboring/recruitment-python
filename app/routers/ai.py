from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_service import (
    call_ollama_stream, call_chat_stream, update_conversation_history,
    request_cancel, is_cancelled, clear_cancel, clear_session,
)
from app.services.model_service import ModelService
from app.schemas.common import Result
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/ai", tags=["AI对话"])

MAX_MESSAGE_LENGTH = 2000


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)
    session_id: str = ""


async def sse_generator(chunks_generator, session_id: str, user_message: str, user_id=None):
    full_response = ""
    async for chunk in chunks_generator:
        if session_id and is_cancelled(session_id, user_id):
            yield f"data: [已取消]\n\n"
            clear_cancel(session_id, user_id)
            return
        full_response += chunk
        yield f"data: {chunk}\n\n"
    if session_id:
        clear_cancel(session_id, user_id)
        await update_conversation_history(session_id, user_message, full_response, user_id)


@router.post("/chat-local")
async def chat_local(request: ChatRequest, current_user: User = Depends(get_current_user)):
    user_id = current_user.id

    async def generate():
        async for chunk in sse_generator(
            call_ollama_stream(request.message, request.session_id, None, user_id),
            request.session_id, request.message, user_id
        ):
            yield chunk
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/chat-stream")
@router.post("/stream", include_in_schema=False)
async def chat_stream(request: ChatRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = current_user.id

    async def generate():
        async for chunk in sse_generator(
            call_chat_stream(request.message, request.session_id, db, user_id),
            request.session_id, request.message, user_id
        ):
            yield chunk
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/status")
async def ai_status(current_user: User = Depends(get_current_user)):
    """返回 AI 模块状态：GLM-4 配置情况、Ollama 可用性、当前模型偏好。"""
    status = await ModelService.get_status()
    return Result.success(status)


@router.post("/cancel")
async def cancel_chat(session_id: str = Query(..., description="要取消的会话 ID"), current_user: User = Depends(get_current_user)):
    """取消当前用户指定会话正在进行的流式请求。"""
    request_cancel(session_id, current_user.id)
    return Result.success({"session_id": session_id, "cancelled": True})


@router.delete("/session")
async def clear_session_history(session_id: str = Query(..., description="要清空的会话 ID"), current_user: User = Depends(get_current_user)):
    """清空当前用户指定会话的历史（前端「新建会话」按钮）。"""
    clear_session(session_id, current_user.id)
    return Result.success({"session_id": session_id, "cleared": True})
