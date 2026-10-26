# -*- coding: utf-8 -*-
"""技能画像服务测试：覆盖率 / 缺口技能 / 匹配排序 / 别名归一化。"""
import pytest

from app.models.job import Job
from app.services import skill_profile
from app.utils.job_key import generate_job_key


@pytest.fixture
def profile_jobs(db_session):
    """构造覆盖不同技能组合的岗位。"""
    rows = [
        # (title, company, city, salary, skills)
        ("Python后端工程师", "公司A", "长沙", (15000, 25000), "Python,FastAPI,Redis"),
        ("AI应用工程师", "公司B", "长沙", (20000, 35000), "Python,LLM,RAG,FastAPI"),
        ("Java开发工程师", "公司C", "北京", (18000, 30000), "Java,SpringBoot,MySQL"),
        ("全栈工程师", "公司D", "深圳", (15000, 28000), "Python,Vue,MySQL"),
        ("RPA开发工程师", "公司E", "武汉", (12000, 20000), "Python,RPA,Playwright"),
    ]
    for title, company, city, (lo, hi), skills in rows:
        db_session.add(Job(
            title=title,
            company_name=company,
            city=city,
            source_site="seed",
            job_key=generate_job_key("seed", title, company, city),
            job_status="ACTIVE",
            min_salary=lo,
            max_salary=hi,
            experience="1-3年",
            education="本科",
            skills=skills,
            job_desc=title,
        ))
    db_session.commit()


def test_normalize_skill_alias():
    assert skill_profile.normalize_skill("SpringBoot") == "spring boot"
    assert skill_profile.normalize_skill("vue.js") == "vue"
    assert skill_profile.normalize_skill("Python") == "python"
    assert skill_profile.normalize_skill("大模型") == "llm"


def test_parse_skill_list_mixed_separators():
    parts = skill_profile.parse_skill_list("Python, FastAPI、Redis;Vue")
    assert set(parts) == {"python", "fastapi", "redis", "vue"}


@pytest.mark.asyncio
async def test_skill_profile_coverage(db_session, profile_jobs):
    """输入 python+fastapi,应排 Python 相关岗位第一,Java 岗位应几乎不匹配。"""
    result = await skill_profile.build_skill_profile(
        db_session, "Python,FastAPI", limit=10
    )
    assert result["my_skills"] == ["fastapi", "python"]
    assert result["summary"]["analyzed"] == 5

    top = result["jobs"][0]
    # Python+FastAPI 覆盖 Python后端(2/3) 与 AI应用(2/4) 都是最高档
    assert top["coverage"] >= 0.5
    assert "python" in top["matched"]

    # 缺口技能应包含 llm / rag / java 等
    missing = {item["skill"] for item in result["missing_skills"]}
    assert "java" in missing


@pytest.mark.asyncio
async def test_skill_profile_city_filter(db_session, profile_jobs):
    result = await skill_profile.build_skill_profile(
        db_session, "Python", city="长沙", limit=10
    )
    assert all(j["city"] == "长沙" for j in result["jobs"])


@pytest.mark.asyncio
async def test_skill_profile_market_demand(db_session, profile_jobs):
    """top_demanded 应统计全市场技能热度。"""
    result = await skill_profile.build_skill_profile(db_session, "Python", limit=10)
    demand = {item["skill"]: item["count"] for item in result["top_demanded"]}
    # Python 出现 4 次，是最高需求技能
    assert demand.get("python", 0) == 4
    assert demand.get("java", 0) == 1


@pytest.mark.asyncio
async def test_skill_profile_empty_input(db_session, profile_jobs):
    with pytest.raises(ValueError):
        await skill_profile.build_skill_profile(db_session, "  ", limit=10)