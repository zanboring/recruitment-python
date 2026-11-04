"""爬取前置校验测试。

锁定的三类「静默给错东西」的行为：

1. **未收录城市** —— 原实现回退到北京的城市编码，搜「南昌」实际爬的是北京岗位，
   数据看起来正常但城市是错的，且不报错不留痕。
2. **未实现平台** —— 原实现 `continue` 跳过，任务仍标记 COMPLETED / 0 条，
   前端只看到「已完成」而不知道原因。
3. **后台任务被 GC** —— `asyncio.create_task(...)` 丢弃返回值时，事件循环只持
   弱引用，任务可能在执行途中被回收，且日志里没有任何异常。

共同点：都不能靠「看日志」发现，必须在代码层面拦成明确结果。
"""
import asyncio
import logging

import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.job import Job
from app.crawlers.cleaner import is_senior_job
from app.crawlers.city_map import (
    CITY_CODE_MAP,
    UnsupportedCityError,
    get_city_code,
    require_city_code,
    supported_cities,
)
from app.services import crawler_service
from app.services.crawler_service import SUPPORTED_PLATFORMS


# ---------- 城市映射：不存在「默认回退」 ----------

def test_未收录城市返回None而不是别的城市():
    """回归核心：绝不能回退到任何具体城市（原实现回退北京）。"""
    assert get_city_code("南昌") is None
    assert get_city_code("南宁") is None
    assert get_city_code("不存在的城市") is None
    assert get_city_code("") is None
    assert get_city_code(None) is None


def test_已收录城市正常返回编码():
    assert get_city_code("长沙") == "101250100"
    assert get_city_code(" 北京 ") == CITY_CODE_MAP["北京"]   # 容忍前后空格


def test_未收录城市取值时抛出带支持清单的错误():
    with pytest.raises(UnsupportedCityError) as exc:
        require_city_code("南昌")

    message = str(exc.value)
    assert "南昌" in message
    assert "长沙" in message              # 列出支持的城市，便于直接改正
    assert "city_map.py" in message       # 指出在哪里补


def test_空城市取值时抛出明确错误():
    with pytest.raises(UnsupportedCityError) as exc:
        require_city_code("")
    assert "必须指定城市" in str(exc.value)


def test_支持城市清单非空且与映射表一致():
    cities = supported_cities()
    assert len(cities) == len(CITY_CODE_MAP)
    assert cities == sorted(cities)
    assert "长沙" in cities


@pytest.mark.asyncio
async def test_爬虫对未收录城市直接抛错而不是爬别处():
    """校验必须发生在真正发起网络请求之前。

    测试环境没有安装 playwright，因此如果校验被放在 playwright 导入之后，
    这里抛出的会是 ImportError 而不是 UnsupportedCityError ——
    仅靠异常类型就能证明校验的先后顺序，无需真的联网。
    """
    from app.crawlers.boss import BossCrawler

    with pytest.raises(UnsupportedCityError):
        await BossCrawler().crawl("Java", "南昌")


# ---------- 任务级前置校验 ----------

@pytest.mark.asyncio
async def test_未收录城市让任务明确失败(db_session):
    task = await crawler_service.create_pending_task(db_session, "Java", "南昌", ["boss"])
    count = await crawler_service.run_crawl(db_session, task, "Java", "南昌", ["boss"])

    assert count == 0
    assert task.status == "FAILED"
    assert "南昌" in task.message
    assert "长沙" in task.message          # 提示支持范围


@pytest.mark.asyncio
async def test_全部平台未实现时任务失败并写明原因(db_session):
    """前端可选 zhaopin/liepin（尚未实现），此任务必须明确失败。"""
    task = await crawler_service.create_pending_task(
        db_session, "Java", "长沙", ["zhaopin", "liepin"]
    )
    count = await crawler_service.run_crawl(db_session, task, "Java", "长沙", ["zhaopin", "liepin"])

    assert count == 0
    assert task.status == "FAILED"
    assert "zhaopin" in task.message and "liepin" in task.message
    assert "boss" in task.message          # 告诉调用方实际支持什么


@pytest.mark.asyncio
async def test_部分平台未实现时完成但说明跳过项(db_session, monkeypatch):
    """有可用平台时任务照常完成，但必须说明哪些被跳过了。"""
    async def fake_crawl_platform(db, keyword, city, platform):
        return 3, [f"{platform}_k1"], []

    monkeypatch.setattr(crawler_service, "_crawl_platform", fake_crawl_platform)
    monkeypatch.setattr(crawler_service, "_mark_offline", _noop_async)

    platforms = ["boss", "liepin"]
    task = await crawler_service.create_pending_task(db_session, "Java", "长沙", platforms)
    count = await crawler_service.run_crawl(db_session, task, "Java", "长沙", platforms)

    assert count == 3
    assert task.status == "COMPLETED"
    assert task.message and "liepin" in task.message
    assert "已跳过" in task.message


@pytest.mark.asyncio
async def test_全部平台可用时不产生多余的提示信息(db_session, monkeypatch):
    """只在真的有跳过项时才写 message，避免给正常任务塞无意义文案。"""
    async def fake_crawl_platform(db, keyword, city, platform):
        return 2, [f"{platform}_k1"], []

    monkeypatch.setattr(crawler_service, "_crawl_platform", fake_crawl_platform)
    monkeypatch.setattr(crawler_service, "_mark_offline", _noop_async)

    task = await crawler_service.create_pending_task(db_session, "Java", "长沙", ["boss"])
    await crawler_service.run_crawl(db_session, task, "Java", "长沙", ["boss"])

    assert task.status == "COMPLETED"
    assert task.message is None


@pytest.mark.asyncio
async def test_有可用平台时未收录城市仍然拦住(db_session, monkeypatch):
    """城市错误与平台错误是独立的两道闸门，不能因为平台可用就放过城市。"""
    async def fake_crawl_platform(db, keyword, city, platform):  # pragma: no cover
        raise AssertionError("不应开始爬取")

    monkeypatch.setattr(crawler_service, "_crawl_platform", fake_crawl_platform)

    task = await crawler_service.create_pending_task(db_session, "Java", "兰州", ["boss"])
    await crawler_service.run_crawl(db_session, task, "Java", "兰州", ["boss"])

    assert task.status == "FAILED"
    assert "兰州" in task.message


async def _noop_async(*args, **kwargs):
    return None


# ---------- 后台任务引用保持 ----------

@pytest.mark.asyncio
async def test_后台任务被强引用持有直到完成():
    """asyncio 只持弱引用，丢弃返回值会让任务可能被 GC 掉。"""
    from app.routers import compat

    started = asyncio.Event()
    finished = {"done": False}

    async def slow_job():
        started.set()
        await asyncio.sleep(0.05)
        finished["done"] = True

    compat._spawn_background(slow_job())
    await started.wait()

    # 启动后集合里必须有引用，否则任务随时可能被回收
    assert len(compat._background_tasks) == 1

    await asyncio.sleep(0.15)
    assert finished["done"] is True
    # 完成后自动移除，避免集合无限增长
    assert len(compat._background_tasks) == 0


# ---------- 接口 ----------

@pytest.mark.asyncio
async def test_可选项接口暴露真实支持范围(client, db_session):
    """平台以结构化列表返回（含中文名与是否已实现），城市为全量收录清单。

    这份信息必须由后端提供：前端硬编码时，能选到必然失败的可选项，同时又用不到
    一半的可用城市 —— 支持范围一变，前端就错，且没有任何提示。
    """
    from tests.helpers import admin_token, auth_headers

    token = await admin_token(client, db_session, "admin_crawlopt")
    resp = await client.get("/api/crawler/options", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    by_value = {p["value"]: p for p in data["platforms"]}

    # 已实现平台：带中文名、标记为可用
    assert by_value["boss"]["implemented"] is True
    assert by_value["boss"]["label"] == "BOSS直聘"

    # 未实现平台：仍然列出（前端可置灰并说明），但明确标记为不可用
    assert by_value["zhaopin"]["implemented"] is False
    assert by_value["zhaopin"]["label"] == "智联招聘"

    # 51job 已接入（app/crawlers/job51.py），应标记为可用
    assert by_value["51job"]["implemented"] is True
    assert by_value["51job"]["label"] == "前程无忧"

    # 已实现的排在前面 —— 前端可直接按顺序渲染。
    # 这里断言「排序性质」而不是写死某个平台名：新增平台时无需改测试。
    implemented_flags = [p["implemented"] for p in data["platforms"]]
    assert implemented_flags == sorted(implemented_flags, reverse=True)

    # 城市为全量收录清单，而不是前端那 11 个
    assert "长沙" in data["cities"]
    assert len(data["cities"]) >= 20


@pytest.mark.asyncio
async def test_兼容层的可选项接口与原生接口结构一致(client, db_session):
    """两个入口必须返回同一形状 —— 否则前端要为同一个概念写两套解析。"""
    from tests.helpers import admin_token, auth_headers

    token = await admin_token(client, db_session, "admin_crawlopt2")
    headers = auth_headers(token)

    native = (await client.get("/api/crawler/options", headers=headers)).json()["data"]
    compat = (await client.get("/api/crawl/options", headers=headers)).json()["data"]

    assert native == compat


@pytest.mark.asyncio
async def test_启动接口回传失败原因(client, db_session):
    """只返回 count 会让调用方看到「成功 0 条」却查不到为什么。"""
    from tests.helpers import admin_token, auth_headers

    token = await admin_token(client, db_session, "admin_crawlstart")
    resp = await client.post(
        "/api/crawler/start",
        json={"keyword": "Java", "city": "南昌", "platforms": ["boss"]},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["count"] == 0
    assert data["status"] == "FAILED"
    assert "南昌" in data["message"]
    assert data["task_id"] is not None


class TestPlatformNormalization:
    """平台标识归一化。

    同一个概念（平台）此前在 compat 层与原生层各有一份归一化实现，属于
    「两处实现同一规则」的典型隐患。现在收敛到 crawler_service 一处。
    """

    def test_中文名与常见拼写都能归一化(self):
        from app.services.crawler_service import normalize_platform

        for raw, expected in [
            ("boss", "boss"),
            ("BOSS", "boss"),
            ("BOSS直聘", "boss"),
            ("直聘", "boss"),
            ("zhipin", "boss"),
            ("zhaopin", "zhaopin"),
            ("智联招聘", "zhaopin"),
            ("智联", "zhaopin"),
            ("51job", "51job"),
            ("前程无忧", "51job"),
            ("猎聘", "liepin"),
        ]:
            assert normalize_platform(raw) == expected, raw

    def test_空值与None回退到已实现的平台(self):
        from app.services.crawler_service import normalize_platform

        assert normalize_platform("") == "boss"
        assert normalize_platform(None) == "boss"
        assert normalize_platform("   ") == "boss"

    def test_未知平台原样返回而不是回退到已实现平台(self):
        """回退会让「选智联」静默变成「爬 BOSS」，数据记错平台名 —— 必须原样返回，
        交给前置校验拦截并给出明确原因。"""
        from app.services.crawler_service import normalize_platform

        assert normalize_platform("somejob") == "somejob"
        assert normalize_platform("未知招聘网") == "未知招聘网"

    def test_平台选项覆盖全部已实现平台且优先展示(self):
        from app.services.crawler_service import platform_options

        options = platform_options()
        values = [o["value"] for o in options]

        # 已实现的平台一个都不能漏，否则前端渲染不出可选项
        assert SUPPORTED_PLATFORMS <= set(values)
        # 已实现的排在未实现的之前
        implemented_flags = [o["implemented"] for o in options]
        assert implemented_flags == sorted(implemented_flags, reverse=True)
        # 每项都有可直接展示的中文名
        assert all(o["label"] for o in options)


class TestPlatformNormalizationAtServiceLayer:
    """平台归一化必须在**服务层入口**生效，而不是只挂在 HTTP 兼容层上。

    此前归一化只写在 compat 层的 ``_normalize_platform`` 里，于是同一个输入会得到
    两种结果：走兼容接口传「BOSS直聘」能正确识别，走原生接口或直接调 service 却会
    被判成「平台未实现」而任务失败。同一规则在不同入口生效范围不同，是这类缺陷的
    典型成因 —— 也因此每个入口都必须能独立正确。
    """

    def test_批量归一化_去重并保持顺序(self):
        from app.services.crawler_service import normalize_platforms

        assert normalize_platforms(["BOSS直聘"]) == ["boss"]
        assert normalize_platforms(["boss", "boss"]) == ["boss"]
        assert normalize_platforms(["智联招聘", "BOSS"]) == ["zhaopin", "boss"]

    def test_批量归一化_空值被跳过而不是变成none平台(self):
        """str(None) 会得到 "None"，再 lower 成 "none" —— 一个空的平台项会变成
        一个叫 none 的平台，最后以「平台未实现：none」这种莫名其妙的理由失败。"""
        from app.services.crawler_service import normalize_platforms

        assert normalize_platforms(["", None, " "]) == []
        assert normalize_platforms([None]) == []
        assert normalize_platforms([]) == []
        assert normalize_platforms(None) == []
        assert normalize_platforms(["boss", None, "智联招聘"]) == ["boss", "zhaopin"]

    @pytest.mark.asyncio
    async def test_创建任务时即归一化后入库(self, db_session):
        """任务列表展示、下架判定、日志排查都读 source_site，存入未归一化的值
        会让同一个平台在不同任务里长得不一样（boss / BOSS直聘 混着出现）。"""
        task = await crawler_service.create_pending_task(
            db_session, "Java", "长沙", ["BOSS直聘", "boss"]
        )
        assert task.source_site == "boss"

    @pytest.mark.asyncio
    async def test_执行层不依赖调用方是否归一化(self, db_session):
        """run_crawl 是执行层的唯一入口（定时任务、后台重跑、直接调用都会经过），
        不能假设调用方已经处理过平台标识。"""
        task = await crawler_service.start_crawl_task(
            db_session, "Java", "长沙", ["智联招聘"]
        )
        # 中文名被识别成未实现的 zhaopin —— 而不是一个叫「智联招聘」的未知平台。
        # 后者虽然也会失败，但原因描述会变成「平台未实现：智联招聘」，
        # 与已实现清单里的标识对不上，排查时容易误以为前端传错了值。
        assert task.status == "FAILED"
        assert "zhaopin" in (task.message or "")

    @pytest.mark.asyncio
    async def test_平台中文名可用于展示(self, db_session):
        from app.services.crawler_service import platform_label

        assert platform_label("boss") == "BOSS直聘"
        assert platform_label("boss,zhaopin") == "BOSS直聘、智联招聘"
        # 未知标识原样返回，便于发现未登记的平台
        assert platform_label("unknown") == "unknown"


# ==========================================================================
# C1｜入库时建/复用 `company` 记录并回填 `job.company_id`
#
# **为什么这些用例放在本文件而不是新建文件**：`tests/test_docs_contract.py`
# 有一条契约 —— `docs/showcase.md` 声明的「N 个测试文件」必须等于
# `tests/test_*.py` 的实际个数；而 `docs/` 不在本轮的改动授权内（trae 在改）。
# 新建测试文件会**必然**让那条契约变红，所以并入本文件。
#
# **为什么需要这组用例**：`company` 表曾经长期是空表 —— 全仓库没有一处代码创建
# `Company`（`git grep "Company("` 只命中模型定义），`job.company_id` 唯一的赋值
# 入口是管理端手填的 `request.company_id`，实际没人填。
# ==========================================================================


def _company_job(key: str, company: str = "字节跳动", city: str = "北京", **extra) -> dict:
    """构造一条已归一化、可直接交给 save_job 的岗位数据。"""
    data = {
        "job_key": key,
        "title": "Python 后端工程师",
        "company_name": company,
        "city": city,
        "source_site": "boss",
        "min_salary": 15000,
        "max_salary": 25000,
    }
    data.update(extra)
    return data


async def _company_of(db, job_key: str):
    job = (await db.execute(select(Job).where(Job.job_key == job_key))).scalar_one()
    if job.company_id is None:
        return job, None
    company = (
        await db.execute(select(Company).where(Company.id == job.company_id))
    ).scalar_one_or_none()
    return job, company


@pytest.mark.asyncio
async def test_新岗位入库会建公司并回填company_id(db_session):
    assert await crawler_service.save_job(db_session, _company_job("boss_新岗位")) is True
    await db_session.commit()

    job, company = await _company_of(db_session, "boss_新岗位")
    assert job.company_id is not None, "company_id 必须被回填，否则 company 表依旧没人用"
    assert company is not None
    assert company.name == "字节跳动"
    # 首次出现的城市记在 company.city 上（不是总部，见 resolve_company_id 注释）
    assert company.city == "北京"


@pytest.mark.asyncio
async def test_同名公司复用同一条记录(db_session):
    """同一家公司在不同城市招人 → 一条 company，两个岗位指向它。"""
    assert await crawler_service.save_job(db_session, _company_job("boss_k1", city="北京")) is True
    assert await crawler_service.save_job(db_session, _company_job("boss_k2", city="上海")) is True
    await db_session.commit()

    companies = (await db_session.execute(select(Company))).scalars().all()
    assert len(companies) == 1, "同名公司必须是同一条记录，否则公司排行榜会被拆散"

    _, c1 = await _company_of(db_session, "boss_k1")
    _, c2 = await _company_of(db_session, "boss_k2")
    assert c1.id == c2.id


@pytest.mark.asyncio
async def test_公司名首尾空格不会分裂成两条(db_session):
    """爬取/上报常带空格：「字节跳动 」与「字节跳动」必须是同一家。"""
    assert await crawler_service.save_job(db_session, _company_job("boss_k3", company="字节跳动")) is True
    assert await crawler_service.save_job(db_session, _company_job("boss_k4", company="  字节跳动 ")) is True
    await db_session.commit()

    companies = (await db_session.execute(select(Company))).scalars().all()
    assert [c.name for c in companies] == ["字节跳动"]


def test_公司名归一化规则():
    normalize = crawler_service.normalize_company_name

    assert normalize("  字节跳动 ") == "字节跳动"
    assert normalize("字节\u3000跳动") == "字节 跳动"   # 全角空格 → 半角
    assert normalize("A   B") == "A B"                  # 连续空白压缩
    assert normalize(None) == ""
    assert normalize("   ") == ""
    # 与 Company.name 的 String(200) 对齐，避免入库时才被数据库截断
    assert len(normalize("x" * 500)) == 200


@pytest.mark.asyncio
async def test_空公司名不建记录且company_id为空(db_session):
    assert await crawler_service.save_job(db_session, _company_job("boss_k5", company="")) is True
    await db_session.commit()

    companies = (await db_session.execute(select(Company))).scalars().all()
    assert companies == [], "没有公司名就不该凭空造一条空公司"
    job, company = await _company_of(db_session, "boss_k5")
    assert job.company_id is None and company is None


@pytest.mark.asyncio
async def test_同名多条时按城市优先且打warning(db_session, caplog):
    """库里已有多条同名（历史数据/并发写入）时，不能悄悄用第一条。

    规则：优先城市匹配；否则最小 id；无论哪种都要 warning 列出全部候选 id ——
    让「数据分裂」在日志里可见，而不是被静默吞掉。
    """
    db_session.add_all([
        Company(name="云图科技", city="北京"),
        Company(name="云图科技", city="上海"),
    ])
    await db_session.commit()

    with caplog.at_level(logging.WARNING):
        cid = await crawler_service.resolve_company_id(db_session, "云图科技", "上海")

    shanghai = (
        await db_session.execute(
            select(Company).where(Company.name == "云图科技", Company.city == "上海")
        )
    ).scalar_one()
    assert cid == shanghai.id
    assert any("重复记录" in r.getMessage() for r in caplog.records), "重名必须有日志，不能静默"


@pytest.mark.asyncio
async def test_同名多条且城市都不匹配时取最小id(db_session, caplog):
    first = Company(name="云图科技", city="北京")
    db_session.add_all([first, Company(name="云图科技", city="广州")])
    await db_session.commit()

    with caplog.at_level(logging.WARNING):
        cid = await crawler_service.resolve_company_id(db_session, "云图科技", "长沙")

    assert cid == first.id
    assert any("最小 id" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_存量岗位重爬时自动补company_id(db_session):
    """已有岗位（company_id 为空）再次被采集到时顺手补上，不必等回填脚本。"""
    db_session.add(Job(
        title="老岗位", company_name="华为", source_site="boss",
        job_key="boss_老岗位_华为_深圳", city="深圳", job_status="ACTIVE",
    ))
    await db_session.commit()

    assert await crawler_service.save_job(
        db_session, _company_job("boss_老岗位_华为_深圳", company="华为", city="深圳")
    ) is False
    await db_session.commit()

    job, company = await _company_of(db_session, "boss_老岗位_华为_深圳")
    assert job.company_id is not None
    assert company.name == "华为"


# ==========================================================================
# C3｜`is_senior_job` 的年限判据：只看**区间下界**
#
# 原实现是 `"5" in experience and "年" in experience` —— 它问的是
# 「文本里有没有字符 5」，不是「门槛是不是 5 年」。于是：
#   「3-5年」「1-5年」「15年」全部被当资深岗丢弃，
#   而「5年以下」这种门槛很低的反被丢弃、「8年」这种真资深的反被保留。
# 现在改成解析下界：下界 ≥ 5 才算资深；`SENIOR_KEYWORDS`（职级词）那一层保留。
# ==========================================================================


@pytest.mark.parametrize(
    "experience",
    ["应届", "1年以内", "1-3年", "3-5年", "不限", "经验不限", ""],
)
def test_非资深经验要求一律保留(experience):
    """任务书点名的 7 类输入，都**不是**资深岗。"""
    assert is_senior_job("Java开发工程师", experience) is False


@pytest.mark.parametrize("experience", ["5-10年", "8年"])
def test_下界达五年才算资深(experience):
    assert is_senior_job("Java开发工程师", experience) is True


def test_区间只看下界而非上界():
    """旧实现的核心误杀：下界只有 1~4 年，却因为文本里有 5 而被丢弃。"""
    assert is_senior_job("后端开发", "1-5年") is False
    assert is_senior_job("后端开发", "4-9年") is False
    assert is_senior_job("后端开发", "5-10年") is True
    assert is_senior_job("后端开发", "10-15年") is True


def test_上界封顶与下界敞开():
    """「5年以下」门槛很低不能算资深；「5年以上」才是资深。"""
    assert is_senior_job("后端开发", "5年以下") is False
    assert is_senior_job("后端开发", "3年以内") is False
    assert is_senior_job("后端开发", "5年以上") is True
    assert is_senior_job("后端开发", "10年以上") is True


def test_职级词那一条仍然生效():
    """SENIOR_KEYWORDS 是对的，不能被这次改动弱化。"""
    for title in ["高级Java工程师", "资深开发", "系统架构师", "技术总监", "团队负责人", "研发主管"]:
        assert is_senior_job(title, "1-3年") is True, title


def test_空值与None不会崩():
    assert is_senior_job("", "") is False
    assert is_senior_job(None, None) is False
    assert is_senior_job(None, "3-5年") is False


def test_非年限文本不误判():
    """经验字段有时是「在校/实习」这类描述，解析不出年限 → 放行。"""
    assert is_senior_job("后端开发", "在校") is False
    assert is_senior_job("后端开发", "实习") is False
    assert is_senior_job("后端开发", "无经验要求") is False


# ==========================================================================
# C2｜来源隔离：`source_site` 是「这条数据是真的还是演示的」的唯一依据
#
# 原实现里 `source_site` 只是被原样抄进 `Job`，没有任何校验 —— 空值或拼错的
# 平台名会静默落库，之后谁也分不出这条数据来自哪里。这里锁三件事：
#   1. 空 / 未知来源**拒绝入库**并留 error 日志（不默认成任何平台）；
#   2. 浏览器批量上报时来源错误是**整批参数错误**，一条都不入；
#   3. 有一个只读统计能回答「这批有多少是真的」。
# ==========================================================================


def test_来源归一化不把空值洗成boss():
    """`normalize_platform("")` 回退 boss 是给 UI 的兜底，绝不能用在入库上。

    两者混用的后果：一条来源不明的数据被写成 `boss`，从此它和真实采集
    无法区分 —— 这正是本轮要修的「seed 与真实数据混在一起」的根因之一。
    """
    for bad in ["", "   ", None]:
        with pytest.raises(ValueError):
            crawler_service.normalize_source_site(bad)

    with pytest.raises(ValueError):
        crawler_service.normalize_source_site("weird_platform")

    # 合法值：已实现平台 / 已知平台 / 演示标记 / 别名
    assert crawler_service.normalize_source_site("seed") == "seed"
    assert crawler_service.normalize_source_site("boss") == "boss"
    assert crawler_service.normalize_source_site("BOSS直聘") == "boss"
    assert crawler_service.normalize_source_site("liepin") == "liepin"


@pytest.mark.asyncio
async def test_来源为空时拒绝入库并留error日志(db_session, caplog):
    data = _company_job("boss_无来源")
    data["source_site"] = ""

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError):
            await crawler_service.save_job(db_session, data)

    # 拒绝必须发生在写库之前：一条都不该落到库里
    assert (await db_session.execute(select(Job))).scalars().all() == []
    assert (await db_session.execute(select(Company))).scalars().all() == []
    assert any(r.levelno >= logging.ERROR and "source_site" in r.getMessage()
               for r in caplog.records), "拒绝入库必须有 error 日志，否则线上无从发现"


@pytest.mark.asyncio
async def test_未知来源时拒绝入库而不是默认成某个平台(db_session, caplog):
    data = _company_job("boss_未知来源")
    data["source_site"] = "weird"

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError) as exc:
            await crawler_service.save_job(db_session, data)

    assert "weird" in str(exc.value)          # 报错里要带上那个未知值，便于改正
    assert (await db_session.execute(select(Job))).scalars().all() == []
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_运行期加载的声明式数据源仍可入库(db_session, tmp_path):
    """白名单不能退化成「只有启动时那批平台」。

    声明式数据源允许运行期加载 yaml（`load_spec_file`），若白名单只认
    `registry.SUPPORTED_PLATFORMS` 这份导入时快照，新源会「抓取成功、入库被拒」。
    """
    from app.crawlers.spec_schema import CrawlSpec

    spec = CrawlSpec.from_dict({
        "site": "c2_runtime_source",
        "url": "https://example.com/list",
        "list": {"container": "div.job", "fields": {"name": "h3::text"}},
    })
    assert crawler_service.normalize_source_site(spec.site) == "c2_runtime_source"

    assert await crawler_service.save_job(
        db_session, _company_job("c2_runtime_source_k1", source_site=spec.site)
    ) is True


@pytest.mark.asyncio
async def test_浏览器上报来源为空时整批拒绝(db_session, caplog):
    """平台传错是**整批参数错误**，不是某条数据的属性 —— 逐条刷 N 条同样日志
    只会把真正的原因淹没。"""
    jobs = [
        {"title": "Python 工程师", "company_name": "某公司", "salary": "15-25K"},
        {"title": "Java 工程师", "company_name": "另一家", "salary": "20-30K"},
    ]

    with caplog.at_level(logging.ERROR):
        result = await crawler_service.ingest_browser_jobs(db_session, jobs, "", "长沙")

    assert result["received"] == 2
    assert result["saved"] == 0
    assert result["invalid"] == result["received"]
    assert (await db_session.execute(select(Job))).scalars().all() == []
    # 整批一条日志，而不是每条数据各刷一条
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert "整批被拒" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_浏览器上报正常来源仍能入库(db_session):
    """拒绝的是来源不明的数据，不是浏览器通道本身 —— 合法来源必须照常入库。"""
    jobs = [{"title": "Python 工程师", "company_name": "某公司", "salary": "15-25K"}]
    result = await crawler_service.ingest_browser_jobs(db_session, jobs, "boss", "长沙")

    assert (result["saved"], result["invalid"]) == (1, 0)
    job = (await db_session.execute(select(Job))).scalar_one()
    assert job.source_site == "boss"


@pytest.mark.asyncio
async def test_按来源统计岗位分布(db_session):
    """「这批数据里有多少是真的」必须是一次查询就能答出来的事。"""
    await crawler_service.save_job(db_session, _company_job("seed_1", source_site="seed"))
    await crawler_service.save_job(db_session, _company_job("seed_2", source_site="seed"))
    await crawler_service.save_job(db_session, _company_job("boss_1", source_site="boss"))
    await db_session.commit()

    stats = await crawler_service.count_jobs_by_source(db_session)
    assert stats["by_source"] == {"seed": 2, "boss": 1}
    assert (stats["total"], stats["demo"], stats["real"], stats["unknown"]) == (3, 2, 1, 0)


@pytest.mark.asyncio
async def test_历史空来源数据被统计为unknown而不是real(db_session):
    """校验是这轮才加的，库里可能已有绕过校验的历史行（来源为空）。

    把它们算进「真实」等于又一次虚报数据量 —— 必须单独计为 unknown，
    让「有数据绕过校验」这件事在统计里可见。
    """
    db_session.add(Job(
        title="历史脏数据", company_name="某公司", source_site="",
        job_key="legacy_1", city="长沙", job_status="NEW",
    ))
    await db_session.commit()

    stats = await crawler_service.count_jobs_by_source(db_session)
    assert (stats["total"], stats["demo"], stats["real"], stats["unknown"]) == (1, 0, 0, 1)


# ==========================================================================
# C4｜风控重定向不能被当成「这页没岗位」
#
# 2026-11-03 实测（scripts/check_boss_login.py，无登录态）：
#   最终 URL 变成 https://www.zhipin.com/web/user/?from=passport-zp&...&_security_check=6_...
#   页面标题变成「Loading https://...&_security_check=6_...」
#   正文只有 9.5KB 空壳，里面**没有**任何风控关键词
#   等 15 秒仍不出现任何岗位卡片容器
#
# 只扫正文的 detect_blocked 判成「没被风控」→ 那一页被当成「没有岗位」→
# 任务显示 COMPLETED / 0 条，看起来像「没数据」，实际是被拦了。
# ==========================================================================


def test_风控重定向能被识别而不是当成空页面():
    from app.crawlers.base import BaseCrawler

    # 2026-11-03 实测拿到的真实 URL 与标题
    blocked_url = (
        "https://www.zhipin.com/web/user/?from=passport-zp"
        "&fromUrl=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjobs"
        "&_security_check=6_1793697911613"
    )
    blocked_title = "Loading https://www.zhipin.com/web/geek/jobs?query=x&_security_check=6_1793697911613"
    shell_html = "<!DOCTYPE html><html><head><title>BOSS直聘</title></head><body></body></html>"

    assert BaseCrawler.detect_blocked(shell_html, 200, blocked_url, blocked_title) is True, (
        "被重定向到登录/安全校验页必须判定为风控，不能当成空页面"
    )

    # 正常搜索页不能被误判
    normal_url = "https://www.zhipin.com/web/geek/job?query=Python&city=101250100&page=1"
    assert BaseCrawler.detect_blocked("<div class='job-card'>正常岗位</div>", 200, normal_url, "BOSS直聘") is False


@pytest.mark.asyncio
async def test_没有登录态时任务说明要写明被拦的原因(db_session, monkeypatch):
    """0 条必须能区分「没数据」与「被拦了」—— 否则排查无从下手。"""

    class _BlockedCrawler:
        source_site = "boss"
        blocked_reason = (
            "页面被重定向到登录/安全校验页（https://www.zhipin.com/web/user/?_security_check=6_x）"
            "—— 无登录态或已被风控拦截，本次未取到任何岗位"
        )

        async def crawl_with_retry(self, keyword, city):
            return []

    monkeypatch.setattr(crawler_service, "get_crawler", lambda platform: _BlockedCrawler())
    monkeypatch.setattr(crawler_service, "probe_url", lambda platform: "")

    saved, keys, notes = await crawler_service._crawl_platform(
        db_session, "Python", "长沙", "boss"
    )

    assert (saved, keys) == (0, [])
    assert any("未取到岗位" in n and "登录" in n for n in notes), (
        f"任务说明必须写明被拦的原因，实际：{notes}"
    )


# ==========================================================================
# C5｜SQLite 写锁：并发写不能因为 `database is locked` 变成 500
#
# 实测（scripts/stress_sqlite_writes.py，10 并发 × 20 轮）：
#   修复前：锁冲突失败 49~93 / 200（每次跑都复现）
#     失败操作耗时 min 6114ms / 中位 6315ms → **等满了 busy_timeout 才放弃**，
#     成功操作耗时中位 3634ms → 几乎每个写都在排队
#   修复后：0 / 200
#
# 机制：SQLite 的写锁是排队的（同一时刻只有一个写事务），而 `log_action`
# 用独立会话写 `sys_log`，业务写事务同时占着锁 → 后面排队的写事务把
# 5 秒等待预算耗光 → 抛 database is locked → 接口 500。
#
# 修复在**引擎级**（app/database.py）：SQLite 才调大 busy_timeout 并开 WAL，
# MySQL / PostgreSQL 行为一字不变。
# ==========================================================================


def test_sqlite连接参数只影响sqlite方言():
    """MySQL / PG 的连接参数必须原样不变 —— 这次改动只对 SQLite 生效。"""
    from app.database import sqlite_connect_args

    assert sqlite_connect_args("mysql+aiomysql://u:p@localhost:3306/db") == {}
    assert sqlite_connect_args("postgresql+asyncpg://u:p@localhost:5432/db") == {}

    sqlite_args = sqlite_connect_args("sqlite+aiosqlite:///./recsys.db")
    assert sqlite_args["connect_args"]["timeout"] == 30, "等锁预算必须是 30 秒"


def test_连接池参数契约不被破坏():
    """`engine_kwargs_for` 的契约是「SQLite 不带连接池参数」，有测试逐字断言。

    所以等锁参数必须走另一个出口，不能混进去 —— 这条断言锁住这个分工。
    """
    from app.database import engine_kwargs_for

    assert engine_kwargs_for("sqlite+aiosqlite:///:memory:") == {}
    assert engine_kwargs_for("mysql+aiomysql://u:p@localhost:3306/db") == {
        "pool_size": 20,
        "max_overflow": 5,
    }


@pytest.mark.asyncio
async def test_并发写不会因为写锁冲突失败(tmp_path):
    """**用生产真正在用的那份配置**建库（同一组函数），然后并发写。

    为什么必须用文件库而不是测试默认的内存库：内存库是 StaticPool
    单连接，根本不会产生写锁争用 —— 拿它测「写锁」等于什么都没测。
    """
    import asyncio

    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from app.database import sqlite_connect_args, tune_sqlite_connection

    url = f"sqlite+aiosqlite:///{(tmp_path / 'concurrency.db').as_posix()}"
    eng = create_async_engine(url, **sqlite_connect_args(url))
    event.listen(eng.sync_engine, "connect", tune_sqlite_connection)

    from app.database import Base
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 确定性守卫：文件库**不得**是 WAL。
    # 只靠下面「errors == []」是概率性判据 —— 2026-11-04 实测 WAL 下 8 线程并发写
    # 有 5/5~9/10 的概率抛 sqlite3.OperationalError: attempt to write a readonly
    # database（不是锁等待，busy_timeout 救不了），偶尔一次通过会让假判据过关。
    async with eng.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
    assert str(mode).lower() != "wal", (
        f"文件库启用了 WAL（journal_mode={mode}）：本机并发写下会随机 SQLITE_READONLY，"
        "换 DELETE 日志模式 + busy_timeout 即可（见 app/database.py 的隔离实测）"
    )

    maker = async_sessionmaker(eng, expire_on_commit=False)
    errors: list = []

    async def writer(index: int):
        try:
            async with maker() as db:
                db.add(Job(
                    title=f"并发岗位-{index}", company_name="并发公司",
                    source_site="boss", job_key=f"conc_{index}",
                    city="长沙", job_status="NEW",
                ))
                await db.flush()
                await asyncio.sleep(0.005)      # 持锁一小会儿，制造争用
                await db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    try:
        await asyncio.gather(*[writer(i) for i in range(8)])
    finally:
        await eng.dispose()

    assert errors == [], f"并发写不应出现锁冲突失败：{errors}"

