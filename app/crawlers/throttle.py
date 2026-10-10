"""跨任务的采集节流、日配额与 ``Retry-After`` 解析。

## 为什么还需要这个模块

``BaseCrawler`` 的反爬护栏解决的是**一次爬取内部**的问题：相邻两次请求之间随机延时、
失败后指数退避、命中风控信号后自适应放大。但它有三个盲区，都出在「任务之间」：

1. **同一个域名可能被并发访问** —— 定时任务一次要跑「3 关键词 × 6 城市」= 18 组，
   两个任务若命中同一域名，只靠组内延时并不能阻止它们在同一时刻发出请求；
2. **没有「今天已经请求了多少次」的概念** —— 设不了日配额，也就无法回答
   「这个平台我今天最多访问多少次」这种最基本的运维问题；
3. **被 429 时只看自己的退避策略** —— 不读对方 ``Retry-After`` 明确要求的等待时长。

本模块补上这三样。

## 后端选择（``QUOTA_BACKEND``，默认 ``memory``）

- ``memory``（默认）：进程内状态，单实例部署完全正确、零依赖；
- ``redis`` ：三样护栏全部走 Redis，**多副本共享**。

**为什么多副本必须换**：日配额是**对招聘站点的合规承诺** —— 进程内实现下
每个 worker 各持一份计数，2 副本时实际访问量翻倍，承诺不再成立。
域名节流同理：内存版只能约束「本进程内的请求」，跨进程的两个采集任务
仍可能同时打同一域名（只加延迟挡不住，必须串行）。

**降级**：Redis 不可用时自动回退进程内实现（行为与改造前一致），
配额/节流不会因为「状态存不了」而整体失效；连接失败进冷却期，避免每请求白等超时。

**为什么用同步 redis 客户端**：``DailyQuota.try_consume()`` 的调用方
（``crawler_service._crawl_platform``）是同步调用且属他人域，不能被改成 await ——
保持同步签名是唯一不触碰调用方的做法。代价是每次判定有一次同步往返
（对「每 20 秒一次请求」的采集节奏可忽略）。
"""
import asyncio
import logging
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- Redis 后端
#
# 与限流中间件 / 熔断器同一套思路：默认进程内，多副本可切 Redis，失败自动降级。
# 键前缀把本模块的键与其他域（缓存 recsys:*、限流 recsys:ratelimit:*、
# 熔断 recsys:breaker:*）区分开，互不误伤。

_DOMAIN_KEY_PREFIX = "recsys:throttle:domain:"
_QUOTA_KEY_PREFIX = "recsys:throttle:quota:"

# 连接失败后的冷却时长（秒）：冷却期内直接用进程内实现，
# 不每请求都白等一次连接超时。
_REDIS_RECONNECT_COOLDOWN = 30.0

# 跨进程抢域名槽位的最大重试次数。刻意有限：宁可漏一次节流，
# 也不让采集任务因为「抢不到槽位」永久卡住。
_MAX_SLOT_RETRIES = 6

# 日配额消耗（Lua，原子）。
#   KEYS[1] = 配额 key；ARGV = {limit, amount, ttl}
#   返回 >0 = 消耗后的计数；0 = 超限（**不计数**，避免失败也把额度吃掉）
_LUA_QUOTA_CONSUME = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', key) or 0)
if current + amount > limit then
    return 0
end
local newval = redis.call('INCRBY', key, amount)
redis.call('EXPIRE', key, ttl)
return newval
"""


class RedisThrottleClient:
    """本模块共用的同步 Redis 客户端（惰性连接 + 冷却降级）。

    - ``enabled()``：按配置判断是否启用（``QUOTA_BACKEND=redis`` 且 ``REDIS_URL`` 非空）；
    - ``get()``：返回客户端；不可用时返回 ``None``，调用方回退进程内实现。

    ``client`` 参数供测试注入替身（用例不依赖真实 Redis 进程）。
    """

    def __init__(self, client=None):
        self._client = client
        self._cooldown_until = 0.0

    def enabled(self) -> bool:
        if self._client is not None:
            return True
        from app.config import settings

        backend = str(getattr(settings, "quota_backend", "memory") or "memory")
        return backend.strip().lower() == "redis" and bool(getattr(settings, "redis_url", ""))

    def get(self):
        if self._client is not None:
            return self._client

        now = time.time()
        if now < self._cooldown_until:
            return None

        from app.config import settings

        url = getattr(settings, "redis_url", "")
        if not url:
            return None
        try:
            import redis as sync_redis

            client = sync_redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=1.0, socket_timeout=1.0
            )
            client.ping()
        except Exception as e:  # noqa: BLE001 - 连接失败一律降级
            self._cooldown_until = now + _REDIS_RECONNECT_COOLDOWN
            logger.warning(
                "采集节流/配额 Redis 后端不可用（%.0fs 内降级进程内）：%s",
                _REDIS_RECONNECT_COOLDOWN, e,
            )
            return None
        self._client = client
        logger.info("采集节流/配额已启用 Redis 后端：%s", url)
        return client


# 模块级共享实例：两个类默认都用它（一份连接、一份冷却状态）
_shared_redis = RedisThrottleClient()


def domain_of(url: str) -> str:
    """取 URL 的域名（小写）。解析不出来时返回空串。

    返回空串而不是抛异常：调用方应把「拿不到域名」当成「无需节流」继续跑，
    不该因为一个畸形 URL 让整个采集任务失败。
    """
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except (ValueError, AttributeError):
        return ""


def parse_retry_after(value, now: Optional[datetime] = None) -> Optional[float]:
    """解析 ``Retry-After`` 响应头，返回需要等待的秒数；无法解析返回 ``None``。

    该头有两种合法格式（RFC 9110）：

    - 秒数：``Retry-After: 120``
    - HTTP 日期：``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT``

    只处理前者会漏掉后者，然后按自己的退避策略重试 —— **对方已经明确说了等多久
    却不听**，是很容易把「临时限流」升级成「封禁」的做法。

    ``now`` 仅用于测试注入，生产走当前 UTC 时间。
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # 先按秒数试（可能是小数）
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    if seconds is not None:
        return max(seconds, 0.0)

    # 再按 HTTP 日期试
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        # 该格式约定为 GMT，但解析器偶尔给出 naive；按 UTC 处理而不是本地时区
        when = when.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    return max((when - now).total_seconds(), 0.0)


class DomainGate:
    """同一域名的请求**串行化**并强制最小间隔。

    为什么要串行而不只是「加延迟」：只加延迟的话，两个任务仍可能各自「等够时间」
    然后同时发请求，对目标站点来说并发依然是 2。串行才能保证同一域名同一时刻
    只有一个请求在飞。

    等待里叠加抖动：完全固定的间隔本身就是一种可识别特征
    （真实用户的请求间隔不会精确等于某个常数）。

    多副本（``QUOTA_BACKEND=redis``）：进程内的锁管不到兄弟进程，改用 Redis 的
    ``SET NX PX`` 做跨进程槽位 —— 键的 TTL 恰好等于「距上次请求的剩余间隔」，
    单条命令原子完成「判断 + 占位 + 定时释放」，比共享时间戳的三步读写可靠
    （后者在并发下会有两个进程同时通过）。
    """

    def __init__(self, jitter_ratio: float = 0.2, redis_backend=None):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_at: Dict[str, float] = {}
        self._jitter_ratio = jitter_ratio
        self._redis = redis_backend or _shared_redis

    def _lock_for(self, domain: str) -> asyncio.Lock:
        lock = self._locks.get(domain)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[domain] = lock
        return lock

    async def acquire(self, domain: str, min_interval: float) -> float:
        """排队直到满足最小间隔，返回本次实际等待的秒数。"""
        if not domain or min_interval <= 0:
            return 0.0

        if self._redis.enabled():
            client = self._redis.get()
            if client is not None:
                return await self._acquire_cross_process(client, domain, min_interval)
            # Redis 不可用 → 落到下面的进程内路径（客户端内已记 warning）

        return await self._acquire_local(domain, min_interval)

    async def _acquire_local(self, domain: str, min_interval: float) -> float:
        """进程内串行（默认后端，也是 Redis 不可用时的降级路径）。"""
        async with self._lock_for(domain):
            elapsed = time.monotonic() - self._last_at.get(domain, 0.0)
            wait = min_interval - elapsed
            if wait > 0:
                wait += random.uniform(0, min_interval * self._jitter_ratio)
                logger.debug("域名 %s 节流等待 %.1fs", domain, wait)
                await asyncio.sleep(wait)
            self._last_at[domain] = time.monotonic()
            return max(wait, 0.0)

    async def _acquire_cross_process(self, client, domain: str, min_interval: float) -> float:
        """跨进程槽位：``SET NX PX`` 保证 ``min_interval`` 内只有一个进程在飞。"""
        key = f"{_DOMAIN_KEY_PREFIX}{domain}"
        waited = 0.0

        for _ in range(_MAX_SLOT_RETRIES):
            try:
                acquired = client.set(key, "1", nx=True, px=max(1, int(min_interval * 1000)))
            except Exception as e:  # noqa: BLE001 - 命令异常一律降级
                logger.warning("域名节流 Redis 失败，本次降级进程内：%s", e)
                return await self._acquire_local(domain, min_interval)

            if acquired:
                if waited:
                    logger.debug("域名 %s 跨进程节流等待 %.1fs", domain, waited)
                return waited

            # 槽位被别的进程占着 → 按剩余 TTL 等待后重试
            try:
                ttl_ms = client.pttl(key)
            except Exception:  # noqa: BLE001
                ttl_ms = -1
            if ttl_ms is None or ttl_ms < 0:
                continue  # 键刚刚过期，立刻重试（不睡，避免白等一整轮）
            gap = min(ttl_ms / 1000.0, min_interval)
            gap += random.uniform(0, min_interval * self._jitter_ratio)
            await asyncio.sleep(gap)
            waited += gap

        logger.warning(
            "域名 %s 重试 %s 次仍未取得跨进程槽位（已等 %.1fs），本次放行 —— "
            "宁可漏一次节流，也不让采集任务卡死",
            domain, _MAX_SLOT_RETRIES, waited,
        )
        return waited

    def reset(self) -> None:
        """清空**本进程**的节流状态（供测试与「重置采集频率」使用）。

        刻意不清 Redis 侧：跨进程槽位由 TTL 自然释放，强行删除会破坏
        「距上次请求的间隔」这一语义（等于伪造「刚请求过/从没请求过」）。
        测试要清 Redis 状态请用替身自带的方法。
        """
        self._locks.clear()
        self._last_at.clear()

    def snapshot(self) -> Dict[str, float]:
        """返回各域名「距上次请求已过多少秒」，供诊断用。"""
        now = time.monotonic()
        return {d: round(now - t, 1) for d, t in self._last_at.items()}


class DailyQuota:
    """按「平台 + 北京日期」计数的日配额。

    为什么用北京日期：与日报、统计接口的口径一致。用 UTC 日期的话，
    跨零点那 8 小时里运维看到的「今天用了多少」会和日报对不上。

    ``limit <= 0`` 视为不限制。

    多副本（``QUOTA_BACKEND=redis``）：计数放 Redis（Lua 原子完成
    「判超限 + INCRBY + EXPIRE」），全局共享 —— 否则 N 个副本各算一份，
    实际访问量变成 N 倍，**对招聘站点的合规承诺不成立**。
    key 含北京日期，跨天由 TTL 自动回收。
    """

    def __init__(self, redis_backend=None):
        self._counts: Dict[Tuple[str, str], int] = defaultdict(int)
        self._redis = redis_backend or _shared_redis

    @staticmethod
    def today() -> str:
        """北京时区的今天（YYYY-MM-DD）。"""
        return (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
        ).strftime("%Y-%m-%d")

    @staticmethod
    def _ttl_seconds() -> int:
        """Redis 配额 key 的过期秒数：到「北京时间明天 0 点」为止（+60s 余量）。

        与 ``today()`` 同一口径 —— key 里已含日期，跨天后新 key 生效、
        旧 key 由 TTL 回收，不需要额外的清理任务。
        """
        beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
        tomorrow = (beijing_now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((tomorrow - beijing_now).total_seconds()) + 60)

    def _redis_key(self, platform: str) -> str:
        return f"{_QUOTA_KEY_PREFIX}{platform}:{self.today()}"

    def used(self, platform: str) -> int:
        if self._redis.enabled():
            client = self._redis.get()
            if client is not None:
                try:
                    return int(client.get(self._redis_key(platform)) or 0)
                except Exception as e:  # noqa: BLE001
                    logger.warning("日配额 Redis 读取失败，回退进程内：%s", e)
        return self._counts[(platform, self.today())]

    def remaining(self, platform: str, limit: int) -> Optional[int]:
        """剩余配额；``limit <= 0`` 时不限制，返回 ``None``。"""
        if limit <= 0:
            return None
        return max(limit - self.used(platform), 0)

    def try_consume(self, platform: str, limit: int, amount: int = 1) -> bool:
        """尝试消耗配额。**超限时返回 False 且不计数**（避免失败也把额度吃掉）。"""
        if limit <= 0:
            return True

        if self._redis.enabled():
            client = self._redis.get()
            if client is not None:
                try:
                    # Lua 原子：判超限 + INCRBY + EXPIRE 一次完成。
                    # 分三步做的话，两个副本会同时读到「还剩 1 次」而都放行 ——
                    # 这正是日配额必须在多副本下共享的原因。
                    result = client.eval(
                        _LUA_QUOTA_CONSUME, 1, self._redis_key(platform),
                        limit, amount, self._ttl_seconds(),
                    )
                    return int(result) > 0
                except Exception as e:  # noqa: BLE001 - 命令异常一律降级
                    logger.warning("日配额 Redis 失败，本次降级进程内：%s", e)

        return self._try_consume_local(platform, limit, amount)

    def _try_consume_local(self, platform: str, limit: int, amount: int = 1) -> bool:
        """进程内计数（默认后端，也是 Redis 不可用时的降级路径）。"""
        key = (platform, self.today())
        if self._counts[key] + amount > limit:
            return False
        self._counts[key] += amount
        return True

    def reset(self) -> None:
        """清空**本进程**的计数（供测试使用）。

        刻意不清 Redis：跨进程配额由 key 的 TTL（到北京明天 0 点）自然回收；
        强行删除等于伪造「今天没访问过」，会真的把额度用超。
        """
        self._counts.clear()

    def snapshot(self, limits: Optional[Dict[str, int]] = None) -> Dict[str, dict]:
        """返回各平台今日用量与剩余，供接口/日志展示。

        Redis 后端下以 ``limits`` 里的平台为范围**逐个查全局计数** ——
        不做 SCAN 全表（既慢，也可能扫到别的键前缀）。
        """
        limits = limits or {}

        if self._redis.enabled():
            client = self._redis.get()
            if client is not None:
                result = {}
                for platform, limit in limits.items():
                    try:
                        used = int(client.get(self._redis_key(platform)) or 0)
                    except Exception:  # noqa: BLE001
                        used = self._counts[(platform, self.today())]
                    result[platform] = {
                        "used": used,
                        "limit": limit,
                        "remaining": max(limit - used, 0) if limit > 0 else None,
                    }
                return result

        result = {}
        for (platform, _date), used in self._counts.items():
            limit = limits.get(platform, 0)
            result[platform] = {
                "used": used,
                "limit": limit,
                "remaining": max(limit - used, 0) if limit > 0 else None,
            }
        return result


# 进程内单例：所有任务共用同一份节流与配额状态
domain_gate = DomainGate()
daily_quota = DailyQuota()


async def await_domain_slot(url: str, min_interval: float) -> float:
    """对 URL 所属域名排队取一个请求槽位，返回等待秒数。"""
    return await domain_gate.acquire(domain_of(url), min_interval)
