# -*- coding: utf-8 -*-
"""Redis 限流后端测试（多副本共享计数）。

**为什么不必依赖真实 Redis 进程**：用一个实现相同语义的最小替身（``FakeRedis``）
注入 client，CI 无 Redis 也能全绿。

**这个替身带来的诚实局限**：它验证的是 Python 侧的调用参数、返回值解析、
降级契约与中间件接线；**Lua 脚本本身的正确性无法在这里被证明**
（替身是 Python 版的同一算法）。Lua 的正确性需要在有真实 Redis 的环境
按任务书 A3 验收：起 2 个 worker 并发打登录接口，总放行数必须 ≤ 配置上限。

本文件的对照用例（内存版双实例放行翻倍 vs Redis 版只放行一份）
是「为什么多副本必须换 Redis」的可执行论证。
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.middleware.rate_limit import (
    _REDIS_KEY_PREFIX,
    _SLIDING_WINDOW_LUA,
    RateLimitMiddleware,
    RedisSlidingWindowLimiter,
    SlidingWindowLimiter,
)


class FakeRedis:
    """最小 Redis 替身：只实现限流 Lua 用到的 ZSET 语义 + eval。

    - ``eval`` 按 ``_SLIDING_WINDOW_LUA`` 的同一算法执行（滑动窗口）；
    - 记录每次调用参数，供断言 key 前缀 / member 唯一性；
    - ``fail=True`` 时 ``eval`` 抛异常，用于验证降级路径。
    """

    def __init__(self):
        self.zsets: dict = {}
        self.ttl: dict = {}
        self.calls: list = []
        self.fail = False

    async def ping(self):
        return True

    async def eval(self, script, numkeys, key, now_ms, window_ms, limit, member):
        self.calls.append({
            "key": key,
            "now_ms": now_ms,
            "window_ms": window_ms,
            "limit": limit,
            "member": member,
        })
        if self.fail:
            raise ConnectionError("fake redis down")

        z = self.zsets.setdefault(key, {})
        for m in [m for m, s in z.items() if s <= now_ms - window_ms]:
            z.pop(m, None)
        count = len(z)
        if count >= limit:
            oldest = min(z.values()) if z else now_ms
            self.ttl[key] = window_ms
            return [0, int(oldest), 0]
        z[member] = now_ms
        self.ttl[key] = window_ms + 1000
        return [1, 0, limit - count - 1]


# --------------------------------------------------------------------------
# Redis 限流器本体
# --------------------------------------------------------------------------


class TestRedisSlidingWindowLimiter:
    async def test_未达上限时放行且remaining递减(self):
        limiter = RedisSlidingWindowLimiter(client=FakeRedis())
        assert await limiter.check("u1", 3, 60) == (True, 0, 2)
        assert await limiter.check("u1", 3, 60) == (True, 0, 1)
        assert await limiter.check("u1", 3, 60) == (True, 0, 0)

    async def test_超过上限被拒绝且retry_after至少1秒(self):
        limiter = RedisSlidingWindowLimiter(client=FakeRedis())
        await limiter.check("u1", 2, 60)
        await limiter.check("u1", 2, 60)
        allowed, retry_after, remaining = await limiter.check("u1", 2, 60)
        assert allowed is False
        assert retry_after >= 1
        assert remaining == 0

    async def test_不同key互不影响(self):
        limiter = RedisSlidingWindowLimiter(client=FakeRedis())
        assert (await limiter.check("a", 1, 60))[0] is True
        assert (await limiter.check("a", 1, 60))[0] is False
        assert (await limiter.check("b", 1, 60))[0] is True

    async def test_窗口过期后配额恢复(self):
        """把已记录请求的时间戳前移，模拟窗口滑过 → 配额应恢复。"""
        fake = FakeRedis()
        limiter = RedisSlidingWindowLimiter(client=fake)
        await limiter.check("u1", 1, 60)
        assert (await limiter.check("u1", 1, 60))[0] is False

        for z in fake.zsets.values():
            for m in list(z):
                z[m] -= 61_000  # 前移 61 秒（窗口 60s）

        assert (await limiter.check("u1", 1, 60))[0] is True

    async def test_上限为0表示不限流且不触达Redis(self):
        fake = FakeRedis()
        limiter = RedisSlidingWindowLimiter(client=fake)
        assert await limiter.check("u1", 0, 60) == (True, 0, 0)
        assert fake.calls == []

    async def test_键带独立前缀避免与缓存键冲突(self):
        fake = FakeRedis()
        limiter = RedisSlidingWindowLimiter(client=fake)
        await limiter.check("login:ip:1.2.3.4", 5, 60)
        assert fake.calls[0]["key"] == f"{_REDIS_KEY_PREFIX}login:ip:1.2.3.4"

    async def test_member唯一_同毫秒并发不互相覆盖(self):
        """ZSET 同 member 是覆盖而非累加：member 重复会让计数偏小、限流失效。"""
        fake = FakeRedis()
        limiter = RedisSlidingWindowLimiter(client=fake)
        for _ in range(5):
            await limiter.check("u1", 10, 60)
        members = {c["member"] for c in fake.calls}
        assert len(members) == 5

    async def test_eval异常时返回None表示降级(self):
        fake = FakeRedis()
        fake.fail = True
        limiter = RedisSlidingWindowLimiter(client=fake)
        assert await limiter.check("u1", 5, 60) is None

    async def test_未配置redis_url时静默返回None(self, monkeypatch):
        monkeypatch.setattr(settings, "redis_url", "", raising=False)
        limiter = RedisSlidingWindowLimiter(redis_url="")
        assert await limiter.check("u1", 5, 60) is None

    async def test_连接失败进入冷却期不每请求重试(self, monkeypatch):
        """连接失败后进入冷却：冷却期内不再尝试建连，避免每请求白等一次超时。"""
        import redis.asyncio as aioredis

        attempts = {"n": 0}

        def fake_from_url(url, **kwargs):
            attempts["n"] += 1
            raise ConnectionError("connection refused")

        monkeypatch.setattr(aioredis, "from_url", fake_from_url)
        monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0", raising=False)

        limiter = RedisSlidingWindowLimiter(connect_timeout=0.1)
        assert await limiter.check("u1", 5, 60) is None  # 第 1 次：建连失败
        assert await limiter.check("u1", 5, 60) is None  # 第 2 次：冷却期内直接降级
        assert attempts["n"] == 1


# --------------------------------------------------------------------------
# Lua 脚本自检（防止误改成固定窗口 / 丢掉过期保护）
# --------------------------------------------------------------------------


class TestLuaScriptShape:
    def test_使用ZSET滑动窗口而非INCR固定窗口(self):
        assert "ZREMRANGEBYSCORE" in _SLIDING_WINDOW_LUA
        assert "ZCARD" in _SLIDING_WINDOW_LUA
        assert "ZADD" in _SLIDING_WINDOW_LUA
        assert "INCR" not in _SLIDING_WINDOW_LUA

    def test_设置过期时间防止key无限堆积(self):
        assert "PEXPIRE" in _SLIDING_WINDOW_LUA

    def test_返回结构含最早记录时间用于计算RetryAfter(self):
        assert "ZRANGE" in _SLIDING_WINDOW_LUA
        assert "WITHSCORES" in _SLIDING_WINDOW_LUA


# --------------------------------------------------------------------------
# 中间件接线与后端选择
# --------------------------------------------------------------------------


def _build_app(redis_limiter, memory_limiter=None) -> FastAPI:
    """构造只挂限流中间件的最小 app（限流规则命中 POST /api/auth/login）。"""
    app = FastAPI()

    @app.post("/api/auth/login")
    async def login():
        return {"ok": True}

    app.add_middleware(
        RateLimitMiddleware, limiter=memory_limiter, redis_limiter=redis_limiter
    )
    return app


async def _post_login(app) -> int:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/auth/login", json={"username": "u", "password": "p"})
    return resp.status_code


@pytest.fixture
def use_redis_backend(monkeypatch):
    """把限流后端切到 redis（monkeypatch 自动恢复，不影响其它用例）。"""
    monkeypatch.setattr(settings, "rate_limit_backend", "redis", raising=False)
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0", raising=False)
    yield


class TestMiddlewareBackendSelection:
    async def test_默认memory后端完全不触碰Redis(self, monkeypatch):
        """默认配置（memory）下 Redis 替身一次都不该被调用 —— 单机行为与改造前一致。"""
        monkeypatch.setattr(settings, "rate_limit_backend", "memory", raising=False)
        fake = FakeRedis()
        app = _build_app(RedisSlidingWindowLimiter(client=fake), SlidingWindowLimiter())
        assert await _post_login(app) == 200
        assert fake.calls == []

    async def test_redis后端生效时计数走Redis(self, use_redis_backend):
        fake = FakeRedis()
        app = _build_app(RedisSlidingWindowLimiter(client=fake), SlidingWindowLimiter())
        assert await _post_login(app) == 200
        assert len(fake.calls) >= 1
        assert fake.calls[0]["key"].startswith(_REDIS_KEY_PREFIX)

    async def test_redis后端跨副本共享计数_总放行等于上限(self, use_redis_backend):
        """**A3 核心判据的可执行版**：两个副本各自持独立内存 limiter，
        但共享同一个 Redis → 总放行数必须等于配置上限（内存版会是 2 倍）。
        """
        fake = FakeRedis()
        limit = settings.rate_limit_login_per_minute  # 默认 10

        # 模拟 2 个 worker：各自独立的内存计数（所以内存版会各放行一份）
        app_a = _build_app(RedisSlidingWindowLimiter(client=fake), SlidingWindowLimiter())
        app_b = _build_app(RedisSlidingWindowLimiter(client=fake), SlidingWindowLimiter())

        allowed = 0
        for i in range(limit * 2):
            app = app_a if i % 2 == 0 else app_b
            if await _post_login(app) != 429:
                allowed += 1

        assert allowed == limit, (
            f"Redis 共享计数下总放行应为 {limit}，实际 {allowed}；"
            f"若为 {limit * 2} 则说明计数未跨副本共享"
        )

    async def test_对照_内存后端双副本放行翻倍(self, monkeypatch):
        """对照组（量化论证）：不共享状态时两个副本各放行一份 → 2 × limit。

        这条用例存在的意义是证明上面那条 Redis 用例「不是恒真」——
        同一个测试骨架，只换后端，结论必须不同。
        """
        monkeypatch.setattr(settings, "rate_limit_backend", "memory", raising=False)
        limit = settings.rate_limit_login_per_minute

        app_a = _build_app(None, SlidingWindowLimiter())
        app_b = _build_app(None, SlidingWindowLimiter())

        allowed = 0
        for i in range(limit * 2):
            app = app_a if i % 2 == 0 else app_b
            if await _post_login(app) != 429:
                allowed += 1

        assert allowed == limit * 2

    async def test_redis故障时降级内存_请求不失败(self, use_redis_backend):
        """Redis 挂掉时：请求仍正常（不 5xx），由内存兜底限流。"""
        fake = FakeRedis()
        fake.fail = True
        app = _build_app(RedisSlidingWindowLimiter(client=fake), SlidingWindowLimiter())

        assert await _post_login(app) == 200

        # 内存兜底仍然限流：连打到超限，必须出现 429（而不是无限放行）
        limit = settings.rate_limit_login_per_minute
        codes = []
        for _ in range(limit + 2):
            codes.append(await _post_login(app))
        assert 429 in codes

    async def test_redis后端未配redis_url时走内存(self, monkeypatch):
        """backend=redis 但 redis_url 为空 → 直接内存（连惰性建连都不做）。"""
        monkeypatch.setattr(settings, "rate_limit_backend", "redis", raising=False)
        monkeypatch.setattr(settings, "redis_url", "", raising=False)
        fake = FakeRedis()
        app = _build_app(RedisSlidingWindowLimiter(client=fake), SlidingWindowLimiter())
        assert await _post_login(app) == 200
        assert fake.calls == []


class TestConfigDefaults:
    def test_限流后端默认memory保证单机行为不变(self):
        from app.config import Settings

        assert Settings.model_fields["rate_limit_backend"].default == "memory"
