import json

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


def _sse_event(name: str, payload) -> str:
    """构造一个具名 SSE 事件。

    具名事件不会被 ``EventSource.onmessage`` 收到（它只触发
    ``addEventListener(name, ...)``），因此**不认识具名事件的老客户端会直接忽略它**，
    正文仍能正常渲染 —— 这让新增的来源/元信息事件是「纯增量」的，
    不会把已有客户端弄坏。
    """
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)
    session_id: str = ""


async def sse_generator(
    chunks_generator,
    session_id: str,
    user_message: str,
    user_id=None,
    sources_holder: list = None,
    meta_holder: dict = None,
):
    """把文本流转成 SSE，并在正文前后附带结构化事件。

    事件序列（均为具名事件，正文字段保持裸 ``data:`` 格式以兼容老客户端）::

        event: sources        # 命中的知识库条目（在首个文本分片之前）
        data: 您好            # 正文分片，逐段推送
        data: ，我是…
        event: meta           # 本轮用了哪个模型、花了多少 token、耗时

    为什么 sources 能在正文之前发出：检索发生在首个分片产出**之前**
    （见 ``ai_service._build_context``），因此读到第一个分片时来源已就绪。
    """
    full_response = ""
    started = False

    async for chunk in chunks_generator:
        if session_id and is_cancelled(session_id, user_id):
            yield "data: [已取消]\n\n"
            clear_cancel(session_id, user_id)
            return

        if not started:
            started = True
            # 先告诉前端「答案依据了哪些资料」，再开始推正文
            if sources_holder:
                yield _sse_event("sources", sources_holder)

        full_response += chunk
        yield f"data: {chunk}\n\n"

    if session_id:
        clear_cancel(session_id, user_id)
        await update_conversation_history(session_id, user_message, full_response, user_id)

    if meta_holder:
        yield _sse_event("meta", meta_holder)


@router.post("/chat-local")
async def chat_local(request: ChatRequest, current_user: User = Depends(get_current_user)):
    user_id = current_user.id

    async def generate():
        sources: list = []
        async for chunk in sse_generator(
            call_ollama_stream(
                request.message, request.session_id, None, user_id,
                on_sources=lambda items: sources.extend(items),
            ),
            request.session_id, request.message, user_id,
            sources_holder=sources,
        ):
            yield chunk
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/chat-stream")
@router.post("/stream", include_in_schema=False)
async def chat_stream(request: ChatRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    user_id = current_user.id

    async def generate():
        # 两个 holder 由回调写入、由 sse_generator 读取：检索在首个分片之前完成，
        # 因此「来源」能在正文之前发出；「元信息」在流结束后发出。
        sources: list = []
        meta: dict = {}
        async for chunk in sse_generator(
            call_chat_stream(
                request.message, request.session_id, db, user_id,
                on_sources=lambda items: sources.extend(items),
                on_meta=lambda info: meta.update(info),
            ),
            request.session_id, request.message, user_id,
            sources_holder=sources, meta_holder=meta,
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
