"""声明式数据源的两项契约测试：

1. ``mapping.target: job`` + ``mapping.field_map`` 必须真正把抽取结果映射成
   标准岗位字典并走既有入库链路（``normalize_job`` → ``save_job``）。
2. ``pagination.next_link`` 必须真的按「下一页」推进，不能每轮重抓第 1 页。

全部使用本地 HTML 与假 ``_fetch``，不发真实请求。
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.crawlers.spec_crawler import SpecCrawler
from app.crawlers.spec_schema import CrawlSpec, SpecError, load_spec_file
from app.models.job import Job
from app.services import crawler_service

JOB_LIST_HTML = """
<html><body>
  <div class="job">
    <h3 class="name">Python 开发工程师</h3>
    <div class="company">某某科技有限公司</div>
    <div class="city">北京</div>
    <div class="salary">15-25K</div>
    <div class="exp">1-3年</div>
    <div class="edu">本科</div>
    <a class="detail" href="/job/1">详情</a>
  </div>
  <div class="job">
    <h3 class="name">后端开发工程师</h3>
    <div class="company">另一家科技</div>
    <div class="city">上海</div>
    <div class="salary">20-30K</div>
    <div class="exp">1-3年</div>
    <div class="edu">本科</div>
    <a class="detail" href="/job/2">详情</a>
  </div>
</body></html>
"""

JOB_SPEC_YAML = """
site: jobs_demo
label: 岗位示例源
request:
  url: "https://example.com/jobs"
pagination:
  type: none
list:
  container: "div.job"
  fields:
    name: "h3.name::text"
    company: "div.company::text"
    city: "div.city::text"
    salary: "div.salary::text"
    exp: "div.exp::text"
    edu: "div.edu::text"
    link: "a.detail::attr(href)"
limits:
  max_items: 10
mapping:
  target: job
  field_map:
    title: name
    company_name: company
    city: city
    salary: salary
    experience: exp
    education: edu
    url: link
"""


def _job_spec(**over):
    data = {
        "site": "jobs_demo",
        "label": "岗位示例源",
        "url": "https://example.com/jobs",
        "list": {
            "container": "div.job",
            "fields": {
                "name": "h3.name::text",
                "company": "div.company::text",
                "city": "div.city::text",
                "salary": "div.salary::text",
                "exp": "div.exp::text",
                "edu": "div.edu::text",
                "link": "a.detail::attr(href)",
            },
        },
        "pagination": {"type": "none", "max_pages": 1},
        "limits": {"max_items": 10},
        "mapping": {
            "target": "job",
            "field_map": {
                "title": "name",
                "company_name": "company",
                "city": "city",
                "salary": "salary",
                "experience": "exp",
                "education": "edu",
                "url": "link",
            },
        },
    }
    data.update(over)
    return CrawlSpec.from_dict(data)


def _mapped_rows(spec, html=JOB_LIST_HTML, params=None):
    crawler = SpecCrawler(spec)
    rows = crawler.parse_page(html)
    return crawler._shape_records(rows, params or {"city": "北京"}, "https://example.com/jobs")


# ------------------------------------------------------- W-4 字段映射


def test_field_map_把抽取字段映射成标准岗位字段():
    rows = _mapped_rows(_job_spec())
    assert rows and len(rows) == 2
    first = rows[0]
    assert first["title"] == "Python 开发工程师"
    assert first["company_name"] == "某某科技有限公司"
    assert first["city"] == "北京"
    assert first["experience"] == "1-3年"
    assert first["education"] == "本科"
    assert first["salary"] == "15-25K"
    assert first["url"] == "https://example.com/job/1"   # 相对链接补齐为绝对地址
    assert first["source_site"] == "jobs_demo"
    assert first["_target"] == "job"


def test_field_map_改一个键名就改变输出字段():
    """判据要求：改 field_map 里的键名必须真的改变输出，而不是被忽略。"""
    spec = _job_spec(mapping={
        "target": "job",
        "field_map": {
            "title": "company",          # 故意把标题指到公司字段
            "company_name": "name",
        },
    })
    rows = _mapped_rows(spec)
    assert rows[0]["title"] == "某某科技有限公司"
    assert rows[0]["company_name"] == "Python 开发工程师"


def test_缺标题的记录被跳过而不是产出空岗位():
    spec = _job_spec(mapping={"target": "job", "field_map": {"title": "company"}})
    html = '<div class="job"><div class="company"></div></div>'
    rows = _mapped_rows(spec, html=html)
    assert rows == []


def test_field_map_写错键名在加载期报错():
    with pytest.raises(SpecError, match="未知标准字段"):
        _job_spec(mapping={"target": "job", "field_map": {"company": "company"}})


def test_field_map_引用不存在的抽取字段在加载期报错():
    # 文案用「不可用」而不是「不存在」：detail.fields 里的字段可能**存在但不可用**
    #（detail 未开启 / max_items=0），两种情况都必须在加载期挡下。
    with pytest.raises(SpecError, match="不可用的抽取字段：not_exist"):
        _job_spec(mapping={"target": "job", "field_map": {"title": "not_exist"}})


def test_generic_记录被显式标注():
    """generic 保持原样，但必须打标，避免下游静默丢弃。"""
    spec = CrawlSpec.from_dict({
        "site": "generic_demo",
        "url": "https://example.com/list",
        "list": {"container": "div.job", "fields": {"name": "h3.name::text"}},
        "limits": {"max_items": 5},
    })
    crawler = SpecCrawler(spec)
    rows = crawler._shape_records(crawler.parse_page(JOB_LIST_HTML), {}, "https://example.com/list")
    assert len(rows) == 2
    assert all(r["_target"] == "generic" for r in rows)
    assert "job_key" not in rows[0]


# --------------------------------------------- W-4 端到端：YAML → 入库


async def test_yaml_到入库全链路(db_session, tmp_path, monkeypatch):
    """一个 target: job 的 YAML，从加载 → 抓取 → normalize_job → save_job 全通。"""
    spec_file = tmp_path / "jobs_demo.yaml"
    spec_file.write_text(JOB_SPEC_YAML, encoding="utf-8")
    spec = load_spec_file(spec_file)
    assert spec.target == "job"

    crawler = SpecCrawler(spec)

    async def fake_fetch(url):
        return JOB_LIST_HTML

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    rows = await crawler.crawl(city="北京")
    assert len(rows) == 2

    first = crawler_service.normalize_job(rows[0])
    assert first["job_key"]
    # 薪资文本交给既有解析器，不在这里自己算
    assert first["min_salary"] == 15000
    assert first["max_salary"] == 25000
    assert await crawler_service.save_job(db_session, first) is True
    await db_session.commit()

    stored = (await db_session.execute(select(Job).where(Job.job_key == first["job_key"]))).scalar_one()
    assert stored.title == "Python 开发工程师"
    assert stored.company_name == "某某科技有限公司"
    assert stored.city == "北京"
    assert stored.source_site == "jobs_demo"

    # 同一岗位再次入库应命中去重，而不是新增一行
    assert await crawler_service.save_job(db_session, first) is False


async def test_generic_数据源不入库但写入任务说明(db_session, monkeypatch):
    """generic 行不能被 deduplicate_jobs 静默吞掉，必须在说明里交代。"""
    class _GenericCrawler:
        spec = SimpleNamespace(target="generic")

        async def crawl_with_retry(self, keyword, city):
            return [{"text": "一条分析用数据", "_target": "generic"}]

    monkeypatch.setattr(crawler_service, "get_crawler", lambda platform: _GenericCrawler())
    monkeypatch.setattr(crawler_service, "probe_url", lambda platform: "")

    saved, keys, notes = await crawler_service._crawl_platform(
        db_session, "x", "北京", "generic_demo"
    )
    assert saved == 0
    assert keys == []
    assert any("generic" in n and "未写入岗位表" in n for n in notes)


# ------------------------------------------------------- W-5 翻页


def _page_html(number: int, next_href: str = "") -> str:
    link = f'<a class="next" href="{next_href}">下一页</a>' if next_href else ""
    return (
        f'<html><body><div class="item"><span class="n">第 {number} 条</span></div>'
        f"{link}</body></html>"
    )


def _paged_spec(max_pages: int = 10):
    return CrawlSpec.from_dict({
        "site": "paged_demo",
        "url": "https://example.com/p1",
        "list": {"container": "div.item", "fields": {"n": "span.n::text"}},
        "pagination": {"type": "next_link", "max_pages": max_pages, "next_selector": "a.next"},
        "limits": {"max_items": 100},
    })


async def test_next_link_依次抓到第三页且不重复(monkeypatch):
    pages = {
        "https://example.com/p1": _page_html(1, "/p2"),
        "https://example.com/p2": _page_html(2, "/p3"),
        "https://example.com/p3": _page_html(3),          # 末页无 next
    }
    calls = []

    crawler = SpecCrawler(_paged_spec())

    async def fake_fetch(url):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    rows = await crawler.crawl()

    assert calls == [
        "https://example.com/p1",
        "https://example.com/p2",
        "https://example.com/p3",
    ]
    assert [r["n"] for r in rows] == ["第 1 条", "第 2 条", "第 3 条"]


async def test_next_link_末页指回自己不进入死循环(monkeypatch):
    """真实站点「下一页」在末页常仍指向当前页，必须 break。"""
    pages = {
        "https://example.com/p1": _page_html(1, "/p2"),
        "https://example.com/p2": _page_html(2, "/p2"),   # 自指
    }
    calls = []

    crawler = SpecCrawler(_paged_spec())

    async def fake_fetch(url):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    rows = await crawler.crawl()

    assert calls == ["https://example.com/p1", "https://example.com/p2"]
    assert len(rows) == 2


async def test_next_link_遵守_max_pages(monkeypatch):
    pages = {
        f"https://example.com/p{i}": _page_html(i, f"/p{i + 1}") for i in range(1, 6)
    }
    calls = []

    crawler = SpecCrawler(_paged_spec(max_pages=2))

    async def fake_fetch(url):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(crawler, "_fetch", fake_fetch)
    await crawler.crawl()

    assert len(calls) == 2
