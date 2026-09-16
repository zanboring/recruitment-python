"""统一缓存层（app/cache.py）的单元测试。

覆盖（均在**内存后端**下验证，离线可跑）：
- set / get 基本读写
- TTL 过期后取不到
- delete_prefix 批量失效
- 未配置 Redis 时自动降级内存（不报错）
"""
import asyncio
import pytest

from app.cache import Cache


def make_cache() -> Cache:
    c = Cache()
    # 显式关闭 Redis 探测：测试不依赖外部服务
    c._redis_broken = True
    return c


@pytest.fixture(autouse=True)
def _clean_cache_state():
    """每个用例使用独立实例，避免相互污染。"""
    yield


def test_set_get_roundtrip():
    c = make_cache()
    asyncio.run(c.set("k1", "v1", 60))
    assert asyncio.run(c.get("k1")) == "v1"


def test_get_missing_returns_none():
    c = make_cache()
    assert asyncio.run(c.get("nope")) is None


def test_ttl_expiry():
    c = make_cache()
    asyncio.run(c.set("k2", "v2", 1))
    assert asyncio.run(c.get("k2")) == "v2"
    # 手动推进过期时间：把 TTL 时间戳改为过去
    import time
    c._memory_ttl["k2"] = time.monotonic() - 1
    assert asyncio.run(c.get("k2")) is None
    assert "k2" not in c._memory


def test_delete_prefix():
    c = make_cache()
    asyncio.run(c.set("kb:1", "a", 60))
    asyncio.run(c.set("kb:2", "b", 60))
    asyncio.run(c.set("emb:x", "c", 60))
    asyncio.run(c.delete_prefix("kb:"))
    assert asyncio.run(c.get("kb:1")) is None
    assert asyncio.run(c.get("kb:2")) is None
    assert asyncio.run(c.get("emb:x")) == "c"


def test_redis_unavailable_degrades_to_memory():
    """未配置 / 连接失败时 get/set 不抛异常，走内存。"""
    c = make_cache()
    # 显式标记 broken 后，读写应静默走内存，不抛任何异常
    c._redis_broken = True
    asyncio.run(c.set("degraded", "ok", 60))
    assert asyncio.run(c.get("degraded")) == "ok"


def test_no_redis_url_auto_memory():
    """完全不配置 REDIS_URL 时（生产默认），缓存应静默使用内存后端。"""
    import app.cache as cache_mod
    from app.config import settings

    original_url = settings.redis_url
    try:
        settings.redis_url = ""
        c = Cache()
        asyncio.run(c.set("auto", "memory", 60))
        assert asyncio.run(c.get("auto")) == "memory"
    finally:
        settings.redis_url = original_url


def test_clear_sync():
    c = make_cache()
    asyncio.run(c.set("c1", "x", 60))
    c.clear_sync()
    assert asyncio.run(c.get("c1")) is None