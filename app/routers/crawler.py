from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.config import settings
from app.crawlers.registry import SUPPORTED_PLATFORMS
from app.database import get_db
from app.dependencies import require_admin
from app.exceptions import AppException
from app.models.user import User
from app.models.crawl_task import CrawlTask
from app.services.crawler_service import (
    ingest_browser_jobs,
    platform_label,
    platform_options,
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
        # 中文名由后端给出，与兼容层保持同一口径
        "source_site_label": platform_label(task.source_site),
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
    """暴露实际支持的平台与实际收录的城市。

    前端此前硬编码了 4 个平台（boss / zhaopin / 51job / liepin），而后端只实现了
    boss —— 选了未实现的平台会得到一个「已完成、0 条」的任务；城市同理（前端
    11 个、后端 20 个）。把真实支持范围暴露出来，调用方才能渲染出正确的可选项。

    ``platforms`` 为结构化列表（``value`` / ``label`` / ``implemented``），
    与兼容层 ``GET /api/crawl/options`` 保持同一形状 —— 两个入口返回不同结构时，
    前端要为同一个概念写两套解析。
    """
    from app.crawlers.city_map import supported_cities

    return Result.success({
        "platforms": platform_options(),
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

class BrowserCollectRequest(BaseModel):
    """浏览器端（油猴脚本 / 扩展）提交的岗位批次。

    字段刻意做得很薄：脚本只从页面 DOM 取原始文本，**不计算 job_key、
    不解析薪资、不做过滤** —— 那些口径留在服务端，否则两个采集入口必然漂移。
    """
    source_site: str = "boss"
    city: str = ""
    keyword: str = ""
    jobs: list[dict] = []


@router.post("/ingest")
async def ingest_from_browser(
    request: BrowserCollectRequest,
    x_collect_token: str = Header("", alias="X-Collect-Token"),
    db: AsyncSession = Depends(get_db),
):
    """接收浏览器端采集的岗位。

    与其它接口不同，这里**不用 JWT**：油猴脚本跑在招聘网站上，没法让用户
    去登录本系统拿 token。改用专门的采集令牌（``BROWSER_COLLECT_TOKEN``）。

    为什么是「自定义请求头 + 令牌」而不用查询参数：
    自定义头会让跨源请求先走 CORS 预检，而普通网页拿不到预检许可，
    于是**网页根本发不出这个请求**；油猴脚本用 ``GM_xmlhttpRequest``
    不受同源策略限制，照常能发。等于借浏览器自己的同源策略，
    顺手挡掉了「任意网站偷偷往 localhost:8080 灌数据」。
    """
    import hmac
    import logging

    logger = logging.getLogger(__name__)
    expected = str(settings.browser_collect_token or "").strip()
    if not expected:
        # 未配置令牌即视为关闭该入口 —— 默认安全，不给出「能写但没有鉴权」的窗口
        raise AppException(
            "浏览器采集入口未开启：请在配置中设置 BROWSER_COLLECT_TOKEN"
            "（生成方式：python -c \"import secrets;print(secrets.token_urlsafe(24))\"）",
            403,
        )
    if not hmac.compare_digest(str(x_collect_token or ""), expected):
        raise AppException("采集令牌不正确", 403)

    source_site = (request.source_site or "").strip()
    if source_site not in SUPPORTED_PLATFORMS:
        raise AppException(
            f"未知平台：{source_site}。已登记：{', '.join(sorted(SUPPORTED_PLATFORMS))}。"
            f"请沿用与爬虫相同的平台标识，不要另造新名 —— job_key 含平台名，"
            f"换名会让同一个岗位在库里出现两行。",
            400,
        )

    jobs = request.jobs or []
    limit = settings.browser_collect_max_batch
    if limit > 0 and len(jobs) > limit:
        raise AppException(f"单次提交不得超过 {limit} 条（收到 {len(jobs)} 条）", 400)

    result = await ingest_browser_jobs(db, jobs, source_site, request.city)
    logger.info(
        "浏览器采集入库：平台=%s 城市=%s 关键词=%s 收到=%s 新增=%s 重复=%s 过滤=%s 无效=%s",
        source_site, request.city, request.keyword,
        result["received"], result["saved"], result["duplicate"],
        result["filtered"], result["invalid"],
    )
    return Result.success(result)
