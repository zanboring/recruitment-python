"""声明式爬虫的 SSRF 闸门（回归 qoder 审计 P0-3，2026-11-08）。

## 为什么需要这组测试

`SpecCrawler` 抓的是**任意站点**，而目标 URL 来自页面里的 `href`
（`urljoin(page_url, href)`）—— 这是第三方可影响的输入：谁能控制被爬页面，
谁就能让爬虫去请求任意地址。

原来的写法是 `httpx.AsyncClient(follow_redirects=True)`，这是典型的**事后校验**：
重定向已经打到 `169.254.169.254` / `127.0.0.1` 之后，才拿到"最终 URL"去判 ——
那时响应都已经回来了，判等于没判。

项目里本来就有 `app/utils/url_safety.py`（逐跳校验 + 强制 `follow_redirects=False`），
但它原先只被「链接导入」「存活核查」两处引用，`app/crawlers/` 下一处都没有。

## 这里锁三件事

1. `_fetch_plain` 走 `safe_get`：内网目标在**请求发出之前**就被拒
2. `crawl_with_retry` 对 `UnsafeURLError` **不重试**：重试不会变好，只白等退避
3. 详情链接指向内网时**停止整批详情抓取**，而不是当成"这一条失败"继续替对方探测

第 3 条是刻意的：单条 404 可以跳过，但"页面里的链接指向内网"说明页面被投毒，
继续抓剩下的链接等于继续替攻击者扫内网。
"""
import pytest

from app.crawlers.base import BaseCrawler
from app.crawlers.spec_crawler import SpecCrawler
from app.crawlers.spec_schema import CrawlSpec
from app.utils.url_safety import UnsafeURLError


def make_spec(**over):
    data = {
        "site": "demo_site",
        "label": "演示",
        "url": "https://example.com/list?page={page}",
        "list": {
            "container": "div.quote",
            "fields": {"text": "span.text::text", "link": "a::attr(href)"},
        },
        "pagination": {"type": "page_param", "start": 1, "max_pages": 1},
        "limits": {"max_items": 5},
    }
    data.update(over)
    return CrawlSpec.from_dict(data)


# ---------------------------------------------------------------------------
# 一、请求发出前就必须拒
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plain_fetch_refuses_loopback_before_request():
    """回环地址：本地管理接口常挂在这上面，是最容易被 SSRF 打穿的目标。"""
    crawler = SpecCrawler(make_spec())
    with pytest.raises(UnsafeURLError):
        await crawler._fetch_plain("http://127.0.0.1:8000/admin")


@pytest.mark.asyncio
async def test_plain_fetch_refuses_metadata_endpoint():
    """169.254.169.254 是云环境的实例凭据端点 —— SSRF 的经典目标。"""
    crawler = SpecCrawler(make_spec())
    with pytest.raises(UnsafeURLError):
        await crawler._fetch_plain("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_plain_fetch_refuses_private_range():
    """RFC1918 私网段同样要拒。"""
    crawler = SpecCrawler(make_spec())
    with pytest.raises(UnsafeURLError):
        await crawler._fetch_plain("http://10.0.0.1/internal")


@pytest.mark.asyncio
async def test_plain_fetch_refuses_non_http_scheme():
    """非 http/https 一律拒（file:// 能读本机文件）。"""
    crawler = SpecCrawler(make_spec())
    with pytest.raises(UnsafeURLError):
        await crawler._fetch_plain("file:///C:/Windows/win.ini")


# ---------------------------------------------------------------------------
# 二、安全拒绝不进重试循环
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_wrapper_does_not_retry_unsafe_target():
    """一次就抛，且 crawl 只被调用一次。

    重试 3 次意味着白等 2+4 秒退避，还会把日志写成"爬取失败"掩盖真实原因
    （目标是内网，换几次都一样）。
    """

    class _Crawler(BaseCrawler):
        calls = 0

        async def crawl(self, keyword: str, city: str = ""):
            type(self).calls += 1
            raise UnsafeURLError("目标解析到内网地址")

        async def parse_page(self, page_content, keyword: str):
            return []

    with pytest.raises(UnsafeURLError):
        await _Crawler("unsafe_demo").crawl_with_retry("python", "北京")

    assert _Crawler.calls == 1, "不安全目标不应进入重试循环"


# ---------------------------------------------------------------------------
# 三、详情链接指向内网 → 停止整批，而不是逐条"失败"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detail_link_to_intranet_stops_the_batch():
    spec = make_spec(
        detail={
            "enabled": True,
            "link_field": "link",
            "fields": {"desc": "p::text"},
            "max_items": 3,
        }
    )
    crawler = SpecCrawler(spec)

    async def _refuse(url: str):
        raise UnsafeURLError("详情页解析到内网地址")

    crawler._fetch = _refuse
    batch = [{"link": "/detail/1"}, {"link": "/detail/2"}]

    await crawler._enrich_batch(batch, "https://example.com/list")

    assert crawler._detail_stopped is True
    assert crawler._detail_requests == 1, "第一条被拒后就该停，不该继续请求第二条"

    crawler._finalize_detail_notes()
    joined = " ".join(crawler.detail_notes)
    assert "不安全" in joined, f"停止原因必须写进任务说明，实际：{joined}"
