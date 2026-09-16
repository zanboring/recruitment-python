# -*- coding: utf-8 -*-
"""阶段：技能画像匹配服务（后端核心）。

设计思路：用户输入「我掌握的技能」，系统：
1. 对全部在架岗位逐条计算技能覆盖率（岗位要求 ∩ 我的技能）/ 岗位要求，
   找出「我能覆盖到位的岗位」与「我缺哪些技能」；
2. 补充岗位薪资、学历、经验维度，输出与 recommend_jobs 一致的排序口径；
3. 聚合全市场 Top 需求技能 + 用户缺口技能排行，回答「我该学什么」。

与 recommend_jobs 的区别：recommend_jobs 是「按我给的技能去 LIKE 招人」，
本质是过滤；技能画像要做的是「拿我的技能去逐岗比对覆盖率 + 找出盲区」，
本质是知己知彼 —— 这才是求职决策需要的信号。
"""
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job

# 常见技能别名归一化：JD 里的写法五花八门，统一后覆盖率统计才不会漏
SKILL_ALIASES = {
    "java开发": "java", "java工程师": "java", "j2ee": "java",
    "springboot": "spring boot", "springcloud": "spring cloud",
    "vuejs": "vue", "vue.js": "vue", "vue3": "vue",
    "reactjs": "react", "react.js": "react",
    "python开发": "python",
    "js": "javascript", "nodejs": "node.js", "node": "node.js",
    "ts": "typescript",
    "ml": "机器学习", "dl": "深度学习", "ai": "人工智能",
    "llms": "llm", "大模型": "llm",
    "fastapi": "fastapi", "django": "django", "flask": "flask",
    "mysql": "mysql", "postgresql": "postgresql", "postgres": "postgresql",
    "redis": "redis", "k8s": "kubernetes", "kubectl": "kubernetes",
    "docker": "docker", "git": "git", "linux": "linux",
    "echarts": "echarts", "html5": "html", "css3": "css",
    "rpa": "rpa", "playwright": "playwright", "selenium": "selenium",
    "pytest": "pytest", "websocket": "websocket", "kafka": "kafka",
}


def normalize_skill(s: str) -> str:
    """技能别名归一化（小写去空格 + 别名映射）。"""
    key = s.strip().lower().replace(" ", "")
    if key in SKILL_ALIASES:
        return SKILL_ALIASES[key]
    return s.strip().lower()


def parse_skill_list(raw: str) -> list:
    """把逗号/顿号/空格分隔的技能字符串拆成归一化后的技能列表。"""
    import re

    if not raw:
        return []
    parts = re.split(r"[,，、;；/|]+", raw)
    return [normalize_skill(p) for p in parts if p.strip()]


async def build_skill_profile(
    db: AsyncSession,
    my_skills: str,
    city: str = "",
    limit: int = 20,
) -> dict:
    """计算技能画像：输入我的技能，输出匹配岗位 + 覆盖率 + 缺口技能。

    返回结构::

        {
          "my_skills": ["python", "fastapi", ...],   # 我的技能（归一化）
          "summary": {
            "analyzed": 123,        # 参与比对的在架岗位数
            "covered_ratio": 0.42,  # 所有岗位技能的平均覆盖率
            "best_match": {...}      # 覆盖率最高的岗位
          },
          "jobs": [                                  # 按覆盖率降序
            {"job_id", "title", "company_name", "city",
             "min_salary", "max_salary", "skills", "score",
             "coverage", "matched", "missing_top"}
          ],
          "missing_skills": [{"skill", "count"}],   # 我的缺口技能（按需求热度）
          "top_demanded": [{"skill", "count"}],       # 全市场 Top 需求技能
        }
    """
    my_set = set(parse_skill_list(my_skills))
    if not my_set:
        raise ValueError("请至少输入一个技能")

    stmt = select(Job).where(Job.job_status == "ACTIVE")
    if city:
        stmt = stmt.where(Job.city == city)
    rows = (await db.execute(stmt)).scalars().all()

    job_insights = []
    demand_counter = Counter()      # 全市场技能需求热度
    missing_counter = Counter()     # 我的缺口技能（岗位要求但我不具备）

    for job in rows:
        job_skills = parse_skill_list(job.skills or "")
        if not job_skills:
            continue
        for s in job_skills:
            demand_counter[s] += 1
            if s not in my_set:
                missing_counter[s] += 1

        matched = [s for s in job_skills if s in my_set]
        coverage = round(len(matched) / len(job_skills), 4)
        missing_in_job = [s for s in job_skills if s not in my_set]

        job_insights.append({
            "job": job,
            "coverage": coverage,
            "matched": matched,
            "missing_in_job": missing_in_job,
        })

    job_insights.sort(key=lambda x: x["coverage"], reverse=True)

    jobs_out = []
    for item in job_insights[:limit]:
        job = item["job"]
        jobs_out.append({
            "job_id": job.id,
            "title": job.title,
            "company_name": job.company_name,
            "city": job.city,
            "min_salary": float(job.min_salary) if job.min_salary else None,
            "max_salary": float(job.max_salary) if job.max_salary else None,
            "education": job.education,
            "experience": job.experience,
            "skills": job.skills,
            "score": round(item["coverage"] * 100, 1),
            "coverage": item["coverage"],
            "matched": item["matched"],
            "missing_top": item["missing_in_job"][:4],
        })

    analyzed = len(job_insights)
    covered_ratio = (
        round(sum(x["coverage"] for x in job_insights) / analyzed, 4)
        if analyzed else 0.0
    )

    return {
        "my_skills": sorted(my_set),
        "summary": {
            "analyzed": analyzed,
            "covered_ratio": covered_ratio,
        },
        "jobs": jobs_out,
        "missing_skills": [
            {"skill": s, "count": c}
            for s, c in missing_counter.most_common(15)
        ],
        "top_demanded": [
            {"skill": s, "count": c}
            for s, c in demand_counter.most_common(20)
        ],
    }