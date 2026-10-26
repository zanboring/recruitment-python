"""可视化统计口径一致性测试。

锁定的问题：图表接口原先**不带岗位状态过滤**，把已下架岗位也算进统计，
而 AI 技能分析只算在架岗位。后果是同一个仪表盘上两个数字互相矛盾：

- 「岗位城市分布」里出现一个用户在工作列表里根本看不到的城市；
- AI 说「技能需求 TOP：Java」，技能图表里却还有只存在于下架岗位里的技能。

数字互相矛盾比数字不准更糟 —— 使用者会直接不信任整个看板。
本文件确保六个图表接口与 AI 分析共用同一个口径（``VISIBLE_STATUS``）。
"""
import pytest

from app.models.job import Job
from app.services.job_service import VISIBLE_STATUS, JobService
from app.services.local_model_service import LocalModelService


def _job(title, city, skills, status, key, salary=(10000, 20000)):
    return Job(
        title=title, city=city, skills=skills, company_name=title + "公司",
        education="本科", experience="3-5年", source_site="boss",
        job_key=key, job_status=status,
        min_salary=salary[0], max_salary=salary[1],
    )


@pytest.fixture
async def mixed_jobs(db_session):
    """2 个在架（长沙/Java）+ 1 个已下架（北京/Python，薪资高得离谱）。

    给下架岗位一个极端薪资，是为了让「有没有把它算进去」在平均值上立刻暴露。
    """
    db_session.add_all([
        _job("在架A", "长沙", "Java,MySQL", VISIBLE_STATUS, "k1"),
        _job("在架B", "长沙", "Java", VISIBLE_STATUS, "k2"),
        _job("已下架", "北京", "Python", "OFFLINE", "k3", salary=(100000, 200000)),
    ])
    await db_session.commit()
    return db_session


# ---------- 六个图表接口：都不应计入下架岗位 ----------

@pytest.mark.asyncio
async def test_城市统计不含下架岗位(mixed_jobs):
    data = {r["name"]: r["count"] for r in await JobService.stat_by_city(mixed_jobs)}
    assert data == {"长沙": 2}
    assert "北京" not in data          # 该城市只存在于下架岗位中


@pytest.mark.asyncio
async def test_公司统计不含下架岗位(mixed_jobs):
    data = {r["name"]: r["count"] for r in await JobService.stat_by_company(mixed_jobs)}
    assert data == {"在架A公司": 1, "在架B公司": 1}


@pytest.mark.asyncio
async def test_技能统计不含下架岗位(mixed_jobs):
    data = {r["name"]: r["count"] for r in await JobService.stat_by_skill(mixed_jobs)}
    assert data == {"Java": 2, "MySQL": 1}
    assert "Python" not in data


@pytest.mark.asyncio
async def test_学历统计不含下架岗位(mixed_jobs):
    data = {r["name"]: r["count"] for r in await JobService.stat_by_education(mixed_jobs)}
    assert data == {"本科": 2}


@pytest.mark.asyncio
async def test_经验统计不含下架岗位(mixed_jobs):
    data = {r["name"]: r["count"] for r in await JobService.stat_by_experience(mixed_jobs)}
    assert data == {"3-5年": 2}


@pytest.mark.asyncio
async def test_薪资区间统计不含下架岗位(mixed_jobs):
    data = {r["name"]: r["count"] for r in await JobService.stat_by_salary_range(mixed_jobs)}
    # 两个在架岗位平均 15000 → 落在 15k-20k 区间
    assert data.get("15k-20k") == 2
    assert sum(data.values()) == 2


# ---------- 摘要接口内部口径一致 ----------

@pytest.mark.asyncio
async def test_摘要的平均薪资只算在架岗位(mixed_jobs):
    """回归：原先 active 只算在架，avg_salary 却把下架岗位一起平均。

    下架岗位薪资 150000，若被算入，平均值会被拉到 60000 量级 ——
    这里断言它等于纯在架岗位的平均值 15000。
    """
    summary = await JobService.stat_summary(mixed_jobs)

    assert summary["active"] == 2
    assert summary["total"] == 3          # 累计采集量含下架，这是有意的
    assert summary["avg_salary"] == 15000.0


@pytest.mark.asyncio
async def test_摘要与图表口径一致(mixed_jobs):
    """同一份数据下，「在架岗位数」应等于城市图表的总计数。"""
    summary = await JobService.stat_summary(mixed_jobs)
    city_total = sum(r["count"] for r in await JobService.stat_by_city(mixed_jobs))

    assert city_total == summary["active"]


# ---------- 与 AI 分析对齐 ----------

@pytest.mark.asyncio
async def test_技能图表与AI技能分析口径一致(mixed_jobs):
    """回归核心：AI 说的 Top 技能必须与技能图表一致。

    此前 AI 分析过滤 ACTIVE、技能图表不过滤，于是 AI 说「Java(2)」而图表里
    还多出一个只存在于下架岗位的 Python。
    """
    chart = {r["name"]: r["count"] for r in await JobService.stat_by_skill(mixed_jobs)}
    report = await LocalModelService._skill_analysis(mixed_jobs)

    for skill, count in chart.items():
        assert f"{skill}({count})" in report, f"{skill} 在图表里是 {count}，AI 报告里却没有"


# ---------- 例外：状态分布图表必须保留全部状态 ----------

@pytest.mark.asyncio
async def test_状态分布统计必须包含下架岗位(mixed_jobs):
    """stat_by_status 的职责就是展示各状态分布，不能跟着一起过滤。"""
    data = {r["name"]: r["count"] for r in await JobService.stat_by_status(mixed_jobs)}

    assert data.get(VISIBLE_STATUS) == 2
    assert data.get("OFFLINE") == 1
