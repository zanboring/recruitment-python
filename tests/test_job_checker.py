# -*- coding: utf-8 -*-
"""URL 导入 + 岗位存活核查 测试。"""
import pytest
import pytest_asyncio

from app.models.job import Job
from app.services import job_checker, url_import_service
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