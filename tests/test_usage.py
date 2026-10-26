"""AI 用量与成本统计测试。

锁定四件事：
1. 费用折算按服务商单价，且**落库时算好**（单价可能变，历史应按当时价计价）
2. ``record`` 在任何异常下都不抛出 —— 统计失败绝不能影响业务
3. ``summary`` 的聚合维度正确（场景 / 模型 / 降级层级 / 日期）
4. 降级事件即使不消耗 token 也要被记录，否则「降级有没有发生」查不出来
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.ai_usage import AiUsage
from app.services import usage_service
from app.utils.timeutil import utc_now


# ---------- 费用折算 ----------

def test_费用按服务商单价折算():
    """DeepSeek 输入 100 万 token、输出 0，应等于其输入单价。"""
    settings.usage_price_deepseek_input = 2.0
    settings.usage_price_deepseek_output = 8.0

    assert usage_service.estimate_cost("deepseek", 1_000_000, 0) == pytest.approx(2.0)
    assert usage_service.estimate_cost("deepseek", 0, 1_000_000) == pytest.approx(8.0)


def test_本地模型费用为零():
    """本地推理无 API 费用，只用于统计「省下多少」。"""
    assert usage_service.estimate_cost("ollama", 1_000_000, 1_000_000) == 0.0
    assert usage_service.estimate_cost("local_fallback", 999, 999) == 0.0


def test_未知服务商按本地单价处理():
    """宁可低估也不要凭空造出一个价格。"""
    settings.usage_price_local = 0.0
    assert usage_service.estimate_cost("some-new-vendor", 1_000_000, 0) == 0.0


# ---------- 写入 ----------

@pytest.mark.asyncio
async def test_记录一次调用(usage_session_factory, db_session):
    await usage_service.record(
        scene=usage_service.SCENE_CHAT,
        provider="deepseek", model="deepseek-flash", tier="cloud",
        prompt_tokens=120, completion_tokens=80, latency_ms=640, user_id=7,
    )

    row = (await db_session.execute(select(AiUsage))).scalars().one()
    assert row.scene == "chat"
    assert row.provider == "deepseek"
    assert row.prompt_tokens == 120
    assert row.completion_tokens == 80
    # total 落库时算好，避免查询端重复计算
    assert row.total_tokens == 200
    assert row.latency_ms == 640
    assert row.success == 1
    assert row.user_id == 7


@pytest.mark.asyncio
async def test_失败调用也记录并留存错误信息(usage_session_factory, db_session):
    await usage_service.record(
        scene=usage_service.SCENE_CHAT,
        provider="deepseek", model="deepseek-flash", tier="cloud",
        success=False, error_msg="HTTPStatusError: 401",
    )

    row = (await db_session.execute(select(AiUsage))).scalars().one()
    assert row.success == 0
    assert "401" in row.error_msg


@pytest.mark.asyncio
async def test_降级事件即使零token也要记录(usage_session_factory, db_session):
    """规则引擎不消耗 token，但这条记录本身就是「降级发生了」的证据。"""
    await usage_service.record(
        scene=usage_service.SCENE_CHAT,
        provider="local_fallback", model="规则引擎", tier="fallback",
    )

    row = (await db_session.execute(select(AiUsage))).scalars().one()
    assert row.tier == "fallback"
    assert row.total_tokens == 0
    assert row.success == 1


@pytest.mark.asyncio
async def test_写入失败不抛异常(monkeypatch, usage_session_factory):
    """统计写入失败绝不能把用户的对话一起弄挂。"""
    class _Boom:
        def __call__(self):
            raise RuntimeError("数据库连不上")

    usage_service.set_session_factory(_Boom())
    # 不抛异常即通过
    await usage_service.record(scene=usage_service.SCENE_CHAT, provider="deepseek")


@pytest.mark.asyncio
async def test_关闭开关后不写入(usage_session_factory, db_session):
    settings.ai_usage_log_enabled = False
    await usage_service.record(scene=usage_service.SCENE_CHAT, provider="deepseek")

    rows = (await db_session.execute(select(AiUsage))).scalars().all()
    assert rows == []


# ---------- 聚合 ----------

@pytest.mark.asyncio
async def test_汇总按场景模型与降级层级聚合(usage_session_factory, db_session):
    await usage_service.record(
        scene=usage_service.SCENE_CHAT, provider="deepseek", model="deepseek-flash",
        tier="cloud", prompt_tokens=100, completion_tokens=50, latency_ms=500,
    )
    await usage_service.record(
        scene=usage_service.SCENE_TOOL_DETECT, provider="deepseek", model="deepseek-flash",
        tier="cloud", prompt_tokens=60, completion_tokens=10, latency_ms=300,
    )
    await usage_service.record(
        scene=usage_service.SCENE_CHAT, provider="ollama", model="qwen2.5:14b",
        tier="local", prompt_tokens=200, completion_tokens=100, latency_ms=900,
    )

    data = await usage_service.summary(db_session, days=7)

    assert data["totals"]["calls"] == 3
    assert data["totals"]["prompt_tokens"] == 360
    assert data["totals"]["completion_tokens"] == 160
    assert data["totals"]["total_tokens"] == 520
    # 三次全部成功
    assert data["totals"]["success_rate"] == 1.0

    scenes = {row["key"]: row for row in data["by_scene"]}
    assert scenes["chat"]["calls"] == 2
    assert scenes["tool_detect"]["calls"] == 1

    tiers = {row["key"]: row for row in data["by_tier"]}
    assert tiers["cloud"]["calls"] == 2
    assert tiers["local"]["calls"] == 1
    # 本地模型不计费
    assert tiers["local"]["cost"] == 0.0

    models = {row["key"]: row for row in data["by_model"]}
    assert models["qwen2.5:14b"]["total_tokens"] == 300


@pytest.mark.asyncio
async def test_统计本地模型省下的费用(usage_session_factory, db_session):
    settings.ai_provider = "deepseek"
    settings.usage_price_deepseek_input = 2.0
    settings.usage_price_deepseek_output = 8.0

    await usage_service.record(
        scene=usage_service.SCENE_CHAT, provider="ollama", model="qwen2.5:14b",
        tier="local", prompt_tokens=1_000_000, completion_tokens=0,
    )

    data = await usage_service.summary(db_session, days=7)
    assert data["totals"]["local_tokens"] == 1_000_000
    # (2.0 + 8.0) / 2 * 1M / 1M = 5.0 元
    assert data["totals"]["saved_cost_estimate"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_按天聚合(usage_session_factory, db_session):
    await usage_service.record(scene=usage_service.SCENE_CHAT, provider="deepseek",
                               prompt_tokens=10, completion_tokens=10)

    data = await usage_service.summary(db_session, days=7)
    assert len(data["by_day"]) == 1
    assert data["by_day"][0]["total_tokens"] == 20


@pytest.mark.asyncio
async def test_超出时间窗口的记录不计入(usage_session_factory, db_session):
    """时间基准统一为 naive UTC，窗口过滤必须生效。"""
    old = AiUsage(scene="chat", provider="deepseek", prompt_tokens=999,
                  completion_tokens=0, total_tokens=999, tier="cloud",
                  created_at=utc_now() - timedelta(days=30))
    db_session.add(old)
    await db_session.commit()

    data = await usage_service.summary(db_session, days=7)
    assert data["totals"]["calls"] == 0
    assert data["totals"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_无数据时不报错且成功率为零(usage_session_factory, db_session):
    data = await usage_service.summary(db_session, days=7)
    assert data["totals"]["calls"] == 0
    assert data["totals"]["success_rate"] == 0.0
    assert data["by_scene"] == []
    assert data["by_day"] == []


@pytest.mark.asyncio
async def test_天数参数下限保护(usage_session_factory, db_session):
    """days=0 / 负数不应导致时间窗口退化成「不过滤」。"""
    data = await usage_service.summary(db_session, days=0)
    assert data["days"] == 1


# ---------- HTTP 接口 ----------

@pytest.mark.asyncio
async def test_用量接口匿名访问被拒(client, usage_session_factory):
    resp = await client.get("/api/model/usage")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_用量接口普通用户被拒(client, usage_session_factory):
    """费用与调用量属于运营数据，不应向普通用户暴露。"""
    from tests.helpers import auth_headers, register_and_login

    token, _user = await register_and_login(client, "normal_usage")
    resp = await client.get("/api/model/usage", headers=auth_headers(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_管理员可读取用量汇总(client, db_session, usage_session_factory):
    from tests.helpers import admin_token, auth_headers

    await usage_service.record(
        scene=usage_service.SCENE_CHAT, provider="deepseek", model="deepseek-flash",
        tier="cloud", prompt_tokens=100, completion_tokens=100,
    )

    token = await admin_token(client, db_session, "admin_usage")
    resp = await client.get("/api/model/usage?days=7", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["totals"]["calls"] == 1
    assert data["totals"]["total_tokens"] == 200
    assert "by_scene" in data and "by_tier" in data


@pytest.mark.asyncio
async def test_模型对话入口同样记录用量(client, db_session, usage_session_factory):
    """两条对话入口（/api/ai/chat-stream 与 /api/model/chat）口径必须一致。

    回归：用量记录最初只接进了 ai_service，/api/model/chat 这条入口的调用
    完全不入账，导致「每次调用都被记录」这一说法并不成立。
    测试环境下无云端密钥且 Ollama 关闭，因此会落到规则引擎 —— 这正好也验证了
    「零 token 的降级事件同样要留痕」。
    """
    from tests.helpers import admin_token, auth_headers

    token = await admin_token(client, db_session, "admin_modelchat")
    resp = await client.post(
        "/api/model/chat", json={"message": "你好", "use_local_model": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["usedModel"] == "local_fallback"

    rows = (await db_session.execute(select(AiUsage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tier == "fallback"
    assert rows[0].scene == "chat"
