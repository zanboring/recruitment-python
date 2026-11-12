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


# ==========================================================================
# 回填脚本：失败分类 / 连续 WAF 止损 / --resume 语义
#
# 为什么这些用例放在本文件而不是新建 test_backfill_detail.py：
# `tests/test_docs_contract.py::test_showcase_test_file_count_matches_disk` 要求
# tests/test_*.py 的文件数等于 docs/showcase.md 的声明值，而本轮的「不可碰」清单里
# 明确写了「任何文档类文件」—— 新建文件会让那条契约变红，而我无权改文档。
# 已在交付报告里说明这次取舍。
# ==========================================================================


def _load_backfill_script():
    """按路径加载 scripts/backfill_detail.py（scripts/ 不是包，不能直接 import）。"""
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "backfill_detail.py"
    spec = importlib.util.spec_from_file_location("backfill_detail_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_backfill_db(path, count: int) -> None:
    """最小库：只建 job 表，列与真实库回填相关的部分一致。"""
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE job (id INTEGER PRIMARY KEY, title TEXT, city TEXT, url TEXT,"
        " job_status TEXT, job_desc TEXT, detail_html TEXT, publish_time TEXT)"
    )
    for i in range(1, count + 1):
        conn.execute(
            "INSERT INTO job (id, title, city, url, job_status, job_desc)"
            " VALUES (?, ?, ?, ?, 'NEW', '标题级描述')",
            (i, f"岗位{i}", "长沙", f"https://we.51job.com/pc/jobdetail?jobId={i}"),
        )
    conn.commit()
    conn.close()


def _install_fake_fetch(monkeypatch, sequence, *, with_html=True):
    """按顺序回放预置结果；返回计数器，用来证明「后面的条有没有被处理」。"""
    calls = {"n": 0}

    async def fake_fetch(self, url, *, keyword="", city="", allow_search=True):
        kind = sequence[calls["n"]]
        calls["n"] += 1
        result = df.DetailResult(job_id=df.extract_job_id(url))
        if kind == "ok":
            result.job_desc = "x" * 80
            result.source = "html"
            result.detail_html = ("<html>" + "y" * 60) if with_html else ""
            result.publish_time = datetime(2026, 11, 12)
        else:
            result.failure = kind
            result.reason = f"stub {kind}"
        return result

    monkeypatch.setattr(df.DetailFetcher, "fetch", fake_fetch)
    return calls


async def test_连续三条WAF立即停批并返回退出码4(tmp_path, monkeypatch, capsys):
    """连续 N 条被 WAF 拦就必须停 —— 硬顶只会把临时限流升级成封 IP。"""
    import sqlite3

    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 6)
    calls = _install_fake_fetch(
        monkeypatch, ["ok", "ok", df.KIND_WAF, df.KIND_WAF, df.KIND_WAF, "ok"]
    )

    args = script._parse_args([
        "--db", str(db_path), "--state", str(tmp_path / "s.json"),
        "--limit", "6", "--max-consecutive-blocked", "3", "--batch-size", "0",
    ])
    code = await script.run(args)

    assert code == 4, "因 WAF 停批应返回 4，与正常完成（0）区分开"
    assert calls["n"] == 5, f"第 3 条 WAF 之后就该停，实际处理了 {calls['n']} 条"

    conn = sqlite3.connect(str(db_path))
    done = conn.execute("select count(*) from job where length(coalesce(job_desc,''))>=30").fetchone()[0]
    status_changed = conn.execute("select count(*) from job where job_status<>'NEW'").fetchone()[0]
    conn.close()
    assert done == 2
    assert status_changed == 0

    out = capsys.readouterr().out
    assert "WAF/风控" in out and "立即停止本批" in out


async def test_连续登录墙同样触发停批(tmp_path, monkeypatch, capsys):
    """实测发现的关键一条：51job 的软限流是「302 到 login.51job.com」而不是 WAF 页。

    只把 WAF 计入止损的话，这条永远不触发 —— 实际上会连打 14 条全部被拦。
    """
    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 8)
    calls = _install_fake_fetch(
        monkeypatch, ["ok"] + [df.KIND_LOGIN] * 7
    )

    args = script._parse_args([
        "--db", str(db_path), "--state", str(tmp_path / "s.json"),
        "--limit", "8", "--max-consecutive-blocked", "3", "--batch-size", "0",
    ])
    code = await script.run(args)

    assert code == 4
    assert calls["n"] == 4, f"1 条成功 + 3 条登录墙后就该停，实际 {calls['n']} 条"
    assert "被站点拦截" in capsys.readouterr().out


async def test_单条WAF不触发停批(tmp_path, monkeypatch):
    """单条 WAF 可能只是抽风，不该一条就把整批停掉。"""
    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 4)
    calls = _install_fake_fetch(monkeypatch, ["ok", df.KIND_WAF, "ok", "ok"])

    args = script._parse_args([
        "--db", str(db_path), "--state", str(tmp_path / "s.json"),
        "--limit", "4", "--max-consecutive-blocked", "3", "--batch-size", "0",
    ])
    assert await script.run(args) == 0
    assert calls["n"] == 4


async def test_失败按类别聚合且写进状态文件(tmp_path, monkeypatch, capsys):
    import json

    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 3)
    state_path = tmp_path / "s.json"
    _install_fake_fetch(monkeypatch, [df.KIND_WAF, df.KIND_NETWORK, df.KIND_NO_FIELD])

    args = script._parse_args([
        "--db", str(db_path), "--state", str(state_path),
        "--limit", "3", "--max-consecutive-blocked", "9", "--batch-size", "0",
    ])
    assert await script.run(args) == 0

    out = capsys.readouterr().out
    for label in ("WAF/风控", "网络错误", "无该字段"):
        assert label in out, f"汇总里没有 {label} 这一档"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["failed"]["1"]["kind"] == df.KIND_WAF
    assert "stub" in state["failed"]["1"]["reason"]


async def test_resume按状态文件跳过_与库判据互相独立(tmp_path, monkeypatch):
    """状态文件是第二道判据：库里这一行仍「缺正文」时，``--resume`` 也应跳过它。

    为什么要独立于库判据：正文可能被人手工改短、或被后来的导入覆盖；
    此时库里看着像"没补过"，但状态文件知道已经成功过，不该再打一次请求。
    """
    import json

    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 3)
    state_path = tmp_path / "s.json"
    state_path.write_text(json.dumps({"ok": [1], "failed": {}}), encoding="utf-8")

    seen = []

    async def fake_fetch(self, url, *, keyword="", city="", allow_search=True):
        job_id = df.extract_job_id(url)
        seen.append(job_id)
        result = df.DetailResult(job_id=job_id)
        result.job_desc = "x" * 80
        result.source = "html"
        return result

    monkeypatch.setattr(df.DetailFetcher, "fetch", fake_fetch)
    base = ["--db", str(db_path), "--state", str(state_path), "--batch-size", "0"]

    # 带 --resume：id=1 在状态文件里已 ok → 只处理 2、3
    assert await script.run(script._parse_args(base + ["--limit", "5", "--resume"])) == 0
    assert seen == ["2", "3"], f"实际处理：{seen}"


def test_旧格式状态文件仍可读(tmp_path):
    """老状态文件里 failed 是纯字符串（没有 kind）——读它不能炸。"""
    import json

    script = _load_backfill_script()
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"ok": [1, 2], "failed": {"3": "旧原因"}}), encoding="utf-8")

    state = script._load_state(path)
    assert state["failed"]["3"] == "旧原因"
    assert state["ok"] == [1, 2]


def test_脚本源码不含GBK编不了的字符():
    """Windows 控制台是 GBK：源码里放 emoji 会在 print 时抛 UnicodeEncodeError。

    2026-11-12 真实踩到：止损分支里的 🛑 让一次 5 条的批次崩在收尾处，
    成功/失败统计一行都没打印出来 —— 跑几十分钟的批次等于白跑。
    """

    def _not_gbk(ch: str) -> bool:
        try:
            ch.encode("gbk")
            return False
        except UnicodeEncodeError:
            return True

    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "backfill_detail.py")
    text = source.read_text(encoding="utf-8")
    bad = sorted({ch for ch in text if _not_gbk(ch)})
    assert not bad, f"这些字符 GBK 编不了，print 时会崩：{[hex(ord(c)) for c in bad]}"


async def test_resume先把没试过的跑完再重试失败的(tmp_path, monkeypatch):
    """失败项排最后：否则被登录墙挡住时，每次运行都原地重试同一批，新数据永远推不动。"""
    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 4)
    state_path = tmp_path / "s.json"

    seen = []

    async def fake_fetch(self, url, *, keyword="", city="", allow_search=True):
        job_id = df.extract_job_id(url)
        seen.append(job_id)
        result = df.DetailResult(job_id=job_id)
        result.failure = df.KIND_NO_FIELD
        result.reason = "stub"
        return result

    monkeypatch.setattr(df.DetailFetcher, "fetch", fake_fetch)
    base = ["--db", str(db_path), "--state", str(state_path), "--limit", "2", "--batch-size", "0"]

    assert await script.run(script._parse_args(base)) == 0
    assert seen == ["1", "2"], "首轮应处理 id=1、2"

    # 第二轮带 --resume：1、2 已失败过 → 排最后，先把 3、4 推进
    assert await script.run(script._parse_args(base + ["--resume"])) == 0
    assert seen == ["1", "2", "3", "4"], f"实际处理顺序：{seen}"


async def test_确认详情页需登录后不再白打前两步(monkeypatch):
    """第一次判明「详情页要登录」后，后续条目直接走搜索兜底。

    未登录态下详情路径 100% 登录墙：不记这个结论的话，每条岗位都要多打两次
    必失败的请求（各占一个域名槽位，20 秒间隔下每条白花 40 秒）。
    """
    calls = {"safe_get": 0, "render": 0, "search": 0}

    async def fake_safe_get(url, **kwargs):
        calls["safe_get"] += 1
        # 登录页：URL 指向 login + 密码表单（结构性判据命中）
        return _FakeResponse(
            '<html><body><input type="password" name="pwd"></body></html>',
            url="https://login.51job.com/login.php?url=x",
        )

    async def fake_search(self, keyword, city, job_id):
        calls["search"] += 1
        return "x" * 80, None, ""

    async def fake_render(self, url):
        calls["render"] += 1
        return None

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", fake_render)
    monkeypatch.setattr(DetailFetcher, "_search_by_keyword", fake_search)

    fetcher = DetailFetcher(min_interval=0)
    first = await fetcher.fetch("https://we.51job.com/pc/jobdetail?jobId=1", keyword="岗位A")
    second = await fetcher.fetch("https://we.51job.com/pc/jobdetail?jobId=2", keyword="岗位B")

    assert first.ok and second.ok          # 两次都由搜索兜底救回来
    assert calls["safe_get"] == 1, "第二次不该再打详情 HTML（已知要登录）"
    assert calls["search"] == 2
    assert any("已确认详情页需登录" in a for a in second.attempts)

    # 关掉这个优化后必须恢复逐条都试（以后带登录态时用得上）
    strict = DetailFetcher(min_interval=0, skip_login_walled_detail=False)
    await strict.fetch("https://we.51job.com/pc/jobdetail?jobId=3", keyword="岗位C")
    await strict.fetch("https://we.51job.com/pc/jobdetail?jobId=4", keyword="岗位D")
    assert calls["safe_get"] == 3


def test_正文已够长但缺detail_html不算待回填(tmp_path):
    """判据只按正文长度：搜索接口永远给不出整页 HTML，拿 detail_html 卡就会「永远填不完」。

    实测：成功回填 52 条后 detail_html 仍为 0/555，若判据含 detail_html，
    每一行都还是「待回填」，全量预估永远停在 555 条。
    """
    import sqlite3

    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 3)

    conn = sqlite3.connect(str(db_path))
    # id=1 已补好正文、但没有 HTML（搜索接口的典型结果）
    conn.execute("update job set job_desc=? where id=1", ("正文" * 40,))
    conn.commit()
    conn.close()

    conn = script._connect(db_path)
    pending = [r["id"] for r in script._pending_rows(conn, 0, set())]
    conn.close()
    assert pending == [2, 3], f"只差 detail_html 的行不该再抓，实际待办：{pending}"


async def test_端到端只写三列(tmp_path, monkeypatch):
    """回填只 UPDATE job_desc / publish_time / detail_html，其余列一个字不动。"""
    import sqlite3

    script = _load_backfill_script()
    db_path = tmp_path / "t.db"
    _make_backfill_db(db_path, 1)
    conn = sqlite3.connect(str(db_path))
    conn.execute("update job set title='原岗位标题' where id=1")
    conn.commit()
    conn.close()

    _install_fake_fetch(monkeypatch, ["ok"])
    args = script._parse_args([
        "--db", str(db_path), "--state", str(tmp_path / "s.json"),
        "--limit", "1", "--batch-size", "0",
    ])
    assert await script.run(args) == 0

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "select title, job_status, length(job_desc), length(coalesce(detail_html,'')), publish_time"
        " from job"
    ).fetchone()
    conn.close()
    assert row[0] == "原岗位标题"      # 标题不变
    assert row[1] == "NEW"            # 状态不变（红线）
    assert row[2] >= 80               # 正文被写入
    assert row[3] > 0                 # HTML 被写入
    assert row[4] is not None         # 发布时间被写入


# ==========================================================================
# 2026-11-12：详情页的风控判据改成**结构性**的
#
# 旧写法直接复用 `BaseCrawler.detect_blocked`（扫正文前 4000 字符里的
# 「风险」「校验」「验证码」）。那是给列表页写的：列表页前 4000 字符是
# head/nav/CSS，岗位正文进不了窗口；详情页正相反 —— 正文就是被判对象。
#
# 实测（同一段正文，只改它在 HTML 里的位置）：
#   正文在 184 字符处   → 判成站点风控，desc_len=0，数据被丢
#   正文在 12799 字符处 → 不被判风控，正常抓到 59 字符
# 判不判取决于字节偏移，这就是本次要修的东西。
# ==========================================================================

# 一段**真实招聘正文**：风控 / 数据校验 / 验证码 / 滑块 全都出现
REAL_JOB_BODY = (
    "<div class='job-detail'>"
    "<h1>算法工程师（风控方向）</h1>"
    "<div class='job_msg'>"
    "岗位职责：负责风控模型的数据校验与规则引擎开发，处理验证码识别、滑块对抗等"
    "风险控制场景；参与反欺诈策略迭代，输出风险特征与模型评估报告。"
    "任职要求：熟悉常见风控指标体系，有数据校验与校验规则设计经验。"
    "</div></div>"
)


def _detail_page(head_extra: str = "") -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>算法工程师（风控方向）-某某科技-前程无忧</title>"
        f"{head_extra}</head><body>{REAL_JOB_BODY}</body></html>"
    )


def test_正文里的风控与校验不再被判成站点风控():
    """关键回归：含「风控」「数据校验」「验证码」「滑块」的**正常岗位**不能被判拦截。"""
    page = _detail_page()
    assert "风控" in page and "验证码" in page

    assert df.detect_blocked_structural(page, 200, "https://we.51job.com/pc/jobdetail?jobId=1") == ("", "")


def test_判定与正文落在第几个字符无关():
    """同一段正文，精简页与 12KB head 页都必须判「没被拦」。

    旧实现下这两个位置会得到相反结论 —— 这是「抽签」而不是判据。
    """
    compact = _detail_page()
    bulky = _detail_page("<style>" + "a{color:#333}\n" * 900 + "</style>")
    # 用**正文里的句子**量偏移（标题里也有「风控方向」，那不是判定窗口的关注点）
    marker = "负责风控模型的数据校验"
    assert compact.find(marker) < 4000
    assert bulky.find(marker) > 4000

    for label, page in (("精简", compact), ("12KB head", bulky)):
        assert df.detect_blocked_structural(page, 200, "https://x/jobdetail?jobId=1") == ("", ""), label
        parsed = parse_detail_html(page)
        assert parsed is not None, f"{label}：正文应该能解析出来"
        assert "风控模型" in parsed[0], label


def test_真WAF页仍必须被识别():
    """收窄判据不能把真拦截一起放过 —— 这是修改的底线。"""
    waf = ('<textarea id="renderData" style="display:none">'
           '{"_waf_bd8ce2ce37":"abc"}</textarea><html><body></body></html>')
    kind, evidence = df.detect_blocked_structural(waf, 200, "https://we.51job.com/pc/jobdetail?jobId=1")
    assert kind == df.KIND_WAF
    assert "骨架" in evidence


def test_429判WAF():
    kind, _ = df.detect_blocked_structural("", 429, "https://x/1")
    assert kind == df.KIND_WAF


def test_登录页URL判登录墙():
    kind, evidence = df.detect_blocked_structural(
        "<html><body></body></html>", 200,
        "https://we.51job.com/web/user/?from=passport&redirect=/pc/jobdetail",
    )
    assert kind == df.KIND_LOGIN and "登录" in evidence


def test_密码表单判登录墙():
    html = "<html><body><form><input type='text' name='u'><input type=\"password\" name='p'></form></body></html>"
    kind, _ = df.detect_blocked_structural(html, 200, "https://x/1")
    assert kind == df.KIND_LOGIN


def test_标题是验证页判WAF():
    kind, _ = df.detect_blocked_structural("<html></html>", 200, "https://x/1", "安全验证")
    assert kind == df.KIND_WAF


def test_安全校验跳转判WAF():
    kind, _ = df.detect_blocked_structural(
        "", 200, "https://we.51job.com/web/geek/jobs?query=x&_security_check=6_123"
    )
    assert kind == df.KIND_WAF


# ---------------------------------------------------------------- fetch 路径


async def test_fetch_含风控正文直接成功且不触发降速(monkeypatch):
    """走完整 fetch：静态 HTML 就能解析成功，不该再去渲染/搜索，也不该 mark_blocked。"""

    async def fake_safe_get(url, **kwargs):
        return _FakeResponse(_detail_page(), url="https://we.51job.com/pc/jobdetail?jobId=171818429")

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", _no_render)
    monkeypatch.setattr(DetailFetcher, "_search_by_keyword", _no_search)

    before = df.BaseCrawler._blocked_signals
    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=171818429")

    assert result.ok is True, result.attempts
    assert result.source == "html"
    assert result.failure == ""
    assert df.BaseCrawler._blocked_signals == before, "正常岗位不该被当成风控而降速"
    assert "风控模型" in result.job_desc


async def test_failure_网络错误(monkeypatch):
    async def boom(url, **kwargs):
        raise TimeoutError("read timeout")

    async def none_render(self, url):
        return None

    monkeypatch.setattr(df, "safe_get", boom)
    monkeypatch.setattr(DetailFetcher, "_render_detail", none_render)

    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=1")
    assert result.ok is False
    assert result.failure == df.KIND_NETWORK


async def test_failure_解析失败(monkeypatch):
    """SPA 空壳：拿到了页面但结构不认，且没有标题可走搜索兜底。"""

    async def fake_safe_get(url, **kwargs):
        return _FakeResponse(_read_fixture(), url=url)

    async def none_render(self, url):
        return None

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", none_render)

    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=1")
    assert result.ok is False
    assert result.failure == df.KIND_PARSE


async def test_failure_WAF_并降速(monkeypatch):
    waf = '<textarea id="renderData">{"_waf_bd8ce2ce37":"x"}</textarea><html></html>'

    async def fake_safe_get(url, **kwargs):
        return _FakeResponse(waf, url=url)

    async def none_render(self, url):
        return None

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", none_render)

    before = df.BaseCrawler._blocked_signals
    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=1")
    assert result.failure == df.KIND_WAF
    assert df.BaseCrawler._blocked_signals == before + 1


async def test_failure_登录墙(monkeypatch):
    login = "<html><body><input type=\"password\" name=\"pwd\"></body></html>"

    async def fake_safe_get(url, **kwargs):
        return _FakeResponse(login, url="https://we.51job.com/pc/jobdetail?jobId=1")

    async def none_render(self, url):
        return None

    monkeypatch.setattr(df, "safe_get", fake_safe_get)
    monkeypatch.setattr(DetailFetcher, "_render_detail", none_render)

    result = await DetailFetcher().fetch("https://we.51job.com/pc/jobdetail?jobId=1")
    assert result.failure == df.KIND_LOGIN
