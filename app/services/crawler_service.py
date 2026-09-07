from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timezone

from app.crawlers.boss import BossCrawler
from app.crawlers.cleaner import deduplicate_jobs, is_senior_job, is_invalid_job, is_high_salary, extract_skills
from app.models.crawl_task import CrawlTask
from app.models.job import Job
from app.config import settings


async def save_job(db: AsyncSession, job_data: dict):
    existing = await db.execute(select(Job).where(Job.job_key == job_data["job_key"]))
    job = existing.scalar_one_or_none()
    if job:
        job.job_status = "ACTIVE"
        job.last_seen_at = datetime.now(timezone.utc)
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
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(job)
    return True


async def start_crawl(db: AsyncSession, keyword: str, city: str, platforms: list) -> int:
    task = CrawlTask(
        source_site=",".join(platforms),
        keyword=keyword,
        city=city,
        status="RUNNING"
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    total_saved = 0
    all_job_keys = []
    try:
        for platform in platforms:
            if platform == "boss":
                crawler = BossCrawler()
                jobs = await crawler.crawl(keyword, city)
                jobs = deduplicate_jobs(jobs)

                count = 0
                for job_data in jobs:
                    title = job_data.get("title", "")
                    experience = job_data.get("experience", "")
                    description = job_data.get("description", "")
                    min_salary = job_data.get("min_salary", 0)

                    if is_senior_job(title, experience):
                        continue
                    if is_invalid_job(title, description):
                        continue
                    if is_high_salary(min_salary):
                        continue

                    if not job_data.get("skills"):
                        job_data["skills"] = extract_skills(title, description)

                    all_job_keys.append(job_data["job_key"])

                    if await save_job(db, job_data):
                        count += 1
                        if count % 50 == 0:
                            await db.commit()

                await db.commit()
                total_saved += count

        if all_job_keys:
            # 仅把「本次爬取平台 + 城市」范围内的岗位标记为下架，
            # 避免误伤其他关键词 / 城市 / 平台的岗位。
            offline_where = [
                Job.job_key.notin_(all_job_keys),
                Job.source_site.in_(platforms),
                Job.job_status.in_(["NEW", "ACTIVE"])
            ]
            if city:
                offline_where.append(Job.city == city)
            stmt = (
                update(Job)
                .where(*offline_where)
                .values(job_status="OFFLINE")
            )
            await db.execute(stmt)
            await db.commit()

        task.status = "COMPLETED"
        task.job_count = total_saved
        await db.commit()
    except Exception as e:
        task.status = "FAILED"
        task.message = str(e)
        await db.commit()
        raise

    return total_saved


async def get_tasks(db: AsyncSession):
    result = await db.execute(select(CrawlTask).order_by(CrawlTask.created_at.desc()))
    return result.scalars().all()


async def get_task(db: AsyncSession, task_id: int):
    result = await db.execute(select(CrawlTask).where(CrawlTask.id == task_id))
    return result.scalar_one_or_none()