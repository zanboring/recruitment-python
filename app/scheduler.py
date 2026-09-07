from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

scheduler = AsyncIOScheduler(
    timezone="Asia/Shanghai",
    job_defaults={"coalesce": True, "max_instances": 1}
)

import logging

logger = logging.getLogger("scheduler")


def start_scheduler():
    from sqlalchemy import select as sa_select
    from app.crawlers.boss import BossCrawler
    from app.crawlers.cleaner import deduplicate_jobs, clean_job_data
    from app.database import async_session
    from app.models.job import Job
    from app.models.crawl_task import CrawlTask

    async def scheduled_crawl():
        keywords = ["Java", "Python", "前端"]
        cities = ["北京", "上海", "广州", "深圳", "杭州", "成都"]

        async with async_session() as db:
            for keyword in keywords:
                for city in cities:
                    task = None
                    try:
                        task = CrawlTask(
                            source_site="boss",
                            keyword=keyword,
                            city=city,
                            status="RUNNING"
                        )
                        db.add(task)
                        await db.commit()
                        await db.refresh(task)

                        crawler = BossCrawler()
                        jobs = await crawler.crawl(keyword, city)
                        jobs = deduplicate_jobs(jobs)

                        count = 0
                        for job_data in jobs:
                            cleaned = clean_job_data(job_data)
                            existing = await db.execute(
                                sa_select(Job).where(Job.job_key == cleaned["job_key"])
                            )
                            if not existing.scalar_one_or_none():
                                job = Job(
                                    title=cleaned["title"],
                                    company_name=cleaned["company_name"],
                                    source_site=cleaned["source_site"],
                                    job_key=cleaned["job_key"],
                                    job_status="ACTIVE",
                                    city=cleaned["city"],
                                    experience=cleaned["experience"],
                                    education=cleaned["education"],
                                    min_salary=cleaned["min_salary"],
                                    max_salary=cleaned["max_salary"],
                                    skills=cleaned["skills"],
                                    job_desc=cleaned["description"],
                                )
                                db.add(job)
                                count += 1
                                if count % 50 == 0:
                                    await db.commit()

                        await db.commit()

                        task.status = "COMPLETED"
                        task.job_count = count
                        await db.commit()
                    except Exception as e:
                        if task:
                            task.status = "FAILED"
                            task.message = str(e)
                            await db.commit()
                        logger.error(f"Scheduled crawl failed for {keyword} in {city}: {e}", exc_info=True)

    if not scheduler.get_job("scheduled_crawl"):
        scheduler.add_job(scheduled_crawl, "cron", hour=2, minute=0, id="scheduled_crawl")
    scheduler.start()