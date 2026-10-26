"""分析报告接口集成测试：/api/jobs/analysis/report

覆盖以下场景：
1. 需要登录（无 token → 401）。
2. LLM 全部不可用（关闭云端+本地）→ 降级为 rule_engine，llmEnhanced=False。
3. 注入 mock 云端供应商 → 返回 llmEnhanced=True + usedModel=primary。
4. 云端全抛错、本地也关闭 → 仍落到 rule_engine，不抛 500。
5. 云端全部返回空字符串 → 也视为不可用，落 rule_engine。
6. stats 字段透传：报告里能拿到 JobService 聚合出的字段。
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.services import llm_client
from tests.helpers import (
    create_job,
    admin_token,
    auth_headers,
    register_and_login,
    SAMPLE_JOB,
)


def _fake_provider() -> llm_client.CloudProvider:
    """构造一个假冒的云端供应商，供 mock 出来的 get_cloud_providers 返回。"""
    return llm_client.CloudProvider(
        name="MockGLM",
        provider="mock_glm",
        api_url="http://mock/api",
        api_key="mock-key",
        model="mock-model",
        temperature=0.7,
    )


def _fake_providers_with_one():
    return [_fake_provider()]


def _fake_providers_empty():
    return []


async def _async_iter(*chunks):
    for c in chunks:
        yield c


@asynccontextmanager
def _patch_call_cloud_stream(stream_chunks):
    """把 ai_service.call_cloud_stream 替换成 yield 固定分片。

    stream_chunks: 一个 (provider, prompt, ...) → AsyncGenerator[str] 的可调用对象，
    实际多数测试里 provider.ident 决定要分片内容。
    """
    async def _gen(provider, prompt, session_id, db, user_id=None, on_usage=None, **kwargs):
        for c in stream_chunks:
            yield c

    with patch("app.services.ai_service.call_cloud_stream", new=_gen):
        yield


# -------- 1. 鉴权 --------

@pytest.mark.asyncio
async def test_未登录拒绝访问报告(client):
    resp = await client.get("/api/jobs/analysis/report")
    assert resp.status_code == 401


# -------- 2. LLM 全不可用时的降级 --------

_PATCH_CLOUD = "app.services.llm_client.get_cloud_providers"
_PATCH_CALL = "app.services.ai_service.call_cloud_stream"


@pytest.mark.asyncio
async def test_LLM全部不可用时降级到rule_engine(client, db_session):
    """未配置云端密钥且 Ollama 关闭时，必须降级到 rule_engine 而非 500。"""
    token, _ = await register_and_login(client, "analyst01")

    with patch(_PATCH_CLOUD, new=_fake_providers_empty):
        resp = await client.get(
            "/api/jobs/analysis/report", headers=auth_headers(token)
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["llmEnhanced"] is False
    assert payload["usedModel"] == "rule_engine"
    # 必须包含统计落点（哪怕是空库也要返回合理文本）
    assert "岗位" in payload["report"]
    assert "stats" in payload
    assert "summary" in payload["stats"]


@pytest.mark.asyncio
async def test_有数据时统计落点包含总数(client, db_session):
    """跑一次后，report 应能反映数据库内的岗位数量。

    只看规则引擎路径，避免依赖 mock。
    """
    admin = await admin_token(client, db_session)
    for i in range(3):
        await create_job(client, admin, title=f"Java 后端 {i}")

    token, _ = await register_and_login(client, "analyst02")

    with patch(_PATCH_CLOUD, new=_fake_providers_empty):
        resp = await client.get(
            "/api/jobs/analysis/report", headers=auth_headers(token)
        )

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["llmEnhanced"] is False
    assert payload["stats"]["summary"]["total"] == 3


# -------- 3. 命中 mock 云端供应商：返回 llmEnhanced=True --------

@pytest.mark.asyncio
async def test_mock云端返回正文报告命中增强路径(client, db_session):
    """mock 一个能产出正文的云端服务 → 接口返回 llmEnhanced=True + usedModel=primary。"""
    token, _ = await register_and_login(client, "analyst03")
    admin = await admin_token(client, db_session)
    await create_job(client, admin)

    with patch(_PATCH_CLOUD, new=_fake_providers_with_one), \
         patch(_PATCH_CALL,
               new=lambda *a, **kw: _async_iter("招聘趋势，", "Java 岗位最多")):
        resp = await client.get(
            "/api/jobs/analysis/report", headers=auth_headers(token)
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["llmEnhanced"] is True
    assert payload["usedModel"] == "primary"
    assert "招聘趋势" in payload["report"]
    assert "Java 岗位最多" in payload["report"]


# -------- 4. 云端抛错 + 本地关闭 → 仍落 rule_engine（不抛 500） --------

@pytest.mark.asyncio
async def test_云端异常且本地不可用时降级到rule_engine(client, db_session):
    token, _ = await register_and_login(client, "analyst04")
    admin = await admin_token(client, db_session)
    await create_job(client, admin)

    async def boom(*a, **kw):
        raise RuntimeError("云端炸了")
        yield  # 变成生成器

    with patch(_PATCH_CLOUD, new=_fake_providers_with_one), \
         patch(_PATCH_CALL, new=boom), \
         patch("app.config.settings.ollama_enabled", False):
        resp = await client.get(
            "/api/jobs/analysis/report", headers=auth_headers(token)
        )

    # 必须不抛 500；降级到 rule_engine
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["llmEnhanced"] is False
    assert payload["usedModel"] == "rule_engine"


# -------- 5. 云端返回空字符串 → 视为不可用 --------

@pytest.mark.asyncio
async def test_云端仅返回空字符串视为不可用(client, db_session):
    """云端生成空串时，service 层视为「无内容」继续降级；不应误报 llmEnhanced=True。"""
    token, _ = await register_and_login(client, "analyst05")
    admin = await admin_token(client, db_session)
    await create_job(client, admin)

    with patch(_PATCH_CLOUD, new=_fake_providers_with_one), \
         patch(_PATCH_CALL,
               new=lambda *a, **kw: _async_iter(" ", "  \n ")):
        resp = await client.get(
            "/api/jobs/analysis/report", headers=auth_headers(token)
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["llmEnhanced"] is False
    assert payload["usedModel"] == "rule_engine"


# -------- 6. stats 字段透传校验 --------

@pytest.mark.asyncio
async def test_报告里包含_JobService聚合出的关键字段(client, db_session):
    """stats 至少透出 summary（总数/在招/平均薪资），供前端 dashboard 独立展示。"""
    admin = await admin_token(client, db_session)
    payload_job = dict(SAMPLE_JOB)
    payload_job["min_salary"] = 10000
    payload_job["max_salary"] = 20000
    await create_job(client, admin, **payload_job)

    token, _ = await register_and_login(client, "analyst06")

    with patch(_PATCH_CLOUD, new=_fake_providers_empty):
        resp = await client.get(
            "/api/jobs/analysis/report", headers=auth_headers(token)
        )

    data = resp.json()["data"]
    summary = data["stats"]["summary"]
    assert summary["total"] == 1
    assert summary["active"] == 1
    # 10000 ~ 20000 → 平均 15000
    assert 14000 <= summary["avg_salary"] <= 16000
