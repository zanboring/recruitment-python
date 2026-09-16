"""跨任务的采集节流、日配额与 ``Retry-After`` 解析。

## 为什么还需要这个模块

``BaseCrawler`` 的反爬护栏解决的是**一次爬取内部**的问题：相邻两次请求之间随机延时、
失败后指数退避、命中风控信号后自适应放大。但它有三个盲区，都出在「任务之间」：

1. **同一个域名可能被并发访问** —— 定时任务一次要跑「3 关键词 × 6 城市」= 18 组，
   两个任务若命中同一域名，只靠组内延时并不能阻止它们在同一时刻发出请求；
2. **没有「今天已经请求了多少次」的概念** —— 设不了日配额，也就无法回答
   「这个平台我今天最多访问多少次」这种最基本的运维问题；
3. **被 429 时只看自己的退避策略** —— 不读对方 ``Retry-After`` 明确要求的等待时长。

本模块补上这三样。状态是**进程内**的：单实例部署足够；多副本部署需换成 Redis
计数器（与限流中间件「单进程内存实现」是同一个取舍，见 README 的「已知取舍」）。
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
    """

    def __init__(self, jitter_ratio: float = 0.2):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_at: Dict[str, float] = {}
        self._jitter_ratio = jitter_ratio

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

        async with self._lock_for(domain):
            elapsed = time.monotonic() - self._last_at.get(domain, 0.0)
            wait = min_interval - elapsed
            if wait > 0:
                wait += random.uniform(0, min_interval * self._jitter_ratio)
                logger.debug("域名 %s 节流等待 %.1fs", domain, wait)
                await asyncio.sleep(wait)
            self._last_at[domain] = time.monotonic()
            return max(wait, 0.0)

    def reset(self) -> None:
        """清空节流状态（供测试与「重置采集频率」使用）。"""
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
    """

    def __init__(self):
        self._counts: Dict[Tuple[str, str], int] = defaultdict(int)

    @staticmethod
    def today() -> str:
        """北京时区的今天（YYYY-MM-DD）。"""
        return (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
        ).strftime("%Y-%m-%d")

    def used(self, platform: str) -> int:
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
        key = (platform, self.today())
        if self._counts[key] + amount > limit:
            return False
        self._counts[key] += amount
        return True

    def reset(self) -> None:
        self._counts.clear()

    def snapshot(self, limits: Optional[Dict[str, int]] = None) -> Dict[str, dict]:
        """返回各平台今日用量与剩余，供接口/日志展示。"""
        limits = limits or {}
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
