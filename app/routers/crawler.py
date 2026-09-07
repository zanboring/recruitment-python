from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import require_admin
from app.exceptions import AppException
from app.models.user import User
from app.models.crawl_task import CrawlTask
from app.services.crawler_service import start_crawl, get_tasks, get_task
from app.schemas.common import Result

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
async def start_crawler(
    request: CrawlRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin)
):
    count = await start_crawl(db, request.keyword, request.city, request.platforms)
    return Result.success({"count": count})


@router.get("/tasks")
async def list_tasks(db: AsyncSession = Depends(get_db)):
    tasks = await get_tasks(db)
    return Result.success([_task_to_dict(task) for task in tasks])


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await get_task(db, task_id)
    if not task:
        raise AppException("任务不存在", 404)
    return Result.success(_task_to_dict(task))