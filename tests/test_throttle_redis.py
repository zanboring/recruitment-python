# -*- coding: utf-8 -*-
"""采集节流 / 日配额的 Redis 后端测试（多副本共享）。

**不依赖真实 Redis 进程**：用同步替身 ``FakeRedisSync``（含真实的 TTL 流逝模拟），
CI 无 Redis 也能全绿。

**诚实局限**：与限流/熔断两份测试同理 —— 替身是 Python 版同一算法，
**Lua 脚本本身的正确性无法在这里被证明**。真实 Redis 下的 A4
（2 worker 下平台累计访问数 ≤ `CRAWL_DAILY_QUOTA_PER_PLATFORM`）需在有 Redis 的环境验收。

本文件的两条对照（内存版双副本各算一份 vs Redis 版共享一份）是
「为什么日配额必须在多副本下共享」的可执行论证 —— 它是**对招聘站点的合规承诺**。
"""
import time

import pytest

from app.config import settings
from app.crawlers.throttle import (
    _LUA_QUOTA_CONSUME,
    DailyQuota,
    DomainGate,
    RedisThrottleClient,
)


class FakeRedisSync:
    """最小同步 Redis 替身：PING / GET / SET(NX PX) / PTTL / EVAL。

    **模拟真实的 TTL 流逝**（用 monotonic 记绝对过期时刻）——
    域名节流的用例依赖「槽位键到点自动过期」，若 TTL 永不流逝，
    测出来的就只是「重试耗尽后放行」，而不是真正的「等到释放再抢到」。
    """

    def __init__(self):
        self.kv: dict = {}
        self.expire_at: dict = {}
        self.fail = False

    def ping(self):
        return True

    def _check(self):
        if self.fail:
            raise ConnectionError("fake redis down")

    def _purge(self, key):
        exp = self.expire_at.get(key)
        if exp is not None and time.monotonic() >= exp:
            self.kv.pop(key, None)
            self.expire_at.pop(key, None)

    def get(self, key):
        self._check()
        self._purge(key)
        return self.kv.get(key)

    def set(self, key, value, nx=False, px=None):
        self._check()
        self._purge(key)
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if px:
            self.expire_at[key] = time.monotonic() + px / 1000.0
        return True

    def pttl(self, key):
        self._check()
        self._purge(key)
        if key not in self.kv:
            return -2
        exp = self.expire_at.get(key)
        if exp is None:
            return -1
        return max(0, int((exp - time.monotonic()) * 1000))

    def eval(self, script, numkeys, *args):
        self._check()
        assert "INCRBY" in script, "目前只有日配额一个 Lua 脚本"
        key = args[0]
        limit, amount, ttl = int(args[1]), int(args[2]), int(args[3])
        self._purge(key)
        current = int(self.kv.get(key, 0) or 0)
        if current + amount > limit:
            return 0
        newval = current + amount
        self.kv[key] = str(newval)
        self.expire_at[key] = time.monotonic() + ttl
        return newval


def _rb(fake):
    """把替身包成 RedisThrottleClient（client 非空时 enabled() 直接为真）。"""
    return RedisThrottleClient(client=fake)


def _memory_rb():
    """内存模式：client 为空 + conftest 已固定 quota_backend=memory → enabled() 为假。"""
    return RedisThrottleClient(client=None)


class TestDailyQuotaRedis:
    def test_未超限时可消耗(self):
        quota = DailyQuota(redis_backend=_rb(FakeRedisSync()))
        assert quota.try_consume("boss", limit=2) is True
        assert quota.try_consume("boss", limit=2) is True
        assert quota.used("boss") == 2

    def test_超限时拒绝且不把额度吃掉(self):
        quota = DailyQuota(redis_backend=_rb(FakeRedisSync()))
        assert quota.try_consume("boss", limit=2) is True
        assert quota.try_consume("boss", limit=2) is True
        assert quota.try_consume("boss", limit=2) is False
        assert quota.try_consume("boss", limit=2) is False
        assert quota.used("boss") == 2  # 失败的尝试不计数

    def test_配额为0视为不限制(self):
        quota = DailyQuota(redis_backend=_rb(FakeRedisSync()))
        assert all(quota.try_consume("boss", limit=0) for _ in range(50))

    def test_不同平台独立计数(self):
        quota = DailyQuota(redis_backend=_rb(FakeRedisSync()))
        assert quota.try_consume("boss", limit=1) is True
        assert quota.try_consume("boss", limit=1) is False
        assert quota.try_consume("51job", limit=1) is True

    def test_key含北京日期(self):
        fake = FakeRedisSync()
        quota = DailyQuota(redis_backend=_rb(fake))
        quota.try_consume("boss", limit=5)
        assert any(
            k.startswith("recsys:throttle:quota:boss:") and quota.today() in k
            for k in fake.kv
        ), f"key 命名不符预期：{list(fake.kv)}"

    def test_过期时长在合理区间(self):
        """到北京时间明天 0 点为止（+60s 余量）→ 60s ~ 25h。"""
        ttl = DailyQuota._ttl_seconds()
        assert 60 <= ttl <= 25 * 3600

    def test_remaining走Redis全局计数(self):
        quota = DailyQuota(redis_backend=_rb(FakeRedisSync()))
        quota.try_consume("boss", limit=3)
        assert quota.remaining("boss", 3) == 2
        assert quota.remaining("boss", 0) is None

    def test_snapshot按limits逐平台查全局(self):
        fake = FakeRedisSync()
        a = DailyQuota(redis_backend=_rb(fake))
        b = DailyQuota(redis_backend=_rb(fake))
        a.try_consume("boss", limit=10)
        b.try_consume("boss", limit=10)
        snap = b.snapshot({"boss": 10})
        assert snap["boss"]["used"] == 2  # 跨实例可见
        assert snap["boss"]["remaining"] == 8


class TestCrossReplicaQuota:
    def test_跨副本共享_总消耗不超过上限(self):
        """**A4 的可执行版**：两个副本各自独立实例、共享同一 Redis。"""
        fake = FakeRedisSync()
        a = DailyQuota(redis_backend=_rb(fake))
        b = DailyQuota(redis_backend=_rb(fake))
        limit = 5
        consumed = 0
        for i in range(limit * 2):
            quota = a if i % 2 == 0 else b
            if quota.try_consume("boss", limit=limit):
                consumed += 1
        assert consumed == limit, (
            f"Redis 共享配额下总消耗应为 {limit}，实际 {consumed}；"
            f"若为 {limit * 2} 则说明配额未跨副本共享（合规承诺不成立）"
        )

    def test_对照组_内存版双副本各算一份(self):
        """对照组：进程内配额各自为战 → 总消耗 2×limit（这正是要解决的问题）。"""
        a = DailyQuota(redis_backend=_memory_rb())
        b = DailyQuota(redis_backend=_memory_rb())
        limit = 5
        consumed = 0
        for i in range(limit * 2):
            quota = a if i % 2 == 0 else b
            if quota.try_consume("boss", limit=limit):
                consumed += 1
        assert consumed == limit * 2


class TestDomainGateRedis:
    async def test_首次请求立即放行(self):
        gate = DomainGate(redis_backend=_rb(FakeRedisSync()))
        assert await gate.acquire("a.com", 10) == 0.0

    async def test_槽位未释放时第二个请求需等待(self):
        """槽位键的 TTL 就是「距上次请求的剩余间隔」—— 等到释放才能再进。"""
        gate = DomainGate(redis_backend=_rb(FakeRedisSync()))
        assert await gate.acquire("a.com", 0.05) == 0.0
        waited = await gate.acquire("a.com", 0.05)
        assert waited > 0  # 确实是等出来的，不是立刻放行

    async def test_不同域名互不影响(self):
        gate = DomainGate(redis_backend=_rb(FakeRedisSync()))
        assert await gate.acquire("a.com", 10) == 0.0
        assert await gate.acquire("b.com", 10) == 0.0

    async def test_未指定域名或间隔为0时立即返回(self):
        gate = DomainGate(redis_backend=_rb(FakeRedisSync()))
        assert await gate.acquire("", 10) == 0.0
        assert await gate.acquire("a.com", 0) == 0.0

    async def test_跨副本共享槽位(self):
        """两个实例共享同一 Redis → 第二个必须等第一个的槽位过期。"""
        fake = FakeRedisSync()
        a = DomainGate(redis_backend=_rb(fake))
        b = DomainGate(redis_backend=_rb(fake))
        assert await a.acquire("a.com", 0.05) == 0.0
        assert await b.acquire("a.com", 0.05) > 0


class TestDegradation:
    def test_配额Redis故障时降级进程内(self):
        fake = FakeRedisSync()
        quota = DailyQuota(redis_backend=_rb(fake))
        assert quota.try_consume("boss", limit=2) is True  # 走 Redis

        fake.fail = True  # Redis 挂了 → 降级进程内计数
        assert quota.try_consume("boss", limit=2) is True   # 本地 0 -> 1
        assert quota.try_consume("boss", limit=2) is True   # 本地 1 -> 2
        assert quota.try_consume("boss", limit=2) is False  # 本地 2 + 1 > 2

    async def test_域名节流Redis故障时降级进程内(self):
        fake = FakeRedisSync()
        gate = DomainGate(redis_backend=_rb(fake))
        assert await gate.acquire("a.com", 0.01) == 0.0

        fake.fail = True  # Redis 挂了 → 降级本地（本地时间戳为空，故仍为 0）
        assert await gate.acquire("a.com", 0.01) == 0.0

    def test_未配置redis_url时不启用(self, monkeypatch):
        monkeypatch.setattr(settings, "quota_backend", "redis", raising=False)
        monkeypatch.setattr(settings, "redis_url", "", raising=False)
        assert RedisThrottleClient().enabled() is False

    def test_默认memory后端不启用(self, monkeypatch):
        monkeypatch.setattr(settings, "quota_backend", "memory", raising=False)
        monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0", raising=False)
        assert RedisThrottleClient().enabled() is False


class TestConfigDefaults:
    def test_配额后端默认memory保证单机行为不变(self):
        from app.config import Settings

        assert Settings.model_fields["quota_backend"].default == "memory"


class TestLuaScript:
    def test_脚本含判超限与递增与过期(self):
        assert "GET" in _LUA_QUOTA_CONSUME
        assert "INCRBY" in _LUA_QUOTA_CONSUME
        assert "EXPIRE" in _LUA_QUOTA_CONSUME

    def test_先判超限再递增(self):
        """顺序很关键：先 INCR 再判断会让超限的那次也把额度吃掉。"""
        idx_check = _LUA_QUOTA_CONSUME.index("current + amount > limit")
        idx_incr = _LUA_QUOTA_CONSUME.index("INCRBY")
        assert idx_check < idx_incr
