"""前程无忧（51job）爬虫的离线单测。

真实站点不可在 CI/沙箱访问，因此这里：
- 薪资解析、城市编码、JSON 结构容错 —— 纯逻辑，直接断言；
- ``crawl()`` 翻页 —— 注入假 Playwright 模块驱动真实循环。

重点锁死两类「静默失败」：
1. 未收录城市回退到别的城市（会把外地岗位记在目标城市名下）；
2. 响应结构变化时把「解析不了」当成「没有岗位」。
"""
import json
import sys
import types

import pytest

from app.config import settings
from app.crawlers.city_map import UnsupportedCityError
from app.crawlers.cleaner import generate_job_key
from app.crawlers.job51 import (
    Job51Crawler,
    UnsupportedAreaError,
    extract_items,
    get_area_code,
    parse_salary_to_monthly,
    require_area_code,
    supported_cities,
)

# --------------------------------------------------------------------------
# 薪资解析
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1-1.5万", (10000, 15000)),          # 单位只写在后半段，前半段要继承
        ("1.5-2万", (15000, 20000)),
        ("8千-1.2万", (8000, 12000)),          # 两端单位不同
        ("6-8千", (6000, 8000)),
        ("1-1.5万·13薪", (10000, 15000)),      # 「13薪」不影响月薪区间
        ("15-25万/年", (12500, 20833)),        # 年包折算成月
        ("2万", (20000, 20000)),               # 单值
        ("8000-12000元/月", (8000, 12000)),
        ("1万-1.5万", (10000, 15000)),         # 两端都写单位
    ],
)
def test_薪资解析(raw, expected):
    assert parse_salary_to_monthly(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["面议", "", None, "保密", "200-300元/天", "50元/小时", "薪资面议"],
)
def test_无法按月薪表达的薪资一律留空(raw):
    """算不出来的宁可为空，也不换算成一个月薪假数据。"""
    assert parse_salary_to_monthly(raw) == (0, 0)


def test_高低价写反时自动纠正():
    assert parse_salary_to_monthly("2-1万") == (10000, 20000)


# --------------------------------------------------------------------------
# 城市编码
# --------------------------------------------------------------------------


def test_城市编码取自真实站点而不是猜测():
    """抽查几个交叉核对过的编码，防止有人随手改错。"""
    assert get_area_code("北京") == "010000"
    assert get_area_code("上海") == "020000"
    assert get_area_code("广州") == "030200"
    assert get_area_code("深圳") == "040000"
    assert get_area_code("长沙") == "190200"
    assert get_area_code("武汉") == "180200"


def test_已收录城市不少于二十个():
    assert len(supported_cities()) >= 20
    assert supported_cities() == sorted(supported_cities())


def test_未收录城市必须报错而不是回退():
    """回退的代价是把别的城市岗位记在目标城市名下，属于数据污染。"""
    with pytest.raises(UnsupportedAreaError):
        require_area_code("南昌")

    # 报错信息要能自解释：列出支持清单与补充方式
    with pytest.raises(UnsupportedCityError) as exc:
        require_area_code("南昌")
    assert "南昌" in str(exc.value)
    assert "长沙" in str(exc.value)
    assert "CITY_AREA_MAP" in str(exc.value)


def test_空城市必须报错():
    for empty in ("", "   ", None):
        with pytest.raises(UnsupportedAreaError):
            require_area_code(empty)


# --------------------------------------------------------------------------
# 响应结构解析
# --------------------------------------------------------------------------

ITEM_A = {
    "jobName": "Python 后端工程师",
    "companyName": "某某科技有限公司",
    "provideSalaryString": "1-1.5万",
    "workAreaString": "长沙-岳麓区",
    "workYearString": "3-4年经验",
    "degreeString": "本科",
    "jobHref": "https://jobs.51job.com/changsha-ylq/123456.html",
    "attributeText": "五险一金 Python FastAPI",
    "issueDateString": "2026-09-01",
}

ITEM_B = {
    "jobName": "Java 开发工程师",
    "companyName": "另一家公司",
    "provideSalaryString": "8千-1.2万·13薪",
    "workAreaString": "长沙-雨花区",
    "workYearString": "1-3年经验",
    "degreeString": "大专",
    "jobHref": "//jobs.51job.com/changsha-yhq/654321.html",
    "attributeText": "Java Spring",
}


def make_payload(*items):
    return json.dumps({"resultbody": {"job": {"items": list(items)}}}, ensure_ascii=False)


def test_解析标准响应():
    items = extract_items(make_payload(ITEM_A))
    assert isinstance(items, list) and len(items) == 1


def test_兼容items挂在resultbody下的变体():
    payload = json.dumps({"resultbody": {"items": [ITEM_A]}})
    assert len(extract_items(payload)) == 1


def test_兼容resultBody驼峰命名():
    payload = json.dumps({"resultBody": {"job": {"items": [ITEM_A]}}})
    assert len(extract_items(payload)) == 1


def test_结构不符时返回None以便与空结果区分():
    """返回 None 让调用方能区分「结构变了」与「这页真的没岗位」。"""
    assert extract_items(json.dumps({"status": "0", "message": "参数错误"})) is None
    assert extract_items("这不是 JSON") is None
    assert extract_items(None) is None
    assert extract_items("<html><body>验证码</body></html>") is None


# --------------------------------------------------------------------------
# 字段映射
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_字段映射与岗位模型口径一致():
    crawler = Job51Crawler()
    jobs = await crawler.parse_page(make_payload(ITEM_A, ITEM_B), "Python")

    assert len(jobs) == 2
    first = jobs[0]

    assert first["title"] == "Python 后端工程师"
    assert first["company_name"] == "某某科技有限公司"
    assert first["source_site"] == "51job"
    assert first["experience"] == "3-4年经验"
    assert first["education"] == "本科"
    assert first["min_salary"] == 10000
    assert first["max_salary"] == 15000
    assert "Python" in first["skills"]
    assert first["url"] == "https://jobs.51job.com/changsha-ylq/123456.html"


@pytest.mark.asyncio
async def test_相对协议链接补全为绝对地址():
    """job_checker 靠 url 核查岗位存活，没有绝对地址就无法核查。"""
    crawler = Job51Crawler()
    jobs = await crawler.parse_page(make_payload(ITEM_B), "Java")
    assert jobs[0]["url"] == "https://jobs.51job.com/changsha-yhq/654321.html"


@pytest.mark.asyncio
async def test_job_key_与统一指纹口径一致():
    crawler = Job51Crawler()
    jobs = await crawler.parse_page(make_payload(ITEM_A), "Python")
    first = jobs[0]
    assert first["job_key"] == generate_job_key(
        "51job", first["title"], first["company_name"], first["city"]
    )


@pytest.mark.asyncio
async def test_无标题的条目被丢弃():
    crawler = Job51Crawler()
    jobs = await crawler.parse_page(make_payload({"companyName": "只有公司"}), "Python")
    assert jobs == []


@pytest.mark.asyncio
async def test_指定城市时城市以调用方为准():
    """爬取任务的城市维度必须与用户选择一致，不能被页面里的地区覆盖。"""
    crawler = Job51Crawler()
    jobs = await crawler.parse_page(make_payload(ITEM_A), "长沙")
    assert jobs[0]["city"] == "长沙"


# --------------------------------------------------------------------------
# crawl() 端到端（假 Playwright）
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    """按顺序回放预设的 (状态码, 响应体)，超出范围时重复最后一页。"""

    def __init__(self, pages):
        self._pages = pages
        self._index = -1
        self.visited = []

    async def goto(self, url, timeout=None):
        self.visited.append(url)
        self._index += 1
        status, _ = self._pages[min(self._index, len(self._pages) - 1)]
        return FakeResponse(status)

    async def evaluate(self, expression):
        _, body = self._pages[min(self._index, len(self._pages) - 1)]
        return body


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    async def new_context(self, **kwargs):
        return self

    async def new_page(self):
        return self._page

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, page):
        self._page = page

    async def launch(self, **kwargs):
        return _FakeBrowser(self._page)


class _FakePlaywright:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture
def fake_browser(monkeypatch):
    """返回一个可配置的安装器，并让延时归零。"""
    monkeypatch.setattr(settings, "crawl_delay_min", 0.0)
    monkeypatch.setattr(settings, "crawl_delay_max", 0.0)

    def install(pages):
        page = FakePage(pages)
        pw_module = types.ModuleType("playwright")
        api_module = types.ModuleType("playwright.async_api")
        api_module.async_playwright = lambda: _FakePlaywright(page)
        pw_module.async_api = api_module
        monkeypatch.setitem(sys.modules, "playwright", pw_module)
        monkeypatch.setitem(sys.modules, "playwright.async_api", api_module)
        return page

    return install


@pytest.mark.asyncio
async def test_crawl_必须把抓到的岗位返回出去(fake_browser):
    page = fake_browser([(200, make_payload(ITEM_A, ITEM_B)), (200, make_payload())])

    results = await Job51Crawler().crawl("Python", "长沙")

    assert len(results) == 2
    assert results[0]["title"] == "Python 后端工程师"
    # 第二页空结果时必须停止翻页，而不是继续空转
    assert len(page.visited) == 2


@pytest.mark.asyncio
async def test_crawl_请求参数带上城市编码与关键词(fake_browser):
    page = fake_browser([(200, make_payload()), (200, make_payload())])

    await Job51Crawler().crawl("Python", "长沙")

    assert "jobArea=190200" in page.visited[0]
    assert "keyword=Python" in page.visited[0]
    assert "pageNum=1" in page.visited[0]


@pytest.mark.asyncio
async def test_crawl_翻页递增(fake_browser):
    page = fake_browser([
        (200, make_payload(ITEM_A)),
        (200, make_payload(ITEM_B)),
        (200, make_payload()),
    ])

    results = await Job51Crawler().crawl("Python", "长沙")

    assert len(results) == 2
    assert "pageNum=1" in page.visited[0]
    assert "pageNum=2" in page.visited[1]


@pytest.mark.asyncio
async def test_crawl_风控页面不得被当成无岗位(fake_browser):
    """验证码页要标记降速并停止，绝不能静默入库空结果。"""
    fake_browser([(200, "<html><body>请完成安全验证</body></html>")])

    results = await Job51Crawler().crawl("Python", "长沙")

    assert results == []


@pytest.mark.asyncio
async def test_crawl_429触发风控降速(fake_browser):
    fake_browser([(429, "")])

    results = await Job51Crawler().crawl("Python", "长沙")

    assert results == []


@pytest.mark.asyncio
async def test_crawl_未收录城市直接报错不发请求(fake_browser):
    page = fake_browser([(200, make_payload())])

    with pytest.raises(UnsupportedAreaError):
        await Job51Crawler().crawl("Python", "南昌")

    assert page.visited == []


@pytest.mark.asyncio
async def test_crawl_同页重复岗位被去重(fake_browser):
    fake_browser([
        (200, make_payload(ITEM_A, ITEM_A)),
        (200, make_payload()),
    ])

    results = await Job51Crawler().crawl("Python", "长沙")

    assert len(results) == 1


@pytest.mark.asyncio
async def test_crawl_结果能被重试包装透传(fake_browser):
    fake_browser([(200, make_payload(ITEM_A, ITEM_B)), (200, make_payload())])

    results = await Job51Crawler().crawl_with_retry("Python", "长沙")

    assert len(results) == 2
