"""声明式爬虫规格（CrawlSpec）与通用执行器（SpecCrawler）的单元测试。

全部用本地 HTML，不联网 —— 网络与反爬永远测不稳定，
这里只保证「规格解析正确」与「字段抽取正确」两件事。
"""
import json

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


# --------------------------------------------------------------- 详情页抓取
#
# 这一组的重点是**请求量**：详情页最容易把「抓一个列表」变成「抓 N 个页面」，
# 所以断言都落在「实际发出了几个请求」上，而不是「结果里有没有这个字段」。

DETAIL_LIST_HTML = """
<html><body>
  <div class="item">
    <a class="title" href="/job/1">岗位一</a><span class="company">列表公司一</span>
  </div>
  <div class="item">
    <a class="title" href="/job/2">岗位二</a><span class="company">列表公司二</span>
  </div>
  <div class="item">
    <a class="title" href="/job/3">岗位三</a><span class="company">列表公司三</span>
  </div>
</body></html>
"""

LIST_URL = "https://example.com/list"


def detail_html(index: int) -> str:
    return (
        f"<html><body><h1>岗位{index}</h1>"
        f'<div class="company">详情公司{index}</div>'
        f'<div class="desc">岗位{index}的详细描述</div>'
        "</body></html>"
    )


def make_detail_spec(detail=None, **over):
    data = {
        "site": "detail_site",
        "label": "详情演示",
        "url": LIST_URL,
        "list": {
            "container": "div.item",
            "fields": {
                "title": "a.title::text",
                "company": "span.company::text",
                "link": "a.title::attr(href)",
            },
        },
        # 详情页：company 与列表同名（应被覆盖），description 只有详情页有
        "detail": detail if detail is not None else {
            "enabled": True,
            "link_field": "link",
            "max_items": 5,
            "fields": {
                "company": "div.company::text",
                "description": "div.desc::text",
            },
        },
        "pagination": {"type": "none"},
        "limits": {"max_items": 10},
    }
    data.update(over)
    return CrawlSpec.from_dict(data)


class FetchRecorder:
    """``_fetch`` 的替身：记录每一次请求，并按 URL 返回预置 HTML。

    断言直接落在 ``calls`` 上 —— 「还能返回数据」不代表「没有继续请求」，
    风控那条只能靠请求计数证明。
    """

    def __init__(self, list_html, detail_pages=None, fail_urls=(), blocked_urls=()):
        self.list_html = list_html
        self.detail_pages = detail_pages or {}
        self.fail_urls = set(fail_urls)
        self.blocked_urls = set(blocked_urls)
        self.calls = []

    async def __call__(self, url):
        self.calls.append(url)
        if url in self.blocked_urls:
            return "<html><body>请完成安全验证（滑块）</body></html>"
        if url in self.fail_urls:
            raise TimeoutError(f"timeout: {url}")
        return self.detail_pages.get(url, self.list_html)

    @property
    def detail_calls(self):
        return [u for u in self.calls if "/job/" in u]


def all_detail_pages():
    return {f"{LIST_URL.replace('/list', '')}/job/{i}": detail_html(i) for i in (1, 2, 3)}


async def test_detail_fields_override_same_name_and_keep_list_values(monkeypatch):
    """详情页字段覆盖列表同名字段；详情页取不到的一律保留列表原值。"""
    crawler = SpecCrawler(make_detail_spec())
    rec = FetchRecorder(DETAIL_LIST_HTML, detail_pages=all_detail_pages())
    monkeypatch.setattr(crawler, "_fetch", rec)

    rows = await crawler.crawl()

    assert len(rows) == 3
    assert rows[0]["title"] == "岗位一"            # 列表页字段保留
    assert rows[0]["company"] == "详情公司1"        # 同名字段被详情页覆盖
    assert rows[0]["description"] == "岗位1的详细描述"


async def test_detail_relative_link_is_joined_to_absolute_url(monkeypatch):
    """列表里的 href 常是 ``/job/1``，必须 urljoin 成绝对地址再请求。"""
    crawler = SpecCrawler(make_detail_spec())
    rec = FetchRecorder(DETAIL_LIST_HTML, detail_pages=all_detail_pages())
    monkeypatch.setattr(crawler, "_fetch", rec)

    await crawler.crawl()

    assert "https://example.com/job/1" in rec.detail_calls
    assert "/job/1" not in rec.calls             # 绝对不能拿相对路径直接发请求


async def test_detail_requests_capped_by_detail_max_items(monkeypatch):
    """detail.max_items=2 时，第 3 条不再抓详情（保留列表页字段）。"""
    spec = make_detail_spec(detail={
        "enabled": True, "link_field": "link", "max_items": 2,
        "fields": {"description": "div.desc::text"},
    })
    crawler = SpecCrawler(spec)
    rec = FetchRecorder(DETAIL_LIST_HTML, detail_pages=all_detail_pages())
    monkeypatch.setattr(crawler, "_fetch", rec)

    rows = await crawler.crawl()

    assert len(rows) == 3
    assert len(rec.detail_calls) == 2
    assert rows[2]["company"] == "列表公司三"
    assert "description" not in rows[2]


async def test_detail_requests_capped_by_total_max_items(monkeypatch):
    """总上限也管详情：limits.max_items=1 时只允许 1 个详情请求。"""
    spec = make_detail_spec(limits={"max_items": 1})
    crawler = SpecCrawler(spec)
    rec = FetchRecorder(DETAIL_LIST_HTML, detail_pages=all_detail_pages())
    monkeypatch.setattr(crawler, "_fetch", rec)

    rows = await crawler.crawl()

    assert len(rows) == 1
    assert len(rec.detail_calls) == 1


async def test_detail_request_throttled_by_interval(monkeypatch):
    """每条详情请求前都要等 ``_interval()`` —— 不因"就三条"跳过。"""
    crawler = SpecCrawler(make_detail_spec())
    rec = FetchRecorder(DETAIL_LIST_HTML, detail_pages=all_detail_pages())
    monkeypatch.setattr(crawler, "_fetch", rec)

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await crawler.crawl()

    assert len(sleeps) == len(rec.detail_calls) == 3


async def test_single_detail_failure_keeps_that_row_and_others_work(monkeypatch):
    """单条详情超时只影响这一条，其余照常补齐，失败条数进说明条目。"""
    crawler = SpecCrawler(make_detail_spec())
    rec = FetchRecorder(
        DETAIL_LIST_HTML,
        detail_pages=all_detail_pages(),
        fail_urls={"https://example.com/job/2"},
    )
    monkeypatch.setattr(crawler, "_fetch", rec)

    rows = await crawler.crawl()

    assert len(rows) == 3
    assert rows[1]["company"] == "列表公司二"     # 失败条目保留列表值
    assert "description" not in rows[1]
    assert rows[2]["description"] == "岗位3的详细描述"
    assert any("失败 1 条" in n for n in crawler.detail_notes)


async def test_blocked_detail_page_stops_further_requests(monkeypatch):
    """详情页命中风控必须立即停手（不是跳过这一条接着打），列表数据仍返回。"""
    crawler = SpecCrawler(make_detail_spec())
    rec = FetchRecorder(
        DETAIL_LIST_HTML,
        detail_pages=all_detail_pages(),
        blocked_urls={"https://example.com/job/1"},
    )
    monkeypatch.setattr(crawler, "_fetch", rec)

    rows = await crawler.crawl()

    # 列表页 1 次 + 详情页 1 次，第 2/3 条详情绝不能再发
    assert len(rec.calls) == 2
    assert len(rec.detail_calls) == 1
    assert len(rows) == 3
    assert rows[0]["company"] == "列表公司一"
    assert any("停止" in n for n in crawler.detail_notes)


async def test_no_detail_config_fetches_only_list(monkeypatch):
    """未配 detail 的既有规格行为零变化：只请求列表页、不加任何字段。"""
    crawler = SpecCrawler(make_spec())
    rec = FetchRecorder(SAMPLE_HTML)
    monkeypatch.setattr(crawler, "_fetch", rec)

    rows = await crawler.crawl()

    assert len(rec.calls) == 2                  # page_param max_pages=2
    assert not rec.detail_calls
    assert set(rows[0]) == {"text", "author", "link", "tags", "_target"}
    assert crawler.detail_notes == []


# ------------------------------------------------ 详情字段的加载期校验


def test_field_map_may_reference_detail_fields_when_configured():
    """detail 三要素齐全时，引用 detail.fields 应当通过加载期校验。"""
    spec = make_detail_spec(mapping={
        "target": "job",
        "field_map": {"title": "title", "company_name": "company", "description": "description"},
    })
    assert spec.target == "job"
    assert spec.detail_max_items == 5


def test_field_map_rejects_detail_fields_when_detail_disabled():
    """detail.enabled=false 却引用 detail.* → 报错（不能静默取空值）。"""
    with pytest.raises(SpecError, match="不可用"):
        make_detail_spec(
            detail={"enabled": False, "link_field": "link", "max_items": 5,
                    "fields": {"description": "div.desc::text"}},
            mapping={"target": "job",
                     "field_map": {"title": "title", "description": "description"}},
        )


def test_field_map_rejects_detail_fields_when_max_items_zero():
    """detail.max_items=0 等于不抓详情，引用它同样要报错。"""
    with pytest.raises(SpecError, match="不可用"):
        make_detail_spec(
            detail={"enabled": True, "link_field": "link", "max_items": 0,
                    "fields": {"description": "div.desc::text"}},
            mapping={"target": "job",
                     "field_map": {"title": "title", "description": "description"}},
        )


def test_field_map_still_rejects_unknown_field():
    """回归：引用根本不存在的字段名，仍然报错。"""
    with pytest.raises(SpecError, match="不可用"):
        make_detail_spec(mapping={
            "target": "job",
            "field_map": {"title": "title", "skills": "no_such_field"},
        })


def test_detail_enabled_without_link_field_raises():
    """开启详情却没写 link_field，等于永远取不到链接 —— 加载期就炸。"""
    with pytest.raises(SpecError, match="link_field"):
        make_detail_spec(detail={
            "enabled": True,
            "fields": {"description": "div.desc::text"},
        })


# ==========================================================================
# type: api —— JSON 接口源（2026-11-10）
#
# 为什么需要这一族：规格引擎原本只会解析 HTML 选择器，而项目里唯一真实跑通的
# 数据源（51job）走的是 JSON 接口。表达能力与真实数据源不匹配，于是「写一份
# YAML 就能接新源」这句话在真实站点上一个都兑现不了。
#
# 这里的用例全部离线（喂 JSON 字符串 / 打桩 safe_get），不联网：
# 网络与反爬永远测不稳定，联网验证放在报告里单独贴原始输出。
# ==========================================================================

API_PAYLOAD = {
    "resultbody": {
        "job": {
            "items": [
                {
                    "jobName": "Python 后端工程师",
                    "companyName": "某某科技有限公司",
                    "provideSalaryString": "1-1.5万",
                    "workAreaString": "长沙-岳麓区",
                    "workYearString": "3-4年经验",
                    "degreeString": "本科",
                    "jobHref": "https://jobs.51job.com/changsha-ylq/123456.html",
                    "attributeText": "五险一金 Python FastAPI",
                },
                {
                    "jobName": "Java 开发工程师",
                    "companyName": "另一家公司",
                    "provideSalaryString": "8千-1.2万·13薪",
                    "workAreaString": "长沙·雨花区",
                    "workYearString": "1-3年经验",
                    "degreeString": "大专",
                    "jobHref": "//jobs.51job.com/changsha-yhq/654321.html",
                },
            ]
        }
    }
}


def make_api_spec(**over):
    """一个最小的 type: api 规格（字段与 51job 同形，便于复用断言）。"""
    data = {
        "site": "job51",
        "label": "接口源演示",
        "type": "api",
        "url": "https://example.com/api/search?keyword={keyword}&pageNum={page}",
        "list": {
            "path": "resultbody.job.items",
            "fields": {
                "title": "json:jobName",
                "company": "json:companyName",
                "salary": "json:provideSalaryString",
                "city": "json:workAreaString | regex:^([^-·]+)",
                "experience": "json:workYearString",
                "education": "json:degreeString",
                "href": "json:jobHref",
            },
        },
        "pagination": {"type": "none", "start": 1, "max_pages": 1},
        "limits": {"max_items": 100, "min_interval": 0},
        "mapping": {
            "target": "job",
            "field_map": {
                "title": "title",
                "company_name": "company",
                "city": "city",
                "salary": "salary",
                "experience": "experience",
                "education": "education",
                "url": "href",
            },
        },
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            merged = dict(data[key])
            merged.update(value)
            data[key] = merged
        else:
            data[key] = value
    return CrawlSpec.from_dict(data)


# ---------------------------------------------------------------- 表达式解析


def test_json表达式解析单层与多层路径():
    assert FieldSpec("f", "json:jobName").parse_expr()["path"] == "jobName"
    single = FieldSpec("f", "json:jobName").parse_expr()
    assert single["kind"] == "json" and single["selector"] is None

    nested = FieldSpec("f", "json:job.salary.min").parse_expr()
    assert nested["kind"] == "json" and nested["path"] == "job.salary.min"


def test_json表达式支持数组展开():
    p = FieldSpec("tags", "json:items[].jobName").parse_expr()
    assert p["kind"] == "json" and p["path"] == "items[].jobName"


def test_json表达式拒绝css后缀并说清可用后缀():
    """写 `json:x::text` 是常见误解（以为和 CSS 一样要声明取值方式）。"""
    with pytest.raises(SpecError, match="regex"):
        FieldSpec("f", "json:jobName::text").parse_expr()


def test_json表达式拒绝下标与过滤器():
    for bad in ("json:items[0].name", "json:items[?(@.x)].name", "json:", "json:..job"):
        with pytest.raises(SpecError):
            FieldSpec("f", bad).parse_expr()


def test_css表达式判别键仍是css_且解析结果不变():
    """回归：不写 json: 的一律按 CSS 处理，旧规格一个字都不用改。"""
    p = FieldSpec("text", "span.text::text").parse_expr()
    assert p["kind"] == "css"
    assert p["selector"] == "span.text"
    assert p["mode"] == "text"
    assert p["multi"] is False

    p_multi = FieldSpec("tags", "a.tag::text[]").parse_expr()
    assert p_multi["kind"] == "css" and p_multi["multi"] is True

    p_attr = FieldSpec("link", "a.detail::attr(href)").parse_expr()
    assert p_attr["mode"] == "attr" and p_attr["attr"] == "href"


# ---------------------------------------------------------------- 取值语义


def test_json缺字段返回None而不是抛异常():
    spec = make_api_spec()
    crawler = SpecCrawler(spec)
    records = crawler.parse_page(json.dumps({
        "resultbody": {"job": {"items": [{"jobName": "只有标题"}]}}
    }))
    assert len(records) == 1
    assert records[0]["title"] == "只有标题"
    assert records[0]["company"] is None      # 缺字段 → None，不炸整批
    assert records[0]["salary"] is None


def test_json与regex组合能截出城市():
    spec = make_api_spec()
    records = SpecCrawler(spec).parse_page(json.dumps(API_PAYLOAD))
    # "长沙-岳麓区" / "长沙·雨花区" 都截到 "长沙"
    assert [r["city"] for r in records] == ["长沙", "长沙"]


def test_json数组展开取值():
    spec = make_api_spec(
        list={"fields": {"tag": "json:tags[]"}},
        mapping={"target": "generic", "field_map": {}},   # 本用例只验证取值，不涉及岗位映射
    )
    crawler = SpecCrawler(spec)
    records = crawler.parse_page(json.dumps({
        "resultbody": {"job": {"items": [
            {"jobName": "x", "tags": ["Python", "RAG"]},
        ]}}
    }))
    assert records[0]["tag"] == ["Python", "RAG"]


def test_json嵌套数组展开并剔除空值():
    """`a[].b` 的语义：对数组每一项继续走剩下的路径，空值不进列表。"""
    spec = make_api_spec(
        list={"fields": {"company": "json:companies[].name"}},
        mapping={"target": "generic", "field_map": {}},
    )
    records = SpecCrawler(spec).parse_page(json.dumps({
        "resultbody": {"job": {"items": [
            {"jobName": "x", "companies": [{"name": "甲公司"}, {"name": ""}, {}]},
        ]}}
    }))
    assert records[0]["company"] == ["甲公司"]


# ---------------------------------------------------------------- 规格校验


def test_api源必须写list_path():
    with pytest.raises(SpecError, match="list.path"):
        make_api_spec(list={"path": "", "fields": {"title": "json:jobName"}})


def test_api源不接受css容器():
    with pytest.raises(SpecError, match="container"):
        make_api_spec(list={"container": "div.job",
                            "path": "resultbody.job.items",
                            "fields": {"title": "json:jobName"}})


def test_api源与render冲突():
    """api 走 httpx 取 JSON；渲染 JS 页面是 html + render 的事，两者不能同时成立。"""
    with pytest.raises(SpecError, match="render"):
        make_api_spec(request={"render": True})


def test_api源不支持detail():
    with pytest.raises(SpecError, match="detail"):
        make_api_spec(detail={"enabled": True, "link_field": "href",
                              "fields": {"description": "div.desc::text"}})


def test_html源不接受list_path():
    with pytest.raises(SpecError, match="list.path"):
        make_api_spec(type="html", list={"path": "a.b", "container": "div.q",
                                        "fields": {"title": "span::text"}})


def test_未知数据源类型报错():
    with pytest.raises(SpecError, match="type"):
        make_api_spec(type="graphql")


# ---------------------------------------------------------------- 解析与错误


def test_api页解析出全部记录():
    spec = make_api_spec()
    records = SpecCrawler(spec).parse_page(json.dumps(API_PAYLOAD, ensure_ascii=False))
    assert len(records) == 2
    assert records[0]["title"] == "Python 后端工程师"
    assert records[0]["company"] == "某某科技有限公司"
    assert records[1]["salary"] == "8千-1.2万·13薪"


def test_非JSON响应报明确错误而不是返回空列表():
    """这是本项最关键的一条：WAF 页 / 登录页都是 HTTP 200 + HTML。

    静默返回 0 条会让任务显示 COMPLETED / 0 条，看起来像「这个城市没岗位」。
    """
    from app.crawlers.spec_crawler import SpecPayloadError

    spec = make_api_spec()
    waf_page = ('<textarea id="renderData" style="display:none">'
                '{"_waf_bd8ce2ce37":"brwFwrXgmQFwikOu1O9Hvm8N9YG2"}</textarea>'
                '<!doctype html><html><head><title>验证</title></head></html>')
    with pytest.raises(SpecPayloadError) as exc:
        SpecCrawler(spec).parse_page(waf_page)
    message = str(exc.value)
    assert "不是合法 JSON" in message
    assert "_waf_bd8ce2ce37" in message        # 带响应片段，便于判断是被拦还是参数错
    assert "type: html" in message             # 给出正确方向


def test_list_path指错时报明确错误():
    from app.crawlers.spec_crawler import SpecPayloadError

    spec = make_api_spec(list={"path": "resultbody.job.nonexistent"})
    with pytest.raises(SpecPayloadError, match="list.path"):
        SpecCrawler(spec).parse_page(json.dumps(API_PAYLOAD))


def test_记录数组不是数组时报明确错误():
    from app.crawlers.spec_crawler import SpecPayloadError

    payload = {"resultbody": {"job": {"items": "不是数组"}}}
    with pytest.raises(SpecPayloadError, match="不是数组"):
        SpecCrawler(make_api_spec()).parse_page(json.dumps(payload))


# ------------------------------------------------------- 输出形状与 job_key


def test_api记录映射成岗位字典且job_key走统一指纹():
    from app.crawlers.cleaner import generate_job_key

    spec = make_api_spec()
    crawler = SpecCrawler(spec)
    rows = crawler.parse_page(json.dumps(API_PAYLOAD, ensure_ascii=False))
    jobs = crawler._shape_records(rows, {"page": 1, "keyword": "python"}, "https://example.com/api")

    assert len(jobs) == 2
    first = jobs[0]
    assert first["_target"] == "job"
    assert first["source_site"] == "job51"
    assert first["title"] == "Python 后端工程师"
    # job_key 必须来自 generate_job_key（平台+标题+公司+城市），不能在这里另算
    assert first["job_key"] == generate_job_key(
        "job51", "Python 后端工程师", "某某科技有限公司", "长沙"
    )


def test_51job草稿规格与硬编码爬虫的字段口径一致():
    """用真实接口 JSON 结构对比：YAML 产出的岗位与 Job51Crawler._map_item 逐字段一致。

    这条是任务书 §三.3 的硬要求（job_key 不一致 = 同一岗位入库成两条）。
    """
    from app.crawlers.job51 import Job51Crawler
    from app.crawlers.spec_schema import SPEC_DIR

    spec = load_spec_file(SPEC_DIR / "_job51.yaml")
    crawler = SpecCrawler(spec)
    rows = crawler.parse_page(json.dumps(API_PAYLOAD, ensure_ascii=False))
    assert len(rows) == 2, "草稿规格应能从真实响应结构里解析出两条记录"

    spec_jobs = crawler._shape_records(
        rows, {"page": 1, "keyword": "python", "area": "190200"},
        "https://we.51job.com/api/job/search-pc",
    )
    reference = Job51Crawler()
    items = API_PAYLOAD["resultbody"]["job"]["items"]
    ref_jobs = [reference._map_item(item, spec_jobs[i]["city"]) for i, item in enumerate(items)]

    for mine, ref in zip(spec_jobs, ref_jobs):
        assert mine["job_key"] == ref["job_key"], "job_key 必须与硬编码爬虫完全一致"
        assert mine["title"] == ref["title"]
        assert mine["company_name"] == ref["company_name"]
        assert mine["city"] == ref["city"]
        assert mine["url"] == ref["url"]
        assert mine["experience"] == ref["experience"]
        assert mine["education"] == ref["education"]


def test_51job草稿规格不会被自动加载():
    """下划线开头 = 禁用。一旦被加载就会顶掉唯一能用的 Job51Crawler（SPECS 优先）。"""
    from app.crawlers.spec_schema import load_all_specs

    sites = load_all_specs()
    assert "51job" not in sites, (
        "草稿规格被自动加载了：registry 里 SPECS 优先于 CRAWLER_REGISTRY，"
        "会立刻把可用的 Job51Crawler 换成走 httpx 的 SpecCrawler —— 而该接口对"
        "非浏览器客户端返回 WAF 页，真实采集会整体失效"
    )


# ------------------------------------------------------------------- 分页


class _FakeResponse:
    def __init__(self, text, status=200, ctype="application/json"):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        return None


@pytest.mark.asyncio
async def test_api源按page_param翻页(monkeypatch):
    """翻页参数复用既有逻辑：URL 里的 {page} 逐页替换，不另造一套。"""
    calls = []

    async def fake_safe_get(url, headers=None, timeout=None):
        calls.append(url)
        page = "2" if "pageNum=2" in url else "1"
        payload = {"resultbody": {"job": {"items": [
            {"jobName": f"岗位-{page}", "companyName": "某公司"},
        ]}}}
        return _FakeResponse(json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr("app.crawlers.spec_crawler.safe_get", fake_safe_get)

    spec = make_api_spec(pagination={"type": "page_param", "start": 1, "max_pages": 2})
    jobs = await SpecCrawler(spec).crawl(keyword="python")

    assert len(calls) == 2, calls
    assert "pageNum=1" in calls[0] and "pageNum=2" in calls[1]
    assert [j["title"] for j in jobs] == ["岗位-1", "岗位-2"]


@pytest.mark.asyncio
async def test_api源遇到明显类型不符的响应在取数层就报错(monkeypatch):
    """类型粗筛先拦一次，别等到 JSON 解析才炸（例如被代理换成图片/压缩包）。"""

    async def fake_safe_get(url, headers=None, timeout=None):
        return _FakeResponse("<html>WAF</html>", ctype="image/png")

    monkeypatch.setattr("app.crawlers.spec_crawler.safe_get", fake_safe_get)
    with pytest.raises(RuntimeError, match="content-type"):
        await SpecCrawler(make_api_spec()).crawl(keyword="python")
