"""爬虫任务编排：抓取 → 清洗过滤 → 入库 → 下架判定。

**这是岗位数据入库的唯一入口**：HTTP 接口（POST /api/crawler/start）与
APScheduler 定时任务都调用 start_crawl。

历史问题：定时任务曾在 scheduler.py 里自己写了一套入库逻辑，缺少清洗过滤、
下架判定、last_seen_at 刷新与重试包装，导致同一个岗位经「手动触发」和
「定时任务」两条路径得到不同结果（甚至绕过高级岗位/无效岗位过滤）。
现在统一收敛到这里，scheduler 只负责按计划调用。
"""
import logging
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawlers.boss import BossCrawler
from app.crawlers.cleaner import (
    deduplicate_jobs,
    extract_skills,
    is_high_salary,
    is_invalid_job,
    is_senior_job,
)
from app.models.crawl_task import CrawlTask
from app.models.job import Job

logger = logging.getLogger(__name__)

# 岗位被判定下架前允许的「未再出现」宽限期（秒）。
OFFLINE_GRACE_SECONDS = 6 * 3600

# 已实现的抓取平台。未实现的平台会被请求前置校验拦下并给出明确原因。
SUPPORTED_PLATFORMS = {"boss"}

# ---- 平台元数据（标识 → 中文名 / 是否已实现）----
#
# 这份映射放在**后端**而不是前端，因为它是业务知识：哪个平台叫什么名字、
# 是否真的有采集实现。前端各存一份必然漂移 —— 事实上此前前端硬编码了 4 个
# 平台而后端只实现了 1 个，用户选中「智联招聘」后会得到一个必然失败的任务，
# 且失败原因界面不展示，只看到一个红色「失败」。
#
# 对外暴露见 ``platform_options()``，前端据此渲染下拉框。
PLATFORM_META = {
    "boss": {"label": "BOSS直聘"},
    "zhaopin": {"label": "智联招聘"},
    "51job": {"label": "前程无忧"},
    "liepin": {"label": "猎聘"},
}

# 平台标识别名：容错归一化用（含中文名，便于直接调接口的调用方）。
PLATFORM_ALIASES = {
    "boss": "boss",
    "zhipin": "boss",
    "boss直聘": "boss",
    "直聘": "boss",
    "zhaopin": "zhaopin",
    "智联": "zhaopin",
    "智联招聘": "zhaopin",
    "51job": "51job",
    "前程无忧": "51job",
    "liepin": "liepin",
    "猎聘": "liepin",
}


def normalize_platform(raw) -> str:
    """把外部传入的平台标识归一化到内部标识。

    未知值**原样返回**，交给请求前置校验拦截并给出明确原因；
    绝不回退到某个已实现平台 —— 回退会让「选智联招聘」静默变成「爬 BOSS」，
    数据被记在错误的平台名下，比直接报错难排查得多（与城市编码同一类问题）。
    """
    value = str(raw or "").strip().lower()
    if not value:
        return "boss"
    return PLATFORM_ALIASES.get(value, value)


def normalize_platforms(platforms) -> list:
    """批量归一化平台标识并去重（保持输入顺序）。

    归一化必须发生在**服务层入口**，而不是只挂在 HTTP 兼容层上。此前它只写在
    compat 层的 ``_normalize_platform`` 里，于是同一个输入会得到两种结果：
    走兼容接口传「BOSS直聘」能正确识别，走原生接口或直接调 service 却会被判成
    「平台未实现」而任务失败 —— 同一规则两处生效范围不同，是这类缺陷的典型成因。
    """
    result: list = []
    for raw in platforms or []:
        # 必须先排除 None：str(None) 会得到 "None"，进一步 lower 成 "none"，
        # 一个「空的平台项」就变成了一个叫 "none" 的平台，最后以
        # 「平台未实现：none」的莫名其妙理由失败。
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        key = normalize_platform(value)
        if key not in result:
            result.append(key)
    return result


def platform_label(raw) -> str:
    """平台标识 → 可展示的中文名（未知标识原样返回）。

    支持逗号分隔的多平台（任务可能同时指定多个平台）。中文名由后端提供，
    前端就不必再维护一份「标识 → 名称」映射 —— 那种映射一旦漂移，
    界面上就会出现用户看不懂的英文标识。
    """
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return str(raw or "")
    labels = []
    for part in parts:
        key = normalize_platform(part)
        meta = PLATFORM_META.get(key)
        labels.append(meta["label"] if meta else key)
    return "、".join(labels)


def platform_options() -> list:
    """平台选项列表（供接口暴露，含中文名与是否已实现）。

    已实现的排在前面 —— 前端可据此把未实现的置灰，用户就不会选到
    注定失败的可选项。
    """
    keys = sorted(
        set(PLATFORM_META) | SUPPORTED_PLATFORMS,
        key=lambda k: (k not in SUPPORTED_PLATFORMS, k),
    )
    return [
        {
            "value": key,
            "label": PLATFORM_META.get(key, {}).get("label", key),
            "implemented": key in SUPPORTED_PLATFORMS,
        }
        for key in keys
    ]


def _utc_now():
    """naive UTC 当前时间，统一入口见 app.utils.timeutil.utc_now。"""
    from app.utils.timeutil import utc_now
    return utc_now()


async def save_job(db: AsyncSession, job_data: dict) -> bool:
    """保存单个岗位。已存在则只刷新状态与 last_seen_at，返回 False。"""
    existing = await db.execute(select(Job).where(Job.job_key == job_data["job_key"]))
    job = existing.scalar_one_or_none()
    if job:
        job.job_status = "ACTIVE"
        job.last_seen_at = _utc_now()
        return False

    job = Job(
        title=job_data["title"],
        company_name=job_data["company_name"],
        source_site=job_data["source_site"],
        job_key=job_data["job_key"],
        job_status="NEW",
        city=job_data["city"],
        experience=job_data["experience"],
        education=job_data["education"],
        min_salary=job_data["min_salary"],
        max_salary=job_data["max_salary"],
        skills=job_data["skills"],
        job_desc=job_data["description"],
        last_seen_at=_utc_now(),
    )
    db.add(job)
    return True


async def _crawl_platform(db: AsyncSession, keyword: str, city: str, platform: str) -> tuple:
    """抓取并入库单个平台。

    返回 (新增条数, 本次抓到的 job_key 列表)。
    """
    crawler = BossCrawler()
    # 走带指数退避的 retry 包装，而不是裸调用 crawl()：
    # 否则目标站点一次抖动就会让整个任务 FAILED。
    jobs = await crawler.crawl_with_retry(keyword, city)
    jobs = deduplicate_jobs(jobs)

    saved = 0
    seen_keys: list = []

    for job_data in jobs:
        title = job_data.get("title", "")
        experience = job_data.get("experience", "")
        description = job_data.get("description", "")
        min_salary = job_data.get("min_salary", 0)

        # 三道清洗规则：高级岗位 / 无效岗位（培训、外包、博彩等）/ 薪资异常
        if is_senior_job(title, experience):
            continue
        if is_invalid_job(title, description):
            continue
        if is_high_salary(min_salary):
            continue

        if not job_data.get("skills"):
            job_data["skills"] = extract_skills(title, description)

        seen_keys.append(job_data["job_key"])

        if await save_job(db, job_data):
            saved += 1
            if saved % 50 == 0:
                await db.commit()

    await db.commit()
    return saved, seen_keys


async def _mark_offline(
    db: AsyncSession,
    seen_keys: list,
    platforms: list,
    city: str,
    crawl_started_at,
) -> None:
    """把「本次确实爬过、但已长期未再出现」的岗位置为 OFFLINE。

    两个保护条件：
    1) 只处理本次实际爬取的平台，避免伤及无关数据；
    2) 额外要求 last_seen_at 早于「本次任务开始时间 - 宽限期」。
       原因是 Job 表没有记录关键词，一次「Java/北京」的爬取无法证明
       「Python/北京」的岗位已下架；用宽限期保证最近被其它关键词任务
       刷新过的岗位不会被误判，只有确实长期没再出现的才置为 OFFLINE。
    """
    grace_cutoff = crawl_started_at - timedelta(seconds=OFFLINE_GRACE_SECONDS)
    conditions = [
        Job.job_key.notin_(seen_keys),
        Job.source_site.in_(platforms),
        Job.job_status.in_(["NEW", "ACTIVE"]),
        or_(Job.last_seen_at.is_(None), Job.last_seen_at < grace_cutoff),
    ]
    if city:
        conditions.append(Job.city == city)

    await db.execute(update(Job).where(*conditions).values(job_status="OFFLINE"))
    await db.commit()


async def create_pending_task(db: AsyncSession, keyword: str, city: str, platforms: list) -> CrawlTask:
    """仅创建一条待执行的任务记录，不执行爬取。

    供「先建任务、后台异步执行」的调用方使用 —— 真实爬取要翻多页、
    每页间隔 12~18 秒，同步执行必然撑爆 HTTP 超时。

    平台标识在这里就归一化后入库：任务列表展示、下架判定、日志排查都读这个字段，
    存入未归一化的值（如「BOSS直聘」）会让同一平台在不同任务里长得不一样。
    """
    platforms = normalize_platforms(platforms)
    task = CrawlTask(
        source_site=",".join(platforms),
        keyword=keyword,
        city=city,
        status="PENDING",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def run_crawl(
    db: AsyncSession,
    task: CrawlTask,
    keyword: str,
    city: str,
    platforms: list,
) -> int:
    """在给定任务记录上执行爬取，返回本次新增岗位数。

    状态流转：RUNNING → COMPLETED / FAILED（失败写入 message 并向上抛）。
    可由请求线程同步调用，也可由后台任务 / 定时任务复用。
    """
    task.status = "RUNNING"
    await db.commit()

    # ---- 前置校验：把「必然失败」的请求拦在爬取之前 ----
    # 这些失败重试不会变好，却要白等 4 次指数退避（2+4+8 秒），
    # 并且原实现会把任务标成 COMPLETED / 0 条且不带任何说明，无从排查。
    # 同样归一化：run_crawl 是执行层的唯一入口，不能假设调用方已经处理过
    # （定时任务、后台重跑、直接调用都会经过这里）
    requested = normalize_platforms(platforms)
    runnable = [p for p in requested if p in SUPPORTED_PLATFORMS]
    skipped = [p for p in requested if p not in SUPPORTED_PLATFORMS]
    skipped_notes: list = []
    if skipped:
        skipped_notes.append(
            f"已跳过未实现平台：{', '.join(skipped)}"
            f"（当前已实现：{', '.join(sorted(SUPPORTED_PLATFORMS))}）"
        )

    if not runnable:
        task.status = "FAILED"
        task.message = (
            f"请求的平台均未实现：{', '.join(skipped) or '（未指定平台）'}。"
            f"当前已实现：{', '.join(sorted(SUPPORTED_PLATFORMS))}"
        )
        await db.commit()
        logger.warning("任务 %s 未执行：%s", task.id, task.message)
        return 0

    # 城市校验同理：未收录城市会抓到别的城市的数据，必须提前拦住
    from app.crawlers.city_map import UnsupportedCityError, require_city_code

    try:
        require_city_code(city)
    except UnsupportedCityError as e:
        task.status = "FAILED"
        task.message = str(e)
        await db.commit()
        logger.warning("任务 %s 未执行：%s", task.id, e)
        return 0

    crawl_started_at = _utc_now()
    try:
        total_saved = 0
        seen_keys: list = []
        crawled_platforms: list = []

        for platform in platforms:
            if platform not in SUPPORTED_PLATFORMS:
                logger.warning("平台 %s 尚未实现，跳过", platform)
                continue
            saved, keys = await _crawl_platform(db, keyword, city, platform)
            total_saved += saved
            seen_keys.extend(keys)
            crawled_platforms.append(platform)

        # 本次一条岗位都没抓到（例如全部被清洗规则过滤）时不触发下架判定，
        # 否则会因「本次结果为空」而把存量岗位整批误判下架。
        if seen_keys and crawled_platforms:
            await _mark_offline(db, seen_keys, crawled_platforms, city, crawl_started_at)

        task.status = "COMPLETED"
        task.job_count = total_saved
        # 被跳过的平台必须写进 message：否则前端只会看到「已完成 / 0 条」，
        # 完全不知道是因为平台没实现（原实现就是这样静默吞掉的）。
        task.message = ("；".join(skipped_notes) or None) if skipped_notes else None
        await db.commit()
        return total_saved
    except Exception as e:
        task.status = "FAILED"
        task.message = str(e)
        await db.commit()
        raise


async def start_crawl_task(db: AsyncSession, keyword: str, city: str, platforms: list) -> CrawlTask:
    """创建任务并**同步**执行，返回**任务对象**。

    与 ``start_crawl`` 的区别只在于返回值：HTTP 接口需要把失败原因（例如
    「平台未实现」「城市未收录」）一并回给调用方，只返回一个 count 会让用户
    看到「成功 / 0 条」而完全不知道原因。
    """
    task = await create_pending_task(db, keyword, city, platforms)
    await run_crawl(db, task, keyword, city, platforms)
    return task


async def start_crawl(db: AsyncSession, keyword: str, city: str, platforms: list) -> int:
    """创建任务并**同步**执行，返回本次新增岗位数。

    原生 HTTP 接口（POST /api/crawler/start）与定时任务走这里：
    调用方需要「返回时数据已入库」。批量导入场景请改用 create_pending_task + 后台执行。
    """
    task = await start_crawl_task(db, keyword, city, platforms)
    return task.job_count or 0


async def get_tasks(db: AsyncSession):
    result = await db.execute(select(CrawlTask).order_by(CrawlTask.created_at.desc()))
    return result.scalars().all()


async def get_task(db: AsyncSession, task_id: int):
    result = await db.execute(select(CrawlTask).where(CrawlTask.id == task_id))
    return result.scalar_one_or_none()
