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
    """前端可选 zhaopin/51job/liepin，但后端只实现了 boss。"""
    task = await crawler_service.create_pending_task(
        db_session, "Java", "长沙", ["zhaopin", "51job"]
    )
    count = await crawler_service.run_crawl(db_session, task, "Java", "长沙", ["zhaopin", "51job"])

    assert count == 0
    assert task.status == "FAILED"
    assert "zhaopin" in task.message and "51job" in task.message
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
    from tests.helpers import admin_token, auth_headers

    token = await admin_token(client, db_session, "admin_crawlopt")
    resp = await client.get("/api/crawler/options", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["platforms"] == sorted(SUPPORTED_PLATFORMS)
    assert "长沙" in data["cities"]
    # 前端硬编码但后端未实现的平台不应出现在可选列表里
    assert "zhaopin" not in data["platforms"]


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
