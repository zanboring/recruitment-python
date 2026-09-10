"""AI 会话隔离测试。

验证核心修复：会话历史与取消标记都按 user_id 隔离，
不同用户即使传同一个 session_id 也读不到对方的内容。
"""
import pytest

from app.services import ai_service


def history_of(session_id: str, user_id=None) -> list:
    return ai_service.conversation_history.get(ai_service._session_key(session_id, user_id), [])


class TestSessionKey:
    def test_携带用户ID时带上用户前缀(self):
        assert ai_service._session_key("s1", 7) == "u7:s1"

    def test_未登录时退化为匿名前缀(self):
        assert ai_service._session_key("s1", None) == "uanonymous:s1"

    def test_空session回落到默认值(self):
        assert ai_service._session_key("", 7) == "u7:default"

    def test_同一session不同用户得到不同键(self):
        assert ai_service._session_key("s1", 1) != ai_service._session_key("s1", 2)


@pytest.mark.asyncio
class TestHistoryIsolation:
    async def test_同一用户同会话可累积上下文(self):
        await ai_service.update_conversation_history("s1", "你好", "你好，有什么可以帮你？", 1)
        await ai_service.update_conversation_history("s1", "再问一句", "收到", 1)

        records = history_of("s1", 1)
        assert len(records) == 4
        assert records[0] == {"role": "user", "content": "你好"}
        assert records[-1]["role"] == "assistant"

    async def test_不同用户同会话互不可见(self):
        await ai_service.update_conversation_history("same-id", "A 的秘密", "A 的回答", 1)
        await ai_service.update_conversation_history("same-id", "B 的秘密", "B 的回答", 2)

        records_a = history_of("same-id", 1)
        records_b = history_of("same-id", 2)
        assert "A 的秘密" not in [r["content"] for r in records_b]
        assert "B 的秘密" not in [r["content"] for r in records_a]

    async def test_未登录调用不会混入已登录用户的会话(self):
        await ai_service.update_conversation_history("s1", "用户1的话", "回复", 1)
        await ai_service.update_conversation_history("s1", "匿名的话", "回复")

        assert "匿名的话" not in [r["content"] for r in history_of("s1", 1)]
        assert "用户1的话" not in [r["content"] for r in history_of("s1", None)]

    async def test_历史长度被截断为最近20条(self):
        for i in range(30):
            await ai_service.update_conversation_history("long", f"q{i}", f"a{i}", 1)

        records = history_of("long", 1)
        assert len(records) == ai_service.MAX_HISTORY_TURNS
        assert records[-1]["content"] == "a29"
        assert records[0]["content"] == "q20"

    async def test_读取到的历史要能带进下一次请求上下文(self):
        await ai_service.update_conversation_history("ctx", "我叫张博仁", "记住了", 9)
        history = await ai_service._get_conversation_history("ctx", 9)
        assert history[0]["content"] == "我叫张博仁"

        other = await ai_service._get_conversation_history("ctx", 10)
        assert other == []


@pytest.mark.asyncio
class TestCancelIsolation:
    async def test_取消标记按用户隔离(self):
        ai_service.request_cancel("s1", 1)
        assert ai_service.is_cancelled("s1", 1) is True
        assert ai_service.is_cancelled("s1", 2) is False

    async def test_清除取消标记不影响其他用户(self):
        ai_service.request_cancel("s1", 1)
        ai_service.request_cancel("s1", 2)
        ai_service.clear_cancel("s1", 1)
        assert ai_service.is_cancelled("s1", 1) is False
        assert ai_service.is_cancelled("s1", 2) is True


@pytest.mark.asyncio
class TestSessionLifecycle:
    async def test_清空会话只影响指定用户(self):
        await ai_service.update_conversation_history("s1", "q", "a", 1)
        await ai_service.update_conversation_history("s1", "q", "a", 2)
        ai_service.clear_session("s1", 1)

        assert history_of("s1", 1) == []
        assert len(history_of("s1", 2)) == 2

    async def test_会话超时后被丢弃(self, monkeypatch):
        await ai_service.update_conversation_history("ttl", "q", "a", 1)
        assert len(history_of("ttl", 1)) == 2

        # 把最后访问时间往前拨到超过 TTL
        ai_service._session_touched_at[ai_service._session_key("ttl", 1)] -= (
            ai_service.SESSION_TTL_SECONDS + 1
        )
        assert await ai_service._get_conversation_history("ttl", 1) == []

    async def test_超出容量上限时淘汰最久未使用的会话(self, monkeypatch):
        monkeypatch.setattr(ai_service, "MAX_SESSIONS", 3)
        for uid in range(1, 5):
            await ai_service.update_conversation_history("overflow", "q", "a", uid)

        assert len(ai_service.conversation_history) <= 3
        assert history_of("overflow", 1) == []
        assert history_of("overflow", 4) != []

    async def test_reset_runtime_state清空全部状态(self):
        await ai_service.update_conversation_history("s1", "q", "a", 1)
        ai_service.request_cancel("s1", 1)
        ai_service.reset_runtime_state()
        assert ai_service.conversation_history == {}
        assert ai_service._cancelled_sessions == set()
