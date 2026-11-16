"""20 条固定夹具上的分布对账（E2）。

夹具是**写死**的，期望值全部手算后写进断言 —— 这样用例不依赖被测代码自己算出来的
数字，否则「实现错了、期望也跟着错」就永远测不出来。

手算基准（20 条，全部 ACTIVE）：
- 城市：长沙 8 / 北京 5 / 上海 4 / 深圳 2 / 广州 1
- 学历：本科 15 / 硕士 4 / 大专 1
- 经验：3-5年 8 / 1-3年 7 / 5年以上 5（原文 "5-10年"，上界 10 > 5 → 归「5年以上」）
- 技能（出现次数，合计 40 次）：
    Python 6 / MySQL 5 / Java 5 / FastAPI 3 / 前端 3 / Vue 3 /
    Spring 2 / Go 2 / 数据分析 2 / PyTorch 2 /
    React 1 / Docker 1 / 算法 1 / Redis 1 / SQL 1 / 运维 1 / K8s 1
- 薪资桶（按 (min+max)/2）：0-5k 0 / 5k-10k 0 / 10k-15k 3 / 15k-20k 5 /
    20k-30k 10 / 30k-50k 2 / 50k+ 0
- 平均薪资（中位值之和 / 20）：421500 / 20 = 21075.0
"""
import pytest
import pytest_asyncio

from app.models.job import Job
from tests.helpers import admin_token, auth_headers

pytestmark = pytest.mark.asyncio


# (城市, 技能, 学历, 经验, 最低, 最高, 标题)
FIXTURES = [
    ("长沙", "Python,FastAPI", "本科", "1-3年", 10000, 14000, "长沙-全栈工程师"),
    ("长沙", "Python,MySQL", "本科", "3-5年", 12000, 18000, "长沙-后端开发"),
    ("长沙", "Java,Spring", "本科", "3-5年", 15000, 25000, "长沙-Java后端"),
    ("长沙", "Java,MySQL", "硕士", "5-10年", 20000, 30000, "长沙-资深架构师"),
    ("长沙", "前端,Vue", "本科", "1-3年", 9000, 13000, "长沙-前端开发"),
    ("长沙", "Go,MySQL", "本科", "3-5年", 16000, 24000, "长沙-Go后端"),
    ("长沙", "Python,Vue", "大专", "1-3年", 8000, 12000, "长沙-Python前端"),
    ("长沙", "数据分析,PyTorch", "硕士", "3-5年", 18000, 28000, "长沙-数据算法"),
    ("北京", "Python,FastAPI", "本科", "3-5年", 18000, 26000, "北京-全栈工程师"),
    ("北京", "Java,Spring", "本科", "5-10年", 25000, 40000, "北京-Java架构"),
    ("北京", "前端,React", "本科", "1-3年", 12000, 18000, "北京-前端React"),
    ("北京", "Go,Docker", "本科", "3-5年", 20000, 32000, "北京-Go运维"),
    ("北京", "算法,PyTorch", "硕士", "5-10年", 30000, 50000, "北京-算法专家"),
    ("上海", "Python,MySQL", "本科", "1-3年", 14000, 20000, "上海-后端开发"),
    ("上海", "Java,Redis", "本科", "3-5年", 20000, 30000, "上海-Java中间件"),
    ("上海", "数据分析,SQL", "硕士", "1-3年", 15000, 22000, "上海-数据分析"),
    ("上海", "运维,K8s", "本科", "5-10年", 18000, 28000, "上海-运维K8s"),
    ("深圳", "Python,FastAPI", "本科", "1-3年", 16000, 24000, "深圳-全栈工程师"),
    ("深圳", "前端,Vue", "本科", "3-5年", 14000, 22000, "深圳-前端Vue"),
    ("广州", "Java,MySQL", "本科", "5-10年", 22000, 35000, "广州-Java后端"),
]

EXPECTED_CITY = {"长沙": 8, "北京": 5, "上海": 4, "深圳": 2, "广州": 1}
EXPECTED_EDUCATION = {"本科": 15, "硕士": 4, "大专": 1}
EXPECTED_EXPERIENCE = {"3-5年": 8, "1-3年": 7, "5年以上": 5}
EXPECTED_SKILL = {
    "Python": 6, "MySQL": 5, "Java": 5, "FastAPI": 3, "前端": 3, "Vue": 3,
    "Spring": 2, "Go": 2, "数据分析": 2, "PyTorch": 2,
    "React": 1, "Docker": 1, "算法": 1, "Redis": 1, "SQL": 1, "运维": 1, "K8s": 1,
}
EXPECTED_SALARY = {"0-5k": 0, "5k-10k": 0, "10k-15k": 3, "15k-20k": 5,
                   "20k-30k": 10, "30k-50k": 2, "50k+": 0}
EXPECTED_AVG_SALARY = 21075.0


@pytest_asyncio.fixture
async def twenty_jobs(db_session):
    """把 20 条固定夹具写进测试内存库。"""
    for i, (city, skills, edu, exp, lo, hi, title) in enumerate(FIXTURES, 1):
        db_session.add(Job(
            job_key=f"e2-fixture-{i:02d}",
            source_site="boss",
            job_status="ACTIVE",
            title=title,
            company_name=f"夹具公司{i:02d}",
            city=city,
            skills=skills,
            education=edu,
            experience=exp,
            min_salary=lo,
            max_salary=hi,
            job_desc=f"E2 固定夹具 {i}",
        ))
    await db_session.commit()


async def _get(client, token, path):
    resp = await client.get(path, headers=auth_headers(token))
    assert resp.status_code == 200, f"{path} 返回 {resp.status_code}: {resp.text}"
    return resp.json()["data"]


async def _post(client, token, path, payload):
    resp = await client.post(path, json=payload, headers=auth_headers(token))
    assert resp.status_code == 200, f"{path} 返回 {resp.status_code}: {resp.text}"
    return resp.json()["data"]


async def test_城市分布与手算一致(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    data = await _get(client, token, "/api/jobs/stat/city")
    assert {item["name"]: item["count"] for item in data} == EXPECTED_CITY
    # 排序必须是降序，前端饼图按返回顺序取 TOP N
    counts = [item["count"] for item in data]
    assert counts == sorted(counts, reverse=True)


async def test_技能分布与手算一致(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    data = await _get(client, token, "/api/jobs/stat/skill")
    assert {item["name"]: item["count"] for item in data} == EXPECTED_SKILL
    assert sum(item["count"] for item in data) == 40


async def test_薪资分布与手算一致(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    data = await _get(client, token, "/api/jobs/stat/salary-range")
    assert {item["name"]: item["count"] for item in data} == EXPECTED_SALARY
    assert sum(item["count"] for item in data) == 20


async def test_学历与经验分布与手算一致(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    edu = await _get(client, token, "/api/jobs/stat/education")
    exp = await _get(client, token, "/api/jobs/stat/experience")
    assert {item["name"]: item["count"] for item in edu} == EXPECTED_EDUCATION
    assert {item["name"]: item["count"] for item in exp} == EXPECTED_EXPERIENCE


async def test_汇总统计与手算一致(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    data = await _get(client, token, "/api/jobs/analysis/summary")
    assert data["total"] == 20
    assert data["active"] == 20
    assert data["avg_salary"] == EXPECTED_AVG_SALARY


async def test_推荐结果合理且受_limit_约束(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    data = await _post(client, token, "/api/jobs/recommend/intelligent", {
        "skills": "Python,FastAPI", "city": "", "experienceYears": 2, "limit": 5,
    })
    assert len(data) == 5, "limit=5 时必须恰好返回 5 条（库里有 6 条命中候选）"
    scores = [item["score"] for item in data]
    assert scores == sorted(scores, reverse=True)
    # 只保留与查询技能确有交集的岗位：20 条里只有 6 条含 Python
    assert all(item["skill_similarity"] > 0 for item in data)
    # 技能完全命中的三条（Python + FastAPI）必须排在最前
    top3 = {item["title"] for item in data[:3]}
    assert top3 == {"长沙-全栈工程师", "北京-全栈工程师", "深圳-全栈工程师"}
    assert sorted(scores[:3]) == [0.75, 0.8, 0.8]


async def test_分页边界(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    # 首页：total 恒为 20，当前页 6 条
    page1 = await _post(client, token, "/api/jobs/page", {"page_num": 1, "page_size": 6})
    assert page1["total"] == 20 and len(page1["list"]) == 6
    # 末页：20 = 6*3 + 2，第 4 页只剩 2 条
    page4 = await _post(client, token, "/api/jobs/page", {"page_num": 4, "page_size": 6})
    assert page4["total"] == 20 and len(page4["list"]) == 2
    # 越界页：列表为空，但 total 不能被算成 0（否则前端会以为库空了）
    page5 = await _post(client, token, "/api/jobs/page", {"page_num": 5, "page_size": 6})
    assert page5["total"] == 20 and page5["list"] == []
    # 一页装下全部
    page_all = await _post(client, token, "/api/jobs/page", {"page_num": 1, "page_size": 20})
    assert page_all["total"] == 20 and len(page_all["list"]) == 20


# ==========================================================================
# 统计口径：可见 = NEW + ACTIVE（2026-11-16）
#
# 事故：九张统计图合计只有 42 条，而库里有 555 条。
# 根因是统计口径写成单个 ``"ACTIVE"``，而爬虫新建岗位写的是 ``"NEW"``；
# ``NEW → ACTIVE`` 的唯一路径是「再次爬到同一岗位并命中」，555 条一次性抓取的
# 数据永远停在 NEW。推荐与存活核查两处早已是 ``in_(["NEW","ACTIVE"])``，
# 只剩统计这一片没对齐。
#
# 这组用例要能**在旧实现上变红**：旧实现只用 == "ACTIVE"，下面的合计会变成 2。
# ==========================================================================


@pytest_asyncio.fixture
async def jobs_by_status(db_session):
    """四种状态各铺几条：NEW×3 / ACTIVE×2 / OFFLINE×2 / CLOSED×1。

    不可见那三行故意用**不重叠**的城市/技能/学历（下架城、下架技能、博士…），
    这样一旦口径写错，分组名本身就会暴露，而不是只差几个数字。
    """
    rows = [
        # (状态, 城市, 技能, 学历, 经验, key)
        ("NEW", "长沙", "Python", "本科", "1-3年", "s1"),
        ("NEW", "长沙", "Python", "本科", "3-5年", "s2"),
        ("NEW", "北京", "Go", "硕士", "5-10年", "s3"),
        ("ACTIVE", "长沙", "Java", "本科", "1-3年", "s4"),
        ("ACTIVE", "上海", "Java", "大专", "3年及以上", "s5"),
        ("OFFLINE", "下架城", "下架技能", "博士", "10年以上", "s6"),
        ("OFFLINE", "下架城", "下架技能", "博士", "10年以上", "s7"),
        ("CLOSED", "关闭城", "关闭技能", "博士", "10年以上", "s8"),
    ]
    for i, (status, city, skills, edu, exp, key) in enumerate(rows):
        db_session.add(Job(
            job_key=f"status-fixture-{key}",
            source_site="51job",
            job_status=status,
            title=f"状态夹具{i}",
            company_name=f"公司{status}",
            city=city,
            skills=skills,
            education=edu,
            experience=exp,
            min_salary=10000 + i * 1000,
            max_salary=20000 + i * 1000,
        ))
    await db_session.commit()


async def test_统计口径只含NEW与ACTIVE且合计等于两者之和(jobs_by_status, db_session):
    from app.services.job_service import JobService

    visible = 5                     # NEW 3 + ACTIVE 2
    city = {r["name"]: r["count"] for r in await JobService.stat_by_city(db_session)}
    assert city == {"长沙": 3, "北京": 1, "上海": 1}
    assert sum(city.values()) == visible, "合计必须等于 NEW + ACTIVE 的行数（旧实现这里会是 2）"

    edu = {r["name"]: r["count"] for r in await JobService.stat_by_education(db_session)}
    assert edu == {"本科": 3, "硕士": 1, "大专": 1}, f"下架/关闭岗位的学历漏进来了：{edu}"

    skill = {r["name"]: r["count"] for r in await JobService.stat_by_skill(db_session)}
    assert skill == {"Python": 2, "Java": 2, "Go": 1}, f"技能口径不对：{skill}"

    company = {r["name"]: r["count"] for r in await JobService.stat_by_company(db_session)}
    assert "公司OFFLINE" not in company and "公司CLOSED" not in company

    salary = await JobService.stat_by_salary_range(db_session)
    assert sum(r["count"] for r in salary) == visible

    exp = await JobService.stat_by_experience(db_session)
    assert sum(r["count"] for r in exp) == visible, "经验统计丢样本（合计对不上可见岗位数）"

    summary = await JobService.stat_summary(db_session)
    assert summary["active"] == visible, "键名保留 active，但含义已是「可见量」"
    assert summary["total"] == 8, "累计采集量仍要含下架/关闭"


async def test_状态分布仍然展示全部四种状态(jobs_by_status, db_session):
    """stat_by_status 是唯一例外：它的职责就是展示各状态分布。"""
    from app.services.job_service import JobService

    data = {r["name"]: r["count"] for r in await JobService.stat_by_status(db_session)}
    assert data == {"NEW": 3, "ACTIVE": 2, "OFFLINE": 2, "CLOSED": 1}


# 真实库（2026-11-16 快照）里 experience 的 22 种写法 → 桶
EXPERIENCE_BUCKET_TABLE = {
    "无需经验": "无需经验",
    "1年": "1年以内",
    "1年及以上": "1-3年",
    "2年": "1-3年",
    "2年及以上": "1-3年",
    "1-3年": "1-3年",
    "2-3年": "1-3年",
    "1-2年": "1-3年",
    "3年": "3-5年",
    "3年及以上": "3-5年",
    "3-5年": "3-5年",
    "3-4年": "3-5年",
    "2-5年": "3-5年",
    "2-4年": "3-5年",
    "1-5年": "3-5年",
    "4-5年": "3-5年",        # 下界 4 > 3，表里两条规则都不命中 → 按上界归 3-5年
    "4年及以上": "5年以上",
    "1-8年": "5年以上",
    "1-7年": "5年以上",
    "3-10年": "5年以上",
    "2-10年": "5年以上",
    "1-10年": "5年以上",
}


async def test_经验归并覆盖真实库里的全部22种写法():
    from app.services.job_service import bucket_experience

    assert len(EXPERIENCE_BUCKET_TABLE) == 22, "这张表必须覆盖全部 22 种真实写法"
    wrong = {
        raw: (bucket_experience(raw), expect)
        for raw, expect in EXPERIENCE_BUCKET_TABLE.items()
        if bucket_experience(raw) != expect
    }
    assert not wrong, f"这些写法归错桶（实际, 期望）：{wrong}"


async def test_经验归并的边界与脏值():
    from app.services.job_service import bucket_experience

    # 解析不出来 → 其他。**不许静默丢弃**：丢掉会让各桶合计对不上岗位总数，
    # 那又是一个"看起来对"的假数字。
    for raw in ("", None, "昨天", "经验丰富", "abc", "面议"):
        assert bucket_experience(raw) == "其他", raw

    # 「不限 / 应届 / 在校 / 实习」都归无需经验
    for raw in ("经验不限", "应届生", "在校", "实习", "在校/实习"):
        assert bucket_experience(raw) == "无需经验", raw

    # 写反的区间也认（5-3年 就是 3-5年），而不是当成解析失败
    assert bucket_experience("5-3年") == "3-5年"
    # 带空格 / 全角的写法
    assert bucket_experience(" 1 - 3 年 ") == "1-3年"


async def test_经验接口返回的是桶而不是原始写法(client, db_session, twenty_jobs):
    token = await admin_token(client, db_session)
    data = await _get(client, token, "/api/jobs/stat/experience")

    names = {item["name"] for item in data}
    assert names == {"3-5年", "1-3年", "5年以上"}, f"接口还在按原文分组：{names}"
    assert {item["name"]: item["count"] for item in data} == EXPECTED_EXPERIENCE
    # 归并不许丢样本：合计必须等于可见岗位数
    assert sum(item["count"] for item in data) == len(FIXTURES)

