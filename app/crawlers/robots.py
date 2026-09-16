"""robots.txt 合规门禁（带缓存）。

``BaseCrawler.check_robots_allowed`` 早就写好了 robots 解析逻辑，但**全项目零调用** ——
合规检查实际上等于不存在（和 ``crawl_with_retry`` 当初「写好了没被调用」是同一类问题）。
本模块把它接到统一入口上，并补两件基础方法没做的事：

1. **按域名缓存**：robots.txt 一小时内只拉一次。每个任务都拉一次本身就是额外流量，
   而且「每个任务都先访问一次 robots.txt」这个模式，比正常客户端更可疑；
2. **明确处置**：命中 Disallow 时按配置阻断或告警，**绝不静默**。

## 关于默认「只告警、不阻断」的取舍

``CRAWL_ROBOTS_STRICT`` 默认为 false。这是刻意取舍而非疏忽：
本项目采集的是公开招聘列表页，且已有前置校验（未收录城市/未实现平台直接拒绝）
与限速；默认阻断会让使用者撞上「配置全对却一个任务也跑不起来」，
且很难判断到底是自己配错了还是站点不允许。
无论哪种模式，命中 Disallow 都会记 WARNING，并且说明会写进任务 message。
需要硬门禁（对外交付、有合规要求）时把 ``CRAWL_ROBOTS_STRICT`` 设为 true。
"""
import logging
import time
from typing import Dict, Optional, Tuple

from app.config import settings
from app.crawlers.base import BaseCrawler
from app.crawlers.throttle import domain_of

logger = logging.getLogger(__name__)

# robots.txt 的缓存时长（秒）。站点改 robots 不频繁，一小时足够；
# 太短等于每个任务都去拉一次，太长则站点收紧规则后我们反应迟钝。
CACHE_TTL_SECONDS = 3600


class _RobotsProbe(BaseCrawler):
    """仅用于借出基类 robots 解析能力的空壳。

    ``check_robots_allowed`` 只用到 ``urlparse`` 与 ``httpx``，不依赖任何实例状态，
    但它是 ``BaseCrawler``（抽象基类）的方法。为了**不改动 base.py**
    （协作约定），这里用最小子类把它借出来；两个抽象方法在本模块永远不会被调用。
    """

    def __init__(self):
        super().__init__("robots-probe")

    async def crawl(self, keyword: str, city: str = ""):
        raise NotImplementedError("robots 探针不执行采集")

    async def parse_page(self, page_content, keyword: str):
        raise NotImplementedError("robots 探针不解析页面")


_probe = _RobotsProbe()

# domain -> (allowed, 记录时刻 monotonic)
_cache: Dict[str, Tuple[bool, float]] = {}


async def is_allowed(url: str, *, use_cache: bool = True) -> Optional[bool]:
    """判断 URL 是否被 robots.txt 允许。

    返回值的三种语义必须区分开，调用方不能把它们混为一谈：

    - ``True``  明确允许，**或 robots.txt 不可达**（沿用基类「不可达即放行」的约定）
    - ``False`` 明确 Disallow
    - ``None``  URL 拿不到域名等无法判断的情况
    """
    domain = domain_of(url)
    if not domain:
        return None

    now = time.monotonic()
    if use_cache:
        cached = _cache.get(domain)
        if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
            return cached[0]

    allowed = await _probe.check_robots_allowed(url)
    _cache[domain] = (allowed, now)
    if not allowed:
        logger.warning("robots.txt 禁止抓取 %s（域名 %s）", url, domain)
    return allowed


async def gate(platform: str, url: str) -> Tuple[bool, str]:
    """合规门禁：返回 ``(是否放行, 说明)``。

    严格模式下命中 Disallow 直接不放行，由调用方把说明写进任务 message 并置 FAILED；
    宽松模式下放行，但说明里写清「robots 禁止、按宽松模式继续」——
    调用方必须把这句话落到日志或 message 里，避免变成静默通过。
    """
    if not settings.crawl_robots_check_enabled:
        return True, ""

    allowed = await is_allowed(url)
    if allowed is None:
        return True, f"robots.txt 无法判断（{platform}），按放行处理"

    if allowed:
        return True, ""

    reason = f"robots.txt 不允许抓取 {url}"
    if settings.crawl_robots_strict:
        return False, f"{reason}（CRAWL_ROBOTS_STRICT=true，已拒绝该平台）"
    return True, f"{reason}（按宽松模式继续；如需硬门禁请设 CRAWL_ROBOTS_STRICT=true）"


def clear_cache() -> None:
    """清空 robots 缓存（供测试与「站点规则变更后立即重查」使用）。"""
    _cache.clear()


def snapshot() -> Dict[str, dict]:
    """返回缓存现状，供诊断「为什么这个平台被拦了」。"""
    now = time.monotonic()
    return {
        domain: {"allowed": allowed, "age_seconds": round(now - fetched_at, 1)}
        for domain, (allowed, fetched_at) in _cache.items()
    }
