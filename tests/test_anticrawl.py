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
    """风控后延迟应比基础延迟大，且随信号数**线性**放大。

    【为什么不再直接比较两次采样值】
    原断言 `more_slowed > slowed` 比较的是两次**独立随机采样**：
        signals=1 → delay ∈ [8, 32]；signals=2 → delay ∈ [16, 64]
    两区间重叠于 [16, 32] —— 第 1 次采到上端、第 2 次采到下端时断言即失败
    （蒙特卡洛 20 万次实测失败率 **3.94%**）。放大系数本身是对的，
    **错的是断言方式**：两个独立随机量之间不存在确定的序关系。

    【做法】沿用本项目既有的「用配置把随机区间压成点」手法
    （见 test_boss_crawler.py / test_job51_crawler.py 的 `crawl_delay_min=max=0.0`），
    把 min/max 与 factor 都设成单点，采样即成为确定值 ——
    于是断言检验的是「放大级别」这一设计意图，而不是两次运气。
    """
    # 区间压成点：base 恒为 1.5；factor 恒为 12 × 信号数
    monkeypatch.setattr(settings, "crawl_delay_min", 1.5)
    monkeypatch.setattr(settings, "crawl_delay_max", 1.5)
    monkeypatch.setattr(settings, "crawl_adaptive_delay_factor", (12, 12))

    c = _DummyCrawler()

    baseline = c._adaptive_delay()
    assert baseline == pytest.approx(1.5), "无风控时应为基础延迟"

    BaseCrawler.mark_blocked()                      # 累计信号 = 1
    slowed = c._adaptive_delay()
    assert slowed == pytest.approx(1.5 * 12), "倍率应为 factor × 1"
    assert slowed > baseline

    BaseCrawler.mark_blocked()                      # 累计信号 = 2
    more_slowed = c._adaptive_delay()
    assert more_slowed == pytest.approx(1.5 * 24), "倍率应为 factor × 2"
    assert more_slowed > slowed
    # 设计意图：倍率随信号数**线性**递增（信号 1→2 时延迟恰好翻倍）
    assert more_slowed == pytest.approx(slowed * 2)


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