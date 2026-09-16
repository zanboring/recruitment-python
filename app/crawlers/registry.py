"""平台爬虫注册表。

把「平台标识 → 爬虫类」收敛到一处，替代原先散落在服务层的硬编码：

- 新增平台只需在这里加一行，``SUPPORTED_PLATFORMS`` 自动跟着变，
  不必再记得同步「服务层的支持集合」与「实际存在的爬虫类」两处
  （那种两处维护必然漂移：加了爬虫却忘了登记，接口就把新平台判成未实现）。

本模块只做注册与实例化，不改动 ``base.py`` / ``boss.py`` / ``cleaner.py``。
"""
from typing import Dict, List, Type

from app.crawlers.base import BaseCrawler
from app.crawlers.boss import BossCrawler
from app.crawlers.job51 import Job51Crawler

# 平台标识 → 爬虫类。标识与 ``crawler_service.PLATFORM_META`` 的键保持一致。
CRAWLER_REGISTRY: Dict[str, Type[BaseCrawler]] = {
    "boss": BossCrawler,
    "51job": Job51Crawler,
}

# 已实现抓取的平台集合（由注册表派生，避免两处维护）
SUPPORTED_PLATFORMS = frozenset(CRAWLER_REGISTRY)


def get_crawler(platform: str) -> BaseCrawler:
    """按平台标识实例化爬虫。

    未实现的平台抛 ``KeyError`` —— 调用方应先用 ``SUPPORTED_PLATFORMS``
    做前置校验并给出明确原因，而不是在这里静默回退到别的平台
    （回退会让「选 51job」变成一个悄悄爬 BOSS 的任务，数据记在错误平台名下）。
    """
    return CRAWLER_REGISTRY[platform]()


def supported_platforms() -> List[str]:
    """已实现平台的排序清单。"""
    return sorted(SUPPORTED_PLATFORMS)


def is_supported(platform: str) -> bool:
    return platform in SUPPORTED_PLATFORMS
