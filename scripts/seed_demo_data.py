# -*- coding: utf-8 -*-
"""
一键生成种子岗位数据（解决「开箱无数据、爬虫又爬不动」的现实问题）。

用法：
    python scripts/seed_demo_data.py          # 默认生成 200 条
    python scripts/seed_demo_data.py 500      # 自定义条数

设计：
- 幂等：job_key（平台+标题+公司+城市）已存在则跳过，重复执行不会产生重复数据；
- 真实性：按真实岗位画像组合（AI应用 / Python后端 / 前端 / 运维 / 数据），
  城市权重、薪资区间、学历经验要求均贴近真实招聘数据；
- 无外部依赖：纯标准库，与 requirements 无关，克隆即可跑。
"""
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.database import async_session, Base, engine  # noqa: E402
from app.models import Job  # noqa: E402,F401  noqa 注释避免未使用告警
from app.utils.job_key import generate_job_key  # noqa: E402

random.seed(42)  # 固定种子，重复执行生成一致数据（可复现）

CITIES = ["北京", "上海", "深圳", "杭州", "广州", "成都", "武汉", "长沙", "南京", "西安"]

# (标题, [技能], 薪资区间, 经验, 学历) —— 覆盖主流技术方向
JOB_TEMPLATES = [
    ("Java开发工程师", ["Java", "SpringBoot", "MySQL", "Redis", "微服务"], (18, 35), 3, "本科"),
    ("Java后端工程师", ["Java", "Spring", "MyBatis", "MySQL", "Kafka"], (15, 30), 3, "本科"),
    ("Python开发工程师", ["Python", "Django", "MySQL", "Linux"], (15, 30), 3, "本科"),
    ("Python后端工程师", ["Python", "FastAPI", "SQLAlchemy", "Docker", "Redis"], (15, 32), 2, "本科"),
    ("AI应用开发工程师", ["Python", "LLM", "RAG", "FastAPI", "向量数据库"], (20, 40), 3, "本科"),
    ("大模型应用工程师", ["Python", "LLM", "Prompt工程", "RAG", "Agent"], (22, 45), 3, "本科"),
    ("RAG算法工程师", ["Python", "RAG", "向量数据库", "Embedding", "NLP"], (25, 45), 3, "硕士"),
    ("AI Agent开发工程师", ["Python", "LLM", "Function Calling", "FastAPI", "Redis"], (22, 42), 2, "本科"),
    ("机器学习工程师", ["Python", "PyTorch", "机器学习", "深度学习", "Spark"], (25, 45), 3, "硕士"),
    ("算法工程师(NLP)", ["Python", "NLP", "Transformer", "LLM", "PyTorch"], (28, 50), 3, "硕士"),
    ("前端开发工程师", ["Vue", "TypeScript", "JavaScript", "Element Plus", "Vite"], (14, 28), 2, "本科"),
    ("前端工程师(Vue)", ["Vue", "TypeScript", "ECharts", "CSS3", "Webpack"], (13, 26), 2, "本科"),
    ("React前端工程师", ["React", "TypeScript", "JavaScript", "Webpack"], (15, 28), 2, "本科"),
    ("后端开发工程师", ["Java", "SpringBoot", "MySQL", "Redis", "Docker"], (16, 30), 2, "本科"),
    ("全栈开发工程师", ["Vue", "Java", "SpringBoot", "MySQL", "JavaScript"], (15, 30), 3, "本科"),
    ("Python全栈工程师", ["Python", "FastAPI", "Vue", "MySQL", "Docker"], (16, 32), 2, "本科"),
    ("运维工程师", ["Linux", "Docker", "Kubernetes", "CI/CD", "Nginx"], (14, 26), 2, "本科"),
    ("DevOps工程师", ["Docker", "Kubernetes", "CI/CD", "Linux", "Jenkins"], (18, 32), 3, "本科"),
    ("测试开发工程师", ["pytest", "Python", "Selenium", "Postman", "自动化测试"], (13, 24), 2, "本科"),
    ("数据分析师", ["Python", "pandas", "SQL", "Excel", "数据可视化"], (13, 24), 2, "本科"),
    ("RPA开发工程师", ["Python", "RPA", "Playwright", "影刀", "SQL"], (12, 25), 2, "本科"),
    ("爬虫开发工程师", ["Python", "Playwright", "反爬", "MySQL", "JavaScript"], (15, 28), 2, "本科"),
    ("大数据开发工程师", ["Java", "Spark", "Hadoop", "Flink", "Kafka"], (22, 40), 3, "本科"),
    ("嵌入式开发工程师", ["C", "C++", "Linux", "RTOS", "单片机"], (15, 28), 2, "本科"),
]

COMPANIES = [
    "字节跳动", "腾讯", "阿里巴巴", "美团", "百度", "京东", "网易", "小米",
    "华为", "中兴通讯", "OPPO", "vivo", "大疆创新", "商汤科技", "科大讯飞",
    "智谱AI", "百度智能云", "金山办公", "湖南麒麟", "拓维信息", "株洲中车",
    "长沙蜜獾信息", "武汉斗鱼", "武汉光庭", "杭州数梦工场", "成都四方伟业",
]


def generate_jobs(count: int) -> list:
    jobs = []
    used_keys = set()
    for _ in range(count):
        template = random.choice(JOB_TEMPLATES)
        title, skills, salary_range, exp_junior, edu = template
        city = random.choice(CITIES)
        company = random.choice(COMPANIES)

        min_sal = random.randint(salary_range[0], salary_range[1] - 5)
        max_sal = min_sal + random.randint(4, 8)
        experience = random.choice(["1-3年", "3-5年"]) if exp_junior == 2 else random.choice(["3-5年", "5-10年"])

        job_key = generate_job_key("seed", title, company, city)
        if job_key in used_keys:
            continue
        used_keys.add(job_key)

        jobs.append({
            "title": title,
            "company_name": company,
            "city": city,
            "min_salary": min_sal * 1000,
            "max_salary": max_sal * 1000,
            "experience": experience,
            "education": edu,
            "skills": "#".join(skills),
            "source_site": "seed",
            "job_key": job_key,
            "job_desc": f"{title}，负责{'、'.join(skills)}相关研发工作。",
        })
    return jobs


async def seed(count: int) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    jobs = generate_jobs(count)
    inserted = 0
    skipped = 0

    async with async_session() as db:
        for d in jobs:
            exists = (await db.execute(
                select(Job.id).where(Job.job_key == d["job_key"])
            )).scalar_one_or_none()
            if exists:
                skipped += 1
                continue
            db.add(Job(
                title=d["title"],
                company_name=d["company_name"],
                city=d["city"],
                min_salary=d["min_salary"],
                max_salary=d["max_salary"],
                experience=d["experience"],
                education=d["education"],
                skills=d["skills"].replace("#", ","),
                source_site=d["source_site"],
                job_key=d["job_key"],
                job_status="ACTIVE",
                job_desc=d["job_desc"],
                url="",
            ))
            inserted += 1
            if inserted % 50 == 0:
                await db.commit()
        await db.commit()

    print(f"种子数据完成：新增 {inserted} 条，跳过 {skipped} 条（已存在）")
    total = (await db.execute(select(func.count(Job.id)))).scalar()
    print(f"当前岗位总数：{total}")
    await engine.dispose()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    asyncio.run(seed(n))