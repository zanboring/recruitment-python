# -*- coding: utf-8 -*-
"""熔断器 Redis 状态共享测试（多副本全局熔断）。

**不依赖真实 Redis 进程**：用同步替身 ``FakeRedisSync`` 实现三个 Lua 脚本的
等价语义，CI 无 Redis 也能全绿。

**诚实局限**（与限流那份测试同理）：替身是 Python 版的同一算法，
**Lua 脚本本身的正确性无法在这里被证明**。真实 Redis 下的 A10
（一个 worker 触发熔断 → 另一个 worker 也进入熔断态）需在有 Redis 的环境验收。

本文件的对照用例（内存版两个实例互不影响 vs Redis 版「一熔俱熔」）
是「为什么多副本必须共享熔断状态」的可执行论证。
"""
import time

import pytest

from app.config import settings
from app.utils.circuit_breaker import (
    _LUA_BREAKER_ALLOW,
    _LUA_BREAKER_FAILURE,
    _LUA_BREAKER_SUCCESS,
    CircuitBreaker,
    CircuitState,
    RedisCircuitBreaker,
    circuit_breaker,
    reset_all,
)


class FakeRedisSync:
    """最小同步 Redis 替身：按脚本特征分派到等价的 Python 实现。

    只实现熔断器用到的命令：GET / SET(NX PX) / DEL / INCR / PEXPIRE / EVAL。
    """

    def __init__(self):
        self.kv: dict = {}
        self.fail = False

    # ---- 基础命令 ----
    def ping(self):
        return True

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("fake redis down")

    def get(self, key):
        self._maybe_fail()
        return self.kv.get(key)

    def set(self, key, value, nx=False, px=None):
        self._maybe_fail()
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def delete(self, *keys):
        self._maybe_fail()
        for k in keys:
            self.kv.pop(k, None)
        return len(keys)

    def incr(self, key):
        self._maybe_fail()
        value = int(self.kv.get(key, 0)) + 1
        self.kv[key] = str(value)
        return value

    def pexpire(self, key, ms):
        self._maybe_fail()
        return True

    # ---- EVAL：按脚本特征分派到等价实现 ----
    def eval(self, script, numkeys, *args):
        self._maybe_fail()
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "NX" in script:
            return self._allow(keys, argv)
        if "INCR" in script:
            return self._failure(keys, argv)
        return self._success(keys)

    def _allow(self, keys, argv):
        state_key, open_since_key, probe_key = keys
        now, cooldown = float(argv[0]), float(argv[1])
        if state_key not in self.kv:
            return 1
        open_since = float(self.kv.get(open_since_key) or 0)
        if now - open_since < cooldown:
            return 0
        if probe_key not in self.kv:
            self.kv[probe_key] = "1"
            return 1
        return 0

    def _success(self, keys):
        for k in keys:
            self.kv.pop(k, None)
        return 1

    def _failure(self, keys, argv):
        state_key, open_since_key, probe_key, failures_key = keys
        now, threshold = float(argv[0]), int(argv[1])
        if state_key in self.kv:
            self.kv[state_key] = "open"
            self.kv[open_since_key] = str(now)
            self.kv.pop(probe_key, None)
            self.kv.pop(failures_key, None)
            return 1
        failures = int(self.kv.get(failures_key, 0)) + 1
        self.kv[failures_key] = str(failures)
        if failures >= threshold:
            self.kv[state_key] = "open"
            self.kv[open_since_key] = str(now)
            self.kv.pop(failures_key, None)
            return 1
        return 0


def _breaker(fake, name="t", threshold=3, cooldown=30.0):
    return RedisCircuitBreaker(
        name=name, failure_threshold=threshold, cooldown_seconds=cooldown, client=fake
    )


class TestRedisCircuitBreaker:
    def test_未达阈值时放行(self):
        b = _breaker(FakeRedisSync(), threshold=3)
        assert b.allow_request() is True
        b.record_failure()
        b.record_failure()
        assert b.allow_request() is True

    def test_达阈值后熔断(self):
        b = _breaker(FakeRedisSync(), threshold=3)
        for _ in range(3):
            b.record_failure()
        assert b.allow_request() is False
        assert b.state == CircuitState.OPEN

    def test_成功调用清空状态回到CLOSED(self):
        fake = FakeRedisSync()
        b = _breaker(fake, threshold=2)
        b.record_failure()
        b.record_failure()
        assert b.allow_request() is False
        b.record_success()
        assert b.state == CircuitState.CLOSED
        assert b.allow_request() is True

    def test_跨副本共享_一个熔断另一个也拒绝(self):
        """核心判据：A 副本熔断 → B 副本（独立实例、共享同一 Redis）也必须拒绝。"""
        fake = FakeRedisSync()
        a = _breaker(fake, name="shared", threshold=2)
        b = _breaker(fake, name="shared", threshold=2)

        a.record_failure()
        a.record_failure()

        assert a.allow_request() is False
        assert b.allow_request() is False  # ← 内存版这里是 True

    def test_跨副本共享_一个恢复另一个也恢复(self):
        fake = FakeRedisSync()
        a = _breaker(fake, name="recover", threshold=1)
        b = _breaker(fake, name="recover", threshold=1)
        a.record_failure()
        assert b.allow_request() is False
        # 任一副本探活成功 → 全部副本同时恢复
        b.record_success()
        assert a.allow_request() is True

    def test_对照组_内存版两个实例互不影响(self):
        """对照组：进程内熔断器各自为战 —— 这正是本改造要解决的问题。"""
        a = CircuitBreaker(name="mem-a", failure_threshold=2, cooldown_seconds=30)
        b = CircuitBreaker(name="mem-b", failure_threshold=2, cooldown_seconds=30)
        a.record_failure()
        a.record_failure()
        assert a.allow_request() is False
        assert b.allow_request() is True  # 另一个实例毫不知情

    def test_冷却后半开只放行一个试探(self):
        """半开期用 SET NX 抢试探权：跨副本也只有一个请求能通过。"""
        fake = FakeRedisSync()
        b = _breaker(fake, name="half", threshold=1, cooldown=30.0)
        b.record_failure()
        assert b.allow_request() is False

        # 把 open_since 前移，模拟冷却已过
        fake.kv["recsys:breaker:half:open_since"] = str(time.time() - 31)
        assert b.state == CircuitState.HALF_OPEN
        assert b.allow_request() is True   # 抢到试探权
        assert b.allow_request() is False  # 已有试探在途 → 拒绝

    def test_半开试探失败立即回到OPEN(self):
        fake = FakeRedisSync()
        b = _breaker(fake, name="probe-fail", threshold=1, cooldown=30.0)
        b.record_failure()
        fake.kv["recsys:breaker:probe-fail:open_since"] = str(time.time() - 31)
        assert b.allow_request() is True       # 试探放行
        b.record_failure()                     # 试探失败
        assert b.allow_request() is False      # 回到 OPEN

    def test_redis故障时降级父类实现(self):
        """Redis 挂了：熔断器仍工作（退回进程内语义），不是失效。"""
        fake = FakeRedisSync()
        b = _breaker(fake, name="deg", threshold=2)
        assert b.allow_request() is True

        fake.fail = True  # Redis 不可用
        assert b.allow_request() is True   # 降级父类：放行
        b.record_failure()
        b.record_failure()
        assert b.allow_request() is False  # 父类计数生效 → 熔断

    def test_stats标注后端与真实状态(self):
        fake = FakeRedisSync()
        b = _breaker(fake, name="stat")
        s = b.stats()
        assert s["backend"] == "redis"
        assert s["state"] == "closed"
        assert s["name"] == "stat"

    def test_禁用时不参与熔断(self):
        b = _breaker(FakeRedisSync(), name="disabled", threshold=1)
        b.enabled = False
        b.record_failure()
        assert b.allow_request() is True


class TestFactoryBackendSelection:
    def teardown_method(self):
        reset_all()

    def test_默认memory后端返回父类实例(self, monkeypatch):
        monkeypatch.setattr(settings, "circuit_breaker_backend", "memory", raising=False)
        reset_all()
        b = circuit_breaker("factory-memory")
        assert type(b) is CircuitBreaker

    def test_redis后端返回Redis实例(self, monkeypatch):
        monkeypatch.setattr(settings, "circuit_breaker_backend", "redis", raising=False)
        monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0", raising=False)
        reset_all()
        b = circuit_breaker("factory-redis")
        assert isinstance(b, RedisCircuitBreaker)

    def test_redis后端但未配url时返回父类(self, monkeypatch):
        monkeypatch.setattr(settings, "circuit_breaker_backend", "redis", raising=False)
        monkeypatch.setattr(settings, "redis_url", "", raising=False)
        reset_all()
        b = circuit_breaker("factory-nourl")
        assert type(b) is CircuitBreaker

    def test_同名返回同一实例(self):
        reset_all()
        assert circuit_breaker("same") is circuit_breaker("same")


class TestLuaScripts:
    def test_半开试探用SET_NX保证跨副本唯一(self):
        assert "SET" in _LUA_BREAKER_ALLOW
        assert "NX" in _LUA_BREAKER_ALLOW
        assert "GET" in _LUA_BREAKER_ALLOW

    def test_成功脚本清理全部四个状态键(self):
        assert _LUA_BREAKER_SUCCESS.count("DEL") == 4

    def test_失败脚本累加计数并按阈值熔断且设过期(self):
        assert "INCR" in _LUA_BREAKER_FAILURE
        assert "PEXPIRE" in _LUA_BREAKER_FAILURE
        assert "SET" in _LUA_BREAKER_FAILURE


class TestConfigDefaults:
    def test_熔断后端默认memory保证单机行为不变(self):
        from app.config import Settings

        assert Settings.model_fields["circuit_breaker_backend"].default == "memory"
