"""浏览器采集通道（油猴脚本 / 扩展）的测试。

这条通道的特殊之处在于它**刻意与爬虫链路有几处不同**，而这几处不同正是测试重点：

1. **必须与爬虫共用同一个平台标识** —— 否则 job_key 不同，同一个岗位会在库里
   出现两行（爬虫采一次、浏览器采一次），统计直接翻倍；
2. **必须不触发下架判定** —— 浏览器采集是部分数据，拿它判断下架会误杀存量岗位；
3. **必须走同一套清洗与过滤** —— 两个入口的数据口径不能漂移；
4. **接口默认关闭** —— 没配令牌就不该有任何写入路径。
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.crawlers.cleaner import generate_job_key
from app.models.job import Job
from app.services import crawler_service

from tests.helpers import admin_token, auth_headers

TOKEN = "test-collect-token-should-be-long-enough"


@pytest.fixture
def collect_on(monkeypatch):
    """打开采集入口并给一个合法令牌。"""
    monkeypatch.setattr(settings, "browser_collect_token", TOKEN)
    monkeypatch.setattr(settings, "browser_collect_max_batch", 500)


@pytest.fixture
def collect_off(monkeypatch):
    monkeypatch.setattr(settings, "browser_collect_token", "")


def make_job(index: int = 0, **overrides) -> dict:
    job = {
        "title": f"Java 后端工程师{index}",
        "company_name": f"某公司{index}",
        "city": "长沙",
        "salary": "15K-25K",
        "experience": "1-3年",
        "education": "本科",
        "skills": "Java,SpringBoot",
        "url": f"https://www.zhipin.com/job_detail/{index}.html",
        "description": f"Java 后端开发{index}",
    }
    job.update(overrides)
    return job


async def post_ingest(client, body: dict, token: str = TOKEN):
    return await client.post(
        "/api/crawler/ingest", json=body, headers={"X-Collect-Token": token}
    )


# --------------------------------------------------------------------------
# 入口开关与鉴权
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_未配置令牌时入口关闭(client, collect_off):
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [make_job()]})
    assert resp.status_code == 403
    assert "BROWSER_COLLECT_TOKEN" in resp.json()["message"]


@pytest.mark.asyncio
async def test_令牌不正确时拒绝(client, collect_on):
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [make_job()]}, token="wrong")
    assert resp.status_code == 403
    assert "令牌" in resp.json()["message"]


@pytest.mark.asyncio
async def test_缺少令牌头时拒绝(client, collect_on):
    resp = await client.post("/api/crawler/ingest", json={"source_site": "boss", "jobs": []})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_不需要_jwt_即可调用(client, collect_on, db_session):
    """油猴脚本没法登录本系统拿 JWT，所以这条入口用采集令牌鉴权。"""
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [make_job()]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["saved"] == 1


# --------------------------------------------------------------------------
# 平台与批量校验
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_未知平台被拒绝并给出提示(client, collect_on):
    resp = await post_ingest(client, {"source_site": "boss-browser", "jobs": [make_job()]})
    assert resp.status_code == 400
    message = resp.json()["message"]
    assert "boss-browser" in message
    # 报错必须解释为什么不能自造平台名 —— 否则使用者会一直以为这只是个校验格式问题
    assert "job_key" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["zhaopin", "liepin"])
async def test_未写爬虫的平台也能通过浏览器通道入库(client, collect_on, db_session, platform):
    """「已知平台」与「爬虫已实现平台」是两个集合。

    浏览器采集不需要服务端有对应爬虫 —— 数据是用户在真实浏览器里取好回传的。
    若按「爬虫已实现」校验，想支持智联/猎聘就得先写一个完整的 Playwright 爬虫，
    这显然不合理。这条用例锁住这个区分。
    """
    resp = await post_ingest(client, {
        "source_site": platform,
        "city": "长沙",
        "jobs": [make_job(1, title=f"{platform} 上的岗位")],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["saved"] == 1

    job = (await db_session.execute(
        select(Job).where(Job.source_site == platform)
    )).scalars().one()
    assert job.title == f"{platform} 上的岗位"


@pytest.mark.asyncio
async def test_爬虫未实现的平台仍不能启动爬取任务(client, db_session):
    """反过来也要成立：浏览器通道能收，不代表能对它启动爬取任务。

    否则接口会把用户引向一个必然失败的任务（`get_crawler` 会 KeyError）。
    两个集合分得清，才能一边「收数据」一边「不承诺抓取」。
    """
    from tests.helpers import admin_token as _admin_token

    token = await _admin_token(client, db_session, "admin_zhaopin_crawl")
    resp = await client.post(
        "/api/crawler/start",
        json={"keyword": "Java", "city": "长沙", "platforms": ["zhaopin"]},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["status"] == "FAILED"
    assert "zhaopin" in (data["message"] or "")
    # 失败原因必须可读 —— 只回一个「成功 0 条」会让用户无从排查
    assert "未实现" in (data["message"] or "")


def test_油猴脚本的站点适配器与后端平台集合一致():
    """跨产物契约测试：脚本里每个 platform 都必须是后端认可的「已知平台」。

    两边是独立的文件，很容易出现「脚本加了新站点、后端不认识」的漂移 ——
    那种情况下用户点采集只会拿到一个 400，且从脚本面板看不出来原因。
    """
    import re
    from pathlib import Path

    from app.services.crawler_service import KNOWN_PLATFORMS

    script = Path(__file__).resolve().parent.parent / "userscript" / "recsys-collector.user.js"
    text = script.read_text(encoding="utf-8")
    declared = set(re.findall(r"platform:\s*'([a-z0-9]+)'", text))

    assert declared, "未能从脚本中解析出任何 platform（正则或脚本结构变了）"
    unknown = declared - KNOWN_PLATFORMS
    assert not unknown, f"脚本声明了后端不认识的平台：{sorted(unknown)}"


def test_油猴脚本的站点适配器数量与说明一致():
    """脚本声明的站点数应与 README 里列的站点数一致，避免文档过期。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    script = (root / "userscript" / "recsys-collector.user.js").read_text(encoding="utf-8")
    readme = (root / "userscript" / "README.md").read_text(encoding="utf-8")

    names = re.findall(r"name:\s*'([^']+)'", script)
    assert len(names) >= 4, f"适配器数量异常：{names}"
    for name in names:
        assert name in readme, f"适配器「{name}」未在 userscript/README.md 中出现，文档已过期"


@pytest.mark.asyncio
async def test_超出批量上限被拒绝(client, collect_on, monkeypatch):
    monkeypatch.setattr(settings, "browser_collect_max_batch", 2)
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [make_job(i) for i in range(3)]})
    assert resp.status_code == 400
    assert "2" in resp.json()["message"]


@pytest.mark.asyncio
async def test_批量上限为0视为不限制(client, collect_on, monkeypatch):
    monkeypatch.setattr(settings, "browser_collect_max_batch", 0)
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [make_job(i) for i in range(5)]})
    assert resp.status_code == 200
    assert resp.json()["data"]["saved"] == 5


# --------------------------------------------------------------------------
# 入库链路与口径一致性
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_入库并返回四项计数(client, collect_on, db_session):
    resp = await post_ingest(client, {
        "source_site": "boss",
        "city": "长沙",
        "keyword": "Java",
        "jobs": [make_job(1), make_job(2)],
    })
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data == {"received": 2, "saved": 2, "duplicate": 0, "filtered": 0, "invalid": 0}

    rows = (await db_session.execute(select(Job))).scalars().all()
    assert len(rows) == 2
    assert all(r.source_site == "boss" for r in rows)


@pytest.mark.asyncio
async def test_与爬虫用同一平台标识时同一个岗位被去重(client, collect_on, db_session):
    """这是本通道最关键的一致性约束。

    浏览器采到的岗位与爬虫采到的若是同一个，必须落成**同一行**。
    若脚本自造 "boss-browser" 之类的平台名，job_key 就会不同，
    库里出现两行、统计翻倍 —— 这条用例锁死这个口径。
    """
    # 先模拟爬虫入库一条
    crawler_job = {
        "title": "Java 后端工程师X",
        "company_name": "某某科技",
        "city": "长沙",
        "experience": "1-3年",
        "education": "本科",
        "salary": "15K-25K",
        "skills": "Java",
        "source_site": "boss",
        "url": "https://www.zhipin.com/job_detail/x.html",
        "description": "Java 后端",
    }
    from app.crawlers.cleaner import clean_job_data

    cleaned = clean_job_data(crawler_job)
    cleaned["job_key"] = generate_job_key("boss", "Java 后端工程师X", "某某科技", "长沙")
    assert await crawler_service.save_job(db_session, cleaned) is True
    await db_session.commit()

    # 浏览器再用同一个岗位提交一次（标题/公司/城市相同即可，URL 不同也应去重）
    resp = await post_ingest(client, {
        "source_site": "boss",
        "jobs": [{
            "title": "Java 后端工程师X",
            "company_name": "某某科技",
            "city": "长沙",
            "salary": "15K-25K",
            "experience": "1-3年",
            "education": "本科",
            "url": "https://www.zhipin.com/job_detail/another-url.html",
        }],
    })
    assert resp.json()["data"]["duplicate"] == 1
    assert resp.json()["data"]["saved"] == 0

    rows = (await db_session.execute(select(Job))).scalars().all()
    assert len(rows) == 1, "同一岗位不得因为采集来源不同而落成两行"


@pytest.mark.asyncio
async def test_走同一套过滤规则(client, collect_on, db_session):
    """高级岗位 / 无效岗位 / 薪资异常三道规则要与爬虫一致。"""
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [
        make_job(1),
        make_job(2, title="高级架构师"),                       # 高级岗位 → 过滤
        make_job(3, title="Java 培训讲师"),                     # 无效岗位 → 过滤
        make_job(4, salary="500K-800K"),                       # 薪资异常 → 过滤
    ]})
    data = resp.json()["data"]
    assert data["saved"] == 1
    assert data["filtered"] == 3


@pytest.mark.asyncio
async def test_无标题条目计为无效(client, collect_on):
    resp = await post_ingest(client, {"source_site": "boss", "jobs": [
        make_job(1),
        {"company_name": "只有公司"},
        {"title": "   "},
        {},
    ]})
    data = resp.json()["data"]
    assert data["received"] == 4
    assert data["saved"] == 1
    assert data["invalid"] == 3


@pytest.mark.asyncio
async def test_非对象条目在_schema_层就被拦下(client, collect_on):
    """`jobs: list[dict]` 会在入口把非对象条目挡掉，不会流到服务层。"""
    resp = await post_ingest(client, {"source_site": "boss", "jobs": ["这不是对象"]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_服务层仍自行防御非对象条目(collect_on):
    """即使将来放宽了接口 schema，服务层也不该因为一条脏数据整批失败。

    直接调服务层验证这层防御是真实存在的（而不是靠接口 schema 兜住）。
    """
    from unittest.mock import AsyncMock

    db = AsyncMock()
    result = await crawler_service.ingest_browser_jobs(db, [None, 123, "字符串"], "boss", "")

    assert result["received"] == 3
    assert result["invalid"] == 3
    assert result["saved"] == 0


@pytest.mark.asyncio
async def test_城市缺失时回退到请求级城市(client, collect_on, db_session):
    resp = await post_ingest(client, {
        "source_site": "boss",
        "city": "上海",
        "jobs": [make_job(1, city="")],
    })
    assert resp.json()["data"]["saved"] == 1

    job = (await db_session.execute(select(Job))).scalars().one()
    assert job.city == "上海"


@pytest.mark.asyncio
async def test_空批次不报错(client, collect_on):
    resp = await post_ingest(client, {"source_site": "boss", "jobs": []})
    assert resp.status_code == 200
    assert resp.json()["data"]["received"] == 0


# --------------------------------------------------------------------------
# 不得触发下架判定
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_浏览器采集不得把存量岗位误判下架(client, collect_on, db_session):
    """浏览器采集是部分数据，不能用来判断「哪些岗位没再出现」。

    爬虫可以判断下架是因为它按「关键词 × 城市」系统性抓取、覆盖范围已知；
    浏览器采集只覆盖用户浏览到的那几页。若这里误触下架判定，
    库里所有没被本次浏览覆盖的岗位会被整批标记 OFFLINE —— 属于大规模数据损坏。
    """
    # 一条早已存在的岗位（last_seen_at 很旧，按爬虫的口径「早该判下架了」）
    from datetime import timedelta

    stale = make_job(99, title="很久以前采过的岗位", company_name="老公司", city="北京")
    from app.crawlers.cleaner import clean_job_data

    cleaned = clean_job_data({**stale, "source_site": "boss"})
    cleaned["job_key"] = generate_job_key("boss", cleaned["title"], cleaned["company_name"], "北京")
    assert await crawler_service.save_job(db_session, cleaned) is True
    await db_session.commit()

    old_job = (await db_session.execute(
        select(Job).where(Job.job_key == cleaned["job_key"])
    )).scalar_one()
    old_job.last_seen_at = crawler_service._utc_now() - timedelta(days=30)
    await db_session.commit()

    # 浏览器只采了一个完全不同的岗位
    resp = await post_ingest(client, {
        "source_site": "boss",
        "city": "长沙",
        "jobs": [make_job(1)],
    })
    assert resp.json()["data"]["saved"] == 1

    await db_session.refresh(old_job)
    assert old_job.job_status != "OFFLINE", "浏览器采集绝不能触发下架判定"


# --------------------------------------------------------------------------
# 接口其它约束
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_平台可选项里不含自造平台名(client, db_session):
    """前端下拉框来自 /options，不该出现 boss-browser 这类名目。"""
    from tests.helpers import admin_token as _admin_token

    token = await _admin_token(client, db_session, "admin_collect_opt")
    resp = await client.get("/api/crawler/options", headers=auth_headers(token))
    values = {p["value"] for p in resp.json()["data"]["platforms"]}
    assert "boss" in values and "51job" in values
    assert not any("browser" in v for v in values)


@pytest_asyncio.fixture
async def admin_client(client, db_session):
    token = await admin_token(client, db_session, "admin_ingest")
    client.headers.update(auth_headers(token))
    return client


@pytest.mark.asyncio
async def test_采集数据在岗位列表里可见(admin_client, collect_on, db_session):
    """端到端：脚本采的数据要能像普通数据一样被查询到。"""
    await post_ingest(admin_client, {"source_site": "boss", "jobs": [make_job(1)]})

    # 岗位列表是 POST /api/jobs/page（分页查询走 POST，与 Java 版契约一致）
    resp = await admin_client.post("/api/jobs/page", json={"page_num": 1, "page_size": 10})
    assert resp.status_code == 200, resp.text
    titles = [item["title"] for item in resp.json()["data"]["list"]]
    assert "Java 后端工程师1" in titles
