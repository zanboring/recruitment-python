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

import pytest

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
        return 3, [f"{platform}_k1"]

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
        return 2, [f"{platform}_k1"]

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
