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
    def get_random_headers(self) -> dict:
        """构造一套完整的浏览器请求头（不只是 UA）。

        仅换 UA 仍能被风控识别：请求头之间是有协变特征的（UA 是 Chrome 却
        戴着 Safari 的 Sec-Fetch 要求，或 Accept-Language 与 UA 区域不符），
        都会被当作自动化特征。这里按 UA 类型返回配套的头集合，尽量模拟真实浏览器。
        """
        ua = self.get_random_ua()
        is_iphone = 'iPhone' in ua
        platform = 'Mobile' if is_iphone else ('Macintosh' if 'Mac' in ua else 'Windows')
        sec_ch_ua = (
            '\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\"'
            if 'Chrome' in ua
            else '\"Not_A Brand\";v=\"8\"'
        )
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Sec-CH-UA": sec_ch_ua,
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": f'"{platform}"',
        }
        if is_iphone:
            headers["Sec-CH-UA-Mobile"] = "?1"
        return headers

    # 最近一次请求是否出现风控信号（429 / 验证码 / 滑块等）
    _blocked_signals = 0

    @classmethod
    def mark_blocked(cls):
        """由平台爬虫在检测到风控信号时调用，触发自适应降速。

        连续命中会进一步放大延迟（封号往往发生在「反复用相同节奏试探」
        而不是「偶发一次」），因此这里用计数而非布尔。
        """
        BaseCrawler._blocked_signals += 1
        logger.warning("检测到风控信号（累计 %s 次），后续延迟将自动拉大", BaseCrawler._blocked_signals)

    def _adaptive_delay(self) -> float:
        """基础随机延迟；若近期出现过风控信号，乘一个放大倍率。

        单次 429 就把整轮停掉太重，这里渐进放大：风控越多、降速越狠，
        直到用户手动降低频率或重启进程（计数清零）。
        """
        base = random.uniform(settings.crawl_delay_min, settings.crawl_delay_max)
        if BaseCrawler._blocked_signals > 0:
            lo, hi = settings.crawl_adaptive_delay_factor
            factor = random.uniform(lo, hi) * BaseCrawler._blocked_signals
            logger.warning("自适应降速：风控 %s 次，延迟放大 %.1f 倍", BaseCrawler._blocked_signals, factor)
            return base * factor
        return base

    async def random_delay(self):
        await asyncio.sleep(self._adaptive_delay())

    @staticmethod
    def detect_blocked(content: str, status_code: int = 200) -> bool:
        """检测响应中是否出现风控信号。

        返回 True 时调用方应：1) 调用 mark_blocked() 自适应降速；
        2) 对整页结果判定为不可用（不把验证码页当空列表入库）。
        """
        if status_code == 429:
            return True
        if not content:
            return False
        low = content.lower()[:4000]
        signals = (
            'captcha', 'verify', 'verification', 'security check',
            '登录后查看', '验证码', '滑动验证', '安全验证', '访问异常',
            '校验', '风险', '滑块', '人机验证',
        )
        return any(s in low for s in signals)
