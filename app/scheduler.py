"""定时任务调度：每天凌晨按「关键词 × 城市」批量爬取岗位。

**为什么只保留一个入口**：
定时爬取直接复用 services.crawler_service.start_crawl，与手动触发的
POST /api/crawler/start 走完全相同的链路（重试包装、清洗过滤、去重、
下架判定、任务状态落库）。

历史问题：本文件曾自己维护一套入库逻辑，缺少清洗过滤与下架判定、
也不刷新 last_seen_at，导致同一份数据经不同入口入库结果不一致。
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
    """执行一轮定时爬取。单组失败只记录日志，不影响其余组合。"""
    # 延迟导入：避免应用启动阶段（导入 scheduler 时）就拉起数据库与爬虫依赖
    from app.database import async_session
    from app.services.crawler_service import start_crawl

    async with async_session() as db:
        for keyword in SCHEDULED_KEYWORDS:
            for city in SCHEDULED_CITIES:
                try:
                    count = await start_crawl(db, keyword, city, SCHEDULED_PLATFORMS)
                    logger.info("定时爬取完成：%s / %s，新增 %s 条", keyword, city, count)
                except Exception as e:  # noqa: BLE001
                    # start_crawl 内部已把该任务置为 FAILED，这里记录后继续下一组
                    logger.error(
                        "定时爬取失败：%s / %s：%s", keyword, city, e, exc_info=True
                    )


def start_scheduler() -> None:
    """注册定时任务并启动调度器（幂等，可安全重复调用）。"""
    if not scheduler.get_job("scheduled_crawl"):
        scheduler.add_job(scheduled_crawl, "cron", hour=2, minute=0, id="scheduled_crawl")
    if not scheduler.running:
        scheduler.start()
