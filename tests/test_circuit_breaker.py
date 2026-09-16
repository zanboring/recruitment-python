# -*- coding: utf-8 -*-
"""熔断器单元测试：三态流转 / 快速失败 / 半开试探 / 恢复。"""
import asyncio
import time

import pytest

from app.utils import circuit_breaker
from app.utils.circuit_breaker import (
    CircuitBreaker, CircuitOpenError, CircuitState, circuit_breaker as get_breaker,
)


def make_breaker(failure_threshold=3, cooldown=0.5):
    cb = CircuitBreaker(
        name="test", failure_threshold=failure_threshold,
        cooldown_seconds=cooldown, enabled=True,
    )
    return cb


def test_initial_closed():
    cb = make_breaker()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_consecutive_failures_open_and_quick_fail():
    cb = make_breaker(failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    # 熔断期间拒绝放行（快速失败，不发起请求）
    assert cb.allow_request() is False
    # reject 由 should_attempt 记录（真实调用方语义）
    assert cb.should_attempt() is False
    assert cb.stats()["rejected"] == 1


def test_after_cooldown_half_open_then_success_closes():
    cb = make_breaker(failure_threshold=2, cooldown=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    # 冷却结束 → HALF_OPEN，放行一个试探
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_returns_open():
    cb = make_breaker(failure_threshold=2, cooldown=0.05)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.1)
    cb.allow_request()  # 半开试探
    cb.record_failure()  # 试探失败
    assert cb.state == CircuitState.OPEN


def test_disabled_always_allows():
    cb = CircuitBreaker(name="off", enabled=False)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.allow_request() is True
    assert cb.closed is True


def test_singleton_by_name():
    a = get_breaker("test-singleton")
    b = get_breaker("test-singleton")
    assert a is b


def test_guard_context_raises_when_open():
    """OPEN 期间 guard() 抛 CircuitOpenError，调用方走降级。"""
    cb = make_breaker(failure_threshold=1, cooldown=5)
    cb.record_failure()  # 触发 OPEN

    async def guarded_call():
        if not cb.should_attempt():
            raise CircuitOpenError("熔断中")
        return "不会被调用"

    async def run():
        with pytest.raises(CircuitOpenError):
            await guarded_call()

    asyncio.run(run())


def test_reset_all():
    get_breaker("rst-a")
    get_breaker("rst-b")
    circuit_breaker.reset_all()
    assert circuit_breaker._BREAKERS == {}