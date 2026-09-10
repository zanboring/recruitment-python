import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import List, Dict

from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
]


class BaseCrawler(ABC):
    def __init__(self, source_site: str):
        self.source_site = source_site
        self.total_count = 0
        self.failed_count = 0

    async def check_robots_allowed(self, url: str) -> bool:
        """检查 robots.txt 是否允许爬取目标 URL（合规性检查）。

        生产环境爬取前应先尊重站点的 robots 协议；当前项目为教学演示用途，
        爬取的 BOSS 直聘岗位列表页通常允许搜索引擎索引。若 robots 不可达
        则默认放行（不阻塞演示），并记录 warning。
        """
        from urllib.parse import urlparse
        import httpx

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(robots_url)
                if resp.status_code != 200:
                    logger.warning("robots.txt 不可达 (%s)，默认放行", robots_url)
                    return True
                # 简化版 robots 解析：只看 User-agent: * 后的 Disallow
                lines = resp.text.splitlines()
                disallow_paths = []
                in_wildcard = False
                for line in lines:
                    line = line.strip()
                    if line.lower().startswith("user-agent:"):
                        in_wildcard = "*" in line.lower().split(":", 1)[1]
                    elif in_wildcard and line.lower().startswith("disallow:"):
                        path = line.split(":", 1)[1].strip()
                        if path and parsed.path.startswith(path):
                            logger.warning("robots.txt 禁止爬取 %s", url)
                            return False
        except Exception as e:
            logger.warning("robots.txt 检查失败 (%s)，默认放行: %s", robots_url, e)
        return True

    async def random_delay(self):
        delay = random.uniform(settings.crawl_delay_min, settings.crawl_delay_max)
        await asyncio.sleep(delay)

    async def crawl_with_retry(self, keyword: str, city: str = "") -> List[Dict]:
        """带指数退避 + 抖动的重试包装。

        之前这个方法写好了却没被调用（爬一次失败整个任务直接 FAILED），
        现在由 CrawlerService.start_crawl 统一走这里。

        退避策略：2^(n+1) 秒，并叠加 0~1 秒随机抖动。加抖动是为了避免
        多个并发任务在同一时刻发起重试，形成周期性流量尖峰被风控识别。
        """
        last_error = None
        for attempt in range(settings.crawl_retry_times):
            try:
                results = await self.crawl(keyword, city)
                return results or []
            except Exception as e:
                last_error = e
                if attempt < settings.crawl_retry_times - 1:
                    wait_time = 2 ** (attempt + 1) + random.uniform(0, 1)
                    logger.warning(
                        "[%s] 第 %s/%s 次爬取失败：%s，%.1fs 后重试",
                        self.source_site, attempt + 1, settings.crawl_retry_times, e, wait_time
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        "[%s] 爬取失败且已用尽 %s 次重试：%s",
                        self.source_site, settings.crawl_retry_times, e
                    )
        raise last_error

    @abstractmethod
    async def crawl(self, keyword: str, city: str = "") -> List[Dict]:
        pass

    @abstractmethod
    async def parse_page(self, page_content, keyword: str) -> List[Dict]:
        pass

    def get_random_ua(self) -> str:
        return random.choice(USER_AGENTS)