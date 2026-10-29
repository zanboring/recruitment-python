# -*- coding: utf-8 -*-
"""URL 导入 + 岗位存活核查 测试。"""
import ipaddress
import logging

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.job import Job
from app.schemas.job import JOB_DESC_MAX
from app.services import job_checker, url_import_service
from app.utils import url_safety
from app.utils.job_key import generate_job_key


def test_detect_offline():
    assert job_checker.detect_offline("该职位已下线，去看看别的岗位") is True
    assert job_checker.detect_offline("职位不存在或已过期") is True
    assert job_checker.detect_offline("position closed") is True
    assert job_checker.detect_offline("Java开发工程师 岗位职责 任职要求") is False
    assert job_checker.detect_offline("该职位在招，欢迎投递") is False  # ALIVE 信号优先
    assert job_checker.detect_offline("") is False  # 空文本保守在线


def test_extract_page_text_strips_tags():
    html = "<html><script>var x=1</script><style>.a{}</style><h1>Python工程师</h1><p>负责开发</p></html>"
    text = url_import_service.extract_page_text(html)
    assert "Python工程师" in text
    assert "var x" not in text
    assert ".a{}" not in text
    assert "<h1>" not in text


def test_rule_extract_salary():
    text = "岗位名称：AI应用工程师 薪资：20-30K 城市：长沙 经验要求：3-5年"
    data = url_import_service._rule_extract(text)
    assert data["title"] == "AI应用工程师"
    assert data["city"] == "长沙"
    assert data["min_salary"] == 20000
    assert data["max_salary"] == 30000


@pytest.mark.asyncio
async def test_check_job_url_not_offline_without_url():
    """无 URL 的岗位核查应返回 False（视为在线，不误伤）。"""
    job = Job(title="t", source_site="x", job_key=generate_job_key("x", "t"))
    assert await job_checker.check_job_url(job) is False


@pytest.mark.asyncio
async def test_check_job_url_404_means_offline(monkeypatch):
    """404 应判定为下线。"""
    import httpx

    class FakeResp:
        status_code = 404

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    job = Job(title="t", source_site="x", job_key=generate_job_key("x", "t"),
              url="https://example.com/job/1")
    assert await job_checker.check_job_url(job) is True


@pytest.mark.asyncio
async def test_run_sweep_only_active_with_url(db_session, monkeypatch):
    """核查只处理 ACTIVE 且有 URL 的岗位；无 URL 的 ACTIVE 不处理。"""
    import asyncio

    monkeypatch.setattr(job_checker.asyncio, "sleep", asyncio.sleep)  # 避免真睡

    # 有 URL 的 ACTIVE 岗位
    with_url = Job(title="java", company_name="A", source_site="seed", city="长沙",
                   job_key=generate_job_key("seed", "java", "A", "长沙"),
                   job_status="ACTIVE", url="https://example.com/job/1")
    # 无 URL 的 ACTIVE
    no_url = Job(title="python", company_name="B", source_site="seed", city="深圳",
                 job_key=generate_job_key("seed", "python", "B", "深圳"),
                 job_status="ACTIVE", url="")
    db_session.add(with_url)
    db_session.add(no_url)
    await db_session.commit()

    # mock check_job_url：有 URL 的判定为下线
    async def fake_check(job):
        return bool(job.url)

    # 需要先让 run_sweep 能看到 last_checked_at 排序；SQLite 无 nullsfirst
    # 走 hasattr 分支会进入 else —— 验证该兼容分支可用
    async def fake_check2(job):
        return False

    # patch 后跑 sweep
    import app.services.job_checker as jc
    monkeypatch.setattr(jc, "check_job_url", fake_check2)

    from app.config import settings
    orig_batch = settings.job_checker_batch_size
    settings.job_checker_batch_size = 10
    try:
        result = await job_checker.run_sweep(db_session)
    finally:
        settings.job_checker_batch_size = orig_batch

    # 无 URL 的岗位不应进入抽查范围（run_sweep 只查有 URL 的）
    assert result["checked"] == 1
    assert result["offline"] == 0


@pytest.mark.asyncio
async def test_url_import_invalid_url():
    with pytest.raises(RuntimeError, match="合法的 http"):
        await url_import_service.import_job_from_url(None, "not-a-url")

@pytest.mark.asyncio
async def test_fetch_page_via_http_success(monkeypatch):
    """HTTP 直抓成功：返回去标签的正文与原始 HTML。"""
    import httpx

    html = "<html><body><h1>Python工程师</h1><p>岗位职责：开发系统</p></body></html>"

    class FakeResp:
        status_code = 200
        text = html

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    text, raw = await url_import_service._fetch_page_via_http("https://example.com/job/1")
    assert "Python工程师" in text
    assert raw == html


@pytest.mark.asyncio
async def test_fetch_page_via_http_404_returns_none(monkeypatch):
    """非 200 状态码视为无内容，返回 (None, None)。"""
    import httpx

    class FakeResp:
        status_code = 404
        text = ""

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    assert await url_import_service._fetch_page_via_http("https://example.com/gone") == (None, None)


@pytest.mark.asyncio
async def test_open_page_text_http_fallback_without_playwright(monkeypatch):
    """无 Playwright（exe 环境）时：HTTP 拿到实质内容直接返回，不抛错。"""
    import httpx

    long_html = "<html><body>" + "<p>岗位职责：" + "详细职责" * 100 + "</p></body></html>"

    class FakeResp:
        status_code = 200
        text = long_html

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    # 模拟 playwright 不可用
    def boom():
        raise ImportError("no playwright")
    monkeypatch.setattr(url_import_service, "_fetch_page_via_http", url_import_service._fetch_page_via_http)  # 保留真实现
    # 直接 monkeypatch sys.modules 中 playwright 不可导入：改用源头封堵
    import builtins
    orig_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "playwright.async_api":
            raise ImportError("no playwright in exe")
        return orig_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    text, raw = await url_import_service.open_page_text("https://example.com/job/1")
    assert "详细职责" in text


@pytest.mark.asyncio
async def test_open_page_text_spa_shell_falls_back_to_playwright(monkeypatch):
    """HTTP 拿到 SPA 空壳（正文过短）时回退 Playwright 渲染。"""
    import httpx

    shell_html = "<html><body><div id=app></div></body></html>"  # 空壳，正文 < 120

    class FakeResp:
        status_code = 200
        text = shell_html

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    rendered_html = "<html><body><p>渲染后的真实岗位详情内容</p></body></html>"

    class FakePage:
        async def goto(self, *a, **k):
            return None

        async def wait_for_timeout(self, *a):
            return None

        async def content(self):
            return rendered_html

    class FakeBrowser:
        async def new_page(self, *a, **k):
            return FakePage()

        async def close(self):
            return None

    class FakePlaywright:
        def __init__(self, browser_object):
            self._browser = browser_object

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        @property
        def chromium(self):
            return self

        async def launch(self, **k):
            return self._browser

    class FakeAsyncPlaywright:
        def __init__(self):
            self.browser = FakeBrowser()
            self._inner = FakePlaywright(self.browser)

        def __call__(self):
            return self._inner

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    import builtins
    orig_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "playwright.async_api":
            mod = type("M", (), {"async_playwright": FakeAsyncPlaywright()})()
            return mod
        return orig_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    text, raw = await url_import_service.open_page_text("https://example.com/spa-job")
    assert "渲染后的真实岗位详情" in text


# --------------------------------------------------------------------------
# 链接导入：**系统抽取**的超长/异构文本 → 截断 + 留痕（不是 422）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_链接导入_超长描述被截断且留痕(db_session, monkeypatch, caplog):
    """LLM 返回 8000 字描述：入库成功、落库长度 == 上限、且有 warning。

    为什么不是 422：这段文本是**模型产物**，用户没有「改小再提交」的入口 ——
    页面明明抓成功了却在「确认入库」失败，是无法向用户解释的。
    （用户**直接提交**超长仍然是 422，见 tests/test_schema_limits.py。）
    """

    async def fake_open(url):
        return "岗位正文", "<html>snapshot</html>"

    async def fake_extract(text):
        return {
            "title": "Java 后端工程师",
            "company_name": "某公司",
            "city": "长沙",
            "experience": "3-5年",
            "education": "本科",
            "skills": "Java,MySQL",
            "description": "描" * 8000,
            "min_salary": 15000,
            "max_salary": 25000,
        }

    monkeypatch.setattr(url_import_service, "open_page_text", fake_open)
    monkeypatch.setattr(url_import_service, "extract_job_from_text", fake_extract)

    with caplog.at_level(logging.WARNING, logger=url_import_service.__name__):
        result = await url_import_service.import_job_from_url(
            db_session, "http://93.184.216.34/job/1"
        )

    assert result["saved"] is True
    row = (await db_session.execute(
        select(Job).where(Job.id == result["job_id"])
    )).scalar_one()

    assert len(row.job_desc) == JOB_DESC_MAX
    assert row.job_desc == "描" * JOB_DESC_MAX

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "job_desc" in m and "截断" in m and "8000" in m for m in warnings
    ), f"缺少截断留痕日志：{warnings}"


@pytest.mark.asyncio
async def test_链接导入_非字符串字段收敛为逗号串(db_session, monkeypatch):
    """LLM 可能返回 `skills: ["Python","Java"]`。

    直接 str() 得到 `"['Python', 'Java']"`（带方括号与引号）—— 能入库，
    但会污染技能匹配与展示；应收敛成 `Python,Java`（本项目的 skills 约定）。
    """

    async def fake_open(url):
        return "正文", "<html></html>"

    async def fake_extract(text):
        return {
            "title": "Python 工程师",
            "company_name": {"name": "某公司"},
            "city": "深圳",
            "experience": 3,
            "education": "本科",
            "skills": ["Python", "Java", {"name": "Docker"}],
            "description": "岗位职责",
        }

    monkeypatch.setattr(url_import_service, "open_page_text", fake_open)
    monkeypatch.setattr(url_import_service, "extract_job_from_text", fake_extract)

    result = await url_import_service.import_job_from_url(
        db_session, "http://93.184.216.34/job/2"
    )

    assert result["saved"] is True
    row = (await db_session.execute(
        select(Job).where(Job.id == result["job_id"])
    )).scalar_one()
    assert row.skills == "Python,Java,Docker"
    assert row.company_name == "某公司"
    assert row.experience == "3"


# --------------------------------------------------------------------------
# 存活核查：SSRF 防护（Job.url 是用户可写的，后台不能替人去打内网）
# --------------------------------------------------------------------------


class _CountingClient:
    """统计「真正发出去的请求数」的 httpx.AsyncClient 替身。

    为什么只断言返回值不够：`check_job_url` 不安全时也返回 False，
    与「网络失败返回 False」长得一模一样 —— 只有请求次数能区分
    「没发请求」和「发了但失败了」。

    为什么替身要**按 follow_redirects 真的模拟跟随**：如果替身无论参数如何
    都只返回第一跳，那「只发了一跳」就成了替身的性质而不是被测代码的性质 ——
    旧实现（交给 httpx 自动跟随）也会「只发一跳」，用例便失去区分力。
    让它真跟随，才能证明新代码是**自己逐跳校验**、旧代码会**静默跟到内网**。
    """

    def __init__(self, responses=None, exc=None):
        self._responses = list(responses or [])
        self._exc = exc
        self._follow = False
        self.get_calls = 0
        self.visited = []

    def with_follow(self, follow: bool):
        self._follow = follow
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.get_calls += 1
        self.visited.append(url)
        if self._exc is not None:
            raise self._exc
        resp = self._responses.pop(0)
        # 模拟 httpx 的自动跟随：这正是「先判后发」要避免的行为
        while self._follow and getattr(resp, "status_code", 0) in (301, 302, 303, 307, 308):
            location = (getattr(resp, "headers", None) or {}).get("location")
            if not location:
                break
            self.get_calls += 1
            self.visited.append(location)
            resp = self._responses.pop(0)
        return resp


def _install_counting(monkeypatch, responses=None, exc=None):
    """把 httpx.AsyncClient 换成替身，并把构造时的 follow_redirects 透传给它。"""
    client = _CountingClient(responses, exc=exc)
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: client.with_follow(k.get("follow_redirects", False)),
    )
    return client


def _public_resolver(monkeypatch):
    """IP 字面量原样返回；域名一律当公网（保持用例离线、行为确定）。"""

    def _fake(host, port):
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            return ["93.184.216.34"]

    monkeypatch.setattr(url_safety, "_resolve", _fake)


def _job_with_url(url: str) -> Job:
    return Job(
        title="t", source_site="x", job_key=generate_job_key("x", "t"), url=url
    )


@pytest.mark.asyncio
async def test_核查_内网URL一个请求都不发(monkeypatch, caplog):
    """job.url = 127.0.0.1：**必须先判后发** —— get 调用次数为 0，返回 False。"""
    client = _install_counting(monkeypatch)
    _public_resolver(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=job_checker.__name__):
        assert await job_checker.check_job_url(_job_with_url("http://127.0.0.1:8000/x")) is False

    assert client.get_calls == 0, "内网地址不应发出任何请求"
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("不安全" in m and "跳过核查" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_核查_重定向到元数据地址只发第一跳(monkeypatch, caplog):
    """首跳公网、Location 指向 169.254.169.254：只发出第一跳就停。"""
    client = _install_counting(
        monkeypatch,
        [
            type("R", (), {"status_code": 302,
                           "headers": {"location": "http://169.254.169.254/latest/meta-data/"}})(),
            # 若防护失效（让客户端自动跟随），这个响应就会被取到
            type("R", (), {"status_code": 200, "headers": {}, "text": "不该被读到"})(),
        ],
    )
    _public_resolver(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=job_checker.__name__):
        assert await job_checker.check_job_url(_job_with_url("http://jobs.example.com/job/1")) is False

    assert client.get_calls == 1, "第二跳（元数据地址）绝不能发出去"
    assert all("169.254.169.254" not in u for u in client.visited), client.visited


@pytest.mark.asyncio
async def test_核查_正常公网页面含下线特征仍判定下线(monkeypatch):
    """回归：安全 URL + 200 + 下线特征 → 仍返回 True。"""
    resp = type("R", (), {"status_code": 200, "headers": {}, "text": "该职位已下线，请看看别的"})()
    _install_counting(monkeypatch, [resp])
    _public_resolver(monkeypatch)

    assert await job_checker.check_job_url(_job_with_url("https://jobs.example.com/job/1")) is True


@pytest.mark.asyncio
async def test_核查_连接异常仍返回False且不抛出(monkeypatch):
    """回归：网络失败 = 不可判定，不把在线岗位误判为下线，也不把异常抛给调用方。"""
    _install_counting(monkeypatch, exc=httpx.ConnectError("boom"))
    _public_resolver(monkeypatch)

    assert await job_checker.check_job_url(_job_with_url("https://jobs.example.com/job/1")) is False