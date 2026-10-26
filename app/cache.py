"""统一缓存层：Redis 优先，未配置 / 连接失败时自动降级为进程内内存缓存。

**为什么做这一层**：知识库检索结果与 embedding 向量在单进程部署下用内存 dict 就够，
但多副本部署时两份实例各自持有一份缓存，知识库改了 A 实例清不掉 B 实例的缓存，
表现为「删除后仍被召回」。Redis 天然解决跨进程一致性问题。

设计约束：
1. **降级不能影响业务**：缓存读取失败只记日志并回退到「视为未命中」，由调用方
   重新查库；写入失败只记日志。缓存是加速手段，不是正确性依赖。
2. **接口保持同步唤醒 async**：全部方法为 async，Redis 用 redis.asyncio，
   内存后端也模拟 async 签名，调用方无需感知后端。
3. **值必须是 JSON 可序列化的**：跨进程共享要求值能落 Redis，因此缓存对象一律
   存「id 列表」这类轻量结构（见 knowledge_service.get_all_enabled），
   不做 ORM 对象缓存。

用法::

    from app.cache import cache

    await cache.set("kb:enabled_ids", json.dumps(ids), 600)
    raw = await cache.get("kb:enabled_ids")
    await cache.delete_prefix("kb:")
"""
import asyncio
import json
import logging
import time
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class Cache:
    """统一缓存门面。自动探测 Redis，失败即永久降级内存（避免每请求重连）。"""

    def __init__(self):
        self._redis = None
        self._redis_broken = False
        self._tried = False
        self._memory: dict = {}
        self._memory_ttl: dict = {}
        self._lock = asyncio.Lock()

    async def _get_redis(self):
        """惰性建立 Redis 连接；失败标记 broken，后续请求直接走内存。"""
        if self._redis is not None or self._redis_broken:
            return self._redis
        if not settings.redis_url:
            self._redis_broken = True
            return None
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.redis_url, decode_responses=True, socket_connect_timeout=2
            )
            await client.ping()
            self._redis = client
            logger.info("Redis 缓存已启用：%s", settings.redis_url)
        except Exception as e:  # noqa: BLE001 - 连接失败一律降级
            self._redis_broken = True
            logger.warning("Redis 不可用，降级为内存缓存：%s", e)
        return self._redis

    async def get(self, key: str) -> Optional[str]:
        r = await self._get_redis()
        if r is not None:
            try:
                return await r.get(key)
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 读取失败，回退内存：%s", e)
        # 内存后端（含 TTL 检查）
        exp = self._memory_ttl.get(key)
        if exp is not None and time.monotonic() > exp:
            self._memory.pop(key, None)
            self._memory_ttl.pop(key, None)
            return None
        return self._memory.get(key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                await r.set(key, value, ex=ttl)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 写入失败，回退内存：%s", e)
        self._memory[key] = value
        self._memory_ttl[key] = time.monotonic() + max(ttl, 1)

    async def delete(self, key: str) -> None:
        r = await self._get_redis()
        if r is not None:
            try:
                await r.delete(key)
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 删除失败（忽略）：%s", e)
        self._memory.pop(key, None)
        self._memory_ttl.pop(key, None)

    async def delete_prefix(self, prefix: str) -> None:
        """删除指定前缀下的全部键（知识库变更时批量失效用）。"""
        r = await self._get_redis()
        if r is not None:
            async for key in self._scan_prefix(r, prefix):
                try:
                    await r.delete(key)
                except Exception:  # noqa: BLE001
                    continue
        for key in [k for k in self._memory if k.startswith(prefix)]:
            self._memory.pop(key, None)
            self._memory_ttl.pop(key, None)

    @staticmethod
    async def _scan_prefix(r, prefix: str):
        """迭代扫描 Redis 中指定前缀的键（SCAN 而非 KEYS，避免阻塞）。"""
        try:
            async for key in r.scan_iter(match=f"{prefix}*", count=200):
                yield key
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis SCAN 失败：%s", e)

    async def clear(self) -> None:
        """清空缓存（仅测试 / 运维手动重建场景使用）。"""
        r = await self._get_redis()
        if r is not None:
            try:
                await r.flushdb()
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis flushdb 失败（忽略）：%s", e)
        self._memory.clear()
        self._memory_ttl.clear()

    def clear_sync(self) -> None:
        """同步清空缓存（供无事件循环的同步测试 fixture 使用）。

        测试环境通常未配置 Redis（会直接走内存后端），因此只需清内存。
        """
        self._memory.clear()
        self._memory_ttl.clear()


# 模块级单例：全进程共用一份连接与降级状态
cache = Cache()