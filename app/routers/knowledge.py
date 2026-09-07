from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException
from app.schemas.common import Result
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseResponse
from app.services.knowledge_service import KnowledgeService
from app.dependencies import require_admin

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/list")
async def list_knowledge(
    page_num: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    items, total = await KnowledgeService.list_knowledge(db, page_num, page_size)
    return Result.success({
        "list": [KnowledgeBaseResponse.model_validate(item) for item in items],
        "total": total
    })


@router.get("/search")
async def search_knowledge(
    keyword: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    items = await KnowledgeService.search(db, keyword)
    return Result.success([KnowledgeBaseResponse.model_validate(item) for item in items])


@router.get("/{knowledge_id}")
async def get_knowledge(
    knowledge_id: int,
    db: AsyncSession = Depends(get_db)
):
    item = await KnowledgeService.get_by_id(db, knowledge_id)
    if not item:
        raise AppException("知识条目不存在", 404)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.post("")
async def create_knowledge(
    request: KnowledgeBaseCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    item = await KnowledgeService.create(db, request)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.put("/{knowledge_id}")
async def update_knowledge(
    knowledge_id: int,
    request: KnowledgeBaseUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    try:
        item = await KnowledgeService.update(db, knowledge_id, request)
    except ValueError as e:
        raise AppException(str(e), 404)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.delete("/{knowledge_id}")
async def delete_knowledge(
    knowledge_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    try:
        await KnowledgeService.delete(db, knowledge_id)
    except ValueError as e:
        raise AppException(str(e), 404)
    return Result.success(None)


@router.patch("/{knowledge_id}/toggle")
async def toggle_knowledge(
    knowledge_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    try:
        item = await KnowledgeService.toggle_status(db, knowledge_id)
    except ValueError as e:
        raise AppException(str(e), 404)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.patch("/{knowledge_id}/score")
async def set_knowledge_score(
    knowledge_id: int,
    score: int = Query(..., ge=0, le=10),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    try:
        item = await KnowledgeService.set_score(db, knowledge_id, score)
    except ValueError as e:
        raise AppException(str(e), 404)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.get("/context/{keyword}")
async def get_context(
    keyword: str,
    db: AsyncSession = Depends(get_db)
):
    context = await KnowledgeService.get_context_for_ai(db, keyword)
    return Result.success(context)


@router.post("/learn")
async def learn_knowledge(
    question: str,
    answer: str,
    tags: str = "",
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    await KnowledgeService.learn_from_response(db, question, answer, tags)
    return Result.success(None)


@router.get("/all")
async def get_all_enabled(
    db: AsyncSession = Depends(get_db)
):
    items = await KnowledgeService.get_all_enabled(db)
    return Result.success([KnowledgeBaseResponse.model_validate(item) for item in items])


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db)
):
    stats = await KnowledgeService.get_stats(db)
    return Result.success(stats)
