"""AI 接口的鉴权与会话隔离集成测试。

覆盖 routes: /api/ai/*
说明：不真的调用 GLM-4 / Ollama，而是把底层流式函数替换成桩，
      专注验证「鉴权」「按用户隔离」「取消」这三层行为。
"""
import pytest

from app.routers import ai as ai_router
from tests.helpers import register_and_login, auth_headers


@pytest.fixture
def stub_stream(app, monkeypatch):
    """把三个流式入口替换成可控桩，并记录调用参数。"""
    recorded = []

    async def fake_chunks(message, session_id="", db=None, user_id=None):
        recorded.append({"message": message, "session_id": session_id, "user_id": user_id})
        for ch in ["你", "好"]:
            yield ch

    # ai.py 只暴露两个流式入口：/chat-local 走 Ollama，/chat-stream 走统一调度
    # （云端具体走 DeepSeek 还是智谱由 llm_client 按配置决定，不在路由层出现）
    monkeypatch.setattr(ai_router, "call_ollama_stream", fake_chunks)
    monkeypatch.setattr(ai_router, "call_chat_stream", fake_chunks)
    return recorded


@pytest.mark.asyncio
class TestAiAuth:
    async def test_未登录访问对话接口返回401(self, client):
        resp = await client.post("/api/ai/chat-stream", json={"message": "hi"})
        assert resp.status_code == 401
        assert resp.json()["message"] == "请先登录"

    async def test_未登录查看AI状态返回401(self, client):
        assert (await client.get("/api/ai/status")).status_code == 401

    async def test_未登录不能取消别人的会话(self, client):
        resp = await client.post("/api/ai/cancel", params={"session_id": "x"})
        assert resp.status_code == 401

    async def test_登录后可以调用对话接口(self, client, stub_stream):
        token, _ = await register_and_login(client, "ai01")
        resp = await client.post(
            "/api/ai/chat-stream", json={"message": "hi", "session_id": "s1"}, headers=auth_headers(token)
        )
        assert resp.status_code == 200
        assert "data: 你" in resp.text
        assert stub_stream[-1]["user_id"] is not None

    async def test_超长消息被拒绝(self, client, stub_stream):
        token, _ = await register_and_login(client, "ai02")
        resp = await client.post(
            "/api/ai/chat-stream", json={"message": "x" * 5000}, headers=auth_headers(token)
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestAiIsolation:
    async def test_调用时带上真实用户ID(self, client, stub_stream, db_session):
        from tests.helpers import admin_token
        token = await admin_token(client, db_session)
        admin_id = 1  # 第一个创建的用户，自增 ID 通常为 1

        await client.post(
            "/api/ai/chat-stream", json={"message": "hi", "session_id": "s1"}, headers=auth_headers(token)
        )
        assert stub_stream[-1]["user_id"] == admin_id

    async def test_两个用户同session_id历史互相隔离(self, client, stub_stream):
        token_a, _ = await register_and_login(client, "iso_a")
        token_b, _ = await register_and_login(client, "iso_b")

        for token, msg in ((token_a, "A 的提问"), (token_b, "B 的提问")):
            await client.post(
                "/api/ai/chat-stream",
                json={"message": msg, "session_id": "shared"},
                headers=auth_headers(token),
            )

        from app.services.ai_service import conversation_history
        keys = list(conversation_history.keys())
        # 同一路径 UB：同一个 session_id 被两个用户写过，应产生两个键
        assert any(k.startswith("u") and k.endswith(":shared") for k in keys)
        all_contents = [
            r["content"] for records in conversation_history.values() for r in records
        ]
        assert all_contents.count("A 的提问") == 1
        assert all_contents.count("B 的提问") == 1

    async def test_取消请求带上的是调用者自己的用户ID(self, client, db_session):
        from app.services import ai_service

        token_a, user_a = await register_and_login(client, "cancel_a")
        token_b, user_b = await register_and_login(client, "cancel_b")
        assert user_a["id"] != user_b["id"]

        resp = await client.post(
            "/api/ai/cancel", params={"session_id": "s9"}, headers=auth_headers(token_a)
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["cancelled"] is True

        assert ai_service.is_cancelled("s9", user_a["id"]) is True
        assert ai_service.is_cancelled("s9", user_b["id"]) is False

    async def test_清空会话接口只清自己的(self, client, db_session):
        from app.services import ai_service

        token_a, user_a = await register_and_login(client, "clr_a")
        other_id = user_a["id"] + 100
        await ai_service.update_conversation_history("s1", "q", "a", user_a["id"])
        await ai_service.update_conversation_history("s1", "q", "a", other_id)

        resp = await client.delete(
            "/api/ai/session", params={"session_id": "s1"}, headers=auth_headers(token_a)
        )
        assert resp.status_code == 200
        assert ai_service.conversation_history.get(ai_service._session_key("s1", user_a["id"])) is None
        assert len(ai_service.conversation_history[ai_service._session_key("s1", other_id)]) == 2
