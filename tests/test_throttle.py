"""采集护栏测试：跨任务节流、日配额、Retry-After 解析、robots 合规门禁。

这些护栏解决的是 ``BaseCrawler`` 覆盖不到的「任务之间」盲区
（它只管一次爬取内部相邻请求的间隔）。用例覆盖三层：

1. **纯逻辑**：Retry-After 两种格式、配额计数、域名解析 —— 直接断言；
2. **节流行为**：真起并发协程，验证同一域名确实被串行化（而不是只加了延迟）；
3. **接线**：配额耗尽 / robots 拒绝时，任务必须**明确失败**而不是静默跑成 0 条。
"""
import asyncio
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.crawlers import robots
from app.crawlers.registry import probe_url
from app.crawlers.throttle import (
    DailyQuota,
    DomainGate,
    domain_of,
    parse_retry_after,
)
from app.services import crawler_service

# --------------------------------------------------------------------------
# 域名解析
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.zhipin.com/web/geek/job?x=1", "www.zhipin.com"),
        ("https://WE.51job.com/pc/search", "we.51job.com"),
        ("http://localhost:8080/api", "localhost"),
        ("", ""),
        ("not a url", ""),
        (None, ""),
    ],
)
def test_域名解析(url, expected):
    assert domain_of(url) == expected


# --------------------------------------------------------------------------
# Retry-After
# --------------------------------------------------------------------------


def test_retry_after_秒数格式():
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(" 30 ") == 30.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("2.5") == 2.5


def test_retry_after_http_日期格式():
    """只处理秒数会漏掉这种写法，然后按自己的策略重试 —— 对方说了等多久却不听。"""
    now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
    value = "Thu, 17 Sep 2026 12:01:00 GMT"
    assert parse_retry_after(value, now=now) == 60.0


def test_retry_after_过去的时间按零处理():
    now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
    value = "Thu, 17 Sep 2026 11:00:00 GMT"
    assert parse_retry_after(value, now=now) == 0.0


@pytest.mark.parametrize("value", [None, "", "   ", "abc", "next-week", "Wed, 99 Xxx 2026 99:99:99 GMT"])
def test_retry_after_无法解析返回_None(value):
    assert parse_retry_after(value) is None


# --------------------------------------------------------------------------
# 域名节流（真起并发验证串行化）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_无域名或不限制时立即返回():
    gate = DomainGate()
    assert await gate.acquire("", 10) == 0.0
    assert await gate.acquire("a.com", 0) == 0.0


@pytest.mark.asyncio
async def test_同一域名两次请求受最小间隔约束():
    """紧邻的第二次请求必须等待。

    断言用 ``acquire`` 返回的等待值（确定性），而不是只看墙钟时间：
    如果两次调用之间恰好已过了足够时间（例如整机负载高、事件循环被别的用例占住），
    节流器本就不需要等待 —— 那种情况下墙钟断言会假失败。
    """
    interval = 0.3
    gate = DomainGate()

    first = await gate.acquire("a.com", interval)
    second = await gate.acquire("a.com", interval)

    assert first == 0.0, "首次请求无需等待"
    assert second > 0, "紧邻的第二次请求必须等待"
    assert second >= interval * 0.8, "等待时长应接近最小间隔"


@pytest.mark.asyncio
async def test_不同域名互不阻塞():
    gate = DomainGate()
    await gate.acquire("a.com", 5)

    waited = await gate.acquire("b.com", 5)

    assert waited == 0.0, "不同域名不应互相等待"


@pytest.mark.asyncio
async def test_并发请求被串行化而不是只延迟():
    """只加延迟的话两个任务会各自「等够时间」后同时发请求，对站点而言并发仍是 2。"""
    interval = 0.2
    gate = DomainGate()

    waits = await asyncio.gather(
        gate.acquire("a.com", interval),
        gate.acquire("a.com", interval),
        gate.acquire("a.com", interval),
    )

    # 第一个直接通过，后两个必须各等一个间隔 —— 这就是「串行」而非「同时延迟」
    assert waits[0] == 0.0
    assert waits[1] >= interval * 0.8
    assert waits[2] >= interval * 0.8


@pytest.mark.asyncio
async def test_节流状态可重置():
    gate = DomainGate()
    await gate.acquire("a.com", 5)
    gate.reset()
    assert gate.snapshot() == {}
    assert await gate.acquire("a.com", 5) == 0.0   # 重置后不应再等


# --------------------------------------------------------------------------
# 日配额
# --------------------------------------------------------------------------


def test_配额用满后拒绝且不计数():
    quota = DailyQuota()
    assert quota.try_consume("boss", limit=2) is True
    assert quota.try_consume("boss", limit=2) is True
    assert quota.try_consume("boss", limit=2) is False
    # 被拒的那次不能把额度吃掉，否则剩余量会莫名其妙地往下掉
    assert quota.used("boss") == 2
    assert quota.remaining("boss", 2) == 0


def test_配额为_0_或负数视为不限制():
    quota = DailyQuota()
    for _ in range(50):
        assert quota.try_consume("boss", limit=0) is True
    assert quota.used("boss") == 0
    assert quota.remaining("boss", 0) is None


def test_配额按平台独立计数():
    quota = DailyQuota()
    quota.try_consume("boss", limit=1)
    assert quota.try_consume("boss", limit=1) is False
    assert quota.try_consume("51job", limit=1) is True


def test_配额快照():
    quota = DailyQuota()
    quota.try_consume("boss", limit=10)
    quota.try_consume("boss", limit=10)
    snap = quota.snapshot({"boss": 10, "51job": 5})
    assert snap["boss"] == {"used": 2, "limit": 10, "remaining": 8}


# --------------------------------------------------------------------------
# robots 合规门禁
# --------------------------------------------------------------------------


@pytest.fixture
def robots_off(monkeypatch):
    monkeypatch.setattr(settings, "crawl_robots_check_enabled", False)
    monkeypatch.setattr(settings, "crawl_robots_strict", False)


@pytest.fixture
def robots_on(monkeypatch):
    monkeypatch.setattr(settings, "crawl_robots_check_enabled", True)
    monkeypatch.setattr(settings, "crawl_robots_strict", False)
    robots.clear_cache()


def _deny_allowed(monkeypatch, allowed):
    calls = []

    async def fake(url):
        calls.append(url)
        return allowed

    monkeypatch.setattr(robots._probe, "check_robots_allowed", fake)
    return calls


@pytest.mark.asyncio
async def test_开关关闭时不检查也不发请求(robots_off, monkeypatch):
    calls = _deny_allowed(monkeypatch, False)

    allowed, note = await robots.gate("boss", probe_url("boss"))

    assert allowed is True
    assert note == ""
    assert calls == []


@pytest.mark.asyncio
async def test_允许时放行且无说明(robots_on, monkeypatch):
    _deny_allowed(monkeypatch, True)

    allowed, note = await robots.gate("boss", probe_url("boss"))

    assert allowed is True
    assert note == ""


@pytest.mark.asyncio
async def test_宽松模式禁止时仍然放行但必须留下说明(robots_on, monkeypatch):
    """不能静默通过 —— 说明里要写清「robots 禁止、按宽松模式继续」。"""
    _deny_allowed(monkeypatch, False)

    allowed, note = await robots.gate("boss", probe_url("boss"))

    assert allowed is True
    assert "robots" in note
    assert "CRAWL_ROBOTS_STRICT" in note


@pytest.mark.asyncio
async def test_严格模式禁止时拒绝(robots_on, monkeypatch):
    monkeypatch.setattr(settings, "crawl_robots_strict", True)
    _deny_allowed(monkeypatch, False)

    allowed, note = await robots.gate("boss", probe_url("boss"))

    assert allowed is False
    assert "robots" in note


@pytest.mark.asyncio
async def test_结果按域名缓存不重复拉取(robots_on, monkeypatch):
    """每个任务都去拉一次 robots.txt 本身就是额外流量，而且模式可疑。"""
    calls = _deny_allowed(monkeypatch, True)

    await robots.gate("boss", probe_url("boss"))
    await robots.gate("boss", probe_url("boss"))
    await robots.gate("boss", probe_url("boss"))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_清缓存后会重新查询(robots_on, monkeypatch):
    calls = _deny_allowed(monkeypatch, True)

    await robots.gate("boss", probe_url("boss"))
    robots.clear_cache()
    await robots.gate("boss", probe_url("boss"))

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_拿不到域名时不阻塞业务(robots_on, monkeypatch):
    _deny_allowed(monkeypatch, False)
    allowed, note = await robots.gate("boss", "")
    assert allowed is True
    assert "无法判断" in note


def test_已登记平台的探针地址():
    assert probe_url("boss").startswith("https://")
    assert probe_url("51job").startswith("https://")
    assert probe_url("zhaopin") == ""


# --------------------------------------------------------------------------
# 接线：护栏触发时任务必须明确失败
# --------------------------------------------------------------------------


def make_job_data(keyword: str = "Java", n: int = 1) -> list:
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


def _no_network(monkeypatch, crawler_called: list):
    monkeypatch.setattr(settings, "crawl_domain_min_interval", 0)

    async def fake(self, keyword, city=""):
        crawler_called.append((keyword, city))
        return make_job_data(keyword)

    monkeypatch.setattr(crawler_service.BossCrawler, "crawl_with_retry", fake)


@pytest.mark.asyncio
async def test_日配额耗尽时任务明确失败(db_session, monkeypatch):
    """「0 条」和「被配额拦下」是两回事，后者必须让使用者看见原因。"""
    monkeypatch.setattr(settings, "crawl_robots_check_enabled", False)
    monkeypatch.setattr(settings, "crawl_daily_quota_per_platform", 1)
    called: list = []
    _no_network(monkeypatch, called)

    assert await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"]) == 1

    with pytest.raises(RuntimeError) as exc:
        await crawler_service.start_crawl(db_session, "Python", "长沙", ["boss"])

    assert "配额" in str(exc.value)
    assert len(called) == 1, "超限的那次不得发起真实请求"

    tasks = await crawler_service.get_tasks(db_session)
    assert any(t.status == "FAILED" and "配额" in (t.message or "") for t in tasks)


@pytest.mark.asyncio
async def test_robots_严格模式在请求之前拦下(db_session, monkeypatch):
    monkeypatch.setattr(settings, "crawl_robots_check_enabled", True)
    monkeypatch.setattr(settings, "crawl_robots_strict", True)
    monkeypatch.setattr(settings, "crawl_daily_quota_per_platform", 0)
    _deny_allowed(monkeypatch, False)
    called: list = []
    _no_network(monkeypatch, called)

    with pytest.raises(RuntimeError) as exc:
        await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])

    assert "robots" in str(exc.value)
    assert called == [], "命中 robots 拒绝时不得发起采集"

    tasks = await crawler_service.get_tasks(db_session)
    assert any(t.status == "FAILED" for t in tasks)


@pytest.mark.asyncio
async def test_robots_宽松模式不阻断采集(db_session, monkeypatch):
    monkeypatch.setattr(settings, "crawl_robots_check_enabled", True)
    monkeypatch.setattr(settings, "crawl_robots_strict", False)
    monkeypatch.setattr(settings, "crawl_daily_quota_per_platform", 0)
    _deny_allowed(monkeypatch, False)
    called: list = []
    _no_network(monkeypatch, called)

    count = await crawler_service.start_crawl(db_session, "Java", "长沙", ["boss"])

    assert count == 1
    assert len(called) == 1
