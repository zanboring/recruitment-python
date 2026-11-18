"""知识库写接口回归测试：更新 / 切换状态 / 评分。

背景（2026-11-03 接口审计 T1 实测）：这三个接口在 commit 之后直接
`KnowledgeBaseResponse.model_validate(item)`，而 `updated_at` 是 onupdate 列，
提交后处于 expired 状态；异步会话下序列化会触发惰性加载并抛 MissingGreenlet，
接口稳定返回 500。修法是在响应前显式 `await db.refresh(item)`。
"""
import pytest
from tests.helpers import admin_token, auth_headers


async def _create_knowledge(client, token, question="审计用知识条目"):
    resp = await client.post(
        "/api/knowledge",
        json={"question": question, "answer": "审计用答案", "tags": "audit"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_更新知识条目返回可序列化的_updated_at(client, db_session):
    token = await admin_token(client, db_session)
    created = await _create_knowledge(client, token)

    resp = await client.put(
        f"/api/knowledge/{created['id']}",
        json={"question": "审计用知识条目-已更新"},
        headers=auth_headers(token),
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["question"] == "审计用知识条目-已更新"
    assert data["updated_at"], "updated_at 必须能在响应里序列化（MissingGreenlet 回归）"


@pytest.mark.asyncio
async def test_切换知识条目状态(client, db_session):
    token = await admin_token(client, db_session)
    created = await _create_knowledge(client, token)

    resp = await client.patch(f"/api/knowledge/{created['id']}/toggle", headers=auth_headers(token))

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] != created["status"]
    assert data["updated_at"]


@pytest.mark.asyncio
async def test_知识条目评分(client, db_session):
    token = await admin_token(client, db_session)
    created = await _create_knowledge(client, token)

    resp = await client.patch(
        f"/api/knowledge/{created['id']}/score",
        params={"score": 8},
        headers=auth_headers(token),
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["quality_score"] == 8
    assert data["updated_at"]


@pytest.mark.asyncio
async def test_知识库统计返回真实总使用次数(client, db_session):
    """回归：前端读 stats.totalUsage，后端此前只返回 total/enabled/manual/auto。

    usage_count 是真在被维护的字段（RAG 命中时 +1），所以补的是真指标，
    不是把卡片删掉。同时验证 camelCase 中间件确实生成了 totalUsage 别名 ——
    只验 snake_case 会漏掉"前端依旧拿到 undefined"这半个缺陷。
    """
    from app.models.knowledge_base import KnowledgeBase

    first = await _create_knowledge(client, await admin_token(client, db_session), "统计用知识A")
    second = await _create_knowledge(client, await admin_token(client, db_session), "统计用知识B")

    for knowledge_id, count in ((first["id"], 2), (second["id"], 5)):
        item = await db_session.get(KnowledgeBase, knowledge_id)
        item.usage_count = count
    await db_session.commit()

    token = await admin_token(client, db_session)
    resp = await client.get("/api/knowledge/stats", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["total_usage"] == 7
    assert data["totalUsage"] == 7, "camelCase 别名没生成，前端卡片仍是空的"
    assert data["total"] >= 2
