"""BossCrawler.crawl() 的端到端回归测试（完全离线）。

为什么需要这个文件：
`tests/test_crawler.py` 覆盖的是「重试包装」与「服务层入库」，它把
`crawl_with_retry` 整个替换成桩函数，因此**从未真正执行过 BossCrawler.crawl()**。
结果是 crawl() 末尾缺失 `return results` 这种致命缺陷能一路绿灯通过：
抓到的岗位被静默丢弃，`crawl_with_retry` 的 `results or []` 永远得到空列表，
任务状态却是 COMPLETED。

这里用假的 Playwright 模块驱动真实的 crawl() 循环，锁死「必须把岗位返回出去」。
"""
import sys
import types

import pytest

from app.config import settings
from app.crawlers.boss import BossCrawler
from app.crawlers.cleaner import generate_job_key

SAMPLE_HTML = """
<html><body>
  <div class="job-card">
    <a href="/job_detail/abc123.html">
      <span class="job-name">Python 后端工程师</span>
      <span class="salary">15K-25K</span>
    </a>
    <span class="company-name">某某科技有限公司</span>
    <span class="job-area">长沙·岳麓区</span>
    <div class="tags">
      <span class="tag">3-5年</span>
      <span class="tag">本科</span>
      <span class="tag">Python</span>
      <span class="tag">FastAPI</span>
    </div>
  </div>
  <div class="job-card">
    <a href="https://www.zhipin.com/job_detail/def456.html">
      <span class="job-name">Java 开发工程师</span>
      <span class="salary">20K-30K</span>
    </a>
    <span class="company-name">另一家公司</span>
    <span class="job-area">长沙·雨花区</span>
    <div class="tags">
      <span class="tag">1-3年</span>
      <span class="tag">大专</span>
      <span class="tag">Java</span>
    </div>
  </div>
</body></html>
"""


class FakePage:
    """第一页返回岗位卡片；从第二页起找不到卡片，触发收尾分支。"""

    def __init__(self, html: str):
        self.html = html
        self.visited: list[str] = []

    async def goto(self, url, timeout=None):
        self.visited.append(url)

    async def wait_for_selector(self, selector, timeout=None):
        # 真实实现逐条尝试选择器，全部失败才抛 TimeoutError
        if "page=2" in self.visited[-1]:
            raise TimeoutError("no job card on page 2")
        return selector

    async def content(self):
        return self.html


class _FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    async def new_context(self, **kwargs):
        return _FakeContext(self._page)

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


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
    """注入假的 playwright 模块，并让延时归零（避免真的睡十几秒）。"""
    page = FakePage(SAMPLE_HTML)

    pw_module = types.ModuleType("playwright")
    api_module = types.ModuleType("playwright.async_api")
    api_module.async_playwright = lambda: _FakePlaywright(page)
    pw_module.async_api = api_module

    monkeypatch.setitem(sys.modules, "playwright", pw_module)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api_module)

    monkeypatch.setattr(settings, "crawl_delay_min", 0.0)
    monkeypatch.setattr(settings, "crawl_delay_max", 0.0)
    return page


@pytest.mark.asyncio
async def test_crawl_必须把抓到的岗位返回出去(fake_browser):
    """回归：crawl() 曾经在 try 块结束后没有 return，抓到数据也全部丢弃。"""
    results = await BossCrawler().crawl("Python", "长沙")

    assert results is not None, "crawl() 返回了 None —— 抓取结果被丢弃"
    assert len(results) == 2

    first = results[0]
    assert first["title"] == "Python 后端工程师"
    assert first["salary"] == "15K-25K"
    assert first["company_name"] == "某某科技有限公司"
    assert first["city"] == "长沙·岳麓区"
    assert first["experience"] == "3-5年"
    assert first["education"] == "本科"
    assert first["skills"] == "Python,FastAPI"
    assert first["source_site"] == "boss"


@pytest.mark.asyncio
async def test_crawl_字段与岗位模型口径一致(fake_browser):
    """产出字段必须与 job_key 生成口径、入库字段对齐。"""
    results = await BossCrawler().crawl("Python", "长沙")
    first = results[0]

    assert first["job_key"] == generate_job_key(
        "boss", first["title"], first["company_name"], first["city"]
    )
    # 相对链接补全为绝对链接，否则 job_checker 无法核查存活
    assert first["url"] == "https://www.zhipin.com/job_detail/abc123.html"
    # 已是绝对链接的原样保留
    assert results[1]["url"] == "https://www.zhipin.com/job_detail/def456.html"


@pytest.mark.asyncio
async def test_crawl_按城市编码拼请求(fake_browser):
    """未收录城市应报错，已收录城市要落到正确的 city 参数。"""
    await BossCrawler().crawl("Python", "长沙")
    assert "city=" in fake_browser.visited[0]
    assert "query=Python" in fake_browser.visited[0]


@pytest.mark.asyncio
async def test_crawl_未收录城市直接报错(fake_browser):
    """未收录城市必须抛错，不能静默回退到别的城市。"""
    with pytest.raises(Exception):
        await BossCrawler().crawl("Python", "不存在的城市")


@pytest.mark.asyncio
async def test_crawl_结果能被重试包装透传(fake_browser):
    """真实链路：crawl_with_retry 必须把 crawl() 的结果原样带出来。

    这正是原先断掉的地方——crawl() 返回 None 时这里只会得到 []，
    而服务层会当作「本次没爬到岗位」并标记任务为 COMPLETED。
    """
    results = await BossCrawler().crawl_with_retry("Python", "长沙")
    assert len(results) == 2
