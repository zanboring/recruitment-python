"""定时任务调度：每天凌晨按「关键词 × 城市」批量爬取岗位。

**为什么只保留一个入口**：
定时爬取直接复用 services.crawler_service.start_crawl，与手动触发的
POST /api/crawler/start 走完全相同的链路（重试包装、清洗过滤、去重、
下架判定、任务状态落库）。

历史问题：本文件曾自己维护一套入库逻辑，缺少清洗过滤与下架判定、
也不刷新 last_seen_at，导致同一份数据经不同入口入库结果不一致。
"""
import asyncio
import logging
import random

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services import report_service

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler(
    timezone="Asia/Shanghai",
    job_defaults={"coalesce": True, "max_instances": 1},
)

# 定时爬取目标：3 个关键词 × 6 个城市 = 18 组任务
SCHEDULED_KEYWORDS = ["Java", "Python", "前端"]
SCHEDULED_CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都"]
SCHEDULED_PLATFORMS = ["boss"]


async def scheduled_crawl() -> None:
    """执行一轮定时爬取。单组失败只记录日志，不影响其余组合。

    节奏刻意做成「人类化」的：**打乱组合顺序 + 组间插入随机间隔**。

    原先是 3 关键词 × 6 城市 = 18 组背靠背连跑，一个小时内打完一整套 ——
    对目标站点来说这是极其规律的流量形状（每天同一时刻、同一批城市、同样的密度），
    比分散访问更容易被识别为脚本。现在一轮会自然摊开到一两个小时，
    且每天的访问顺序都不同。
    """
    from app.config import settings
    from app.database import async_session
    from app.services.crawler_service import start_crawl

    combos = [(keyword, city) for keyword in SCHEDULED_KEYWORDS for city in SCHEDULED_CITIES]
    # 固定顺序意味着「每天同一时刻访问同一批城市」，规律性本身就是可识别特征
    random.shuffle(combos)

    async with async_session() as db:
        for index, (keyword, city) in enumerate(combos):
            if index:
                gap = random.uniform(
                    settings.scheduled_crawl_gap_min, settings.scheduled_crawl_gap_max
                )
                logger.info("组间间隔 %.0f 秒（第 %s/%s 组）", gap, index + 1, len(combos))
                await asyncio.sleep(gap)
            try:
                count = await start_crawl(db, keyword, city, SCHEDULED_PLATFORMS)
                logger.info("定时爬取完成：%s / %s，新增 %s 条", keyword, city, count)
            except Exception as e:  # noqa: BLE001
                # start_crawl 内部已把该任务置为 FAILED，这里记录后继续下一组
                logger.error(
                    "定时爬取失败：%s / %s：%s", keyword, city, e, exc_info=True
                )


async def scheduled_daily_report() -> None:
    """每日在爬取任务之后自动生成招聘市场日报。

    单次失败只记录日志，不中断调度器；日报表内同日重复执行幂等。
    """
    from app.database import async_session

    async with async_session() as db:
        try:
            report = await report_service.create_daily_report(db, None)
            logger.info("定时日报完成：%s（%s）", report.report_date, report.status)
        except Exception as e:  # noqa: BLE001
            logger.error("定时日报生成失败：%s", e, exc_info=True)


def _clamp(value, low: int, high: int, default: int) -> int:
    """把配置里的时间参数收敛到合法区间。

    配置写错（越界、非数字）时用默认值并在日志里说明，而不是让
    APScheduler 抛异常把整个应用启动带崩 —— 一个「几点跑」的参数
    不该有能力阻止服务起来。
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        logger.warning("定时任务时间配置非法（%r），回退默认值 %s", value, default)
        return default
    if not low <= number <= high:
        logger.warning("定时任务时间配置越界（%s），回退默认值 %s", number, default)
        return default
    return number


def _sync_job(job_id: str, func, enabled: bool, hour: int, minute: int, label: str) -> None:
    """按配置注册/移除一个 cron 任务（幂等）。"""
    existing = scheduler.get_job(job_id)
    if not enabled:
        if existing:
            scheduler.remove_job(job_id)
        logger.info("定时任务「%s」已按配置关闭", label)
        return
    if existing:
        # 已注册时也重建一次：否则改了 .env 里的时间却不重启就永远不生效
        scheduler.remove_job(job_id)
    scheduler.add_job(func, "cron", hour=hour, minute=minute, id=job_id)
    logger.info("定时任务「%s」已注册：%02d:%02d", label, hour, minute)


def start_scheduler() -> None:
    """注册定时任务并启动调度器（幂等，可安全重复调用）。

    触发时间与开关全部来自配置（``app/config.py`` 的 scheduled_* / report_*），
    原先写死为「爬取 02:00、日报 06:30」，换部署环境必须改代码。
    """
    from app.config import settings

    _sync_job(
        "scheduled_crawl", scheduled_crawl,
        enabled=settings.scheduled_crawl_enabled,
        hour=_clamp(settings.scheduled_crawl_hour, 0, 23, 2),
        minute=_clamp(settings.scheduled_crawl_minute, 0, 59, 0),
        label="定时爬取",
    )
    _sync_job(
        "scheduled_daily_report", scheduled_daily_report,
        enabled=settings.report_enabled,
        hour=_clamp(settings.report_hour, 0, 23, 6),
        minute=_clamp(settings.report_minute, 0, 59, 30),
        label="每日日报",
    )

    if not scheduler.running:
        scheduler.start()
