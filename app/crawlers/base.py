import asyncio
import random
from abc import ABC, abstractmethod
from typing import List, Dict

from app.config import settings

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

    async def random_delay(self):
        delay = random.uniform(settings.crawl_delay_min, settings.crawl_delay_max)
        await asyncio.sleep(delay)

    async def crawl_with_retry(self, keyword: str, city: str = "") -> List[Dict]:
        all_results = []
        for attempt in range(settings.crawl_retry_times):
            try:
                results = await self.crawl(keyword, city)
                all_results.extend(results)
                break
            except Exception as e:
                if attempt < settings.crawl_retry_times - 1:
                    wait_time = 2 ** (attempt + 1)
                    await asyncio.sleep(wait_time)
                else:
                    raise e
        return all_results

    @abstractmethod
    async def crawl(self, keyword: str, city: str = "") -> List[Dict]:
        pass

    @abstractmethod
    async def parse_page(self, page_content, keyword: str) -> List[Dict]:
        pass

    def get_random_ua(self) -> str:
        return random.choice(USER_AGENTS)