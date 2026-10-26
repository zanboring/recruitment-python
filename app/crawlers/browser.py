"""Playwright 浏览器上下文的统一启动入口。

``app/crawlers/base.py`` 只提供反爬**策略**（随机 UA、完整请求头、风控信号
检测、自适应降速、重试包装），没有提供浏览器启动 —— 启动逻辑原先写在
``boss.py`` 里。新增平台若各自复制一份，代理与请求头配置必然漂移
（改了一处忘了另一处）。

故抽出本模块：新增平台统一调用 :func:`launch`，代理、视口、请求头只维护一份。

注意：本模块不修改 ``base.py`` / ``boss.py`` / ``cleaner.py``，
``boss.py`` 仍保留它自己的启动实现。
"""
import logging
from typing import Tuple

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}


async def launch(playwright, crawler, viewport: dict = None) -> Tuple[object, object]:
    """启动 chromium 并返回带反爬请求头的 ``(browser, context)``。

    参数 ``playwright`` 由调用方传入（而不是在这里 ``async with``），
    这样一次爬取任务可以复用同一个浏览器进程完成多页翻页。

    ``crawler`` 需要提供 ``get_random_headers()``（``BaseCrawler`` 已有）。
    该字典里的 ``User-Agent`` 必须单独传给 ``user_agent``，
    Playwright 不允许 UA 同时出现在 ``user_agent`` 与 ``extra_http_headers``。

    调用方负责在 finally 中 ``await browser.close()``。
    """
    launch_kwargs = {"headless": True}
    proxy = settings.crawl_proxy
    if proxy:
        # 支持 http://user:pass@host:port 与 http://host:port 两种形式
        launch_kwargs["proxy"] = {"server": proxy}
        logger.info("已启用爬虫代理：%s", proxy.split("@")[-1])

    browser = await playwright.chromium.launch(**launch_kwargs)

    headers = crawler.get_random_headers()
    context = await browser.new_context(
        user_agent=headers.pop("User-Agent"),
        extra_http_headers=headers,
        viewport=viewport or DEFAULT_VIEWPORT,
    )
    return browser, context
