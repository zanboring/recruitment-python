from fastapi import APIRouter, Depends, Query
from pydantic import AliasChoices
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException
from app.schemas.common import Result
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseResponse
from app.services.knowledge_service import KnowledgeService
from app.dependencies import require_admin
from app.utils.log_decorator import log_action

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/list")
async def list_knowledge(
    page_num: int = Query(1, ge=1, validation_alias=AliasChoices("page_num", "page")),
    page_size: int = Query(20, ge=1, le=100, validation_alias=AliasChoices("page_size", "size", "pageSize")),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    """分页查询知识条目。

    分页参数同时接受 page_num/page_size（原生）与 page/size（Java 版前端）；
    响应额外附带 page/size，供前端分页组件回显。
    """
    items, total = await KnowledgeService.list_knowledge(db, page_num, page_size)
    return Result.success({
        "list": [KnowledgeBaseResponse.model_validate(item) for item in items],
        "total": total,
        "page": page_num,
        "size": page_size,
    })


@router.get("/search")
async def search_knowledge(
    keyword: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    items = await KnowledgeService.search(db, keyword)
    return Result.success([KnowledgeBaseResponse.model_validate(item) for item in items])


# 注册顺序说明：/all、/stats 等静态路径必须排在动态路径 /{knowledge_id} 之前。
# FastAPI 按注册顺序匹配，若 /{knowledge_id} 在前，GET /api/knowledge/all
# 会命中 /{knowledge_id} 并把 "all" 当 int 解析，直接 422 int_parsing。
@router.get("/all")
async def get_all_enabled(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    items = await KnowledgeService.get_all_enabled(db)
    return Result.success([KnowledgeBaseResponse.model_validate(item) for item in items])


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    stats = await KnowledgeService.get_stats(db)
    return Result.success(stats)


@router.get("/{knowledge_id}")
async def get_knowledge(
    knowledge_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    item = await KnowledgeService.get_by_id(db, knowledge_id)
    if not item:
        raise AppException("知识条目不存在", 404)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.post("")
@log_action("新增知识条目")
async def create_knowledge(
    request: KnowledgeBaseCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    item = await KnowledgeService.create(db, request)
    return Result.success(KnowledgeBaseResponse.model_validate(item))


@router.put("/{knowledge_id}")
@log_action("更新知识条目")
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
@log_action("删除知识条目")
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
@log_action("切换知识条目状态")
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
@log_action("知识条目评分")
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
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    context = await KnowledgeService.get_context_for_ai(db, keyword)
    return Result.success(context)


@router.post("/learn")
@log_action("知识库学习入库")
async def learn_knowledge(
    question: str,
    answer: str,
    tags: str = "",
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin)
):
    await KnowledgeService.learn_from_response(db, question, answer, tags)
    return Result.success(None)
