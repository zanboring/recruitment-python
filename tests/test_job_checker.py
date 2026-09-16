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