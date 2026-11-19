# -*- coding: utf-8 -*-
"""技能画像服务测试：覆盖率 / 缺口技能 / 匹配排序 / 别名归一化。"""
import pytest
import pytest_asyncio

from app.models.job import Job
from app.services import skill_profile
from app.utils.job_key import generate_job_key


# 必须是 async 夹具且 await commit：原先写成同步 def 里调 db_session.commit()，
# 拿到的是协程对象、**从未被 await**（pytest 报 RuntimeWarning 就是这个）——
# 数据能查到只是因为 SQLAlchemy 的 autoflush 把待提交对象刷进了同一事务，
# 一旦换掉 autoflush 或断言走到独立会话，这些用例就会静默变成"测了个寂寞"。
@pytest_asyncio.fixture
async def profile_jobs(db_session):
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
    await db_session.commit()


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


# ==========================================================================
# 技能证据来源（2026-11-24）
#
# ``skills_sources`` 按标签逐条编码成 ``|title:Java|desc:MySQL|``。
# 验收问题是「哪些标签来自标题」——下面同时测编码与**真实 SQL 查询**，
# 避免只测 Python 解析、SQL 里却查不出来（那这一列就白加了）。
# ==========================================================================


def _recompute_module():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "recompute_skills.py"
    spec = importlib.util.spec_from_file_location("recompute_skills_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_来源编码区分标题与正文():
    script = _recompute_module()
    encoded = script.encode_skill_sources(
        ["Java", "Spring", "MySQL"], "Java开发工程师"
    )
    assert encoded == "|title:Java|desc:Spring|desc:MySQL|", encoded
    assert script.title_labels_from_sources(encoded) == {"Java"}
    assert script.source_of_label(encoded, "Java") == "title"
    assert script.source_of_label(encoded, "MySQL") == "desc"
    assert script.source_of_label(encoded, "Go") == ""


def test_短标签不会被长词误判成标题证据():
    """词表只有 ``Go``（词边界匹配），标题写 Golang 时不该算 Go 的标题证据。

    前后带 ``|`` 的作用另测：查 ``title:Go`` 不能命中 ``title:Golang`` 这类标签。
    """
    script = _recompute_module()
    encoded = script.encode_skill_sources(["Go"], "Golang 工程师")
    assert encoded == "|desc:Go|", encoded
    assert script.source_of_label(encoded, "Go") == "desc"


def test_用SQL精确查出标题级标签(tmp_path):
    import sqlite3

    script = _recompute_module()
    conn = sqlite3.connect(str(tmp_path / "sources.db"))
    conn.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, skills TEXT, skills_sources TEXT)")
    conn.executemany(
        "INSERT INTO job (id, skills, skills_sources) VALUES (?, ?, ?)",
        [
            (1, "Java,MySQL", script.encode_skill_sources(["Java", "MySQL"], "Java开发工程师")),
            (2, "Java,Redis", script.encode_skill_sources(["Java", "Redis"], "后端开发")),
            (3, "Golang", script.encode_skill_sources(["Golang"], "Golang 工程师")),
        ],
    )
    conn.commit()

    title_java = conn.execute(
        "SELECT id FROM job WHERE skills_sources LIKE '%|title:Java|%' ORDER BY id"
    ).fetchall()
    desc_java = conn.execute(
        "SELECT id FROM job WHERE skills_sources LIKE '%|desc:Java|%' ORDER BY id"
    ).fetchall()
    conn.close()

    assert title_java == [(1,)]
    assert desc_java == [(2,)]


def test_老库补列是幂等的(tmp_path):
    import sqlite3

    script = _recompute_module()
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, skills TEXT)")
    conn.commit()

    assert script._ensure_sources_column(conn) is True
    assert script._ensure_sources_column(conn) is False
    cols = {row[1] for row in conn.execute("PRAGMA table_info(job)")}
    conn.close()
    assert "skills_sources" in cols