from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.models.job import Job
from app.recommender.jaccard import skill_similarity


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


EDUCATION_LEVELS = {
    "博士": 5,
    "硕士": 4,
    "本科": 3,
    "大专": 2,
    "高中": 1,
    "不限": 0,
    "": 0,
}

EXPERIENCE_YEARS_MAP = {
    "10年以上": 10,
    "5-10年": 7,
    "3-5年": 4,
    "1-3年": 2,
    "1年以下": 1,
    "应届": 0,
    "": 0,
}


def get_education_level(education: str) -> int:
    for key in EDUCATION_LEVELS:
        if key in education:
            return EDUCATION_LEVELS[key]
    return 0


def get_experience_years(experience: str) -> int:
    for key in EXPERIENCE_YEARS_MAP:
        if key in experience:
            return EXPERIENCE_YEARS_MAP[key]
    return 0


def years_to_experience_string(years: int) -> str:
    """把年限映射成岗位经验区间的**展示文案**。

    注意：仅用于展示。计算匹配度请用 experience_match_by_years —— 因为
    「3 年 → "3-5年" → 解析回 4 年」的往返不幂等，用它做计算会高估用户年限。
    """
    if years >= 10:
        return "10年以上"
    elif years >= 5:
        return "5-10年"
    elif years >= 3:
        return "3-5年"
    elif years >= 1:
        return "1-3年"
    elif years == 0:
        return "应届"
    return ""


def education_match(user_edu: str, job_edu: str) -> float:
    user_level = get_education_level(user_edu)
    job_level = get_education_level(job_edu)
    if user_level >= job_level:
        return 1.0
    elif job_level == 0:
        return 1.0
    else:
        return user_level / job_level if job_level > 0 else 0.0


def experience_match(user_exp: str, job_exp: str) -> float:
    user_years = get_experience_years(user_exp)
    job_years = get_experience_years(job_exp)
    if user_years >= job_years:
        return 1.0
    elif job_years == 0:
        return 1.0
    else:
        return user_years / job_years if job_years > 0 else 0.0


def experience_match_by_years(user_years: int, job_exp: str) -> float:
    """用「用户实际年限」与岗位经验要求计算匹配度。

    刻意不经过 years_to_experience_string：
    该函数把 3 年映射为区间字符串 "3-5年"，再用 get_experience_years 解析回来
    得到 4 年，int → str → int 往返不幂等，用户年限被系统性高估
    （3 年多算 1 年、5 年多算 2 年），经验匹配分随之虚高。

    参数：
        user_years 用户实际工作年限
        job_exp    岗位经验要求原文，如 "3-5年"、"经验不限"
    """
    job_years = get_experience_years(job_exp)
    if job_years <= 0:
        # 岗位不限经验（含 "不限"、"应届" 与空值）
        return 1.0
    if user_years >= job_years:
        return 1.0
    return user_years / job_years


async def recommend_jobs(
    db: AsyncSession,
    skills: str = "",
    education: str = "",
    experience_years: int = 0,
    city: str = "",
    limit: int = 10
) -> list:
    MAX_CANDIDATES = 500
    stmt = select(Job).where(Job.job_status == "ACTIVE")

    if city:
        stmt = stmt.where(Job.city == city)

    if skills:
        skill_keywords = [s.strip() for s in skills.split(",") if s.strip()]
        if skill_keywords:
            or_conditions = []
            for keyword in skill_keywords:
                escaped = _escape_like(keyword)
                or_conditions.append(Job.skills.like(f"%{escaped}%", escape="\\"))
                or_conditions.append(Job.title.like(f"%{escaped}%", escape="\\"))
            stmt = stmt.where(or_(*or_conditions))

    stmt = stmt.limit(MAX_CANDIDATES)

    result = await db.execute(stmt)
    jobs = result.scalars().all()

    recommendations = []
    for job in jobs:
        skill_sim = skill_similarity(skills, job.skills or "")

        edu_match = education_match(education, job.education or "")
        exp_match = experience_match_by_years(experience_years, job.experience or "")

        score = skill_sim * 0.7 + edu_match * 0.2 + exp_match * 0.1

        recommendations.append({
            "job_id": job.id,
            "title": job.title,
            "company_name": job.company_name,
            "city": job.city,
            "min_salary": float(job.min_salary) if job.min_salary else None,
            "max_salary": float(job.max_salary) if job.max_salary else None,
            "skills": job.skills,
            "education": job.education,
            "experience": job.experience,
            "score": round(score, 4),
            "skill_similarity": round(skill_sim, 4),
            "education_match": round(edu_match, 4),
            "experience_match": round(exp_match, 4)
        })

    recommendations.sort(key=lambda x: x["score"], reverse=True)
    return recommendations[:limit]