"""反爬增强测试：风控检测 / 自适应降速 / 完整请求头伪装。"""
import pytest

from app.config import settings
from app.crawlers.base import BaseCrawler


class _ResetBlocked:
    """每个用例前重置类级风控计数。"""

    def __call__(self):
        BaseCrawler._blocked_signals = 0

    def __enter__(self):
        BaseCrawler._blocked_signals = 0
        return self

    def __exit__(self, *args):
        BaseCrawler._blocked_signals = 0


@pytest.fixture(autouse=True)
def _reset_blocked():
    BaseCrawler._blocked_signals = 0
    yield
    BaseCrawler._blocked_signals = 0


class _DummyCrawler(BaseCrawler):
    def __init__(self):
        super().__init__("dummy")

    async def crawl(self, keyword: str, city: str = ""):
        return []

    async def parse_page(self, page_content, keyword: str):
        return []


def test_detect_blocked_by_status():
    assert BaseCrawler.detect_blocked("", 429) is True


def test_detect_blocked_by_keywords():
    assert BaseCrawler.detect_blocked("请拖动滑块完成验证", 200) is True
    assert BaseCrawler.detect_blocked("captcha required", 200) is True
    assert BaseCrawler.detect_blocked("正常岗位页面", 200) is False
    assert BaseCrawler.detect_blocked("", 200) is False


def test_mark_blocked_increments():
    BaseCrawler.mark_blocked()
    BaseCrawler.mark_blocked()
    assert BaseCrawler._blocked_signals == 2


def test_adaptive_delay_larger_after_blocked(monkeypatch):
    """风控后延迟应比基础延迟大（倍率 >1 且随计数放大）。"""
    monkeypatch.setattr(settings, "crawl_delay_min", 1)
    monkeypatch.setattr(settings, "crawl_delay_max", 2)
    monkeypatch.setattr(settings, "crawl_adaptive_delay_factor", (8, 16))

    c = _DummyCrawler()
    baseline = c._adaptive_delay()
    assert 1 <= baseline <= 2

    BaseCrawler.mark_blocked()
    slowed = c._adaptive_delay()
    assert slowed > baseline

    BaseCrawler.mark_blocked()
    more_slowed = c._adaptive_delay()
    assert more_slowed > slowed


def test_random_headers_complete():
    """请求头应包含核心浏览器指纹字段，且 UA 与头集合配套。"""
    c = _DummyCrawler()
    headers = c.get_random_headers()
    for field in ["User-Agent", "Accept", "Accept-Language", "Sec-Fetch-Mode",
                  "Sec-Fetch-Site", "Upgrade-Insecure-Requests", "Sec-CH-UA"]:
        assert field in headers, f"缺少请求头 {field}"
    assert headers["Accept"].startswith("text/html")
    ua = headers["User-Agent"]
    # UA 是移动端则 Sec-CH-UA-Mobile 应为 ?1，桌面端为 ?0
    if "iPhone" in ua:
        assert headers["Sec-CH-UA-Mobile"] == "?1"
    else:
        assert headers["Sec-CH-UA-Mobile"] == "?0"