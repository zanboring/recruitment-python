# -*- coding: utf-8 -*-
"""详情页回填的解析与取数闸门测试。

**测试素材的来源，以及为什么正负两侧用的是不同素材**

``tests/fixtures/job51_detail.html`` 是 **2026-11-10 从
``https://we.51job.com/pc/jobdetail?jobId=171818429`` 实际抓回来的响应原文**
（只做了「整页保存」，没有改内容；已核对其中不含 cookie / token / 手机号 /
招聘者姓名）。它就是 51job 详情页在未登录态下的**真实形态**：一个
``<div id="app"></div>`` 的 SPA 空壳，正文由前端 JS 再请求接口后填充。

因此这份真实样本只能覆盖**负向**用例：``parse_detail_html`` 必须认出
「结构不匹配」并返回 ``None``，而不是把空壳或脚本里的字面量当正文。

**正向**用例需要的「带正文的详情页 HTML」在未登录态下抓不到
（``we.51job.com`` 302 登录墙、``jobs.51job.com`` 阿里云滑块、
``m.51job.com`` 找不到页，三条路径实测全部不可用），所以：

- 结构化数据（JSON-LD ``JobPosting``）与正文容器两条正向路径，用
  **测试内联的合成片段**验证「解析契约」—— 它们标了 ``SYNTHETIC``，
  不代表任何真实页面；
- 搜索接口那条路径用**真实**的 ``search-pc`` item 字段（2026-11-10 抓取，
  ``jobId=174174002``，``jobDescribe`` 原文照搬），因为那是真实拿得到的数据。

这不是「用假数据冒充真实结果」：真实数据用在真实能拿到的地方，
抓不到的地方就用合成片段只验解析规则，并在报告中如实说明。
"""
import inspect
from datetime import datetime

import pytest

from app.crawlers import detail_fetcher as df
from app.crawlers.detail_fetcher import (
    MIN_DESC_LEN,
    DetailFetcher,
    DetailResult,
    extract_job_id,
    parse_detail_html,
    parse_search_item,
)
from app.utils.url_safety import UnsafeURLError

# 真实抓取的 51job 详情页响应（SPA 空壳）
FIXTURE = "tests/fixtures/job51_detail.html"


def _read_fixture() -> str:
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return fh.read()


def _real_search_item() -> dict:
    """2026-11-10 抓到的真实 ``search-pc`` item（原样照搬，未改写内容）。"""
    return {
        "jobId": "174174002",
        "jobName": "智能计算开发工程师（6）",
        "jobAreaString": "长沙·开福区",
        "provideSalaryString": "9千-1.4万",
        "issueDateString": "2026-11-10 10:33:27",
        "jobHref": "https://jobs.51job.com/changsha-kfq/174174002.html",
        "jobDescribe": (
            "岗位职责\n"
            "1. 负责人工智能基础软件的开发工作，包括大模型（如科学计算大模型、通用大语言模型）"
            "在异构计算架构（CPU/GPU）下的执行流程优化、并行计算优化、图神经网络优化、"
            "GPU 架构下的大模型算子加速、AI4S 场景下大模型算法计算模式与硬件架构的协同优化。\n"
            "2. 负责开发模块的测试、维护、优化，及文档编写\n"
            "3. 理解业务场景（大数据/AI相关业务），可以协助完成需求对接、业务梳理、文档撰写等。\n"
            "任职要求：\n"
            "1. 应届硕士及以上学历；\n"
            "2. 备良好的问题解决和调试能力；\n"
            "3. 要求较熟练掌握Python、用过Pytorch；\n"
            "4. 有ai infra相关的学习或项目经历优先；"
        ),
    }


# --------------------------------------------------------------- 真实样本：负向


def test_real_detail_page_is_spa_shell_and_returns_none():
    """真实详情页在未登录态只有空壳：必须返回 None，而不是空串或脚本内容。"""
    html = _read_fixture()
    assert len(html) > 200  # 足够长，不会命中「太短」那条早退

    assert parse_detail_html(html, "https://we.51job.com/pc/jobdetail?jobId=171818429") is None


def test_real_detail_page_parse_does_not_raise():
    """结构不匹配只返回 None，不抛异常 —— 调用方靠 None 决定换路径。"""
    html = _read_fixture()
    # 畸形输入也不能炸
    for sample in (html, "", "<html>", "not html at all", "<div class='job-detail'>"):
        assert parse_detail_html(sample) is None


# --------------------------------------------------------------- 合成片段：正向


JSON_LD_PAGE = """<!DOCTYPE html>
<html><head><title>某岗位</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "JobPosting", "title": "后端开发工程师",
 "datePosted": "2026-04-27",
 "description": "<p>岗位职责：负责推荐系统后端服务的设计与开发。</p><p>任职要求：熟悉 Python / FastAPI，有三年以上后端经验。</p>"}
</script></head><body><div id="app"></div></body></html>
"""
# SYNTHETIC：仅用于验证 JSON-LD 契约，不代表任何真实页面。


def test_parse_detail_html_json_ld():
    parsed = parse_detail_html(JSON_LD_PAGE, "https://example.com/job/1")
    assert parsed is not None
    desc, publish = parsed
    assert "推荐系统后端服务" in desc
    assert "<p>" not in desc and "&nbsp;" not in desc
    assert publish == datetime(2026, 4, 27)


CONTAINER_PAGE = """<html><body>
<div class="top-bar"><span>发布时间：2026-04-27 16:35:53</span></div>
<div class="job-detail">
  <div class="job-detail-title">岗位职责</div>
  <div class="job-detail-content">
    负责招聘推荐系统的数据链路建设，包括爬虫调度、特征加工与离线评估。<br>
    参与推荐策略迭代，持续提升岗位与候选人的匹配质量。
  </div>
</div>
</body></html>
"""
# SYNTHETIC：仅用于验证「嵌套容器按深度配对」这条规则。


def test_parse_detail_html_nested_container():
    """正文容器里有嵌套 div：必须抓到**完整**正文，不能停在第一个内层结束标签。"""
    parsed = parse_detail_html(CONTAINER_PAGE)
    assert parsed is not None
    desc, publish = parsed
    assert "数据链路建设" in desc
    assert "匹配质量" in desc          # 后半段在第二个内层 div 里
    assert publish == datetime(2026, 4, 27, 16, 35, 53)


def test_parse_detail_html_short_container_returns_none():
    """容器命中但正文太短（不足 MIN_DESC_LEN）视为结构不匹配。"""
    page = '<html><body><div class="job-detail">太短</div></body></html>'
    assert parse_detail_html(page) is None


# --------------------------------------------------------------- 搜索接口条甲


def test_parse_search_item_real_sample():
    desc, publish = parse_search_item(_real_search_item())
    assert desc.startswith("岗位职责")
    assert "任职要求" in desc
    assert len(desc) >= MIN_DESC_LEN
    assert publish == datetime(2026, 11, 10, 10, 33, 27)


def test_parse_search_item_strips_html_br():
    item = {"jobDescribe": "岗位职责<br>1. 负责后端服务开发与维护，保障接口稳定性。<br>2. 参与架构设计。",
            "issueDateString": "2026-04-27"}
    desc, publish = parse_search_item(item)
    assert "<br>" not in desc
    assert desc.splitlines()[0] == "岗位职责"
    assert publish == datetime(2026, 4, 27)


def test_parse_search_item_too_short_or_not_dict():
    assert parse_search_item({"jobDescribe": "太短"}) is None
    assert parse_search_item({"jobName": "没有正文"}) is None
    assert parse_search_item("不是 dict") is None
    assert parse_search_item(None) is None


def test_parse_search_item_tolerates_bad_date():
    item = {"jobDescribe": "岗位职责：负责推荐算法工程化落地，包含特征、模型与在线服务。" * 2,
            "issueDateString": "昨天"}
    desc, publish = parse_search_item(item)
    assert desc and publish is None


# --------------------------------------------------------------- job_id 提取


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://we.51job.com/pc/jobdetail?jobId=171818429", "171818429"),
        ("https://we.51job.com/pc/jobdetail?jobId=171818429&s=1", "171818429"),
        ("https://jobs.51job.com/changsha-kfq/174174002.html", "174174002"),
        ("https://m.51job.com/jobdetail/174174002.html", "174174002"),
        ("https://we.51job.com/pc/search", ""),
        ("", ""),
    ],
)
def test_extract_job_id(url, expected):
    assert extract_job_id(url) == expected


# --------------------------------------------------------------- 取数闸门


class _FakeResponse:
    def __init__(self, text, status_code=200, url="https://we.51job.com/pc/jobdetail?jobId=1"):
        self.text = text
        self.status_code = status_code
        self.url = url


async def _no_render(self, url):  # 打桩：证明静态路径成功时不会去起浏览器
    raise AssertionError("静态 HTML 已解析成功，不应再走渲染路径")


async def _no_search(self, keyword, city, job_id):
    raise AssertionError("静态 HTML 已解析成功，不应再走搜索兜底")


async def test_fetch_goes_through_safe_get(monkeypatch):
    """出站请求必须走 ``safe_get``（逐跳校验），并且**不**传 follow_redirects。"""
    calls = []

    async def fake_safe_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(JSON_LD_PAGE, url=url)

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", _no_render)
    monkeypatch.setattr(DetailFetcher, "_search_by_keyword", _no_search)

    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=171818429")

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://we.51job.com/pc/jobdetail?jobId=171818429"
    # follow_redirects 交给 safe_get 内部强制 False，调用方不许自己传 True
    assert "follow_redirects" not in kwargs
    assert result.ok and result.source == "html"
    assert result.job_id == "171818429"
    assert result.detail_html  # 静态路径要留下 HTML


def test_module_never_enables_redirect_following():
    """源码级守卫：模块内不许出现 ``follow_redirects=True``。

    ``follow_redirects=False`` 允许出现（模块文档就是在讲这件事），
    真正要拦的是「有人日后把它改成 True，跳过逐跳校验」。
    """
    source = inspect.getsource(df)
    assert "follow_redirects=True" not in source
    assert "safe_get(" in source


async def test_fetch_returns_reason_when_all_paths_fail(monkeypatch):
    """三条路径全失败：返回 ok=False + reason，不抛异常。"""

    async def fake_safe_get(url, **kwargs):
        return _FakeResponse(_read_fixture(), url=url)

    async def fake_render(self, url):
        return None

    async def fake_search(self, keyword, city, job_id):
        return None

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", fake_render)
    monkeypatch.setattr(DetailFetcher, "_search_by_keyword", fake_search)

    result = await DetailFetcher().fetch(
        "https://we.51job.com/pc/jobdetail?jobId=171818429", keyword="智能计算开发工程师"
    )
    assert result.ok is False
    assert result.reason
    assert any("html" in a for a in result.attempts)
    assert any("search" in a for a in result.attempts)


async def test_fetch_unsafe_url_does_not_raise(monkeypatch):
    """目标地址不安全时只记录原因，不向上抛（整批回填不能因一条中止）。"""

    async def deny(url, **kwargs):
        raise UnsafeURLError("拒绝访问内网/本机地址：127.0.0.1 -> 127.0.0.1")

    async def fake_render(self, url):
        return None

    monkeypatch.setattr(df, "safe_get", deny)
    monkeypatch.setattr(DetailFetcher, "_render_detail", fake_render)

    result = await DetailFetcher().fetch("http://127.0.0.1:8080/jobdetail?jobId=1")
    assert result.ok is False
    assert "不安全" in result.reason


async def test_fetch_unexpected_errors_are_contained(monkeypatch):
    """任何意外异常都只落在 attempts 里（网络/解析/浏览器都可能抛）。"""

    async def boom(url, **kwargs):
        raise RuntimeError("connection reset")

    async def boom_render(self, url):
        raise RuntimeError("browser gone")

    async def boom_search(self, keyword, city, job_id):
        raise RuntimeError("api changed")

    monkeypatch.setattr(df, "safe_get", boom)
    monkeypatch.setattr(DetailFetcher, "_render_detail", boom_render)
    monkeypatch.setattr(DetailFetcher, "_search_by_keyword", boom_search)

    result = await DetailFetcher().fetch(
        "https://we.51job.com/pc/jobdetail?jobId=1", keyword="某岗位"
    )
    assert result.ok is False
    joined = " ".join(result.attempts)
    assert "RuntimeError" in joined
    assert "connection reset" in joined and "browser gone" in joined


async def test_fetch_empty_url(monkeypatch):
    result = await DetailFetcher().fetch("")
    assert result.ok is False
    assert result.reason == "缺少岗位 URL"


async def test_fetch_skips_search_without_keyword(monkeypatch):
    """没有标题就不该打搜索接口 —— 按 jobId 搜不到，白打一次是多余的请求。"""

    async def fake_safe_get(url, **kwargs):
        return _FakeResponse(_read_fixture(), url=url)

    async def fake_render(self, url):
        return None

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", fake_render)

    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=1")
    assert result.ok is False
    assert any("未提供标题" in a for a in result.attempts)


# --------------------------------------------------------------- 结果口径


def test_detail_result_ok_threshold():
    assert DetailResult(job_desc="x" * (MIN_DESC_LEN - 1)).ok is False
    assert DetailResult(job_desc="x" * MIN_DESC_LEN).ok is True
    assert DetailResult().as_dict()["publish_time"] is None
    d = DetailResult(job_id="1", job_desc="x" * 40, publish_time=datetime(2026, 11, 10),
                     detail_html="<html>" + "a" * 20)
    payload = d.as_dict()
    assert payload["ok"] is True
    assert payload["publish_time"] == "2026-11-10T00:00:00"
    assert payload["detail_html_len"] == 26
