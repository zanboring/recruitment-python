"""爬虫重试机制与商家逻辑测试。

重点验证：
1. BaseCrawler.crawl_with_retry 的指数退避 / 最终放弃行为；
2. CrawlerService.start_crawl 真的走的是 retry 包装而不是裸 crawl()；
3. 下架判定有宽限期，不会因为「换关键词爬取」误伤同平台的其它岗位。
"""
import pytest

from app.config import settings
from app.crawlers import base as base_module
from app.crawlers.base import BaseCrawler
from app.services import crawler_service
from sqlalchemy import select
from app.models.job import Job


class StubCrawler(BaseCrawler):
    """用可控的失败序列替代真实爬虫，避免测试依赖网络。"""

    def __init__(self, failures: int, results: list = None):
        super().__init__("stub")
        self.remaining_failures = failures
        self.calls = 0
        self.results = results or []

    async def crawl(self, keyword: str, city: str = ""):
        self.calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError(f"网络抖动 第 {self.calls} 次")
        return list(self.results)

    async def parse_page(self, page_content, keyword: str):
        return []


@pytest.fixture
def fast_sleep(monkeypatch):
    """把重试等待替换为立即返回，并记录等待时长，避免测试真的睡几十秒。"""
    recorded = []

    class FakeAsyncio:
        @staticmethod
        async def sleep(seconds):
            recorded.append(seconds)

    monkeypatch.setattr(base_module, "asyncio", FakeAsyncio)
    return recorded


def make_job_data(keyword: str, n: int) -> list:
    return [{
        "title": f"{keyword}工程师{i}",
        "company_name": f"公司{i}",
        "source_site": "boss",
        "job_key": f"boss_{keyword}_{i}",
        "city": "长沙",
        "experience": "1-3年",
        "education": "本科",
        "min_salary": 12000,
        "max_salary": 20000,
        "skills": "Java,SpringBoot",
        "description": "负责后端服务开发",
    } for i in range(n)]


@pytest.mark.asyncio
class TestCrawlWithRetry:
    async def test_首次成功则不再重试(self, fast_sleep):
        crawler = StubCrawler(failures=0, results=[{"a": 1}])
        result = await crawler.crawl_with_retry("Java", "长沙")
        assert result == [{"a": 1}]
        assert crawler.calls == 1
        assert fast_sleep == []

    async def test_失败后自动重试直到成功(self, fast_sleep):
        crawler = StubCrawler(failures=2, results=[{"a": 1}])
        result = await crawler.crawl_with_retry("Java")
        assert result == [{"a": 1}]
        assert crawler.calls == 3

    async def test_退避时长按指数增长(self, fast_sleep):
        crawler = StubCrawler(failures=3, results=[])
        await crawler.crawl_with_retry("Java")
        # 2^1=2, 2^2=4, 2^3=8，再叠加 0~1 秒抖动
        assert len(fast_sleep) == 3
        for i, waited in enumerate(fast_sleep):
            base = 2 ** (i + 1)
            assert base <= waited <= base + 1

    async def test_重试次数受配置控制(self, fast_sleep, monkeypatch):
        monkeypatch.setattr(settings, "crawl_retry_times", 2)
        crawler = StubCrawler(failures=99)
        with pytest.raises(RuntimeError):
            await crawler.crawl_with_retry("Java")
        assert crawler.calls == 2
        assert len(fast_sleep) == 1

    async def test_用尽重试后抛出最后一次异常(self, fast_sleep):
        crawler = StubCrawler(failures=99)
        with pytest.raises(RuntimeError) as exc:
            await crawler.crawl_with_retry("Java")
        assert "网络抖动" in str(exc.value)

    async def test_空结果返回空列表(self, fast_sleep):
        crawler = StubCrawler(failures=0, results=[])
        assert await crawler.crawl_with_retry("Java") == []


@pytest.mark.asyncio
class TestStartCrawl:
    async def test_走的是带重试的入口(self, db_session, monkeypatch, fast_sleep):
        """裸 crawl() 不应该被调用；失败应通过 retry 恢复。"""
        calls = {"retry": 0, "plain": 0}

        async def fake_crawl_with_retry(self, keyword, city=""):
            calls["retry"] += 1
            return make_job_data(keyword, 2)

        async def fail(self, keyword, city=""):
            calls["plain"] += 1
            raise AssertionError("start_crawl 不应直接调用 crawl()")

        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake_crawl_with_retry)
        monkeypatch.setattr(crawler_service.BossCrawler, "crawl", fail)

        count = await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])
        assert count == 2
        assert calls == {"retry": 1, "plain": 0}

    async def test_任务状态落库为已完成(self, db_session, monkeypatch):
        async def fake(self, keyword, city=""):
            return make_job_data(keyword, 3)

        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake)
        await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])

        tasks = await crawler_service.get_tasks(db_session)
        assert len(tasks) == 1
        assert tasks[0].status == "COMPLETED"
        assert tasks[0].job_count == 3

    async def test_爬虫彻底失败时任务状态为失败(self, db_session, monkeypatch, fast_sleep):
        async def fake(self, keyword, city=""):
            raise RuntimeError("目标站点不可达")

        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake)
        with pytest.raises(RuntimeError):
            await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])

        tasks = await crawler_service.get_tasks(db_session)
        assert tasks[0].status == "FAILED"
        assert "目标站点不可达" in tasks[0].message

    async def test_重复爬取的岗位被去重而非新增(self, db_session, monkeypatch):
        async def fake(self, keyword, city=""):
            return make_job_data("Java", 2)

        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake)
        assert await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"]) == 2
        assert await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"]) == 0

    async def test_下架判定有宽限期不会误伤(self, db_session, monkeypatch):
        """换关键词爬取时，刚被其它关键词刷新过的岗位不能被判成下架。"""
        first = make_job_data("Java", 1)
        async def fake_first(self, keyword, city=""):
            return first
        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake_first)
        await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])

        # 第二次只爬「Python」，Java 岗位不在结果里
        async def fake_second(self, keyword, city=""):
            return make_job_data("Python", 1)
        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake_second)
        await crawler_service.start_crawl(db_session, "Python", "长沙", ["boss"])

        job = (await db_session.execute(
            select(Job).where(Job.job_key == "boss_Java_0")
        )).scalar_one()
        assert job.job_status in ("NEW", "ACTIVE")

    async def test_长期未出现才被标记下架(self, db_session, monkeypatch):
        """把 last_seen_at 改到宽限期之前，再爬一次应被置为 OFFLINE。"""
        from datetime import timedelta

        async def fake_first(self, keyword, city=""):
            return make_job_data("Java", 1)
        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake_first)
        await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])

        job = (await db_session.execute(
            select(Job).where(Job.job_key == "boss_Java_0")
        )).scalar_one()
        job.last_seen_at = crawler_service._utc_now() - timedelta(
            seconds=crawler_service.OFFLINE_GRACE_SECONDS + 60
        )
        await db_session.commit()

        async def fake_second(self, keyword, city=""):
            return make_job_data("Python", 1)
        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake_second)
        await crawler_service.start_crawl(db_session, "Python", "长沙", ["boss"])

        await db_session.refresh(job)
        assert job.job_status == "OFFLINE"

    async def test_过滤规则筛掉高薪异常岗位(self, db_session, monkeypatch):
        """月薪异常高（疑似虚假/猎头挂靠）的岗位不入库存。"""
        data = make_job_data("Java", 1)
        data[0]["min_salary"] = 200000
        async def fake(self, keyword, city=""):
            return data
        monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake)

        assert await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"]) == 0
