"""`type: api` 的三道护栏（回归 qoder 第 4 轮审计 D3 / D4 / D5）。

## 背景

2026-11-11 合并了一份 814 行的改动：让声明式规格引擎支持 JSON 接口源。
qoder 只读审计后发现三个缺陷，全部由集成端修复，这里用测试锁住。

三件事分开说，因为它们的后果不一样：

| 编号 | 缺陷 | 后果 |
|---|---|---|
| D3 | `SpecPayloadError` 没进「不重试」名单 | 拿到 WAF 页后连打 4 轮、退避约 14 秒，日志还写成"爬取失败" |
| D4 | JSON 正文被喂给为 HTML 设计的风控检测 | **把成功当失败**：含「风控/校验」字样的岗位会整页丢弃 |
| D5 | `_parse_api_page` 有两条静默 0 条路径 | "接口没数据"和"字段路径全错"在返回值上完全一样，查不出来 |

D4 最严重：它是唯一一个**会丢数据**的缺陷，而且丢得毫无痕迹。
"""
import json

import pytest

from app.crawlers.base import BaseCrawler
from app.crawlers.spec_crawler import SpecCrawler, SpecPayloadError
from app.crawlers.spec_schema import CrawlSpec


def make_api_spec(**over):
    """最小可用的 `type: api` 规格。"""
    data = {
        "site": "api_demo",
        "label": "JSON 源演示",
        "type": "api",
        "request": {"url": "https://example.com/api?page={page}", "timeout": 10},
        "list": {
            "path": "items",
            "fields": {
                "title": "json:title",
                "company": "json:company",
                "city": "json:city",
            },
        },
        "pagination": {"type": "page_param", "start": 1, "max_pages": 1},
        "limits": {"max_items": 10},
    }
    data.update(over)
    return CrawlSpec.from_dict(data)


# ---------------------------------------------------------------------------
# D3：确定性失败不进入重试循环
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_确定性失败不进入重试循环():
    """`SpecPayloadError` 是"再试一次也一样"的失败，不该被重试 4 轮。

    生产表现：51job 对非浏览器客户端返回 HTTP 200 + WAF HTML，
    重试 4 次 = 多等 2+4+8 秒，且日志写成"爬取失败"，掩盖真实原因。
    """

    class _Crawler(BaseCrawler):
        calls = 0

        async def crawl(self, keyword: str, city: str = ""):
            type(self).calls += 1
            raise SpecPayloadError("[api_demo] 响应不是合法 JSON")

        async def parse_page(self, page_content, keyword: str):
            return []

    with pytest.raises(SpecPayloadError):
        await _Crawler("api_demo").crawl_with_retry("python")

    assert _Crawler.calls == 1, "确定性失败不应重试"


# ---------------------------------------------------------------------------
# D4：api 源不因岗位正文里的关键词被判风控（最严重的一条）
# ---------------------------------------------------------------------------

def test_风控信号表确实会命中岗位正文():
    """先证明缺陷是真实存在的 —— 这句话确实会被判成"站点风控"。

    如果哪天信号表改准了，这条会红，那就该去改 D4 的修复（而不是删这条测试）。
    """

    class _Minimal(BaseCrawler):
        async def crawl(self, keyword: str, city: str = ""):
            return []

        async def parse_page(self, page_content, keyword: str):
            return []

    probe = _Minimal("x")
    assert probe.detect_blocked("负责风控模型的数据校验") is True
    assert probe.detect_blocked("代码审计与风险识别") is True


@pytest.mark.asyncio
async def test_api源不因岗位正文含风险词而整页丢弃(monkeypatch):
    """含「风控」字样的**岗位描述**不能被当成站点在拦你。

    HTML 页面里前 4000 字符是 head/nav/CSS，岗位正文进不了判定窗口；
    JSON 的第一条记录正文就在最前面，同样 4000 字符的窗口内容密度完全不同。
    所以 api 源只用 status_code + 结构错误判风控，不用关键词。
    """
    crawler = SpecCrawler(make_api_spec())
    payload = json.dumps(
        {"items": [{"title": "负责风控模型的数据校验", "company": "某公司", "city": "北京"}]},
        ensure_ascii=False,
    )

    async def _fake_fetch(url: str) -> str:
        return payload

    monkeypatch.setattr(crawler, "_fetch", _fake_fetch)

    rows = await crawler.crawl(keyword="python")

    assert len(rows) == 1, "含「风控」字样的岗位被误判成站点风控，整页数据丢了"


# ---------------------------------------------------------------------------
# D5：静默 0 条的两条路径
# ---------------------------------------------------------------------------

def test_空数组是合法结果不抛错():
    """"这一页确实没有岗位"是正常结果，不能当错误。

    （不能因为讨厌"0 条"就把空数组也判成错误 —— 那会让翻到最后一页必然失败。）
    """
    crawler = SpecCrawler(make_api_spec())
    assert crawler._parse_api_page({"items": []}) == []


def test_有元素但全部映射为空必须报错():
    """数组里有 3 条，一条都没映射出来 —— 这是字段路径全错了，不是没数据。

    修之前的行为：静默返回 `[]`，任务显示 COMPLETED / 0 条，
    看起来像"这个城市没岗位"，实际是 YAML 写错了。
    """
    crawler = SpecCrawler(make_api_spec())
    payload = {"items": [{"wrong_key": 1}, {"wrong_key": 2}, {"wrong_key": 3}]}

    with pytest.raises(SpecPayloadError, match="全部映射为空"):
        crawler._parse_api_page(payload)


def test_部分映射成功时不报错():
    """只要有一条能映射出来，就说明字段路径是对的，其余条目不完整属正常。"""
    crawler = SpecCrawler(make_api_spec())
    payload = {"items": [{"title": "后端工程师", "company": "A"}, {"wrong_key": 1}]}

    rows = crawler._parse_api_page(payload)

    assert len(rows) == 1
    assert rows[0]["title"] == "后端工程师"
