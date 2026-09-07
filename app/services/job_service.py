from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, case, delete
from collections import Counter
import hashlib

from app.models.job import Job
from app.schemas.job import JobQueryDTO, JobCreateRequest, JobUpdateRequest, JobResponse

HOT_CITIES = ["北京", "上海", "深圳", "杭州", "广州"]


def _escape_like(s: str) -> str:
    return s.replace("%", "\\%").replace("_", "\\_")


class JobService:
    @staticmethod
    async def create_job(db: AsyncSession, request: JobCreateRequest) -> JobResponse:
        job_key = JobService._generate_job_key(request.source_site, request.title, request.company_name or "")

        stmt = select(Job).where(Job.job_key == job_key)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            raise ValueError("岗位已存在")

        job = Job(
            company_id=request.company_id,
            title=request.title,
            company_name=request.company_name,
            source_site=request.source_site,
            job_key=job_key,
            city=request.city,
            experience=request.experience,
            education=request.education,
            min_salary=request.min_salary,
            max_salary=request.max_salary,
            salary_unit=request.salary_unit,
            skills=request.skills,
            job_desc=request.job_desc,
            url=request.url
        )

        db.add(job)
        await db.commit()
        await db.refresh(job)

        return JobResponse.model_validate(job)

    @staticmethod
    async def update_job(db: AsyncSession, job_id: int, request: JobUpdateRequest) -> JobResponse:
        job = await db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")

        if request.company_id is not None:
            job.company_id = request.company_id
        if request.title is not None:
            job.title = request.title
        if request.company_name is not None:
            job.company_name = request.company_name
        if request.source_site is not None:
            job.source_site = request.source_site
        if request.job_status is not None:
            job.job_status = request.job_status
        if request.city is not None:
            job.city = request.city
        if request.experience is not None:
            job.experience = request.experience
        if request.education is not None:
            job.education = request.education
        if request.min_salary is not None:
            job.min_salary = request.min_salary
        if request.max_salary is not None:
            job.max_salary = request.max_salary
        if request.salary_unit is not None:
            job.salary_unit = request.salary_unit
        if request.skills is not None:
            job.skills = request.skills
        if request.job_desc is not None:
            job.job_desc = request.job_desc
        if request.url is not None:
            job.url = request.url

        await db.commit()
        await db.refresh(job)

        return JobResponse.model_validate(job)

    @staticmethod
    async def delete_job(db: AsyncSession, job_id: int):
        job = await db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")

        await db.delete(job)
        await db.commit()

    @staticmethod
    async def batch_delete_jobs(db: AsyncSession, job_ids: list[int]):
        stmt = delete(Job).where(Job.id.in_(job_ids))
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def get_job(db: AsyncSession, job_id: int) -> JobResponse:
        job = await db.get(Job, job_id)
        if not job:
            raise ValueError("岗位不存在")
        return JobResponse.model_validate(job)

    @staticmethod
    def _build_conditions(dto: JobQueryDTO) -> list:
        conditions = []

        if dto.keyword:
            like = f"%{_escape_like(dto.keyword)}%"
            conditions.append(
                or_(
                    Job.title.like(like),
                    Job.company_name.like(like),
                    Job.skills.like(like),
                    Job.job_desc.like(like)
                )
            )
        if dto.city:
            conditions.append(Job.city == dto.city)
        if dto.company_name:
            conditions.append(Job.company_name.like(f"%{_escape_like(dto.company_name)}%"))
        if dto.source_site:
            conditions.append(Job.source_site == dto.source_site)
        if dto.min_salary is not None:
            conditions.append(Job.max_salary >= dto.min_salary)
        if dto.max_salary is not None:
            conditions.append(Job.min_salary <= dto.max_salary)
        if dto.status:
            conditions.append(Job.job_status == dto.status)

        return conditions

    @staticmethod
    async def query_jobs(db: AsyncSession, dto: JobQueryDTO) -> list[JobResponse]:
        conditions = JobService._build_conditions(dto)

        stmt = select(Job).where(and_(*conditions))

        stmt = stmt.order_by(
            case(((Job.min_salary.isnot(None), 0)), else_=1),
            case(((Job.skills.isnot(None) & (Job.skills != ""), 0)), else_=1),
            case(((Job.city.in_(HOT_CITIES), 0)), else_=1),
            Job.created_at.desc()
        )

        stmt = stmt.offset((dto.page_num - 1) * dto.page_size).limit(dto.page_size)

        result = await db.execute(stmt)
        jobs = result.scalars().all()

        return [JobResponse.model_validate(job) for job in jobs]

    @staticmethod
    async def count_jobs(db: AsyncSession, dto: JobQueryDTO) -> int:
        conditions = JobService._build_conditions(dto)

        stmt = select(func.count(Job.id)).where(and_(*conditions))
        result = await db.execute(stmt)
        return result.scalar()

    @staticmethod
    async def stat_by_city(db: AsyncSession) -> list[dict]:
        stmt = select(Job.city, func.count(Job.id).label("count")).where(
            Job.city.isnot(None)
        ).group_by(Job.city).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_company(db: AsyncSession) -> list[dict]:
        stmt = select(Job.company_name, func.count(Job.id).label("count")).where(
            Job.company_name.isnot(None)
        ).group_by(Job.company_name).order_by(func.count(Job.id).desc()).limit(20)
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_skill(db: AsyncSession) -> list[dict]:
        result = await db.execute(select(Job.skills).where(Job.skills.isnot(None)))
        rows = result.scalars().all()

        skill_count = Counter()
        for skills_str in rows:
            if skills_str:
                for skill in skills_str.split(","):
                    skill = skill.strip()
                    if skill:
                        skill_count[skill] += 1

        return [{"name": k, "count": v} for k, v in skill_count.most_common(20)]

    @staticmethod
    async def stat_by_salary_range(db: AsyncSession) -> list[dict]:
        ranges = [
            (0, 5000),
            (5000, 10000),
            (10000, 15000),
            (15000, 20000),
            (20000, 30000),
            (30000, 50000),
            (50000, float("inf"))
        ]
        labels = ["0-5k", "5k-10k", "10k-15k", "15k-20k", "20k-30k", "30k-50k", "50k+"]

        result = await db.execute(
            select(Job.min_salary, Job.max_salary).where(
                Job.min_salary.isnot(None)
            )
        )
        rows = result.all()

        counts = [0] * len(ranges)
        for min_sal, max_sal in rows:
            avg_sal = (min_sal + max_sal) / 2 if max_sal else min_sal
            for i, (low, high) in enumerate(ranges):
                if low <= avg_sal < high:
                    counts[i] += 1
                    break

        return [{"name": labels[i], "count": counts[i]} for i in range(len(labels))]

    @staticmethod
    async def stat_by_education(db: AsyncSession) -> list[dict]:
        stmt = select(Job.education, func.count(Job.id).label("count")).where(
            Job.education.isnot(None)
        ).group_by(Job.education).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_experience(db: AsyncSession) -> list[dict]:
        stmt = select(Job.experience, func.count(Job.id).label("count")).where(
            Job.experience.isnot(None)
        ).group_by(Job.experience).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_by_status(db: AsyncSession) -> list[dict]:
        stmt = select(Job.job_status, func.count(Job.id).label("count")).group_by(
            Job.job_status
        ).order_by(func.count(Job.id).desc())
        result = await db.execute(stmt)
        rows = result.all()
        return [{"name": row[0], "count": row[1]} for row in rows]

    @staticmethod
    async def stat_summary(db: AsyncSession) -> dict:
        total = await db.execute(select(func.count(Job.id)))
        active = await db.execute(select(func.count(Job.id)).where(Job.job_status == "ACTIVE"))
        avg_sal = await db.execute(
            select(func.avg((Job.min_salary + Job.max_salary) / 2)).where(
                Job.min_salary.isnot(None)
            )
        )

        from datetime import datetime, timezone
        return {
            "total": total.scalar() or 0,
            "active": active.scalar() or 0,
            "avg_salary": round(avg_sal.scalar() or 0, 2),
            "update_time": datetime.now(timezone.utc).strftime("%Y-%m-%d")
        }

    @staticmethod
    def _generate_job_key(platform: str, title: str, company: str) -> str:
        raw = f"{platform}{title}{company}"
        return f"{platform}_{hashlib.sha256(raw.encode()).hexdigest()}"


    @staticmethod
    async def collect_analysis_stats(db: AsyncSession) -> dict:
        """聚合多维度统计数据，供 AI 分析报告使用。"""
        summary = await JobService.stat_summary(db)
        city = await JobService.stat_by_city(db)
        salary_range = await JobService.stat_by_salary_range(db)
        skill = await JobService.stat_by_skill(db)
        education = await JobService.stat_by_education(db)
        experience = await JobService.stat_by_experience(db)
        return {
            "summary": summary,
            "city": city[:10],
            "salary_range": salary_range,
            "skill": skill[:10],
            "education": education,
            "experience": experience,
        }
