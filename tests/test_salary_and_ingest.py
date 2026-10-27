"""薪资解析与「爬虫输出 → 入库」契约的回归测试。

## 这两个测试为什么存在

2026-10-26 全量审阅发现两个**静默**故障，792 个用例全绿却没有一条能发现：

1. ``boss.py`` 只产出 ``salary``（"15-25K·13薪"），不产出 ``min_salary`` /
   ``max_salary``；而 ``save_job`` 按下标取 ``job_data["min_salary"]``。
   结果是 BOSS 的**每一条新岗位**都抛 ``KeyError``，整个任务被标 FAILED；
   库里已存在的岗位走 update 分支不碰该键，所以「抓到的全是老岗位」时
   反而看起来成功 —— 故障被完美掩盖。
2. ``cleaner.parse_salary`` 的正则只认 ``8K-12K``，BOSS 的 ``15-25K·13薪``
   不匹配，于是整站薪资落成 0：岗位能入库、列表能显示，只是薪资列全空、
   薪资分布图与「高薪过滤」静默失效。

两个都属于「不报错但结果全错」的类型 —— 必须靠契约测试锁住，
而不是靠「跑得通」判断。
"""
import pytest

from app.crawlers.cleaner import clean_job_data, extract_skills, generate_job_key, parse_salary
from app.services.crawler_service import normalize_job, save_job

# --------------------------------------------------------------------------
# 薪资文本 → 月薪区间
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # BOSS 的真实写法：单位只写在区间后半段，且常带「·13薪」
        ("15-25K·13薪", (15000, 25000)),
        ("15-25K", (15000, 25000)),
        ("8-12K·14薪", (8000, 12000)),
        ("20-30K", (20000, 30000)),
        # 51job 的写法（保持原有覆盖不回归）
        ("1-1.5万", (10000, 15000)),
        ("8千-1.2万", (8000, 12000)),
        ("6-8千", (6000, 8000)),
        ("15-25万/年", (12500, 20833)),
        ("2万", (20000, 20000)),
        ("8000-12000元/月", (8000, 12000)),
        ("1万-1.5万", (10000, 15000)),
        # 两端写反时纠正
        ("2-1万", (10000, 20000)),
    ],
)
def test_薪资解析覆盖两家站点的真实写法(raw, expected):
    assert parse_salary(raw) == expected


@pytest.mark.parametrize("raw", ["面议", "", None, "保密", "200-300元/天", "50元/小时"])
def test_薪资算不出来就留空而不是瞎猜(raw):
    """薪资是全部分析与可视化的输入，假数据的破坏力远大于空值。"""
    assert parse_salary(raw) == (0, 0)


# --------------------------------------------------------------------------
# 爬虫输出 → 入库 契约
# --------------------------------------------------------------------------


def _boss_job(idx: int = 1) -> dict:
    """复刻 ``boss.py`` 的实际输出结构：**只有 salary，没有 min/max**。

    这个结构就是线上真实的契约，改动它等于改爬虫输出 —— 若将来 boss 也自己
    算 min/max，这个测试仍然有效（normalize_job 应当幂等）。
    """
    return {
        "title": f"Python开发{idx}",
        "salary": "15-25K·13薪",
        "company_name": "某公司",
        "city": "北京",
        "experience": "3-5年",
        "education": "本科",
        "skills": "Python,Django",
        "source_site": "boss",
        "job_key": generate_job_key("boss", f"Python开发{idx}", "某公司", "北京"),
        "url": "https://example.com/job/1",
        "description": "Python开发 Python,Django",
    }


def test_normalize_job_给缺字段的爬虫输出补上薪资():
    normalized = normalize_job(_boss_job())
    assert normalized["min_salary"] == 15000
    assert normalized["max_salary"] == 25000


def test_normalize_job_对已归一化的数据幂等():
    """job51 在 crawl 内部就调用过 clean_job_data，二次归一化不能把值冲成 0。"""
    job = {**_boss_job(), "min_salary": 15000, "max_salary": 25000}
    assert normalize_job(job)["min_salary"] == 15000
    assert normalize_job(job)["max_salary"] == 25000


def test_normalize_job_容忍None字段():
    """页面抽不到某个字段时是 None，clean 内部要 .strip()，不能因此崩。

    注意 skills 为 None 时会被 ``clean_job_data`` 用标题/描述**自动补齐**，
    所以这里只断言「不抛异常且 city 落成空串」，不断言 skills 为空。
    """
    job = {**_boss_job(), "skills": None, "city": None}
    normalized = normalize_job(job)
    assert normalized["city"] == ""
    assert isinstance(normalized["skills"], str)


@pytest.mark.asyncio
async def test_入库路径_不因缺薪资字段而整批失败(db_session):
    """这是本次修复的原始故障点：save_job 曾直接 job_data["min_salary"]。"""
    job = normalize_job(_boss_job(1))
    assert await save_job(db_session, job) is True
    await db_session.commit()

    from app.models.job import Job
    from sqlalchemy import select

    row = (await db_session.execute(select(Job))).scalars().first()
    assert row is not None
    assert row.title == "Python开发1"
    assert (row.min_salary, row.max_salary) == (15000, 25000)


@pytest.mark.asyncio
async def test_同一岗位重复入库只刷新不新增(db_session):
    """指纹相同视为同一岗位，走 update 分支返回 False。"""
    job = normalize_job(_boss_job(2))
    assert await save_job(db_session, job) is True
    assert await save_job(db_session, job) is False
    await db_session.commit()


def test_clean_job_data_截断超长描述():
    """description 列是 Text 但下游会拼进 prompt，必须限长。"""
    cleaned = clean_job_data({**_boss_job(), "description": "x" * 9000})
    assert len(cleaned["description"]) == 5000


# --------------------------------------------------------------------------
# 技能抽取：短词不能命中别的单词内部
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,desc,expected_in,expected_not_in",
    [
        # 「Django」里含 "go"，子串匹配会让每条 Django 岗位都多一个 Go 标签
        ("Django开发工程师", "使用django框架，python后端", "Python", "Go"),
        ("Go开发工程师", "golang 微服务", "Go", None),
        ("C++工程师", "C++ QT 开发", "C++", None),
        ("Node.js后端", "node.js express", "Node.js", None),
    ],
)
def test_技能抽取按词边界匹配(title, desc, expected_in, expected_not_in):
    skills = extract_skills(title, desc)
    assert expected_in in skills, f"{skills} 应包含 {expected_in}"
    if expected_not_in:
        assert expected_not_in not in skills.split(","), (
            f"{skills} 不应包含 {expected_not_in}（短词命中了别的单词内部）"
        )
