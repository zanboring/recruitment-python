from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import require_admin
from app.exceptions import AppException
from app.models.user import User
from app.models.crawl_task import CrawlTask
from app.services.crawler_service import (
    SUPPORTED_PLATFORMS,
    start_crawl_task,
    get_tasks,
    get_task,
)
from app.schemas.common import Result
from app.utils.log_decorator import log_action

router = APIRouter(prefix="/api/crawler", tags=["爬虫"])


class CrawlRequest(BaseModel):
    keyword: str
    city: str = ""
    platforms: list[str] = ["boss"]


def _task_to_dict(task: CrawlTask) -> dict:
    return {
        "id": task.id,
        "source_site": task.source_site,
        "keyword": task.keyword,
        "city": task.city,
        "status": task.status,
        "job_count": task.job_count,
        "message": task.message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.post("/start")
@log_action("启动爬取任务")
async def start_crawler(
    request: CrawlRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    """启动爬取任务（同步执行）。

    返回值除 ``count`` 外还带上 ``status`` / ``message`` / ``task_id``：
    平台未实现或城市未收录时任务会失败并写明原因，只回一个 count 会让调用方
    看到「成功 0 条」却查不到为什么。
    """
    task = await start_crawl_task(db, request.keyword, request.city, request.platforms)
    return Result.success({
        "count": task.job_count or 0,
        "task_id": task.id,
        "status": task.status,
        "message": task.message,
    })


@router.get("/options")
async def get_crawl_options(user: User = Depends(require_admin)):
    """暴露实际支持的平台与城市。

    前端目前硬编码了 4 个平台（boss / zhaopin / 51job / liepin），而后端只实现了
    boss —— 选了未实现的平台会得到一个「已完成、0 条」的任务。与其让调用方猜，
    不如把真实支持范围直接暴露出来，前端可据此渲染可选项或给出提示。
    """
    from app.crawlers.city_map import supported_cities

    return Result.success({
        "platforms": sorted(SUPPORTED_PLATFORMS),
        "cities": supported_cities(),
    })


@router.get("/tasks")
async def list_tasks(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    tasks = await get_tasks(db)
    return Result.success([_task_to_dict(task) for task in tasks])


@router.get("/tasks/{task_id}")
async def get_task_detail(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    task = await get_task(db, task_id)
    if not task:
        raise AppException("任务不存在", 404)
    return Result.success(_task_to_dict(task))