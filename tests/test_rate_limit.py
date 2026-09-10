"""限流器单元测试 + 中间件集成测试。

覆盖 app/middleware/rate_limit.py 与 app/main.py 的中间件装配。
"""
import pytest

from app.config import settings
from app.middleware.rate_limit import (
    SlidingWindowLimiter, RateLimitMiddleware, default_limiter,
    resolve_limit, _client_identity,
)
from tests.helpers import register, build_register_payload


class TestSlidingWindowLimiter:
    def test_未达上限时全部放行(self):
        limiter = SlidingWindowLimiter()
        results = [limiter.check("u1", 3, 60) for _ in range(3)]
        assert all(r[0] for r in results)

    def test_超过上限被拒绝(self):
        limiter = SlidingWindowLimiter()
        assert limiter.check("u1", 2, 60)[0] is True
        assert limiter.check("u1", 2, 60)[0] is True
        allowed, retry_after, remaining = limiter.check("u1", 2, 60)
        assert allowed is False
        assert retry_after >= 1
        assert remaining == 0

    def test_remaining随调用递减(self):
        limiter = SlidingWindowLimiter()
        assert limiter.check("u1", 5, 60)[2] == 4
        assert limiter.check("u1", 5, 60)[2] == 3
        assert limiter.check("u1", 5, 60)[2] == 2

    def test_不同key互不影响(self):
        limiter = SlidingWindowLimiter()
        limiter.check("a", 1, 60)
        assert limiter.check("a", 1, 60)[0] is False
        assert limiter.check("b", 1, 60)[0] is True

    def test_窗口过期后配额恢复(self):
        limiter = SlidingWindowLimiter()
        limiter.check("u1", 1, 1)
        assert limiter.check("u1", 1, 1)[0] is False
        import time
        time.sleep(1.1)
        assert limiter.check("u1", 1, 1)[0] is True

    def test_窗口滑动而非固定窗口(self):
        """滑动窗口：老请求过期后立即腾出配额，不会出现整分钟清零的突刺。"""
        limiter = SlidingWindowLimiter()
        limiter.check("u1", 2, 2)
        limiter.check("u1", 2, 2)
        assert limiter.check("u1", 2, 2)[0] is False
        import time
        time.sleep(2.1)
        assert limiter.check("u1", 2, 2)[0] is True

    def test_上限为0表示不限流(self):
        limiter = SlidingWindowLimiter()
        assert all(limiter.check("u1", 0, 60)[0] for _ in range(100))

    def test_reset清空全部计数(self):
        limiter = SlidingWindowLimiter()
        limiter.check("u1", 1, 60)
        limiter.reset()
        assert limiter.check("u1", 1, 60)[0] is True

    def test_容量上限保护不会无限增长(self):
        limiter = SlidingWindowLimiter()
        for i in range(10001):
            limiter.check(f"k{i}", 100, 60)
        assert len(limiter._hits) <= 10000


class TestResolveLimit:
    def test_登录接口走严格配额(self):
        assert resolve_limit("POST", "/api/auth/login") == ("login", settings.rate_limit_login_per_minute)

    def test_注册接口走最严配额(self):
        assert resolve_limit("POST", "/api/auth/register") == ("register", settings.rate_limit_register_per_minute)

    def test_AI对话接口有独立配额(self):
        assert resolve_limit("POST", "/api/ai/chat-stream") == ("ai", settings.rate_limit_ai_per_minute)
        assert resolve_limit("POST", "/api/model/chat") == ("ai", settings.rate_limit_ai_per_minute)

    def test_其它接口走默认配额(self):
        assert resolve_limit("GET", "/api/jobs/stat/city") == ("default", settings.rate_limit_default_per_minute)

    def test_非接口路径不限流(self):
        assert resolve_limit("GET", "/") is None
        assert resolve_limit("GET", "/static/app.js") is None

    def test_同一路径不同方法区分对待(self):
        """只有 POST 登录会被限流，GET 不该命中登录规则。"""
        assert resolve_limit("GET", "/api/auth/login") == ("default", settings.rate_limit_default_per_minute)


class TestClientIdentity:
    def test_无认证头时退化为IP(self):
        scope = {"headers": [], "client": ("10.0.0.1", 1234)}
        assert _client_identity(scope) == "ip:10.0.0.1"

    def test_默认忽略可伪造的代理头(self):
        """默认不采信 X-Forwarded-For。

        代理头由客户端完全可控，若当作限流维度，攻击者每个请求伪造一个新 IP
        就能获得一份新配额，登录防爆破与注册防灌水会同时失效。
        因此默认必须退回 TCP 层真实对端地址。
        """
        scope = {"headers": [(b"x-forwarded-for", b"203.0.113.5")], "client": ("10.0.0.1", 1234)}
        assert _client_identity(scope) == "ip:10.0.0.1"

    def test_可信代理场景需显式开启开关才采信代理头(self):
        """开启 RATE_LIMIT_TRUST_FORWARDED_FOR 后，取 XFF 第一段作为真实客户端 IP。"""
        original = settings.rate_limit_trust_forwarded_for
        settings.rate_limit_trust_forwarded_for = True
        try:
            scope = {
                "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
                "client": ("10.0.0.1", 1234),
            }
            assert _client_identity(scope) == "ip:203.0.113.5"
        finally:
            settings.rate_limit_trust_forwarded_for = original

    def test_携带合法token时按用户ID计数(self):
        from app.utils.security import create_token

        token = create_token(42, "u42", "USER")
        scope = {"headers": [(b"authorization", f"Bearer {token}".encode())]}
        assert _client_identity(scope) == "user:42"

    def test_非法token仍退化为IP(self):
        scope = {"headers": [(b"authorization", b"Bearer garbage")], "client": ("10.0.0.9", 1)}
        assert _client_identity(scope) == "ip:10.0.0.9"


@pytest.mark.asyncio
class TestRateLimitMiddleware:
    async def test_登录接口超限返回429(self, client):
        limit = settings.rate_limit_login_per_minute
        codes = []
        # 用不存在的用户名：只会返回 401，不会触发账号锁定污染后续断言
        for i in range(limit + 1):
            resp = await client.post(
                "/api/auth/login",
                json={"username": f"ghost{i}", "password": "whatever"},
            )
            codes.append(resp.status_code)

        assert codes[:limit] == [401] * limit
        assert codes[-1] == 429

    async def test_响应带RetryAfter重试提示头(self, client):
        for i in range(settings.rate_limit_login_per_minute + 1):
            resp = await client.post(
                "/api/auth/login", json={"username": f"ghost{i}", "password": "x"}
            )
        assert resp.status_code == 429
        assert resp.headers["retry-after"].isdigit()
        assert int(resp.headers["retry-after"]) >= 1

    async def test_放行请求的响应带配额头(self, client):
        resp = await client.get("/api/jobs/stat/city")
        assert resp.status_code == 200
        assert int(resp.headers["x-ratelimit-remaining"]) < settings.rate_limit_default_per_minute

    async def test_静态资源不受限流影响(self, client):
        for _ in range(settings.rate_limit_default_per_minute + 5):
            assert (await client.get("/api/jobs/stat/city")).status_code in (200, 429)

    async def test_已登录用户按用户维度计数(self, client, db_session):
        """同一用户名下的并发请求走 user:<id> 计数，不会被同 IP 的其他用户挤占。"""
        from tests.helpers import admin_token
        token = await admin_token(client, db_session)

        codes = []
        for _ in range(settings.rate_limit_default_per_minute + 1):
            resp = await client.get(
                "/api/jobs/stat/city", headers={"Authorization": f"Bearer {token}"}
            )
            codes.append(resp.status_code)
        assert codes[-1] == 429

    async def test_注册超限后仍可访问其它接口(self, client):
        """限流是分桶的：注册被打满不应影响查询接口。"""
        limit = settings.rate_limit_register_per_minute
        for i in range(limit + 1):
            resp = await client.post(
                "/api/auth/register", json=build_register_payload(f"spam{i}")
            )
        assert resp.status_code == 429

        assert (await client.get("/api/jobs/stat/city")).status_code == 200

    async def test_关闭限流开关后不再拦截(self, app, engine):
        settings.rate_limit_enabled = False
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://pytest") as c:
                for _ in range(settings.rate_limit_login_per_minute + 3):
                    resp = await c.post(
                        "/api/auth/login", json={"username": "ghost", "password": "x"}
                    )
                    assert resp.status_code == 401
        finally:
            settings.rate_limit_enabled = True
