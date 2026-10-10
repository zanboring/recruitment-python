"""P1-1：AI 会话历史外置 Redis（多副本下不丢、不串台）。

这些用例**不依赖真实 Redis 进程**（任务书 §3 约束 2）：用一个最小异步替身覆盖
Redis 路径，替身实现了会话历史用到的 lrange / rpush / ltrim / expire / delete。

对应验收 A9 —— 2 worker 下同一 session_id 连续多轮对话、历史不丢不串。
本文件以「同一份 Redis 存储被两次独立读取」模拟两个副本：只要历史落的是共享存储，
任意副本读到的就是同一份（进程内实现做不到这一点，见下）。
"""
import asyncio

import pytest

from app.config import settings
from app.services import ai_service


class FakeRedisList:
    """会话历史用到的 Redis 命令的最小异步替身（不含任何网络连接）。"""

    def __init__(self):
        self.lists: dict[str, list] = {}
        self.expires: dict[str, int] = {}
        self.fail = False

    def _guard(self):
        if self.fail:
            raise ConnectionError("替身模拟 Redis 故障")

    async def ping(self):
        self._guard()
        return True

    async def lrange(self, key, start, end):
        self._guard()
        items = list(self.lists.get(key, []))
        if start < 0:
            start = max(len(items) + start, 0)
        if end < 0:
            end = len(items) + end
        return items[start:end + 1]

    async def rpush(self, key, *values):
        self._guard()
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        self._guard()
        items = self.lists.get(key, [])
        if start < 0:
            start = max(len(items) + start, 0)
        if end < 0:
            end = len(items) + end
        self.lists[key] = items[start:end + 1]
        return "OK"

    async def expire(self, key, ttl):
        self._guard()
        self.expires[key] = ttl
        return True

    async def delete(self, key):
        self._guard()
        self.lists.pop(key, None)
        self.expires.pop(key, None)
        return 1


@pytest.fixture
def redis_session(monkeypatch):
    """把会话历史切到 Redis 后端，并注入替身（全程不连真实 Redis）。"""
    fake = FakeRedisList()
    monkeypatch.setattr(settings, "session_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "redis://stub:6379/0")
    ai_service.set_session_redis_client(fake)
    yield fake
    ai_service.set_session_redis_client(None)


class TestBackendSelection:
    def test_默认内存后端不启用Redis(self):
        """默认值必须是 memory —— 单机行为与改造前完全一致。"""
        assert settings.session_backend == "memory"
        assert ai_service._session_redis_enabled() is False

    @pytest.mark.asyncio
    async def test_未配置redis_url时退回内存后端(self, monkeypatch):
        """SESSION_BACKEND=redis 但未配 REDIS_URL 时，连惰性建连都不做。"""
        monkeypatch.setattr(settings, "session_backend", "redis")
        monkeypatch.setattr(settings, "redis_url", "")
        assert await ai_service._get_session_redis() is None

        await ai_service.update_conversation_history("s", "问题", "回答", 1)
        assert ai_service.conversation_history.get(ai_service._session_key("s", 1))


@pytest.mark.asyncio
class TestRedisHistory:
    async def test_历史落在共享存储_副本读到同一份(self, redis_session):
        await ai_service.update_conversation_history("s1", "你好", "你好呀", 1)

        # 第一次读取（副本 A）
        first = await ai_service._get_conversation_history("s1", 1)
        # 第二次读取（等价于副本 B：只认共享存储，不认进程内字典）
        second = await ai_service._get_conversation_history("s1", 1)

        expected = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]
        assert first == expected == second
        # 关键：确实写进了 Redis 存储，而不是进程内字典
        assert ai_service.conversation_history == {}
        assert "recsys:chat:u1:s1" in redis_session.lists

    async def test_不同用户同会话互不可见(self, redis_session):
        await ai_service.update_conversation_history("same", "A 的秘密", "A 的回答", 1)

        assert await ai_service._get_conversation_history("same", 2) == []
        assert "recsys:chat:u1:same" in redis_session.lists
        assert "recsys:chat:u2:same" not in redis_session.lists

    async def test_超出上限自动截断(self, redis_session):
        for i in range(30):
            await ai_service.update_conversation_history("long", f"q{i}", f"a{i}", 1)

        history = await ai_service._get_conversation_history("long", 1)
        assert len(history) == ai_service.MAX_HISTORY_TURNS
        assert history[-1] == {"role": "assistant", "content": "a29"}

    async def test_TTL由Redis原生管理(self, redis_session):
        """替代内存后端的手工淘汰：TTL 交给 Redis EXPIRE。"""
        await ai_service.update_conversation_history("s", "q", "a", 1)
        assert redis_session.expires["recsys:chat:u1:s"] == ai_service.SESSION_TTL_SECONDS

    async def test_清空会话会删除Redis键(self, redis_session):
        await ai_service.update_conversation_history("s", "q", "a", 1)
        assert "recsys:chat:u1:s" in redis_session.lists

        ai_service.clear_session("s", 1)
        # clear_session 是同步接口，删除以任务形式投递，让它执行完
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert "recsys:chat:u1:s" not in redis_session.lists
        assert await ai_service._get_conversation_history("s", 1) == []

    async def test_Redis故障时降级进程内且不丢会话(self, redis_session):
        """Redis 读写失败都退回进程内实现 —— 行为与改造前一致，对话不中断。"""
        redis_session.fail = True

        await ai_service.update_conversation_history("s", "问题", "回答", 1)
        assert ai_service.conversation_history.get(ai_service._session_key("s", 1))

        history = await ai_service._get_conversation_history("s", 1)
        assert history[-1] == {"role": "assistant", "content": "回答"}


class TestIsolationGuard:
    def test_替身机制不依赖真实Redis进程(self):
        """注入的替身即后端 —— 用例不会去连真实 Redis（否则 CI 跑不了）。"""
        fake = FakeRedisList()
        ai_service.set_session_redis_client(fake)
        try:
            assert ai_service._session_redis_enabled() is True
            assert ai_service._session_redis_client is fake
        finally:
            ai_service.set_session_redis_client(None)

    def test_reset_runtime_state清掉注入的替身(self):
        ai_service.set_session_redis_client(FakeRedisList())
        ai_service.reset_runtime_state()
        assert ai_service._session_redis_client is None