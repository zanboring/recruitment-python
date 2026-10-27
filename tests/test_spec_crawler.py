"""声明式爬虫规格（CrawlSpec）与通用执行器（SpecCrawler）的单元测试。

全部用本地 HTML，不联网 —— 网络与反爬永远测不稳定，
这里只保证「规格解析正确」与「字段抽取正确」两件事。
"""
import pytest

from app.crawlers.spec_crawler import CrawlBlockedError, SpecCrawler
from app.crawlers.spec_schema import (
    CrawlSpec,
    FieldSpec,
    SpecError,
    load_all_specs,
    load_spec_file,
)

SAMPLE_HTML = """
<html><body>
  <div class="quote">
    <span class="text">“第一句名言。”</span>
    <small class="author">作者甲</small>
    <a href="/author/1">详情</a>
    <div class="tags">
      <a class="tag">勇气</a><a class="tag">成长</a>
    </div>
  </div>
  <div class="quote">
    <span class="text">“第二句名言。”</span>
    <small class="author">作者乙</small>
    <a href="/author/2">详情</a>
    <div class="tags"><a class="tag">坚持</a></div>
  </div>
</body></html>
"""


def make_spec(**over):
    data = {
        "site": "demo_site",
        "label": "演示",
        "url": "https://example.com/list?page={page}",
        "list": {
            "container": "div.quote",
            "fields": {
                "text": "span.text::text",
                "author": "small.author::text",
                "link": "a::attr(href)",
                "tags": "div.tags a.tag::text[]",
            },
        },
        "pagination": {"type": "page_param", "start": 1, "max_pages": 2},
        "limits": {"max_items": 10},
    }
    data.update(over)
    return CrawlSpec.from_dict(data)


# --------------------------------------------------------------- 规格解析


def test_load_bundled_demo_spec():
    """仓库自带的示例规格必须能加载 —— 它同时是文档。"""
    specs = load_all_specs()
    assert "quotes_demo" in specs
    spec = specs["quotes_demo"]
    assert spec.list_container == "div.quote"
    assert spec.max_pages >= 1
    assert any(f.name == "tags" for f in spec.fields)


def test_spec_rejects_missing_url():
    with pytest.raises(SpecError, match="url"):
        CrawlSpec.from_dict({
            "site": "x1",
            "list": {"container": "div", "fields": {"a": "span::text"}},
        })


def test_spec_rejects_missing_container():
    with pytest.raises(SpecError, match="container"):
        CrawlSpec.from_dict({
            "site": "x1",
            "url": "https://e.com/{page}",
            "list": {"fields": {"a": "span::text"}},
        })


def test_spec_rejects_bad_site_id():
    """站点标识会被用作平台 key，必须限制字符集。"""
    with pytest.raises(SpecError, match="site 标识不合法"):
        CrawlSpec.from_dict({
            "site": "Bad Site!",
            "url": "https://e.com/{page}",
            "list": {"container": "div", "fields": {"a": "span::text"}},
        })


def test_spec_rejects_bad_field_suffix():
    """字段表达式语法错误要在加载期炸，而不是跑到那一页才发现。"""
    with pytest.raises(SpecError, match="后缀无法识别"):
        CrawlSpec.from_dict({
            "site": "x1",
            "url": "https://e.com/{page}",
            "list": {"container": "div", "fields": {"a": "span::nonsense"}},
        })


def test_broken_file_does_not_kill_other_specs(tmp_path):
    """一个文件写错不能让整批消失（否则症状是「所有爬虫都没了」）。"""
    good = tmp_path / "good.yaml"
    good.write_text(
        "site: good_one\nlabel: ok\nurl: https://e.com/{page}\n"
        "list:\n  container: div.item\n  fields:\n    a: span::text\n",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.yaml"
    bad.write_text("site: bad_one\nurl: ''\n", encoding="utf-8")

    specs = load_all_specs(tmp_path)
    assert list(specs) == ["good_one"]


# --------------------------------------------------------------- 字段抽取


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("span.text::text", "“第一句名言。”"),
        ("small.author::text", "作者甲"),
        ("a::attr(href)", "/author/1"),
        ("div.tags a.tag::text[]", ["勇气", "成长"]),
    ],
)
def test_extract_variants(expr, expected):
    spec = make_spec(list={
        "container": "div.quote",
        "fields": {"v": expr},
    })
    crawler = SpecCrawler(spec)
    rows = crawler.parse_page(SAMPLE_HTML)
    assert rows[0]["v"] == expected


def test_extract_regex_postprocess():
    """正则后处理：从 "￥15-25K" 里取两个数字。"""
    html = '<div class="i"><span class="s">￥15-25K</span></div>'
    spec = make_spec(list={
        "container": "div.i",
        "fields": {"lo": r"span.s::text|regex:(\d+)-\d+"},
    })
    rows = SpecCrawler(spec).parse_page(html)
    assert rows[0]["lo"] == "15"


def test_missing_field_returns_none_not_crash():
    spec = make_spec(list={
        "container": "div.quote",
        "fields": {"nope": "span.absent::text"},
    })
    rows = SpecCrawler(spec).parse_page(SAMPLE_HTML)
    # 其余字段都为空的整行会被过滤，这里断言不抛异常即可
    assert isinstance(rows, list)


def test_empty_html_returns_empty_list():
    assert SpecCrawler(make_spec()).parse_page("") == []


def test_no_match_warns_but_returns_empty():
    """选择器不匹配时返回空列表（站点改版的典型症状），不能抛异常。"""
    assert SpecCrawler(make_spec()).parse_page("<html><body>nothing</body></html>") == []


# --------------------------------------------------------------- URL 渲染


def test_render_url_substitutes_params():
    spec = make_spec()
    assert spec.render_url({"page": 3}) == "https://example.com/list?page=3"


def test_render_url_keeps_unknown_placeholder():
    """缺参数时保留占位符并告警，不静默拼出一个错 URL。"""
    spec = make_spec(url="https://e.com/s?q={keyword}&page={page}")
    assert spec.render_url({"page": 1}) == "https://e.com/s?q={keyword}&page=1"


def test_build_params_injects_keyword_and_city():
    spec = make_spec()
    params = spec.build_params(keyword="Python", city="北京")
    assert params["keyword"] == "Python"
    assert params["city"] == "北京"
    assert params["page"] == 1


# --------------------------------------------------------------- 风控处理


async def test_crawl_raises_blocked_when_captcha_detected(monkeypatch):
    """命中验证码页时必须抛 CrawlBlockedError，不能当成 0 条结果。"""
    spec = make_spec()
    crawler = SpecCrawler(spec)

    async def fake_fetch(url):
        return "<html><body>请完成安全验证 滑块验证</body></html>"

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)

    with pytest.raises(CrawlBlockedError, match="风控"):
        await crawler.crawl(keyword="x")


async def test_crawl_parses_pages_and_respects_max_items(monkeypatch):
    """两页各 2 条，max_items=3 时应被截断为 3。"""
    spec = make_spec(limits={"max_items": 3}, pagination={"type": "page_param", "max_pages": 5})
    crawler = SpecCrawler(spec)

    async def fake_fetch(url):
        return SAMPLE_HTML

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    rows = await crawler.crawl(keyword="x")
    assert len(rows) == 3


async def test_next_link_pagination_stops_when_no_next(monkeypatch):
    """next_link 模式下没有下一页就应停，不能无限循环。"""
    spec = make_spec(
        pagination={"type": "next_link", "max_pages": 10, "next_selector": "a.next"},
    )
    crawler = SpecCrawler(spec)

    async def fake_fetch(url):
        return SAMPLE_HTML          # 里面没有 a.next

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    rows = await crawler.crawl()
    assert len(rows) == 2           # 只解析了第一页
