"""推荐候选池的口径（回归 2026-11-11 发现的"42 条"问题）。

## 缺陷

爬虫新建岗位写的是 `NEW`（`crawler_service.py:425`），而推荐原来只认 `ACTIVE`
（`analyzer.py:118`）。`NEW → ACTIVE` 的唯一路径是**重爬同一岗位并命中**
（`save_job:403`），而 555 条真实数据都是一次性抓的 —— 于是全部停在 `NEW`，
**推荐候选池实际只有 42 条**，其中只有 13 条有 `skills`。

这不是"数据少"，是同一份数据在两个地方有两种"在架"定义：
`crawler_service.py:643` 的存活核查用 `in_(["NEW", "ACTIVE"])`，推荐却只用 `ACTIVE`。

## 口径

| 值 | 含义 | 算在架吗 |
|---|---|---|
| `NEW` | 新发现，尚未被二次观察 | ✅ |
| `ACTIVE` | 已被重复观察到 | ✅ |
| `OFFLINE` | 存活核查判离线 | ❌ |
| `CLOSED` | 人工下架 | ❌ |
"""
import pytest

from app.models.job import Job
from app.recommender.analyzer import recommend_jobs


def _job(key: str, status: str, title: str = "Python后端", **kw):
    return Job(
        title=title,
        company_name=kw.pop("company_name", "测试公司"),
        city=kw.pop("city", "北京"),
        source_site="51job",
        job_key=key,
        job_status=status,
        skills=kw.pop("skills", "Python,FastAPI"),
        **kw,
    )


@pytest.mark.asyncio
async def test_推荐候选包含NEW状态(db_session):
    """爬虫刚抓到的岗位（NEW）必须能被推荐，否则候选池只剩被重爬过的那几条。"""
    db_session.add(_job("k_new_1", "NEW", title="Python后端开发"))
    await db_session.commit()

    rows = await recommend_jobs(db_session, skills="Python")

    titles = {r["title"] for r in rows}
    assert "Python后端开发" in titles, "NEW 状态的岗位被排除在推荐之外"


@pytest.mark.asyncio
async def test_推荐排除已下架与已离线(db_session):
    """`OFFLINE` / `CLOSED` 才是"不在架"，必须排除。"""
    db_session.add(_job("k_closed_1", "CLOSED", title="Python已下架"))
    db_session.add(_job("k_offline_1", "OFFLINE", title="Python已离线"))
    await db_session.commit()

    rows = await recommend_jobs(db_session, skills="Python")

    titles = {r["title"] for r in rows}
    assert "Python已下架" not in titles
    assert "Python已离线" not in titles
